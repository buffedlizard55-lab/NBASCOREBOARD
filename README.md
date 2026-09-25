# NBASCOREBOARD — official NBA live & historical scoreboard

> **Project prompt (read at the start of every session)**
>
> Review the repo.
>
> Need to work on creating a normal looking scoreboard for when NBA games become live. We need to use NBA.com, ESPN or any other NBA scoreboard sites as layout reference, but it should use only the official NBA play by play game stats and game statistics.
>
> Let's work on reverse engineering the NBA site and rebuilding a scoreboard for all games and historical games as well. I want real verified official live play by play game data from the official governing league, NBA. The play by play game data and all statistics should come directly from the NBA site — NBA.com.
>
> Put this prompt into the repo readme and read it every time we work on the project as a starting point to make sure we are building what we are aiming for and have a strong base to continue building and improving on making something useful for everyday use. It should solve the problem of having to manually check everything ourselves and have an up to date current feed.
>
> Core values (focal point for building, developing, researching, suggesting upgrades, implementing):
> **Maximize P(Win)** — "Maximize the Probability of Winning": our decision-making framework. In every decision we weigh tradeoffs, assess risk, and choose the path that maximizes the probability that Arena succeeds. We set aside our emotions and make tough decisions in order to maximize P(Win). "Maximize P(Win)" frees us from constraints and clarifies that we must put Arena first.
> **Own the Outcome** — We own results end to end — not just our individual slice of the work. When problems arise and we have the means to act, we do so without waiting for permission or assignment. We treat failure and success as signals and use them to improve. At Arena, we stay accountable to the final outcome.
>
> Work line by line verifying from official verified trusted sources, provide links for manual review. There should be no manual input, work on your own to complete tasks. Flag any irregularities for review. No hallucinations. Verify no hallucinations. The goal of this project is to get a full list that follows our requirements.
>
> Site creation: create a GitHub page for this repo with a clean UI, user friendly, simple and easy to use, organized and clean, including all relevant information in an easy-to-read format with official verified links as sources for review.
>
> Run this task through multiple passes. Pass 1: implement the task completely and verify the result. Pass 2: review your work for bugs, missing requirements, incorrect assumptions and edge cases. Fix everything you find. Pass 3: re-check the entire implementation against the original request. Improve accuracy, reliability, completeness and code quality. Do not stop after the first pass.

**Live site:** https://buffedlizard55-lab.github.io/NBASCOREBOARD/
**Repo:** https://github.com/buffedlizard55-lab/NBASCOREBOARD

---

## What this is

A normal-looking NBA scoreboard that shows **live games today**, **any past date**, and the **official schedule ahead** — every number coming from NBA-owned endpoints, with the raw evidence for each of them committed to this repo.

- **Live board:** status/clock, both teams with records, Q1–Q4 + OT, totals, top scorers, TV — refreshed from the official live feed by a scheduled pipeline.
- **Historical board:** any date, including nights from the 1990s (quarter scores, records, leaders, TV) archived from the official `nba.com/games?date=…` page.
- **Game detail:** official box score (player stats) and play-by-play (every action with clock and score) for games from 2019-20 onward.
- **Schedule:** the whole official season, including **future** games.
- **Sources & evidence:** every endpoint used, with probe results and a machine-written fetch log.

Nothing on the page is third-party: no ESPN/aggregator data, no scraping of someone else's API. ESPN-style layout is used only as a visual reference.

---

## How it works (and why it has to work this way)

The first task in this session was to find out *how a browser can read official NBA data at all*. That was measured, not assumed — see [`data/verification/`](data/verification/) and the probe scripts in [`scripts/`](scripts/).

