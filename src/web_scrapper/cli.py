"""Command line: `uv run web-scrapper <command> ...`

    web-scrapper reddit puzzles --limit 50 --sort new     collect + store (incremental for "new")
    web-scrapper fetch https://www.reddit.com/r/puzzles/  diagnose one page (status, block, timing)
    web-scrapper db summary                               what's stored, recent runs, today's budget
    web-scrapper db posts --subreddit puzzles --by score  list stored posts
    web-scrapper db export -o posts.csv                   export (csv or jsonl, by file extension)
    web-scrapper lab                                      start the Scraper Lab UI

Exit codes (for cron/scripts): 0 ok, 1 error, 2 usage, 3 blocked, 4 stopped by throttle/budget.
With --json, records go to stdout (one JSON object per line) and the summary to stderr.
"""

import argparse
import asyncio
import csv
import json
import logging
import sys
from datetime import date
from pathlib import Path

from .config import BrowserConfig, ThrottleConfig
from .extractors.reddit import SORTS, TIME_FILTERS, subreddit_url
from .fetcher import FetchOptions, fetch
from .reddit import collect_subreddit
from .session import open_session
from .storage import DEFAULT_DB, DEFAULT_JSONL_DIR, JsonlSink, SqliteStore
from .throttle import site_key

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_BLOCKED, EXIT_STOPPED = 0, 1, 2, 3, 4
DEFAULT_PROFILE = Path("profiles/reddit")
POST_ORDER = {
    "score": "CAST(score AS INTEGER) DESC",
    "comments": "CAST(comment_count AS INTEGER) DESC",
    "new": "created_at DESC",
    "seen": "last_seen_at DESC",
}


# ---------------------------------------------------------------- argument parsing


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="web-scrapper",
        description="Bot-detection-aware scraper (Camoufox). See docs/ARCHITECTURE.md.",
        epilog="Exit codes: 0 ok, 1 error, 2 usage, 3 blocked, 4 stopped by throttle/budget.",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="log fetches, retries, waits")
    sub = p.add_subparsers(dest="command", required=True)

    def browser_args(sp: argparse.ArgumentParser) -> None:
        g = sp.add_argument_group("browser")
        g.add_argument("--headless", action="store_true", help="no window (easier to detect)")
        g.add_argument(
            "--profile",
            type=Path,
            default=DEFAULT_PROFILE,
            help=f"persistent profile dir (default: {DEFAULT_PROFILE})",
        )
        g.add_argument("--no-profile", action="store_true", help="fresh browser, no cookies")
        g.add_argument("--locale", default="en-IN", help="browser locale (default: en-IN)")

    r = sub.add_parser("reddit", help="collect posts from a subreddit and store them")
    r.add_argument("subreddit", help="name, with or without r/")
    r.add_argument("--sort", default="new", choices=sorted(SORTS))
    r.add_argument("--time", dest="t", choices=sorted(TIME_FILTERS), help="for --sort top")
    r.add_argument("--limit", type=int, default=50, help="max posts (default: 50)")
    r.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"SQLite file ({DEFAULT_DB})")
    r.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL_DIR, help="JSONL directory")
    r.add_argument("--no-jsonl", action="store_true", help="don't write JSONL")
    r.add_argument(
        "--no-incremental",
        action="store_true",
        help="don't stop at already-stored posts (incremental is on for --sort new)",
    )
    r.add_argument("--max-pages-per-day", type=int, help="override the daily budget (400)")
    r.add_argument("--json", action="store_true", help="print records as JSON lines")
    browser_args(r)

    f = sub.add_parser("fetch", help="load one URL and report status / block / timing")
    f.add_argument("url")
    f.add_argument("--wait-selector", help="CSS selector to wait for (else network idle)")
    f.add_argument("--retries", type=int, default=1)
    f.add_argument("--save-html", type=Path, help="write the page HTML here")
    browser_args(f)

    d = sub.add_parser("db", help="inspect or export stored data")
    d.add_argument("--db", type=Path, default=DEFAULT_DB)
    dsub = d.add_subparsers(dest="db_command", required=True)
    dsub.add_parser("summary", help="counts, recent runs, today's page budget")
    dp = dsub.add_parser("posts", help="list stored Reddit posts")
    dp.add_argument("--subreddit")
    dp.add_argument("--by", choices=sorted(POST_ORDER), default="score")
    dp.add_argument("--limit", type=int, default=20)
    de = dsub.add_parser("export", help="export records to .csv or .jsonl")
    de.add_argument("-o", "--output", type=Path, required=True)
    de.add_argument("--kind", default="reddit_post")

    sub.add_parser("lab", help="start the Scraper Lab UI on http://127.0.0.1:8765")
    return p


def browser_config(args: argparse.Namespace) -> BrowserConfig:
    """CLI flags -> BrowserConfig (on top of SCRAPER_* env vars, e.g. proxy credentials)."""
    overrides: dict = {"extra": {"locale": args.locale}} if args.locale else {}
    if args.headless:
        overrides["headless"] = True
    overrides["profile_dir"] = None if args.no_profile else args.profile
    return BrowserConfig.from_env(**overrides)


def _out(msg: str, as_json: bool) -> None:
    """Human-readable output: stdout normally, stderr when stdout carries JSON."""
    print(msg, file=sys.stderr if as_json else sys.stdout)


# ---------------------------------------------------------------- commands


