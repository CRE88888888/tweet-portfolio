#!/usr/bin/env python3
"""engine.py — turn 3-judge labels into an inferred portfolio (deterministic).

  engine.py --posts posts.jsonl --cache cache/ --subject @handle --asof 2026-11-09 [--params my.json]
            [--filing filing_base.json] [--coverage coverage.json] --out portfolio.json

Pipeline: consensus (2/3 per dimension) -> per-ticker evidence + state machine -> level A/B/C/D ->
conviction score (account-normalised factors, see spec.md) -> score^k -> cap/floor projection -> cash -> trade log (new names bought from cash).
Same posts + same cache + same params => byte-identical output (no wall-clock, sorted keys, fixed rounding).
"""
import json, os, sys, argparse, math, datetime as dt, re
from collections import defaultdict, Counter
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from judge import JUDGES, judge_dir

DISCLAIMER = ("本組合是從公開推文內容推斷，不是真實持倉。作者未公開、未提及的大部位可能才是多數；"
              "推文可能誇大、延遲或刻意誤導。權重為規則化推估，不代表實際比例。非投資建議。"
              " / Inferred from public posts, NOT actual holdings. Large unseen positions may be the majority. "
              "Weights are rule-based estimates. Not investment advice.")
SIZE_RANK = {"none": 0, "small": 1, "normal": 2, "large": 3, "max": 4}
STATES = ["未知", "建倉", "加碼", "持有", "減碼", "清倉"]

def T(s): return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
def r4(x): return round(x + 0.0, 4)

def load_json(p): return json.load(open(p, encoding="utf-8"))

def norm_ticker(t, alias):
    t = (t or "").strip().upper().lstrip("$").replace(" ", "")
    for pre in ("NASDAQ:", "NYSE:", "NYSEARCA:", "AMEX:", "OTC:", "US:"):
        if t.startswith(pre): t = t[len(pre):]
    return alias.get(t, t)

def kind_of(tk):
    if tk.startswith("PRIVATE:"): return "private"
    if tk in ("BTC", "ETH", "SOL", "XRP", "DOGE"): return "crypto"
    if ":" in tk: return "non_us"
    return "us"

def majority(vals):
    c = Counter(vals).most_common()
    if c and c[0][1] >= 2: return c[0][0]
    return None

def consensus(posts, cache, alias, model_tag="default", mode="strict", kept=None):
    """strict: every post needs all 3 judges, stance and holding each need 2 of 3.
    fast: use the judges a post has — 3 -> 2-of-3, 2 -> both must agree, 1 (literal only) -> its label;
    posts the rule prefilter skipped carry no signal and are not counted as missing."""
    dirs = [(j, judge_dir(cache, j, model_tag)) for j in JUDGES]
    if mode == "fast" and kept is None:
        from prefilter import build_matcher
        hit = build_matcher(posts); kept = {p["id"] for p in posts if hit(p)}
    sigs, missing = [], 0
    for p in posts:
        per = []
        for j, d in dirs:
            f = os.path.join(d, p["id"] + ".json")
            if not os.path.exists(f): continue
            lab = {}
            for L in load_json(f)["labels"]:
                tk = norm_ticker(L["ticker"], alias)
                if not tk or tk in lab: continue
                lab[tk] = L
            per.append(lab)
        if mode == "strict" and len(per) < 3: missing += 1; continue
        if mode == "fast" and not per:
            if p["id"] in kept: missing += 1
            continue
        n = len(per)
        for tk in sorted(set().union(*[set(x) for x in per])):
            V = [x.get(tk) or {"stance": "neutral", "holding": "none", "action": "none", "size": "none",
                               "defends": False, "kind": "own", "evidence_quote": ""} for x in per]
            need = 1 if n == 1 else 2
            maj = lambda vals: (Counter(vals).most_common(1)[0][0] if Counter(vals).most_common(1)[0][1] >= need else None)
            stance = maj([v["stance"] for v in V]); holding = maj([v["holding"] for v in V])
            kind = maj([v["kind"] for v in V]) or "own"
            action = maj([v["action"] for v in V]) or "none"
            size = maj([v["size"] for v in V]) or "none"
            defends = sum(bool(v["defends"]) for v in V) >= need
            unanimous = n == 3 and len({v["stance"] for v in V}) == 1 and len({v["holding"] for v in V}) == 1
            quote = max((v["evidence_quote"] for v in V if v.get("evidence_quote")), key=len, default="")
            votes = [f'{v["stance"]}/{v["holding"]}/{v["action"]}' for v in V]
            sigs.append(dict(id=p["id"], ts=p["created_at"], url=p.get("url") or f"https://x.com/i/status/{p['id']}",
                             type=p["type"], tk=tk, stance=stance, holding=holding, action=action, size=size,
                             defends=defends, kind=kind, unanimous=unanimous, votes=votes, n_judges=n, quote=quote[:200],
                             text=p["text"][:280], low_conf=(stance is None or holding is None),
                             counts=kind in ("own", "endorse") and stance is not None and holding is not None))
    sigs.sort(key=lambda s: (s["ts"], s["id"], s["tk"]))
    return sigs, missing

