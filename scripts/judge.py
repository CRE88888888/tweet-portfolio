#!/usr/bin/env python3
"""judge.py — run the LLM judges over posts, with a content-addressed cache.

  --mode fast (default): rule prefilter (prefilter.py) -> literal judge on kept posts (batch 40) -> trader + skeptic
                         only on posts touching the current top-10 names or with an unsure literal label (2 rounds).
  --mode strict:         all 3 judges on every post in the window (batch 25).
  --max-posts N / --max-escalate M (fast): caps; defaults fast_max_posts=400, fast_max_escalate=160 in params; 0 = no cap.

  judge.py --posts posts.jsonl --start 2026-05-13 --end 2026-11-09 --cache cache/ [--mode strict] [--batch N] [--parallel 8]
           [--llm-cmd 'codex exec --skip-git-repo-check --ephemeral -s read-only -o {out} --output-schema {schema} -']
           [--export-dir req/]   # instead of calling an LLM, write request files for 3 separate sub-agents
           [--import-dir resp/]  # read their answers back into the cache

Each judge = one prompt file in prompts/ (judge_trader.md, judge_skeptic.md, judge_literal.md) + _common_output.md.
Each judge sees ONLY its own prompt; calls are fresh processes (no shared context). Temperature 0 when the backend
allows it. Cache key = sha256(prompt text + schema + model tag) / post id, so re-runs never re-judge a post and the
same posts + same cache always give the same labels (that is what makes the pipeline reproducible).
Output: cache/<judge>_<hash12>/<post_id>.json  = {"id":..., "labels":[...]}
"""
import json, os, sys, argparse, hashlib, subprocess, tempfile, concurrent.futures as cf

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
JUDGES = ["judge_trader", "judge_skeptic", "judge_literal"]
DEFAULT_CMD = "codex exec --skip-git-repo-check --ephemeral -s read-only -c model_reasoning_effort=\"low\" -o {out} --output-schema {schema} -"

def prompt_text(j):
    p = open(os.path.join(ROOT, "prompts", j + ".md"), encoding="utf-8").read()
    c = open(os.path.join(ROOT, "prompts", "_common_output.md"), encoding="utf-8").read()
    return p.strip() + "\n\n" + c.strip() + "\n"

def judge_dir(cache, j, model_tag):
    schema = open(os.path.join(ROOT, "prompts", "schema.json")).read()
    h = hashlib.sha256((prompt_text(j) + schema + model_tag).encode()).hexdigest()[:12]
    d = os.path.join(cache, f"{j}_{h}"); os.makedirs(d, exist_ok=True); return d

def judgeable(p):
    return p["type"] != "repost"

def to_input(p):
    item = {"id": p["id"], "date": (p["created_at"] or "")[:10], "type": p["type"], "text": p["text"]}
    ctx = [c for c in p.get("context", []) if c.get("text") and c["rel"] in ("replied_to", "quoted")]
    if ctx: item["context"] = "\n---\n".join(f"[{c['rel']}] {c['text'][:1200]}" for c in ctx)
    return item

def build_request(j, batch):
    return prompt_text(j) + "\n## Posts\n```json\n" + json.dumps([to_input(p) for p in batch], ensure_ascii=False, indent=0) + "\n```\n"

def store(d, batch, answer):
    got = {str(x["id"]): x for x in answer.get("posts", [])}
    n = 0
    for p in batch:
        x = got.get(p["id"])
        if x is None: continue                     # missing -> stays uncached, re-asked next run
        json.dump({"id": p["id"], "labels": x.get("labels", [])}, open(os.path.join(d, p["id"] + ".json"), "w"),
                  ensure_ascii=False, sort_keys=True); n += 1
    return n

def run_llm(cmd, j, d, batch, a_timeout=600):
    batch = [p for p in batch if not os.path.exists(os.path.join(d, p["id"] + ".json"))]   # another worker may have done it
    if not batch: return 0, "already cached"
    schema = os.path.join(ROOT, "prompts", "schema.json")
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "out.json")
        c = cmd.format(out=out, schema=schema)
        r = subprocess.run(c, shell=True, input=build_request(j, batch), capture_output=True, text=True, timeout=a_timeout, cwd=td)
        if not os.path.exists(out): return 0, (r.stderr or r.stdout)[-300:]
        try: ans = json.loads(open(out).read())
        except Exception as e: return 0, f"bad json {e}"
    return store(d, batch, ans), ""

