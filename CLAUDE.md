# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Experimental web scraper (target: Reddit and similar bot-protected sites). Three approaches coexist as top-level scripts:

- **Camoufox (current direction)**: `diagnose.py`, `test_bot.py`, `check_ip.py`. Stealth Firefox via `camoufox.async_api.AsyncCamoufox` (`os="macos"`, with `Screen` from `camoufox.fingerprints`). New scraping work goes here.
- **Selenium (legacy)**: `crawler.py` is a same-domain crawler using headless Chrome that writes to `scrape.db` (SQLite `pages` table). `db.py` dumps that table. Don't extend it; port useful parts to Camoufox.
- **reddrix**: `test_reddit_json.py` uses the `reddrix` library for Reddit JSON (subreddit posts and comments).

`src/web_scrapper/` is the package: `config.py` (`BrowserConfig`, `ProxyConfig`, `from_env()`), `browser.py` (`open_browser(cfg)`, the only module that imports camoufox), `fetcher.py` (`fetch(page, url, FetchOptions)` → `FetchResult`: block classification via `BlockKind` incl. visible banners in shadow DOM and JS-challenge waiting, retries with backoff for 429/5xx/network errors only, failure artifacts in `diagnostics/failures/`), `human.py` (`browse()`, log-normal pauses), `throttle.py` (`Throttle`: per-site gaps, breaks, budget, cool-downs, circuit breaker; raises `ThrottleStop`), `session.py` (`open_session()` / `ScrapeSession.visit()` = throttle → fetch → record → browse; this is the normal entry point for scraping), `extractors/` (pure `(html, url) -> records` parsers in the `EXTRACTORS` registry; `reddit.py` parses `shreddit-post` attributes into `RedditPost`), `reddit.py` (`collect_subreddit()`: infinite-scroll pagination with per-batch throttle pacing; `seen=` for incremental runs), `storage.py` (`SqliteStore` at `data/scraper.db`: generic `records` table + `reddit_posts` view, upserts, `page_budget` for the persistent daily budget, `runs` log; `JsonlSink`) `cli.py` (`uv run web-scrapper reddit|fetch|db|lab`; exit codes 0 ok, 1 error, 2 usage, 3 blocked, 4 throttle/budget) and `lab/` (Scraper Lab UI). Full architecture, defaults and the decision log are in `docs/ARCHITECTURE.md` (keep it updated when design decisions change). The step-by-step roadmap is in `docs/PLAN.md` and the Camoufox API cheat sheet in `docs/camoufox_reference.md`.

**Scraper Lab**: run `uv run scraper-lab` and open http://127.0.0.1:8765. It's a local FastAPI + single-HTML UI for running experiments. The form cards are generated from the `BrowserConfig` / `HumanConfig` / `ThrottleConfig` dataclasses (`lab/schema.py` `SECTIONS`), so new config fields appear automatically. Several URLs in one run share one `ScrapeSession`. Every run is saved to `output/runs/<id>/`. The server auto-reloads on changes under `src/`. Presets live in `lab/server.py`.

## Environment & dependencies

- Python 3.14, managed with **uv**. Run scripts with `uv run python <file>.py`.
- `pyproject.toml` is the source of truth. Add dependencies with `uv add <pkg>`. `requirements.txt` is leftover: don't update it.
- `camoufox[geoip]` is pinned to `==0.5.9` (in 0.5.x, `Screen` comes from `camoufox.fingerprints`, not browserforge; the geoip extra is `maxminddb`). Known gap: `selenium` is imported by the legacy `crawler.py` but isn't declared or installed.
- Camoufox downloads its browser binary on first use (`camoufox fetch`).

## Gotchas

- Real pytest tests live in `tests/` (`uv run pytest`; `testpaths` is set so the root scripts aren't collected). They use fakes, never a real browser.
- The top-level `test_*.py` files are **not pytest tests**. They're scripts that launch real browsers or hit Reddit (all guarded by `if __name__ == "__main__"`, as is `check_ip.py`). Lab API tests use FastAPI's `TestClient` (needs the `httpx2` dev dep); CLI tests monkeypatch `cli.open_session` / `cli.collect_subreddit`.
- Scripts default to `headless=False` (a visible browser) on purpose. Headless mode is easier for sites to detect.
- `diagnose.py` visits pixelscan.net, waits about 30s, and overwrites `diagnostics/report.json`, `page.html` and `browserscan.png`. Use those files to compare fingerprints across changes.
- `*.db` files are gitignored, and there's no CI.

## Lint & format

ruff is set up as a dev dependency and configured in `pyproject.toml`. A PostToolUse hook in `.claude/settings.json` runs `ruff format` on every `.py` file Claude edits. Run lint by hand with `uv run ruff check .` (add `--fix` to apply fixes). The whole repo is lint-clean.

## Worktrees

`.claude/worktrees/reddit-scraper-architecture-77e15b` is a git worktree on branch `claude/reddit-scraper-architecture-77e15b` (Reddit scraper architecture work).
