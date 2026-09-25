## NBA access probe (why the block happens, what a browser can use)

### Client variations vs cdn.nba.com

| variant | status | bytes | note |
|---|---|---|---|
| `python-minimal` | 403 | 445 | HTTPError 403 |
| `python-browser-headers` | 200 | 376 | ok |
| `python-pages-origin` | 403 | 445 | HTTPError 403 |
| `python-no-referer` | 403 | 445 | HTTPError 403 |
| `curl-default` | 403 | 446 |  |
| `curl-browser-headers-http2` | 200 | 364 | ok |
| `python-boxscore-endpoint` | 200 | 41012 | ok |

### CORS relays reading the official URL

| relay | status | bytes | real NBA JSON | ACAO |
|---|---|---|---|---|
| `allorigins-raw` | 408 | 24 | None | https://buffedlizard55-lab.github.io |
| `codetabs` | 503 | 7106 | None | - |
| `corsproxy.io` | 401 | 81 | None | * |
| `cors.lol` | 429 | 20 | None | - |
| `thingproxy` | - | 0 | None | - |
| `jina-reader` | 403 | 5990 | None | - |
| `whateverorigin` | 200 | 10821 | False | * |
