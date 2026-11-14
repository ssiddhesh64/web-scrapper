# web-scrapper

An experimental, bot-detection-aware web scraper aimed at **Reddit**, built on
[Camoufox](https://camoufox.com), a stealth Firefox driven through Playwright.

It's built step by step ([docs/PLAN.md](docs/PLAN.md)). Current state:

- **Steps 1–7 done**:
  - launch config and a browser factory
  - a resilient fetcher (block detection, safe retries, JS-challenge handling)
  - human-like browsing and per-site throttling (`ScrapeSession`)
  - a Reddit extractor that collects subreddit posts with infinite-scroll pagination
  - SQLite/JSONL storage with incremental re-runs and a persistent daily page budget
  - a `web-scrapper` command line
- **Scraper Lab**: a local web UI for running and comparing scrape experiments.
- **Next**: Step 8 (scale: proxies, identities, concurrency).

> Decisions, folder map, every default and the internals are documented in
> **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**. The Camoufox API cheat sheet is in
> [docs/camoufox_reference.md](docs/camoufox_reference.md).

---

## Requirements

- Developed and tested on **macOS**, where a visible browser window is the default. Camoufox also
  runs on Linux (use `SCRAPER_HEADLESS=virtual` with Xvfb on servers) and Windows, but those are
  untested here.
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.14 for you).
- Disk space for Camoufox's browser and GeoIP data, which is cached outside the repo
  (`uv run camoufox path`; about 2.5 GB on the dev machine).

## Quick start

```bash
uv sync                      # 1. install dependencies (Python 3.14, via uv)
uv run camoufox fetch        # 2. download the Camoufox browser binary (once)

uv run web-scrapper reddit puzzles --limit 30     # 3. collect posts -> data/scraper.db
uv run web-scrapper db posts --by score           #    look at what was stored

uv run web-scrapper lab      # or open the Scraper Lab UI -> http://127.0.0.1:8765
uv run pytest                # tests (no browser, no network)
```

A browser window opens while scraping. That's intentional: headless browsers are easier to detect.

---

## Command line: `web-scrapper`

```
uv run web-scrapper [-v] <command> [options]
```

| Command | What it does |
|---|---|
| `reddit <subreddit>` | Collect posts from a subreddit and store them (SQLite + JSONL) |
| `fetch <url>` | Load one page and report status, block type, JS challenge and timing |
| `db summary` | Stored counts per subreddit, recent runs, today's page budget |
| `db posts` | List stored posts |
| `db export -o FILE` | Export stored records to `.csv` or `.jsonl` |
| `lab` | Start the Scraper Lab UI (same as `uv run scraper-lab`) |

Global flag: `-v` / `--verbose` logs every fetch, retry, wait and block. It goes **before** the
command: `uv run web-scrapper -v reddit puzzles`.

### `reddit`: collect a subreddit

```bash
uv run web-scrapper reddit puzzles                          # 50 newest posts (default)
uv run web-scrapper reddit r/puzzles --limit 100            # "r/" prefix is fine
uv run web-scrapper reddit AskReddit --sort top --time week --limit 25
uv run web-scrapper reddit puzzles --json > posts.jsonl     # records to stdout, summary to stderr
uv run web-scrapper reddit puzzles --json | jq -r '.title'  # pipe into other tools
```