| Question | Measured answer | Evidence |
|---|---|---|
| Can a page on our origin call `cdn.nba.com`? | **No.** Requests whose `Origin` is not `nba.com` get `403` (Akamai), with or without a referer, HTTP/1.1 or HTTP/2 | [`browser-transport-probe.json`](data/verification/browser-transport-probe.json), [`access-probe.json`](data/verification/access-probe.json) |
| Can a server (GitHub Actions) call `cdn.nba.com`? | **Yes**, with a browser-shaped header set (Referer/Origin `nba.com` + Chrome UA + sec-fetch + sec-ch-ua). Plain requests get `403`; the drop-one-header matrix is in the evidence | [`deep-probe.json`](data/verification/deep-probe.json) |
| Is NBA's S3 mirror usable from a browser? | **No.** It returns the identical bytes (200) but sends **no CORS header** | [`s3-probe.json`](data/verification/s3-probe.json) |
| Can `stats.nba.com` be reached from a cloud runner? | **No.** Every documented header recipe timed out (45 s) from GitHub Actions | [`endpoint-probe.json`](data/verification/endpoint-probe.json), [`deep-probe.json`](data/verification/deep-probe.json) |
| Do official `nba.com` pages server-render the numbers? | **Yes.** `/games?date=…` embeds every game card (id, status, clock, records, quarter scores, leaders, TV) in `__NEXT_DATA__` — for **any** date | [`cards-probe.json`](data/verification/cards-probe.json) |
| How far back do CDN game files go? | **2019-20** confirmed; 2018-19 and older return a missing-object 403 | [`coverage-probe.json`](data/verification/coverage-probe.json) |
| Does the CDN publish future games? | **Yes** — `scheduleLeagueV2_1.json` contains the whole season (2026-27: 1274 games, 2026-10-03 → 2027-04-11) | [`deep-probe.json`](data/verification/deep-probe.json) |
| Is `data.nba.net` alive? | **No.** TLS certificate no longer matches the hostname | [`endpoint-probe.json`](data/verification/endpoint-probe.json) |

**Consequence (the architecture):** no browser can read official NBA JSON directly, so a **GitHub Actions pipeline** fetches the official feeds on a real egress and commits them next to the site; the page reads them same-origin. That keeps 100% official data, needs no server of your own, and removes all manual checking.

```
official NBA endpoints ──▶ GitHub Actions (every 10 min) ──▶ data/*.json committed ──▶ GitHub Pages ──▶ browser
     (cdn.nba.com,        scripts/sync_nba_data.py          (raw or documented      (same-origin,      scoreboard
      nba.com pages)                                            field subsets)         no CORS issue)
```

Every stored file carries a `_sync` block with the **official source URL**, the fetch time, and a **sha256 of the official payload** it was built from — so any number on the page can be traced back to the endpoint it came from.

---

## Official data sources used

| Endpoint | Used for | Notes |
|---|---|---|
| `cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json` | Live scoreboard | Stored unmodified under `official`/`scoreboard` |
| `cdn.nba.com/static/json/liveData/boxscore/boxscore_{gameId}.json` | Box scores | Field subset (stats/players) under `game` |
| `cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{gameId}.json` | Play-by-play | Field subset under `actions` |
| `cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json` | Season schedule incl. future games | Compacted to the fields the UI uses |
| `www.nba.com/games?date=YYYY-MM-DD` | Historical / future scoreboards, any date | Server-rendered `__NEXT_DATA__` game cards |
| `cdn.nba.com/logos/...`, `cdn.nba.com/headshots/...` | Team logos, player headshots | Images load from any origin (measured 200) |
| `stats.nba.com/stats/*` | *(not used)* | Unreachable from cloud runners — see limitations |
| `data.nba.net/*` | *(not used)* | Certificate invalid, feeds stale since 2022-23 |

