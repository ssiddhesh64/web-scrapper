"""Storage, persistent daily budget, and incremental collection. No browser, no network."""

import asyncio
import json
import random

import pytest

from web_scrapper.config import HumanConfig, ThrottleConfig
from web_scrapper.fetcher import FetchOptions
from web_scrapper.reddit import collect_subreddit
from web_scrapper.session import ScrapeSession
from web_scrapper.storage import JsonlSink, SqliteStore, WriteStats
from web_scrapper.throttle import DailyBudgetExceeded, Throttle

from .test_reddit import FakeContext, FeedPage, no_sleep


def post(i: int, score: int = 1, **extra) -> dict:
    return {"id": f"t3_p{i}", "subreddit": "puzzles", "title": f"Post {i}", "score": score, **extra}


# ---------- JsonlSink ----------


def test_jsonl_appends_and_dedupes_across_reopen(tmp_path):
    with JsonlSink(tmp_path) as sink:
        assert sink.write("reddit_post", [post(1), post(2), post(1)]) == WriteStats(
            inserted=2, skipped=1
        )
    with JsonlSink(tmp_path) as sink:  # a later run: ids are re-read from the file
        stats = sink.write("reddit_post", [post(2, score=99), post(3)])
    assert stats == WriteStats(inserted=1, skipped=1)
    lines = [json.loads(x) for x in (tmp_path / "reddit_post.jsonl").read_text().splitlines()]
    assert [x["id"] for x in lines] == ["t3_p1", "t3_p2", "t3_p3"]
    assert lines[1]["score"] == 1  # first version wins in JSONL
    assert all("_scraped_at" in x for x in lines)


def test_jsonl_survives_a_torn_last_line(tmp_path):
    (tmp_path / "reddit_post.jsonl").write_text(json.dumps(post(1)) + '\n{"id": "t3_p2", "ti')
    with JsonlSink(tmp_path) as sink:
        assert sink.has("reddit_post", "t3_p1")
        assert sink.write("reddit_post", [post(2)]).inserted == 1


def test_records_without_id_are_skipped(tmp_path):
    with JsonlSink(tmp_path) as sink:
        assert sink.write("reddit_post", [{"title": "no id"}]).skipped == 1
    store = SqliteStore(":memory:")
    assert store.write("reddit_post", [{"title": "no id"}]).skipped == 1


def test_bad_kind_rejected(tmp_path):
    with pytest.raises(ValueError):
        JsonlSink(tmp_path).write("../evil", [post(1)])
    with pytest.raises(ValueError):
        SqliteStore(":memory:").write("drop table", [post(1)])


# ---------- SqliteStore ----------


def test_sqlite_upsert_counts_inserted_updated_unchanged():
    store = SqliteStore(":memory:")
    assert store.write("reddit_post", [post(1), post(2)]) == WriteStats(inserted=2)
    stats = store.write("reddit_post", [post(1, score=50), post(2), post(3)])
    assert stats == WriteStats(inserted=1, updated=1, unchanged=1)
    assert store.get("reddit_post", "t3_p1")["score"] == 50  # latest data kept
    assert store.count("reddit_post") == 3 and store.has("reddit_post", "t3_p3")


def test_sqlite_keeps_first_seen_and_counts_sightings():
    store = SqliteStore(":memory:")
    store.write("reddit_post", [post(1)])
    store.db.execute("UPDATE records SET first_seen_at='2020-01-01T00:00:00+00:00'")
    store.write("reddit_post", [post(1, score=7)])
    row = store.db.execute("SELECT * FROM reddit_posts").fetchone()
    assert row["first_seen_at"] == "2020-01-01T00:00:00+00:00"
    assert row["last_seen_at"] > row["first_seen_at"] and row["seen_count"] == 2


def test_reddit_posts_view_exposes_typed_columns():
    store = SqliteStore(":memory:")
    store.write("reddit_post", [post(1, score=5, nsfw=True), post(2, score=40)])
    store.write("other_kind", [{"id": "x"}])  # not part of the view
    rows = store.db.execute(
        "SELECT id, score, nsfw FROM reddit_posts WHERE score > 10 ORDER BY score DESC"
    ).fetchall()
    assert [tuple(r) for r in rows] == [("t3_p2", 40, None)]  # numbers stay numbers
    assert store.summary()["kinds"] == {"other_kind": 1, "reddit_post": 2}


