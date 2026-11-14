"""Load one URL robustly: wait properly, detect blocks, retry only when retrying is safe.

    opts = FetchOptions(wait_for="selector", wait_selector="shreddit-post")
    result = await fetch(page, url, opts)
    if result.ok: ...
    elif result.block is BlockKind.RATE_LIMITED: ...

Retry policy, designed so we don't escalate a soft block into a ban:
- RATE_LIMITED (429), SERVER_ERROR (5xx), NETWORK_ERROR -> retry with backoff + jitter,
  honouring Retry-After.
- FORBIDDEN, CAPTCHA, LOGIN_WALL -> never retried. Trying again immediately only looks more
  bot-like. The caller should stop, cool down or change identity.
"""

import asyncio
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

log = logging.getLogger(__name__)


class BlockKind(StrEnum):
    NONE = "none"
    RATE_LIMITED = "rate_limited"  # 429 / "too many requests"
    FORBIDDEN = "forbidden"  # 401 / 403 / "you've been blocked"
    CAPTCHA = "captcha"  # challenge pages (captcha, Cloudflare "just a moment")
    LOGIN_WALL = "login_wall"  # redirected to a login page
    SERVER_ERROR = "server_error"  # 5xx
    NETWORK_ERROR = "network_error"  # DNS, connection reset, goto timeout, proxy failure


RETRYABLE = {BlockKind.RATE_LIMITED, BlockKind.SERVER_ERROR, BlockKind.NETWORK_ERROR}

# phrase -> kind. Matched against the title + the first part of the visible text, lower-cased.
BLOCK_PHRASES: dict[str, BlockKind] = {
    "too many requests": BlockKind.RATE_LIMITED,
    "you're doing that too much": BlockKind.RATE_LIMITED,
    "whoa there, pardner": BlockKind.FORBIDDEN,  # Reddit's block page
    "you've been blocked": BlockKind.FORBIDDEN,
    "you have been blocked": BlockKind.FORBIDDEN,
    "request has been blocked": BlockKind.FORBIDDEN,
    "access denied": BlockKind.FORBIDDEN,
    "unusual traffic": BlockKind.CAPTCHA,
    "verify you are human": BlockKind.CAPTCHA,
    "are you a robot": BlockKind.CAPTCHA,
    "prove your humanity": BlockKind.CAPTCHA,
    "captcha": BlockKind.CAPTCHA,
    "just a moment...": BlockKind.CAPTCHA,  # Cloudflare interstitial
}
LOGIN_PATH = re.compile(r"/(login|signin|sign-in|account/login)(/|\?|$)", re.IGNORECASE)
# Block/challenge pages are short. Phrases in the body only count when the page is this small,
# so a real Reddit thread that *mentions* "captcha" isn't flagged. Title matches always count.
SHORT_PAGE_CHARS = 3_000
NAV_STATUS_JS = "() => performance.getEntriesByType('navigation')[0]?.responseStatus ?? null"


@dataclass(frozen=True)
class FetchOptions:
    wait_until: str = "domcontentloaded"  # goto: commit | domcontentloaded | load | networkidle
    wait_for: str = "networkidle"  # then: networkidle | selector | none
    wait_selector: str = ""
    timeout_ms: int = 30_000
    settle_ms: int = 0  # extra pause after the wait (lets late JS render)
    retries: int = 2  # extra attempts after the first, for RETRYABLE outcomes only
    backoff_base: float = 5.0  # seconds; doubles each attempt, with jitter
    backoff_max: float = 120.0  # cap per wait. A longer Retry-After means give up instead
    failure_dir: Path | None = Path("diagnostics/failures")  # None = don't save artifacts


@dataclass
class FetchResult:
    url: str
    final_url: str = ""
    status: int | None = None
    title: str = ""
    html: str = ""
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    block: BlockKind = BlockKind.NONE
    signals: list[str] = field(default_factory=list)  # human-readable reasons for `block`
    warnings: list[str] = field(default_factory=list)  # non-fatal (e.g. wait timed out)
    error: str | None = None  # last exception, if the page never loaded
    attempts: int = 0
    elapsed: float = 0.0
    artifacts: list[str] = field(default_factory=list)  # saved screenshot/html on failure

    @property
    def blocked(self) -> bool:
        return self.block is not BlockKind.NONE

    @property
    def ok(self) -> bool:
        return self.status is not None and self.status < 400 and not self.blocked


