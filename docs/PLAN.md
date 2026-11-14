# Reddit Scraper Plan (Camoufox)

Goal: grow `test_bot.py` into a small, modular scraper where launching the browser, fetching,
parsing and storing are separate pieces. Adding a new site or output format should then mean
adding a file, not rewriting one. API reference: [camoufox_reference.md](camoufox_reference.md).

We go one step at a time. Each step ends with something you can run.

---

## Target layout

```
src/web_scrapper/
  config.py        # ScraperConfig / BrowserConfig dataclasses (all knobs in one place, .env overrides)
  browser.py       # `async with open_browser(cfg)` -> page/context. The ONLY file that imports camoufox
  human.py         # random delays, human scrolling, reading pauses
  fetcher.py       # fetch(page, url) -> FetchResult: waits, retries, backoff, block detection
  throttle.py      # rate limiter (per-domain min interval + jitter, daily budget)
  extractors/
    base.py        # Extractor protocol: parse(html|page) -> list[dict]
    reddit.py      # subreddit listing, post + comments
  storage.py       # Sink protocol: JsonlSink, SqliteSink
  cli.py           # `uv run web-scrapper reddit r/python --limit 50`
tests/             # pytest, with browser/network mocked
profiles/          # persistent browser profiles (gitignored)
```

Data flow: `cli → config → browser → fetcher(+throttle, human) → extractor → storage`.
Each arrow is a plain function call on a small interface, so any box can be swapped
(for example the `reddrix` JSON client as an alternative "fetcher" for Reddit).

---

## Steps