def test_sqlite_persists_on_disk(tmp_path):
    path = tmp_path / "sub" / "scraper.db"
    with SqliteStore(path) as store:
        store.write("reddit_post", [post(1)])
    with SqliteStore(path) as store:
        assert store.has("reddit_post", "t3_p1")


def test_budget_and_run_log():
    store = SqliteStore(":memory:")
    store.add_pages("2026-11-14", "reddit.com")
    store.add_pages("2026-11-14", "reddit.com", 4)
    assert store.pages_on("2026-11-14", "reddit.com") == 5
    assert store.pages_on("2026-11-15", "reddit.com") == 0
    run = store.start_run("https://www.reddit.com/r/puzzles/new/", {"limit": 5})
    store.finish_run(run, "limit reached", 5, WriteStats(inserted=2, updated=1))
    last = store.summary()["runs"][0]
    assert (last["reason"], last["records"], last["inserted"], last["updated"]) == (
        "limit reached",
        5,
        2,
        1,
    )


# ---------- persistent daily budget in the throttle ----------


def throttle_with(store, day="2026-11-14", **cfg):
    base = {"min_interval": 0, "max_interval": 0, "break_seconds": (0, 0)}
    return Throttle(
        ThrottleConfig(**(base | cfg)), sleep=no_sleep, budget_store=store, today=lambda: day
    )


def test_daily_budget_survives_across_sessions():
    store, url = SqliteStore(":memory:"), "https://www.reddit.com/r/puzzles/"
    t1 = throttle_with(store, max_pages_per_day=3)
    asyncio.run(t1.wait(url))
    asyncio.run(t1.wait(url))
    t2 = throttle_with(store, max_pages_per_day=3)  # a new run, same day
    asyncio.run(t2.wait("https://old.reddit.com/"))  # same site, other subdomain
    with pytest.raises(DailyBudgetExceeded):
        asyncio.run(t2.wait(url))
    assert asyncio.run(t2.wait("https://example.com/")) == 0  # other sites unaffected
    t3 = throttle_with(store, day="2026-11-15", max_pages_per_day=3)  # next day: fresh budget
    assert asyncio.run(t3.wait(url)) == 0


def test_no_store_means_no_daily_limit():
    t = throttle_with(None, max_pages_per_day=1)
    for _ in range(3):
        asyncio.run(t.wait("https://www.reddit.com/"))


# ---------- incremental collection ----------


def collect_with(page, seen, **kwargs):
    session = ScrapeSession(
        FakeContext(page),
        fetch_opts=FetchOptions(retries=0, failure_dir=None),
        human_cfg=HumanConfig(enabled=False),
        throttle=throttle_with(None),
        sleep=no_sleep,
        rng=random.Random(0),
    )
    scroll = HumanConfig(
        scrolls=(1, 1), scroll_px=(1000, 1000), scroll_back_chance=0, mouse_moves=(0, 0)
    )
    return asyncio.run(
        collect_subreddit(session, "puzzles", scroll_cfg=scroll, seen=seen, **kwargs)
    )


def test_stops_when_caught_up_with_stored_posts():
    store = SqliteStore(":memory:")
    store.write("reddit_post", [post(i) for i in range(10, 40)])  # posts from a previous run
    res = collect_with(
        FeedPage(total=100, initial=5, per_load=5),
        seen=lambda pid: store.has("reddit_post", pid),
        limit=100,
        stop_after_seen=10,
    )
    assert res.reason.startswith("caught up") and res.already_seen >= 10
    assert len(res.posts) < 30  # stopped long before the limit: far fewer requests


def test_without_seen_collects_normally():
    res = collect_with(FeedPage(total=100, initial=5, per_load=5), seen=None, limit=30)
    assert res.reason == "limit reached" and res.already_seen == 0
