"""Reddit extractor + collector tests. No browser, no network."""

import asyncio
import random
from datetime import UTC, datetime
from pathlib import Path

import pytest

from web_scrapper.config import HumanConfig, ThrottleConfig
from web_scrapper.extractors import EXTRACTORS, has_more_posts, parse_listing, subreddit_url
from web_scrapper.fetcher import FetchOptions
from web_scrapper.reddit import collect_subreddit
from web_scrapper.session import ScrapeSession
from web_scrapper.throttle import Throttle

FIXTURE = (Path(__file__).parent / "fixtures" / "reddit_listing.html").read_text()

# ---------- parse_listing ----------


def test_parses_posts_in_order_skipping_ads_duplicates_and_incomplete():
    posts = parse_listing(FIXTURE)
    assert [p.id for p in posts] == ["t3_aaa111", "t3_bbb222"]


def test_full_post_fields():
    p = parse_listing(FIXTURE)[0]
    assert p.title == "A tricky grid puzzle"  # whitespace normalised
    assert p.subreddit == "puzzles"
    assert p.permalink == "https://www.reddit.com/r/puzzles/comments/aaa111/a_tricky_grid_puzzle/"
    assert p.author == "alice" and p.score == 17 and p.comment_count == 12
    assert p.upvote_ratio == 0.9
    assert p.created_at == datetime(2026, 11, 13, 19, 18, 22, 11000, tzinfo=UTC)
    assert p.post_type == "image" and p.url == "https://i.redd.it/example1.png"
    assert p.domain == "i.redd.it" and p.flair == "[SOLVED]" and p.language == "en"
    assert not p.nsfw and not p.spoiler


def test_sparse_post_is_tolerant():
    p = parse_listing(FIXTURE)[1]
    assert p.author is None  # deleted account -> empty attribute
    assert p.score == -3 and p.comment_count == 0
    assert p.created_at is None  # unparseable timestamp doesn't crash
    assert p.subreddit == "puzzles"  # from subreddit-prefixed-name
    assert p.url == p.permalink  # self post links to itself (relative href made absolute)
    assert p.nsfw and p.spoiler  # boolean attributes


def test_to_dict_is_json_ready():
    d = parse_listing(FIXTURE)[0].to_dict()
    assert d["created_at"] == "2026-11-13T19:18:22.011000+00:00"
    assert EXTRACTORS["reddit_listing"](FIXTURE, "https://www.reddit.com/r/puzzles/")[0] == d


def test_has_more_posts():
    assert has_more_posts(FIXTURE)
    assert not has_more_posts("<html><shreddit-post></shreddit-post></html>")


def test_empty_or_unrelated_html():
    assert parse_listing("") == []
    assert parse_listing("<html><body><p>Your request has been blocked</p></body></html>") == []


# ---------- subreddit_url ----------


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("puzzles",), "https://www.reddit.com/r/puzzles/hot/"),
        (("r/puzzles", "new"), "https://www.reddit.com/r/puzzles/new/"),
        (("/r/AskReddit/", "top", "week"), "https://www.reddit.com/r/AskReddit/top/?t=week"),
    ],
)
def test_subreddit_url(args, expected):
    assert subreddit_url(*args) == expected


@pytest.mark.parametrize(
    "args",
    [("bad name!",), ("x",), ("puzzles", "latest"), ("puzzles", "new", "week"), ("p1", "top", "x")],
)
def test_subreddit_url_rejects_bad_input(args):
    with pytest.raises(ValueError):
        subreddit_url(*args)


# ---------- collect_subreddit (infinite scroll) ----------


def post_html(i: int) -> str:
    return (
        f'<shreddit-post id="t3_p{i}" post-title="Post {i}" '
        f'permalink="/r/puzzles/comments/p{i}/post_{i}/" score="{i}" comment-count="1" '
        f'subreddit-name="puzzles"></shreddit-post>'
    )


class FakeMouse:
    def __init__(self, page):
        self.page = page

    async def move(self, *_):
        pass

    async def wheel(self, _dx, dy):
        self.page.scroll(dy)


class FeedPage:
    """A feed that behaves like Reddit's: every post is POST_PX tall, and the next batch of
    `per_load` posts only loads when the viewport gets near the bottom of what's loaded.
    Optionally shows a block banner after `block_after` scroll steps."""

    POST_PX, VIEWPORT = 600, 800

    def __init__(self, total=12, initial=3, per_load=2, block_after=None):
        self.total, self.loaded, self.per_load = total, min(initial, total), per_load
        self.block_after = block_after
        self.scroll_y = 0
        self.steps = 0
        self.mouse = FakeMouse(self)
        self.url = "about:blank"
        self.main_frame = object()

    def height(self):
        return self.loaded * self.POST_PX

    def scroll(self, dy):
        self.steps += 1
        self.scroll_y = max(0, min(self.scroll_y + dy, max(0, self.height() - self.VIEWPORT)))
        near_end = self.scroll_y + self.VIEWPORT >= self.height() - 2 * self.VIEWPORT
        if near_end and self.loaded < self.total:
            self.loaded = min(self.total, self.loaded + self.per_load)

    # --- what fetch() / collect_subreddit() need
    def is_closed(self):
        return False

    def on(self, *_):
        pass

    def remove_listener(self, *_):
        pass

    async def goto(self, url, **_):
        self.url = url
        return type("R", (), {"status": 200, "headers": {}})()

    async def wait_for_load_state(self, *_, **__):
        pass

    async def wait_for_selector(self, *_, **__):
        pass

    async def wait_for_timeout(self, *_):
        pass

    async def title(self):
        return "r/puzzles"

    async def content(self):
        more = '<faceplate-partial id="partial-more-posts-x"></faceplate-partial>'
        posts = "".join(post_html(i) for i in range(self.loaded))
        return "<html>" + posts + (more if self.loaded < self.total else "")

    def locator(self, _):
        class L:
            async def inner_text(self):
                return "feed " * 1000

        return L()

    async def evaluate(self, js):
        if "scrollY" in js:
            return [self.scroll_y + self.VIEWPORT, self.height()]
        if js.strip() == "() => innerHeight":
            return self.VIEWPORT
        return [1200, self.VIEWPORT, self.height()]

    def get_by_text(self, _pattern):
        blocked = self.block_after is not None and self.steps >= self.block_after

        class Matches:
            async def count(self):
                return 1 if blocked else 0

            def nth(self, _i):
                class M:
                    async def is_visible(self):
                        return True

                    async def inner_text(self):
                        return "Your request has been blocked by network security."

                return M()

        return Matches()

    async def screenshot(self, **_):
        return b""


