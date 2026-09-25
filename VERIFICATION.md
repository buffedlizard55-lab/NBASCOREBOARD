# VERIFICATION — every claim, and how to check it by hand

This file is the audit trail for the project. **Rule: no number on the site may exist without a source URL recorded here or inside the data file's `_sync` block.** If a claim could not be verified, it is listed as a limitation, not shown as data.

Machine-readable evidence lives in [`data/verification/`](../data/verification/); each entry below names the file and the manual-review link.

---

## 1. Access: can we read official NBA JSON, and from where?

| # | Claim | Result | Evidence | Check it yourself |
|---|---|---|---|---|
| 1.1 | A normal browser page cannot fetch `cdn.nba.com` | **Confirmed blocked.** Non-`nba.com` `Origin` → `403` + Akamai page; no CORS header for our origin (CDN sends `Access-Control-Allow-Origin: https://www.nba.com`) | [`browser-transport-probe.json`](../data/verification/browser-transport-probe.json), [`access-probe.json`](../data/verification/access-probe.json) | `curl -sD- -H 'Origin: https://example.com' https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json` (from a residential IP) |
| 1.2 | A server can, with a browser-shaped header set | **Confirmed 200** from GitHub Actions: `Referer`/`Origin: https://www.nba.com` + Chrome `User-Agent` + `sec-fetch-*`/`sec-ch-ua`. Removing any single header still worked; a minimal UA+Referer+Origin set did **not** | [`deep-probe.json`](../data/verification/deep-probe.json) → `cdnHeaderSensitivity` | same curl with the full header set in `scripts/nba_official.py` |
| 1.3 | Public CORS relays can proxy it | **No.** 6 of 7 relays failed (5xx, 401, 429, DNS, non-NBA body) | [`access-probe.json`](../data/verification/access-probe.json) → `usableRelays` | re-run `scripts/probe_access.py` |
| 1.4 | NBA's S3 mirror works around CORS | **No.** Identical bytes with `200` and *no* CORS header → unreadable by a browser | [`s3-probe.json`](../data/verification/s3-probe.json) | `curl -sI https://nba-data.storage.googleapis.com/...` |
| 1.5 | `stats.nba.com` is reachable from a cloud runner | **No.** Every documented recipe timed out (~45 s) | [`deep-probe.json`](../data/verification/deep-probe.json) → `statsRecipes`, [`endpoint-probe.json`](../data/verification/endpoint-probe.json) | re-run the probe workflow |
| 1.6 | `data.nba.net` is a usable fallback | **No.** TLS certificate doesn't match the hostname; community reports say feeds stopped in 2022-23 | [`endpoint-probe.json`](../data/verification/endpoint-probe.json) | `curl -sv https://data.nba.net/prod/v1/today.json` |
| 1.7 | `www.nba.com` pages server-render the numbers | **Yes.** `/games?date=…` embeds game cards in `__NEXT_DATA__` for **any** date (verified back to 1996) | [`cards-probe.json`](../data/verification/cards-probe.json) | open `view-source:https://www.nba.com/games?date=1996-06-16` and search `gameCardFeed` |
| 1.8 | Team logos / headshots are browser-loadable | **Yes**, plain `200` images, no CORS needed | [`endpoint-probe.json`](../data/verification/endpoint-probe.json) | open a logo URL in a tab |

**Conclusion:** the only working path is *server-side fetch with browser headers → commit compact official JSON → serve same-origin from Pages*. That is exactly what `scripts/sync_nba_data.py` and `.github/workflows/sync-nba-data.yml` do.

---

## 2. Coverage: how far back, and how fresh?

