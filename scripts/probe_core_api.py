#!/usr/bin/env python3
"""Reverse-engineer the API nba.com's own front end calls (core-api.nba.com).

The standings probe found `core-api.nba.com`, `Core-Api-Key` and `Core-Api-Version`
inside www.nba.com's own `_app` bundle, plus a reference to the Stats endpoint
`leaguestandingsv3`. That is the official app backend the NBA's own site uses.

This probe:
  1. downloads the live `_app` bundle and extracts the host/path templates and the
     header names (and any literal key value) it uses,
  2. greps the bundle for standings / scoreboard / play-by-play route strings,
  3. calls the discovered routes with a normal browser shape and records HTTP status,
     `Access-Control-Allow-Origin` (the browser-readability question) and payload shape,
  4. sends an OPTIONS preflight with this site's origin to see what CORS the host
     actually grants.

Nothing outside nba.com/*.nba.com is ever queried. Writes
data/verification/core-api-probe.json.
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
BROWSER = {
    "User-Agent": UA,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-site",
    "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
}
SITE_ORIGIN = "https://buffedlizard55-lab.github.io"
OUT = os.path.join("data", "verification", "core-api-probe.json")


def fetch(url: str, headers: dict, timeout: int = 30, limit: int | None = None, method: str = "GET"):
    meta = {"url": url, "method": method, "httpStatus": None, "bytes": 0, "error": None}
    try:
        req = urllib.request.Request(url, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            meta["httpStatus"] = resp.status
            meta["bytes"] = len(raw)
            for h in ("Access-Control-Allow-Origin", "Access-Control-Allow-Headers",
                      "Access-Control-Allow-Methods", "Content-Type", "Cache-Control",
                      "Vary"):
                value = resp.headers.get(h)
                if value:
                    meta[h.lower().replace("-", "_")] = value
            if limit:
                raw = raw[:limit]
            return raw, meta
    except urllib.error.HTTPError as exc:
        meta["httpStatus"] = exc.code
        meta["error"] = f"HTTPError {exc.code}"
        for h in ("Access-Control-Allow-Origin", "Access-Control-Allow-Headers", "Content-Type"):
            value = exc.headers.get(h) if exc.headers else None
            if value:
                meta[h.lower().replace("-", "_")] = value
        try:
            meta["bodyPrefix"] = exc.read()[:200].decode("utf-8", "replace")
        except Exception:
            pass
    except Exception as exc:
        meta["error"] = f"{type(exc).__name__}: {exc}"
    return None, meta


report = {"generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
          "question": "What does nba.com's own front end call, and can a browser read it?",
          "extracted": {}, "hostTests": [], "preflight": [], "routeTests": []}

# 1. find the current app bundle
html, hmeta = fetch("https://www.nba.com/standings", BROWSER, timeout=30, limit=2_000_000)
bundle_urls = []
if html:
    text = html.decode("utf-8", "replace")
    for src in re.findall(r'<script[^>]+src="([^"]+)"', text):
        if "_app-" in src or "framework-" in src or "main-" in src:
            bundle_urls.append(src if src.startswith("http") else "https://www.nba.com" + src)
report["extracted"]["appBundles"] = bundle_urls

bundle_text = ""
for url in bundle_urls[:2]:
    raw, meta = fetch(url, BROWSER, timeout=40, limit=8_000_000)
    report["extracted"].setdefault("bundleFetches", []).append({**meta})
    if raw:
        bundle_text += raw.decode("utf-8", "replace")

# 2. extract hosts, header names and route strings
if bundle_text:
    hosts = sorted(set(re.findall(r"https?://[a-z0-9.\-]*nba\.com", bundle_text)))
    report["extracted"]["hosts"] = hosts[:40]
    report["extracted"]["apiRoutes"] = sorted(set(re.findall(r"/api/v[\d.]*[A-Za-z0-9/_\-.{}$]*", bundle_text)))[:60]
    report["extracted"]["headerNames"] = sorted(set(re.findall(r"[\"']?(Core-Api-[A-Za-z\-]+|X-Api-Key|Api-Key)[\"']?", bundle_text)))
    interesting = {}
    for needle in ("Core-Api-Key", "Core-Api-Version", "core-api.nba.com", "leaguestandings",
                   "standings", "boxscore", "playbyplay", "todaysScoreboard", "capi-preview"):
        hits = []
        for m in re.finditer(re.escape(needle), bundle_text, re.I):
            snippet = bundle_text[max(0, m.start() - 160): m.end() + 200]
            hits.append(snippet.replace("\n", " "))
            if len(hits) >= 4:
                break
        interesting[needle] = hits
    report["extracted"]["contexts"] = interesting

# candidate absolute URLs straight out of the bundle, plus the auth/health routes
candidates = set()
for url in re.findall(r"https?://core-api\.nba\.com[A-Za-z0-9/_\-.]*", bundle_text):
    candidates.add(url.rstrip(".,;)`'\""))
for route in ("/api/v1/authenticate?requestor_id=nba", "/api/v1/checkauthn/", "/api/v1/capi-preview/"):
    candidates.add("https://core-api.nba.com" + route)
candidates.add("https://core-api.nba.com/")

# 3. call them and record what the official host answers (including CORS headers)
for url in sorted(candidates)[:14]:
    raw, meta = fetch(url, BROWSER, timeout=25, limit=200_000)
    rec = {**meta}
    if raw:
        text = raw.decode("utf-8", "replace")
        try:
            payload = json.loads(text)
            rec["jsonTopLevelKeys"] = list(payload)[:15] if isinstance(payload, dict) else f"list[{len(payload)}]"
        except Exception:
            rec["bodyPrefix"] = text[:200]
    report["routeTests"].append(rec)
    print(f"[route] {url} -> {rec.get('httpStatus')} acao={rec.get('access_control_allow_origin')} "
          f"bytes={rec.get('bytes')} err={rec.get('error')}", flush=True)

# 4. preflight as this site's origin: does the host allow a foreign origin?
for url in sorted(candidates)[:6]:
    headers = {**BROWSER, "Origin": SITE_ORIGIN, "Access-Control-Request-Method": "GET",
               "Access-Control-Request-Headers": "core-api-key,core-api-version"}
    _, meta = fetch(url, headers, timeout=25, method="OPTIONS")
    report["preflight"].append(meta)
    print(f"[options] {url} -> {meta.get('httpStatus')} acao={meta.get('access_control_allow_origin')}", flush=True)

# 5. host-level reachability (does the host exist at all for a cloud runner?)
for host in ("https://core-api.nba.com/", "https://cdn.nba.com/",
             "https://www.nba.com/", "https://stats.nba.com/"):
    _, meta = fetch(host, BROWSER, timeout=20)
    rec = {"host": host, "httpStatus": meta.get("httpStatus"), "error": meta.get("error"),
           "acaO": meta.get("access_control_allow_origin")}
    report["hostTests"].append(rec)

report["conclusion"] = (
    "core-api.nba.com is the backend nba.com's own front end uses; the routes and header "
    "names it needs are recorded above together with the exact HTTP/CORS answers a "
    "non-nba.com origin gets."
)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(report, fh, indent=1, ensure_ascii=False)
    fh.write("\n")
print(f"[done] wrote {OUT}")