### Step 1: clean up test_bot.py (no new files)
- [x] Respect the `headless` arg (it's currently hard-coded to `False`)
- [x] Remove unused `import time`, fix the `constrains` typo
- [x] Put `asyncio.run` behind `if __name__ == "__main__":` (importing the file then no longer opens a browser)
- [x] Replace the fixed `sleep(10)` with `wait_for_selector` / `wait_for_load_state`
- [x] Check `response.status` from `goto`
- [x] Print a short preview instead of the full HTML; save the full HTML to a file
- [x] Turn on `humanize=True`
- [x] Import `Screen` from `camoufox.fingerprints` (browserforge's crashes on 0.5.x)

### Step 2: config + browser factory
- [x] `BrowserConfig` dataclass (os, headless, humanize, proxy, geoip, block_images, profile_dir)
- [x] `open_browser(cfg)` async context manager in `browser.py` that wraps `AsyncCamoufox`
- [x] Optional persistent profile (`persistent_context=True, user_data_dir=profiles/<name>`)
- [x] `BrowserConfig.from_env()` reads SCRAPER_* env vars (proxy creds stay out of code)
- [x] `test_bot.py` becomes about 15 lines that call these

### Step 2.5: Scraper Lab UI (done)
- [x] `uv run scraper-lab` → http://127.0.0.1:8765. Form generated from `BrowserConfig`; runs saved in `output/runs/`
- [x] First-draft block detection lives in `lab/runner.py`; move it to `fetcher.py` in Step 3

### Step 3: fetcher with resilience
- [x] `FetchResult(url, status, html, final_url, blocked: bool, elapsed)`
- [x] Block detection: status 403/429, captcha/"blocked" text, redirect to login
- [x] Retries with exponential backoff + jitter. Honour `Retry-After`
- [x] Screenshot + HTML dump on failure into `diagnostics/failures/`
- [x] Retry only RATE_LIMITED / SERVER_ERROR / NETWORK_ERROR. FORBIDDEN / CAPTCHA / LOGIN_WALL are never retried
- [x] Phrase matching only on the title or short pages (avoids false positives on real threads)
- [x] Lab uses `fetch()`. `tests/test_fetcher.py` has 20 tests with a FakePage (`uv run pytest`)

### Step 4: human behaviour + throttling (done)
- [x] `human.pause()` / `lognormal_between()`: log-normal, not uniform
- [x] `human.browse()`: mouse drift, uneven scrolling (sometimes back up), reading pause
- [x] `throttle.Throttle`: per-site gap (4–10 s), breaks every 15–30 pages, 60-page session budget
- [x] Cool-downs on 429/5xx (60 s, doubling, max 15 min). 403/captcha stops the site. Circuit breaker after 3 failures
- [x] `session.ScrapeSession` / `open_session()`: throttle → fetch → record → browse, one tab
- [x] `HumanConfig` + `ThrottleConfig` in `config.py`, as auto-generated cards in the Lab, plus a multi-URL session mode and a Session tab
- [x] Fixed two fetcher bugs found live: same-document navigations, and empty-body 403s that looked like network errors
- [ ] Later: a daily budget persisted across runs (with storage, Step 6)

### Step 5: Reddit extractor
- [ ] Note (2026-11-14, found with the Lab): logged-out `old.reddit.com` now redirects to
      `/login/?reason=lor2` ("accounts are required to access old Reddit"), so old.reddit is out
      unless logged in.
- [ ] Decide on a source: `old.reddit.com` HTML (simple, stable selectors), new-Reddit `shreddit-*`
      elements, or the `.json` endpoints / `reddrix`. Prefer the `.json` endpoints / the official API where possible
- [ ] `extractors/reddit.py`: listing → posts (id, title, author, score, url, created, num_comments)
- [ ] Pagination (`after=` cursor / "next" link), with a limit

### Step 6: storage
- [ ] `Sink` protocol. `JsonlSink` first, then `SqliteSink` (port ideas from `crawler.py`)
- [ ] De-duplicate by post id, so re-runs are incremental

### Step 7: tests + CLI
- [ ] pytest: extractor tests on saved HTML fixtures, fetcher tests with a fake page
- [ ] `cli.py` with argparse, wired to `[project.scripts]`
- [ ] Housekeeping: `uv add "camoufox[geoip]"`, `uv remove asyncio`

### Step 8 (later): scale
- [ ] Proxy pool, with one sticky identity (profile + proxy + fingerprint) per worker
- [ ] `AsyncNewContext` for several identities in one browser
- [ ] Concurrency via `asyncio.Semaphore`, kept small

---

## Anti-detection / anti-ban checklist

**Fingerprint (Camoufox handles most of it, so don't break it)**
- Don't override the UA, navigator, WebGL or `config` by hand. Keep `os` consistent with the profile.
- No fixed `window`. No `block_webgl`. No `mw:` main-world JS.
- Headed on macOS. `headless="virtual"` on a Linux server.
- Re-run `diagnose.py` after every launch-config change.

**Network identity (the most common ban reason)**
- Your home IP is the identity. A datacenter/VPS IP is flagged quickly. Residential/mobile proxies are best.
- `geoip=True` with a proxy, so timezone, locale and WebRTC all match the IP.
- One proxy ↔ one profile ↔ one fingerprint. Don't rotate the IP mid-session with the same cookies.

**Behaviour**
- `humanize=True`. Random, non-uniform delays. Scroll before you extract. Don't hit 100 URLs back to back.
- Arrive naturally: land on the subreddit, then click into posts. Don't deep-link thousands of URLs.
- Limit pages per session and sessions per day. Take breaks. Avoid perfectly regular schedules.
- `block_images=True` to save bandwidth (an acceptable trade-off for scraping).

**State**
- A persistent profile, so cookies and consent banners survive between runs (a returning visitor looks normal).
- Detect blocks early and **stop/back off** rather than retrying hard. Hammering after a 429 is what escalates to a ban.

**Rules**
- Check `robots.txt` and Reddit's terms. Reddit offers an official API (OAuth, clear rate limits)
  and `.json` endpoints. For bulk data, these are far more reliable than browser scraping and much less likely to get you banned.
- Never scrape while logged into your personal account.
