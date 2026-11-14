"""Fetcher tests. No browser or network: a FakePage plays back scripted responses."""

import asyncio
import random
from email.utils import formatdate

import pytest
from playwright.async_api import Error as PlaywrightError

from web_scrapper.fetcher import (
    BlockKind,
    FetchOptions,
    backoff_delay,
    classify,
    fetch,
    parse_retry_after,
)

# ---------- classify ----------


@pytest.mark.parametrize(
    ("status", "url", "title", "text", "expected"),
    [
        (200, "https://x.com/r/python/", "r/python", "lots of posts", BlockKind.NONE),
        (429, "https://x.com/", "", "", BlockKind.RATE_LIMITED),
        (403, "https://x.com/", "", "", BlockKind.FORBIDDEN),
        (503, "https://x.com/", "", "", BlockKind.SERVER_ERROR),
        (200, "https://old.reddit.com/login/?dest=x", "Welcome", "", BlockKind.LOGIN_WALL),
        (200, "https://x.com/", "Just a moment...", "", BlockKind.CAPTCHA),
        (200, "https://x.com/", "Blocked", "whoa there, pardner!", BlockKind.FORBIDDEN),
    ],
)
def test_classify(status, url, title, text, expected):
    kind, signals = classify(status, url, title, text)
    assert kind is expected
    assert bool(signals) == (expected is not BlockKind.NONE)


def test_classify_ignores_phrases_inside_long_real_pages():
    # A real thread *about* captchas must not be flagged as a captcha page.
    long_text = "How do I solve a captcha in Playwright? " + "discussion " * 1000
    assert classify(200, "https://x.com/r/python/", "r/python", long_text)[0] is BlockKind.NONE


def test_classify_login_word_in_path_segment_only():
    assert classify(200, "https://x.com/r/loginhelp/", "", "")[0] is BlockKind.NONE


# ---------- retry-after / backoff ----------


def test_parse_retry_after_seconds_and_date():
    assert parse_retry_after("120") == 120
    now = 1_700_000_000
    assert parse_retry_after(formatdate(now + 30, usegmt=True), now=now) == pytest.approx(30)
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None


def test_backoff_grows_and_is_capped():
    rng = random.Random(0)
    for attempt, lo, hi in [(1, 2.5, 5), (2, 5, 10), (3, 10, 20)]:
        assert lo <= backoff_delay(attempt, 5, 120, rng) <= hi
    assert backoff_delay(20, 5, 120, rng) <= 120


# ---------- fetch with a fake page ----------


class FakeResponse:
    def __init__(self, status, headers=None, url="", frame=None):
        self.status = status
        self.headers = headers or {}
        self.url = url
        self.frame = frame
        self.request = type("Req", (), {"is_navigation_request": lambda self: True})()


class EmptyBodyResponse(Exception):
    """Script step: an HTTP response arrives, then goto raises (Firefox empty-body case)."""

    def __init__(self, status):
        self.status = status


class FakeLocator:
    def __init__(self, page):
        self.page = page

    async def inner_text(self):
        return self.page.text