async def cmd_reddit(args: argparse.Namespace) -> int:
    if args.limit < 1:
        print("--limit must be >= 1", file=sys.stderr)
        return EXIT_USAGE
    try:
        url = subreddit_url(args.subreddit, args.sort, args.t)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE

    throttle = ThrottleConfig()
    if args.max_pages_per_day:
        throttle = ThrottleConfig(max_pages_per_day=args.max_pages_per_day)
    opts = FetchOptions(wait_for="selector", wait_selector="shreddit-post", screenshot=True)
    incremental = args.sort == "new" and not args.no_incremental

    with SqliteStore(args.db) as store:
        seen = (lambda pid: store.has("reddit_post", pid)) if incremental else None
        run_id = store.start_run(url, {"limit": args.limit, "source": "cli"})
        async with open_session(
            browser_config(args), fetch_opts=opts, throttle=throttle, budget_store=store
        ) as session:
            res = await collect_subreddit(
                session, args.subreddit, sort=args.sort, t=args.t, limit=args.limit, seen=seen
            )
        records = [p.to_dict() for p in res.posts]
        stats = store.write("reddit_post", records)
        store.finish_run(run_id, res.reason, len(records), stats)
        if records and not args.no_jsonl:
            with JsonlSink(args.jsonl) as sink:
                sink.write("reddit_post", records)

    if args.json:
        for rec in records:
            print(json.dumps(rec, ensure_ascii=False))
    _out(f"{res.url}", args.json)
    _out(f"  stopped: {res.reason}  ({len(records)} posts, {res.rounds} scroll rounds)", args.json)
    _out(
        f"  stored:  {stats.inserted} new, {stats.updated} updated, {stats.unchanged} unchanged"
        f" -> {args.db}",
        args.json,
    )
    if res.reason.startswith("throttle"):
        return EXIT_STOPPED
    if not res.ok:
        return EXIT_BLOCKED
    return EXIT_OK


async def cmd_fetch(args: argparse.Namespace) -> int:
    opts = FetchOptions(
        wait_for="selector" if args.wait_selector else "networkidle",
        wait_selector=args.wait_selector or "",
        retries=args.retries,
    )
    async with open_session(browser_config(args), fetch_opts=opts) as session:
        page = await session.get_page()
        r = await fetch(page, args.url, opts)
    print(f"{args.url}\n  status:  {r.status}   final: {r.final_url}")
    print(f"  title:   {r.title[:100]!r}")
    print(f"  block:   {r.block}   attempts: {r.attempts}   elapsed: {r.elapsed}s")
    if r.challenge_seconds is not None:
        print(f"  JS challenge: {r.challenge_seconds}s (pending={r.challenge_pending})")
    for line in r.signals:
        print(f"  signal:  {line}")
    for line in r.warnings:
        print(f"  warning: {line}")
    if r.artifacts:
        print(f"  artifacts: {', '.join(r.artifacts)}")
    if args.save_html and r.html:
        args.save_html.parent.mkdir(parents=True, exist_ok=True)
        args.save_html.write_text(r.html, encoding="utf-8")
        print(f"  html -> {args.save_html}")
    if r.ok:
        return EXIT_OK
    return EXIT_ERROR if r.error and r.status is None else EXIT_BLOCKED


def cmd_db(args: argparse.Namespace) -> int:
    if not args.db.exists():
        print(f"no database at {args.db} (run `web-scrapper reddit <sub>` first)", file=sys.stderr)
        return EXIT_ERROR
    with SqliteStore(args.db) as store:
        if args.db_command == "summary":
            s = store.summary()
            print(f"{s['path']}")
            for kind, n in s["kinds"].items():
                print(f"  {n:>6}  {kind}")
            for row in s["subreddits"]:
                print(
                    f"          r/{row['subreddit']}: {row['posts']} (last seen {row['last_seen']})"
                )
            today = date.today().isoformat()
            used = store.pages_on(today, site_key("https://www.reddit.com/"))
            print(f"  budget today: reddit.com {used}/{ThrottleConfig().max_pages_per_day} pages")
            print("  recent runs:")
            for run in s["runs"][:5]:
                print(
                    f"    #{run['id']} {run['started_at']}  {run['target']}  -> {run['reason']}"
                    f"  ({run['inserted'] or 0} new / {run['records'] or 0})"
                )
            return EXIT_OK

        if args.db_command == "posts":
            sql = "SELECT score, comment_count, subreddit, title, permalink FROM reddit_posts"
            params: list = []
            if args.subreddit:
                sql += " WHERE subreddit = ?"
                params.append(args.subreddit.removeprefix("r/"))
            sql += f" ORDER BY {POST_ORDER[args.by]} LIMIT ?"
            params.append(args.limit)
            for row in store.db.execute(sql, params):
                print(
                    f"{row['score']:>6} pts {row['comment_count']:>5} c  "
                    f"r/{row['subreddit']}  {row['title'][:80]}"
                )
            return EXIT_OK

        # export
        rows = [
            json.loads(r["data"])
            for r in store.db.execute(
                "SELECT data FROM records WHERE kind=? ORDER BY first_seen_at", (args.kind,)
            )
        ]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.suffix.lower() == ".csv":
            fields = sorted({k for row in rows for k in row})
            with args.output.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        elif args.output.suffix.lower() in (".jsonl", ".ndjson"):
            with args.output.open("w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        else:
            print("output must end in .csv or .jsonl", file=sys.stderr)
            return EXIT_USAGE
        print(f"exported {len(rows)} {args.kind} records -> {args.output}")
        return EXIT_OK


# ---------------------------------------------------------------- entry point


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "reddit":
            return asyncio.run(cmd_reddit(args))
        if args.command == "fetch":
            return asyncio.run(cmd_fetch(args))
        if args.command == "db":
            return cmd_db(args)
        if args.command == "lab":
            from .lab.server import main as lab_main

            lab_main()
            return EXIT_OK
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    return EXIT_USAGE


def entry() -> None:
    """[project.scripts] entry point: exit with main()'s code."""
    sys.exit(main())
