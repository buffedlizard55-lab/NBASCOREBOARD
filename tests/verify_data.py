#!/usr/bin/env python3
"""Offline integrity check of every published NBA artifact and its manifest.

This checks internal consistency and NBA URL provenance. A sourceSha256 is a
fingerprint, not proof of an NBA response: compare it to a fresh official fetch
when network access is available. Never claim this script verifies NBA's servers.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from sync_nba_data import live_gate_payload, semantic_hash, valid_date, valid_game_id, last_action_score  # noqa: E402

DATA = ROOT / "data"
errors = []
checked = 0


def require(condition, message):
    if not condition:
        errors.append(message)


def read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"{path.relative_to(ROOT)}: cannot parse JSON: {exc}")
        return None


def source_for(path, payload=None):
    relative = path.relative_to(DATA)
    if relative == pathlib.Path("live/scoreboard.json"):
        return "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
    if relative.parts[0] == "scoreboard" and len(relative.parts) == 2:
        date = relative.stem
        if valid_date(date):
            return f"https://www.nba.com/games?date={date}"
    if relative.parts[0] == "schedule" and len(relative.parts) == 2:
        return "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"
    if relative.parts[0] == "calendar" and len(relative.parts) == 2 and re.fullmatch(r"\d{4}", relative.stem):
        # The year map is embedded in a particular NBA.com date page, not a
        # separate endpoint. Its exact captured source date must match this year.
        source = (payload or {}).get("_sync", {}).get("source", "")
        match = re.fullmatch(r"https://www\.nba\.com/games\?date=(\d{4}-\d{2}-\d{2})", source)
        if match and valid_date(match.group(1)) and match.group(1).startswith(relative.stem):
            return source
    if relative.parts[0] == "games" and len(relative.parts) == 3:
        _, game_id, filename = relative.parts
        if valid_game_id(game_id) and filename in ("boxscore.json", "playbyplay.json"):
            kind = filename[:-5]
            return f"https://cdn.nba.com/static/json/liveData/{kind}/{kind}_{game_id}.json"
    return None


index = read(DATA / "index.json") or {}
for path in sorted(DATA.glob("**/*.json")):
    if path == DATA / "index.json" or path.parts[-2] == "verification":
        continue
    payload = read(path)
    if not isinstance(payload, dict):
        continue
    checked += 1
    name = str(path.relative_to(ROOT))
    expected_source = source_for(path, payload)
    sync = payload.get("_sync") or {}
    require(expected_source is not None, f"{name}: unknown published path")
    require(sync.get("source") == expected_source, f"{name}: source is not the official endpoint for this file")
    require(bool(re.fullmatch(r"[0-9a-f]{64}", sync.get("sourceSha256") or "")), f"{name}: invalid official response hash")
    require(bool(re.fullmatch(r"[0-9a-f]{64}", sync.get("contentHash") or "")), f"{name}: invalid content hash")
    require(isinstance(sync.get("sourceBytes"), int) and sync["sourceBytes"] > 0, f"{name}: missing official response size")
    try:
        ts = dt.datetime.fromisoformat(sync.get("fetchedAtUtc", ""))
        require(ts.tzinfo is not None, f"{name}: fetch time is not UTC-aware")
    except ValueError:
        require(False, f"{name}: invalid fetch time")
    content = {k: v for k, v in payload.items() if k != "_sync"}
    gate = live_gate_payload(content["scoreboard"]) if path == DATA / "live/scoreboard.json" else content
    require(semantic_hash(gate) == sync.get("contentHash"), f"{name}: stored numbers differ from contentHash")

    if path == DATA / "live/scoreboard.json":
        sb = (payload.get("scoreboard") or {}).get("scoreboard") or {}
        games = sb.get("games")
        require(isinstance(games, list), f"{name}: missing game list")
        if isinstance(games, list):
            require(payload.get("gameCount") == len(games), f"{name}: wrong gameCount")
            require(payload.get("liveCount") == sum(g.get("gameStatus") == 2 for g in games), f"{name}: wrong liveCount")
            require(len({g.get("gameId") for g in games}) == len(games), f"{name}: duplicate game IDs")
        require(payload.get("feedDate") == sb.get("gameDate") and valid_date(sb.get("gameDate")), f"{name}: bad feed date")
        require(index.get("live", {}).get("feedDate") == sb.get("gameDate"), f"{name}: manifest feed date differs")
    elif path.parent.name == "scoreboard":
        games = payload.get("games") or []
        require(payload.get("gameDate") == path.stem, f"{name}: wrong date")
        require(payload.get("gameCount") == len(games), f"{name}: wrong gameCount")
        require(len({g.get("gameId") for g in games}) == len(games), f"{name}: duplicate game IDs")
        require(index.get("scoreboards", {}).get(path.stem, {}).get("gameCount") == len(games), f"{name}: manifest count differs")
        for g in games:
            gid = g.get("gameId")
            require(valid_game_id(gid), f"{name}: invalid gameId {gid}")
            require(str(g.get("gameTimeEastern") or "").startswith(path.stem), f"{name}: game {gid} ET date differs")
            box_path = DATA / "games" / str(gid) / "boxscore.json"
            if g.get("gameStatus") != 3 or not box_path.is_file():
                continue
            box = read(box_path) or {}
            bg = box.get("game") or {}
            if bg.get("gameStatus") != 3:
                continue
            require(bg.get("gameId") == gid, f"{name}: game {gid} box ID differs")
            for side in ("home", "away"):
                team, card = bg.get(f"{side}Team") or {}, g.get(side) or {}
                require(team.get("teamId") == card.get("teamId") and str(team.get("score")) == str(card.get("score")),
                        f"{name}: game {gid} {side} score/ID differs from box")
    elif path.parent.name == "calendar":
        counts = payload.get("dateCounts")
        require(payload.get("year") == path.stem, f"{name}: wrong calendar year")
        require(isinstance(counts, dict) and bool(counts), f"{name}: missing year date counts")
        if isinstance(counts, dict):
            for date, count in counts.items():
                require(valid_date(date) and date.startswith(path.stem) and type(count) is int and count >= 0,
                        f"{name}: invalid date/count for {date}")
            require(payload.get("knownGameDates") == sum(type(c) is int and c > 0 for c in counts.values()),
                    f"{name}: wrong knownGameDates")
            require(payload.get("explicitNoGameDates") == sum(type(c) is int and c == 0 for c in counts.values()),
                    f"{name}: wrong explicitNoGameDates")
            for date, count in counts.items():
                archive_path = DATA / "scoreboard" / f"{date}.json"
                if archive_path.exists() and date < dt.datetime.now(dt.timezone.utc).date().isoformat():
                    digest = read(archive_path) or {}
                    require(digest.get("gameCount") == count,
                            f"{name}: NBA calendar says {count} games on {date}, archive says {digest.get('gameCount')}")
        entry = index.get("calendars", {}).get(path.stem, {})
        require(entry.get("source") == expected_source and entry.get("path") == f"data/calendar/{path.name}"
                and entry.get("knownDates") == len(counts or {}) and entry.get("gameDates") == payload.get("knownGameDates")
                and entry.get("noGameDates") == payload.get("explicitNoGameDates"),
                f"{name}: manifest calendar counts or source differ")
    elif path.parent.name == "schedule":
        games = payload.get("games") or []
        require(payload.get("season") == path.stem, f"{name}: wrong season")
        require(payload.get("gameCount") == len(games), f"{name}: wrong gameCount")
        require(len({g.get("gameId") for g in games}) == len(games), f"{name}: duplicate game IDs")
        require(index.get("schedules", {}).get(path.stem, {}).get("gameCount") == len(games), f"{name}: manifest count differs")
    elif path.name == "boxscore.json":
        require((payload.get("game") or {}).get("gameId") == path.parent.name, f"{name}: wrong game ID")
        require(path.parent.name in index.get("games", {}), f"{name}: missing from manifest")
    elif path.name == "playbyplay.json":
        actions = payload.get("actions") or []
        require(payload.get("gameId") == path.parent.name, f"{name}: wrong game ID")
        require(payload.get("actionCount") == len(actions) and len(actions) > 0, f"{name}: truncated or empty actions")
        box = read(path.with_name("boxscore.json")) or {}
        bg = box.get("game") or {}
        if bg.get("gameStatus") == 3 and actions:
            expected = (str((bg.get("awayTeam") or {}).get("score")), str((bg.get("homeTeam") or {}).get("score")))
            require(last_action_score(actions) == expected, f"{name}: final PBP score differs from box")
            require((actions[-1].get("actionType"), actions[-1].get("subType")) == ("game", "end"),
                    f"{name}: final PBP lacks NBA Game End action")

# The manifest must not advertise missing artifacts; existence is needed for a
# date-picker/box-score link to be usable on a static GitHub Pages deployment.
for group, suffix in (("scoreboards", ".json"), ("calendars", ".json"), ("schedules", ".json")):
    for key, entry in index.get(group, {}).items():
        folder = {"scoreboards": "scoreboard", "calendars": "calendar", "schedules": "schedule"}[group]
        expected_path = f"data/{folder}/{key}{suffix}"
        require(entry.get("path") == expected_path and (ROOT / expected_path).is_file(),
                f"index.json: {group}.{key} refers to a missing or incorrect file")
for game_id, entry in index.get("games", {}).items():
    expected_dir = f"data/games/{game_id}/"
    require(valid_game_id(game_id) and entry.get("path") == expected_dir
            and (ROOT / expected_dir / "boxscore.json").is_file()
            and entry.get("hasPlaybyplay") == (ROOT / expected_dir / "playbyplay.json").is_file(),
            f"index.json: game {game_id} path or play-by-play availability differs from files")

print(f"Checked {checked} NBA data files against hashes, paths, counts, and cross-source scores")
for error in errors:
    print(f"IRREGULARITY: {error}")
if errors:
    print(f"{len(errors)} irregularities — publication blocked")
    sys.exit(1)
print("No internal integrity irregularities (upstream availability/accuracy requires independent NBA review)")
