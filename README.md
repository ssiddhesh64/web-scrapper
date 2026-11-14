# web-scrapper

An experimental, bot-detection-aware web scraper aimed at **Reddit**, built on
[Camoufox](https://camoufox.com), a stealth Firefox driven through Playwright.

It is being built step by step. The current state is:

- **Steps 1–4 done**: launch config, a browser factory, a resilient fetcher (block
  detection + safe retries), human-like browsing and per-site throttling, wired together in a
  `ScrapeSession`.
- **Scraper Lab**: a local web UI for running and comparing scrape experiments.
- **Next**: Step 5 (Reddit extractor). See [docs/PLAN.md](docs/PLAN.md).

> Full documentation of the decisions, folders, defaults and internals is in
> **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

---

## Quick start

```bash
# 1. Install Python deps (Python 3.14, managed by uv)
uv sync

# 2. Download the Camoufox browser binary (once)
uv run camoufox fetch

# 3. Open the Scraper Lab UI  →  http://127.0.0.1:8765
uv run scraper-lab

# or run the example script
uv run python test_bot.py

# tests (no browser, no network)
uv run pytest
```

## Using it from code

```python
import asyncio
from pathlib import Path
from web_scrapper import BrowserConfig, FetchOptions, ThrottleStop, open_session

async def main(urls):
    cfg = BrowserConfig(profile_dir=Path("profiles/reddit1"))      # keep cookies between runs
    opts = FetchOptions(wait_for="selector", wait_selector="shreddit-post")

    # One browser, one tab, one throttle. Each visit = wait for the throttle -> fetch -> browse like a human
    async with open_session(cfg, fetch_opts=opts) as session:
        for url in urls:
            try:
                visit = await session.visit(url)
            except ThrottleStop as stop:          # budget used up, or the site was stopped (403/captcha)
                print("stopping:", stop)
                break
            r = visit.result
            if r.ok:
                print(r.title, len(r.html), "waited", visit.waited, "browse", visit.browse)
            else:
                print("blocked:", r.block, r.signals, r.artifacts)

asyncio.run(main(["https://www.reddit.com/r/python/", "https://www.reddit.com/r/learnpython/"]))
```

Pacing is configured with `HumanConfig` (reading pauses, scrolls, mouse) and `ThrottleConfig`
(gap between page loads per site, breaks, session budget, cool-downs). Pass them as
`open_session(..., human_cfg=..., throttle=...)`. The defaults are in
[docs/ARCHITECTURE.md §4](docs/ARCHITECTURE.md#4-default-settings).

Proxy credentials and other settings can come from environment variables instead of code:

```bash
SCRAPER_PROXY_SERVER=http://host:port SCRAPER_PROXY_USERNAME=u SCRAPER_PROXY_PASSWORD=p \
SCRAPER_PROFILE_DIR=profiles/reddit1 uv run python test_bot.py
```

## Project layout (short version)

```
src/web_scrapper/      the package (all new code goes here)
  config.py            BrowserConfig, ProxyConfig, HumanConfig, ThrottleConfig: all settings
  browser.py           open_browser(cfg): the ONLY module that imports camoufox
  fetcher.py           fetch(page, url, opts) -> FetchResult: waits, block detection, retries
  human.py             browse(page): mouse drift, scrolling, reading pauses (log-normal timing)
  throttle.py          Throttle: per-site gaps, breaks, session budget, cool-downs, circuit breaker
  session.py           open_session() / ScrapeSession.visit(): throttle -> fetch -> browse
  lab/                 Scraper Lab UI (FastAPI server + single HTML page)
tests/                 pytest unit tests (fakes only, no real browser)
docs/                  PLAN.md (roadmap), ARCHITECTURE.md (this project's docs), camoufox_reference.md
test_bot.py            small example script using the package
diagnose.py            fingerprint diagnostic against pixelscan.net (writes diagnostics/)
check_ip.py            prints the public IP Camoufox is using
test_reddit_json.py    Reddit JSON via the `reddrix` library (separate approach)
crawler.py, db.py      legacy Selenium crawler + SQLite dump (not extended any more)
output/                run outputs (gitignored)    profiles/  browser profiles (gitignored)
```

## Commands

| Command | What it does |
|---|---|
| `uv run scraper-lab` | Starts the lab UI on http://127.0.0.1:8765. Auto-reloads on changes under `src/`. |
| `uv run python test_bot.py` | Visits two pages in one session (throttled, human browsing) and prints a summary. |
| `uv run pytest` | Runs the unit tests in `tests/`. |
| `uv run ruff check .` / `uv run ruff format .` | Lint / format. |
| `uv run python diagnose.py` | Fingerprint diagnostic (about 30s). Overwrites the files in `diagnostics/`. |
| `uv run camoufox version` | Camoufox, browser and Playwright versions (useful for bug reports). |

## Responsible use

Scrape gently: the defaults favour slow, human-like, non-retrying behaviour on purpose.
Check `robots.txt` and the site's terms. For bulk Reddit data, Reddit's official API and
`.json` endpoints are more reliable than browser scraping and less likely to get you banned.
Never scrape while logged into a personal account.
