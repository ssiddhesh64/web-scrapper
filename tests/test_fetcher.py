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


class FakeTextMatches:
    """What page.get_by_text(regex) returns: the visible banners that match."""

    def __init__(self, texts):
        self.texts = texts

    async def count(self):
        return len(self.texts)

    def nth(self, i):
        text = self.texts[i]

        class Match:
            async def is_visible(self):
                return True

            async def inner_text(self):
                return text

        return Match()


class FakePage:
    """Each goto() consumes the next scripted step: (status, url, title, text, headers)
    or an Exception to raise."""

    def __init__(self, steps, banners=(), titles=()):
        self.banners = list(banners)  # texts visible on the page, e.g. inside shadow DOM
        self.titles = list(titles)  # successive title() values (simulates a JS challenge)
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
        if self.titles:
            self.title_ = self.titles.pop(0)
        return self.title_

    def get_by_text(self, pattern):
        return FakeTextMatches([b for b in self.banners if pattern.search(b)])

    async def content(self):
        return f"<html><body>{self.text}</body></html>"

    def locator(self, _):
        return FakeLocator(self)

    async def screenshot(self, path=None, **_):
        if path:
            open(path, "wb").write(b"png")
        return b"top-png"


OK = (200, None, "r/python", "posts " * 100, {})
OPTS = FetchOptions(retries=2, backoff_base=1, failure_dir=None, challenge_timeout_ms=50)


def run_fetch(steps, opts=OPTS, **page_kwargs):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    page = FakePage(steps, **page_kwargs)
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


# ---------- Reddit "network security" block, shadow DOM, JS challenge, screenshots ----------

REDDIT_BLOCK = (
    "Your request has been blocked by network security. "
    "Please try to login with your Reddit account."
)


def test_reddit_network_security_block_page_is_forbidden():
    kind, signals = classify(200, "https://www.reddit.com/r/puzzles/", "Reddit", REDDIT_BLOCK)
    assert kind is BlockKind.FORBIDDEN


def test_visible_banner_on_long_page_is_detected():
    # A normal-looking feed (long text, so body phrases are ignored) with a block banner that
    # only exists in shadow DOM: page.content()/innerText miss it, get_by_text finds it.
    feed = (200, None, "r/puzzles", "post " * 2000, {})
    result, page, sleeps = run_fetch([feed, OK], banners=[REDDIT_BLOCK])
    assert result.block is BlockKind.FORBIDDEN and not result.ok
    assert any("Visible on page" in s for s in result.signals)
    assert page.gotos == 1 and sleeps == []  # hard block: never retried


def test_harmless_visible_text_is_ignored():
    feed = (200, None, "r/puzzles", "post " * 2000, {})
    result, _, _ = run_fetch([feed], banners=["Join the most real place on the internet"])
    assert result.ok


def test_js_challenge_that_clears_is_ok_and_timed():
    challenge = "Loading https://www.reddit.com/r/puzzles/?solution=abc&js_challenge=1"
    result, page, _ = run_fetch(
        [OK], titles=[challenge, challenge, "Reddit - The heart of the internet", "Puzzles"]
    )
    assert result.ok and result.challenge_seconds is not None and not result.challenge_pending
    assert any("JS challenge" in w for w in result.warnings)


def test_js_challenge_that_never_clears_is_captcha_not_retried():
    challenge = "Loading https://www.reddit.com/r/puzzles/?js_challenge=1"
    stuck = (200, None, challenge, "", {})
    result, page, sleeps = run_fetch([stuck, OK])
    assert result.block is BlockKind.CAPTCHA and result.challenge_pending
    assert page.gotos == 1 and sleeps == []


def test_is_challenge_title():
    from web_scrapper.fetcher import is_challenge_title

    assert is_challenge_title("Loading https://www.reddit.com/r/x/?js_challenge=1")
    assert is_challenge_title("Just a moment...")
    assert not is_challenge_title("Loading screens in games - r/gamedev")
    assert not is_challenge_title("Puzzles")


def test_top_screenshot_only_when_asked():
    assert run_fetch([OK])[0].screenshot is None
    shot = run_fetch([OK], FetchOptions(failure_dir=None, screenshot=True))[0].screenshot
    assert shot == b"top-png"


def test_failure_artifacts_include_top_screenshot(tmp_path):
    opts = FetchOptions(retries=0, failure_dir=tmp_path, screenshot=True)
    result, _, _ = run_fetch([(403, None, "Forbidden", "nope", {})], opts)
    folder = next(tmp_path.iterdir())
    assert (folder / "top.png").read_bytes() == b"top-png"
    assert str(folder / "top.png") in result.artifacts
