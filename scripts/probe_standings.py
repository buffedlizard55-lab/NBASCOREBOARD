#!/usr/bin/env python3
"""Where can an official standings table come from?

The project refuses to show third-party numbers, so standings have been left out
until an official source answers. This probe (run on the CI egress, which is the
only place with NBA access) tries every official candidate it can think of and,
failing JSON, reads the JavaScript that www.nba.com/standings itself loads and
greps it for the endpoint the site calls. Whatever it finds is recorded as
evidence instead of a guess.

Writes data/verification/standings-probe.json.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import re
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HTML_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1", "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none",
    "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
}
JSON_HEADERS = {
    **HTML_HEADERS,
    "Accept": "*/*", "Referer": "https://www.nba.com/", "Origin": "https://www.nba.com",
    "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-site",
}
OUT = os.path.join("data", "verification", "standings-probe.json")

# Every official-looking standings location we know about, plus the Stats API recipe.
CANDIDATES = [
    ("cdn standings/leagueStandings.json",
     "https://cdn.nba.com/static/json/staticData/standings/leagueStandings.json"),
    ("cdn standings/standings.json",
     "https://cdn.nba.com/static/json/staticData/standings/standings.json"),
    ("cdn staticData/leagueStandings.json",
     "https://cdn.nba.com/static/json/staticData/leagueStandings.json"),
    ("cdn liveData/standings/standings_00.json",
     "https://cdn.nba.com/static/json/liveData/standings/standings_00.json"),
    ("cdn staticData/standings_2025_26.json",
     "https://cdn.nba.com/static/json/staticData/standings/standings_2025_26.json"),
    ("stats leaguestandingsv3",
     "https://stats.nba.com/stats/leaguestandingsv3?LeagueID=00&Season=2025-26&SeasonType=Regular%20Season"),
    ("stats leaguestandings",
     "https://stats.nba.com/stats/leaguestandings?LeagueID=00&Season=2025-26&SeasonType=Regular%20Season"),
    ("www standings page", "https://www.nba.com/standings"),
    ("www standings page (season)", "https://www.nba.com/standings?season=2025-26"),
]

ENDPOINT_HINTS = re.compile(
    r"(core-api[^\"'\s]*|/api/v[\d.]+/[^\"'\s]*|leaguestandings[^\"'\s]*|"
    r"standings[^\"'\s]*\.json[^\"'\s]*)", re.I)


def fetch(url: str, headers: dict, timeout: int = 30, limit: int | None = None):
    meta = {"url": url, "httpStatus": None, "bytes": 0, "error": None}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            meta["httpStatus"] = resp.status
            meta["bytes"] = len(raw)
            meta["contentType"] = resp.headers.get("Content-Type")
            meta["cacheControl"] = resp.headers.get("Cache-Control")
            if limit:
                raw = raw[:limit]
            return raw, meta
    except urllib.error.HTTPError as exc:
        meta["httpStatus"] = exc.code
        meta["error"] = f"HTTPError {exc.code}"
        meta["bodyPrefix"] = exc.read()[:160].decode("utf-8", "replace")
    except Exception as exc:
        meta["error"] = f"{type(exc).__name__}: {exc}"
    return None, meta


def shape(payload, depth: int = 0):
    """Describe a JSON payload's shape without dumping it."""
    if depth > 2:
        return "..."
    if isinstance(payload, dict):
        return {k: shape(v, depth + 1) for k, v in list(payload.items())[:12]}
    if isinstance(payload, list):
        return [shape(payload[0], depth + 1)] if payload else []
    return type(payload).__name__


def looks_like_standings(payload) -> bool:
    text = json.dumps(payload)[:20000].lower()
    hits = sum(k in text for k in ("wins", "losses", "winpercentage", "playoffrank", "conference"))
    return hits >= 2


report = {"generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
          "question": "Which official endpoint can supply standings?",
          "candidates": [], "bundles": [], "conclusion": ""}

