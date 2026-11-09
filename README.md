# tweet-portfolio

An agent skill that infers a person's portfolio and approximate weights from about six months of their public X posts, optionally anchored on 13F / Form 4 / congressional PTR filings.

- `SKILL.md`: the recipe the agent follows (route → fetch → judge → weights → output)
- `scripts/`: normalize, prefilter, judge (fast / strict), engine, render, filing_base
- `prompts/`: three independent judge prompts and the shared JSON schema
- `tables/`: default parameters, conviction weights, ticker aliases, name lexicon
- `spec.md`: the weighting formula and why each default was chosen
- `examples/`: a worked run and backtests

Bullish/bearish labels come from three LLM judges with a 2-of-3 vote and a label cache, so the same account with the same cache always gives the same weights.

> 本組合是從公開推文內容推斷，不是真實持倉。作者未公開、未提及的大部位可能才是多數；推文可能誇大、延遲或刻意誤導。權重為規則化推估，不代表實際比例。非投資建議。
