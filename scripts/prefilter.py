"""Fast-mode rule prefilter: keep posts that mention any security-like token. Used only to skip LLM calls."""
import json, os, re
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASH = re.compile(r"\$[A-Za-z][A-Za-z0-9.]{0,9}\b")
def _load(name): return json.load(open(os.path.join(ROOT, "tables", name), encoding="utf-8"))

def build_matcher(posts):
    words = [w.lower() for w in _load("name_lexicon.json")["words"]]
    words += [k.lower() for k in _load("ticker_aliases.json") if not k.endswith(":")]
    # tickers this account has cashtagged anywhere become bare-word tokens for its other posts
    own = set()
    for p in posts:
        for m in CASH.findall(p.get("text", "")): own.add(m[1:].lower())
    latin = sorted({w for w in words + list(own) if re.fullmatch(r"[a-z0-9&.\- ]+", w)}, key=len, reverse=True)
    cjk = sorted({w for w in words if w not in latin}, key=len, reverse=True)
    rx = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(w) for w in latin) + r")(?![a-z0-9])") if latin else None
    def hit(p):
        t = p.get("text", "") + "\n" + "\n".join(c.get("text", "") for c in p.get("context", []) if c.get("rel") in ("quoted", "replied_to"))
        if CASH.search(t): return True
        tl = t.lower()
        if rx and rx.search(tl): return True
        return any(w in tl for w in cjk)
    return hit

POS = re.compile(r"(?<![a-z])(own|owns|owned|bought|buy|buying|sold|sell|selling|holding|holdings|hold|added|adding|add|trim|trimmed|"
                 r"position|positions|long|short|shares|stake|all in|all-in|calls|puts|entry|exited|"
                 r"portfolio|allocation)(?![a-z])|加倉|加碼|買入|買進|買了|賣出|賣了|持有|持倉|清倉|減倉|減碼|部位|倉位|重倉|抄底|梭哈|建倉")

def relevance(p, hit_tokens, end_ts):
    """rank used when a fast-mode cap applies: security tokens + position words + recency (half-life 60 d)."""
    import datetime as dt
    t = p.get("text", "")
    n_tok = min(3, hit_tokens(p))
    pos = 1 if POS.search(t.lower()) else 0
    age = max(0.0, (end_ts - dt.datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))).total_seconds() / 86400)
    return 2.0 * n_tok + 3.0 * pos + 2.0 * 0.5 ** (age / 60) + (0.5 if p.get("type") == "reply" and pos else 0.0)

def select(window_posts, all_posts, max_posts=0):
    """fast-mode kept set = prefilter hits; if more than max_posts (>0), keep the top max_posts by relevance.
    Deterministic: ties broken by newer first, then id. Returns (kept ids, n_hits)."""
    import datetime as dt
    hit = build_matcher(all_posts)
    hits = [p for p in window_posts if hit(p)]
    if not max_posts or len(hits) <= max_posts: return {p["id"] for p in hits}, len(hits)
    end_ts = max(dt.datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")) for p in window_posts)
    def ntok(p):
        t = p.get("text", "")
        return len(set(m.lower() for m in CASH.findall(t))) + (1 if hit({"text": t}) else 0)
    hits.sort(key=lambda p: (-round(relevance(p, ntok, end_ts), 6), -int(p["id"])))
    return {p["id"] for p in hits[:max_posts]}, len(hits)