class FakeContext:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page


async def no_sleep(_):
    pass


def collect(page, **kwargs):
    session = ScrapeSession(
        FakeContext(page),
        fetch_opts=FetchOptions(retries=0, failure_dir=None),
        human_cfg=HumanConfig(enabled=False),
        throttle=Throttle(
            ThrottleConfig(min_interval=0, max_interval=0, break_seconds=(0, 0)), sleep=no_sleep
        ),
        sleep=no_sleep,
        rng=random.Random(0),
    )
    scroll = HumanConfig(
        scrolls=(1, 1), scroll_px=(1000, 1000), scroll_back_chance=0, mouse_moves=(0, 0)
    )
    return asyncio.run(collect_subreddit(session, "puzzles", scroll_cfg=scroll, **kwargs)), session


def test_collects_until_limit():
    res, _ = collect(FeedPage(total=50, initial=3, per_load=2), limit=10)
    assert res.reason == "limit reached" and len(res.posts) == 10
    assert [p.id for p in res.posts] == [f"t3_p{i}" for i in range(10)]  # feed order, no dupes
    assert res.ok and res.log and res.log[-1]["posts"] >= 10


def test_long_first_page_needs_several_scrolls_before_more_load():
    # Regression (found live on r/puzzles): ~25 posts arrive with the first load, so several
    # rounds pass before we're near the bottom. Those rounds must NOT count as stale.
    res, _ = collect(FeedPage(total=60, initial=25, per_load=5), limit=40, stale_rounds=3)
    assert res.reason == "limit reached" and len(res.posts) == 40
    assert any(not row["near_bottom"] and row["new"] == 0 for row in res.log)


def test_stops_at_end_of_feed():
    res, _ = collect(FeedPage(total=7), limit=100)
    assert res.reason == "end of feed" and len(res.posts) == 7


def test_stops_when_bottom_reached_and_nothing_loads():
    page = FeedPage(total=100, initial=3, per_load=0)  # placeholder stays, nothing ever loads
    res, _ = collect(page, limit=50, stale_rounds=3)
    assert res.reason.startswith("no new posts after 3") and len(res.posts) == 3


def test_max_rounds():
    res, _ = collect(FeedPage(total=1000, initial=3, per_load=1), limit=500, max_rounds=5)
    assert res.reason.startswith("max_rounds") and res.rounds == 5


def test_block_banner_while_scrolling_stops_and_stops_the_site():
    res, session = collect(FeedPage(total=100, block_after=2), limit=50)
    assert res.reason.startswith("blocked while scrolling") and not res.ok
    assert len(res.posts) > 0  # what we got before the block is kept
    assert session.throttle.is_stopped("https://www.reddit.com/r/puzzles/")


def test_throttle_budget_ends_collection_cleanly():
    page = FeedPage(total=100)
    session = ScrapeSession(
        FakeContext(page),
        fetch_opts=FetchOptions(retries=0, failure_dir=None),
        human_cfg=HumanConfig(enabled=False),
        throttle=Throttle(
            ThrottleConfig(
                min_interval=0, max_interval=0, break_seconds=(0, 0), max_pages_per_session=3
            ),
            sleep=no_sleep,
        ),
        sleep=no_sleep,
    )
    res = asyncio.run(collect_subreddit(session, "puzzles", limit=50))
    assert res.reason.startswith("throttle") and res.rounds == 2  # 1 load + 2 rounds = budget 3


def test_throttle_paces_batches_not_every_scroll():
    res, session = collect(FeedPage(total=60, initial=25, per_load=5), limit=40)
    batches = sum(1 for row in res.log if row["new"] > 0)
    # 1 page load + 1 wait before the first round + 1 wait after each batch (the last batch,
    # which reaches the limit, ends the loop before another wait)
    assert session.throttle.pages <= 2 + batches
    assert session.throttle.pages < res.rounds  # far fewer than one wait per scroll


def test_default_max_rounds_scales_with_limit():
    res, _ = collect(FeedPage(total=10_000, initial=3, per_load=0), limit=100, stale_rounds=999)
    assert res.reason == "max_rounds (64) reached"