class FakePage:
    """Each goto() consumes the next scripted step: (status, url, title, text, headers)
    or an Exception to raise."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.url = "about:blank"
        self.title_ = self.text = ""
        self.gotos = 0
        self.main_frame = object()
        self._handlers = []

    def on(self, event, handler):
        if event == "response":
            self._handlers.append(handler)

    def remove_listener(self, event, handler):
        self._handlers.remove(handler)

    async def goto(self, url, **_):
        self.gotos += 1
        step = self.steps.pop(0)
        if isinstance(step, EmptyBodyResponse):
            for h in self._handlers:
                h(FakeResponse(step.status, url=url, frame=self.main_frame))
            raise PlaywrightError("Page.goto: NS_ERROR_NET_EMPTY_RESPONSE")
        if isinstance(step, Exception):
            raise step
        status, final_url, self.title_, self.text, headers = step
        self.url = final_url or url
        if status == "same-document":  # Playwright returns None for #anchor navigations
            return None
        return FakeResponse(status, headers)

    async def evaluate(self, _js):
        return 200  # Navigation Timing status of the current document

    async def wait_for_load_state(self, *_, **__):
        pass

    async def wait_for_selector(self, *_, **__):
        pass

    async def wait_for_timeout(self, *_):
        pass

    async def title(self):
        return self.title_

    async def content(self):
        return f"<html><body>{self.text}</body></html>"

    def locator(self, _):
        return FakeLocator(self)

    async def screenshot(self, **_):
        pass


OK = (200, None, "r/python", "posts " * 100, {})
OPTS = FetchOptions(retries=2, backoff_base=1, failure_dir=None)


def run_fetch(steps, opts=OPTS):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    page = FakePage(steps)
    result = asyncio.run(fetch(page, "https://x.com/r/python/", opts, sleep=fake_sleep))
    return result, page, sleeps


def test_success_first_try():
    result, page, sleeps = run_fetch([OK])
    assert result.ok and result.attempts == 1 and page.gotos == 1 and sleeps == []


def test_429_is_retried_then_succeeds():
    result, page, sleeps = run_fetch([(429, None, "", "", {}), OK])
    assert result.ok and result.attempts == 2 and len(sleeps) == 1


def test_retry_after_header_is_honoured():
    result, _, sleeps = run_fetch([(429, None, "", "", {"retry-after": "7"}), OK])
    assert result.ok and sleeps[0] >= 7


def test_huge_retry_after_gives_up_instead_of_waiting():
    result, page, sleeps = run_fetch([(429, None, "", "", {"retry-after": "3600"}), OK])
    assert result.block is BlockKind.RATE_LIMITED and page.gotos == 1 and sleeps == []
    assert any("Retry-After" in w for w in result.warnings)


@pytest.mark.parametrize(
    "step",
    [
        (403, None, "", "", {}),
        (200, "https://old.reddit.com/login/?x=1", "Welcome to Reddit", "", {}),
        (200, None, "Just a moment...", "", {}),
    ],
)
def test_hard_blocks_are_never_retried(step):
    result, page, sleeps = run_fetch([step, OK])
    assert not result.ok and page.gotos == 1 and sleeps == []


def test_network_error_retried_until_exhausted():
    err = PlaywrightError("net::ERR_CONNECTION_RESET")
    result, page, sleeps = run_fetch([err, err, err])
    assert result.block is BlockKind.NETWORK_ERROR and result.error
    assert page.gotos == 3 and len(sleeps) == 2


def test_failure_artifacts_saved(tmp_path):
    opts = FetchOptions(retries=0, failure_dir=tmp_path)
    result, _, _ = run_fetch([(403, None, "Forbidden", "nope", {})], opts)
    saved = list(tmp_path.iterdir())
    assert len(saved) == 1 and (saved[0] / "page.html").exists()
    assert (saved[0] / "summary.txt").read_text().count("forbidden") >= 1
    assert result.artifacts


def test_same_document_navigation_uses_current_document_status():
    result, _, _ = run_fetch(
        [("same-document", "https://x.com/#projects", "t", "posts " * 100, {})]
    )
    assert result.ok and result.status == 200
    assert any("Same-document" in w for w in result.warnings)


def test_empty_body_403_is_forbidden_not_a_retryable_network_error():
    result, page, sleeps = run_fetch([EmptyBodyResponse(403), OK])
    assert result.block is BlockKind.FORBIDDEN and result.status == 403
    assert page.gotos == 1 and sleeps == []  # NOT retried
    assert any("NS_ERROR_NET_EMPTY_RESPONSE" in w for w in result.warnings)


def test_empty_body_429_is_rate_limited_and_retried():
    result, page, _ = run_fetch([EmptyBodyResponse(429), OK])
    assert result.ok and page.gotos == 2
