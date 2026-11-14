"""human.py + session.py tests with fake pages (no browser)."""

import asyncio
import math
import random
import statistics

import pytest

from web_scrapper.config import HumanConfig, ThrottleConfig
from web_scrapper.fetcher import FetchOptions
from web_scrapper.human import browse, lognormal_between
from web_scrapper.session import ScrapeSession
from web_scrapper.throttle import DomainBlocked

# ---------- lognormal_between ----------


def test_lognormal_stays_in_range_and_centres_on_geometric_mean():
    rng = random.Random(42)
    xs = [lognormal_between(2, 8, rng) for _ in range(5000)]
    assert min(xs) >= 2 and max(xs) <= 8
    assert statistics.median(xs) == pytest.approx(math.sqrt(2 * 8), rel=0.05)


def test_lognormal_degenerate_range():
    assert lognormal_between(3, 3) == 3


# ---------- browse ----------


class FakeMouse:
    def __init__(self):
        self.moves, self.wheels = [], []

    async def move(self, x, y):
        self.moves.append((x, y))

    async def wheel(self, dx, dy):
        self.wheels.append(dy)


class FakePage:
    """Enough of Page for fetch() + human.browse()."""

    def __init__(self, steps=None):
        self.mouse = FakeMouse()
        self.steps = list(steps or [])
        self.url = "about:blank"
        self.title_ = self.text = ""
        self.gotos = 0

    def is_closed(self):
        return False

    def get_by_text(self, _pattern):
        class NoMatches:
            async def count(self):
                return 0

        return NoMatches()

    def on(self, *_):
        pass

    def remove_listener(self, *_):
        pass

    async def evaluate(self, _js):
        return [1200, 800, 5000]

    async def goto(self, url, **_):
        self.gotos += 1
        status, title, text = self.steps.pop(0)
        self.url, self.title_, self.text = url, title, text
        return type("R", (), {"status": status, "headers": {}})()

    async def wait_for_load_state(self, *_, **__):
        pass

    async def title(self):
        return self.title_

    async def content(self):
        return "<html></html>"

    def locator(self, _):
        page = self

        class L:
            async def inner_text(self):
                return page.text

        return L()


async def no_sleep(_):
    pass


def test_browse_scrolls_within_config_and_pauses():
    page, slept = FakePage(), []

    async def sleep(s):
        slept.append(s)

    cfg = HumanConfig(scrolls=(2, 3), mouse_moves=(1, 1), scroll_back_chance=0)
    did = asyncio.run(browse(page, cfg, rng=random.Random(0), sleep=sleep))
    assert 2 <= did["scrolls"] <= 3
    assert len(page.mouse.wheels) == did["scrolls"] and all(d > 0 for d in page.mouse.wheels)
    assert len(page.mouse.moves) == 1
    assert len(slept) == did["scrolls"] + 1  # one pause per scroll + the reading pause
    x, y = page.mouse.moves[0]
    assert 0 < x < 1200 and 0 < y < 800


def test_browse_disabled_does_nothing():
    page = FakePage()
    assert asyncio.run(browse(page, HumanConfig(enabled=False), sleep=no_sleep)) == {}
    assert page.mouse.wheels == []


def test_human_config_validation():
    with pytest.raises(ValueError):
        HumanConfig(scrolls=(5, 1))


# ---------- ScrapeSession ----------


class FakeContext:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page


def make_session(steps, **throttle):
    page = FakePage(steps)
    session = ScrapeSession(
        FakeContext(page),
        fetch_opts=FetchOptions(retries=0, failure_dir=None),
        human_cfg=HumanConfig(scrolls=(1, 1), mouse_moves=(0, 0)),
        throttle=ThrottleConfig(break_seconds=(0, 0), **throttle),
        sleep=no_sleep,
        rng=random.Random(0),
    )
    return session, page


OK = (200, "r/python", "posts " * 200)


def test_session_reuses_one_tab_and_browses_ok_pages():
    session, page = make_session([OK, OK])

    async def go():
        a = await session.visit("https://www.reddit.com/r/python/")
        b = await session.visit("https://www.reddit.com/r/learnpython/")
        return a, b

    a, b = asyncio.run(go())
    assert a.result.ok and b.result.ok
    assert a.waited == 0 and b.waited >= 4  # throttle gap applied on the 2nd visit
    assert a.browse["scrolls"] == 1
    assert page.gotos == 2 and len(session.visits) == 2


def test_session_stops_after_hard_block():
    session, page = make_session([(403, "Forbidden", "no"), OK])

    async def go():
        v = await session.visit("https://www.reddit.com/r/python/")
        assert not v.result.ok and v.browse == {}  # no browsing on a block page
        await session.visit("https://www.reddit.com/r/other/")

    with pytest.raises(DomainBlocked):
        asyncio.run(go())
    assert page.gotos == 1  # the second URL was never requested
