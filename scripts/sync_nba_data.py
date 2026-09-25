#!/usr/bin/env python3
"""
sync_nba_data.py — build the scoreboard's official data set, server-side.

WHY SERVER-SIDE (measured, 2026-09-25 — see data/verification/):
  * A page on any non-nba.com origin is refused by cdn.nba.com (Akamai 403).
    Measured: Origin=https://buffedlizard55-lab.github.io -> 403, with or without
    a referer, over HTTP/1.1 and HTTP/2.
  * NBA's own S3 mirror of the same files answers 200 but sends no CORS header.
  * stats.nba.com never answered a GitHub-hosted runner (45s timeouts, all recipes).
  => No browser can read official NBA JSON directly. The only way to run a scoreboard
     with 100% official data and zero third parties is to fetch it on a real egress
     (GitHub Actions) and publish it next to the site.

WHAT IT PUBLISHES (all under data/, all traceable to an official URL):
  data/live/scoreboard.json        today's official live scoreboard (raw payload)
  data/schedule/<season>.json      compacted official season schedule (incl. future)
  data/scoreboard/<date>.json      compacted NBA.com date cards, incl. checked empty dates
  data/games/<gameId>/boxscore.json    current or final official player/team statistics
  data/games/<gameId>/playbyplay.json  every official action for a current or final game
  data/index.json                  manifest the site reads first
  data/verification/sync-log.json  machine record of every fetch (url, status, counts)

Compaction never invents values: every number is copied from the official payload and
each file records the official source URL plus a sha256 of the original payload.
Files are only rewritten when the official content actually changed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nba_official import (  # noqa: E402
    CDN_HEADERS,
    LIVE_SCOREBOARD_URL,
    SEASON_SCHEDULE_URL,
    HTML_HEADERS,
    games_page_url,
    live_boxscore_url,
    live_playbyplay_url,
    s3_boxscore_url,
)
from nba_official import live_playbyplay_url as pbp_url  # noqa: F401  (kept explicit)

DATA = os.path.join(ROOT, "data")
LOG_PATH = os.path.join(DATA, "verification", "sync-log.json")
MAX_LOG_RUNS = 30
GAME_ID = re.compile(r"^\d{10}$")
HISTORY_DAYS_DEFAULT = 14
BACKFILL_MAX_DATES = 60
BACKFILL_BATCH = 6
ARCHIVE_MAX_GAMES_PER_RUN = 24
# Play-by-play is ~160 KB per game. The archive keeps everything under this budget
# (~40 MB) and, when it grows past it, drops the OLDEST regular-season play-by-play
# first — playoffs, recent games and the last 60 days are never pruned. Any pruned
# night can be restored on demand: --date YYYY-MM-DD --with-games --force.
PBP_BUDGET_BYTES = 40 * 1024 * 1024
PBP_RETENTION_DAYS = 60
PIPELINE = "scripts/sync_nba_data.py"

# Player statistics fields kept from the official box score (subset = smaller files).
PLAYER_STAT_KEYS = [
    "minutes", "points", "reboundsTotal", "assists", "steals", "blocks",
    "turnovers", "fieldGoalsMade", "fieldGoalsAttempted", "fieldGoalsPercentage",
    "threePointersMade", "threePointersAttempted", "threePointersPercentage",
    "freeThrowsMade", "freeThrowsAttempted", "freeThrowsPercentage",
    "reboundsOffensive", "reboundsDefensive", "foulsPersonal", "plusMinusPoints",
    "pointsInThePaint", "pointsFastBreak", "assistsTurnoverRatio", "comment",
]
TEAM_STAT_KEYS = [
    "points", "reboundsTotal", "assists", "steals", "blocks", "turnovers",
    "fieldGoalsMade", "fieldGoalsAttempted", "fieldGoalsPercentage",
    "threePointersMade", "threePointersAttempted", "threePointersPercentage",
    "freeThrowsMade", "freeThrowsAttempted", "freeThrowsPercentage",
    "reboundsOffensive", "reboundsDefensive", "foulsPersonal", "pointsInThePaint",
    "pointsFastBreak", "pointsFromTurnovers", "pointsSecondChance", "benchPoints",
    "biggestLead", "biggestScoringRun", "leadChanges", "timesTied", "timeoutsRemaining",
]
ACTION_KEYS = [
    "actionNumber", "period", "clock", "teamTricode", "personId", "playerNameI",
    "actionType", "subType", "description", "scoreHome", "scoreAway", "isFieldGoal",
    "shotResult", "pointsTotal", "reboundTotal", "stealPersonId", "blockPersonId",
    "assistPersonId", "shotDistance", "x", "y",
]


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- http


def http_get(url: str, headers: dict, timeout: int = 40, retries: int = 3):
    """GET a URL and return (payload_or_None, meta). Never raises.

    meta always carries `sourceSha256`/`sourceBytes` of the raw response body, so the
    exact official bytes behind every stored file are recorded and reproducible.
    """
    meta = {"url": url, "attempts": 0, "httpStatus": None, "error": None, "bytes": 0}
    for attempt in range(1, retries + 1):
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
                meta["sourceSha256"] = hashlib.sha256(raw).hexdigest()
                meta["cacheControl"] = resp.headers.get("Cache-Control")
                try:
                    return json.loads(raw.decode("utf-8")), meta
                except Exception as exc:
                    meta["error"] = f"JSON parse failed: {exc}"
                    meta["bodyPrefix"] = raw[:120].decode("utf-8", "replace")
                    return None, meta
        except urllib.error.HTTPError as exc:
            body = exc.read()
            meta["httpStatus"] = exc.code
            meta["error"] = f"HTTPError {exc.code}"
            # 403 from this CDN is either the Akamai wall (bot-looking request) or a
            # missing object. Record which, so the log is diagnosable.
            meta["bodyPrefix"] = body[:100].decode("utf-8", "replace")
            if 400 <= exc.code < 500 and exc.code != 429:
                return None, meta
        except Exception as exc:
            meta["error"] = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(min(2 ** attempt, 6))
    return None, meta


def http_get_html(url: str, timeout: int = 45):
    meta = {"url": url, "httpStatus": None, "error": None, "bytes": 0}
    try:
        req = urllib.request.Request(url, headers=HTML_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            meta["httpStatus"] = resp.status
            meta["bytes"] = len(raw)
            meta["sourceSha256"] = hashlib.sha256(raw).hexdigest()
            return raw.decode("utf-8", "replace"), meta
    except Exception as exc:
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return None, meta


# -------------------------------------------------------------------------- store


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
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
        fh.write("\n")


def valid_date(value: str) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def valid_game_id(value) -> bool:
    return isinstance(value, str) and bool(GAME_ID.fullmatch(value))


def detail_era(game: dict) -> bool:
    """CDN game files have been observed from the 2019-20 season, not earlier."""
    season = game.get("seasonYear") or ""
    return isinstance(season, str) and season[:4].isdigit() and int(season[:4]) >= 2019


def known_schedule_game_ids(date_str: str) -> set[str]:
    """Known NBA season-schedule game IDs for an ET date, if published here.

    The date cards and live feed are authoritative for results; the schedule is
    only a guard against declaring a known game night empty when a feed is partial.
    An absent schedule cannot establish that a day had no games.
    """
    schedule_dir = os.path.join(DATA, "schedule")
    if not os.path.isdir(schedule_dir):
        return set()
    ids = set()
    for filename in os.listdir(schedule_dir):
        if filename.endswith(".json"):
            sched = load_json(os.path.join(schedule_dir, filename)) or {}
            ids.update(g["gameId"] for g in sched.get("games", [])
                       if g.get("gameDateEst") == date_str and valid_game_id(g.get("gameId")))
    return ids


def scoreboard_error(payload) -> str | None:
    """Never turn a changed/partial NBA response into a fake 'no games' night."""
    sb = payload.get("scoreboard") if isinstance(payload, dict) else None
    if not isinstance(sb, dict) or not valid_date(sb.get("gameDate")):
        return "missing or invalid scoreboard.gameDate"
    if not isinstance(sb.get("games"), list):
        return "missing scoreboard.games array"
    if not sb["games"] and known_schedule_game_ids(sb["gameDate"]):
        return "empty live feed contradicts published NBA season schedule; retry rather than display zero games"
    seen = set()
    for g in sb["games"]:
        if not isinstance(g, dict) or not valid_game_id(g.get("gameId")):
            return "game without a valid official gameId"
        if g["gameId"] in seen:
            return f"duplicate gameId {g['gameId']}"
        seen.add(g["gameId"])
        for side in ("homeTeam", "awayTeam"):
            t = g.get(side)
            if not isinstance(t, dict) or not t.get("teamId") or not t.get("teamTricode"):
                return f"game {g['gameId']} missing {side} identity"
    return None


def last_action_score(actions: list) -> tuple | None:
    for a in reversed(actions):
        if a.get("scoreAway") is not None and a.get("scoreHome") is not None:
            return str(a["scoreAway"]), str(a["scoreHome"])
    return None


def store_compact(path: str, official_url: str, content, note: str,
                  source_sha256: str | None = None, source_bytes: int | None = None,
                  gate=None) -> bool:
    """Write a compacted file when its stored content changed.

    `contentHash` is a sha256 of the stored content (so "unchanged" means "nothing the
    site shows moved"), while `sourceSha256`/`sourceBytes` record the exact official
    response the content was built from — the two together make every file auditable.
    """
    # `gate` (when given) is the part of the payload the board actually depends on:
    # transport metadata such as the feed's own generation time must not cause a commit
    # on every run. The full payload is still stored; only the change test narrows.
    content_hash = semantic_hash(gate if gate is not None else content)
    existing = load_json(path)
    if isinstance(existing, dict) and existing.get("_sync", {}).get("contentHash") == content_hash:
        return False
    sync = {
        "source": official_url,
        "fetchedAtUtc": now_utc(),
        "contentHash": content_hash,
        "note": note,
        "pipeline": PIPELINE,
    }
    if source_sha256:
        sync["sourceSha256"] = source_sha256
    if source_bytes:
        sync["sourceBytes"] = source_bytes
    write_json(path, {"_sync": sync, **content})
    return True


# ---------------------------------------------------------------------- compactors


def _subset(src: dict, keys: list) -> dict:
    if not isinstance(src, dict):
        return {}
    return {k: src.get(k) for k in keys if k in src}


def compact_periods(team: dict) -> list:
    out = []
    for p in (team or {}).get("periods") or []:
        if isinstance(p, dict):
            out.append({"period": p.get("period"), "periodType": p.get("periodType"),
                        "score": p.get("score")})
    return out


def compact_player(player: dict) -> dict | None:
    if not isinstance(player, dict) or not player.get("personId"):
        return None
    return {
        "personId": player.get("personId"),
        "name": player.get("name") or player.get("familyName"),
        "nameI": player.get("nameI"),
        "firstName": player.get("firstName"),
        "familyName": player.get("familyName"),
        "jerseyNum": player.get("jerseyNum"),
        "position": player.get("position"),
        "starter": player.get("starter"),
        "oncourt": player.get("oncourt"),
        "played": player.get("played"),
        "statistics": _subset(player.get("statistics") or {}, PLAYER_STAT_KEYS),
    }


def compact_team_box(team: dict) -> dict:
    return {
        "teamId": team.get("teamId"),
        "teamCity": team.get("teamCity"),
        "teamName": team.get("teamName"),
        "teamTricode": team.get("teamTricode"),
        "score": team.get("score"),
        "inBonus": team.get("inBonus"),
        "timeoutsRemaining": team.get("timeoutsRemaining"),
        "periods": compact_periods(team),
        "statistics": _subset(team.get("statistics") or {}, TEAM_STAT_KEYS),
        "players": [p for p in (compact_player(pl) for pl in (team.get("players") or [])) if p],
    }


def compact_boxscore(payload: dict) -> dict:
    game = payload.get("game") or {}
    return {
        "gameId": game.get("gameId"),
        "gameCode": game.get("gameCode"),
        "gameStatus": game.get("gameStatus"),
        "gameStatusText": game.get("gameStatusText"),
        "gameEt": game.get("gameEt"),
        "gameTimeUTC": game.get("gameTimeUTC"),
        "gameTimeLocal": game.get("gameTimeLocal"),
        "period": game.get("period"),
        "regulationPeriods": game.get("regulationPeriods"),
        "attendance": game.get("attendance"),
        "duration": game.get("duration"),
        "seriesText": game.get("seriesText"),
        "seriesGameNumber": game.get("seriesGameNumber"),
        "arena": game.get("arena"),
        "officials": game.get("officials"),
        "homeTeam": compact_team_box(game.get("homeTeam") or {}),
        "awayTeam": compact_team_box(game.get("awayTeam") or {}),
        "gameLeaders": {
            side: {
                k: v for k, v in (game.get("gameLeaders", {}).get(side) or {}).items()
                if k in ("personId", "name", "jerseyNum", "position", "teamTricode",
                         "points", "rebounds", "assists")
            }
            for side in ("homeLeaders", "awayLeaders")
        },
    }


def compact_actions(payload: dict, limit: int | None = None) -> list:
    actions = (payload.get("game") or {}).get("actions") or []
    picked = actions[-limit:] if limit else actions
    out = []
    for a in picked:
        if not isinstance(a, dict):
            continue
        out.append({k: a.get(k) for k in ACTION_KEYS if k in a})
    return out


def compact_schedule(payload: dict) -> dict | None:
    league = payload.get("leagueSchedule") or {}
    if not isinstance(league, dict) or not league.get("gameDates"):
        return None
    games = []
    for gd in league["gameDates"]:
        for g in gd.get("games") or []:
            if not isinstance(g, dict) or not g.get("gameId"):
                continue
            home = g.get("homeTeam") or {}
            away = g.get("awayTeam") or {}
            games.append({
                "gameId": g.get("gameId"),
                "gameCode": g.get("gameCode"),
                "gameStatus": g.get("gameStatus"),
                "gameStatusText": g.get("gameStatusText"),
                "gameDateEst": (g.get("gameDateEst") or "")[:10],
                "gameDateTimeUTC": g.get("gameDateTimeUTC"),
                "day": g.get("day"),
                "gameLabel": g.get("gameLabel"),
                "gameSubLabel": g.get("gameSubLabel"),
                "seriesGameNumber": g.get("seriesGameNumber"),
                "seriesText": g.get("seriesText"),
                "arena": g.get("arenaName"),
                "arenaCity": g.get("arenaCity"),
                "arenaState": g.get("arenaState"),
                "postponedStatus": g.get("postponedStatus"),
                "homeTeamId": home.get("teamId"),
                "homeTricode": home.get("teamTricode"),
                "homeCity": home.get("teamCity"),
                "homeName": home.get("teamName"),
                "homeWins": home.get("wins"),
                "homeLosses": home.get("losses"),
                "homeScore": home.get("score"),
                "awayTeamId": away.get("teamId"),
                "awayTricode": away.get("teamTricode"),
                "awayCity": away.get("teamCity"),
                "awayName": away.get("teamName"),
                "awayWins": away.get("wins"),
                "awayLosses": away.get("losses"),
                "awayScore": away.get("score"),
                "broadcasters": [
                    {"name": b.get("broadcasterDisplay"), "abbr": b.get("broadcasterAbbreviation"),
                     "type": b.get("broadcasterType"), "region": b.get("regionId")}
                    for b in (g.get("broadcasters") or {}).get("nationalBroadcasters", [])
                ] + [
                    {"name": b.get("broadcasterDisplay"), "abbr": b.get("broadcasterAbbreviation"),
                     "type": "local"}
                    for b in (g.get("broadcasters") or {}).get("homeBroadcasters", [])[:1]
                ],
                "pointsLeaders": [
                    {"personId": p.get("personId"), "name": p.get("firstName", "") + " " + (p.get("lastName") or ""),
                     "points": p.get("points"), "teamTricode": p.get("teamTricode")}
                    for p in (g.get("pointsLeaders") or [])
                ],
            })
    return {
        "season": league.get("seasonYear"),
        "leagueId": league.get("leagueId"),
        "gameCount": len(games),
        "firstGameDate": min((g["gameDateEst"] for g in games), default=None),
        "lastGameDate": max((g["gameDateEst"] for g in games), default=None),
        "games": games,
    }


def leaders_from_box(team: dict) -> dict | None:
    best = None
    for pl in team.get("players") or []:
        st = pl.get("statistics") or {}
        pts = st.get("points") or 0
        if best is None or (pts or 0) > (best[1] or 0):
            best = (pl, pts)
    if not best:
        return None
    pl, pts = best
    st = pl.get("statistics") or {}
    return {
        "personId": pl.get("personId"),
        "name": pl.get("name") or pl.get("nameI"),
        "jerseyNum": pl.get("jerseyNum"),
        "position": pl.get("position"),
        "teamTricode": team.get("teamTricode"),
        "points": pts,
        "rebounds": st.get("reboundsTotal"),
        "assists": st.get("assists"),
    }


def digest_game(box_payload: dict, source_url: str) -> dict:
    """One row of a historical scoreboard, copied from an official box score."""
    game = compact_boxscore(box_payload)
    home, away = game.get("homeTeam") or {}, game.get("awayTeam") or {}
    return {
        "gameId": game.get("gameId"),
        "gameCode": game.get("gameCode"),
        "gameStatus": game.get("gameStatus"),
        "gameStatusText": game.get("gameStatusText"),
        "gameEt": game.get("gameEt"),
        "gameTimeUTC": game.get("gameTimeUTC"),
        "period": game.get("period"),
        "attendance": game.get("attendance"),
        "arena": (game.get("arena") or {}).get("arenaName") if isinstance(game.get("arena"), dict) else None,
        "seriesText": game.get("seriesText"),
        "home": {
            "teamId": home.get("teamId"), "tricode": home.get("teamTricode"),
            "city": home.get("teamCity"), "name": home.get("teamName"),
            "score": home.get("score"), "periods": home.get("periods"),
            "leaders": leaders_from_box(home),
        },
        "away": {
            "teamId": away.get("teamId"), "tricode": away.get("teamTricode"),
            "city": away.get("teamCity"), "name": away.get("teamName"),
            "score": away.get("score"), "periods": away.get("periods"),
            "leaders": leaders_from_box(away),
        },
        "_source": source_url,
    }


# ----------------------------------------------------------------------- steps


def live_gate_payload(payload: dict) -> dict:
    """The official live feed minus volatile transport metadata (`meta.time`).

    Measured: the feed's `meta.time` changes on every request while the game data is
    identical (an empty offseason feed is otherwise byte-identical between runs), so
    hashing it rewrote `data/live/scoreboard.json` every 10 minutes — ~144 commits a
    day that trip GitHub Pages' build-rate limit. The gate keeps real changes (scores,
    clocks, periods, status) and drops only the timestamp.
    """
    sb = payload.get("scoreboard") or {}
    if not isinstance(sb, dict):
        return payload
    return {"feedDate": sb.get("gameDate"), "games": sb.get("games") or []}


def sync_live(log: dict) -> bool:
    """Publish NBA's live board and *complete* box/PBP for games underway.

    A live game must not have to wait until Final for its player stats or its first
    400 actions. The same game files are updated while live, then kept at final.
    """
    print("[live] official today's scoreboard", flush=True)
    payload, meta = http_get(LIVE_SCOREBOARD_URL, CDN_HEADERS)
    error = scoreboard_error(payload)
    if error:
        log["steps"].append({"step": "live", **meta, "result": "INVALID" if payload is not None else "FAILED",
                             "error": meta.get("error") or error})
        print(f"[live] FAILED status={meta.get('httpStatus')} err={error}", flush=True)
        return False  # keep the last good snapshot, never publish an invented empty night
    sb = payload["scoreboard"]
    games = sb["games"]
    written = store_compact(
        os.path.join(DATA, "live", "scoreboard.json"), LIVE_SCOREBOARD_URL,
        {"feedDate": sb["gameDate"], "gameCount": len(games),
         "liveCount": sum(1 for g in games if g.get("gameStatus") == 2),
         "scoreboard": payload},
        "unmodified official payload under scoreboard (write gated on game data, not meta.time)",
        source_sha256=meta.get("sourceSha256"), source_bytes=meta.get("bytes"),
        gate=live_gate_payload(payload),
    )
    log["steps"].append({"step": "live", **meta, "result": "OK", "gameCount": len(games),
                         "feedDate": sb["gameDate"], "written": written})
    print(f"[live] OK games={len(games)} feedDate={sb['gameDate']} written={written}", flush=True)

    for g in games:
        if g.get("gameStatus") not in (2, 3):
            continue
        gid = g["gameId"]
        old_box = load_json(os.path.join(DATA, "games", gid, "boxscore.json")) or {}
        # Refresh every in-progress game; at Final replace any provisional detail.
        bg = old_box.get("game") or {}
        final_drift = g["gameStatus"] == 3 and any(
            str((bg.get(side) or {}).get("score")) != str((g.get(side) or {}).get("score"))
            or (bg.get(side) or {}).get("teamId") != (g.get(side) or {}).get("teamId")
            for side in ("homeTeam", "awayTeam"))
        force = g["gameStatus"] == 2 or bg.get("gameStatus") != 3 or final_drift
        archive_game(gid, log, force=force, expected=g)
    # Obsolete last-80-actions feed cannot masquerade as the full live PBP.
    old_plays = os.path.join(DATA, "live", "plays.json")
    if os.path.exists(old_plays):
        os.remove(old_plays)
        log["steps"].append({"step": "removeTruncatedPlays", "result": "OK", "written": True})
    return True


def archive_game(game_id: str, log: dict, force: bool = False,
                 expected: dict | None = None) -> bool:
    """Keep official full box/PBP for a live or final game; reject swapped/partial files."""
    if not valid_game_id(game_id):
        log["steps"].append({"step": "gameDetails", "gameId": game_id, "result": "INVALID"})
        return False
    box_path = os.path.join(DATA, "games", game_id, "boxscore.json")
    pbp_path = os.path.join(DATA, "games", game_id, "playbyplay.json")
    if not force and os.path.exists(box_path) and os.path.exists(pbp_path):
        saved_box = (load_json(box_path) or {}).get("game") or {}
        saved_actions = (load_json(pbp_path) or {}).get("actions") or []
        complete = (saved_box.get("gameStatus") == 3 and saved_actions and
                    (saved_actions[-1].get("actionType"), saved_actions[-1].get("subType")) == ("game", "end") and
                    last_action_score(saved_actions) == (
                        str((saved_box.get("awayTeam") or {}).get("score")),
                        str((saved_box.get("homeTeam") or {}).get("score"))))
        agrees_with_card = not expected or expected.get("gameStatus") != 3 or all(
            (saved_box.get(f"{side}Team") or {}).get("teamId") ==
            (expected.get(f"{side}Team") or expected.get(side) or {}).get("teamId") and
            str((saved_box.get(f"{side}Team") or {}).get("score")) ==
            str((expected.get(f"{side}Team") or expected.get(side) or {}).get("score"))
            for side in ("home", "away"))
        if complete and agrees_with_card:
            return False  # only a COMPLETE, cross-checked final can be skipped
    wrote = False
    box, bmeta = http_get(live_boxscore_url(game_id), CDN_HEADERS)
    game = box.get("game") if isinstance(box, dict) else None
    valid_box = (isinstance(game, dict) and game.get("gameId") == game_id
                 and game.get("gameStatus") in (2, 3)
                 and all(isinstance(game.get(side), dict)
                         and game[side].get("teamId") and game[side].get("teamTricode")
                         and game[side].get("score") is not None
                         for side in ("homeTeam", "awayTeam")))
    if valid_box and expected and expected.get("gameStatus") == 3:
        # 'expected' may be an NBA live-board game (homeTeam/awayTeam) or a
        # compacted NBA.com date card (home/away). Compare before storing a box,
        # not merely after an inconsistent archive has already been published.
        matches = game.get("gameStatus") == 3 and all(
            (game[f"{side}Team"].get("teamId") ==
             (expected.get(f"{side}Team") or expected.get(side) or {}).get("teamId") and
             str(game[f"{side}Team"].get("score")) ==
             str((expected.get(f"{side}Team") or expected.get(side) or {}).get("score")))
            for side in ("home", "away"))
        if not matches:
            log["steps"].append({"step": "archiveBoxscore", "gameId": game_id, **bmeta,
                                 "result": "MISMATCH", "error": "NBA scoreboard/date card and NBA box score disagree"})
            return False  # don't publish a contradictory 'final'; retry on next poll
    if valid_box:
        compact = compact_boxscore(box)
        box_written = store_compact(
            box_path, live_boxscore_url(game_id),
            {"game": compact, "gameLeaders": compact.get("gameLeaders")},
            "official box score, player/team stats copied field-for-field (subset of keys)",
            source_sha256=bmeta.get("sourceSha256"), source_bytes=bmeta.get("bytes"),
        )
        wrote |= box_written
        log["steps"].append({"step": "archiveBoxscore", "gameId": game_id, **bmeta,
                             "result": "OK", "written": box_written})
    else:
        log["steps"].append({"step": "archiveBoxscore", "gameId": game_id, **bmeta,
                             "result": "INVALID" if box is not None else "FAILED",
                             "error": bmeta.get("error") or "missing/mismatched game, teams or score"})

    # A previously stored in-progress PBP is not a complete final game. Once the
    # box becomes final, remove the provisional action file if it does not already
    # contain NBA's Game End with the final score. This also makes the next poll
    # retry a PBP fetch that failed during the final transition.
    if valid_box and game["gameStatus"] == 3 and os.path.exists(pbp_path):
        old_actions = (load_json(pbp_path) or {}).get("actions") or []
        old_last = old_actions[-1] if old_actions else {}
        complete = (old_last.get("actionType"), old_last.get("subType")) == ("game", "end")
        expected_score = (str(game["awayTeam"]["score"]), str(game["homeTeam"]["score"]))
        if not complete or last_action_score(old_actions) != expected_score:
            os.remove(pbp_path)
            wrote = True
            log["steps"].append({"step": "retireIncompletePlaybyplay", "gameId": game_id,
                                 "result": "OK", "written": True,
                                 "note": "provisional actions did not contain NBA's matching final Game End"})

    pbp, pmeta = http_get(live_playbyplay_url(game_id), CDN_HEADERS)
    pg = pbp.get("game") if isinstance(pbp, dict) else None
    raw_actions = pg.get("actions") if isinstance(pg, dict) else None
    if (not isinstance(pg, dict) or pg.get("gameId") != game_id
            or not isinstance(raw_actions, list) or not raw_actions
            or not all(isinstance(a, dict) for a in raw_actions)):
        log["steps"].append({"step": "archivePlaybyplay", "gameId": game_id, **pmeta,
                             "result": "INVALID" if pbp is not None else "FAILED",
                             "error": pmeta.get("error") or "missing/mismatched game or actions"})
        return wrote
    actions = compact_actions(pbp)  # ALL actions, including during a live game
    current_box = game if valid_box else (load_json(box_path) or {}).get("game") or {}
    if current_box.get("gameStatus") == 3:
        expected_score = (str(current_box["awayTeam"]["score"]), str(current_box["homeTeam"]["score"]))
        actual = last_action_score(actions)
        if actual != expected_score or (actions[-1].get("actionType"), actions[-1].get("subType")) != ("game", "end"):
            log["steps"].append({"step": "archivePlaybyplay", "gameId": game_id, **pmeta,
                                 "result": "MISMATCH", "error": f"PBP score {actual}, final action {actions[-1].get('actionType')}/{actions[-1].get('subType')} != completed box {expected_score}"})
            return wrote  # no provisional actions can masquerade as complete Final
    pbp_written = store_compact(
        pbp_path, live_playbyplay_url(game_id),
        {"gameId": game_id, "actionCount": len(actions), "actions": actions},
        "all official play-by-play actions, fields copied field-for-field (subset of keys)",
        source_sha256=pmeta.get("sourceSha256"), source_bytes=pmeta.get("bytes"),
    )
    wrote |= pbp_written
    log["steps"].append({"step": "archivePlaybyplay", "gameId": game_id, **pmeta,
                         "result": "OK", "actionCount": len(actions), "written": pbp_written})
    print(f"[details] {game_id} actions={len(actions)} written={wrote}", flush=True)
    return wrote


def game_ids_for_date_from_schedule(date_str: str, season: str) -> list:
    sched = load_json(os.path.join(DATA, "schedule", f"{season}.json"))
    if not sched:
        return []
    ids = [g["gameId"] for g in sched.get("games", []) if g.get("gameDateEst") == date_str]
    return ids


def compact_card(cd: dict) -> dict | None:
    """One scoreboard row, copied from an official www.nba.com game card."""
    if not isinstance(cd, dict) or not cd.get("gameId"):
        return None

    def team(t: dict) -> dict:
        t = t or {}
        leader = t.get("teamLeader") or {}
        return {
            "teamId": t.get("teamId"), "tricode": t.get("teamTricode"),
            "name": t.get("teamName"), "slug": t.get("teamSlug"),
            "wins": t.get("wins"), "losses": t.get("losses"),
            "score": t.get("score"), "inBonus": t.get("inBonus"),
            "timeoutsRemaining": t.get("timeoutsRemaining"),
            "periods": [{"period": p_.get("period"), "score": p_.get("score")}
                        for p_ in (t.get("periods") or []) if isinstance(p_, dict)],
            "leader": {
                "personId": leader.get("personId"), "name": leader.get("name"),
                "jerseyNum": leader.get("jerseyNum"), "position": leader.get("position"),
                "points": leader.get("points"), "rebounds": leader.get("rebounds"),
                "assists": leader.get("assists"), "blocks": leader.get("blocks"),
                "steals": leader.get("steals"),
            } if leader.get("personId") and leader.get("name") else None,
        }

    bcs = cd.get("broadcasters") or {}
    broadcasters = [b.get("broadcasterDisplayName") for b in (bcs.get("nationalBroadcasters") or [])]
    for key in ("homeTvBroadcasters", "awayTvBroadcasters"):
        for b in (bcs.get(key) or [])[:1]:
            broadcasters.append(b.get("broadcasterDisplayName"))
    return {
        "gameId": cd.get("gameId"),
        "leagueId": cd.get("leagueId"),
        "seasonYear": cd.get("seasonYear"),
        "seasonType": cd.get("seasonType"),
        "gameStatus": cd.get("gameStatus"),
        "gameStatusText": cd.get("gameStatusText"),
        "gameClock": cd.get("gameClock"),
        "period": cd.get("period"),
        "gameTimeEastern": cd.get("gameTimeEastern"),
        "gameTimeUtc": cd.get("gameTimeUtc"),
        "gameSubtype": cd.get("gameSubtype"),
        "ifNecessary": cd.get("ifNecessary"),
        "isNeutral": cd.get("isNeutral"),
        "home": team(cd.get("homeTeam")),
        "away": team(cd.get("awayTeam")),
        "broadcasters": [b for b in broadcasters if b],
        "shareUrl": cd.get("shareUrl"),
    }


def cards_for_date(date_str: str, log: dict):
    """Read the official www.nba.com/games?date=<date> page.

    Measured (data/verification/cards-probe.json): the page server-renders every
    game of that date in __NEXT_DATA__, with period scores, team records, leaders
    and broadcasters — for any date, including the 1990s.
    """
    import re
    html, meta = http_get_html(games_page_url(date_str))
    if not html:
        log["steps"].append({"step": "cards", "date": date_str, **meta, "result": "FAILED"})
        return None, meta
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        log["steps"].append({"step": "cards", "date": date_str, **meta, "result": "NO_NEXT_DATA"})
        return None, meta
    try:
        nd = json.loads(m.group(1))
    except Exception as exc:
        log["steps"].append({"step": "cards", "date": date_str, **meta,
                             "result": f"PARSE_ERROR {exc}"})
        return None, meta
    props = ((nd.get("props") or {}).get("pageProps") or {})
    selected = props.get("selectedDate")
    # NBA currently server-renders this alongside the cards. When it is a date
    # string, reject a redirected/default page rather than archive its empty feed
    # as the requested night. Older page variants may omit this field entirely.
    if isinstance(selected, str) and selected and not selected.startswith(date_str):
        log["steps"].append({"step": "cards", "date": date_str, **meta,
                             "result": "INVALID", "error": f"NBA page selected {selected}, not {date_str}"})
        return None, meta
    feed = props.get("gameCardFeed")
    modules = feed.get("modules") if isinstance(feed, dict) else None
    if not isinstance(modules, list):
        log["steps"].append({"step": "cards", "date": date_str, **meta,
                             "result": "INVALID", "error": "missing gameCardFeed.modules"})
        return None, meta
    try:
        cards = []
        for mod in modules:
            if not isinstance(mod, dict) or not isinstance(mod.get("cards"), list):
                raise ValueError("invalid cards module")
            cards.extend(mod["cards"])
        rows = []
        for card in cards:
            cd = card.get("cardData") if isinstance(card, dict) else None
            if (not isinstance(cd, dict) or not valid_game_id(cd.get("gameId"))
                    or not str(cd.get("gameTimeEastern") or "").startswith(date_str)
                    or any(not isinstance(cd.get(t), dict) or not cd[t].get("teamId")
                           or not cd[t].get("teamTricode") for t in ("homeTeam", "awayTeam"))):
                raise ValueError("missing game identity or ET date differs from request")
            rows.append(compact_card(cd))
        if len({r["gameId"] for r in rows}) != len(rows):
            raise ValueError("duplicate game IDs")
    except ValueError as exc:
        log["steps"].append({"step": "cards", "date": date_str, **meta,
                             "result": "INVALID", "error": str(exc)})
        return None, meta  # no silent dropping of bad rows or invented 0-game dates
    log["steps"].append({"step": "cards", "date": date_str, **meta, "result": "OK",
                         "gameCount": len(rows)})
    print(f"[cards] {date_str} cards={len(rows)}", flush=True)
    return rows, meta


def sync_date_digest(date_str: str, log: dict, season: str | None = None,
                     force: bool = False) -> bool:
    """Archive one NBA date; re-read unfinished rows until NBA marks them final.

    A date that is 'yesterday' in UTC may still have games live in the US. It must
    not get frozen as an incomplete historical scoreboard at the first fetch.
    """
    if not valid_date(date_str):
        log["steps"].append({"step": "digest", "date": date_str, "result": "INVALID"})
        return False
    path = os.path.join(DATA, "scoreboard", f"{date_str}.json")
    existing = load_json(path)
    scheduled = known_schedule_game_ids(date_str)
    if not force and existing and isinstance(existing.get("games"), list):
        games = existing["games"]
        # A schedule published later can reveal that an earlier zero-card fetch
        # was incomplete. Do not freeze that apparent empty night forever.
        if games and all(g.get("gameStatus") == 3 for g in games):
            return True
        if not games and not scheduled:
            return True
    rows, meta = cards_for_date(date_str, log)
    if rows is None:
        return False
    if not rows and scheduled:
        log["steps"].append({"step": "cardsVsSchedule", "date": date_str,
                             "url": games_page_url(date_str), "result": "MISMATCH",
                             "error": f"NBA date page has zero cards; NBA schedule lists {len(scheduled)} games"})
        return False
    rows.sort(key=lambda r: (r.get("gameTimeUtc") or "", r.get("gameId") or ""))
    content = {"gameDate": date_str, "gameCount": len(rows), "games": rows,
               "sourcePage": games_page_url(date_str)}
    written = store_compact(path, games_page_url(date_str), content,
                            "rows copied from the official NBA.com game cards for this date",
                            source_sha256=meta.get("sourceSha256"), source_bytes=meta.get("bytes"))
    log["steps"].append({"step": "digest", "date": date_str, "result": "OK",
                         "gameCount": len(rows), "written": written})
    print(f"[digest] {date_str} OK games={len(rows)} written={written}", flush=True)
    return True


def verify_digest_against_boxscore(date_str: str, log: dict) -> None:
    """Compare final date cards with *archived official* box scores, when available.

    Never infer a box score exists from the game ID: the CDN is only confirmed for
    sampled games from 2019-20 on and older files may be missing or pruned.
    """
    digest = load_json(os.path.join(DATA, "scoreboard", f"{date_str}.json")) or {}
    for g in digest.get("games", []):
        gid = g.get("gameId")
        if g.get("gameStatus") != 3 or not valid_game_id(gid):
            continue
        box = load_json(os.path.join(DATA, "games", gid, "boxscore.json")) or {}
        official = box.get("game")
        if not isinstance(official, dict) or official.get("gameStatus") != 3:
            continue  # not cross-checked; do not claim otherwise
        matches = official.get("gameId") == gid
        for side in ("home", "away"):
            team = official.get(f"{side}Team") or {}
            card = g.get(side) or {}
            matches &= (team.get("teamId") == card.get("teamId") and
                        str(team.get("score")) == str(card.get("score")))
        entry = {"step": "verifyScore", "gameId": gid, "date": date_str,
                 "url": box.get("_sync", {}).get("source"),
                 "cardsSource": digest.get("_sync", {}).get("source"),
                 "result": "OK" if matches else "MISMATCH"}
        log["steps"].append(entry)
        if not matches:
            print(f"[verify] MISMATCH {gid} between official cards and CDN box score", flush=True)


def sync_schedule(log: dict) -> None:
    payload, meta = http_get(SEASON_SCHEDULE_URL, CDN_HEADERS, timeout=60)
    if not payload:
        log["steps"].append({"step": "schedule", **meta, "result": "FAILED"})
        print(f"[schedule] FAILED status={meta.get('httpStatus')}", flush=True)
        return
    compact = compact_schedule(payload)
    if (not compact or not re.fullmatch(r"\d{4}-\d{2}", str(compact.get("season")))
            or compact.get("leagueId") != "00" or not compact["gameCount"]
            or len({g["gameId"] for g in compact["games"]}) != compact["gameCount"]):
        log["steps"].append({"step": "schedule", **meta, "result": "INVALID",
                             "error": "season, league, games or unique IDs missing"})
        return
    season = compact["season"]
    path = os.path.join(DATA, "schedule", f"{season}.json")
    written = store_compact(path, SEASON_SCHEDULE_URL, compact,
                            "official season schedule; includes future games (verified)",
                            source_sha256=meta.get("sourceSha256"), source_bytes=meta.get("bytes"))
    log["steps"].append({"step": "schedule", "season": season, **meta, "result": "OK",
                         "gameCount": compact["gameCount"], "written": written})
    print(f"[schedule] OK season={season} games={compact['gameCount']} written={written}", flush=True)


def sync_archive_pending(log: dict, days: int, max_games: int = ARCHIVE_MAX_GAMES_PER_RUN) -> None:
    """Archive box scores + play-by-play for games that finished but are not stored.

    Bounded per run: each archived game is ~24 KB of box score plus ~160 KB of
    play-by-play, so the pipeline archives the newest games first and stops at the
    cap (the next run continues where this one left off).
    """
    attempted = 0
    today = dt.datetime.now(dt.timezone.utc).date()
    for offset in range(1, days + 1):
        if attempted >= max_games:
            break
        date_str = (today - dt.timedelta(days=offset)).isoformat()
        digest = load_json(os.path.join(DATA, "scoreboard", f"{date_str}.json")) or {}
        for g in digest.get("games", []):
            gid = g.get("gameId")
            if g.get("gameStatus") != 3 or not valid_game_id(gid) or not detail_era(g):
                continue
            box = load_json(os.path.join(DATA, "games", gid, "boxscore.json")) or {}
            pbp_path = os.path.join(DATA, "games", gid, "playbyplay.json")
            bg = box.get("game") or {}
            pbp_file = load_json(pbp_path) or {}
            actions = pbp_file.get("actions") or []
            matches = bg.get("gameStatus") == 3 and all(
                (bg.get(f"{side}Team") or {}).get("teamId") == (g.get(side) or {}).get("teamId")
                and str((bg.get(f"{side}Team") or {}).get("score")) == str((g.get(side) or {}).get("score"))
                for side in ("home", "away"))
            complete = bool(actions and actions[-1].get("actionType") == "game"
                            and actions[-1].get("subType") == "end"
                            and last_action_score(actions) == (
                                str((bg.get("awayTeam") or {}).get("score")),
                                str((bg.get("homeTeam") or {}).get("score"))))
            if matches and complete:
                continue
            archive_game(gid, log, force=True, expected=g)
            attempted += 1  # bound failed attempts too; retry on the next run
            if attempted >= max_games:
                break
        verify_digest_against_boxscore(date_str, log)


def backfill_history_step(log: dict, batch: int = 6) -> None:
    """Walk backwards through the calendar a few dates per run.

    Every official date page is archived once, so the historical scoreboard grows
    by itself with no manual work. Dates with no games are recorded too, which
    stops us re-fetching them forever.
    """
    cursor_path = os.path.join(DATA, "verification", "backfill-cursor.json")
    state = load_json(cursor_path) or {}
    cursor = state.get("nextDate") or (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()
    stop_before = state.get("earliestTarget") or "1946-11-01"  # BAA/NBA first season
    fetched = 0
    while fetched < batch:
        if cursor < stop_before:
            print("[backfill] reached earliest target", flush=True)
            break
        path = os.path.join(DATA, "scoreboard", f"{cursor}.json")
        existing = load_json(path)
        if existing and (existing.get("gameCount") != 0 or not known_schedule_game_ids(cursor)):
            cursor = (dt.date.fromisoformat(cursor) - dt.timedelta(days=1)).isoformat()
            continue
        if not sync_date_digest(cursor, log, force=True):
            break  # NBA failure/mismatch: retry the same date on the next full run
        verify_digest_against_boxscore(cursor, log)
        fetched += 1
        cursor = (dt.date.fromisoformat(cursor) - dt.timedelta(days=1)).isoformat()
    state["nextDate"] = cursor
    state["earliestTarget"] = stop_before
    state["lastRunUtc"] = now_utc()
    state["note"] = ("Progressive archive cursor: the pipeline walks backwards through "
                     "the calendar, storing each official date page once.")
    write_json(cursor_path, state)
    print(f"[backfill] fetched {fetched} dates, next={cursor}", flush=True)


def prune_details(log: dict, budget_bytes: int = PBP_BUDGET_BYTES,
                  days: int = PBP_RETENTION_DAYS) -> None:
    """Keep the detail archive under a size budget, dropping the oldest first.

    Nothing is removed while the archive fits the budget — this only exists so a
    self-growing archive cannot bloat the repository. Playoffs, anything from the last
    `days` days, and every box score are never touched.
    """
    games_dir = os.path.join(DATA, "games")
    if not os.path.isdir(games_dir):
        return
    cutoff = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=days)).isoformat()
    entries, total = [], 0
    for gid in sorted(os.listdir(games_dir)):
        pbp_path = os.path.join(games_dir, gid, "playbyplay.json")
        if not os.path.exists(pbp_path):
            continue
        size = os.path.getsize(pbp_path)
        total += size
        box = load_json(os.path.join(games_dir, gid, "boxscore.json")) or {}
        game_et = ((box.get("game") or {}).get("gameEt") or "")[:10]
        entries.append((game_et, gid, pbp_path, size))
    if total <= budget_bytes:
        return
    over = total - budget_bytes
    freed = 0
    for game_et, gid, pbp_path, size in sorted(entries):  # oldest first
        if freed >= over:
            break
        if gid.startswith("004") or (game_et and game_et >= cutoff):
            continue  # playoffs and recent games stay
        os.remove(pbp_path)
        freed += size
        log["steps"].append({"step": "prunePlaybyplay", "gameId": gid, "result": "OK", "written": True,
                             "note": f"removed {size} B to stay under the {budget_bytes // (1024*1024)} MB archive budget; "
                                     f"box score kept, restore with --date … --with-games --force"})
    print(f"[prune] archive was {total // 1024} KB, freed {freed // 1024} KB", flush=True)


def refresh_stored(log: dict, max_dates: int = 40, max_games: int = 40) -> None:
    """Re-fetch already-stored files so each one carries the current `_sync` schema.

    Files written before `sourceSha256`/`sourceBytes` existed only prove the stored
    content hash; re-reading the official source is the only honest way to add the
    hash of the official bytes. It also re-verifies archived games against the live
    official files, which is exactly what the audit trail promises.
    """
    sb_dir = os.path.join(DATA, "scoreboard")
    all_dates = sorted(n[:-5] for n in os.listdir(sb_dir) if n.endswith(".json")) if os.path.isdir(sb_dir) else []
    pending_dates = [d for d in all_dates
                     if "sourceSha256" not in (load_json(os.path.join(sb_dir, f"{d}.json")) or {}).get("_sync", {})]
    for date_str in pending_dates[:max_dates]:
        sync_date_digest(date_str, log, season=None, force=True)
    print(f"[refresh] dates pending={len(pending_dates)} refreshed={min(len(pending_dates), max_dates)}", flush=True)

    # The season schedule is one big file (4.7 MB official) — re-read it only when its
    # provenance block is missing the official-bytes hash.
    sched_dir = os.path.join(DATA, "schedule")
    if os.path.isdir(sched_dir):
        stale = [n for n in sorted(os.listdir(sched_dir))
                 if n.endswith(".json")
                 and "sourceSha256" not in (load_json(os.path.join(sched_dir, n)) or {}).get("_sync", {})]
        if stale:
            print(f"[refresh] refreshing season schedule ({', '.join(stale)})", flush=True)
            sync_schedule(log)

    games_dir = os.path.join(DATA, "games")
    pending_games = []
    if os.path.isdir(games_dir):
        for gid in sorted(os.listdir(games_dir)):
            box = load_json(os.path.join(games_dir, gid, "boxscore.json")) or {}
            if "sourceSha256" not in (box.get("_sync") or {}):
                pending_games.append(gid)
    for gid in pending_games[:max_games]:
        archive_game(gid, log, force=True)
    print(f"[refresh] games pending={len(pending_games)} refreshed={min(len(pending_games), max_games)}", flush=True)


def build_index(log: dict) -> None:
    history = load_json(LOG_PATH) or {}
    heartbeat = history.get("heartbeat") or {}
    index = {
        "generatedBy": PIPELINE,
        "rules": {
            "dataOwner": "NBA — only official endpoints are used",
            "evidence": "data/verification/",
            "docs": "README.md#official-data-sources-used",
            "verification": "VERIFICATION.md",
            "note": "Every file keeps the official source URL and a sha256 of the payload it was built from.",
        },
        "live": None,
        "heartbeat": {
            "lastCheckedUtc": heartbeat.get("lastCheckedUtc"),
            "lastSuccessfulLiveUtc": heartbeat.get("lastSuccessfulLiveUtc"),
            "lastResult": heartbeat.get("lastResult"),
            "mode": heartbeat.get("mode"),
        },
        "issues": heartbeat.get("issues") or [],
        "scoreboards": {},
        "games": {},
        "schedules": {},
        "standings": {},
        "sources": {
            "liveScoreboard": LIVE_SCOREBOARD_URL,
            "seasonSchedule": SEASON_SCHEDULE_URL,
            "boxscoreTemplate": "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{gameId}.json",
            "playbyplayTemplate": "https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{gameId}.json",
            "nbaGamesPage": "https://www.nba.com/games",
        },
    }
    live = load_json(os.path.join(DATA, "live", "scoreboard.json"))
    if live:
        sb = (live.get("scoreboard") or {}).get("scoreboard") or {}
        index["live"] = {
            "path": "data/live/scoreboard.json",
            "source": live["_sync"].get("source"),
            "fetchedAtUtc": live["_sync"]["fetchedAtUtc"],
            "feedDate": sb.get("gameDate"),
            "gameCount": len(sb.get("games") or []),
            "liveCount": sum(1 for g in sb.get("games") or [] if g.get("gameStatus") == 2),
        }
    for name in sorted(os.listdir(os.path.join(DATA, "scoreboard"))) if os.path.isdir(os.path.join(DATA, "scoreboard")) else []:
        if not name.endswith(".json"):
            continue
        payload = load_json(os.path.join(DATA, "scoreboard", name))
        if not payload:
            continue
        index["scoreboards"][name[:-5]] = {
            "path": f"data/scoreboard/{name}",
            "fetchedAtUtc": payload["_sync"]["fetchedAtUtc"],
            "gameCount": payload.get("gameCount"),
            "source": payload["_sync"].get("source"),
        }
    games_dir = os.path.join(DATA, "games")
    if os.path.isdir(games_dir):
        for gid in sorted(os.listdir(games_dir)):
            box_path = os.path.join(games_dir, gid, "boxscore.json")
            box = load_json(box_path)
            if not box:
                continue
            g = box.get("game") or {}
            home, away = g.get("homeTeam") or {}, g.get("awayTeam") or {}
            index["games"][gid] = {
                "path": f"data/games/{gid}/",
                "fetchedAtUtc": box["_sync"]["fetchedAtUtc"],
                "gameEt": g.get("gameEt"),
                "status": g.get("gameStatus"),
                "away": away.get("teamTricode"), "home": home.get("teamTricode"),
                "awayScore": away.get("score"), "homeScore": home.get("score"),
                "hasPlaybyplay": os.path.exists(os.path.join(games_dir, gid, "playbyplay.json")),
                "officialBoxscore": box["_sync"]["source"],
            }
    for name in sorted(os.listdir(os.path.join(DATA, "schedule"))) if os.path.isdir(os.path.join(DATA, "schedule")) else []:
        if not name.endswith(".json"):
            continue
        payload = load_json(os.path.join(DATA, "schedule", name))
        if not payload:
            continue
        index["schedules"][name[:-5]] = {
            "path": f"data/schedule/{name}",
            "source": payload["_sync"].get("source"),
            "fetchedAtUtc": payload["_sync"]["fetchedAtUtc"],
            "gameCount": payload.get("gameCount"),
            "firstGameDate": payload.get("firstGameDate"),
            "lastGameDate": payload.get("lastGameDate"),
        }
    path = os.path.join(DATA, "index.json")
    if load_json(path) == index:
        print("[index] unchanged", flush=True)
        return
    write_json(path, index)
    print(f"[index] live={bool(index['live'])} dates={len(index['scoreboards'])} "
          f"games={len(index['games'])} seasons={len(index['schedules'])}", flush=True)


def append_log(log: dict) -> bool:
    """Store noteworthy runs and one liveness heartbeat per hour (not a commit/tick)."""
    history = load_json(LOG_PATH) or {"runs": []}
    previous = history.get("heartbeat") or {}
    summary = log.get("summary") or {}
    issues = [{k: step[k] for k in ("step", "date", "gameId", "url", "result", "error") if k in step}
              for step in log["steps"] if step.get("result") != "OK"][:20]
    meaningful = (summary.get("written") or 0) > 0 or bool(issues) or (
        previous.get("lastResult") not in (None, "OK") and not issues)
    hour = (log.get("runStartedUtc") or "")[:13]
    if not meaningful and previous.get("hour") == hour:
        return False  # successful no-op in this hour: no commit, no fictitious check time
    live_ok = any(s.get("step") == "live" and s.get("result") == "OK" for s in log["steps"])
    history["heartbeat"] = {
        "hour": hour,
        "lastCheckedUtc": log["runStartedUtc"],
        "lastSuccessfulLiveUtc": log["runStartedUtc"] if live_ok else previous.get("lastSuccessfulLiveUtc"),
        "lastMeaningfulUtc": log["runStartedUtc"] if meaningful else previous.get("lastMeaningfulUtc"),
        "lastResult": "ISSUES" if issues else "OK",
        "mode": log.get("mode"),
        "issues": issues,
        "note": "Unchanged successful checks publish at most one hourly heartbeat; failed or "
                "changed runs are stored below (last 30).",
    }
    history["_note"] = "Last 30 changed/failed runs, plus an hourly successful-check heartbeat."
    if meaningful:
        history["runs"] = [log] + (history.get("runs") or [])[:MAX_LOG_RUNS - 1]
    write_json(LOG_PATH, history)
    return True


def default_season() -> str:
    now = dt.datetime.now(dt.timezone.utc)
    year = now.year if now.month >= 9 else now.year - 1
    return f"{year}-{str((year + 1) % 100).zfill(2)}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["live", "daily", "full", "refresh"], default="live")
    ap.add_argument("--date", help="build data/scoreboard/<date>.json for this date")
    ap.add_argument("--game", help="re-verify one official NBA gameId's box score and full PBP")
    ap.add_argument("--season", default=None)
    ap.add_argument("--history-days", type=int, default=HISTORY_DAYS_DEFAULT)
    ap.add_argument("--force", action="store_true", help="rewrite even if unchanged")
    ap.add_argument("--with-games", action="store_true",
                    help="also archive box score + play-by-play for the games of --date (2019-20+)")
    args = ap.parse_args()

    season = args.season or default_season()
    log = {"runStartedUtc": now_utc(), "mode": args.mode, "season": season, "steps": []}

    if args.date and not valid_date(args.date):
        ap.error("--date must be a real YYYY-MM-DD calendar date")
    if args.game and not valid_game_id(args.game):
        ap.error("--game must be a ten-digit official NBA gameId")
    sync_live(log)  # also updates complete box/PBP for each game in progress

    if args.game:
        archive_game(args.game, log, force=True)
        recorded = load_json(os.path.join(DATA, "games", args.game, "boxscore.json")) or {}
        game_et = ((recorded.get("game") or {}).get("gameEt") or "")[:10]
        if valid_date(game_et):
            verify_digest_against_boxscore(game_et, log)

    if args.date:
        date_ok = sync_date_digest(args.date, log, season, force=True)
        if args.with_games and date_ok:
            digest = load_json(os.path.join(DATA, "scoreboard", f"{args.date}.json")) or {}
            for g in digest.get("games", []):
                if (g.get("gameStatus") == 3 and valid_game_id(g.get("gameId"))
                        and detail_era(g)):
                    archive_game(g["gameId"], log, force=args.force, expected=g)
            verify_digest_against_boxscore(args.date, log)

    if args.mode in ("daily", "full"):
        today = dt.datetime.now(dt.timezone.utc).date()
        for offset in range(1, args.history_days + 1):
            sync_date_digest((today - dt.timedelta(days=offset)).isoformat(), log, season)
        sync_archive_pending(log, args.history_days)

    if args.mode in ("full", "refresh"):
        prune_details(log)

    if args.mode == "refresh":
        refresh_stored(log)

    if args.mode == "full":
        sync_schedule(log)
        backfill_history_step(log, batch=BACKFILL_BATCH)
        # One-time backfill of this season's finished dates (bounded).
        sched = load_json(os.path.join(DATA, "schedule", f"{season}.json"))
        if sched:
            today = dt.datetime.now(dt.timezone.utc).date().isoformat()
            done = {g["gameDateEst"] for g in sched.get("games", []) if g.get("gameDateEst") and g["gameDateEst"] < today}
            pending = [d for d in sorted(done) if not os.path.exists(os.path.join(DATA, "scoreboard", f"{d}.json"))]
            for date_str in pending[-BACKFILL_MAX_DATES:]:
                sync_date_digest(date_str, log, season, force=True)

    log["runFinishedUtc"] = now_utc()
    log["summary"] = {
        "ok": sum(1 for s in log["steps"] if s.get("result") == "OK"),
        "failed": sum(1 for s in log["steps"] if s.get("result") != "OK"),
        "written": sum(1 for s in log["steps"] if s.get("written")),
    }
    logged = append_log(log)
    build_index(log)  # read the persisted heartbeat; don't manufacture a commit/tick
    print(f"[done] {log['summary']} log={logged}", flush=True)
    return 1 if log["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