def labelled(cache, p, model_tag):
    return {j for j in JUDGES if os.path.exists(os.path.join(judge_dir(cache, j, model_tag), p["id"] + ".json"))}

def run_jobs(a, jobs):
    if a.export_dir:
        os.makedirs(a.export_dir, exist_ok=True)
        for k, (j, d, b) in enumerate(jobs):
            open(os.path.join(a.export_dir, f"{j}__{k:04d}.md"), "w").write(build_request(j, b))
        print(f"exported {len(jobs)} requests; give each file to a FRESH sub-agent that sees only that file; save its JSON as <same name>.json in the import dir, import, then rerun this command for the next stage")
        return False
    done = 0
    with cf.ThreadPoolExecutor(a.parallel) as ex:
        fs = {ex.submit(run_llm, a.llm_cmd, j, d, b, a.timeout): (j, len(b)) for j, d, b in jobs}
        for f in cf.as_completed(fs):
            try: n, err = f.result()
            except Exception as e: n, err = 0, f"failed: {type(e).__name__} (will be retried on the next run)"
            done += 1
            print(f"[{done}/{len(jobs)}] {fs[f][0]} stored {n}/{fs[f][1]} {err}", flush=True)
    return True

def batches(j, d, todo, n):
    return [(j, d, todo[i:i + n]) for i in range(0, len(todo), n)]

