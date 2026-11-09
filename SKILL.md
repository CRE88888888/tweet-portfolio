---
name: tweet-portfolio
description: >-
  When the user asks what stocks an X/Twitter investor, KOL or fund manager
  holds, wants a person's portfolio or position weights inferred from their
  posts (optionally anchored on their 13F / Form 4 / congressional PTR filings),
  or wants that inferred portfolio tracked and updated over time.
---
# Tweet → inferred portfolio

Turns one person's public X posts (plus their SEC / congressional filings, if any) into a weighted long-only
portfolio with per-name confidence, evidence links, a trade log and a fixed disclaimer.
It is an **inference**, never a statement of real holdings. Most KOLs file nothing, so there is no ground truth;
distortion is handled by the disclaimer, confidence bands and a coverage report — never by inventing numbers.

Files (relative to this skill's directory):
- `scripts/normalize.py` – merge saved X read-tool results into `posts.jsonl`
- `scripts/judge.py` – LLM judges + label cache; `--mode fast` (default) or `--mode strict`
- `scripts/prefilter.py` – fast-mode rule prefilter (does a post mention any security at all?)
- `scripts/engine.py` – consensus → evidence levels → state machine → conviction score → weights → cash → trade log
- `scripts/render.py` – markdown summary
- `scripts/filing_base.py` – 13F info table (or manual Form 4 / PTR book) → filing base
- `prompts/judge_trader.md`, `judge_skeptic.md`, `judge_literal.md`, shared `_common_output.md`, `schema.json`
- `tables/params_default.json`, `tables/conviction.json`, `tables/ticker_aliases.json`, `tables/name_lexicon.json` (prefilter only)
- `spec.md` – weighting formula and why each default was chosen
- `examples/` – a full worked run (JSON + markdown)

Setup: the scripts, prompts and tables live in https://github.com/cre8-protocol/tweet-portfolio. On first use run
`git clone https://github.com/cre8-protocol/tweet-portfolio ~/tweet-portfolio` (or `git -C ~/tweet-portfolio pull` to update),
then copy that folder to a work folder on your box (e.g. `work/<handle>/`) and run the scripts from there. All paths
below are relative to the repo root. Python 3 standard library only; the judge step needs an LLM CLI (default: Codex CLI) or sub-agents.
Read your X tools guide skill before the first X call.

## 1. Route: filer or not
Check, in this order, with WebFetch / web search (EDGAR needs a descriptive User-Agent; use the EDGAR full-text
search or `https://data.sec.gov/submissions/CIK##########.json` for the CIK):
1. **13F filer** (manages ≥ $100M; fund name usually in the X bio) → latest 13F-HR information table.
2. **Form 4 insider** (officer/director/10% owner of a company) → recent Form 4s for that issuer.
3. **US Congress member** → House / Senate periodic transaction reports (PTR).
4. Otherwise → **posts-only** mode.

Filer mode: save the 13F XML, run `filing_base.py --period-end <period end> --source <url> --cusip-map <map> table.xml
> filing_base.json` (Form 4 / PTR: `--manual TICKER=weight,...` from the reported positions). Those names enter at
level A; only posts **after the period end** update them (an explicit sale removes a name; adds raise its
score, a trim discounts it; new names from posts join at level B/C). State in the output which filing and date the base came from.
13F lags up to 45 days after quarter end and omits shorts, cash, non-US listings, OTC-only names, private stakes and crypto.

## 2. Fetch posts (last 180 days)
Use the X read tools (search over the full archive, get-posts-by-ids). Save every raw result page to a file.
- `from:<handle>` over the window, paging until no next token: originals, quotes, **and the person's replies and
  self-threads** — replies are mandatory; KOLs often reveal positions there ("still holding", "added more").
  Request `created_at, author_id, referenced_tweets, note_tweet` and expand `referenced_tweets.id` so the quoted /
  replied-to post comes back in `includes`; it is judged together with the post.
- Long posts: search results cut text at ~280 characters. For every post whose text is ≥ 230 characters and has no
  `note_tweet`, re-fetch by id in batches of 100 (one read per batch) to get `note_tweet` / `article` full text.
- Quota: one search page ≈ 25 posts per read. Before starting, estimate pages = (posts/day × 180) / 25. If that
  exceeds the read budget, fall back to **ticker-keyword search**: `from:<handle> (own OR bought OR sold OR position
  OR shares OR long OR added OR trimmed OR <their tickers>)` and add `is:reply` variants. Record the mode.
- Write `coverage.json` by hand: `{"fetch_mode": "full" | "keyword", "reply_coverage": "full" | "keyword: <query>" |
  "none", "reads_used": N, "notes": "..."}`. It is merged into the output and shown to the user.
- `python3 scripts/normalize.py --subject-id <author_id> --out posts.jsonl raw/*.txt`

## 3. Judge: fast mode (default) or strict mode
Three judge prompts differ in angle and wording (trader, skeptic, literal extractor). Each call is a fresh process
that sees only its own prompt and the posts (temperature 0 where the backend allows it).

**Fast mode (default, `--mode fast`)**
1. Rule prefilter (`prefilter.py`): a post is kept if its text, or the quoted / replied-to text, has a `$cashtag`, a
   ticker the account cashtags anywhere in the file (as a bare word), a `ticker_aliases.json` key, or a word from
   `name_lexicon.json` (company / coin names, 中文名稱). Posts that mention no security are dropped and never judged.
   The prefilter only decides *whether* to judge; it never produces a label.
2. **Cap** (`fast_max_posts`, default 400): if more posts pass the prefilter, only the 400 most relevant are judged.
   Relevance = 2 × security tokens in the post (max 3) + 3 if it has position words (bought / sold / holding /
   position / added / trim / 加倉 / 持有 / 清倉 / 部位 …) + 2 × 0.5^(age / 60 d) recency; ties → newer first. Deterministic.
   Kept posts go to the **literal** judge in batches of 40 (`--batch`, 30–50 recommended), same schema as strict,
   8 calls in parallel (`--parallel`, default 8; 6–8 is the useful range for one Codex CLI login).
3. Escalation: a portfolio is built from the current labels; posts whose literal labels touch a **top-10** name
   (`--escalate-top 10`) or are low-confidence (implied holding, endorse, or bull/bear with an action but no stated
   holding) are sent to **trader and skeptic** too, for a 2-of-3 vote, top-10 posts first, newest first, at most
   `fast_max_escalate` posts (default 160) across both rounds. Repeated once (`--rounds 2`) because the top 10 can
   move after escalation. Budget per account: ⌈400/40⌉ + 2 × ⌈160/40⌉ = 18 calls ≈ 3 waves at parallel 8 ≈ 10 min.
   Change the caps with `--max-posts N --max-escalate M` on `judge.py` (0 = no cap) **and** the same values in a
   `--params` file for `engine.py` (`{"fast_max_posts": N, "fast_max_escalate": M}`) so both pick the same posts;
   labels already in the cache are always used, capped or not.
4. Labels share the strict cache (same prompt hash), so strict labels already on disk are reused and a later
   `--mode strict` run only adds what is missing.

**Strict mode (`--mode strict`)**: every post in the window goes to all three judges (batch 25), no prefilter.
Use it for a final or published result, a backtest, or when fast and strict disagree on a name you care about.
Pass the same `--mode` to `engine.py`.

Measured on the 1,006-post example account (`examples/`, fresh caches, as of 2026-11-09 23:00 Asia/Taipei):

| mode | LLM calls | wall time | vs strict: Spearman / top-10 overlap |
|---|---|---|---|
| strict | ≈150 | ≈60 min | — |
| fast, no cap (`--max-posts 0 --max-escalate 0`) | 64 | 36 min (parallel 8, shared CLI) | 0.949 / 9 of 10 |
| **fast default** (cap 400 / 160, parallel 8) | 18 | **11.8 min** | 0.592 / 7 of 10 |

A second account (1,493 posts, 813 pass the prefilter): fast no cap 31 min; fast default **9.2 min**, against fast
no cap Spearman 0.678, top-10 overlap 8 of 10, same top 2. The cap keeps the loudest names (top 2–5 stable) but moves
the 3%-floor tail and the middle ranks. Pick by need:
- quick look / first pass → default fast (~10 min);
- a published or tracked result → `--max-posts 0 --max-escalate 0` (fast, no cap) or `--mode strict`.
Labels are shared, so a later uncapped or strict run only judges what is still missing.

Commands:
- LLM CLI on the box: `judge.py --posts posts.jsonl --start <d> --end <d> --cache cache/ [--mode strict] --llm-cmd '<cmd>'`
  where `<cmd>` reads the prompt on stdin and writes schema-valid JSON to `{out}` (schema at `{schema}`).
  The default is the Codex CLI; swap in any CLI that supports a JSON schema. `--timeout 600` kills a hung call; the
  batch is retried on the next run.
- Sub-agents: `judge.py ... --export-dir req/` writes one request per batch; give each file to a **fresh** sub-agent
  with no other context, save its JSON answer as `resp/<same name>.json`, then `judge.py ... --import-dir resp/`.
  In fast mode, run export → import twice more (escalation needs the literal labels first).
Per post and security each judge returns: `stance` (bull/bear/neutral) and `holding`
(explicit/implied/sold/none) as **separate dimensions**, plus `action` (buy/add/hold/trim/sell), `size` wording
(max = largest position / all-in, large, normal, small = 一小部分 / starter), `defends` (defends the name against
critics or a drawdown — 護盤), `kind` (own / endorse / quote_only / irony / about_others) and a verbatim quote.

Labels are cached per judge under `cache/<judge>_<hash of prompt+schema+model>/<post id>.json`. A rerun only judges
new posts; the same posts + same cache + same params always give byte-identical output. Keep the cache with the
result — it is the reproducibility record. Changing a prompt changes its hash and starts a new cache.

## 4. Consensus, evidence, weights (`scripts/engine.py`)
- **Consensus** per (post, security): a judge that did not list it votes neutral/none. Strict: stance and holding
  each need 2 of 3; if either has no majority the signal is **low confidence** and is not counted (listed in the
  output). Fast: the judges the post has decide — 3 judges → 2 of 3, 2 judges → they must agree, literal only →
  its label is used (the unanimity part of confidence counts 0.5 for single-judge signals). Prefiltered posts are
  not counted as missing labels; `coverage.signals_by_judge_count` reports how many signals rest on 1/2/3 judges.
  quote_only / irony / about_others never count.
- **Evidence level** per security over the window: **A** filing; **B** explicit holding or a reported buy/add/hold
  (or implied holding ≥ 2 times) and more bull than bear signals; **C** ≥ 3 bullish mentions on distinct days within
  30 days; **D** anything else (watch list only).
- **State machine** driven by the reported action: 未知 → 建倉 → 加碼 → 持有 → 減碼 → 清倉. 清倉 removes the name until a
  new buy/hold. Only bullish, not-sold names enter; bearish names are excluded.
- **Weight** (details and the reasons behind every default in `spec.md`): each eligible name gets a
  **conviction score** from five factors — time-decayed bullish mentions (half-life 60 d), explicit holding/add
  count, defends count (護盤), size wording points (max/all-in high, 一小部分 low), evidence level. Each count is
  normalised **within the account** (log1p ÷ the account's max) so loud and quiet accounts both get separation, then
  combined with fixed weights from `tables/conviction.json` (0.30 / 0.20 / 0.20 / 0.15 / 0.15). 減碼 names × 0.8.
  Top 15 by score; weight ∝ score^k (k = 3), projected to **cap 35% / floor 3%**. Filer mode mixes the filing
  weight 50/50 into the score.
- **Confidence** per name = 0.5 × level score (A 1.0, B 0.9, C 0.6) + 0.3 × judge unanimity + 0.2 × min(1, decayed
  mentions / 6). **Cash** = 2.5% + (30% − 2.5%) × (1 − weighted confidence), plus anything the cap cannot place;
  100% when nothing qualifies.
- **Range** = lowest/highest weight when each factor weight and k are moved ±20% one at a time — a sensitivity band,
  not a statistical interval. The output also reports HHI and effective number of names.
- **Trade log**: signals replayed in time order; a new name is bought **from cash**, exits go to cash, adds/trims move
  weight against cash; a final row rebalances to the target. Every row carries the source post text, time and link.
- All numbers live in `tables/*.json`; the user may override any of them with `--params my.json`
  (e.g. `{"cap":0.25,"floor":0.02,"window_days":90,"half_life_days":30}`) and any conviction value with `--conviction` (e.g. `{"k":2}`). Report which overrides were used.

```
python3 scripts/engine.py --posts posts.jsonl --cache cache/ --subject @handle --asof YYYY-MM-DD [--mode strict] \
  [--filing filing_base.json] [--coverage coverage.json] [--params my.json] [--conviction my_conviction.json] --out portfolio.json
python3 scripts/render.py portfolio.json --tz <user's IANA zone> > portfolio.md
```

## 5. Output to the user
- `portfolio.json`: holdings (ticker, weight, range, confidence, level, state, conviction score and its parts, 2–3 evidence links),
  cash, watch list, low-confidence examples, coverage, params, trade log.
- `portfolio.md`: table + evidence + recent adjustments, with the disclaimer at top and bottom.
- Always show this disclaimer verbatim, untranslated parts included:
  > 本組合是從公開推文內容推斷，不是真實持倉。作者未公開、未提及的大部位可能才是多數；推文可能誇大、延遲或刻意誤導。權重為規則化推估，不代表實際比例。非投資建議。
- Times in the user's own zone with a label. Mention coverage gaps (keyword-only replies, unexpanded long posts,
  missing labels) next to the numbers, not in a footnote.
- Never present the result as what the person owns, never fill gaps with outside knowledge of their book, and never
  place trades from it.

## 6. Updating later
Fetch only posts newer than the last run (`start_time` = last post time), re-fetch long ones, run `normalize.py`
into the same `posts.jsonl`, `judge.py` with the same `--mode` (judges only the new posts), `engine.py --asof <today>`. For filers, swap the
base when a new filing lands. Compare with the previous `portfolio.json` and report entries, exits and moves > 3 pp.

## Long runs: save progress
Judging is the slow part (a 1,000-post account: strict ≈ 120–150 LLM calls, ~1 h; fast default ≤ 18 calls, ~10 min; fast without cap ≈ 40–65 calls, 30–40 min). The label cache *is* the
checkpoint: if the run is interrupted, rerun the same `judge.py` command and only unlabelled posts are sent.
Keep a short `PROGRESS.md` in the work folder (what was fetched, reads used, which judge is incomplete, next command)
so another session can resume. Two workers can share a cache safely (`--reverse` on the second one).

## Known limits
- Hosted LLM CLIs may not expose temperature; determinism comes from the label cache, not from the model.
- Judging costs LLM tokens: strict ~3 calls per 25 posts; fast default ≤ 18 calls per account; fast without cap ~1.5–2.2 calls per 40 kept posts.
- Fast default cap: posts beyond the 400 most relevant are never judged, so names that live only in short or old
  posts can drop out; `coverage.posts_capped_out` says how many. Report it next to the weights.
- Fast mode: a post naming a security only by a nickname missing from `name_lexicon.json` is never judged, and names
  outside the top 10 mostly rest on the literal judge alone. Add aliases to the lexicon, or rerun `--mode strict`.
- Tweet-only inference systematically misses quiet, large positions (backtests against 13Fs recover a minority of
  the top-10 names) and over-weights loud ones. ETF-heavy advisory books look nothing like the advisor's feed.
- Weights are relative within one account: the top name is the person's *loudest* conviction, not necessarily
  their biggest position; diversified advisors look more concentrated than their real book (backtest MAE rises).
- "I missed it / should have bought" style posts can be read as bullish without a holding and still reach level C.
- Backtests against 13Fs (two funds): top-10 recall 0.2–0.5, Spearman ≈ −0.6 to +0.15. Treat weights as ordinal at best.
- Tickers come from the judges; check `watchlist` and `ticker_aliases.json` for share-class or ADR splits.