# ---------- pure helpers (no browser needed, easy to unit-test) ----------


def classify(
    status: int | None, final_url: str, title: str, text: str
) -> tuple[BlockKind, list[str]]:
    """Decide whether a loaded page is a block page. The first matching rule wins."""
    signals: list[str] = []
    kind = BlockKind.NONE

    def hit(k: BlockKind, why: str) -> None:
        nonlocal kind
        signals.append(why)
        if kind is BlockKind.NONE:
            kind = k

    if status == 429:
        hit(BlockKind.RATE_LIMITED, "HTTP 429: rate limited")
    elif status in (401, 403):
        hit(BlockKind.FORBIDDEN, f"HTTP {status}: access denied")
    elif status is not None and status >= 500:
        hit(BlockKind.SERVER_ERROR, f"HTTP {status}: server error")

    if LOGIN_PATH.search(urlparse(final_url).path or ""):
        hit(BlockKind.LOGIN_WALL, f"Redirected to a login page ({urlparse(final_url).path})")

    title_l = title.lower()
    body_l = text.lower() if len(text) <= SHORT_PAGE_CHARS else ""
    for phrase, k in BLOCK_PHRASES.items():
        if phrase in title_l:
            hit(k, f'Title contains "{phrase}"')
        elif phrase in body_l:
            hit(k, f'Short page contains "{phrase}"')
    return kind, signals


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Retry-After header -> seconds to wait. Accepts '120' or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    now = time.time() if now is None else now
    return max(0.0, when.timestamp() - now)


def backoff_delay(attempt: int, base: float, cap: float, rng: random.Random = random) -> float:
    """Exponential backoff with 'equal jitter': half fixed, half random.

    attempt 1 -> 2.5-5s, attempt 2 -> 5-10s, attempt 3 -> 10-20s ... (base=5), capped at `cap`.
    The fixed half guarantees a real pause; the random half stops retries being clockwork.
    """
    ceiling = min(cap, base * 2 ** (attempt - 1))
    return ceiling / 2 + rng.uniform(0, ceiling / 2)


# ---------- the fetch itself ----------


async def _document_status(page: Page) -> int | None:
    """HTTP status of the document currently shown (Navigation Timing API)."""
    try:
        status = await page.evaluate(NAV_STATUS_JS)
    except PlaywrightError:
        return None
    return int(status) if status else None


async def _goto(page: Page, url: str, opts: FetchOptions, result: FetchResult):
    """page.goto(), but keep the HTTP response even if goto itself raises.

    Firefox raises NS_ERROR_NET_EMPTY_RESPONSE for e.g. a 403/429 with an empty body, even
    though the response arrived. Without this, a 403 would be misread as a (retryable!)
    network error. Returns (response, recovered_from_error).
    """
    seen = []

    def on_response(r):
        if r.request.is_navigation_request() and r.frame == page.main_frame:
            seen.append(r)

    page.on("response", on_response)
    try:
        return await page.goto(url, wait_until=opts.wait_until, timeout=opts.timeout_ms), False
    except PlaywrightError as e:
        if not seen:
            raise
        result.warnings.append(
            f"goto raised {str(e).splitlines()[0]!r} but an HTTP {seen[-1].status} response "
            "arrived; classified from that response"
        )
        return seen[-1], True
    finally:
        page.remove_listener("response", on_response)