def escalation_targets(a, posts, kept):
    """posts that need the other two judges: they mention a current top-10 name, or the literal label is unsure."""
    import datetime as dt
    import engine as E
    P = E.load_json(os.path.join(ROOT, "tables", "params_default.json"))
    if a.params: P.update(E.load_json(a.params))
    M = E.load_json(os.path.join(ROOT, "tables", "conviction.json"))
    alias = E.load_json(os.path.join(ROOT, "tables", "ticker_aliases.json"))
    asof = E.T((a.end or max(p["created_at"] for p in posts)[:10]) + "T23:59:59+00:00")
    sigs, _ = E.consensus(posts, a.cache, alias, a.model_tag, mode="fast", kept=kept)
    rows, w, _, _ = E.build(sigs, asof, P, M)
    top = set(sorted(w, key=lambda t: (-w[t], t))[:a.escalate_top])
    dl = judge_dir(a.cache, "judge_literal", a.model_tag); out = []
    for p in posts:
        if p["id"] not in kept: continue
        f = os.path.join(dl, p["id"] + ".json")
        if not os.path.exists(f): continue
        L = json.load(open(f))["labels"]
        tks = {E.norm_ticker(x["ticker"], alias) for x in L}
        unsure = any(x["holding"] == "implied" or x["kind"] == "endorse" or (x["stance"] != "neutral" and x["holding"] == "none" and x["action"] != "none") for x in L)
        if tks & top or unsure: out.append(dict(p, _top=bool(tks & top)))
    return out, top

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts", required=True); ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--mode", choices=["fast", "strict"], default="fast",
                    help="fast (default): rule prefilter + literal judge, trader+skeptic only on top-10 / unsure posts; strict: all posts x 3 judges")
    ap.add_argument("--cache", default=os.path.join(ROOT, "cache")); ap.add_argument("--batch", type=int, default=None)
    ap.add_argument("--parallel", type=int, default=8); ap.add_argument("--llm-cmd", default=DEFAULT_CMD)
    ap.add_argument("--model-tag", default="default"); ap.add_argument("--judges", default=",".join(JUDGES))
    ap.add_argument("--timeout", type=int, default=600); ap.add_argument("--params")
    ap.add_argument("--escalate-top", type=int, default=10); ap.add_argument("--max-posts", type=int, help="fast: cap on posts sent to the literal judge (default params fast_max_posts=400; 0 = no cap)")
    ap.add_argument("--max-escalate", type=int, help="fast: cap on posts escalated to trader+skeptic (default fast_max_escalate=160; 0 = no cap)"); ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--reverse", action="store_true", help="newest posts first (lets a 2nd worker share the cache)")
    ap.add_argument("--export-dir"); ap.add_argument("--import-dir"); a = ap.parse_args()
    posts = [json.loads(l) for l in open(a.posts, encoding="utf-8")]
    all_posts = posts
    posts = [p for p in posts if judgeable(p) and (not a.start or p["created_at"] >= a.start) and (not a.end or p["created_at"] <= a.end + "T99")]
    if a.import_dir:
        n = 0; byid = {p["id"]: p for p in posts}
        for f in sorted(os.listdir(a.import_dir)):
            if not f.endswith(".json"): continue
            j = f.split("__")[0]; d = judge_dir(a.cache, j, a.model_tag)
            ans = json.load(open(os.path.join(a.import_dir, f)))
            n += store(d, [byid[str(x["id"])] for x in ans.get("posts", []) if str(x["id"]) in byid], ans)
        print("imported", n); return
    if a.mode == "strict":
        jobs = []
        for j in a.judges.split(","):
            d = judge_dir(a.cache, j, a.model_tag)
            todo = [p for p in posts if not os.path.exists(os.path.join(d, p["id"] + ".json"))]
            if a.reverse: todo = todo[::-1]
            print(f"{j}: {len(posts)} posts, {len(posts)-len(todo)} cached, {len(todo)} to judge -> {d}")
            jobs += batches(j, d, todo, a.batch or 25)
        run_jobs(a, jobs); return
    # ---- fast mode ----
    import engine as E
    from prefilter import select
    P = E.load_json(os.path.join(ROOT, "tables", "params_default.json"))
    if a.params: P.update(E.load_json(a.params))
    if a.max_posts is not None: P["fast_max_posts"] = a.max_posts
    if a.max_escalate is not None: P["fast_max_escalate"] = a.max_escalate
    kept, n_hits = select(posts, all_posts, P.get("fast_max_posts", 0))
    print(f"fast: {len(posts)} posts in window, {n_hits} pass the rule prefilter, {len(posts)-n_hits} skipped (no security mentioned); "
          f"cap fast_max_posts={P.get('fast_max_posts', 0)} -> {len(kept)} kept, {n_hits-len(kept)} lowest-relevance dropped")
    dl = judge_dir(a.cache, "judge_literal", a.model_tag)
    todo = [p for p in posts if p["id"] in kept and len(labelled(a.cache, p, a.model_tag)) < 2 and not os.path.exists(os.path.join(dl, p["id"] + ".json"))]
    print(f"stage 1 literal: {len(todo)} to judge")
    if todo and not run_jobs(a, batches("judge_literal", dl, todo, a.batch or 40)): return
    budget = P.get("fast_max_escalate", 0) or 10**9   # posts escalated across all rounds
    done_esc = set()
    for r in range(a.rounds):
        esc, top = escalation_targets(a, posts, kept)
        n_all = len(esc)
        # top-10 hits first, then unsure; newer first; stop at the escalation budget
        esc = sorted(esc, key=lambda p: (0 if p.get("_top") else 1, -int(p["id"])))
        room = max(0, budget - len(done_esc))
        esc = [p for p in esc if p["id"] in done_esc] + [p for p in esc if p["id"] not in done_esc][:room]
        done_esc |= {p["id"] for p in esc}
        jobs = []
        for j in ("judge_trader", "judge_skeptic"):
            d = judge_dir(a.cache, j, a.model_tag)
            t2 = [p for p in esc if not os.path.exists(os.path.join(d, p["id"] + ".json"))]
            jobs += batches(j, d, t2, a.batch or 40)
        print(f"stage 2 round {r+1}: top{a.escalate_top}={sorted(top)}; {n_all} posts qualify, {len(esc)} escalated (budget fast_max_escalate={P.get('fast_max_escalate', 0)}), {sum(len(b) for _,_,b in jobs)} judge-posts to run")
        if not jobs: break
        if not run_jobs(a, jobs): return

if __name__ == "__main__": main()