| # | Claim | Result | Evidence |
|---|---|---|---|
| 2.1 | CDN box score + play-by-play coverage | **2019-20 → present** (confirmed for `0021900001`, `0022200879`, `0022301170`, `0022400196`, `0022400247`, `0042000404`); 2018-19 and earlier → missing-object `403` | [`coverage-probe.json`](../data/verification/coverage-probe.json), [`deep-probe.json`](../data/verification/deep-probe.json) → `cdnCoverage` |
| 2.2 | Historical scoreboards before 2019-20 | **Available for any date** via the official date page (quarter scores, records, leaders, TV); verified for 1996-06-16, 2003-06-15, 2010-06-17, 2016-06-19, 2020-10-11, 2024-06-17, 2024-11-04 | [`cards-probe.json`](../data/verification/cards-probe.json) + archived `data/scoreboard/*.json` |
| 2.3 | Live feed cache/freshness | `cache-control: max-age=10` with etag/last-modified; the pipeline samples it every 10 minutes | [`endpoint-probe.json`](../data/verification/endpoint-probe.json) |
| 2.4 | Schedule includes future games | **Yes** — 1274 games for 2026-27 with dates through 2027-04-11, read by the pipeline | [`deep-probe.json`](../data/verification/deep-probe.json) → `scheduleFiles`; `data/schedule/2026-27.json` |
| 2.5 | Standings | **Unavailable from the paths tried.** 5 CDN standings paths → missing-object `403`; both Stats API recipes → read timeout; `nba.com/standings` HTML → 200 but no standings data in `__NEXT_DATA__` and no standings-shaped node anywhere in `pageProps` | [`standings-probe.json`](../data/verification/standings-probe.json) |
| 2.7 | What nba.com's own front end calls | Its `_app` bundle references **`core-api.nba.com`** with `Core-Api-Key` / `Core-Api-Version` headers and `/api/v1/...` routes, and one chunk references the Stats endpoint `leaguestandingsv3`. **Measured:** `core-api.nba.com` returns `403` to a cloud runner on `/`, `/api/v1/authenticate?requestor_id=nba`, `/api/v1/checkauthn/` and `/api/v1/capi-preview/` (and to OPTIONS preflight), always with `Access-Control-Allow-Origin: https://www.nba.com` — so neither this site's origin nor a cloud runner can read it | [`core-api-probe.json`](../data/verification/core-api-probe.json), [`standings-probe.json`](../data/verification/standings-probe.json) → `endpointHints` |
| 2.6 | Older seasons' schedules | Not published on the CDN (only the current one). Future/deeper seasons need the Stats API or the date pages | [`deep-probe.json`](../data/verification/deep-probe.json) |

---

## 3. Data integrity rules enforced by the pipeline

1. **`_sync` provenance on every file:** `{source, fetchedAtUtc, contentHash, sourceSha256, sourceBytes, note, pipeline}`.
   `contentHash` is a sha256 of the **stored content** (so "unchanged" means nothing the site shows moved) and `sourceSha256`/`sourceBytes` are the sha256 and size of the **exact official response** the file was built from. Both together make every number reproducible: fetch `source`, hash it, compare.
   Every one of the 67 stored artifacts carries this block (verified by `--mode refresh`, run 2026-09-25: 97 fetches, 0 failures, 49 files rewritten onto the current schema).
2. **Hash-gated writes:** a file is only rewritten when its stored content changes → no churn commits, and `fetchedAtUtc` means "the moment this content was captured".
3. **No synthesis:** the pipeline never adds numbers that are not in the official payload. Derived values (e.g. a percentage formatted for display) are computed in the browser from official fields only.
4. **Cross-check on final:** when a game reaches "Final", the digest is re-read and the stored row is verified against the official box score (scores, team ids) — mismatches are written to `sync-log.json` as `MISMATCH` instead of being silently kept.
5. **Append-only evidence:** probe workflows add timestamped result files; they never edit previous results.
6. **Failure visibility:** every fetch attempt, its HTTP status, and the byte count land in [`sync-log.json`](../data/verification/sync-log.json), including failures.
7. **No churn:** the live feed is gated on game data only — the feed's own `meta.time` (which changes on every request even when nothing else does) cannot create a commit, keeping the pipeline inside GitHub Pages' build-rate limit.

---

## 4. Corrections (things previously stated that were wrong)

