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
  data/live/plays.json             compacted recent plays for today's games
  data/schedule/<season>.json      compacted official season schedule (incl. future)
  data/scoreboard/<date>.json      compacted scoreboard for a played date
  data/games/<gameId>/boxscore.json    compacted official box score (archived at final)
  data/games/<gameId>/playbyplay.json  compacted official play-by-play (archived at final)
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
PLAYS_PER_GAME = 80
HISTORY_DAYS_DEFAULT = 14
BACKFILL_MAX_DATES = 60
BACKFILL_BATCH = 6
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
    """GET a URL and return (payload_or_None, meta). Never raises."""
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
    meta = {"url": url, "httpStatus": None, "error": None}
    try:
        req = urllib.request.Request(url, headers=HTML_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            meta["httpStatus"] = resp.status
            meta["bytes"] = len(raw)
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


def store_compact(path: str, official_url: str, official_payload, content, note: str) -> bool:
    """Write a compacted file unless the official content behind it is unchanged."""
    content_hash = semantic_hash(official_payload)
    existing = load_json(path)
    if isinstance(existing, dict) and existing.get("_sync", {}).get("contentHash") == content_hash:
        return False
    payload = {
        "_sync": {
            "source": official_url,
            "fetchedAtUtc": now_utc(),
            "contentHash": content_hash,
            "officialBytes": len(json.dumps(official_payload, separators=(",", ":"))),
            "note": note,
            "pipeline": PIPELINE,
        },
        **content,
    }
    write_json(path, payload)
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
        "starter": player.get("starter") or player.get("position") == "G" and None,
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


def sync_live(log: dict) -> None:
    print("[live] official today's scoreboard", flush=True)
    payload, meta = http_get(LIVE_SCOREBOARD_URL, CDN_HEADERS)
    entry = {"step": "live", **meta}
    if not payload or not isinstance(payload.get("scoreboard"), dict):
        log["steps"].append({**entry, "result": "FAILED"})
        print(f"[live] FAILED status={meta.get('httpStatus')} err={meta.get('error')}", flush=True)
        return
    sb = payload["scoreboard"]
    games = sb.get("games") or []
    written = store_compact(
        os.path.join(DATA, "live", "scoreboard.json"), LIVE_SCOREBOARD_URL, payload,
        {"feedDate": sb.get("gameDate"), "gameCount": len(games),
         "liveCount": sum(1 for g in games if g.get("gameStatus") == 2),
         "scoreboard": payload},
        "unmodified official payload under `scoreboard`",
    )
    log["steps"].append({**entry, "result": "OK", "gameCount": len(games),
                         "feedDate": sb.get("gameDate"), "written": written})
    print(f"[live] OK games={len(games)} feedDate={sb.get('gameDate')} written={written}", flush=True)

    # Recent plays for games happening/recently finished today.
    plays = {"_sync": {"sources": [], "note": "compacted official play-by-play actions (last N)"},
             "games": {}}
    for g in games:
        gid = g.get("gameId")
        if g.get("gameStatus") not in (2, 3) or not gid:
            continue
        pbp, pmeta = http_get(live_playbyplay_url(gid), CDN_HEADERS)
        if not pbp or not (pbp.get("game") or {}).get("actions"):
            log["steps"].append({"step": "livePlays", "gameId": gid, **pmeta, "result": "FAILED"})
            continue
        actions = compact_actions(pbp, limit=PLAYS_PER_GAME)
        plays["games"][gid] = {
            "gameStatus": g.get("gameStatus"),
            "gameStatusText": g.get("gameStatusText"),
            "actions": actions,
            "_source": live_playbyplay_url(gid),
        }
        plays["_sync"]["sources"].append(live_playbyplay_url(gid))
        log["steps"].append({"step": "livePlays", "gameId": gid, **pmeta, "result": "OK",
                             "actionCount": len(actions)})
    if plays["games"]:
        path = os.path.join(DATA, "live", "plays.json")
        existing = load_json(path)
        new_hash = semantic_hash({k: v for k, v in plays["games"].items()})
        if not existing or existing.get("_sync", {}).get("contentHash") != new_hash:
            plays["_sync"]["contentHash"] = new_hash
            plays["_sync"]["fetchedAtUtc"] = now_utc()
            write_json(path, plays)
            print(f"[live] plays written for {len(plays['games'])} games", flush=True)


def archive_game(game_id: str, log: dict, force: bool = False) -> bool:
    """Archive official box score + play-by-play once a game is final."""
    box_path = os.path.join(DATA, "games", game_id, "boxscore.json")
    pbp_path = os.path.join(DATA, "games", game_id, "playbyplay.json")
    if not force and os.path.exists(box_path) and os.path.exists(pbp_path):
        return False
    wrote = False
    box, bmeta = http_get(live_boxscore_url(game_id), CDN_HEADERS)
    if box and isinstance(box.get("game"), dict) and box["game"].get("gameId"):
        wrote |= store_compact(
            box_path, live_boxscore_url(game_id), box,
            {"game": compact_boxscore(box),
             "gameLeaders": compact_boxscore(box).get("gameLeaders")},
            "official box score, player/team stats copied field-for-field (subset of keys)",
        )
        log["steps"].append({"step": "archiveBoxscore", "gameId": game_id, **bmeta, "result": "OK",
                             "written": wrote})
        print(f"[archive] {game_id} boxscore OK {bmeta.get('bytes')}B", flush=True)
    else:
        log["steps"].append({"step": "archiveBoxscore", "gameId": game_id, **bmeta, "result": "FAILED"})
        print(f"[archive] {game_id} boxscore FAILED status={bmeta.get('httpStatus')}", flush=True)
    pbp, pmeta = http_get(live_playbyplay_url(game_id), CDN_HEADERS)
    if pbp and (pbp.get("game") or {}).get("actions"):
        actions = compact_actions(pbp)
        wrote |= store_compact(
            pbp_path, live_playbyplay_url(game_id), pbp,
            {"gameId": game_id, "actionCount": len(actions), "actions": actions},
            "official play-by-play actions, fields copied field-for-field (subset of keys)",
        )
        log["steps"].append({"step": "archivePlaybyplay", "gameId": game_id, **pmeta, "result": "OK",
                             "actionCount": len(actions)})
        print(f"[archive] {game_id} pbp OK actions={len(actions)}", flush=True)
    else:
        log["steps"].append({"step": "archivePlaybyplay", "gameId": game_id, **pmeta, "result": "FAILED"})
        print(f"[archive] {game_id} pbp FAILED status={pmeta.get('httpStatus')}", flush=True)
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
            } if leader else None,
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
    feed = props.get("gameCardFeed") or {}
    cards = [c for mod in (feed.get("modules") or []) for c in (mod.get("cards") or [])]
    rows = [r for r in (compact_card((c or {}).get("cardData") or {}) for c in cards) if r]
    log["steps"].append({"step": "cards", "date": date_str, **meta, "result": "OK",
                         "gameCount": len(rows)})
    print(f"[cards] {date_str} cards={len(rows)}", flush=True)
    return rows, meta