def step_state(state, s):
    a, h = s["action"], s["holding"]
    if a == "sell" or h == "sold": return "清倉"
    if a == "trim": return "減碼"
    if a == "buy": return "建倉" if state in ("未知", "清倉") else "加碼"
    if a == "add": return "加碼"
    if a == "hold" or (h in ("explicit", "implied") and s["stance"] == "bull"):
        return "持有" if state in ("未知", "清倉") else state
    return state

def c_qualifies(days, P):
    days = sorted(set(days))
    for i in range(len(days)):
        j = i
        while j + 1 < len(days) and (days[j + 1] - days[i]).days <= P["c_span_days"]: j += 1
        if j - i + 1 >= P["c_min_days"]: return True
    return False

def score_ticker(tk, S, asof, P, M, filing_w=None):
    bull = [s for s in S if s["stance"] == "bull"]; bear = [s for s in S if s["stance"] == "bear"]
    state = "持有" if filing_w else "未知"; hist = []
    for s in S:
        ns = step_state(state, s)
        if ns != state: hist.append(dict(ts=s["ts"], frm=state, to=ns, id=s["id"]))
        state = ns
    expl = [s for s in S if s["holding"] == "explicit" or (s["action"] in ("buy", "add", "hold") and s["stance"] == "bull")]
    impl = [s for s in S if s["holding"] == "implied"]
    if filing_w: level = "A"
    elif (expl or len(impl) >= P["b_min_implied"]) and len(bull) > len(bear): level = "B"
    elif c_qualifies([T(s["ts"]).date() for s in bull], P) and len(bull) > len(bear): level = "C"
    else: level = "D"
    # raw conviction components (normalised across the account later, in conviction())
    w8 = lambda s: 0.5 ** ((asof - T(s["ts"])).days / P["half_life_days"])
    dec = sum(w8(s) for s in bull)
    SP = M["size_points"]
    size_pts = max(0.0, sum(SP[s["size"]] * w8(s) for s in S if s["size"] != "none" and s["stance"] != "bear"))
    nd = sum(1 for s in S if s["defends"] and s["stance"] != "bear")
    n_add = sum(1 for s in S if s["action"] in ("buy", "add"))
    f = dict(mentions_decayed=r4(dec), size_points=r4(size_pts), defends=nd, explicit_or_add=len(expl) + n_add,
             last_bull_age_days=(asof - T(bull[-1]["ts"])).days if bull else None)
    sizes = [s["size"] for s in S if s["size"] != "none" and s["stance"] != "bear"]
    if sizes: f["size_words"] = dict(sorted(Counter(sizes).items()))
    unan = (sum(1.0 if s["unanimous"] else (0.5 if s.get("n_judges", 3) == 1 else 0.0) for s in S) / len(S)) if S else (1.0 if filing_w else 0.0)
    volume = min(1.0, dec / P["volume_full_at"]) if not filing_w else 1.0
    cw = P["conf_weights"]
    conf = cw["level"] * P["level_score"].get(level, 0) + cw["unanimity"] * unan + cw["volume"] * volume
    reason = None
    if state == "清倉": reason = "清倉 (sold)"
    elif level == "D": reason = "evidence level D"
    elif not filing_w and len(bull) <= len(bear): reason = "not net bullish"
    k = kind_of(tk)
    if reason is None and k == "private" and not P["include_private"]: reason = "private company (not investable)"
    if reason is None and k == "crypto" and not P["include_crypto"]: reason = "crypto excluded by params"
    ev = sorted([s for s in S if s["stance"] == "bull"],
                key=lambda s: (-{"explicit": 2, "implied": 1}.get(s["holding"], 0), -SIZE_RANK[s["size"]], -s["unanimous"], s["ts"]), )
    seen, uniq = set(), []
    for s in ev:
        key = (s["quote"] or s["text"]).strip().lower()
        if key in seen or s["url"] in seen: continue
        seen.add(key); seen.add(s["url"]); uniq.append(s)
    ev = sorted(uniq[:P["evidence_links"]], key=lambda s: s["ts"])
    return dict(ticker=tk, level=level, state=state, state_history=hist, factors=f, filing_w=filing_w,
                n_bull=len(bull), n_bear=len(bear), n_explicit=len(expl), n_implied=len(impl), n_defends=nd,
                decayed_mentions=r4(dec), unanimity=r4(unan), confidence=r4(conf), raw=1.0 if level in ("A", "B", "C") else 0.0,
                asset_type=k, excluded_reason=reason,
                evidence=[dict(url=s["url"], date=s["ts"], quote=s["quote"] or s["text"][:160],
                               holding=s["holding"], action=s["action"], votes=s["votes"]) for s in ev])

