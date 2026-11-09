## Input
A JSON list of posts by ONE author (the "subject"). Each item: `id`, `date`, `type` (original | reply | quote | thread),
`text` (the subject's own words), and optionally `context` (the post the subject replied to or quoted, written by SOMEONE ELSE).

## Output (JSON only, must match the schema)
`{"posts":[{"id":"<post id>","labels":[ ... ]}]}` — one entry per input post, in the same order. `labels` may be empty.
Each label is about ONE security as seen by the SUBJECT in THIS post:
- `ticker`: primary US ticker in capitals without `$` (ADR if the company is foreign and has one, e.g. TSM, ASML).
  No US listing → `EXCHANGE:SYMBOL` (e.g. `TPE:2330` only if no ADR, `KRX:000660`). Crypto → `BTC`, `ETH`, `SOL`.
  Private company → `PRIVATE:NAME`. Index/ETF → its ETF ticker. Never invent a ticker for a vague theme ("AI stocks").
- `stance`: bull | bear | neutral — the subject's view of the security AS AN INVESTMENT.
- `holding`: explicit (subject says he/his fund owns, bought, holds, is long) | implied (first-person ownership
  is strongly implied, e.g. "our position", "we're shareholders", P&L talk about it) | sold (says he exited / sold all) | none.
- `action`: buy | add | hold | trim | sell | none — the position change the subject reports in this post.
- `size`: max (largest / top / biggest position, all-in, "most of my portfolio") | large ("big position", "heavy", "a lot")
  | small ("small position", "starter", "a little", "一小部分", "tiny") | normal (holding mentioned without size words) | none.
- `defends`: true if the subject defends this security / his position against criticism, doubt or a price drop
  (replying to a skeptic, "not selling", "buying the dip", "haters"). Otherwise false.
- `kind`: own (subject's own claim) | endorse (subject agrees with the quoted/replied post's claim about it) |
  quote_only (re-shares without taking a view) | irony (sarcasm/joke) | about_others (discusses someone else's position).
- `evidence_quote`: the shortest verbatim span (≤160 chars) from the SUBJECT's `text` that supports the label.
Only label securities the subject actually refers to (name, ticker, product clearly standing for the company).
Securities mentioned only inside `context` get a label only if the subject's own text takes a view on them.
Judge only the text; do not use outside knowledge of the subject's real holdings.