def sync_date_digest(date_str: str, log: dict, season: str, force: bool = False) -> bool:
    """Archive the official scoreboard for one date (live, historical or future)."""
    path = os.path.join(DATA, "scoreboard", f"{date_str}.json")
    if not force and os.path.exists(path):
        return False
    rows, meta = cards_for_date(date_str, log)
    if rows is None:
        return False
    rows.sort(key=lambda r: (r.get("gameTimeUtc") or "", r.get("gameId") or ""))
    content = {
        "gameDate": date_str,
        "gameCount": len(rows),
        "games": rows,
        "sourcePage": games_page_url(date_str),
    }
    written = store_compact(path, games_page_url(date_str), rows, content,
                            "rows copied from the official NBA.com game cards for this date")
    log["steps"].append({"step": "digest", "date": date_str, "result": "OK",
                         "gameCount": len(rows), "written": written})
    print(f"[digest] {date_str} OK games={len(rows)} written={written}", flush=True)
    return written


def verify_digest_against_boxscore(date_str: str, log: dict) -> None:
    """Cross-check an archived date against official CDN box scores (2019-20+ only).

    Independent-source check: the card digest comes from www.nba.com HTML, the box
    score from cdn.nba.com JSON. If the final scores disagree, the run is flagged.
    """
    digest = load_json(os.path.join(DATA, "scoreboard", f"{date_str}.json"))
    if not digest:
        return
    for g in digest.get("games", []):
        gid = g.get("gameId")
        if not gid or str(gid) < "0021900001":  # CDN game files start with 2019-20
            continue
        box, meta = http_get(live_boxscore_url(gid), CDN_HEADERS)
        if not box or not isinstance(box.get("game"), dict):
            continue
        official = box["game"]
        hb = (official.get("homeTeam") or {}).get("score")
        ab = (official.get("awayTeam") or {}).get("score")
        hd = (g.get("home") or {}).get("score")
        ad = (g.get("away") or {}).get("score")
        match = (hb == hd and ab == ad)
        log["steps"].append({"step": "verifyScore", "gameId": gid, "result": "OK" if match else "MISMATCH",
                             "cards": f"{ad}-{hd}", "cdnBoxscore": f"{ab}-{hb}",
                             **{k: meta.get(k) for k in ("httpStatus", "bytes")}})
        if not match:
            print(f"[verify] MISMATCH {gid}: cards {ad}-{hd} vs boxscore {ab}-{hb} — flagged", flush=True)