async def _load_once(page: Page, url: str, opts: FetchOptions, result: FetchResult) -> None:
    response, recovered = await _goto(page, url, opts, result)
    if response is not None:
        result.status = response.status
        result.headers = dict(response.headers)
    else:
        # Same-document navigation (e.g. only the #anchor changed): Playwright returns no
        # response. Use the status of the document we're on instead.
        result.status = await _document_status(page)
        result.warnings.append(
            "Same-document navigation (no new HTTP response); status is from the current document"
        )
    if recovered:
        # The page is a browser error page: don't wait on it, just record what we can.
        result.final_url = response.url
        try:
            result.title = await page.title()
            result.html = await page.content()
        except PlaywrightError:
            pass
        return
    try:
        if opts.wait_for == "selector" and opts.wait_selector:
            await page.wait_for_selector(opts.wait_selector, timeout=opts.timeout_ms)
        elif opts.wait_for == "networkidle":
            await page.wait_for_load_state("networkidle", timeout=opts.timeout_ms)
    except PlaywrightTimeoutError:
        result.warnings.append(f"Timed out waiting for {opts.wait_for}; used what had loaded")
    if opts.settle_ms:
        await page.wait_for_timeout(opts.settle_ms)
    result.final_url = page.url
    result.title = await page.title()
    result.html = await page.content()
    result.text = await page.locator("body").inner_text()


async def _save_artifacts(page: Page, result: FetchResult, folder: Path) -> None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    host = urlparse(result.url).netloc.replace(":", "_") or "page"
    out = folder / f"{stamp}-{host}-{result.block.value}"
    out.mkdir(parents=True, exist_ok=True)
    try:
        await page.screenshot(path=out / "screenshot.png", full_page=True)
        result.artifacts.append(str(out / "screenshot.png"))
    except PlaywrightError as e:
        result.warnings.append(f"Could not save screenshot: {e}")
    html = result.html
    if not html:
        try:
            html = await page.content()
        except PlaywrightError:
            html = ""
    (out / "page.html").write_text(html, encoding="utf-8")
    summary = f"url: {result.url}\nfinal_url: {result.final_url}\nstatus: {result.status}\n"
    summary += f"block: {result.block}\nerror: {result.error}\nsignals:\n"
    summary += "".join(f"  - {s}\n" for s in result.signals)
    (out / "summary.txt").write_text(summary, encoding="utf-8")
    result.artifacts += [str(out / "page.html"), str(out / "summary.txt")]


async def fetch(
    page: Page,
    url: str,
    opts: FetchOptions = FetchOptions(),
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> FetchResult:
    """Navigate `page` to `url` and report what happened. Never raises for HTTP or network
    problems; check `result.ok` / `result.block`. `sleep` is injectable for tests."""
    started = time.perf_counter()
    total_attempts = 1 + max(0, opts.retries)
    result = FetchResult(url=url)

    for attempt in range(1, total_attempts + 1):
        result = FetchResult(url=url, attempts=attempt)
        try:
            await _load_once(page, url, opts, result)
            result.block, result.signals = classify(
                result.status, result.final_url, result.title, result.text
            )
        except PlaywrightError as e:  # includes goto TimeoutError
            result.error = f"{type(e).__name__}: {str(e).splitlines()[0]}"
            result.block = BlockKind.NETWORK_ERROR
            result.signals = [result.error]

        if not result.blocked:
            log.info("fetched %s -> %s (attempt %d)", url, result.status, attempt)
            break
        if result.block not in RETRYABLE:
            log.warning("blocked (%s) at %s; not retrying: %s", result.block, url, result.signals)
            break
        if attempt == total_attempts:
            log.warning("giving up on %s after %d attempts (%s)", url, attempt, result.block)
            break

        delay = backoff_delay(attempt, opts.backoff_base, opts.backoff_max)
        retry_after = parse_retry_after(result.headers.get("retry-after"))
        if retry_after is not None:
            if retry_after > opts.backoff_max:
                result.warnings.append(
                    f"Retry-After is {retry_after:.0f}s (> backoff_max); "
                    "giving up instead of waiting"
                )
                break
            delay = max(delay, retry_after)
        log.warning(
            "%s at %s (attempt %d/%d); retrying in %.1fs",
            result.block,
            url,
            attempt,
            total_attempts,
            delay,
        )
        await sleep(delay)

    result.elapsed = round(time.perf_counter() - started, 2)
    if result.blocked and opts.failure_dir is not None:
        await _save_artifacts(page, result, opts.failure_dir)
    return result
