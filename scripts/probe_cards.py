#!/usr/bin/env python3
"""Dump the server-rendered game cards from www.nba.com/games?date=... .

The pages probe proved the cards exist for arbitrary dates. This records exactly
which fields they carry, so the pipeline can build official historical scoreboards
without guessing.
"""
from __future__ import annotations
import datetime as dt, gzip, json, os, re, urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
     "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate",
     "Upgrade-Insecure-Requests": "1", "Sec-Fetch-Dest": "document",
     "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin",
     "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="99"',
     "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"'}


def fetch(url):
    req = urllib.request.Request(url, headers=H)
    with urllib.request.urlopen(req, timeout=45) as r:
        b = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            b = gzip.decompress(b)
        return b.decode("utf-8", "replace")


def cards_for(date):
    html = fetch(f"https://www.nba.com/games?date={date}")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None, len(html)
    nd = json.loads(m.group(1))
    props = ((nd.get("props") or {}).get("pageProps") or {})
    feed = props.get("gameCardFeed") or {}
    out = []
    for mod in feed.get("modules") or []:
        for card in mod.get("cards") or []:
            out.append(card)
    return {"props": props, "cards": out}, len(html)


report = {"generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(), "dates": []}
for date in ["2024-11-04", "2019-10-22", "1996-06-16", "2026-10-03"]:
    rec = {"date": date}
    try:
        data, size = cards_for(date)
        rec["htmlBytes"] = size
        if data is None:
            rec["error"] = "no __NEXT_DATA__"
        else:
            cards = data["cards"]
            rec["cardCount"] = len(cards)
            if cards:
                rec["cardKeys"] = list(cards[0].keys())
                rec["firstCard"] = cards[0]
                c0 = cards[0]
                for k in ("cardData", "data", "gameCard", "game"):
                    if isinstance(c0.get(k), dict):
                        rec[f"{k}Keys"] = list(c0[k].keys())
                # flatten search for anything game-id like
                raw = json.dumps(cards)
                rec["gameIdsFound"] = sorted(set(re.findall(r'"gameId"\s*:\s*"(\d{10})"', raw)))[:10]
                rec["gameCodeSample"] = re.findall(r'"gameCode"\s*:\s*"([^"]+)"', raw)[:5]
            else:
                rec["propsKeys"] = list(data["props"].keys())
        print(f"  {date}: bytes={rec.get('htmlBytes')} cards={rec.get('cardCount')} keys={rec.get('cardKeys')} ids={rec.get('gameIdsFound')}", flush=True)
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        print(f"  {date}: ERROR {rec['error']}", flush=True)
    report["dates"].append(rec)

os.makedirs("data/verification", exist_ok=True)
json.dump(report, open("data/verification/cards-probe.json", "w"), indent=1)
print("Wrote data/verification/cards-probe.json", flush=True)
