#!/usr/bin/env python3
"""
nba_official.py — the only places this project may fetch from.

Everything here is NBA-owned and every entry is backed by a measured response in
data/verification/. Nothing else is allowed to appear in the data pipeline.

Access facts measured on 2026-09-25 (see data/verification/*.json):

* cdn.nba.com JSON feeds answer HTTP 200 **only** to requests that look like a real
  browser tab on nba.com (Referer + Origin https://www.nba.com, Chrome UA, sec-fetch,
  sec-ch-ua, Priority). Requests carrying any *other* Origin are answered 403 by
  Akamai — that includes this project's own GitHub Pages origin, so a browser on our
  page can never read the CDN directly (measured: "browser-shape-no-referer" -> 403).
* The same objects are mirrored on NBA's own S3 bucket
  (nba-prod-us-east-1-mediaops-stats.s3.amazonaws.com, named inside every payload's
  meta.request). It returns 200 for the identical bytes but sends no CORS header, so
  it is a server-side path only. (measured: data/verification/s3-probe.json)
* stats.nba.com never answers a GitHub-hosted runner (all header recipes timed out,
  45s) — so standings/historical stats must come from other official sources.
* www.nba.com HTML pages DO answer and server-render JSON in `__NEXT_DATA__`
  (measured: data/verification/coverage-probe.json).

Therefore: the GitHub Actions pipeline is the data path; the published site reads its
own static files. That is the only design that keeps 100% official data with no
third-party aggregator and no manual work.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- hosts
CDN_HOST = "https://cdn.nba.com"
S3_MIRROR_HOST = "https://nba-prod-us-east-1-mediaops-stats.s3.amazonaws.com"
STATS_HOST = "https://stats.nba.com"
WWW_HOST = "https://www.nba.com"
LEGACY_HOST = "https://data.nba.net"  # NBA-run but DEPRECATED (see DEPRECATED below)

ALLOWED_HOSTS = (CDN_HOST, S3_MIRROR_HOST, STATS_HOST, WWW_HOST, LEGACY_HOST)

SITE_ORIGIN = "https://buffedlizard55-lab.github.io"

# The exact header set that produced HTTP 200 from a GitHub-hosted runner
# (data/verification/access-probe.json -> variant "python-browser-headers", and
# data/verification/deep-probe.json -> "full-browser-set").
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",  # no brotli: urllib cannot decode br
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

# www.nba.com navigations look different from XHR calls.
HTML_HEADERS = {
    "User-Agent": BROWSER_HEADERS["User-Agent"],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "sec-ch-ua": BROWSER_HEADERS["sec-ch-ua"],
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

CDN_HEADERS = BROWSER_HEADERS
STATS_HEADERS = {
    **BROWSER_HEADERS,
    "Referer": "https://www.nba.com/",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}


def team_logo_url(team_id) -> str:
    return f"{CDN_HOST}/logos/nba/{team_id}/primary/L/logo.svg"


def player_headshot_url(player_id) -> str:
    return f"{CDN_HOST}/headshots/nba/latest/260x190/{player_id}.png"


# ------------------------------------------------------------------ official URLs
LIVE_SCOREBOARD_URL = f"{CDN_HOST}/static/json/liveData/scoreboard/todaysScoreboard_00.json"
LIVE_ODDS_URL = f"{CDN_HOST}/static/json/liveData/odds/odds_todaysGames.json"
LIVE_CHANNELS_URL = f"{CDN_HOST}/static/json/liveData/channels/v2/channels_00.json"
SEASON_SCHEDULE_URL = f"{CDN_HOST}/static/json/staticData/scheduleLeagueV2_1.json"


def live_boxscore_url(game_id: str) -> str:
    return f"{CDN_HOST}/static/json/liveData/boxscore/boxscore_{game_id}.json"


def live_playbyplay_url(game_id: str) -> str:
    return f"{CDN_HOST}/static/json/liveData/playbyplay/playbyplay_{game_id}.json"


def s3_boxscore_url(game_id: str) -> str:
    return f"{S3_MIRROR_HOST}/NBA/liveData/boxscore/boxscore_{game_id}.json"


def s3_playbyplay_url(game_id: str) -> str:
    return f"{S3_MIRROR_HOST}/NBA/liveData/playbyplay/playbyplay_{game_id}.json"


def stats_scoreboard_v3_url(date_str: str, league_id: str = "00") -> str:
    return f"{STATS_HOST}/stats/scoreboardv3?GameDate={date_str}&LeagueID={league_id}"


def stats_standings_url(season: str, season_type: str = "Regular Season") -> str:
    return (
        f"{STATS_HOST}/stats/leaguestandingsv3?LeagueID=00&Season={season}"
        f"&SeasonType={season_type.replace(' ', '+')}"
    )


def stats_schedule_url(season: str, league_id: str = "00") -> str:
    return f"{STATS_HOST}/stats/scheduleleaguev2?LeagueID={league_id}&Season={season}"


# Official human-facing pages (linked from the UI, and used as a data source only
# where a probe has proven the page server-renders the numbers).
def games_page_url(date_str: str | None = None) -> str:
    return f"{WWW_HOST}/games" + (f"?date={date_str}" if date_str else "")


def schedule_page_url(season: str) -> str:
    return f"{WWW_HOST}/schedule?season={season}"


NBA_STANDINGS_PAGE = f"{WWW_HOST}/standings"
NBA_GAMES_PAGE = f"{WWW_HOST}/games"


def game_page_url(game_code: str) -> str:
    return f"{WWW_HOST}/game/{game_code}"


# ------------------------------------------------------------------- deprecations
DEPRECATED = {
    "data.nba.net": (
        "NBA-run legacy host whose TLS certificate no longer matches the hostname "
        "(measured CERTIFICATE_VERIFY_FAILED, data/verification/endpoint-probe.json) "
        "and whose feeds stopped updating after 2022-23. Never used."
    ),
}
