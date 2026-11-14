# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Experimental web scraper (target: Reddit and similar bot-protected sites). Three approaches coexist as top-level scripts:

- **Camoufox (current direction)**: `diagnose.py`, `test_bot.py`, `check_ip.py`. Stealth Firefox via `camoufox.async_api.AsyncCamoufox` plus `browserforge` fingerprints (`Screen`, `os="macos"`). New scraping work goes here.
- **Selenium (legacy)**: `crawler.py` is a same-domain crawler using headless Chrome that writes to `scrape.db` (SQLite `pages` table). `db.py` dumps that table. Don't extend it; port useful parts to Camoufox.
- **reddrix**: `test_reddit_json.py` uses the `reddrix` library for Reddit JSON (subreddit posts and comments).

`src/web_scrapper/` is the package: `config.py` (`BrowserConfig`, `ProxyConfig`, `from_env()`), `browser.py` (`open_browser(cfg)`, the only module that imports camoufox) and `lab/` (Scraper Lab UI). The step-by-step roadmap is in `docs/PLAN.md` and the Camoufox API cheat sheet in `docs/camoufox_reference.md`.

**Scraper Lab**: run `uv run scraper-lab` and open http://127.0.0.1:8765. It's a local FastAPI + single-HTML UI for running experiments. The browser-config form is generated from the `BrowserConfig` dataclass (`lab/schema.py`), so new config fields appear automatically. Every run is saved to `output/runs/<id>/`. The server auto-reloads on changes under `src/`. Presets live in `lab/server.py`.

## Environment & dependencies

- Python 3.14, managed with **uv**. Run scripts with `uv run python <file>.py`.
- `pyproject.toml` is the source of truth. Add dependencies with `uv add <pkg>`. `requirements.txt` is leftover: don't update it.
- Known gaps: `camoufox` is pinned to `==0.5.9` in pyproject (in 0.5.x, `Screen` comes from `camoufox.fingerprints`, not browserforge). `selenium` is imported by `crawler.py` but isn't declared or installed. `asyncio` is listed as a dependency, but it's stdlib and the PyPI package is an obsolete backport, so remove it rather than rely on it.
- Camoufox downloads its browser binary on first use (`camoufox fetch`).

## Gotchas

- `test_*.py` are **not pytest tests yet**. They're scripts that launch real browsers or hit Reddit. `test_bot.py` is now guarded by `if __name__ == "__main__"`, but `check_ip.py` still runs at import time. The plan is to convert them into real pytest tests: put the runtime work behind `if __name__ == "__main__"` and mock network/browser calls in unit tests.
- Scripts default to `headless=False` (a visible browser) on purpose. Headless mode is easier for sites to detect.
- `diagnose.py` visits pixelscan.net, waits about 30s, and overwrites `diagnostics/report.json`, `page.html` and `browserscan.png`. Use those files to compare fingerprints across changes.
- `*.db` files are gitignored, and there's no CI.

## Lint & format

ruff is set up as a dev dependency and configured in `pyproject.toml`. A PostToolUse hook in `.claude/settings.json` runs `ruff format` on every `.py` file Claude edits. Run lint by hand with `uv run ruff check .` (add `--fix` to apply fixes). Existing files haven't been bulk-reformatted yet.

## Worktrees

`.claude/worktrees/reddit-scraper-architecture-77e15b` is a git worktree on branch `claude/reddit-scraper-architecture-77e15b` (Reddit scraper architecture work).