| Old claim | Reality | Where fixed |
|---|---|---|
| "official CDN is CORS-enabled for any origin" | Non-`nba.com` origins are refused (403) | README §Corrections, data client rewrite |
| "`data.nba.net` is the fallback" | Certificate invalid; feeds stale since 2022-23 | README, pipeline (removed) |
| "No public NBA endpoint publishes future games" | `scheduleLeagueV2_1.json` publishes the whole season | README, schedule feature |
| "Standings from `leaguestandingsv3`" | Unreachable from cloud; CDN standings absent | README limitations, UI shows "not published" |
| "`0042000404` = 2020 Finals Game 4" | It is PHX @ MIL, 2021-07-14 (2021 Finals Game 4) | corrected here and in README |
| "Historical detail needs the Stats API" | `nba.com/games?date=` covers any date's scoreboard | README, new pipeline |

---

## 5. Manual review checklist (5 minutes)

- [ ] Open the [live site](https://buffedlizard55-lab.github.io/NBASCOREBOARD/) — the status bar shows the snapshot's capture time and its source URL.
- [ ] Open [`data/live/scoreboard.json`](https://github.com/buffedlizard55-lab/NBASCOREBOARD/blob/main/data/live/scoreboard.json) — compare `_sync.source` with the official [today's scoreboard](https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json).
- [ ] Open any archived date file, e.g. [`data/scoreboard/1996-06-16.json`](https://github.com/buffedlizard55-lab/NBASCOREBOARD/blob/main/data/scoreboard/1996-06-16.json) — compare with [nba.com/games?date=1996-06-16](https://www.nba.com/games?date=1996-06-16).
- [ ] Open [`data/games/0022400154/boxscore.json`](https://github.com/buffedlizard55-lab/NBASCOREBOARD/blob/main/data/games/0022400154/boxscore.json) — compare with the official [box score](https://cdn.nba.com/static/json/liveData/boxscore/boxscore_0022400154.json).
- [ ] Read [`sync-log.json`](https://github.com/buffedlizard55-lab/NBASCOREBOARD/blob/main/data/verification/sync-log.json) — every status is `ok`/`unchanged`/`failed`, nothing hidden.
- [ ] Confirm the page's **Sources** section lists every endpoint it reads, with links.

---

## 6. Known irregularities flagged for review

1. **`stats.nba.com` blocks cloud IP ranges** (Akamai) while `cdn.nba.com` answers the same runner with a browser header set. The Stats API is a cornerstone of the community ecosystem, so this asymmetry is worth re-probing occasionally — the pipeline records a fresh result whenever it runs.
2. **Historical coverage cliff at 2019-20.** Games before that return a `403` (*missing object*, not *forbidden*) for game files, while the date pages happily show 1996. This looks like a data-retention policy, not a permissions problem.
3. **The CDN sends no CORS header on its S3 mirror** (nba-data.storage.googleapis.com) even though it serves the bytes publicly; only `cdn.nba.com` sets (restrictive) CORS. Practically, that makes the mirror useless for browser apps.
4. **`Origin`-based blocking on `cdn.nba.com`** rejects `https://buffedlizard55-lab.github.io` with a 403 page, but the *same request* with `Origin: https://www.nba.com` returns 200. If the CDN ever trusts the `Origin` header alone for anything sensitive, that would matter; for public JSON it only affects who can read it.
5. **`data.nba.net`'s certificate mismatch** is a stale-configuration signal; the host may be decommissioned.

---

## 7. How to reproduce everything from scratch

```bash
python3 scripts/probe_endpoints.py     # → data/verification/endpoint-probe.json
python3 scripts/probe_deep.py          # → deep-probe.json (headers, stats, schedule, coverage)
python3 scripts/probe_access.py        # → access-probe.json (403 mechanics, relays)
python3 scripts/probe_s3_mirror.py     # → s3-probe.json
python3 scripts/probe_coverage.py      # → coverage-probe.json
python3 scripts/probe_cards.py         # → cards-probe.json (date pages)
python3 scripts/probe_browser_transport.py  # → browser-transport-probe.json
python3 scripts/probe_pages.py         # → pages-probe.json (nba.com page payloads)
python3 scripts/sync_nba_data.py --mode daily   # → data/live, data/scoreboard, sync-log.json
```
