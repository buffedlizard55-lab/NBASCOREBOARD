# NBA Scoreboard — official-source scores, box scores & play-by-play

> **Start every work session by reading this README, beginning with the project charter.** Do not substitute a third-party statistic, a guessed result, or a plausible-looking zero for missing NBA data. Verify and cite the NBA source; flag anything that cannot be verified.

## Project charter (the user’s request; broken NBA.com link formatting normalized)

> Review the repo.
>
> Need to work on creating a normal looking scoreboard for when NBA games become live. We need to use [NBA.com](https://www.nba.com), ESPN or any other NBA scoreboard sites, but it should use only the official NBA play by play game stats and game statistics.
>
> Let's work on reverse engineering the NBA site and rebuilding a scoreboard for all games and historical games as well. I want real verified official live play by play game data from the official governing league, NBA. The play by play game data and all statistics should come directly from the NBA site. [NBA.com](https://www.nba.com)
>
> Put this prompt into the repo readme and read it everytime we work on the project as a starting point to make sure we are building what we are aiming for and have a strong base to continue building and improving on making something useful for everyday use. It should solve the problem of having to manually check everything ourselves and having an up to date current feed.
>
> Review the repo.
>
> The following is taken from the Arena AI team and I think it makes a good point on building a successful project, so let's keep the Core Values and Own the Outcome as a focal point when building, developing, researching, suggesting upgrades, and implementing the work.
>
> **Our Core Values**
>
> **Maximize P(Win)**
>
> “Maximize the Probability of Winning”: our decision making framework. In every decision, we weigh tradeoffs, assess risk, and choose the path that maximizes the probability that Arena succeeds. We set aside our emotions and make tough decisions in order to maximize P(Win). “Maximize P(Win)” frees us from constraints and clarifies that we must put Arena first.
>
> **Own the Outcome**
>
> We own results end to end — not just our individual slice of the work. When problems arise and we have the means to act, we do so without waiting for permission or assignment. We treat failure and success as signals and use them to improve. At Arena, we stay accountable to the final outcome.
>
> Work line by line verifying from official verified trusted sources, provide links for manual review. There should be no manual input, work on your own to complete tasks. Flag any irregularities for review. No hallucinations.
>
> Verify no hallucinations.
>
> The goal of this project is to get a full list that follow our requirements. No hallucinations. Verify line by line.
>
> **Site creation**
>
> Create a github page for this repo that has clean ui, user friendly, simple and easy to use.
>
> It should be organized and clean. It should include all relevant information in an easy to read format with official verified links as sources for review. Work line by line verify everything no hallucinations.
>
> Go ahead and create a pull request and then merge the pull request onto the main. Make suggestions for what work still needs to be done and any limitations that is in the way of a successful project. It should be worked on in this next session or the next session. Work line by line verify everything no hallucinations.
>
> Run this task through multiple passes.
>
> Pass 1: Implement the task completely and verify the result.
>
> Pass 2: Review your work for bugs, missing requirements, incorrect assumptions, and edge cases. Fix everything you find.
>
> Pass 3: Re-check the entire implementation against the original request. Improve accuracy, reliability, completeness, and code quality. Fix any remaining issues.
>
> Do not stop after the first pass. Each pass must build on the previous one. Before finishing, verify that the final result fully satisfies the original request. Work line by line verify everything no hallucinations.

**Site:** https://buffedlizard55-lab.github.io/NBASCOREBOARD/ · **Source code:** https://github.com/buffedlizard55-lab/NBASCOREBOARD

## Reality check — what is and is not built

This is an **independent, read-only viewer** of NBA-owned data. The GitHub Pages site already exists. It is a clean scores-first page with quarter lines, date navigation, team search, box scores, *complete* play-by-play for games whose detail is published, schedule, sources and capture/health indicators. No ESPN or other aggregator supplies statistics. Source links are on the page and in every stored `_sync` block.

**Do not call a GitHub Pages snapshot “real-time.”** The static site cannot read the NBA CDN from its origin in the measured configuration ([browser access evidence](data/verification/browser-transport-probe.json)). A GitHub Actions job samples the official feeds and publishes JSON to Pages. The requested interval is five minutes (GitHub’s minimum scheduled interval); **GitHub can delay/skip jobs and Pages publication adds latency**. As of the 2026-09-25 review, the workflow had **no `schedule`-triggered runs** yet—existing runs had been triggered by pushes. A first real live-game night has **not** been observed end-to-end. The site displays both the snapshot capture time and last *confirmed successful* read, warns about a stale/failed feed, and never labels a stale score as an unqualified live result. There is an optional direct browser-to-**NBA CDN only** test in Sources; there is **no arbitrary relay** that could inject unverified data.

**Historical coverage is incomplete.** The Pages archive contains only dates actually fetched and recorded in [`data/index.json`](data/index.json). On 2026-09-25 this checkout contained 29 date files (including empty off-season dates), 19 game-detail archives and one 2026–27 season schedule. The pipeline backfills a few days per full pass and rechecks unfinished dates; a date that is not archived is explicitly *unknown here*, not “no games.” An NBA.com date-page link is provided. The archived [1996 Finals date](https://www.nba.com/games?date=1996-06-16) has official scores but **not** verified CDN player/PBP detail. CDN box/PBP files have only been sampled successfully from the 2019–20 era onward; that is **not** a promise of every game’s availability. Older regular-season PBP is subject to a repository size/retention policy. All-history PBP needs more official coverage and storage than this static repo provides.

## Official sources: click through to verify

| What the UI shows | Official source / human review | Published copy |
|---|---|---|
| Game status, teams, quarter and total scores | [NBA CDN scoreboard](https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json) · [NBA.com games](https://www.nba.com/games) | [`data/live/scoreboard.json`](data/live/scoreboard.json) |
| Player and team box score (while in progress and at final, if the NBA file is valid) | `https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{gameId}.json` · [MIL @ CLE example](https://www.nba.com/game/mil-vs-cle-0022400154/box-score) | [`data/games/0022400154/boxscore.json`](data/games/0022400154/boxscore.json) |
| Every action, clock and running score (if the NBA file is valid) | `https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{gameId}.json` · [MIL @ CLE example](https://www.nba.com/game/mil-vs-cle-0022400154/play-by-play) | [`data/games/0022400154/playbyplay.json`](data/games/0022400154/playbyplay.json) |
| Past-date scoreboard rows | [NBA.com games for 2024-11-04](https://www.nba.com/games?date=2024-11-04) · [1996 Finals example](https://www.nba.com/games?date=1996-06-16) (`__NEXT_DATA__` cards) | [`2024-11-04`](data/scoreboard/2024-11-04.json) · [`1996-06-16`](data/scoreboard/1996-06-16.json) |
| Future/current season matchups and tip-offs | [NBA CDN league schedule](https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json) | [`data/schedule/2026-27.json`](data/schedule/2026-27.json) |

Raw CDN links may return a 403 in an ordinary browser outside `nba.com`; the human NBA.com links are provided for independent review. We do **not** use `stats.nba.com` (timed out from prior cloud probes), `data.nba.net` (certificate mismatch in prior probes), ESPN stats, unofficial feeds, or made-up standings. See [`VERIFICATION.md`](VERIFICATION.md) for the **dated** network evidence and what it does *not* prove.

## How data gets from NBA.com to Pages

```
NBA.com date cards + NBA CDN scoreboard/box/PBP/schedule
    → scripts/sync_nba_data.py on GitHub Actions (server-side)
    → validated, source-stamped files in data/
    → GitHub Pages → browser reads same-origin JSON
```

- `.github/workflows/sync-nba-data.yml`: asks GitHub for five-minute polls and one full backfill/schedule pass at 06:00 UTC; dispatch is also possible. These are **requested**, not guaranteed, intervals. It publishes error evidence and marks the run failed if an NBA read/schema/score cross-check fails. Only a validated feed can replace the previous snapshot.
- In-progress games: the job reads **all** official play actions and the current official box score into `data/games/<id>/` on each successful live poll. At Final it replaces provisional detail, retries missing detail on recent dates, and cross-checks final box/PBP scores and team identities against NBA.com date cards when both exist. No live game was underway when this change was authored; mocked live transitions are tests, not proof of live network behavior.
- Historical dates: reads `https://www.nba.com/games?date=YYYY-MM-DD`; rejects missing/malformed cards, a conflicting server-selected date, or a card whose ET date differs from the request. A zero-card archive means that the **returned NBA.com feed contained zero cards at capture time**, not a permanent guarantee about the date. If a published NBA season schedule lists games on that date, an empty response is flagged and not treated as proof of no games; an earlier cached empty response is rechecked. Unfinished rows are re-fetched until final, and a final NBA.com card is compared with the NBA CDN box **before** archiving it.
- `data/index.json` lists **published** artifacts, counts, and last recorded confirmed read; `data/verification/sync-log.json` retains the last 30 changed/failed runs plus at most one unchanged-success heartbeat per hour. It is **not a full log of every no-op tick**. Any reported irregularity is listed under `issues` in the index and shown on the site.
- `_sync.source` is an NBA URL; `_sync.fetchedAtUtc` is when the *displayed content* changed. `_sync.sourceSha256`/`sourceBytes` fingerprint the fetched NBA response; `_sync.contentHash` fingerprints the stored fields (on the live feed it excludes volatile `meta.time`). These hashes provide traceability, **not** independent proof that NBA published the bytes. Compare against an official fresh fetch to verify that claim.
- Detail retention: PBP older than 60 days and not a playoff game may be pruned once the archive exceeds its **40 MiB target**. That target cannot always be enforced when protected files alone exceed it. Box scores remain. A missing file is labelled unavailable, never filled with third-party data.

## Run and verify locally

```bash
python3 -m http.server 8000 --bind 0.0.0.0  # local static preview: uses checked-in data/
python3 -m unittest discover -s tests -p 'test_*.py' -v  # mocked feed transitions and failure cases
python3 tests/verify_data.py             # all published files: provenance, stored hashes, counts, score joins
npm --prefix tests ci && npm --prefix tests test  # jsdom UI: real archived NBA files + synthetic live scenario
python3 scripts/sync_nba_data.py --mode full    # real NBA fetch, only where network access permits
```

For a date not in the manifest, the human-readable [NBA.com games date picker](https://www.nba.com/games) is the source until automation has archived it. The [backfill workflow](.github/workflows/backfill-dates.yml) also accepts an explicit list; it records failures instead of treating a failed fetch as a successful empty scoreboard. Python tests are offline and do not modify checked-in `data/`. The `Verify` CI gate runs both suites and the full integrity scan; `deploy.yml` re-checks data and publishes **only** site files (not `.git` or scripts).

## Limitations & next work (own the outcome)

1. **Prove live behavior at a real game, or change the hosting path.** Before calling this real-time, observe a scheduled run, CDN reads for an in-progress NBA game, complete current box/PBP, Pages publication, and the UI stale indicator end-to-end. As of the review, no scheduled run or live game was observed. If GitHub’s best-effort cron remains inactive, the honest solution is a hosted poller/proxy that can read NBA-owned feeds (or a licensed NBA feed), **not** a misleading “live” badge on old static JSON. This is the top priority next session.
2. **Historical completeness and retention.** Backfill by official game days/seasons more efficiently, keep track of exactly which game dates and details were checked, and budget storage for full historical PBP. Some pre-2019 NBA detail is absent on the CDN paths tested. Do not promise a full all-time list until the official source and storage are verified.
3. **Single Pages publisher.** This repository was configured for legacy branch Pages builds *and* an Actions Pages deployment; the two can race and waste build capacity. If the maintainer has Pages admin permissions, choose **Settings → Pages → Build and deployment → GitHub Actions** to let `deploy.yml` be the sole publisher. Verify configuration via the GitHub Pages API after changing it.
4. **Re-check official schema and policy.** NBA page internals and CDN access rules can change. The parser fails closed and logs irregularities, but should be re-probed with independent NBA.com links and respect NBA data/usage terms. Restore detail only from verified NBA sources; do not infer missing standings or scores.

## Three-pass audit for this change (2026-09-25)

- **Pass 1 — functionality:** a scores-first responsive GitHub Pages UI; official date/schedule fallback; full live-game box/PBP ingestion (instead of after-final/last-80-only), validations, per-game official links and capture times. No live game was available to verify on this date.
- **Pass 2 — failure and edge cases:** re-fetch unfinished UTC-yesterday scores, retry incomplete PBP archives, reject wrong game IDs/final score mismatches, never turn malformed JSON/unarchived dates into “no games,” close stale async game responses, and drop untrusted user-defined relay support. Added regression tests for these cases.
- **Pass 3 — evidence and publication:** full stored-file integrity verifier, honest freshness heartbeat and workflow failure visibility, Pages deploy data gate, documented scheduler/era/retention gaps. Independent live-night confirmation and all-history coverage remain **open**; the implementation does not claim to satisfy those unverified parts of the charter.

**Data ownership:** all statistics belong to the NBA. This independent site is not affiliated with the league.
