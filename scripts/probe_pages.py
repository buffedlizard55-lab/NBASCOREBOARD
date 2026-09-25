#!/usr/bin/env python3
"""Inspect what www.nba.com server-renders (official source, runner-reachable).

The coverage probe found `<script id="__NEXT_DATA__">` on /games and /standings.
If those payloads carry the scoreboard for a requested date, the pipeline can build
official historical scoreboards for dates the CDN cannot serve (and standings).

This probe records the real structure only.
"""
from __future__ import annotations
import datetime as dt, gzip, json, os, re, urllib.error, urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
     "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate",
     "Referer": "https://www.nba.com/", "Sec-Fetch-Dest": "document",
     "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin",
     "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="99"',
     "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"', "Upgrade-Insecure-Requests": "1"}


def fetch_html(url, timeout=40):
    req = urllib.request.Request(url, headers=H)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            try: body = gzip.decompress(body)
            except Exception: pass
        return body.decode("utf-8", "replace")


def next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def describe(obj, depth=0, max_depth=3):
    if isinstance(obj, dict):
        if depth >= max_depth:
            return {"__keys": list(obj.keys())[:25]}
        return {k: describe(v, depth + 1, max_depth) for k, v in list(obj.items())[:25]}
    if isinstance(obj, list):
        return {"__len": len(obj), "__item": describe(obj[0], depth + 1, max_depth) if obj else None}
    if isinstance(obj, str):
        return obj[:60]
    return obj


report = {"generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(), "games": [], "standings": {}, "schedule": {}}

print("== /games?date=... __NEXT_DATA__ per date ==", flush=True)
for date in ["2026-09-25", "2024-11-04", "2019-10-22", "1996-06-16"]:
    url = f"https://www.nba.com/games?date={date}"
    rec = {"date": date, "url": url}
    try:
        html = fetch_html(url)
        nd = next_data(html)
        rec["htmlBytes"] = len(html)
        rec["hasNextData"] = bool(nd)
        if nd:
            props = ((nd.get("props") or {}).get("pageProps") or {})
            rec["pagePropsKeys"] = list(props.keys())
            events = props.get("events")
            feed = props.get("gameCardFeed")
            rec["eventsType"] = type(events).__name__
            if isinstance(events, list):
                rec["eventCount"] = len(events)
                rec["eventsShape"] = describe(events[:2])
            if isinstance(feed, dict):
                rec["feedKeys"] = list(feed.keys())[:15]
                rec["feedShape"] = describe(feed, 0, 2)
        print(f"  {date}: bytes={rec.get('htmlBytes')} next={rec.get('hasNextData')} events={rec.get('eventCount')} feedKeys={rec.get('feedKeys')}", flush=True)
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        print(f"  {date}: ERROR {rec['error']}", flush=True)
    report["games"].append(rec)

print("== /standings: where do standings numbers live? ==", flush=True)
try:
    html = fetch_html("https://www.nba.com/standings")
    nd = next_data(html)
    report["standings"]["htmlBytes"] = len(html)
    report["standings"]["hasNextData"] = bool(nd)
    if nd:
        props = ((nd.get("props") or {}).get("pageProps") or {})
        report["standings"]["pagePropsKeys"] = list(props.keys())
        raw = json.dumps(props)
        for needle in ["standings", "Standings", "conference", "playoffRank", "winPercentage", "wins", "losses"]:
            report["standings"][f"contains:{needle}"] = needle in raw
        layout = props.get("layout")
        report["standings"]["layoutShape"] = describe(layout, 0, 4)
        # find any list of team-like dicts
        found = []
        def walk(o, path=""):
            if isinstance(o, dict):
                keys = set(o.keys())
                if {"wins", "losses"} <= keys or {"WINS", "LOSSES"} <= keys:
                    found.append({"path": path, "keys": sorted(keys)[:25]})
                for k, v in o.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(o, list):
                for i, v in enumerate(o[:3]):
                    walk(v, f"{path}[{i}]")
        walk(props)
        report["standings"]["standingsLikeNodes"] = found[:10]
        print(f"  standings nodes: {found[:5]}", flush=True)
except Exception as e:
    report["standings"]["error"] = f"{type(e).__name__}: {e}"
    print("  standings ERROR", e, flush=True)

print("== /schedule?season=... (past-season game ids?) ==", flush=True)
for season in ["2025-26", "2019-20"]:
    url = f"https://www.nba.com/schedule?season={season}"
    rec = {"season": season, "url": url}
    try:
        html = fetch_html(url)
        nd = next_data(html)
        rec["htmlBytes"] = len(html)
        rec["hasNextData"] = bool(nd)
        if nd:
            props = ((nd.get("props") or {}).get("pageProps") or {})
            rec["pagePropsKeys"] = list(props.keys())
            raw = json.dumps(props)
            rec["gameIdMatches"] = len(re.findall(r'"gameId"\s*:\s*"(\d{10})"', raw)) + len(re.findall(r'"gameId"\s*:\s*(\d{10})', raw))
            m = re.search(r'"gameId"\s*:\s*"?(\d{10})', raw)
            rec["firstGameId"] = m.group(1) if m else None
        print(f"  {season}: bytes={rec.get('htmlBytes')} next={rec.get('hasNextData')} gameIds={rec.get('gameIdMatches')} first={rec.get('firstGameId')}", flush=True)
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        print(f"  {season}: ERROR {rec['error']}", flush=True)
    report["schedule"][season] = rec

os.makedirs("data/verification", exist_ok=True)
json.dump(report, open("data/verification/pages-probe.json", "w"), indent=1)
print("Wrote data/verification/pages-probe.json", flush=True)
