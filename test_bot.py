"""Playground: collect posts from a subreddit and store them.

uv run python test_bot.py [subreddit] [limit] [sort]

- SQLite (data/scraper.db): upserts by post id, keeps first_seen/last_seen, logs the run, and
  holds the per-site daily page budget. Query it:  sqlite3 data/scraper.db
  "select score, title from reddit_posts order by score desc limit 10"
- JSONL (data/jsonl/reddit_post.jsonl): append-only copy, one post per line, de-duplicated.
- Incremental: with sort=new, a re-run stops as soon as it reaches posts already stored.
"""

import asyncio
import logging
import sys
from pathlib import Path

from web_scrapper import (
    BrowserConfig,
    FetchOptions,
    JsonlSink,
    SqliteStore,
    collect_subreddit,
    open_session,
)

# Settings recommended after the "blocked by network security" investigation:
# a persistent profile (returning visitor, JS challenge passed once), consistent locale,
# and waiting for real posts rather than the challenge interstitial.
BROWSER = BrowserConfig(profile_dir=Path("profiles/reddit"), extra={"locale": "en-IN"})
FETCH = FetchOptions(wait_for="selector", wait_selector="shreddit-post", screenshot=True)


async def main(subreddit: str = "puzzles", limit: int = 25, sort: str = "new"):
    with SqliteStore() as store, JsonlSink() as jsonl:
        before = store.count("reddit_post")
        seen = (lambda pid: store.has("reddit_post", pid)) if sort == "new" else None

        async with open_session(BROWSER, fetch_opts=FETCH, budget_store=store) as session:
            run_id = store.start_run(f"r/{subreddit}/{sort}", {"limit": limit})
            res = await collect_subreddit(session, subreddit, sort=sort, limit=limit, seen=seen)

        records = [p.to_dict() for p in res.posts]
        stats = store.write("reddit_post", records)
        jsonl_stats = jsonl.write("reddit_post", records)
        store.finish_run(run_id, res.reason, len(records), stats)

        print(f"\n{res.url}")
        print(f"  stopped: {res.reason} | posts: {len(res.posts)} | rounds: {res.rounds}")
        print(f"  sqlite: {stats.inserted} new, {stats.updated} updated, {stats.unchanged} same")
        print(f"  jsonl:  {jsonl_stats.inserted} appended ({jsonl.path('reddit_post')})")
        print(f"  stored posts: {before} -> {store.count('reddit_post')} ({store.path})")
        for p in res.posts[:5]:
            print(f"  {p.score:>5} pts {p.comment_count:>4} c  {p.title[:70]}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = sys.argv[1:]
    asyncio.run(
        main(
            args[0] if args else "puzzles",
            int(args[1]) if len(args) > 1 else 25,
            args[2] if len(args) > 2 else "new",
        )
    )
