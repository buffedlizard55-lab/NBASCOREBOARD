#!/usr/bin/env python3
"""
probe_s3_mirror.py — Test the official NBA S3 mirror that NBA's own payloads name.

Every cdn.nba.com live payload carries a `meta.request` field naming the object it
was generated from, e.g.
    https://nba-prod-us-east-1-mediaops-stats.s3.amazonaws.com/NBA/liveData/scoreboard/todaysScoreboard_00.json
That host belongs to the NBA, so it is in scope for this project — but only if it
answers a *browser* origin, because the whole point is a page that needs no server.

This probe asks, for each path:
    - does it return 200?
    - does it send Access-Control-Allow-Origin for https://buffedlizard55-lab.github.io ?

That single header decides whether the published scoreboard can read official NBA
data directly from the visitor's browser (best case, no proxy, no server, live).
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import urllib.error
import urllib.request

PAGES = "https://buffedlizard55-lab.github.io"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

S3 = "https://nba-prod-us-east-1-mediaops-stats.s3.amazonaws.com"


def headers(origin: str) -> dict:
    return {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nba.com/",
        "Origin": origin,
    }


def get(url: str, origin: str, timeout: int = 25) -> dict:
    rec = {"url": url, "origin": origin, "ok": False}
    try:
        req = urllib.request.Request(url, headers=headers(origin))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                try:
                    body = gzip.decompress(body)
                except Exception:
                    pass
            rec.update(
                httpStatus=resp.status,
                bytes=len(body),
                ok=200 <= resp.status < 300,
                acao=resp.headers.get("Access-Control-Allow-Origin"),
                corsHeaders={k: v for k, v in resp.headers.items() if k.lower().startswith("access-control")},
                bodyPrefix=body[:120].decode("utf-8", "replace"),
                looksLikeOfficialNbaJson=b'"meta"' in body or b'"gameId"' in body or b'"scoreboard"' in body,
            )
            try:
                parsed = json.loads(body.decode("utf-8"))
                rec["topLevelKeys"] = list(parsed.keys()) if isinstance(parsed, dict) else None
            except Exception:
                pass
    except urllib.error.HTTPError as exc:
        body = exc.read()
        rec.update(httpStatus=exc.code, bytes=len(body), error=f"HTTPError {exc.code}",
                   corsHeaders={k: v for k, v in (exc.headers or {}).items() if k.lower().startswith("access-control")},
                   bodyPrefix=body[:160].decode("utf-8", "replace"))
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def main() -> int:
    paths = [
        f"{S3}/NBA/liveData/scoreboard/todaysScoreboard_00.json",
        f"{S3}/NBA/liveData/boxscore/boxscore_0022400247.json",
        f"{S3}/NBA/liveData/playbyplay/playbyplay_0022400247.json",
        f"{S3}/NBA/games/0022400247/boxscore.json",
        f"{S3}/NBA/games/0022400247/playbyplay.json",
        f"{S3}/?list-type=2&prefix=NBA/liveData&max-keys=25",
    ]
    report = {"generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "question": "Does the NBA-owned S3 mirror answer a third-party browser origin?",
              "results": []}
    for url in paths:
        for origin in (PAGES, "https://www.nba.com"):
            rec = get(url, origin)
            report["results"].append(rec)
            print(f"{url[-70:]:70} origin={origin[-28:]:28} status={rec.get('httpStatus')} bytes={rec.get('bytes')} acao={rec.get('acao')} {rec.get('error','')}", flush=True)

    os.makedirs("data/verification", exist_ok=True)
    with open("data/verification/s3-probe.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    print("Wrote data/verification/s3-probe.json", flush=True)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as fh:
            fh.write("## NBA S3 mirror probe\n\n| path | origin | status | ACAO |\n|---|---|---|---|\n")
            for r in report["results"]:
                fh.write(f"| `{r['url'][-60:]}` | {r['origin']} | {r.get('httpStatus')} | {r.get('acao')} |\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
