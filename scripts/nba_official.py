#!/usr/bin/env python3
"""
nba_official.py — The single source of truth for every official NBA endpoint this
project is allowed to touch.

Rules enforced here:
  * Only NBA-owned hosts: cdn.nba.com, stats.nba.com, www.nba.com, data.nba.net
    (data.nba.net is listed because it is NBA-run, but it is marked DEPRECATED and
    is not used for scoreboard data).
  * Every entry carries a `reference` field: the public link a human can open to
    double-check the endpoint. If a human cannot open it, it does not belong here.
  * No third-party aggregators (ESPN, sportsdata, balldontlie, ...) anywhere.

The live probes in scripts/probe_endpoints.py record what these URLs actually
return; data/verification/endpoint-probe.json is the evidence file.
"""

from __future__ import annotations

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Origin of the published site — the header NBA's CDN expects from browser traffic.
SITE_ORIGIN = "https://buffedlizard55-lab.github.io"

CDN_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip",
    "Referer": "https://www.nba.com/",
    "Origin": SITE_ORIGIN,
}

# stats.nba.com is stricter: it expects the stats-site referer/token headers.
STATS_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}

# Official games page for human verification / deep links.
NBA_GAMES_PAGE = "https://www.nba.com/games"
NBA_STANDINGS_PAGE = "https://www.nba.com/standings"
NBA_GAME_PAGE = "https://www.nba.com/game/{gameCode}"


def games_page_for_date(date_str: str) -> str:
    return f"{NBA_GAMES_PAGE}?date={date_str}"


# ---------------------------------------------------------------- live CDN (JSON)
LIVE_SCOREBOARD_URL = (
    "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
)
LIVE_ODDS_URL = "https://cdn.nba.com/static/json/liveData/odds/odds_todaysGames.json"
LIVE_CHANNELS_URL = "https://cdn.nba.com/static/json/liveData/channels/v2/channels_00.json"


def boxscore_url(game_id: str) -> str:
    return f"https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"


def playbyplay_url(game_id: str) -> str:
    return f"https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{game_id}.json"


def team_logo_url(team_id) -> str:
    return f"https://cdn.nba.com/logos/nba/{team_id}/primary/L/logo.svg"


def player_headshot_url(player_id) -> str:
    return f"https://cdn.nba.com/headshots/nba/latest/260x190/{player_id}.png"


# --------------------------------------------------------------- stats.nba.com API
STATS_BASE = "https://stats.nba.com/stats"


def scoreboard_v3_url(date_str: str, league_id: str = "00") -> str:
    return f"{STATS_BASE}/scoreboardv3?GameDate={date_str}&LeagueID={league_id}"


def scoreboard_v2_url(date_str: str, league_id: str = "00") -> str:
    return (
        f"{STATS_BASE}/scoreboardV2?GameDate={date_str}&LeagueID={league_id}&DayOffset=0"
    )


def league_standings_v3_url(season: str, season_type: str = "Regular Season",
                            league_id: str = "00") -> str:
    return (
        f"{STATS_BASE}/leaguestandingsv3?LeagueID={league_id}"
        f"&Season={season}&SeasonType={season_type.replace(' ', '+')}"
    )


def play_by_play_v3_url(game_id: str) -> str:
    return f"{STATS_BASE}/playbyplayv3?GameID={game_id}&StartPeriod=0&EndPeriod=14"


def play_by_play_v2_url(game_id: str) -> str:
    return f"{STATS_BASE}/playbyplayv2?GameID={game_id}&StartPeriod=0&EndPeriod=14"


def box_score_traditional_v3_url(game_id: str) -> str:
    return (
        f"{STATS_BASE}/boxscoretraditionalv3?GameID={game_id}&StartPeriod=0&EndPeriod=14"
        "&StartRange=0&EndRange=2147483647&RangeType=0"
    )


def schedule_league_v2_url(season: str, league_id: str = "00") -> str:
    return f"{STATS_BASE}/scheduleleaguev2?LeagueID={league_id}&Season={season}"


def schedule_candidates(season: str | None = None):
    """Official schedule sources, best first. The pipeline keeps the first one that
    returns a shapes it recognises (see sync_nba_data.py)."""
    out = [
        ("cdn-static-schedule-v2-1", "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"),
        ("cdn-static-schedule-v2", "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"),
    ]
    if season:
        year = season.split("-")[0]
        out.append(
            ("stats-scheduleleaguev2", schedule_league_v2_url(season))
        )
        out.append(
            (
                "cdn-static-schedule-season",
                f"https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_{year}.json",
            )
        )
    return out


# ------------------------------------------------------------------- deprecations
DEPRECATED = {
    "data.nba.net": (
        "NBA-run legacy host. Community reports it stopped updating after the 2022-23 "
        "season. Kept only for historical verification; never used for live scores."
    ),
    "cdn.nba.com/static/json/liveData/scoreboard/scoreboard_<date>.json": (
        "Unverified date-based CDN path. Probed by scripts/probe_endpoints.py; only "
        "used if the probe records HTTP 200."
    ),
}
