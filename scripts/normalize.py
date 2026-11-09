#!/usr/bin/env python3
"""normalize.py --subject-id <author_id> --out posts.jsonl <x_result_file> [...]

Merge raw X read-tool results (search / timeline / get-posts-by-ids pages, saved as files) into one
posts.jsonl. Dedupe by id; longer text wins (note_tweet / article full text beats a truncated copy).
Each output row: {id, created_at, type, text, context:[{rel, id, author_id, text}], url}
type: original | reply | thread (reply to self) | quote | repost (reposts are kept but never judged).
"""
import json, sys, os, argparse

def full(d):
    if d.get("note_tweet"): return d["note_tweet"].get("text") or d.get("text", "")
    a = d.get("article") or {}
    if a.get("plain_text") or a.get("title"): return (a.get("title", "") + "\n" + a.get("plain_text", "")).strip()
    return d.get("text", "")

def objects(path):
    t = open(path, encoding="utf-8").read(); dec = json.JSONDecoder(); i = 0
    while True:
        i = t.find("{", i)
        if i < 0: return
        try:
            o, j = dec.raw_decode(t[i:])
        except ValueError:
            i += 1; continue
        if isinstance(o, dict) and ("data" in o or "meta" in o): yield o
        i += j

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--subject-id", default=None); ap.add_argument("--out", required=True)
    ap.add_argument("files", nargs="+"); a = ap.parse_args()
    rows = {}
    if os.path.exists(a.out):
        for l in open(a.out, encoding="utf-8"): r = json.loads(l); rows[r["id"]] = r
    for f in a.files:
        for o in objects(f):
            data = o.get("data") or []
            if isinstance(data, dict): data = [data]
            inc = {x["id"]: x for x in (o.get("includes") or {}).get("tweets", [])}
            for d in data:
                if a.subject_id and d.get("author_id") and d["author_id"] != a.subject_id: continue
                ctx, typ = [], "original"
                for r in d.get("referenced_tweets") or []:
                    x = inc.get(r["id"], {})
                    ctx.append({"rel": r["type"], "id": r["id"], "author_id": x.get("author_id"), "text": full(x)[:2000]})
                    if r["type"] == "retweeted": typ = "repost"
                    elif r["type"] == "quoted" and typ == "original": typ = "quote"
                    elif r["type"] == "replied_to":
                        typ = "thread" if (a.subject_id and x.get("author_id") == a.subject_id) else "reply"
                new = {"id": d["id"], "created_at": d.get("created_at"), "type": typ, "text": full(d),
                       "context": ctx, "url": f"https://x.com/i/status/{d['id']}"}
                old = rows.get(d["id"])
                if old:
                    if len(new["text"]) < len(old["text"]): new["text"] = old["text"]
                    if not new["created_at"]: new["created_at"] = old["created_at"]
                    oc = {c["id"]: c for c in old.get("context", [])}
                    for c in new["context"]:
                        if c["id"] in oc and len(oc[c["id"]]["text"]) > len(c["text"]): c["text"] = oc[c["id"]]["text"]
                    if not new["context"]: new["context"], new["type"] = old.get("context", []), old.get("type", typ)
                rows[d["id"]] = new
    out = sorted(rows.values(), key=lambda r: (r["created_at"] or "", r["id"]))
    with open(a.out, "w", encoding="utf-8") as fo:
        for r in out: fo.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"{len(out)} posts -> {a.out}")

if __name__ == "__main__": main()
