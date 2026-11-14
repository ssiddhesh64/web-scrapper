"""Throttle tests with a fake clock: sleep() just advances time, so nothing actually waits."""

import asyncio
import random

import pytest

from web_scrapper.config import ThrottleConfig
from web_scrapper.fetcher import BlockKind
from web_scrapper.throttle import (
    DomainBlocked,
    SessionBudgetExceeded,
    Throttle,
    site_key,
)

URL = "https://www.reddit.com/r/python/"


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def make(**cfg):
    clock = FakeClock()
    base = {"break_seconds": (0.0, 0.0)}  # no breaks unless a test asks for them
    t = Throttle(
        ThrottleConfig(**(base | cfg)), clock=clock, sleep=clock.sleep, rng=random.Random(1)
    )
    return t, clock


def run(coro):
    return asyncio.run(coro)


def test_site_key_groups_subdomains():
    assert site_key("https://old.reddit.com/x") == site_key("https://www.reddit.com/y")
    assert site_key("https://reddit.com") == "reddit.com"
    assert site_key("http://127.0.0.1:8799/x") == "127.0.0.1"
    assert site_key("http://10.0.0.1/") != site_key("http://192.168.0.1/")
    assert site_key("http://[::1]:8765/") == "::1"


def test_first_request_is_immediate_then_gap_enforced():
    t, clock = make(min_interval=4, max_interval=10)
    assert run(t.wait(URL)) == 0
    waited = run(t.wait(URL))
    assert 4 <= waited <= 10


def test_gap_already_elapsed_means_no_wait():
    t, clock = make(min_interval=4, max_interval=10)
    run(t.wait(URL))
    clock.t += 60  # we spent a minute parsing
    assert run(t.wait(URL)) == 0


def test_different_sites_dont_wait_for_each_other():
    t, _ = make(min_interval=4, max_interval=10)
    run(t.wait(URL))
    assert run(t.wait("https://example.com/")) == 0


def test_gaps_are_not_constant():
    t, _ = make(min_interval=4, max_interval=10)
    run(t.wait(URL))
    gaps = {round(run(t.wait(URL)), 3) for _ in range(10)}
    assert len(gaps) > 5


def test_session_budget():
    t, _ = make(max_pages_per_session=2, min_interval=0, max_interval=0)
    run(t.wait(URL))
    run(t.wait(URL))
    with pytest.raises(SessionBudgetExceeded):
        run(t.wait(URL))


def test_breaks_happen():
    t, clock = make(min_interval=0, max_interval=0, break_every=(3, 3), break_seconds=(50, 60))
    waits = [run(t.wait(URL)) for _ in range(5)]
    assert waits[:3] == [0, 0, 0]
    assert 50 <= waits[3] <= 60  # break before page 4
    assert waits[4] == 0


def test_rate_limit_cooldown_doubles_then_resets():
    t, clock = make(min_interval=0, max_interval=0, cooldown_base=60, cooldown_max=900)
    run(t.wait(URL))
    t.record(URL, BlockKind.RATE_LIMITED)
    assert run(t.wait(URL)) == pytest.approx(60)
    t.record(URL, BlockKind.RATE_LIMITED)
    assert run(t.wait(URL)) == pytest.approx(120)
    t.record(URL, BlockKind.NONE)  # success resets
    t.record(URL, BlockKind.RATE_LIMITED)
    assert run(t.wait(URL)) == pytest.approx(60)


def test_cooldown_is_capped():
    t, _ = make(
        min_interval=0,
        max_interval=0,
        cooldown_base=60,
        cooldown_max=100,
        max_consecutive_failures=10,
    )
    for _ in range(4):
        run(t.wait(URL))
        t.record(URL, BlockKind.SERVER_ERROR)
    assert run(t.wait(URL)) == pytest.approx(100)


@pytest.mark.parametrize("kind", [BlockKind.FORBIDDEN, BlockKind.CAPTCHA])
def test_hard_block_stops_site_immediately(kind):
    t, _ = make()
    run(t.wait(URL))
    t.record(URL, kind)
    assert t.is_stopped(URL)
    with pytest.raises(DomainBlocked):
        run(t.wait("https://old.reddit.com/"))  # same site, other subdomain
    assert run(t.wait("https://example.com/")) == 0  # other sites unaffected


def test_hard_block_can_be_configured_to_cool_down_instead():
    t, _ = make(stop_on_hard_block=False)
    run(t.wait(URL))
    t.record(URL, BlockKind.FORBIDDEN)
    assert not t.is_stopped(URL)


def test_circuit_breaker_on_consecutive_failures():
    t, _ = make(min_interval=0, max_interval=0, max_consecutive_failures=3)
    for kind in (BlockKind.LOGIN_WALL, BlockKind.NETWORK_ERROR):
        run(t.wait(URL))
        t.record(URL, kind)
    assert not t.is_stopped(URL)
    run(t.wait(URL))
    t.record(URL, BlockKind.LOGIN_WALL)
    assert t.is_stopped(URL)


def test_config_validation():
    with pytest.raises(ValueError):
        ThrottleConfig(min_interval=5, max_interval=1)