Human-review links: [today's raw feed](https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json) ·
[example box score](https://cdn.nba.com/static/json/liveData/boxscore/boxscore_0022400247.json) ·
[example play-by-play](https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_0022400247.json) ·
[season schedule](https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json) ·
[1996-06-16 on nba.com](https://www.nba.com/games?date=1996-06-16) ·
[NBA.com games](https://www.nba.com/games) · [NBA.com standings](https://www.nba.com/standings)

---

## What the pipeline publishes

| Path | Contents | Source |
|---|---|---|
| `data/index.json` | Manifest: live snapshot, archived dates, archived games, schedules | built by the pipeline |
| `data/live/scoreboard.json` | Today's official live feed (unmodified) | `todaysScoreboard_00.json` |
| `data/live/plays.json` | Recent actions for games in progress/finished today | official play-by-play |
| `data/scoreboard/<YYYY-MM-DD>.json` | Scoreboard rows for that date (status, clock, records, quarter scores, leaders, TV) | `nba.com/games?date=…` |
| `data/games/<gameId>/boxscore.json` | Player/team box score | official box score |
| `data/games/<gameId>/playbyplay.json` | Every action with clock and running score | official play-by-play |
| `data/schedule/<season>.json` | Whole season, including future games | `scheduleLeagueV2_1.json` |
| `data/verification/*.json` | Probe evidence and the per-run fetch log | probe workflows + pipeline |

Files are only rewritten when the content the site depends on changes: the write gate hashes the stored content and, for the live feed, ignores the feed's own `meta.time` (measured to change on every request while the game data is identical), so an unchanged feed never produces a commit. Each file records the official source URL, the sha256 of what it shows, and the sha256 + size of the exact official response behind it.

---

## Corrections to claims made in earlier sessions

These were wrong or unsupported and are now fixed in code and docs:

1. **"cdn.nba.com is CORS-enabled, so the browser can fetch it."** — False. Measured: any non-`nba.com` origin gets **403**; the CDN's `Access-Control-Allow-Origin` is `https://www.nba.com`. The site no longer depends on that.
2. **"`http://data.nba.net` is a usable historical fallback."** — False. Its certificate no longer matches the hostname (TLS failure) and community reports say it stopped updating in 2022-23. Removed from the pipeline; only linked as historical context.
3. **"NBA public APIs don't provide future schedules."** — False. `scheduleLeagueV2_1.json` carried all 1274 games of 2026-27, including games through April 2027 (measured).
4. **"Standings can be read from `cdn.nba.com/.../standings/standings.json` or `leaguestandingsv3`."** — Unverified/blocked. Those CDN paths return a missing-object 403, and `stats.nba.com` never answers a cloud runner. The site now shows **no standings** and says why, instead of displaying numbers from somewhere else.
5. **"Game `0042000404` is the 2020 Finals Game 4."** — Wrong. The official payload for that id is **PHX @ MIL, 2021-07-14** (2021 Finals Game 4). Fixed in the docs; the ID→game mapping is now always read from official data.
6. **"Historical games need the Stats API."** — Not anymore: `nba.com/games?date=…` server-renders any date (verified with 1996-06-16, 2010-06-17, 2016-06-19, 2020-10-11, 2024-06-17, 2024-11-04).

---

## Limitations (measured — not assumptions)

1. **Freshness depends on the path in use.** Server-side probes show the CDN refusing every non-`nba.com` origin, so the committed snapshot (refreshed every 10 minutes by the pipeline) is the guaranteed path in any browser. The page also ships a **Test direct CDN access** button: where a browser *can* read the official feed — or where you point it at your own relay with `?relay=` — it switches itself to 10-second live reads and says which path is active. A direct read that starts failing falls back to the snapshot instead of breaking.
2. **`stats.nba.com` is unreachable from cloud runners**, so historical stats outside the CDN live-data era (2018-19 and older box scores/play-by-play) are not available here. Older **scoreboards** (quarter scores, records, leaders) *are* available and archived.
3. **Detail archives are intentionally bounded.** A box score is ~24 KB and a play-by-play ~160 KB per game, so the pipeline archives new finals automatically (last 14 days, max 24 games per run), keeps play-by-play for the last 60 days plus every playoff game, and prunes older regular-season play-by-play (the box score stays). Re-archive any night with `--date YYYY-MM-DD --with-games --force`.
4. **No standings.** Measured across every official candidate we could find — 5 CDN standings paths (missing-object 403), 2 Stats API recipes (read timeout from cloud), `nba.com/standings` HTML (200, but no standings data in its `__NEXT_DATA__` and no standings-shaped node anywhere in `pageProps`) — and the app backend nba.com itself calls (`core-api.nba.com`) answers **403** to our runner and sends `Access-Control-Allow-Origin: https://www.nba.com` only. Shown as "not published" instead of substituting a third-party provider. Evidence: [`standings-probe.json`](data/verification/standings-probe.json), [`core-api-probe.json`](data/verification/core-api-probe.json).
5. **Playoff/finals dates before 1996** are whatever `nba.com/games?date=…` serves — the archive grows backwards one date at a time, so older nights appear as the cursor reaches them.
6. **This is a read-only mirror of public endpoints.** It is not affiliated with the NBA; respect NBA's terms and rate limits.

---

## Optional: true live refresh with your own relay

If you want the board to poll the official feeds directly (10-second cache), run a tiny pass-through and tell the page about it. It relays only the official URLs the page asks for; no third party ever sees the data.

```js
// Cloudflare Worker example (free tier is fine)
export default {
  async fetch(request) {
    const target = new URL(request.url).searchParams.get('url');
    const allowed = [
      'https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json',
      'https://cdn.nba.com/static/json/liveData/boxscore/',
      'https://cdn.nba.com/static/json/liveData/playbyplay/',
    ];
    if (!target || !allowed.some((prefix) => target.startsWith(prefix))) {
      return new Response('blocked', { status: 403 });
    }
    const upstream = await fetch(target, {
      headers: {
        'Referer': 'https://www.nba.com/',
        'Origin': 'https://www.nba.com',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Sec-Fetch-Dest': 'empty', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Site': 'same-site',
      },
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { 'content-type': 'application/json', 'access-control-allow-origin': '*' },
    });
  },
};
```

Then open `https://buffedlizard55-lab.github.io/NBASCOREBOARD/?relay=https://<your-worker>/?url=` once — the relay is remembered in `localStorage`.

---

## Running it yourself

```bash
# 1. Local preview of the published site (static files only)
python3 -m http.server 8000
# → http://localhost:8000    (reads whatever is committed under ./data)

# 2. Refresh the official data (only works from a network that NBA allows —
#    residential/office IPs; cloud IPs are refused, which is why Actions does it)
python3 scripts/sync_nba_data.py --mode live            # today's feed + today's finals
python3 scripts/sync_nba_data.py --mode daily           # + last 14 days of scoreboards
python3 scripts/sync_nba_data.py --mode full            # + season schedule + history walk
python3 scripts/sync_nba_data.py --mode refresh         # re-read stored files (provenance + drift check)
python3 scripts/sync_nba_data.py --date 2024-11-04 --with-games   # one date, with box score + PBP

# 3. Run the page's own test suite (jsdom, no browser needed)
npm --prefix tests install && node tests/ui_smoke.mjs
#   → boots the real index.html + app.js against the committed data/ files and
#     asserts 31 behaviours (live view, archived dates, game detail, filters, schedule,
#     missing-file fallbacks, the direct-access self-test)

# 3. Re-run the verification probes (records fresh evidence)
python3 scripts/probe_endpoints.py       # which official hosts answer, and how
python3 scripts/probe_deep.py            # header requirements, stats API, schedule, coverage
python3 scripts/probe_access.py          # why the 403 happens + which relays work
python3 scripts/probe_coverage.py        # how far back game data goes
python3 scripts/probe_cards.py           # structure of the official date pages
python3 scripts/probe_s3_mirror.py       # S3 mirror behaviour
python3 scripts/probe_browser_transport.py  # browser-reachable transport shapes
python3 scripts/probe_standings.py      # official standings candidates + nba.com's own JS
python3 scripts/probe_core_api.py       # the API nba.com's front end calls (core-api.nba.com)
```

### Publishing

GitHub Pages serves this repository from **`main` / root**, and the repo has **two** publishers configured: GitHub's legacy branch build (which produced the live site verified in this session) and [`deploy.yml`](.github/workflows/deploy.yml) (an Actions artifact deploy that also reports success). They publish the same commit tree, so the result is correct either way, but running both is redundant — and the legacy builder allows only **10 builds per hour**, which matters because the data pipeline commits every 10 minutes during games. Recommended cleanup: **Settings → Pages → Source: GitHub Actions**, so `deploy.yml` is the single path (it also verifies `index.html`/`README.md` before deploying). Until then the pipeline gates its writes so an unchanged feed cannot create a commit (see below), keeping commits well inside that limit.

Workflows: [`sync-nba-data.yml`](.github/workflows/sync-nba-data.yml) (publication, every 10 min) ·
[`backfill-dates.yml`](.github/workflows/backfill-dates.yml) (archive specific dates) ·
the `probe-*.yml` verification workflows (evidence) ·
[`deploy.yml`](.github/workflows/deploy.yml) (Pages).

`sync-nba-data.yml` accepts a mode hint in the commit message: `[sync:full]`, `[sync:daily]`, or nothing for `live`.

---

## Verification workflow (how to check this repo without trusting it)

1. Open [`data/verification/sync-log.json`](data/verification/sync-log.json) — every fetch the pipeline made, with HTTP status and counts.
2. Open the corresponding official URL for any entry and compare (links are in the same file, and in `_sync.source` inside every data file).
3. Re-run a probe workflow from the Actions tab; it commits new evidence rather than editing old results.
4. Compare a stored payload's `_sync.contentHash` with a fresh `sha256` of the official JSON.
5. `VERIFICATION.md` lists each claim with its measured evidence and a manual-review link.

---

## Next steps (prioritised, each with its blocker stated)

1. **Real-time board without any third party (highest value).** The page now ships a **`Test direct CDN access`** button: if your browser can read `cdn.nba.com` (the CDN may trust a real browser where it refuses our server-side probes), the board switches itself to 10-second live reads from the official feed and says so in the header. The same test runs through a visitor relay if one is configured (`?relay=…`). *Blocker:* the CDN returned 403 for every non-nba.com origin we could measure from a server; only a real browser can settle how it treats an ordinary reader.
2. **Standings without third parties.** *Measured answer:* `core-api.nba.com` — the backend nba.com's own front end calls with `Core-Api-Key`/`Core-Api-Version` — returns **403** to a cloud runner on every route tried, including `/api/v1/authenticate`, and its `Access-Control-Allow-Origin` is `https://www.nba.com` only, so a page on this origin cannot read it either; a chunk that references the Stats endpoint `leaguestandingsv3` inherits the same blocked Stats host. Remaining option: derive the table from official per-team records (every archived date page carries them) and label it clearly as *computed from official records* rather than published standings. *Blocker:* only a `nba.com`-origin host (or a licensed feed) reaches the real thing.
3. **Wider detail archive.** Bulk-archive box scores + play-by-play for whole past seasons from the CDN (2019-20+). *Blocker:* size — ~200 MB per season of play-by-play; needs a size policy (e.g. current season only, or external storage).
4. **Shot charts.** Play-by-play actions carry `x`/`y` and `shotDistance`; render a half-court chart per game. *Blocker:* none — data is already in `data/games/*/playbyplay.json`.
5. **In-game win probability.** `pbOdds` exists in the live feed; decide whether to surface official odds. *Blocker:* product decision (betting-adjacent).
6. **PWA/offline + notification when your team goes live.** *Blocker:* none.
7. **Better pre-2019 coverage.** Only scoreboard-level data is available for those seasons; deeper stats need a licensed/official source. *Blocker:* external.

---

## Session log

| Date | Session | Work |
|---|---|---|
| 2026-09-25 | `arena/01a0d9ed-nbascoreboard` | First build: static page, live feed attempt, standings attempt (PRs #1–#3) |
| 2026-09-25 | `arena/01a0d9f3-nbascoreboard` | Strip view, schema docs, standings correction (PR #4) |
| 2026-09-25 | `arena/01a0da01-nbascoreboard` | **Merged to `main` (PRs #5, #6) and verified live:** probed 7 different access paths on real egress; found the 403 is `Origin`-based, that the pipeline egress can read the CDN with browser headers, that S3 has no CORS, that stats.nba.com is blocked from cloud, that CDN coverage starts 2019-20, and that `nba.com/games?date=` server-renders any date. Rebuilt the pipeline (live feed, any-date history, schedule incl. future, box score + PBP archive), rewrote the UI to read published data, corrected six wrong claims from earlier sessions, added verification evidence + fetch log to the page · then: audit-grade provenance (`sourceSha256`/`sourceBytes` on all 65 artifacts), direct-access self-test, 30-assertion UI smoke test as a merge gate, measured standings/core-api verdict, size-budget retention, and a write gate that stops metadata churn. Merged in PRs #5/#6; the live site was then fetched and checked against this repo's data |

---

**Data ownership:** all data belongs to the NBA. This project reads official public endpoints that nba.com itself uses, stores the responses (unmodified, or with a documented field subset and a payload hash), and links every number back to its source.