def sync_schedule(log: dict) -> None:
    payload, meta = http_get(SEASON_SCHEDULE_URL, CDN_HEADERS, timeout=60)
    if not payload:
        log["steps"].append({"step": "schedule", **meta, "result": "FAILED"})
        print(f"[schedule] FAILED status={meta.get('httpStatus')}", flush=True)
        return
    compact = compact_schedule(payload)
    if not compact:
        log["steps"].append({"step": "schedule", **meta, "result": "SHAPE_UNKNOWN"})
        return
    season = compact.get("season") or "unknown"
    path = os.path.join(DATA, "schedule", f"{season}.json")
    written = store_compact(path, SEASON_SCHEDULE_URL, payload, compact,
                            "official season schedule; includes future games (verified)")
    log["steps"].append({"step": "schedule", "season": season, **meta, "result": "OK",
                         "gameCount": compact["gameCount"], "written": written})
    print(f"[schedule] OK season={season} games={compact['gameCount']} written={written}", flush=True)


def sync_archive_pending(log: dict, days: int) -> None:
    """Archive box scores + play-by-play for games that finished but are not stored."""
    today = dt.datetime.now(dt.timezone.utc).date()
    for offset in range(1, days + 1):
        date_str = (today - dt.timedelta(days=offset)).isoformat()
        digest = load_json(os.path.join(DATA, "scoreboard", f"{date_str}.json"))
        if not digest:
            continue
        for g in digest.get("games", []):
            gid = g.get("gameId")
            if not gid:
                continue
            if os.path.exists(os.path.join(DATA, "games", gid, "boxscore.json")):
                continue
            archive_game(gid, log)


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
        if os.path.exists(path):
            cursor = (dt.date.fromisoformat(cursor) - dt.timedelta(days=1)).isoformat()
            continue
        rows, meta = cards_for_date(cursor, log)
        if rows is None:
            break  # network problem: try again next run
        content = {"gameDate": cursor, "gameCount": len(rows), "games": rows,
                   "sourcePage": games_page_url(cursor)}
        store_compact(path, games_page_url(cursor), rows, content,
                      "rows copied from the official NBA.com game cards for this date")
        if rows:
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


def build_index(log: dict) -> None:
    index = {
        "generatedBy": PIPELINE,
        "rules": {
            "dataOwner": "NBA — only official endpoints are used",
            "evidence": "data/verification/",
            "docs": "README.md#verified-data-sources",
            "note": "Every file keeps the official source URL and a sha256 of the payload it was built from.",
        },
        "live": None,
        "plays": None,
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
            "fetchedAtUtc": live["_sync"]["fetchedAtUtc"],
            "feedDate": sb.get("gameDate"),
            "gameCount": len(sb.get("games") or []),
            "liveCount": sum(1 for g in sb.get("games") or [] if g.get("gameStatus") == 2),
        }
    plays = load_json(os.path.join(DATA, "live", "plays.json"))
    if plays:
        index["plays"] = {
            "path": "data/live/plays.json",
            "fetchedAtUtc": plays["_sync"].get("fetchedAtUtc"),
            "gameCount": len(plays.get("games") or {}),
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


def append_log(log: dict) -> None:
    history = load_json(LOG_PATH) or {"runs": []}
    runs = history.get("runs") or []
    runs.insert(0, log)
    history["runs"] = runs[:MAX_LOG_RUNS]
    history["_note"] = ("Machine-written record of each run: official endpoint called, "
                        "HTTP status, bytes, and how many games/actions came back.")
    write_json(LOG_PATH, history)


def default_season() -> str:
    now = dt.datetime.now(dt.timezone.utc)
    year = now.year if now.month >= 9 else now.year - 1
    return f"{year}-{str((year + 1) % 100).zfill(2)}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["live", "daily", "full"], default="live")
    ap.add_argument("--date", help="build data/scoreboard/<date>.json for this date")
    ap.add_argument("--season", default=None)
    ap.add_argument("--history-days", type=int, default=HISTORY_DAYS_DEFAULT)
    ap.add_argument("--force", action="store_true", help="rewrite even if unchanged")
    args = ap.parse_args()

    season = args.season or default_season()
    log = {"runStartedUtc": now_utc(), "mode": args.mode, "season": season, "steps": []}

    sync_live(log)
    # Archive any of today's games that just went final.
    live = load_json(os.path.join(DATA, "live", "scoreboard.json"))
    if live:
        for g in ((live.get("scoreboard") or {}).get("scoreboard") or {}).get("games") or []:
            if g.get("gameStatus") == 3 and g.get("gameId"):
                archive_game(g["gameId"], log)

    if args.date:
        sync_date_digest(args.date, log, season, force=True)

    if args.mode in ("daily", "full"):
        today = dt.datetime.now(dt.timezone.utc).date()
        for offset in range(1, args.history_days + 1):
            sync_date_digest((today - dt.timedelta(days=offset)).isoformat(), log, season)
        sync_archive_pending(log, args.history_days)

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
