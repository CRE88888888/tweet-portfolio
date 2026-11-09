#!/usr/bin/env python3
"""filing_base.py --period-end YYYY-MM-DD --source <url> [--cusip-map map.json] [--manual t=w,...] infotable.xml > filing_base.json

Turns a 13F information table (XML saved from EDGAR) into the long-equity base used by engine.py --filing.
Options (putCall) are dropped; share classes are summed; weights are value shares of the long book.
CUSIPs not in --cusip-map are kept as NAME:<issuer> so nothing is silently lost — map them before trusting the base.
For Form 4 / PTR (congress) filers, which list trades not books, build the base by hand with --manual TICKER=weight.
"""
import sys, json, argparse, re, xml.etree.ElementTree as ET
from collections import defaultdict
ap = argparse.ArgumentParser(); ap.add_argument("--period-end", required=True); ap.add_argument("--source", default="")
ap.add_argument("--cusip-map"); ap.add_argument("--manual"); ap.add_argument("xml", nargs="?"); a = ap.parse_args()
agg = defaultdict(float)
if a.manual:
    for kv in a.manual.split(","): k, v = kv.split("="); agg[k.strip().upper()] += float(v)
if a.xml:
    cm = json.load(open(a.cusip_map)) if a.cusip_map else {}
    root = ET.parse(a.xml).getroot()
    strip = lambda t: re.sub(r"^\{.*\}", "", t)
    for it in root.iter():
        if strip(it.tag) != "infoTable": continue
        d = {strip(c.tag): (c.text or "").strip() for c in it.iter()}
        if d.get("putCall"): continue
        tk = cm.get(d.get("cusip", "")) or "NAME:" + d.get("nameOfIssuer", "?").upper()
        agg[tk] += float(d.get("value", 0) or 0)
tot = sum(agg.values()) or 1
json.dump(dict(period_end=a.period_end, source=a.source,
               positions={k: round(v / tot, 6) for k, v in sorted(agg.items(), key=lambda x: (-x[1], x[0]))}),
          sys.stdout, ensure_ascii=False, indent=1)
