# Weighting spec (v1.1 conviction score; v1.2 judge modes)

**One line:** weight ∝ score^k, where score = fixed-weight mix of account-normalised factors, then cap/floor; 減碼 names are discounted.

## Steps
1. Eligible names = evidence level A/B/C, state ≠ 清倉, more bull than bear signals, investable (see params).
2. Raw factors per name over the window:
   - `mentions_decayed` = Σ 0.5^(age/half_life) over bullish signals (half_life_days, default 60)
   - `explicit_or_add` = # signals with explicit holding or buy/add/hold + # buy/add actions
   - `defends` = # signals where the subject defends the name (護盤)
   - `size_points` = max(0, Σ decayed size points): max 3, large 2, normal 0.5, small −1
   - `level` = A 1.0, B 0.75, C 0.4 (categorical, not normalised)
3. Normalise within the account: part = log1p(x) / max over eligible names of log1p(x) → [0,1].
4. score = Σ w_f · part_f / Σ w_f with w = mentions 0.30, explicit_or_add 0.20, defends 0.20, size 0.15, level 0.15.
   Filer mode: score = 0.5 · score + 0.5 · (filing weight / max filing weight). State 減碼 → score × 0.8.
5. Keep the top `max_names` (15) by score; raw weight = score^k (k = 3); scale to the equity share (1 − cash);
   iterative cap 35% / floor 3% projection.
6. Cash = cash_min + (cash_max − cash_min) × (1 − weighted confidence) (unchanged).
7. Range = min/max weight over 12 scenarios: each factor weight × 0.8 and × 1.2 (one at a time), k × 0.8 and × 1.2.
   It is a sensitivity band, not a confidence interval.
8. Concentration reported as HHI and effective N = 1/HHI of the equity weights.

## Why these defaults (set before looking at backtests, not tuned on them)
- **Account-relative normalisation (log1p / max)**: the old absolute thresholds saturated for loud accounts (every name
  at 1.5×). Dividing by the account's own max makes the loudest name = 1 regardless of posting volume; log1p damps
  the long tail so a name with 190 mentions is not 10× one with 19.
- **Mentions 0.30**: most frequent and least noisy signal (needs only stance), so the largest weight.
- **Explicit/add 0.20, defends 0.20**: direct statements of ownership and willingness to defend a name are the
  strongest tells of real size, but rarer and judged with less agreement, so each below mentions.
- **Size wording 0.15**: very informative when present, but most names never get a size word; a higher weight would
  let one "all-in" post dominate.
- **Level 0.15**: already a gate (D excluded); small weight keeps B > C without double counting explicit holdings.
- **k = 3**: on scores between ~0.45 and ~0.95 (typical spread after normalisation) k = 3 gives roughly a 10× gap
  between top and bottom names, enough to separate them while the 35% cap still binds only on a clear leader.
  k = 1 reproduces near-equal weights; k ≥ 5 collapses to 2–3 names.
- **Floor 3%**: with 15 names a 5% floor forced 75% of equity into the floor; 3% keeps tail names visible without
  flattening the distribution.
- **Trim discount 0.8**: a reported trim means a smaller but still open position; 0.8 on the score ≈ 0.5 on the
  weight at k = 3.
- **Half-life 60 days**: one third of the 180-day window, so a name not mentioned for a quarter counts about half.
- **±20% perturbation**: the size of a plausible disagreement about any one default.

## Backtest effect (tweets only vs the subject's 13F, same judge labels)
| subject | version | MAE top10 (pp) | Spearman |
|---|---|---|---|
| fund A | v1 multiplier | 9.08 | −0.588 |
| fund A | v1.1 conviction | 8.81 | −0.574 |
| advisor B | v1 multiplier | 6.12 | −0.479 |
| advisor B | v1.1 conviction | 9.09 | −0.478 |
| advisor B ex-ETF | v1 / v1.1 | 6.32 / 8.22 | +0.154 / +0.154 |
Ranking barely changes (Spearman ±0.01); MAE rises for the diversified advisor because concentration is now higher
than his real book. Defaults were not changed to chase these numbers.