def conviction(rows, M, fw=None, k=None):
    """account-relative score in [0,1]: each factor log1p'd and divided by the account max, then combined
    with fixed weights; 減碼 names discounted; returns {ticker: (score, weight_raw = score**k, parts)}"""
    fw = fw or M["factor_weights"]; k = M["k"] if k is None else k
    keys = ["mentions_decayed", "explicit_or_add", "defends", "size_points"]
    mx = {q: max([math.log1p(r["factors"][q]) for r in rows.values()] + [0]) for q in keys}
    fmax = max([r["filing_w"] or 0 for r in rows.values()] + [0])
    out = {}
    for t, r in rows.items():
        parts = {q: (math.log1p(r["factors"][q]) / mx[q] if mx[q] > 0 else 0.0) for q in keys}
        parts["level"] = M["level_points"][r["level"]]
        sc = sum(fw[q] * parts[q] for q in parts) / sum(fw[q] for q in parts)
        if fmax > 0:                                   # filer mode: filing weight is its own factor
            fp = (r["filing_w"] or 0) / fmax; sc = (1 - M["filing_factor_weight"]) * sc + M["filing_factor_weight"] * fp
            parts["filing"] = fp
        if r["state"] == "減碼": sc *= M["trim_discount"]
        out[t] = (sc, sc ** k, parts)
    return out

def project(raw, equity, cap, floor):
    """weights summing to <= equity, each in [floor, cap]; leftover beyond n*cap returns to cash."""
    names = sorted(raw, key=lambda k: (-raw[k], k))
    while names and len(names) * floor > equity + 1e-12: names.pop()
    w = {k: 0.0 for k in names}; free = set(names); budget = equity
    for _ in range(100):
        tot = sum(raw[k] for k in free)
        if not free or tot <= 0: break
        for k in free: w[k] = budget * raw[k] / tot
        hi = [k for k in free if w[k] > cap + 1e-12]; lo = [k for k in free if w[k] < floor - 1e-12]
        if not hi and not lo: break
        for k in hi: w[k] = cap; free.discard(k); budget -= cap
        if not hi:
            for k in lo: w[k] = floor; free.discard(k); budget -= floor
    return {k: v for k, v in w.items()}