for label, url in CANDIDATES:
    raw, meta = fetch(url, JSON_HEADERS if url.endswith(".json") or "stats.nba.com" in url else HTML_HEADERS,
                      timeout=25)
    rec = {"label": label, **meta}
    if raw:
        text = raw.decode("utf-8", "replace")
        rec["looksLikeStandings"] = False
        try:
            payload = json.loads(text)
            rec["shape"] = shape(payload)
            rec["looksLikeStandings"] = looks_like_standings(payload)
        except Exception:
            rec["htmlBytes"] = len(text)
            rec["hasTableRows"] = "<tr" in text.lower()
            rec["mentionsStandings"] = "standings" in text.lower()
            rec["scriptSrcs"] = re.findall(r'<script[^>]+src="([^"]+)"', text)[:40]
    report["candidates"].append(rec)
    print(f"[cand] {label}: status={rec.get('httpStatus')} bytes={rec.get('bytes')} "
          f"err={rec.get('error')}", flush=True)

# Mine the page's own __NEXT_DATA__ for standings-shaped payloads.
page_raw, _ = fetch("https://www.nba.com/standings", HTML_HEADERS, timeout=30, limit=4_000_000)
if page_raw:
    text = page_raw.decode("utf-8", "replace")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S)
    if m:
        try:
            nd = json.loads(m.group(1))
        except Exception as exc:
            report["nextDataError"] = str(exc)
        else:
            pp = ((nd.get("props") or {}).get("pageProps") or {})
            report["nextDataPagePropsKeys"] = list(pp.keys())
            found = []
            def walk(node, path=""):
                if isinstance(node, dict):
                    keys = {k.lower() for k in node.keys()}
                    if {"wins", "losses"} <= keys or "winpercentage" in keys:
                        found.append({"path": path, "keys": sorted(node.keys())[:14]})
                    for k, v in list(node.items())[:40]:
                        walk(v, f"{path}.{k}")
                elif isinstance(node, list) and node:
                    walk(node[0], f"{path}[0]")
            walk(pp)
            report["standingsShapedNodes"] = found[:10]

# Read the JS the standings page loads and look for the endpoint it calls.
page = next((c for c in report["candidates"] if c["label"] == "www standings page"), {})
bundle_urls = []
for src in page.get("scriptSrcs", []):
    if src.startswith("/"):
        src = "https://www.nba.com" + src
    if src.startswith("https://www.nba.com/") or src.startswith("https://cdn.nba.com/"):
        bundle_urls.append(src)

scanned = 0
# Next.js serves the page bundle from the *last* chunks in the list, so scan all of
# them (bounded) rather than the first few polyfills.
for url in bundle_urls[:40]:
    if scanned >= 24:  # keep the probe quick and the artifact small
        break
    raw, meta = fetch(url, HTML_HEADERS, timeout=40, limit=8_000_000)
    rec = {"url": url, "httpStatus": meta.get("httpStatus"), "bytes": meta.get("bytes"),
           "error": meta.get("error"), "matches": []}
    if raw:
        text = raw.decode("utf-8", "replace")
        seen = set()
        for m in ENDPOINT_HINTS.finditer(text):
            snippet = m.group(1)
            if snippet in seen:
                continue
            seen.add(snippet)
            rec["matches"].append(snippet[:200])
            if len(rec["matches"]) >= 40:
                break
        rec["scannedBytes"] = len(text)
    report["bundles"].append(rec)
    print(f"[bundle] {url.split('/')[-1][:40]} status={rec['httpStatus']} matches={len(rec['matches'])}",
          flush=True)
    scanned += 1

hits = sorted({m for b in report["bundles"] for m in b["matches"]})
report["endpointHints"] = hits[:60]
json_candidates = [c["label"] for c in report["candidates"] if c.get("looksLikeStandings")]
report["conclusion"] = (
    f"Official JSON candidates that look like standings: {json_candidates or 'none'}. "
    f"Endpoint strings found inside nba.com's own JS: {len(hits)}."
)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(report, fh, indent=1, ensure_ascii=False)
    fh.write("\n")
print(f"[done] wrote {OUT}: {report['conclusion']}")

# Candidates are official hosts only; anything non-NBA is never queried.
