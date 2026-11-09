# Examples
- `serenity_portfolio.json` / `.md` — posts-only demo, as of 2026-11-09 23:00 (Asia/Taipei), 994 posts in a 180-day window,
  3 judges × 994 posts, 0 posts missing labels. Run twice → identical md5.
- `backtest_*.json`, `backtest_v1_vs_13f.json` — tweet-only portfolios scored against the person's 13F for the same period end.

Weighting v1.1 (conviction score, spec.md). Serenity concentration: old multiplier version effective N 14.98
(top 3 = 20.6% of equity, bottom 5 = 32.0%) → v1.1 effective N 8.27 (top 3 = 46.3%, bottom 5 = 17.1%).

| subject (13F period end) | round | recall@top10 | MAE top10 (pp) | Spearman |
|---|---|---|---|---|
| fund A (2026-03-31) | old lexicon judges | 0.10 | 9.86 | −0.438 |
| fund A | v1 LLM judges + multiplier | 0.20 | 9.08 | −0.588 |
| fund A | v1.1 conviction | 0.20 | 8.81 | −0.574 |
| advisor B (2026-09-30) | old (tuned) | 0.20 | 5.84 | −0.562 |
| advisor B | v1 multiplier | 0.20 | 6.12 | −0.479 |
| advisor B | v1.1 conviction | 0.20 | 9.09 | −0.478 |
| advisor B ex-ETF | v1 → v1.1 | 0.50 | 6.32 → 8.22 | +0.154 → +0.154 |

Judge modes: `serenity_portfolio.json` is the **strict** run (`--mode strict`). The fast-mode run on a fresh cache
(`serenity_portfolio_fast.json`, spec.md "Judge modes") gave Spearman 0.949 / top-10 overlap 9 of 10 against it.
- `serenity_portfolio_fast_capped.json` / `.md` — default fast mode (cap 400/160, parallel 8): 11.8 min, Spearman 0.592 / top-10 overlap 7 of 10 vs strict.
- `serenity_portfolio_fast.json` / `.md` — fast without cap: 36 min, Spearman 0.949 / 9 of 10.
