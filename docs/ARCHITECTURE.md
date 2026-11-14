# Architecture & Decisions

Detailed documentation of the project as of **2026-11-14** (Steps 1–4 + the Scraper Lab).
For setup and commands, see the [README](../README.md). For what comes next, see
[PLAN.md](PLAN.md). For the Camoufox API, see [camoufox_reference.md](camoufox_reference.md).

Contents:
1. [Goal and approach](#1-goal-and-approach)
2. [How the pieces fit together](#2-how-the-pieces-fit-together)
3. [Folder and file map](#3-folder-and-file-map)
4. [Default settings](#4-default-settings)
5. [Module details](#5-module-details)
6. [Scraper Lab](#6-scraper-lab)
7. [Testing](#7-testing)
8. [Decision log](#8-decision-log)
9. [Findings so far](#9-findings-so-far)
10. [Known gaps and next steps](#10-known-gaps-and-next-steps)

---

## 1. Goal and approach

**Goal:** scrape Reddit (and similar bot-protected sites) reliably, without getting blocked or banned.

**Approach:** use a real browser that is hard to tell apart from a human-driven Firefox
(Camoufox), pace it like a person (throttling + human behaviour), and keep the code modular. Each concern (configuration, launching, fetching,
later throttling, parsing and storage) lives in its own small module behind a simple interface,
so each one can change without rewriting the others.

Three scraping approaches exist in the repo. Only the first one is being developed:

| Approach | Where | Status |
|---|---|---|
| **Camoufox** (stealth Firefox via Playwright) | `src/web_scrapper/`, `test_bot.py`, `diagnose.py`, `check_ip.py` | **Current direction** |
| reddrix (Reddit JSON library) | `test_reddit_json.py` | Kept as an alternative data source |
| Selenium (headless Chrome crawler) | `crawler.py`, `db.py` | Legacy. Not extended; useful ideas get ported |

---

## 2. How the pieces fit together

```
 caller (test_bot.py, Scraper Lab, future CLI)
   │
   │  open_session(BrowserConfig, fetch_opts=FetchOptions, human_cfg=HumanConfig, throttle=ThrottleConfig)
   ▼
 session.py  ScrapeSession.visit(url)                                       planned
   │   1. throttle.wait(url)     throttle.py: may I load now? (sleeps / raises ThrottleStop)
   │   2. fetch(page, url)       fetcher.py: goto, wait, classify, retry if safe
   │   3. throttle.record(...)   feeds cool-downs + circuit breaker
   │   4. human.browse(page)     human.py: mouse, scrolls, reading pause (only if OK)
   ▼                                                                     ─► extractors/ ─► storage.py
 browser.py  open_browser(cfg) → BrowserContext (the only camoufox import)     (Step 5)      (Step 6)
 config.py   BrowserConfig · HumanConfig · ThrottleConfig · ProxyConfig (no project imports)
```

A typical call:

```python
async with open_session(BrowserConfig.from_env()) as session:   # browser + one tab + throttle
    for url in urls:
        try:
            visit = await session.visit(url)       # throttle -> fetch -> record -> browse
        except ThrottleStop as stop:               # budget used up, or the site was stopped
            break
        if visit.result.ok: ...                    # the caller decides what to do
```

The lower layers are still usable on their own: `open_browser()` + `fetch()` for a single page
without pacing, or `Throttle` around your own fetching code.

**Dependency rule:** dependencies only point downwards. `config.py` imports nothing from the
project. `browser.py` imports only `config`. `fetcher.py` only needs a Playwright `Page`.
`human.py` needs `config`. `throttle.py` needs `config`, `fetcher.BlockKind` and `human`'s random
helpers. `session.py` composes them all. The Lab is a consumer like any other script, and nothing
in the core imports the Lab.

---

## 3. Folder and file map

```
web-scrapper/
├── README.md                    project overview + quick start
├── CLAUDE.md                    guidance for Claude Code sessions in this repo
├── pyproject.toml               dependencies, scripts, ruff + pytest config (source of truth)
├── uv.lock                      locked dependency versions
├── .python-version              3.14
├── requirements.txt             leftover from before uv. Don't update it
│
├── src/web_scrapper/            ── THE PACKAGE (all new code goes here) ──
│   ├── __init__.py              public API (configs, open_browser, fetch, open_session,
│   │                            ScrapeSession, Visit, Throttle, ThrottleStop & subclasses, ...)
│   ├── config.py                BrowserConfig, ProxyConfig, HumanConfig, ThrottleConfig; from_env()
│   ├── browser.py               build_launch_kwargs(cfg), open_browser(cfg)
│   ├── fetcher.py               fetch(), FetchOptions, FetchResult, BlockKind, classify(),
│   │                            parse_retry_after(), backoff_delay()
│   ├── human.py                 browse(): mouse drift, scrolling, reading pause; lognormal_between()
│   ├── throttle.py              Throttle: per-site gaps, breaks, budget, cool-downs, circuit breaker
│   ├── session.py               ScrapeSession.visit(), open_session(): throttle → fetch → browse
│   └── lab/                     Scraper Lab (local UI)
│       ├── server.py            FastAPI app, presets, `scraper-lab` entry point
│       ├── runner.py            runs one experiment (1+ URLs in one session) → output/runs/<id>/
│       ├── schema.py            config dataclasses ⇄ form fields (auto-generated form cards)
│       └── static/index.html    the whole UI: HTML + CSS + vanilla JS, no build step
│
├── tests/
│   ├── test_fetcher.py          fetcher: classify, retry policy, backoff, edge cases (23 tests)
│   ├── test_throttle.py         throttle with a fake clock (14 tests)
│   └── test_human_session.py    human.browse + ScrapeSession with fake pages (7 tests)
│
├── docs/
│   ├── ARCHITECTURE.md          this file
│   ├── PLAN.md                  step-by-step roadmap with checkboxes + anti-ban checklist
│   └── camoufox_reference.md    Camoufox / Playwright API cheat sheet (checked against 0.5.9)
│
├── test_bot.py                  example script: open_session → visit 2 pages → print/save
├── diagnose.py                  fingerprint diagnostic on pixelscan.net → diagnostics/
├── check_ip.py                  prints the public IP Camoufox uses (still runs on import)
├── test_reddit_json.py          reddrix example (r/MachineLearning, with comments)
├── crawler.py, db.py            legacy Selenium crawler → scrape.db, and its dump script
│
├── diagnostics/                 diagnose.py output: report.json, page.html, browserscan.png
│   └── failures/                fetcher failure artifacts (gitignored)
├── output/                      (gitignored) script output
│   ├── page.html                last test_bot.py result
│   └── runs/<id>/               one folder per Lab run (see §6)
├── profiles/                    (gitignored) persistent browser profiles (cookies, storage)
│
└── .claude/
    ├── settings.json            hook: runs `ruff format` on every .py file Claude edits
    ├── skills/fingerprint-check/  /fingerprint-check skill: runs diagnose.py and analyses leaks
    └── worktrees/               git worktree for the reddit-scraper-architecture branch
```

**Gitignored:** `.venv/`, `__pycache__/`, `*.db` / `*.sqlite*`, `output/`, `profiles/`,
`diagnostics/failures/` and `docs/` (see §10).

---

## 4. Default settings

### 4.1 `BrowserConfig` (src/web_scrapper/config.py)

| Field | Default | Meaning / why this default |
|---|---|---|
| `os` | `"macos"` | OS the fingerprint claims. Matches the development Mac, so the fingerprint stays consistent with the real hardware (GPU, fonts, canvas). |
| `headless` | `False` | Visible window. Headless is easier to detect. `"virtual"` (Xvfb) is allowed on Linux only, and anything else raises `ValueError`. |
| `humanize` | `True` | Human-like cursor movement. Can also be a float: the max seconds per move. |
| `max_screen` | `(1920, 1080)` | Upper bound for the generated screen size. Becomes `camoufox.fingerprints.Screen`. |
| `proxy` | `None` | `ProxyConfig(server, username, password)`. `None` means a direct connection. |
| `geoip` | `None` = **auto** | Turns on automatically when a proxy is set, so timezone, locale and WebRTC IP match the exit IP. `True` / `False` force it. |
| `block_images` | `False` | Can be turned on to save bandwidth or proxy traffic. |
| `profile_dir` | `None` | `None` = a fresh browser each run. A path (e.g. `profiles/reddit1`) gives a persistent profile that keeps cookies and storage. |
| `extra` | `{}` | Escape hatch: raw Camoufox kwargs, merged last. |

**Environment variables** read by `BrowserConfig.from_env()`. Explicit keyword overrides win.

| Variable | Sets |
|---|---|
| `SCRAPER_PROXY_SERVER`, `SCRAPER_PROXY_USERNAME`, `SCRAPER_PROXY_PASSWORD` | `proxy` |
| `SCRAPER_HEADLESS` (`true` / `false` / `virtual`) | `headless` |
| `SCRAPER_BLOCK_IMAGES` (`true` / `false`) | `block_images` |
| `SCRAPER_PROFILE_DIR` | `profile_dir` |

**What Camoufox receives with all defaults** (`build_launch_kwargs(BrowserConfig())`):
```python
{"os": "macos", "headless": False, "humanize": True,
 "screen": Screen(max_width=1920, max_height=1080), "block_images": False}
```
With a proxy it also gets `proxy={...}` and `geoip=True`. With a profile it gets
`persistent_context=True, user_data_dir="..."`.

### 4.2 `FetchOptions` (src/web_scrapper/fetcher.py)

| Field | Default | Meaning |
|---|---|---|
| `wait_until` | `"domcontentloaded"` | When `page.goto` returns (`commit` / `domcontentloaded` / `load` / `networkidle`). |
| `wait_for` | `"networkidle"` | Then wait for `networkidle`, a `selector`, or `none`. A timeout here is only a warning. |
| `wait_selector` | `""` | CSS selector used when `wait_for="selector"`. |
| `timeout_ms` | `30000` | Timeout for goto and for the wait. |
| `settle_ms` | `0` | Extra fixed pause after the wait. |
| `retries` | `2` | Extra attempts (3 in total), **only** for retryable outcomes. |
| `backoff_base` | `5.0` s | First backoff is 2.5–5 s, then it doubles each attempt. |
| `backoff_max` | `120.0` s | Cap per wait. A longer `Retry-After` means give up rather than wait. |
| `failure_dir` | `diagnostics/failures` | Where failure artifacts go. `None` disables them. |

Other constant: `SHORT_PAGE_CHARS = 3000`. Block phrases in the page body only count when the
page text is this short. Title matches always count.

### 4.3 `HumanConfig` (src/web_scrapper/config.py)

Used by `human.browse()` after every **OK** page load. Ranges are `(min, max)`, and durations are drawn
log-normally (see §5.4).

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `True` | `False` = no browsing at all (fastest, least human) |
| `read_seconds` | `(2.0, 8.0)` | "Reading" pause per page. Median about 4 s |
| `scrolls` | `(1, 4)` | Mouse-wheel scrolls per page |
| `scroll_px` | `(300, 900)` | Distance per scroll |
| `scroll_pause` | `(0.6, 2.5)` s | Pause after each scroll |
| `scroll_back_chance` | `0.15` | Chance that a scroll goes back up 20–50% of a step |
| `mouse_moves` | `(0, 2)` | Idle cursor moves (Camoufox `humanize` curves the path) |

A typical page therefore costs about 5–15 s of browsing on top of the load itself.

### 4.4 `ThrottleConfig` (src/web_scrapper/config.py)

| Field | Default | Meaning |
|---|---|---|
| `min_interval` / `max_interval` | `4.0` / `10.0` s | Gap between page-load **starts** on the same site (log-normal, median about 6.3 s). Time spent browsing counts towards it |
| `max_pages_per_session` | `60` | Then `SessionBudgetExceeded`. Start a fresh session later |
| `break_every` | `(15, 30)` pages | How often to take a longer break |
| `break_seconds` | `(30.0, 120.0)` | Length of that break. `(0, 0)` disables breaks |
| `cooldown_base` | `60.0` s | After 429/5xx: wait this long, doubling per repeat (60 → 120 → 240 ...) |
| `cooldown_max` | `900.0` s | Cap on the cool-down |
| `stop_on_hard_block` | `True` | 403 / captcha → that site is stopped for the session (`DomainBlocked`) |
| `max_consecutive_failures` | `3` | Any non-OK results in a row (incl. login wall, network error) → site stopped |

"Site" = the last two labels of the hostname (`www.reddit.com` and `old.reddit.com` → `reddit.com`).
That's naive for suffixes like `.co.uk`, but fine for our targets.

### 4.5 Scraper Lab defaults

| Setting | Default |
|---|---|
| Address | `http://127.0.0.1:8765` (localhost only), auto-reload watching `src/` |
| Runs at a time | 1 (a second request gets HTTP 409) |
| Retries / backoff in the form | `1` / `5` s. Lower than the scripts, for quick experiments |
| Human behaviour / throttle | The `HumanConfig` / `ThrottleConfig` defaults above (editable cards) |
| Extra URLs per run | Up to 20, visited in the same session |
| Screenshot | on (viewport). Full page off |
| Stored per run | text up to 50,000 chars, 200 values per extract rule, 1,500 network entries |

### 4.6 Toolchain

| Tool | Version | Note |
|---|---|---|
| Python | 3.14 | via uv |
| camoufox | **0.5.9 (pinned)** | `Screen` must come from `camoufox.fingerprints` |
| playwright | 1.62.0 | installed as a camoufox dependency |
| browserforge | 1.2.4 | |
| fastapi / uvicorn | 0.162.7 / 0.54.0 | Lab only |
| pytest / ruff | 9.2.0 / 0.17.5 | dev group. Ruff: line length 100, rules E F I B UP |
| reddrix | 1.2.18 | used by `test_reddit_json.py` |

---

## 5. Module details

### 5.1 `config.py`: what we want

- `BrowserConfig` and `ProxyConfig` are **frozen dataclasses**, so a config can't be changed
  mid-run. To vary it, build a new one (`dataclasses.replace(cfg, block_images=True)`).
- `__post_init__` validates early, e.g. `headless="virtual"` outside Linux raises a clear
  `ValueError` instead of a crash deep inside Camoufox.
- `use_geoip` resolves the "auto" geoip rule (on if there is a proxy).
- `ProxyConfig.to_playwright()` produces Playwright's `{"server", "username", "password"}` dict.
- No camoufox import here, by design.

### 5.2 `browser.py`: how to launch

- `build_launch_kwargs(cfg)` is a **pure** function: config in, Camoufox kwargs out, with no
  side effects. You can inspect it, log it or unit-test it. The Lab's "Preview launch" button uses it.
- `open_browser(cfg)` is an async context manager that **always yields a `BrowserContext`**:
  - with `profile_dir`: Camoufox returns a persistent context, passed straight through, and the
    profile folder is created if needed;
  - without it: we call `browser.new_context()`. Camoufox applies its stealth defaults to new
    contexts too (checked in its source).
  - Contexts and the browser are closed in `finally` / `async with`, even when scraping code crashes.

### 5.3 `fetcher.py`: loading a page robustly

`fetch(page, url, opts, *, sleep=asyncio.sleep) -> FetchResult`

1. `page.goto(url, wait_until=..., timeout=...)`, then wait (`networkidle` / selector / none),
   then `settle_ms`.
2. Capture status, headers, final URL, title, HTML and visible text.
3. `classify()` the outcome into a `BlockKind`.
4. Retry or stop according to the policy below.
5. On final failure, save artifacts.

It **never raises for HTTP or network problems**: those are normal outcomes when scraping.
Programming errors still raise.

**`BlockKind` and the retry policy**

| Kind | Detected by | Retried? |
|---|---|---|
| `NONE` | nothing matched | (success) |
| `RATE_LIMITED` | HTTP 429, "too many requests", "you're doing that too much" | ✅ |
| `SERVER_ERROR` | HTTP ≥ 500 | ✅ |
| `NETWORK_ERROR` | any Playwright error from goto (DNS, reset, proxy, timeout) | ✅ |
| `FORBIDDEN` | HTTP 401/403, "you've been blocked", "access denied", Reddit's "whoa there, pardner" | ❌ never |
| `CAPTCHA` | "captcha", "verify you are human", Cloudflare's "Just a moment..." | ❌ never |
| `LOGIN_WALL` | final URL path matches `/login`, `/signin`, `/sign-in`, `/account/login` | ❌ never |

The rule behind this: temporary problems deserve a polite wait. A site that has *judged* you
must not be hit again immediately, because that looks more like a bot and is how soft blocks
become bans.

**Backoff:** `backoff_delay(attempt)` uses "equal jitter": half of the wait is fixed, half is
random. With `base=5` that gives 2.5–5 s, then 5–10 s, then 10–20 s, capped at `backoff_max`.
If the response has a `Retry-After` header (seconds or an HTTP date), the wait is at least that
long. If it exceeds `backoff_max`, the fetcher gives up with a warning rather than stalling.

**`FetchResult`:** `url, final_url, status, title, html, text, headers, block, signals,
warnings, error, attempts, elapsed, artifacts`, plus the properties `blocked` and `ok`.
`ok` means status < 400 **and** not blocked. Status alone isn't enough: Reddit's login wall returns 200.

**Failure artifacts:** `diagnostics/failures/<YYYYmmdd-HHMMSS>-<host>-<kind>/` contains
`screenshot.png` (full page), `page.html` and `summary.txt` (url, status, block, error, signals).

**Two edge cases handled (both found with the Lab):**
- *Same-document navigation* (only the `#anchor` changes, as SPAs do): `goto()` returns no
  response. The status is taken from the current document via the Navigation Timing API
  (`performance.getEntriesByType('navigation')[0].responseStatus`), with a warning.
- *Response arrives, then goto raises*: Firefox raises `NS_ERROR_NET_EMPTY_RESPONSE` for a
  403/429 **with an empty body**. `_goto()` records main-frame navigation responses during goto,
  and if goto raises after one arrived, the result is classified from that real status. Without
  this, an empty 403 would look like a retryable network error and **be retried**.

### 5.4 `human.py`: behaving like a person on a page

- `lognormal_between(lo, hi)`: human delays aren't uniform. Most are near a typical value and a
  few are much longer. The median is the geometric mean `√(lo·hi)`, σ is set so about 95% fall
  inside the range, and the rest are clamped. It's used for every pause in the project.
- `browse(page, cfg)` = `move_mouse()` (0–2 drifts inside the central viewport) → `scroll()`
  (uneven wheel steps, a pause after each, sometimes a small scroll back up) → a reading `pause()`.
  It returns what it did (`{"mouse_moves", "scrolls", "read_seconds"}`).
- Camoufox's `humanize` makes each cursor movement look human. `human.py` decides *what* to do
  and *when*. Bots are mostly caught by timing, not by individual actions.
- `sleep` and `rng` are injectable for tests.

### 5.5 `throttle.py`: when the next page load may happen

- `await throttle.wait(url)` sleeps until a load is allowed and returns the seconds waited.
  It raises `SessionBudgetExceeded` or `DomainBlocked` (both subclasses of `ThrottleStop`).
- `throttle.record(url, block)` reports the outcome. OK resets the failure streak and the
  cool-down doubling. Soft blocks set a cool-down. Hard blocks and failure streaks stop the site.
- State is per site, with an `asyncio.Lock` per site, so concurrent workers queue up instead of
  bursting. The session-wide page count drives breaks and the budget.
- `throttle.log` keeps human-readable decisions ("cool-down 60s", "break 74s", "STOP ...").
  The Lab shows them.
- `clock`, `sleep` and `rng` are injectable, so the tests use a fake clock and nothing actually waits.

### 5.6 `session.py`: putting it together

- `ScrapeSession(context, fetch_opts, human_cfg, throttle)` uses **one tab for all visits**, like
  a person (`get_page()` creates it lazily and recreates it if closed).
- `visit(url, browse=True)` runs `throttle.wait` → `fetch` → `throttle.record` → `human.browse`
  (only if OK) and returns a `Visit(url, result, waited, browse, started_at)`. It also appends
  to `session.visits`.
- `open_session(browser_cfg, ...)` is an async context manager: `open_browser` + `ScrapeSession`.

---

## 6. Scraper Lab

A local UI for running experiments quickly. Start it with `uv run scraper-lab` and open
**http://127.0.0.1:8765**.

**Layout:** form (left), results (centre), run history (right). ⌘↵ / Ctrl↵ runs.

**Form sections**
- **Target**: presets (below) or any URL, plus optional **"Then also visit"** URLs (one per line).
  All of them run in **one session**: same tab, same cookies, same throttle.
- **Browser config**: *generated from `BrowserConfig`* by `lab/schema.py`. Adding a field
  to the dataclass makes it appear after a page refresh. For a tooltip, add an entry to `HELP`
  in `schema.py` or use `field(..., metadata={"help": "..."})`. An empty proxy server falls
  back to the `SCRAPER_PROXY_*` environment variables.
- **Human behaviour** / **Throttle**: generated from `HumanConfig` / `ThrottleConfig`, the same way.
- **Page load & behaviour**: the `FetchOptions` fields and screenshot options.
- **Extract**: name / CSS selector / attribute rows (empty = text, `html` = innerHTML,
  or an attribute name).
- **Run JavaScript**: evaluated in Camoufox's isolated world (invisible to the page).

**Result tabs:** Overview (block signals, page facts, timings, *identity the page sees*),
**Session** (every visit: result, throttle wait, fetch time, attempts, what browsing did, plus the
throttle's decisions), Screenshot, Extract, Text (searchable), HTML (source, or rendered with scripts disabled), Network
(filterable), Console, Cookies (names only, values hidden), Launch config, JS, Raw JSON.

**History:** every run is stored in `output/runs/<YYYYmmdd-HHMMSS-mmm>/`:
`request.json` (form inputs, passwords masked), `result.json`, `page.html`, `screenshot.png`.
From the history list you can view a run, **Load inputs** into the form, **Re-run**, or delete it.

**Presets** (edit `PRESETS` in `lab/server.py`):
my site · old.reddit r/python (now redirects to login) · session demo (3 pages of my site) · reddit new UI r/python (`shreddit-post`)
· reddit JSON r/python · browserscan · pixelscan · httpbin headers · public IP.

**How it stays in sync with the code:** runs go through the real `open_browser()` and
`ScrapeSession` (so the real `fetch()`, `Throttle` and `human.browse()`), the form cards are built
from the config dataclasses, and uvicorn reloads on any change under `src/`. The tabs other than
Session describe the **last** page of the run.
Edit the code, then click Run.

**Safety choices:** it listens on 127.0.0.1 only. Run IDs are validated (no path traversal).
Only `http(s)` URLs are accepted. Scraped HTML is served with `Content-Security-Policy: sandbox`
so its scripts can't run on the Lab's origin. Proxy passwords are masked in saved files and in the UI.

**API** (used by the page, but scriptable too): `GET /api/schema`, `POST /api/preview`,
`POST /api/run`, `GET /api/status`, `GET /api/runs`, `GET|DELETE /api/runs/{id}`,
`GET /runs/{id}/screenshot.png`, `GET /runs/{id}/page.html[?download=true]`.

---

## 7. Testing

- `uv run pytest` runs `tests/` only (`testpaths = ["tests"]` in pyproject), so the root
  scripts named `test_*.py` (which launch browsers) are never collected.
- 44 tests, all with fakes, under a second in total.
- `tests/test_throttle.py`: a `FakeClock` whose `sleep()` advances time. It covers the gap
  enforced and not constant, no wait when the gap has passed, sites independent, subdomains
  grouped, budget, breaks, cool-down doubling/reset/cap, hard block stops the whole site (other
  sites fine), opt-out of the hard stop, circuit breaker, config validation.
- `tests/test_human_session.py`: the log-normal range and median, `browse()` staying inside the
  config ranges with one pause per scroll plus the read pause, disabled browsing, a session reusing
  one tab, the throttle gap applied between visits, no browsing on a block page, and a hard block
  meaning the next URL is never requested.
- `tests/test_fetcher.py` uses a `FakePage` that plays back scripted responses, plus
  an injected `sleep`. It covers the same-document navigation status, empty-body 403 → `FORBIDDEN`
  and not retried, empty-body 429 → retried, and: each `BlockKind`;
  no false positive for long pages that mention "captcha"; `/r/loginhelp/` not being a login wall;
  `Retry-After` parsing (seconds and dates); backoff ranges and the cap; 429 → retry → success;
  `Retry-After` honoured; a huge `Retry-After` → give up; 403 / captcha / login never retried;
  network errors retried until exhausted; failure artifacts written.
- Manual checks are done in the Lab (real browser). Fingerprint checks use `diagnose.py` /
  the `/fingerprint-check` skill.

---

## 8. Decision log

| # | Decision | Why |
|---|---|---|
| D1 | **Camoufox** as the browser | Fingerprint spoofing in C++ (not detectable JS patches), Playwright API, humanized cursor. |
| D2 | **Headed by default**, `headless="virtual"` only on Linux | Plain headless is easier to detect. The docs recommend a virtual display. |
| D3 | **Don't touch the fingerprint by hand**: no manual user agent, `navigator.*`, fixed `window`, `block_webgl` or main-world JS | An inconsistent fingerprint is worse than the generated one. These are the docs' explicit leak warnings. |
| D4 | `os="macos"` default | Consistent with the real machine (GPU, fonts, canvas). |
| D5 | `humanize=True` default | Free realism for any mouse interaction. |
| D6 | All settings in a **frozen `BrowserConfig` dataclass** | One object to pass around. A new setting = one line. No accidental mid-run changes. |
| D7 | `config.py` has **no camoufox import**. `browser.py` is the only module that imports it | Swapping or upgrading the browser library touches one file. |
| D8 | `open_browser` **always yields a `BrowserContext`** | Callers don't care whether a persistent profile is used. |
| D9 | `build_launch_kwargs` is **pure** (folder creation moved into `open_browser`) | Inspectable and testable. The Lab preview doesn't create folders as you type. |
| D10 | **Secrets via environment variables** (`from_env`) | Proxy credentials never live in code or git. |
| D11 | **geoip auto-on with a proxy** | Timezone / locale / WebRTC that disagree with the IP are a classic bot signal. |
| D12 | **Persistent profiles** optional (`profiles/`, gitignored) | A returning visitor with cookies looks more human. Profiles contain session cookies, so never commit them. |
| D13 | `fetch()` **returns a result rather than raising** | Blocks are normal outcomes. The caller decides (stop, wait, change identity). |
| D14 | **Typed `BlockKind`** instead of a bool | Different blocks need different reactions. |
| D15 | **Retry only 429 / 5xx / network errors; never 403 / captcha / login** | Retrying a judged request escalates to a ban. |
| D16 | Exponential backoff with **equal jitter**, honour `Retry-After`, **give up on a huge Retry-After** | A real pause every time, no clockwork rhythm, and no silent hour-long stall. |
| D17 | Block phrases count **only in the title or on short pages** | Avoids flagging real threads that mention "captcha" or "blocked". |
| D18 | Save **failure artifacts** automatically | Unattended runs can be debugged afterwards. |
| D19 | `ok` = status < 400 **and** not blocked | Login walls and soft blocks often return 200. |
| D20 | Replace fixed `sleep()` with **event-based waits** (`networkidle` / selector) | Faster on fast pages, more reliable on slow ones. A wait timeout is a warning, not a crash. |
| D21 | Scripts' runtime code behind `if __name__ == "__main__"` | Importing (e.g. by pytest) must not open a browser. |
| D22 | **Scraper Lab** = FastAPI + one static HTML page (no build step, no CDN) | Small, local, and easy to change. It reuses the package code instead of duplicating it. |
| D23 | Lab form **generated from `BrowserConfig`** | The UI can't drift from the code. |
| D24 | Lab binds to **127.0.0.1**, serves scraped HTML **sandboxed**, masks passwords, one run at a time | It's a local tool that handles untrusted pages and credentials. |
| D25 | **Pin `camoufox==0.5.9`** in pyproject | It was undeclared (and could have been removed by `uv sync`). Its API changed (`Screen`). |
| D26 | Unit tests use **fakes** (`FakePage`, injected `sleep`) | Fast, deterministic, no network or ban risk. Real-browser checks happen in the Lab. |
| D27 | Prefer official / JSON sources for bulk Reddit data where possible | More reliable, within the terms, much lower ban risk. The browser is for what those can't give. |
| D28 | **Log-normal** delays everywhere (not uniform) | Matches how human timing is distributed: mostly typical, occasionally long. |
| D29 | Throttle **per site** (last two hostname labels), not per URL or per host | Rate limits apply per site. old/www reddit share one. |
| D30 | Gap measured between load **starts**. Browsing time counts towards it | Time spent "reading" is real time between requests. No double waiting. |
| D31 | Soft blocks → **cool-down that doubles**. Hard blocks → **stop the site** for the session | Back off harder when warnings repeat. Never hammer a site that has judged you. |
| D32 | **Circuit breaker**: 3 non-OK results in a row stop the site | Catches slow failure modes (login walls, broken proxy) before they waste the budget. |
| D33 | **Session budget** (60 pages) + periodic **breaks** | Real sessions end, and long uninterrupted runs look automated. |
| D34 | `ScrapeSession`: **one tab**, a fixed order throttle → fetch → record → browse | Callers can't forget a step. One tab is how people browse. |
| D35 | Browse **only after OK** loads | Interacting with a block page wastes time and adds no realism. |
| D36 | `fetch()` recovers the real status when goto raises after a response | Empty-body 403s must not become retryable network errors (see §5.3). |
| D37 | Frozen config dataclasses marked immutable for ruff (`extend-immutable-calls`) | They're safe as default arguments, so the B008 warnings were false positives. |

---

## 9. Findings so far

- **The Camoufox docs are out of date on `Screen`.** In 0.5.9, `browserforge.fingerprints.Screen`
  crashes the launch (`'Screen' object has no attribute 'as_conditions'`). Use
  `camoufox.fingerprints.Screen`.
- **old.reddit.com now needs an account.** Logged-out requests redirect to
  `/login/?reason=lor2` ("accounts are required to access old Reddit"). The fetcher reports
  this as `LOGIN_WALL` (HTTP 200!). Step 5 will target the new Reddit UI (`shreddit-post`)
  or the `.json` endpoints instead.
- **Small locale mismatch in the default fingerprint:** `navigator.languages` is `en-US` while
  `Intl` reports locale `en-IN` and timezone `Asia/Kolkata` (from the host). It can be tuned with
  `extra={"locale": ...}` or a geoip-matched proxy. To be revisited in the fingerprint work.
- **Same-document navigations** (`/#anchor`) return no response from `goto()`, which made them look
  failed. Fixed via the Navigation Timing API.
- **Firefox raises on empty-body error responses** (`NS_ERROR_NET_EMPTY_RESPONSE`, e.g.
  httpbin `/status/403`). This made a 403 look like a retryable network error. Fixed (D36).
  Verified live: 403 → 1 attempt → site stopped, next URL never requested. 429 → retried → 8 s
  cool-down → next page waited exactly 8 s → OK.
- Launch is the slowest phase (about 11 s of a 15 s run), which is a reason to reuse a browser or
  context across many pages later instead of relaunching per page.

---

## 10. Known gaps and next steps

**Housekeeping**
- The `asyncio` PyPI package is still listed in dependencies. It's an obsolete backport of a
  stdlib module and should be removed (`uv remove asyncio`).
- `check_ip.py` still runs at import time. `crawler.py` imports `selenium`, which isn't installed.
- Existing ruff issues in the legacy scripts are not fixed yet.

**Roadmap** (details in [PLAN.md](PLAN.md))
- ~~**Step 4**: human behaviour + throttling~~ **done** (`human.py`, `throttle.py`, `session.py`).
  Not done yet: a *daily* budget that persists across runs (needs storage, so it comes with Step 6).
- **Step 5 (next)**: Reddit extractor (new UI / `.json`), pagination.
- **Step 6**: storage sinks (JSONL, then SQLite, deduplicated by post id).
- **Step 7**: more tests + a CLI (`uv run web-scrapper reddit r/python --limit 50`).
- **Step 8**: scale, with a proxy pool, one identity per worker, and `AsyncNewContext`.
