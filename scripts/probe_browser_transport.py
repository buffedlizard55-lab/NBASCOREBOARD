#!/usr/bin/env python3
"""Can a BROWSER on our own origin read official NBA JSON?

A browser always sends `Origin` on a cross-origin fetch, and cannot forge it.
It CAN, however, suppress the `Referer` (referrerPolicy="no-referrer") and choose
HTTP/2. This probe tests those exact browser-reachable shapes against cdn.nba.com.

Only measured responses are recorded.
"""
from __future__ import annotations
import datetime as dt, gzip, json, os, subprocess, urllib.error, urllib.request

PAGES = "https://buffedlizard55-lab.github.io"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
URL = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"

BASE = {
    "User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br", "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "cross-site",
    "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
    "Priority": "u=1, i",
}

def py(headers):
    rec = {}
    try:
        req = urllib.request.Request(URL, headers=headers)
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            rec = {"httpStatus": r.status, "bytes": len(body), "ok": True,
                   "acao": r.headers.get("Access-Control-Allow-Origin"),
                   "bodyPrefix": body[:100].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        b = e.read()
        rec = {"httpStatus": e.code, "bytes": len(b), "ok": False,
               "acao": (e.headers or {}).get("Access-Control-Allow-Origin"),
               "bodyPrefix": b[:100].decode("utf-8", "replace")}
    except Exception as e:
        rec = {"error": f"{type(e).__name__}: {e}"}
    return rec

def curl(args):
    cmd = ["curl", "-sS", "-o", "-", "-w", "\n__C__%{http_code}", "--max-time", "25"] + args + [URL]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
        text = out.stdout
        status = None
        if "__C__" in text:
            text, _, status = text.rpartition("__C__")
            status = int(status.strip() or 0)
        return {"httpStatus": status, "bytes": len(text), "ok": bool(status and 200 <= status < 300),
                "bodyPrefix": text[:100], "stderr": (out.stderr or "")[:120]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

tests = {
  # The exact shape a page on our origin produces with referrerPolicy:"no-referrer"
  "browser-shape-no-referer": py({**BASE, "Origin": PAGES}),
  # With the page referer a browser sends by default
  "browser-shape-with-referer": py({**BASE, "Origin": PAGES, "Referer": PAGES + "/"}),
  # Control: what nba.com's own origin gets
  "nba-origin-control": py({**BASE, "Origin": "https://www.nba.com", "Referer": "https://www.nba.com/"}),
  # HTTP/2 + suppressed referer via curl (browsers use HTTP/2)
  "curl-http2-pages-origin-no-referer": curl([
      "--http2", "--compressed", "-H", f"User-Agent: {UA}", "-H", "Accept: */*",
      "-H", f"Origin: {PAGES}", "-H", "Referer;", "-H", "Sec-Fetch-Mode: cors",
      "-H", "Sec-Fetch-Site: cross-site", "-H", "Sec-Fetch-Dest: empty"]),
  # HTTP/2 + pages origin + nba referer (referer spoofing: browsers cannot do this)
  "curl-http2-pages-origin-nba-referer": curl([
      "--http2", "--compressed", "-H", f"User-Agent: {UA}", "-H", "Accept: */*",
      "-H", f"Origin: {PAGES}", "-H", "Referer: https://www.nba.com/"]),
}
report = {"generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
          "url": URL, "pageOrigin": PAGES, "tests": tests}
os.makedirs("data/verification", exist_ok=True)
json.dump(report, open("data/verification/browser-transport-probe.json", "w"), indent=1)
for k, v in tests.items():
    print(f"{k:40} status={v.get('httpStatus')} bytes={v.get('bytes')} acao={v.get('acao')} err={v.get('error','')}{v.get('stderr','')}")