## Judge modes (v1.2): fast is the default
The weighting above is identical in both modes; only which posts get which judges changes.

| | fast (default) | strict (`--mode strict`) |
|---|---|---|
| posts judged | rule prefilter keeps posts naming a security ($cashtag, the account's own cashtagged tickers, `ticker_aliases`, `name_lexicon`) | every post in the window |
| judges | literal on all kept posts; trader + skeptic only on posts touching the current top 10 or with an unsure literal label (2 rounds) | trader, skeptic, literal on every post |
| cap | `fast_max_posts` 400 most relevant posts (2 × security tokens + 3 × position words + 2 × recency), `fast_max_escalate` 160 escalated posts; 0 = no cap | none |
| batch / parallel | 40 posts per call, 8 in parallel | 25, 6–8 in parallel |
| consensus | 3 judges → 2/3; 2 → must agree; 1 → literal label; single-judge unanimity credit 0.5 | 2/3 or low confidence |
| cache | shared, same prompt hashes | same |

Why: weights depend almost only on the names that make the top 10; their posts still get the 2-of-3 vote. Tail
names (rank 11–15, floor 3%) are the ones that may rest on one judge, so they are the least reliable in fast mode.

Test on the example account (1,006 posts, as of 2026-11-09 23:00 Asia/Taipei, fresh cache):

| | strict | fast |
|---|---|---|
| posts judged | 1,006 × 3 judges | 965 kept (41 dropped) → literal 965, trader 562, skeptic 542 |
| LLM calls | ≈150 (batch 20–25) | 64 (4 timed out after 600 s, retried) |
| wall time | ≈ 60 min (20:27–21:26) | 36 min (21:59–22:35; shared the CLI with a 2nd run) |
| signals by # judges | all 3 | 1: 893, 2: 117, 3: 3,153 |
| names / cash / eff. N | 15 / 7.7% / 8.27 | 15 / 8.0% / 8.44 |

Fast vs strict: Spearman 0.949 over the 16-name union, top-10 overlap 9/10, Σ|Δweight| 10.5 pp. Only in strict: IREN
(3.0%); only in fast: EPA:SOI (3.0%). Top 5 order: strict SIVE 25.7, AAOI 10.3, AXTI 6.8, 3363 6.1, NBIS 6.1; fast
SIVE 25.0, AAOI 10.7, AXTI 7.1, NBIS 6.2, CCXI 5.3. Two fast engine runs on the same cache gave the same md5.
This account mentions a security in 96% of posts, so the prefilter saved little; macro / chatty accounts save more.

### Cap and parallelism (v1.2.1)
Goal: any account in about 10 minutes. Budget = ⌈400/40⌉ literal + 2 × ⌈160/40⌉ escalation = 18 calls, 3 waves at
parallel 8. Relevance ranking (deterministic, ties newer first) = 2 × min(3, security tokens) + 3 × [position word]
+ 2 × 0.5^(age/60 d) (+0.5 for a reply with a position word). Escalation order: posts touching the current top 10
first, then other unsure posts, newest first.

| example account (fresh caches) | calls | wall | Spearman vs strict | top-10 overlap vs strict |
|---|---|---|---|---|
| fast, no cap | 64 | 36 min | 0.949 | 9 |
| fast, cap 400/160 (default) | 18 | 11.8 min | 0.592 | 7 |

Capped top 5: SIVE 26.6, AAOI 12.3, LITE 8.0, AXTI 7.0, KRX:005930 5.1; cash 8.4%; 553 posts capped out; two engine runs
same md5. Second account (1,493 posts): capped 9.2 min vs uncapped 31 min, Spearman 0.678 / top-10 8 of 10 against the
uncapped fast run, top 2 unchanged. The cap is a speed/accuracy trade: the leaders survive, the tail does not.
Defaults were set from the 10-minute budget, not tuned on these comparisons.