def build(sigs, asof, P, M, filing=None, upto=None):
    end = upto or asof; start = asof - dt.timedelta(days=P["window_days"])
    f_end = T(filing["period_end"] + "T23:59:59+00:00") if filing else None
    use = [s for s in sigs if s["counts"] and start <= T(s["ts"]) <= end and (not f_end or T(s["ts"]) > f_end)]
    by = defaultdict(list)
    for s in use: by[s["tk"]].append(s)
    fw = {}
    if filing:
        pos = filing["positions"]; top = sorted(pos, key=lambda k: (-pos[k], k))[:P["max_names"]]
        mean = sum(pos[k] for k in top) / len(top); fw = {k: pos[k] / mean for k in top}
    rows = {tk: score_ticker(tk, by.get(tk, []), end, P, M, fw.get(tk)) for tk in sorted(set(by) | set(fw))}
    inc = {k: r for k, r in rows.items() if r["excluded_reason"] is None and r["raw"] > 0}
    if not inc: return rows, {}, 1.0, 0.0
    elig = sorted(inc); cv = conviction(inc, M)
    for t, (sc, _, parts) in cv.items(): inc[t]["score"] = r4(sc); inc[t]["score_parts"] = {q: r4(v) for q, v in parts.items()}
    keep = sorted(inc, key=lambda t: (-cv[t][0], t))[:P["max_names"]]
    for t in inc:
        if t not in keep: rows[t]["excluded_reason"] = "beyond max_names"
    inc = {t: inc[t] for t in keep}
    tr = sum(cv[t][1] for t in inc)
    overall = sum(cv[t][1] * inc[t]["confidence"] for t in inc) / tr if tr else 0.0
    cash = P["cash_min"] + (P["cash_max"] - P["cash_min"]) * (1 - overall)
    w = project({t: cv[t][1] for t in inc}, 1 - cash, P["cap"], P["floor"])
    for t in inc:
        if t not in w: rows[t]["excluded_reason"] = "below floor after projection"
    cash = 1 - sum(w.values())
    rows["__equity__"] = dict(inc=sorted(w), elig=elig, equity=1 - cash)
    return rows, w, cash, overall

def sensitivity(rows, w, P, M):
    """re-weight the SAME names with each factor weight and k moved ±perturb (one at a time, plus all-up/all-down
    on k); return {ticker: [min, max]} of the resulting weights (equity share held fixed)."""
    names = rows["__equity__"]["inc"]; eq = rows["__equity__"]["equity"]; sub = {t: rows[t] for t in rows["__equity__"]["elig"]}
    e = M["perturb"]; scen = []
    for q in M["factor_weights"]:
        for d in (1 - e, 1 + e):
            fw = dict(M["factor_weights"]); fw[q] *= d; scen.append((fw, M["k"]))
    for d in (1 - e, 1 + e): scen.append((M["factor_weights"], M["k"] * d))
    lo = {t: w[t] for t in names}; hi = dict(lo)
    for fw, k in scen:
        cv = conviction(sub, M, fw, k)
        ww = project({t: cv[t][1] for t in names}, eq, P["cap"], P["floor"])
        for t in names:
            lo[t] = min(lo[t], ww.get(t, 0.0)); hi[t] = max(hi[t], ww.get(t, 0.0))
    return {t: [r4(lo[t]), r4(hi[t])] for t in names}

