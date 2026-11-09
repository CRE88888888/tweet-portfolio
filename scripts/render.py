#!/usr/bin/env python3
"""render.py portfolio.json [--tz Asia/Taipei] > summary.md — markdown summary with the fixed disclaimer."""
import json, sys, argparse, datetime as dt
from zoneinfo import ZoneInfo
ap = argparse.ArgumentParser(); ap.add_argument("file"); ap.add_argument("--tz", default="UTC"); a = ap.parse_args()
X = json.load(open(a.file, encoding="utf-8")); tz = ZoneInfo(a.tz)
def lt(s): return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(tz).strftime("%Y-%m-%d %H:%M") + f" ({a.tz})"
pc = lambda x: f"{100*x:.1f}%"
c = X["coverage"]
L = [f"# 推文推斷組合：{X['subject']}", "", f"> ⚠️ {X['disclaimer']}", "",
     f"- 模式：{X['mode']}；截至 {lt(X['asof'])}；視窗 {c['window_start'][:10]} → {c['window_end'][:10]}",
     f"- 整體信心 {X['overall_confidence']:.2f}；現金 {pc(X['cash'])}",
     f"- 覆蓋：視窗內 {c['posts_in_window']} 則（{', '.join(f'{k} {v}' for k, v in c['posts_by_type'].items())}），"
     f"有效訊號 {c['signals_counted']}/{c['signals_total']}，低信心 {c['signals_low_confidence']}，三判全一致比例 {c['signals_unanimous_share']}",
     ]
for k in ("reply_coverage", "fetch_mode", "notes"):
    if c.get(k): L.append(f"- {k}: {c[k]}")
L += ["", "| # | 標的 | 權重 | 敏感度區間 | conviction | 信心 | 證據 | 狀態 | 看多/看空 | 護盤 | 明說持有+加碼 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
for i, h in enumerate(X["holdings"], 1):
    L.append(f"| {i} | {h['ticker']} | {pc(h['weight'])} | {pc(h['range'][0])}–{pc(h['range'][1])} | {h['score']:.3f} | {h['confidence']:.2f} | {h['level']} | {h['state']} | {h['n_bull']}/{h['n_bear']} | {h['n_defends']} | {h['factors']['explicit_or_add']} |")
L.append(f"| | 現金 | {pc(X['cash'])} | | | | | | | | |")
cc = X.get("concentration") or {}
if cc: L.append(f"\n- 集中度：HHI {cc['hhi']}，有效檔數 {cc['effective_n']}，前 3 大占股票部位 {pc(cc['top3_share_of_equity'])}，後 5 檔 {pc(cc['bottom5_share_of_equity'])}")
L.append("- 區間＝因子權重與 k 各擾動 ±20% 時的權重範圍（敏感度，非統計信賴區間）")
L += ["", "## 支持貼文"]
for h in X["holdings"]:
    L.append(f"**{h['ticker']}**")
    for e in h["evidence"]:
        L.append(f"- {lt(e['date'])} [{e['holding']}/{e['action']}] “{e['quote'][:160]}” — {e['url']}")
if X.get("watchlist"):
    L += ["", "## 未納入（觀察名單）", ", ".join(f"{w['ticker']}（{w['level']}，{w['reason']}）" for w in X["watchlist"][:15])]
tl = [t for t in X.get("trade_log", []) if t.get("ticker")]
if tl:
    L += ["", f"## 調整紀錄（最近 10 筆，共 {len(tl)} 筆）"]
    for t in tl[-10:]:
        L.append(f"- {lt(t['ts'])} {t['action']} {t['ticker']} {pc(t['weight_from'])}→{pc(t['weight_to'])}：“{t['source_text'][:120]}” {t['source_url']}")
L += ["", f"> {X['disclaimer']}", ""]
print("\n".join(L))
