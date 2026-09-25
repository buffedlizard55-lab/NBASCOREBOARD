#!/usr/bin/env python3
"""
probe_endpoints.py — Empirical verification of OFFICIAL NBA endpoints.

WHY THIS EXISTS
---------------
This project may only use official NBA data. Before any endpoint is used by the
scoreboard, it is probed from a real network egress (GitHub Actions runner) and the
RAW response evidence (HTTP status, headers, CORS headers, JSON shape) is recorded
into data/verification/endpoint-probe.json.

Nothing in this file asserts what an endpoint returns. It only records what the
official server actually answered at the time of the run. If a URL 404s, the report
says 404. No guesswork, no hallucinated schema.

Usage:
    python3 scripts/probe_endpoints.py [--out data/verification/endpoint-probe.json]

Stdlib only (no pip install required).
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# Real deployment origin of this project — CORS is checked against it, because the
# browser-side scoreboard must be able to read the feed from this origin.
PAGES_ORIGIN = "https://buffedlizard55-lab.github.io"
TIMEOUT = 45


def cdn_headers() -> dict:
    return {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip",
        "Referer": "https://www.nba.com/",
        "Origin": PAGES_ORIGIN,
    }


def stats_headers() -> dict:
    return {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip",
        "Origin": "https://www.nba.com",
        "Referer": "https://www.nba.com/",
        # Documented as required by the community for stats.nba.com; probe records
        # whether they actually matter by testing with and without them.
        "x-nba-stats-origin": "stats",
        "x-nba-stats-token": "true",
    }


def fetch(url: str, headers: dict, method: str = "GET") -> dict:
    """Fetch a URL and return a fully self-describing evidence record."""
    started = time.time()
    rec = {
        "url": url,
        "method": method,
        "requestHeaders": sorted(headers.keys()),
        "ok": False,
    }
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            rec["httpStatus"] = resp.status
            rec["contentType"] = resp.headers.get("Content-Type")
            rec["contentLength"] = len(raw)
            rec["ok"] = 200 <= resp.status < 300
            rec["headers"] = {
                k.lower(): v
                for k, v in resp.headers.items()
                if k.lower()
                in {
                    "access-control-allow-origin",
                    "access-control-allow-headers",
                    "access-control-allow-methods",
                    "cache-control",
                    "age",
                    "date",
                    "last-modified",
                    "etag",
                    "server",
                    "content-type",
                    "vary",
                    "x-cache",
                    "x-served-by",
                }
            }
            rec["bodyPrefix"] = raw[:220].decode("utf-8", "replace")
            ct = (resp.headers.get("Content-Type") or "").lower()
            if "json" in ct or raw[:1] in (b"{", b"["):
                try:
                    rec["json"] = json.loads(raw.decode("utf-8"))
                except Exception as exc:  # pragma: no cover - evidence only
                    rec["jsonParseError"] = str(exc)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        rec["httpStatus"] = exc.code
        rec["contentType"] = exc.headers.get("Content-Type") if exc.headers else None
        rec["contentLength"] = len(body)
        rec["bodyPrefix"] = body[:220].decode("utf-8", "replace")
        rec["headers"] = {k.lower(): v for k, v in (exc.headers or {}).items()}
        rec["error"] = f"HTTPError {exc.code}"
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    rec["elapsedMs"] = round((time.time() - started) * 1000)
    return rec


def shape(obj, depth: int = 0, max_depth: int = 3, max_keys: int = 200) -> object:
    """Structural fingerprint of a JSON payload (types + keys), for the report."""
    if isinstance(obj, dict):
        keys = list(obj.keys())
        out = {"__type": "object", "__keyCount": len(keys)}
        if depth >= max_depth:
            out["__keys"] = keys[:max_keys]
            return out
        for k in keys[:max_keys]:
            out[k] = shape(obj[k], depth + 1, max_depth, max_keys)
        return out
    if isinstance(obj, list):
        out = {"__type": "array", "__len": len(obj)}
        if obj:
            out["__item"] = shape(obj[0], depth + 1, max_depth, max_keys)
        return out
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "bool"
    if isinstance(obj, int):
        return "int"
    if isinstance(obj, float):
        return "float"
    return "str"


def json_notes(rec: dict) -> dict:
    """Extra notes about JSON payloads we care about (counts, first ids, dates)."""
    notes = {}
    data = rec.get("json")
    if not isinstance(data, dict):
        return notes
    notes["topLevelKeys"] = list(data.keys())
    sb = data.get("scoreboard")
    if isinstance(sb, dict):
        notes["scoreboardKeys"] = list(sb.keys())
        notes["gameDate"] = sb.get("gameDate")
        games = sb.get("games")
        if isinstance(games, list):
            notes["gameCount"] = len(games)
            if games:
                g = games[0]
                notes["firstGameKeys"] = list(g.keys()) if isinstance(g, dict) else None
                if isinstance(g, dict):
                    notes["firstGameId"] = g.get("gameId")
                    notes["firstGameStatus"] = g.get("gameStatus")
                    for side in ("homeTeam", "awayTeam"):
                        t = g.get(side)
                        if isinstance(t, dict):
                            notes[f"{side}Keys"] = list(t.keys())
    # stats.nba.com resultSets style
    rs = data.get("resultSets")
    if isinstance(rs, list):
        notes["resultSetNames"] = [r.get("name") for r in rs if isinstance(r, dict)]
        for r in rs:
            if isinstance(r, dict) and r.get("headers"):
                notes[f"headers:{r.get('name')}"] = r.get("headers")
                notes[f"rowCount:{r.get('name')}"] = len(r.get("rowSet") or [])
    # stats.nba.com v3 style (camelCase payload key)
    for k, v in data.items():
        if isinstance(v, dict) and "games" in v and k != "scoreboard":
            notes[f"container:{k}"] = list(v.keys())
            games = v.get("games")
            if isinstance(games, list):
                notes[f"gameCount:{k}"] = len(games)
    return notes


def probe(url: str, headers: dict, label: str, notes: bool = True) -> dict:
    print(f"  -> {label}: {url}", flush=True)
    rec = fetch(url, headers)
    rec["label"] = label
    status = rec.get("httpStatus", "-")
    size = rec.get("contentLength", 0)
    acao = (rec.get("headers") or {}).get("access-control-allow-origin", "(none)")
    print(
        f"     status={status} bytes={size} elapsedMs={rec['elapsedMs']} "
        f"acao={acao} err={rec.get('error', '')}",
        flush=True,
    )
    if notes and rec.get("json") is not None:
        rec["notes"] = json_notes(rec)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/verification/endpoint-probe.json")
    args = ap.parse_args()

    report = {
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "runner": {
            "corsCheckOrigin": PAGES_ORIGIN,
            "python": sys.version.split()[0],
            "platform": sys.platform,
        },
        "purpose": (
            "Empirical, timestamped evidence of what official NBA servers return "
            "to a real network egress. No values in this file are hand-written."
        ),
        "probes": [],
    }

    print("== A. CDN live & static endpoints (cdn.nba.com) ==", flush=True)
    cdn_targets = [
        ("live-scoreboard-today", "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"),
        ("live-playbyplay-2024", "https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_0022400247.json"),
        ("live-boxscore-2024", "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_0022400247.json"),
        ("live-odds", "https://cdn.nba.com/static/json/liveData/odds/odds_todaysGames.json"),
        ("live-channels", "https://cdn.nba.com/static/json/liveData/channels/v2/channels_00.json"),
        ("static-schedule-v2-1", "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"),
        ("static-schedule-v2", "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"),
        ("static-schedule-2025-26", "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_2025_26.json"),
        ("logo-lakers", "https://cdn.nba.com/logos/nba/1610612747/primary/L/logo.svg"),
        ("headshot-lebron", "https://cdn.nba.com/headshots/nba/latest/260x190/2544.png"),
        ("experimental-date-scoreboard", "https://cdn.nba.com/static/json/liveData/scoreboard/scoreboard_20241104.json"),
        ("experimental-scoreboard-00", "https://cdn.nba.com/static/json/liveData/scoreboard/scoreboard_00.json"),
    ]
    for label, url in cdn_targets:
        report["probes"].append(probe(url, cdn_headers(), label))

    print("== B. Stats API (stats.nba.com) — server-side only ==", flush=True)
    stats_targets = [
        ("stats-scoreboardv3-2025-11-04", "https://stats.nba.com/stats/scoreboardv3?GameDate=2025-11-04&LeagueID=00"),
        ("stats-scoreboardv3-2016-06-19", "https://stats.nba.com/stats/scoreboardv3?GameDate=2016-06-19&LeagueID=00"),
        ("stats-scoreboardv3-1996-06-16", "https://stats.nba.com/stats/scoreboardv3?GameDate=1996-06-16&LeagueID=00"),
        ("stats-leaguestandingsv3-2025-26", "https://stats.nba.com/stats/leaguestandingsv3?LeagueID=00&Season=2025-26&SeasonType=Regular+Season"),
        ("stats-boxscoretraditionalv3", "https://stats.nba.com/stats/boxscoretraditionalv3?GameID=0022400247&StartPeriod=0&EndPeriod=14&StartRange=0&EndRange=2147483647&RangeType=0"),
        ("stats-playbyplayv3", "https://stats.nba.com/stats/playbyplayv3?GameID=0022400247&StartPeriod=0&EndPeriod=14"),
        ("stats-scheduleleaguev2", "https://stats.nba.com/stats/scheduleleaguev2?LeagueID=00&Season=2025-26"),
        ("stats-commonplayerinfo", "https://stats.nba.com/stats/commonplayerinfo?PlayerID=2544"),
    ]
    for label, url in stats_targets:
        report["probes"].append(probe(url, stats_headers(), label, notes=True))

    print("== C. Stats API without the x-nba-stats headers (control test) ==", flush=True)
    control_headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip",
    }
    report["probes"].append(
        probe(
            "https://stats.nba.com/stats/scoreboardv3?GameDate=2025-11-04&LeagueID=00",
            control_headers,
            "control-no-stats-headers",
            notes=False,
        )
    )

    print("== D. Legacy / deprecated hosts ==", flush=True)
    for label, url in [
        ("legacy-data-nba-net-2020", "https://data.nba.net/data/10s/prod/v1/20200102/scoreboard.json"),
        ("legacy-data-nba-net-old-path", "https://data.nba.net/prod/v1/20200102/scoreboard.json"),
        ("legacy-data-nba-net-2025", "https://data.nba.net/data/10s/prod/v1/20251104/scoreboard.json"),
    ]:
        report["probes"].append(probe(url, cdn_headers(), label, notes=False))

    print("== E. Coverage matrix: historical game IDs -> CDN boxscore / pbp ==", flush=True)
    # Harvest REAL game ids from the official stats scoreboard for a spread of dates.
    # Nothing is hard-coded: if a date returns no games, that is what we record.
    historical_dates = [
        "2025-11-04",
        "2025-06-22",
        "2024-11-04",
        "2022-01-15",
        "2019-10-22",
        "2016-06-19",
        "2010-06-17",
        "2005-06-23",
        "1996-06-16",
    ]
    harvested = []
    for date in historical_dates:
        rec = probe(
            f"https://stats.nba.com/stats/scoreboardv3?GameDate={date}&LeagueID=00",
            stats_headers(),
            f"harvest-scoreboard-{date}",
        )
        report["probes"].append(rec)
        games = (((rec.get("json") or {}).get("scoreboard") or {}) if rec.get("json") else {}).get("games") or []
        if not games and rec.get("json"):
            # v3 resultSets shape fallback
            for rs in (rec["json"].get("resultSets") or []):
                if rs.get("name") == "GameHeader" and rs.get("rowSet") and rs.get("headers"):
                    idx = rs["headers"].index("GAME_ID")
                    games = [{"gameId": row[idx]} for row in rs["rowSet"]]
        for g in games[:1]:
            gid = g.get("gameId") or g.get("GAME_ID")
            if gid:
                harvested.append({"date": date, "gameId": gid, "gameCount": len(games)})

    coverage = []
    for row in harvested:
        gid = row["gameId"]
        for kind, url in [
            ("boxscore", f"https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{gid}.json"),
            ("playbyplay", f"https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{gid}.json"),
        ]:
            rec = probe(url, cdn_headers(), f"coverage-{kind}-{gid}", notes=False)
            rec["coverage"] = {"date": row["date"], "gameId": gid, "kind": kind, "gamesThatDate": row["gameCount"]}
            coverage.append(rec)
            report["probes"].append(rec)

    report["coverage"] = [
        {
            "date": c["coverage"]["date"],
            "gameId": c["coverage"]["gameId"],
            "kind": c["coverage"]["kind"],
            "httpStatus": c.get("httpStatus"),
            "bytes": c.get("contentLength"),
        }
        for c in coverage
    ]

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=False)
    print(f"\nWrote {args.out} ({len(report['probes'])} probes)", flush=True)

    # Markdown summary for the GitHub Actions run page
    ok = sum(1 for p in report["probes"] if p.get("ok"))
    lines = [
        "## NBA endpoint probe (official endpoints only)",
        "",
        f"- generated: `{report['generatedAtUtc']}`",
        f"- probes: **{len(report['probes'])}**, HTTP 2xx: **{ok}**",
        f"- CORS checked with `Origin: {PAGES_ORIGIN}`",
        "",
        "| label | status | bytes | ms | ACAO |",
        "|---|---|---|---|---|",
    ]
    for p in report["probes"]:
        acao = (p.get("headers") or {}).get("access-control-allow-origin", "-")
        lines.append(
            f"| `{p['label']}` | {p.get('httpStatus', p.get('error', '?'))} | "
            f"{p.get('contentLength', 0)} | {p.get('elapsedMs', '-')} | {acao} |"
        )
    lines.append("")
    summary = "\n".join(lines)
    with open(args.out.replace(".json", ".md"), "w", encoding="utf-8") as fh:
        fh.write(summary + "\n")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(summary + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