def simulate(sigs, asof, P, M, filing=None):
    """event log: re-evaluate after every counted signal; new names are bought from cash, exits go to cash,
    adds/trims move weight against cash; a final rebalance aligns to the target."""
    start = asof - dt.timedelta(days=P["window_days"])
    f_end = T(filing["period_end"] + "T23:59:59+00:00") if filing else None
    ev = [s for s in sigs if s["counts"] and start <= T(s["ts"]) <= asof and (not f_end or T(s["ts"]) > f_end)]
    book, cash, log = {}, 1.0, []
    if filing:
        _, w0, c0, _ = build([], asof, P, M, filing, upto=f_end); book, cash = dict(w0), c0
        log.append(dict(ts=filing["period_end"], action="init_from_filing", weights={k: r4(v) for k, v in sorted(book.items())}, cash=r4(cash)))
    for s in ev:
        rows, target, _, _ = build(sigs, asof, P, M, filing, upto=T(s["ts"]))
        tk = s["tk"]; r = rows.get(tk)
        if r is None: continue
        before = book.get(tk, 0.0); after = before; act = None
        funded = None
        if tk in target and tk not in book:
            after = target[tk]; act = "buy_from_cash"
            spare = max(0.0, cash - P["cash_min"])
            if after > spare and book:                      # not enough cash: trim the others pro rata
                need = after - spare; tot = sum(book.values())
                for k in book: book[k] -= need * book[k] / tot
                cash += need; funded = dict(from_cash=r4(spare), pro_rata_trim_of_others=r4(need))
        elif tk in book and tk not in target:
            after = 0.0; act = "exit_to_cash"
        elif tk in book and s["action"] == "add":
            after = min(P["cap"], before * (1 + P["add_step"]), before + cash); act = "add_from_cash"
        elif tk in book and s["action"] == "trim":
            after = before * P["trim_factor"]; act = "trim_to_cash"
        if act and abs(after - before) > 1e-9:
            cash -= after - before
            if after > 0: book[tk] = after
            else: book.pop(tk, None)
            log.append(dict(ts=s["ts"], action=act, ticker=tk, weight_from=r4(before), weight_to=r4(after), cash_after=r4(cash),
                            level=r["level"], state=r["state"], funded=funded, source_url=s["url"], source_text=s["text"], votes=s["votes"]))
    _, target, tcash, _ = build(sigs, asof, P, M, filing)
    log.append(dict(ts=asof.isoformat(), action="rebalance_to_target", weights={k: r4(v) for k, v in sorted(target.items())}, cash=r4(tcash)))
    return log

