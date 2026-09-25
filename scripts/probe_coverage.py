#!/usr/bin/env python3
"""Coverage + official-page probe.

1. How far back does cdn.nba.com game data actually go? Measured with game ids that
   are either community-documented or format-derived (00X+YY+5 digits). A 200 whose
   payload contains a real `game` object OFFICIALLY verifies that game exists.
2. Does www.nba.com server-render scoreboard/standings JSON into its HTML pages?
   (that would be an official, runner-reachable source for standings)
"""
from __future__ import annotations
import datetime as dt, gzip, json, os, re, urllib.error, urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
H = {
    "User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br", "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com", "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-site",
    "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"', "Priority": "u=1, i",
}

def get(url, headers=H, timeout=30):
    rec = {"url": url, "ok": False}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                try: body = gzip.decompress(body)
                except Exception: pass
            rec.update(httpStatus=r.status, bytes=len(body), ok=200 <= r.status < 300)
            ct = (r.headers.get("Content-Type") or "")
            rec["contentType"] = ct
            if "json" in ct or body[:1] in (b"{", b"["):
                try: rec["json"] = json.loads(body.decode("utf-8"))
                except Exception: pass
            else:
                rec["body"] = body.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        b = e.read()
        rec.update(httpStatus=e.code, bytes=len(b), error=f"HTTPError {e.code}",
                   bodyPrefix=b[:120].decode("utf-8", "replace"))
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        rec["httpStatus"] = None
    return rec

IDS = [
    "0022400247", "0022400196", "0022301170", "0022200879", "0042000404",
    "0021900001", "0021800001", "0021700001", "0021600001", "0021500001",
    "0021400001", "0021300001", "0021200001",
]
out = {"generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(), "coverage": [], "officialPages": []}
print("== CDN historical coverage (boxscore + playbyplay) ==", flush=True)
for gid in IDS:
    row = {"gameId": gid}
    for kind in ("boxscore", "playbyplay"):
        rec = get(f"https://cdn.nba.com/static/json/liveData/{kind}/{kind}_{gid}.json")
        info = {"kind": kind, "httpStatus": rec.get("httpStatus"), "bytes": rec.get("bytes"),
                "error": rec.get("error")}
        payload = rec.get("json") or {}
        game = payload.get("game") if isinstance(payload, dict) else None
        if isinstance(game, dict):
            info["verifiedGame"] = True
            info["gameEt"] = game.get("gameEt") or game.get("gameTimeUTC")
            info["gameStatus"] = game.get("gameStatus")
            info["home"] = (game.get("homeTeam") or {}).get("teamTricode")
            info["away"] = (game.get("awayTeam") or {}).get("teamTricode")
            if kind == "boxscore":
                ht = game.get("homeTeam") or {}
                info["hasPeriods"] = bool(ht.get("periods"))
                info["homeTeamKeys"] = list(ht.keys())
                if isinstance(ht.get("periods"), list) and ht["periods"]:
                    info["firstPeriod"] = ht["periods"][0]
                info["gameKeys"] = list(game.keys())[:30]
        row[kind] = info
        print(f"  {gid} {kind:10} status={info['httpStatus']} bytes={info['bytes']} game={info.get('verifiedGame')} {info.get('away')}@{info.get('home')} et={info.get('gameEt')}", flush=True)
    out["coverage"].append(row)

print("== Official www.nba.com pages: is JSON server-rendered? ==", flush=True)
for label, url in [("standings", "https://www.nba.com/standings"),
                   ("games", "https://www.nba.com/games")]:
    rec = get(url, {**H, "Accept": "text/html,application/xhtml+xml"}, timeout=35)
    html = rec.pop("body", "") or ""
    info = {"label": label, "url": url, "httpStatus": rec.get("httpStatus"), "bytes": rec.get("bytes")}
    nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if nd:
        info["hasNextData"] = True
        try:
            payload = json.loads(nd.group(1))
            info["nextDataKeys"] = list(payload.keys())
            props = payload.get("props", {}).get("pageProps", {})
            info["pagePropsKeys"] = list(props.keys())[:30] if isinstance(props, dict) else None
            for k in ("standings", "scoreboard", "games", "data", "initialState"):
                if isinstance(props, dict) and k in props:
                    v = props[k]
                    info[f"pageProps.{k}"] = list(v.keys())[:20] if isinstance(v, dict) else type(v).__name__
            info["nextDataBytes"] = len(nd.group(1))
        except Exception as e:
            info["nextDataError"] = str(e)
    else:
        info["hasNextData"] = False
        info["htmlPrefix"] = html[:200]
    out["officialPages"].append(info)
    print(f"  {label:10} status={info['httpStatus']} bytes={info['bytes']} nextData={info.get('hasNextData')} props={info.get('pagePropsKeys')}", flush=True)

os.makedirs("data/verification", exist_ok=True)
json.dump(out, open("data/verification/coverage-probe.json", "w"), indent=1)
print("Wrote data/verification/coverage-probe.json", flush=True)
step = os.environ.get("GITHUB_STEP_SUMMARY")
if step:
    with open(step, "a", encoding="utf-8") as fh:
        fh.write("## Coverage probe\n\n| gameId | boxscore | pbp | verified game | date |\n|---|---|---|---|---|\n")
        for r in out["coverage"]:
            b, p = r["boxscore"], r["playbyplay"]
            fh.write(f"| {r['gameId']} | {b['httpStatus']} {b['bytes']}B | {p['httpStatus']} {p['bytes']}B | {b.get('verifiedGame')} | {b.get('gameEt')} {b.get('away')}@{b.get('home')} |\n")
        for i in out["officialPages"]:
            fh.write(f"| page {i['label']} | {i['httpStatus']} nextData={i.get('hasNextData')} props={i.get('pagePropsKeys')} |\n")
