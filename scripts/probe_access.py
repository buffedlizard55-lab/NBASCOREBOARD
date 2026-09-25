#!/usr/bin/env python3
"""
probe_access.py — Second-stage experiment: WHY is cdn.nba.com returning 403, and
what transport can a browser actually use to read official NBA JSON?

Background (evidence from data/verification/endpoint-probe.json, 2026-09-25):
  * cdn.nba.com JSON paths  -> HTTP 403 "Access Denied" (Akamai) from a GitHub
    Actions runner, while logos/headshots on the same host return 200.
  * stats.nba.com           -> connection dropped / read timeout from the runner.
  * data.nba.net            -> TLS certificate no longer valid for that host.

This script tests, from a real network egress:
  A. Header/TLS variations against cdn.nba.com (is it the client fingerprint?)
  B. www.nba.com HTML pages (is the whole domain blocked or just the JSON API?)
  C. Public CORS relays fetching the OFFICIAL url (does any relay return real
     NBA JSON to a browser?) — data still originates from NBA; the relay is
     transport only. Results decide whether the site can offer a working
     fallback for networks that cannot reach the CDN directly.

Nothing here is assumed: every row of the output is a measured HTTP response.

Usage: python3 scripts/probe_access.py [--out data/verification/access-probe.json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

CDN_URL = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
BOX_URL = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_0022400247.json"
SITE_ORIGIN = "https://buffedlizard55-lab.github.io"

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# A header set copied field-for-field from what a Chrome tab sends when nba.com's
# own React app polls the live feed (see Yutori analysis of the NBA.com SPA).
BROWSER_HEADERS = {
    "User-Agent": CHROME_UA,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Priority": "u=1, i",
}

PROXIES = [
    ("allorigins-raw", "https://api.allorigins.win/raw?url="),
    ("codetabs", "https://api.codetabs.com/v1/proxy?quest="),
    ("corsproxy.io", "https://corsproxy.io/?url="),
    ("cors.lol", "https://api.cors.lol/?url="),
    ("thingproxy", "https://thingproxy.freeboard.io/fetch/"),
    ("jina-reader", "https://r.jina.ai/"),
    ("whateverorigin", "https://www.whateverorigin.org/get?url="),
]


def raw_fetch(url: str, headers: dict, timeout: int = 30) -> dict:
    rec = {"url": url, "ok": False}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                try:
                    body = gzip.decompress(body)
                except Exception:
                    pass
            rec["httpStatus"] = resp.status
            rec["ok"] = 200 <= resp.status < 300
            rec["bytes"] = len(body)
            rec["headers"] = {k.lower(): v for k, v in resp.headers.items()}
            rec["bodyPrefix"] = body[:200].decode("utf-8", "replace")
            rec["looksLikeNbaJson"] = b'"scoreboard"' in body or b'"gameId"' in body
    except urllib.error.HTTPError as exc:
        body = exc.read()
        rec["httpStatus"] = exc.code
        rec["bytes"] = len(body)
        rec["headers"] = {k.lower(): v for k, v in (exc.headers or {}).items()}
        rec["bodyPrefix"] = body[:200].decode("utf-8", "replace")
        rec["error"] = f"HTTPError {exc.code}"
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def curl_fetch(url: str, extra_args: list[str], timeout: int = 30) -> dict:
    cmd = ["curl", "-sS", "-o", "-", "-w", "\n__HTTP__%{http_code}", "--max-time", str(timeout)] + extra_args + [url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        text = out.stdout
        status = None
        if "__HTTP__" in text:
            text, _, status = text.rpartition("__HTTP__")
            status = int(status.strip() or 0)
        return {
            "ok": status is not None and 200 <= status < 300,
            "httpStatus": status,
            "bytes": len(text),
            "bodyPrefix": text[:200],
            "looksLikeNbaJson": '"scoreboard"' in text or '"gameId"' in text,
            "stderr": (out.stderr or "")[:200],
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/verification/access-probe.json")
    args = ap.parse_args()

    report = {
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "question": (
            "Can an automated host (and a browser relay) read official NBA JSON, and "
            "what does the block depend on?"
        ),
        "tests": {},
    }

    print("== A. Header / client variations against cdn.nba.com ==", flush=True)
    variants = []
    variants.append(("python-minimal", raw_fetch(CDN_URL, {"User-Agent": CHROME_UA})))
    variants.append(("python-browser-headers", raw_fetch(CDN_URL, BROWSER_HEADERS)))
    variants.append(
        (
            "python-pages-origin",
            raw_fetch(CDN_URL, {**BROWSER_HEADERS, "Origin": SITE_ORIGIN,
                                "Referer": SITE_ORIGIN + "/"}),
        )
    )
    variants.append(("python-no-referer", raw_fetch(CDN_URL, {"User-Agent": CHROME_UA, "Accept": "*/*"})))
    variants.append(("curl-default", curl_fetch(CDN_URL, [])))
    variants.append(
        (
            "curl-browser-headers-http2",
            curl_fetch(
                CDN_URL,
                [
                    "--http2", "--compressed",
                    "-H", f"User-Agent: {CHROME_UA}",
                    "-H", "Accept: */*",
                    "-H", "Accept-Language: en-US,en;q=0.9",
                    "-H", "Referer: https://www.nba.com/",
                    "-H", "Origin: https://www.nba.com",
                    "-H", "Sec-Fetch-Dest: empty",
                    "-H", "Sec-Fetch-Mode: cors",
                    "-H", "Sec-Fetch-Site: same-site",
                ],
            ),
        )
    )
    variants.append(("python-boxscore-endpoint", raw_fetch(BOX_URL, BROWSER_HEADERS)))
    report["tests"]["cdnVariants"] = [
        {"variant": name, **rec} for name, rec in variants
    ]
    for name, rec in variants:
        print(f"  {name:30} status={rec.get('httpStatus')} bytes={rec.get('bytes')} err={rec.get('error','')}", flush=True)

    print("== B. www.nba.com HTML (is the whole domain blocked?) ==", flush=True)
    html_tests = [
        ("www-nba-com-home", "https://www.nba.com/"),
        ("www-nba-com-games", "https://www.nba.com/games"),
        ("www-nba-com-standings", "https://www.nba.com/standings"),
    ]
    report["tests"]["wwwHtml"] = [
        {"label": label, **raw_fetch(url, BROWSER_HEADERS)} for label, url in html_tests
    ]
    for row in report["tests"]["wwwHtml"]:
        print(f"  {row['label']:24} status={row.get('httpStatus')} bytes={row.get('bytes')} err={row.get('error','')}", flush=True)

    print("== C. CORS relays fetching the official NBA URL ==", flush=True)
    relay_rows = []
    encoded = urllib.parse.quote(CDN_URL, safe="")
    for name, prefix in PROXIES:
        if name == "thingproxy":
            target = prefix + CDN_URL
        elif name == "jina-reader":
            target = prefix + CDN_URL
        else:
            target = prefix + encoded
        rec = raw_fetch(target, {"User-Agent": CHROME_UA, "Accept": "*/*",
                                 "Origin": SITE_ORIGIN})
        relay_rows.append({"relay": name, "target": target, **rec})
        print(
            f"  {name:16} status={rec.get('httpStatus')} bytes={rec.get('bytes')} "
            f"nbaJson={rec.get('looksLikeNbaJson')} acao={(rec.get('headers') or {}).get('access-control-allow-origin','-')}",
            flush=True,
        )
    report["tests"]["relays"] = relay_rows

    # A relay only counts as usable when it returns the real payload AND the browser
    # would be allowed to read it.
    usable = [
        {"relay": r["relay"], "target": r["target"],
         "acao": (r.get("headers") or {}).get("access-control-allow-origin")}
        for r in relay_rows
        if r.get("looksLikeNbaJson") and (r.get("headers") or {}).get("access-control-allow-origin")
    ]
    report["usableRelays"] = usable

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nWrote {args.out}; usable relays: {[u['relay'] for u in usable]}", flush=True)

    summary = [
        "## NBA access probe (why the block happens, what a browser can use)",
        "",
        "### Client variations vs cdn.nba.com",
        "",
        "| variant | status | bytes | note |",
        "|---|---|---|---|",
    ]
    for name, rec in variants:
        summary.append(
            f"| `{name}` | {rec.get('httpStatus', '-')} | {rec.get('bytes', 0)} | "
            f"{rec.get('error', 'ok' if rec.get('ok') else '')} |"
        )
    summary += ["", "### CORS relays reading the official URL", "",
                "| relay | status | bytes | real NBA JSON | ACAO |", "|---|---|---|---|---|"]
    for r in relay_rows:
        summary.append(
            f"| `{r['relay']}` | {r.get('httpStatus', '-')} | {r.get('bytes', 0)} | "
            f"{r.get('looksLikeNbaJson')} | {(r.get('headers') or {}).get('access-control-allow-origin', '-')} |"
        )
    text = "\n".join(summary) + "\n"
    with open(args.out.replace(".json", ".md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