def conc(w):
    e = sum(w.values())
    if not e: return None
    h = sum((v / e) ** 2 for v in w.values())
    return dict(hhi=r4(h), effective_n=r4(1 / h), top3_share_of_equity=r4(sum(sorted(w.values())[-3:]) / e),
                bottom5_share_of_equity=r4(sum(sorted(w.values())[:5]) / e))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts", required=True); ap.add_argument("--cache", required=True); ap.add_argument("--subject", required=True)
    ap.add_argument("--asof", required=True); ap.add_argument("--params"); ap.add_argument("--filing"); ap.add_argument("--coverage")
    ap.add_argument("--model-tag", default="default"); ap.add_argument("--conviction"); ap.add_argument("--mode", choices=["fast", "strict"], default="fast"); ap.add_argument("--out", required=True); ap.add_argument("--no-trade-log", action="store_true")
    a = ap.parse_args()
    P = load_json(os.path.join(ROOT, "tables", "params_default.json"))
    if a.params: P.update(load_json(a.params))
    M = load_json(os.path.join(ROOT, "tables", "conviction.json"))
    if a.conviction: M.update(load_json(a.conviction))
    alias = load_json(os.path.join(ROOT, "tables", "ticker_aliases.json"))
    asof = T(a.asof + "T23:59:59+00:00") if len(a.asof) == 10 else T(a.asof)
    start = asof - dt.timedelta(days=P["window_days"])
    filing = load_json(a.filing) if a.filing else None
    posts = [json.loads(l) for l in open(a.posts, encoding="utf-8")]
    win = [p for p in posts if p["type"] != "repost" and start <= T(p["created_at"]) <= asof]
    kept = None
    if a.mode == "fast":
        from prefilter import select
        kept, n_hits = select(win, posts, P.get("fast_max_posts", 0))
        # posts beyond the cap that were judged anyway (older runs / strict labels) still count
        jd = [os.path.join(a.cache, d) for d in sorted(os.listdir(a.cache)) if os.path.isdir(os.path.join(a.cache, d))]
        kept = kept | {p["id"] for p in win if any(os.path.exists(os.path.join(d, p["id"] + ".json")) for d in jd)}
        n_hits = max(n_hits, len(kept))
    sigs, missing = consensus(win, a.cache, alias, a.model_tag, mode=a.mode, kept=kept)
    rows, w, cash, overall = build(sigs, asof, P, M, filing)
    rng = sensitivity(rows, w, P, M) if w else {}
    rows.pop("__equity__", None)
    hold = []
    for k in sorted(w, key=lambda k: (-w[k], k)):
        r = rows[k]
        hold.append(dict(ticker=k, weight=r4(w[k]), range=rng[k], score=r["score"], score_parts=r["score_parts"],
                         confidence=r["confidence"], level=r["level"], state=r["state"],
                         factors=r["factors"], n_bull=r["n_bull"], n_bear=r["n_bear"], n_explicit=r["n_explicit"],
                         n_defends=r["n_defends"], asset_type=r["asset_type"], evidence=r["evidence"], state_history=r["state_history"]))
    cnt = sum(1 for s in sigs if s["counts"])
    jc = Counter(s.get("n_judges", 3) for s in sigs)
    cov = dict(window_start=start.isoformat(), window_end=asof.isoformat(), posts_in_window=len(win),
               posts_by_type=dict(sorted(Counter(p["type"] for p in win).items())), posts_missing_labels=missing, posts_prefiltered_out=(len(win) - n_hits) if kept is not None else 0, posts_capped_out=(n_hits - len(kept)) if kept is not None else 0,
               signals_total=len(sigs), signals_counted=cnt, signals_low_confidence=sum(s["low_conf"] for s in sigs),
               signals_discarded_kind=sum(1 for s in sigs if not s["low_conf"] and s["kind"] not in ("own", "endorse")),
               judge_mode=a.mode, signals_by_judge_count={str(k): v for k, v in sorted(jc.items())}, signals_unanimous_share=r4(sum(s["unanimous"] for s in sigs) / len(sigs)) if sigs else None)
    if a.coverage: cov.update(load_json(a.coverage))
    out = dict(subject=a.subject, mode="filing+posts" if filing else "posts_only", asof=asof.isoformat(),
               disclaimer=DISCLAIMER, holdings=hold, cash=r4(cash), overall_confidence=r4(overall),
               watchlist=[dict(ticker=k, level=r["level"], state=r["state"], reason=r["excluded_reason"], n_bull=r["n_bull"], n_bear=r["n_bear"])
                          for k, r in sorted(rows.items(), key=lambda x: (-x[1]["raw"], -x[1]["n_bull"], x[0])) if k not in w and r["level"] != "D"][:40],
               low_confidence_examples=[dict(url=s["url"], ticker=s["tk"], votes=s["votes"], text=s["text"][:160]) for s in sigs if s["low_conf"]][:30],
               coverage=cov, params=P, conviction_table=M, concentration=conc(w), filing=dict(period_end=filing["period_end"], source=filing.get("source")) if filing else None)
    if not a.no_trade_log: out["trade_log"] = simulate(sigs, asof, P, M, filing)
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    print(f"{a.out}: {len(hold)} names, cash {cash:.3f}, overall conf {overall:.3f}, signals {cnt}/{len(sigs)}, missing {missing}")

if __name__ == "__main__": main()
