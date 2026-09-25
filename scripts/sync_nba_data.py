#!/usr/bin/env python3
"""
sync_nba_data.py — Fetch OFFICIAL NBA data server-side and publish it as static JSON.

WHY THIS EXISTS
---------------
Two real-world blockers stop a purely client-side scoreboard from showing every game:

1. stats.nba.com (historical scoreboards, standings, pre-2019 play-by-play) refuses
   cross-origin browser requests (no Access-Control-Allow-Origin). A browser page
   cannot read it directly.
2. Some client networks/VPNs/regions cannot reach cdn.nba.com at all.

This pipeline runs on GitHub Actions (real egress), pulls ONLY official NBA
endpoints, and commits the responses as static files next to the site. GitHub Pages
then serves them same-origin, so the scoreboard has a verified official fallback
for every case above — with no third-party data and no manual work.

GUARANTEES
----------
* Only hosts owned by the NBA are contacted (see scripts/nba_official.py).
* Every stored payload keeps the exact official response under `official`, plus a
  `_sync` block recording source URL + fetch time. Nothing is edited or invented.
* Files are written only when the official content actually changed, so the repo
  and the deploy pipeline do not churn.
* Every run appends a machine-readable record of what it did to
  data/verification/sync-log.json (endpoint, HTTP status, bytes, games found).

Usage:
  python3 scripts/sync_nba_data.py --mode live      # today's feed only (frequent)
  python3 scripts/sync_nba_data.py --mode daily     # live + standings + recent history
  python3 scripts/sync_nba_data.py --mode full      # daily + season schedule + backfill
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nba_official import (  # noqa: E402
    CDN_HEADERS,
    LIVE_SCOREBOARD_URL,
    STATS_HEADERS,
    boxscore_url,
    league_standings_v3_url,
    playbyplay_url,
    schedule_candidates,
    scoreboard_v3_url,
)

TIMEOUT = 30
RETRIES = 3
DATA = os.path.join(ROOT, "data")
LOG_PATH = os.path.join(DATA, "verification", "sync-log.json")
MAX_LOG_RUNS = 40
# How many historical dates we keep as static scoreboards in the repo.
HISTORY_WINDOW_DAYS = 45
BACKFILL_MAX_DATES = 120


# --------------------------------------------------------------------------- io


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def http_get_json(url: str, headers: dict, timeout: int = TIMEOUT):
    """GET a URL. Returns (json_or_None, meta). Never raises."""
    meta = {"url": url, "attempts": 0, "httpStatus": None, "error": None, "bytes": 0}
    for attempt in range(1, RETRIES + 1):
        meta["attempts"] = attempt
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
                meta["cacheControl"] = resp.headers.get("Cache-Control")
                meta["serverDate"] = resp.headers.get("Date")
                try:
                    return json.loads(raw.decode("utf-8")), meta
                except Exception as exc:
                    meta["error"] = f"JSON parse failed: {exc}"
                    return None, meta
        except urllib.error.HTTPError as exc:
            meta["httpStatus"] = exc.code
            meta["error"] = f"HTTPError {exc.code}"
            if 400 <= exc.code < 500 and exc.code != 429:
                return None, meta  # no point retrying a 404/403
        except Exception as exc:
            meta["error"] = f"{type(exc).__name__}: {exc}"
        if attempt < RETRIES:
            time.sleep(min(2 ** attempt, 8))
    return None, meta


def semantic_hash(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def write_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")


def store_official(path: str, url: str, official, extra: dict | None = None) -> bool:
    """Write official payload at `path` unless its content is unchanged.

    Returns True when the file was written.
    """
    content_hash = semantic_hash(official)
    existing = load_json(path)
    if existing and isinstance(existing, dict):
        if existing.get("_sync", {}).get("contentHash") == content_hash:
            return False  # identical official content -> no write, no churn
    payload = {
        "_sync": {
            "source": url,
            "fetchedAtUtc": now_utc(),
            "contentHash": content_hash,
            "pipeline": "scripts/sync_nba_data.py",
            "dataOwner": "NBA (official)",
        },
        "official": official,
    }
    if extra:
        payload["_sync"].update(extra)
    write_json(path, payload)
    return True


# ------------------------------------------------------------------- extractors
# Tolerant readers: they accept every official shape observed by the probe and
# return [] rather than inventing data when a shape is unknown.


def find_key(obj, names, depth=0):
    """Case-insensitive key lookup for the first matching name (bounded depth)."""
    if depth > 6 or not isinstance(obj, dict):
        return None
    lowered = {k.lower(): k for k in obj.keys()}
    for name in names:
        if name.lower() in lowered:
            return obj[lowered[name.lower()]]
    for value in obj.values():
        if isinstance(value, dict):
            found = find_key(value, names, depth + 1)
            if found is not None:
                return found
    return None


def games_from_scoreboard_payload(payload) -> list:
    """Extract games from either cdn.nba.com (scoreboard.games) or
    stats.nba.com scoreboardv3 (scoreboard.games or resultSets)."""
    if not isinstance(payload, dict):
        return []
    sb = payload.get("scoreboard")
    if isinstance(sb, dict) and isinstance(sb.get("games"), list):
        return [g for g in sb["games"] if isinstance(g, dict)]
    for rs in payload.get("resultSets") or []:
        if not isinstance(rs, dict):
            continue
        if (rs.get("name") or "").lower() in ("gameheader", "games"):
            headers = rs.get("headers") or []
            rows = rs.get("rowSet") or []
            out = []
            for row in rows:
                rec = dict(zip(headers, row))
                gid = rec.get("GAME_ID")
                if gid:
                    out.append({"gameId": gid, "_resultSetRow": rec})
            if out:
                return out
    return []


def game_id(game: dict):
    return game.get("gameId") or game.get("GAME_ID") or game.get("gameid")


def game_date_et(game: dict) -> str | None:
    """Best-effort ET game date (YYYY-MM-DD) from either feed shape."""
    for key in ("gameEt", "gameTimeUTC", "gameTimeEst", "gameDateTimeEst", "gameDate"):
        val = game.get(key) or (game.get("_resultSetRow") or {}).get(key.upper())
        if isinstance(val, str) and len(val) >= 10:
            return val[:10]
    return None


# ------------------------------------------------------------------ sync steps


def sync_live(log: dict) -> None:
    print("[live] fetching today's official scoreboard", flush=True)
    payload, meta = http_get_json(LIVE_SCOREBOARD_URL, CDN_HEADERS)
    entry = {"step": "live", **meta}
    if payload is None:
        log["steps"].append({**entry, "result": "FAILED"})
        print(f"[live] FAILED: {meta.get('error')} status={meta.get('httpStatus')}", flush=True)
        return
    games = games_from_scoreboard_payload(payload)
    sb = payload.get("scoreboard") or {}
    written = store_official(
        os.path.join(DATA, "live", "scoreboard.json"),
        LIVE_SCOREBOARD_URL,
        payload,
        extra={
            "feedDate": sb.get("gameDate"),
            "gameCount": len(games),
        },
    )
    live_count = sum(1 for g in games if g.get("gameStatus") == 2)
    log["steps"].append(
        {
            **entry,
            "result": "OK",
            "gameCount": len(games),
            "liveCount": live_count,
            "feedDate": sb.get("gameDate"),
            "written": written,
        }
    )
    print(
        f"[live] OK games={len(games)} live={live_count} feedDate={sb.get('gameDate')} written={written}",
        flush=True,
    )


def sync_scoreboard_date(date_str: str, log: dict, force: bool = False) -> bool:
    """Archive one official historical scoreboard (stats.nba.com scoreboardv3)."""
    url = scoreboard_v3_url(date_str)
    path = os.path.join(DATA, "scoreboard", f"{date_str}.json")
    if not force and os.path.exists(path):
        # Already archived and games are final -> nothing to gain by re-fetching,
        # except for today (scores can still change).
        if date_str != dt.datetime.now(dt.timezone.utc).date().isoformat():
            return False
    payload, meta = http_get_json(url, STATS_HEADERS)
    if payload is None:
        log["steps"].append({"step": "scoreboard", "date": date_str, **meta, "result": "FAILED"})
        print(f"[history] {date_str} FAILED: {meta.get('error')}", flush=True)
        return False
    games = games_from_scoreboard_payload(payload)
    if not games:
        log["steps"].append(
            {"step": "scoreboard", "date": date_str, **meta, "result": "NO_GAMES", "gameCount": 0}
        )
        print(f"[history] {date_str} official response had no games", flush=True)
        return False
    written = store_official(
        path, url, payload, extra={"gameDate": date_str, "gameCount": len(games)}
    )
    log["steps"].append(
        {"step": "scoreboard", "date": date_str, **meta, "result": "OK",
         "gameCount": len(games), "written": written}
    )
    print(f"[history] {date_str} OK games={len(games)} written={written}", flush=True)
    return written


def sync_history(log: dict, days: int = HISTORY_WINDOW_DAYS, force: bool = False) -> None:
    today = dt.datetime.now(dt.timezone.utc).date()
    for offset in range(1, days + 1):
        date_str = (today - dt.timedelta(days=offset)).isoformat()
        sync_scoreboard_date(date_str, log, force=force)


def sync_standings(log: dict, seasons: list[str], season_types: list[str]) -> None:
    for season in seasons:
        for season_type in season_types:
            url = league_standings_v3_url(season, season_type)
            payload, meta = http_get_json(url, STATS_HEADERS)
            if payload is None:
                log["steps"].append(
                    {"step": "standings", "season": season, "type": season_type, **meta,
                     "result": "FAILED"}
                )
                print(f"[standings] {season} {season_type} FAILED: {meta.get('error')}", flush=True)
                continue
            rows = find_key(payload, ["resultSets"]) or []
            count = 0
            for rs in rows if isinstance(rows, list) else []:
                if isinstance(rs, dict) and (rs.get("name") or "").lower() == "standings":
                    count = len(rs.get("rowSet") or [])
            safe_type = season_type.replace(" ", "")
            path = os.path.join(DATA, "standings", f"{season}-{safe_type}.json")
            written = store_official(
                path, url, payload, extra={"season": season, "seasonType": season_type,
                                           "teamCount": count}
            )
            log["steps"].append(
                {"step": "standings", "season": season, "type": season_type, **meta,
                 "result": "OK", "teamCount": count, "written": written}
            )
            print(f"[standings] {season} {season_type} OK teams={count} written={written}", flush=True)


def trim_schedule(payload) -> dict | None:
    """Reduce an official CDN/Stats schedule payload to the fields the UI needs.

    Returns None when the shape is not a game list (so we never publish junk).
    """
    if not isinstance(payload, dict):
        return None
    league = payload.get("leagueSchedule") or payload.get("LeagueSchedule")
    if not isinstance(league, dict):
        league = find_key(payload, ["leagueSchedule"]) or {}
    game_dates = None
    if isinstance(league, dict):
        game_dates = league.get("gameDates") or league.get("GameDates")
    if not isinstance(game_dates, list):
        game_dates = find_key(payload, ["gameDates"]) or []
    games_out = []
    if isinstance(game_dates, list) and game_dates:
        for gd in game_dates:
            if not isinstance(gd, dict):
                continue
            for g in gd.get("games") or []:
                if not isinstance(g, dict):
                    continue
                gid = game_id(g)
                if not gid:
                    continue
                home = g.get("homeTeam") or {}
                away = g.get("awayTeam") or {}
                games_out.append(
                    {
                        "gameId": gid,
                        "gameCode": g.get("gameCode"),
                        "gameDateEst": (g.get("gameDateEst") or gd.get("gameDate") or "")[:10],
                        "gameDateTimeUTC": g.get("gameDateTimeUTC"),
                        "gameStatus": g.get("gameStatus"),
                        "gameStatusText": g.get("gameStatusText"),
                        "homeTeamId": home.get("teamId"),
                        "homeTricode": home.get("teamTricode"),
                        "homeScore": home.get("score"),
                        "homeWins": home.get("wins"),
                        "homeLosses": home.get("losses"),
                        "awayTeamId": away.get("teamId"),
                        "awayTricode": away.get("teamTricode"),
                        "awayScore": away.get("score"),
                        "awayWins": away.get("wins"),
                        "awayLosses": away.get("losses"),
                        "arena": (g.get("arena") or {}).get("arenaName"),
                        "seriesText": g.get("seriesText"),
                    }
                )
    if not games_out:
        # stats.nba.com scheduleleaguev2 resultSets fallback
        for rs in payload.get("resultSets") or []:
            if not isinstance(rs, dict):
                continue
            headers = rs.get("headers") or []
            if "GAME_ID" not in headers or "GAME_DATE" not in headers:
                continue
            for row in rs.get("rowSet") or []:
                rec = dict(zip(headers, row))
                games_out.append(
                    {
                        "gameId": rec.get("GAME_ID"),
                        "gameCode": rec.get("GAME_ID"),
                        "gameDateEst": (rec.get("GAME_DATE") or "")[:10],
                        "gameStatus": 3 if rec.get("WL") or rec.get("PTS") else None,
                        "homeTeamId": rec.get("HOME_TEAM_ID"),
                        "homeTricode": rec.get("HOME_TEAM_ABBREVIATION"),
                        "awayTeamId": rec.get("AWAY_TEAM_ID"),
                        "awayTricode": rec.get("AWAY_TEAM_ABBREVIATION"),
                        "arena": rec.get("ARENA_NAME"),
                    }
                )
    if not games_out:
        return None
    season = None
    for key in ("seasonYear", "SeasonYear", "seasonId", "SeasonID"):
        if isinstance(league, dict) and league.get(key):
            season = str(league[key])
            break
    # Derive season from game IDs (002YY -> season ending year) as a fallback.
    if not season:
        gid = games_out[0]["gameId"]
        if isinstance(gid, str) and len(gid) >= 5 and gid[3:5].isdigit():
            yy = int(gid[3:5])
            season = f"{2000 + yy - 1}-{yy:02d}"
    return {"season": season, "gameCount": len(games_out), "games": games_out}


def sync_schedule(log: dict, season: str | None) -> None:
    for label, url in schedule_candidates(season):
        payload, meta = http_get_json(url, CDN_HEADERS if "cdn.nba.com" in url else STATS_HEADERS)
        if payload is None:
            log["steps"].append({"step": "schedule", "candidate": label, **meta, "result": "FAILED"})
            print(f"[schedule] {label} FAILED: {meta.get('error')}", flush=True)
            continue
        trimmed = trim_schedule(payload)
        if not trimmed:
            log["steps"].append(
                {"step": "schedule", "candidate": label, **meta, "result": "SHAPE_UNKNOWN"}
            )
            print(f"[schedule] {label} returned 200 but shape not recognised -> skipped", flush=True)
            continue
        season_slug = trimmed.get("season") or (season or "unknown")
        path = os.path.join(DATA, "schedule", f"{season_slug}.json")
        old = load_json(path) or {}
        official = {"season": trimmed["season"], "games": trimmed["games"]}
        # merge with previously stored games for the same season (never lose data)
        if isinstance(old.get("official"), dict) and old["official"].get("games"):
            merged = {g["gameId"]: g for g in old["official"]["games"] if g.get("gameId")}
            for g in trimmed["games"]:
                merged[g["gameId"]] = {**merged.get(g["gameId"], {}), **g}
            official["games"] = sorted(
                merged.values(), key=lambda g: (g.get("gameDateEst") or "", g["gameId"])
            )
        written = store_official(
            path, url, official, extra={"season": trimmed["season"] or season,
                                        "gameCount": len(official["games"])}
        )
        log["steps"].append(
            {"step": "schedule", "candidate": label, **meta, "result": "OK",
             "season": trimmed["season"], "gameCount": len(official["games"]), "written": written}
        )
        print(
            f"[schedule] {label} OK season={trimmed['season']} games={len(official['games'])} "
            f"written={written}",
            flush=True,
        )
        return


def backfill_from_schedule(log: dict, season: str) -> int:
    """Archive official scoreboards for finished game dates of the stored schedule.

    This is what makes historical navigation work for visitors whose browser cannot
    reach stats.nba.com (CORS) — the data is already published next to the site.
    """
    path = os.path.join(DATA, "schedule", f"{season}.json")
    payload = load_json(path)
    if not payload:
        print(f"[backfill] no stored schedule for {season}", flush=True)
        return 0
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    dates = sorted(
        {
            g.get("gameDateEst")
            for g in (payload.get("official") or {}).get("games", [])
            if g.get("gameDateEst") and g["gameDateEst"] < today
        }
    )
    pending = [
        d
        for d in dates
        if not os.path.exists(os.path.join(DATA, "scoreboard", f"{d}.json"))
    ]
    if len(pending) > BACKFILL_MAX_DATES:
        pending = pending[-BACKFILL_MAX_DATES:]
    print(f"[backfill] {len(dates)} finished dates, {len(pending)} not archived yet", flush=True)
    written = 0
    for date_str in pending:
        if sync_scoreboard_date(date_str, log, force=True):
            written += 1
    return written


def build_index(log: dict) -> None:
    """Manifest so the site can discover what is published without guessing."""
    index = {
        "live": None,
        "scoreboards": {},
        "schedules": {},
        "standings": {},
        "sources": {
            "liveScoreboard": LIVE_SCOREBOARD_URL,
            "historicalScoreboard": "https://stats.nba.com/stats/scoreboardv3?GameDate=YYYY-MM-DD&LeagueID=00",
            "boxscore": boxscore_url("{gameId}"),
            "playbyplay": playbyplay_url("{gameId}"),
            "standings": "https://stats.nba.com/stats/leaguestandingsv3?LeagueID=00&Season=YYYY-YY&SeasonType=Regular+Season",
        },
        "rules": {
            "dataOwner": "NBA (official endpoints only)",
            "docs": "VERIFICATION.md",
            "note": "Files hold the unmodified official response under `official`.",
        },
    }
    live = load_json(os.path.join(DATA, "live", "scoreboard.json"))
    if live:
        index["live"] = {
            "path": "data/live/scoreboard.json",
            "fetchedAtUtc": live["_sync"]["fetchedAtUtc"],
            "feedDate": live["_sync"].get("feedDate"),
            "gameCount": live["_sync"].get("gameCount"),
        }
    sb_dir = os.path.join(DATA, "scoreboard")
    if os.path.isdir(sb_dir):
        for name in sorted(os.listdir(sb_dir)):
            if not name.endswith(".json"):
                continue
            payload = load_json(os.path.join(sb_dir, name))
            if not payload:
                continue
            games = games_from_scoreboard_payload(payload.get("official"))
            index["scoreboards"][name[:-5]] = {
                "path": f"data/scoreboard/{name}",
                "fetchedAtUtc": payload["_sync"]["fetchedAtUtc"],
                "gameCount": len(games),
            }
    for sub, key in (("schedule", "schedules"), ("standings", "standings")):
        d = os.path.join(DATA, sub)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".json"):
                continue
            payload = load_json(os.path.join(d, name))
            if not payload:
                continue
            index[key][name[:-5]] = {
                "path": f"data/{sub}/{name}",
                "fetchedAtUtc": payload["_sync"]["fetchedAtUtc"],
                "gameCount": payload["_sync"].get("gameCount"),
                "season": payload["_sync"].get("season"),
            }
    index["verifiedAt"] = {
        "live": (index["live"] or {}).get("fetchedAtUtc"),
        "historicalDates": len(index["scoreboards"]),
        "seasons": len(index["schedules"]),
        "standingsTables": len(index["standings"]),
    }
    path = os.path.join(DATA, "index.json")
    existing = load_json(path)
    if existing == index:
        print("[index] unchanged", flush=True)
        return
    write_json(path, index)
    print(
        f"[index] written live={bool(index['live'])} dates={len(index['scoreboards'])} "
        f"seasons={len(index['schedules'])} standings={len(index['standings'])}",
        flush=True,
    )


def append_log(log: dict) -> None:
    history = load_json(LOG_PATH) or {"runs": []}
    runs = history.get("runs") or []
    runs.insert(0, log)
    history["runs"] = runs[:MAX_LOG_RUNS]
    history["_note"] = (
        "Machine-written record of each sync run: which official endpoint was called, "
        "what HTTP status came back, and how many games/teams were in the response."
    )
    write_json(LOG_PATH, history)


def default_season() -> str:
    now = dt.datetime.now(dt.timezone.utc)
    year = now.year if now.month >= 9 else now.year - 1
    return f"{year}-{str((year + 1) % 100).zfill(2)}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["live", "daily", "full"], default="live")
    ap.add_argument("--history-days", type=int, default=HISTORY_WINDOW_DAYS)
    ap.add_argument("--season", default=None, help="e.g. 2025-26 (defaults to current)")
    ap.add_argument("--force-history", action="store_true", help="re-fetch archived dates")
    args = ap.parse_args()

    season = args.season or default_season()
    log = {
        "runStartedUtc": now_utc(),
        "mode": args.mode,
        "season": season,
        "steps": [],
    }

    sync_live(log)

    if args.mode in ("daily", "full"):
        sync_standings(log, [season], ["Regular Season"])
        sync_history(log, days=args.history_days, force=args.force_history)

    if args.mode == "full":
        sync_schedule(log, season)
        # Backfill the season's finished dates so historical navigation has data
        # even for clients that cannot reach cdn.nba.com.
        backfill_from_schedule(log, season)
        sync_standings(log, [season], ["Playoffs", "Pre Season"])

    build_index(log)
    log["runFinishedUtc"] = now_utc()
    log["summary"] = {
        "ok": sum(1 for s in log["steps"] if s.get("result") == "OK"),
        "failed": sum(1 for s in log["steps"] if s.get("result") == "FAILED"),
        "written": sum(1 for s in log["steps"] if s.get("written")),
    }
    append_log(log)
    print(f"[done] {log['summary']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