| Option | Default | Meaning |
|---|---|---|
| `--sort` | `new` | `new`, `hot`, `top`, `rising`, `best`, `controversial` |
| `--time` | – | `hour`, `day`, `week`, `month`, `year`, `all` (only with `--sort top`/`controversial`) |
| `--limit` | `50` | Maximum posts to collect |
| `--db` | `data/scraper.db` | SQLite file |
| `--jsonl` / `--no-jsonl` | `data/jsonl` | Also append to `data/jsonl/reddit_post.jsonl` (or don't) |
| `--no-incremental` | off | By default `--sort new` stops when it reaches posts already stored |
| `--max-pages-per-day` | `400` | Daily page budget per site (counted across runs) |
| `--json` | off | Print records as JSON lines on stdout |

**Incremental runs:** with `--sort new`, a re-run stops as soon as it reaches posts that are
already stored ("caught up"), so it only fetches what's new. Run it as often as you like; it
stays cheap.

### `fetch`: diagnose a single page

```bash
uv run web-scrapper fetch https://www.reddit.com/r/puzzles/ --wait-selector shreddit-post
uv run web-scrapper fetch https://example.com --no-profile --save-html output/page.html
```

| Option | Default | Meaning |
|---|---|---|
| `--wait-selector` | – | CSS selector to wait for (otherwise: network idle) |
| `--retries` | `1` | Retries for 429 / 5xx / network errors (never for 403 / captcha / login walls) |
| `--save-html` | – | Write the page HTML to this file |

It prints the status, final URL, title, block type, attempts, JS-challenge time, signals and
warnings, plus where any failure artifacts were saved (`diagnostics/failures/`).

### `db`: inspect and export

```bash
uv run web-scrapper db summary
uv run web-scrapper db posts --subreddit puzzles --by comments --limit 10
uv run web-scrapper db export -o exports/posts.csv          # or .jsonl; --kind reddit_post
uv run web-scrapper db --db other.db summary                # --db goes before the subcommand
```

| Subcommand | Options |
|---|---|
| `summary` | – |
| `posts` | `--subreddit NAME`, `--by score\|comments\|new\|seen` (default `score`), `--limit N` (default 20) |
| `export` | `-o FILE` (required, `.csv` or `.jsonl`), `--kind` (default `reddit_post`) |

You can also query the database directly. The `reddit_posts` view has normal columns:

```bash
sqlite3 -header -column data/scraper.db \
  "select score, comment_count, title from reddit_posts order by score desc limit 10"
```

### Browser options (for `reddit` and `fetch`)

| Option | Default | Meaning |
|---|---|---|
| `--profile DIR` | `profiles/reddit` | Persistent browser profile (cookies survive, so you look like a returning visitor) |
| `--no-profile` | off | Fresh browser with no cookies |
| `--locale` | `en-IN` | Browser locale. Keep it consistent with your IP's country |
| `--headless` | off | No window. Easier for sites to detect |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | OK, including "caught up" and partial results |
| `1` | Error (e.g. the page never loaded, no database) |
| `2` | Bad arguments (unknown sort, invalid subreddit name, ...) |
| `3` | Blocked (403, captcha, login wall, "blocked by network security") |
| `4` | Stopped by the throttle (session or daily budget, site stopped) |

For example, a cron script that backs off when blocked or throttled:

```bash
uv run web-scrapper reddit puzzles --limit 50
case $? in
  0) ;;                                             # ok (or caught up)
  3|4) echo "blocked or throttled: pausing until tomorrow"; touch .pause ;;
  *) echo "error: check the output" ;;
esac
```

---

## Configuration via environment variables

Secrets (proxy credentials) and machine-specific settings can live in the environment instead
of code. CLI flags override them.

| Variable | Sets |
|---|---|
| `SCRAPER_PROXY_SERVER`, `SCRAPER_PROXY_USERNAME`, `SCRAPER_PROXY_PASSWORD` | Proxy. With a proxy, `geoip` turns on automatically (timezone/locale match the exit IP) |
| `SCRAPER_HEADLESS` | `true` / `false` / `virtual` (`virtual` is Linux-only, Xvfb) |
| `SCRAPER_BLOCK_IMAGES` | `true` / `false` |
| `SCRAPER_PROFILE_DIR` | Persistent profile directory |

```bash
SCRAPER_PROXY_SERVER=http://host:port SCRAPER_PROXY_USERNAME=u SCRAPER_PROXY_PASSWORD=p \
  uv run python check_ip.py        # shows the exit IP the browser will use
```

## Key safety defaults

| | Default |
|---|---|
| Gap between page loads on one site | 4–10 s, randomised (log-normal) |
| Longer breaks | 30–120 s every 15–30 pages |
| Pages per session / per site per day | 60 / 400 |
| After 429 or 5xx | Retry with backoff, then a 60 s cool-down that doubles (max 15 min) |
| After 403 / captcha / "network security" block | **Never retried.** The site is stopped for the session |
| Circuit breaker | 3 failures in a row stop the site |
| On every page | Mouse drift, 1–4 uneven scrolls, a 2–8 s "reading" pause |

All defaults are listed in [ARCHITECTURE.md §4](docs/ARCHITECTURE.md#4-default-settings).

---

## Using it from Python

Collect a subreddit and store it:

```python
import asyncio
from pathlib import Path
from web_scrapper import BrowserConfig, FetchOptions, SqliteStore, collect_subreddit, open_session

async def main():
    cfg = BrowserConfig(profile_dir=Path("profiles/reddit"), extra={"locale": "en-IN"})
    opts = FetchOptions(wait_for="selector", wait_selector="shreddit-post")
    with SqliteStore() as store:                                  # data/scraper.db
        async with open_session(cfg, fetch_opts=opts, budget_store=store) as session:
            res = await collect_subreddit(
                session, "puzzles", sort="new", limit=60,
                seen=lambda pid: store.has("reddit_post", pid),   # incremental
            )
        stats = store.write("reddit_post", [p.to_dict() for p in res.posts])
    print(res.reason, len(res.posts), stats)

asyncio.run(main())
```

Visit arbitrary pages with throttling and human behaviour:

```python
import asyncio
from web_scrapper import BrowserConfig, ThrottleStop, open_session

async def main(urls):
    async with open_session(BrowserConfig.from_env()) as session:   # one browser, one tab, one throttle
        for url in urls:
            try:
                visit = await session.visit(url)     # throttle -> fetch -> record -> browse like a human
            except ThrottleStop as stop:             # budget used up, or the site was stopped (403/captcha)
                print("stopping:", stop)
                break
            r = visit.result
            print(url, "ok" if r.ok else f"blocked: {r.block} {r.signals}", "| waited", visit.waited)

asyncio.run(main(["https://www.siddheshsawant.in/", "https://www.siddheshsawant.in/#experience"]))
```

Pacing is configured with `HumanConfig` (reading pauses, scrolls, mouse) and `ThrottleConfig`
(gaps, breaks, budgets, cool-downs). Pass them as `open_session(..., human_cfg=..., throttle=...)`.

---

## Scraper Lab (local UI)

```bash
uv run scraper-lab      # or: uv run web-scrapper lab   ->  http://127.0.0.1:8765
```

A form on the left, results in the middle, run history on the right:

- **Presets** for your site, Reddit (with a records extractor), the fingerprint checkers
  (browserscan, pixelscan), a headers echo and the public IP.
- **Every setting is editable.** The browser, human-behaviour and throttle cards are generated
  from the config dataclasses, so new settings appear automatically.
- **Several URLs in one session**, to watch the throttle at work (Session tab).
- **Result tabs:** block signals, the identity the page sees, timings, screenshots (at load and
  at end), extracted records, text, HTML, network, console and cookies.
- **Save records** writes into `data/scraper.db`. The Database box shows what's stored.
- **Auto-reload:** the server restarts when files under `src/` change, so edit the code and run again.

---

## Project layout

```
src/web_scrapper/      the package (all new code goes here)
  config.py            BrowserConfig, ProxyConfig, HumanConfig, ThrottleConfig: all settings
  browser.py           open_browser(cfg): the ONLY module that imports camoufox
  fetcher.py           fetch(page, url, opts) -> FetchResult: waits, block detection, retries
  human.py             browse(page): mouse drift, scrolling, reading pauses (log-normal timing)
  throttle.py          Throttle: per-site gaps, breaks, budgets, cool-downs, circuit breaker
  session.py           open_session() / ScrapeSession.visit(): throttle -> fetch -> browse
  reddit.py            collect_subreddit(): load a feed, scroll like a person, paginate
  extractors/          pure HTML -> records parsers (reddit_listing), EXTRACTORS registry
  storage.py           SqliteStore (records, daily budget, run log) and JsonlSink
  cli.py               the `web-scrapper` command
  lab/                 Scraper Lab UI (FastAPI server + single HTML page)
tests/                 121 pytest tests (fakes only, no real browser) + fixtures/
docs/                  ARCHITECTURE.md, PLAN.md, camoufox_reference.md
test_bot.py            playground: collect a subreddit (same as `web-scrapper reddit`)
check_ip.py            prints the public IP the browser uses (honours SCRAPER_PROXY_*)
diagnose.py            fingerprint diagnostic on pixelscan.net (writes diagnostics/)
test_reddit_json.py    Reddit JSON via the `reddrix` library (separate approach)
crawler.py, db.py      legacy Selenium crawler + SQLite dump (not extended)

gitignored:
data/                  scraper.db (SQLite) and jsonl/
profiles/              persistent browser profiles (contain cookies: never commit)
output/                Lab runs (output/runs/<id>/), playground output
diagnostics/failures/  screenshot + HTML + summary of blocked/failed fetches
```

## Development

| Command | What it does |
|---|---|
| `uv run pytest` | All tests (about 1 s). `uv run pytest tests/test_storage.py -k caught_up` for one |
| `uv run ruff check .` / `uv run ruff format .` | Lint / format (the repo is lint-clean) |
| `uv run python diagnose.py` | Fingerprint diagnostic (about 30 s). Overwrites the files in `diagnostics/` |
| `uv run camoufox version` | Camoufox, browser and Playwright versions (for bug reports) |
| `uv add <pkg>` | Add a dependency (`pyproject.toml` is the source of truth) |

---

## Troubleshooting

| Symptom | What to do |
|---|---|
| "Your request has been blocked by network security" on Reddit | Turn **VPNs off** (free-VPN IPs are heavily flagged), keep the default persistent profile, don't run many fresh sessions back to back, and wait a few hours if it persists. The scraper reports it as `forbidden` and stops (exit code `3`) |
| `old.reddit.com` redirects to a login page | Logged-out old Reddit now requires an account. Use the new Reddit (`web-scrapper reddit`) |
| `AttributeError: 'Screen' object has no attribute 'as_conditions'` | In camoufox 0.5.x, import `Screen` from `camoufox.fingerprints`, not `browserforge` |
| Browser binary missing / launch fails | `uv run camoufox fetch` |
| Exit code `4` | Session or daily budget reached (`web-scrapper db summary` shows today's usage). Wait, or raise `--max-pages-per-day` carefully |
| A run "succeeded" but got few posts | Check `stopped:` in the output: `caught up` is normal for incremental runs |

## Responsible use

Scrape gently: the defaults favour slow, human-like, non-retrying behaviour on purpose.
Check `robots.txt` and the site's terms. For bulk Reddit data, Reddit's official API and
`.json` endpoints are more reliable than browser scraping and less likely to get you banned.
Never scrape while logged into a personal account.
