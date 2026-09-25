#!/usr/bin/env python3
"""
probe_deep.py — Stage-3 experiment. Pins down exactly what the pipeline may rely on.

Questions this answers, with measured HTTP responses only:

  A. Which request headers does cdn.nba.com actually require? (drop-one-at-a-time)
  B. Does stats.nba.com answer a datacenter client under any documented header
     recipe? (standings + historical need it)
  C. Is the official season schedule available on the CDN, and what season does it
     cover? (drives historical navigation)
  D. How far back does cdn.nba.com game data go? (boxscore/play-by-play per season,
     using game IDs harvested from the official schedule file — never invented)
  E. Are there official CDN standings/static paths? (404 => we do not use them)

Usage: python3 scripts/probe_deep.py [--out data/verification/deep-probe.json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import urllib.error
import urllib.request

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Full browser fingerprint that produced HTTP 200 against cdn.nba.com in
# data/verification/access-probe.json (variant "python-browser-headers").
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

CDN_SCOREBOARD = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"


def get(url: str, headers: dict, timeout: int = 25) -> dict:
    rec = {"url": url, "requestHeaderNames": sorted(headers.keys()), "ok": False}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                try:
                    body = gzip.decompress(body)
                except Exception:
                    pass
            rec.update(
                httpStatus=resp.status,
                bytes=len(body),
                ok=200 <= resp.status < 300,
                acao=resp.headers.get("Access-Control-Allow-Origin"),
                cacheControl=resp.headers.get("Cache-Control"),
                contentType=resp.headers.get("Content-Type"),
                bodyPrefix=body[:160].decode("utf-8", "replace"),
            )
            try:
                rec["json"] = json.loads(body.decode("utf-8"))
            except Exception:
                pass
    except urllib.error.HTTPError as exc:
        body = exc.read()
        rec.update(httpStatus=exc.code, bytes=len(body), error=f"HTTPError {exc.code}",
                   bodyPrefix=body[:160].decode("utf-8", "replace"))
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def summary(rec: dict) -> str:
    return (
        f"status={rec.get('httpStatus')} bytes={rec.get('bytes')} "
        f"err={rec.get('error', '')} acao={rec.get('acao')}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/verification/deep-probe.json")
    args = ap.parse_args()

    report = {
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "note": "Every value below is a measured response, not an assumption.",
    }

    print("== A. Header sensitivity at cdn.nba.com ==", flush=True)
    variants = [("full-browser-set", BROWSER_HEADERS)]
    for drop in ("Sec-Fetch-Dest", "Sec-Fetch-Mode", "Sec-Fetch-Site", "sec-ch-ua",
                 "Priority", "Accept-Language", "Accept-Encoding"):
        variants.append((f"drop-{drop}", {k: v for k, v in BROWSER_HEADERS.items() if k != drop}))
    variants += [
        ("ua-referer-origin-only", {
            "User-Agent": CHROME_UA, "Accept": "*/*",
            "Referer": "https://www.nba.com/", "Origin": "https://www.nba.com",
        }),
        ("ua-referer-only", {"User-Agent": CHROME_UA, "Accept": "*/*",
                             "Referer": "https://www.nba.com/"}),
        ("accept-json-content-type", {**BROWSER_HEADERS, "Accept": "application/json"}),
    ]
    rows = []
    for name, hdrs in variants:
        rec = get(CDN_SCOREBOARD, hdrs)
        rows.append({"variant": name, **rec})
        print(f"  {name:28} {summary(rec)}", flush=True)
    report["cdnHeaderSensitivity"] = rows

    print("== B. stats.nba.com header recipes (20s timeout) ==", flush=True)
    stats_url = "https://stats.nba.com/stats/scoreboardv3?GameDate=2025-11-04&LeagueID=00"
    recipes = {
        "community-recipe": {
            "Host": "stats.nba.com",
            "User-Agent": CHROME_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://stats.nba.com/",
            "Origin": "https://stats.nba.com",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "x-nba-stats-origin": "stats",
            "x-nba-stats-token": "true",
        },
        "www-referer": {
            "User-Agent": CHROME_UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.nba.com/",
            "x-nba-stats-origin": "stats",
            "x-nba-stats-token": "true",
        },
        "browser-full-on-stats": {**BROWSER_HEADERS},
        "ubuntu-curl-minimal": {"User-Agent": CHROME_UA},
    }
    stats_rows = []
    for name, hdrs in recipes.items():
        rec = get(stats_url, hdrs, timeout=20)
        stats_rows.append({"recipe": name, **rec})
        print(f"  {name:26} {summary(rec)}", flush=True)
    report["statsRecipes"] = stats_rows

    print("== C. Official CDN schedule files ==", flush=True)
    schedule_urls = [
        "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json",
        "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json",
    ]
    sched_rows = []
    schedule_payload = None
    for url in schedule_urls:
        rec = get(url, BROWSER_HEADERS, timeout=40)
        info = {"url": url, **{k: rec.get(k) for k in
                               ("httpStatus", "bytes", "ok", "error", "acao", "cacheControl")}}
        payload = rec.get("json")
        if isinstance(payload, dict):
            league = payload.get("leagueSchedule") or {}
            dates = league.get("gameDates") or []
            info["topLevelKeys"] = list(payload.keys())
            info["leagueKeys"] = list(league.keys())[:20] if isinstance(league, dict) else None
            info["seasonYear"] = league.get("seasonYear") if isinstance(league, dict) else None
            info["gameDateCount"] = len(dates)
            info["firstGameDate"] = (dates[0].get("gameDate") if dates and isinstance(dates[0], dict) else None)
            info["lastGameDate"] = (dates[-1].get("gameDate") if dates and isinstance(dates[-1], dict) else None)
            info["gameCount"] = sum(len(d.get("games") or []) for d in dates if isinstance(d, dict))
            if dates and isinstance(dates[0], dict) and (dates[0].get("games") or []):
                g = dates[0]["games"][0]
                info["firstGameKeys"] = list(g.keys())
                info["firstGameId"] = g.get("gameId")
                info["firstGameDateEst"] = g.get("gameDateEst")
                info["homeTeamKeys"] = list((g.get("homeTeam") or {}).keys())
            if schedule_payload is None and info.get("httpStatus") == 200:
                schedule_payload = payload
        sched_rows.append(info)
        print(f"  {url.split('/')[-1]:26} status={info['httpStatus']} bytes={info['bytes']} games={info.get('gameCount')} season={info.get('seasonYear')}", flush=True)
    report["scheduleFiles"] = sched_rows

    print("== D. CDN historical coverage (ids harvested from the official schedule) ==", flush=True)
    coverage = []
    if schedule_payload:
        # Collect one game id per distinct game-type/season prefix so we sample eras.
        samples = {}
        for gd in schedule_payload.get("leagueSchedule", {}).get("gameDates", []):
            for g in gd.get("games") or []:
                gid = g.get("gameId")
                if not isinstance(gid, str) or len(gid) < 5:
                    continue
                key = gid[:5]  # e.g. 00224, 00492, 00125 ...
                samples.setdefault(key, gid)
        print(f"  distinct game-id prefixes in schedule: {sorted(samples)[:12]}", flush=True)
        for key in sorted(samples):
            gid = samples[key]
            for kind, url in (
                ("boxscore", f"https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{gid}.json"),
                ("playbyplay", f"https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{gid}.json"),
            ):
                rec = get(url, BROWSER_HEADERS)
                row = {"gameId": gid, "kind": kind, "httpStatus": rec.get("httpStatus"),
                       "bytes": rec.get("bytes"), "error": rec.get("error")}
                if kind == "boxscore" and isinstance(rec.get("json"), dict):
                    game = rec["json"].get("game") or {}
                    row["hasPeriods"] = bool((game.get("homeTeam") or {}).get("periods"))
                    row["gameStatus"] = game.get("gameStatus")
                    row["gameEt"] = game.get("gameEt") or game.get("gameTimeLocal")
                    if isinstance(game.get("homeTeam"), dict):
                        row["homeTeamKeys"] = list(game["homeTeam"].keys())
                    row["topLevelKeys"] = list(rec["json"].keys())
                coverage.append(row)
                print(f"  {gid} {kind:10} status={row['httpStatus']} bytes={row['bytes']} periods={row.get('hasPeriods')}", flush=True)
    else:
        print("  schedule unavailable -> coverage not measurable in this run", flush=True)
    report["cdnCoverage"] = coverage

    print("== E. Candidate official CDN static paths (unverified candidates) ==", flush=True)
    candidates = [
        "https://cdn.nba.com/static/json/staticData/standings/standings.json",
        "https://cdn.nba.com/static/json/liveData/standings/standings.json",
        "https://cdn.nba.com/static/json/staticData/leagueStandings.json",
        "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_2024_25.json",
    ]
    cand_rows = []
    for url in candidates:
        rec = get(url, BROWSER_HEADERS)
        cand_rows.append({"url": url, "httpStatus": rec.get("httpStatus"), "bytes": rec.get("bytes"),
                          "error": rec.get("error")})
        print(f"  {url.split('/static/json/')[-1]:48} status={rec.get('httpStatus')} bytes={rec.get('bytes')}", flush=True)
    report["staticCandidates"] = cand_rows

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    print(f"\nWrote {args.out}", flush=True)

    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as fh:
            fh.write("## Deep probe: header requirements, stats API, schedule, coverage\n\n")
            fh.write("| test | result |\n|---|---|\n")
            for r in report["cdnHeaderSensitivity"]:
                fh.write(f"| cdn `{r['variant']}` | {r.get('httpStatus')} / {r.get('bytes')}B |\n")
            for r in report["statsRecipes"]:
                fh.write(f"| stats `{r['recipe']}` | {r.get('httpStatus')} / {r.get('error','')} |\n")
            for r in report["scheduleFiles"]:
                fh.write(f"| schedule `{r['url'].split('/')[-1]}` | {r.get('httpStatus')} games={r.get('gameCount')} season={r.get('seasonYear')} |\n")
            for r in report["cdnCoverage"]:
                fh.write(f"| coverage `{r['gameId']}` {r['kind']} | {r.get('httpStatus')} {r.get('bytes')}B |\n")
            for r in report["staticCandidates"]:
                fh.write(f"| static `{r['url'].split('/static/json/')[-1]}` | {r.get('httpStatus')} |\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
