"""Human-like behaviour on a loaded page: reading pauses, scrolling, idle mouse moves.

Camoufox's `humanize=True` already makes each cursor *movement* look human (curved path,
realistic speed). This module decides *what* to do and *when*: how long to "read", how far
and how often to scroll, when to drift the mouse. Bots are usually caught by timing
(perfectly regular, impossibly fast), not by any single action.
"""

import asyncio
import math
import random
from collections.abc import Awaitable, Callable

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from .config import HumanConfig

Sleep = Callable[[float], Awaitable[None]]

VIEWPORT_JS = "() => [innerWidth, innerHeight, document.documentElement.scrollHeight]"


def lognormal_between(lo: float, hi: float, rng: random.Random = random) -> float:
    """A random duration in [lo, hi], log-normally distributed.

    Human delays aren't uniform: most are near a typical value, a few are much longer.
    The median sits at the geometric mean of lo and hi, and about 95% of draws fall inside
    the range naturally; the rest are clamped.
    """
    if hi <= lo:
        return lo
    lo_ = max(lo, 0.01)
    mu = (math.log(lo_) + math.log(hi)) / 2
    sigma = (math.log(hi) - math.log(lo_)) / 4
    return min(hi, max(lo, rng.lognormvariate(mu, sigma)))


def randint_between(bounds: tuple[int, int], rng: random.Random = random) -> int:
    return rng.randint(int(bounds[0]), int(bounds[1]))


async def pause(lo: float, hi: float, *, rng=random, sleep: Sleep = asyncio.sleep) -> float:
    seconds = lognormal_between(lo, hi, rng)
    await sleep(seconds)
    return seconds


async def _viewport(page: Page) -> tuple[int, int, int]:
    try:
        w, h, total = await page.evaluate(VIEWPORT_JS)
        return int(w), int(h), int(total)
    except PlaywrightError:
        return 1280, 800, 0


async def move_mouse(page: Page, cfg: HumanConfig, *, rng=random) -> int:
    """Drift the cursor to a few random points inside the viewport (humanize curves them)."""
    moves = randint_between(cfg.mouse_moves, rng)
    w, h, _ = await _viewport(page)
    for _ in range(moves):
        x = rng.uniform(0.15, 0.85) * w
        y = rng.uniform(0.2, 0.8) * h
        await page.mouse.move(x, y)
    return moves


async def scroll(page: Page, cfg: HumanConfig, *, rng=random, sleep: Sleep = asyncio.sleep) -> int:
    """Scroll down in uneven steps with pauses, sometimes a little back up. Returns the count."""
    count = randint_between(cfg.scrolls, rng)
    for _ in range(count):
        delta = randint_between(cfg.scroll_px, rng)
        if rng.random() < cfg.scroll_back_chance:
            delta = -int(delta * rng.uniform(0.2, 0.5))
        await page.mouse.wheel(0, delta)
        await pause(*cfg.scroll_pause, rng=rng, sleep=sleep)
    return count


async def browse(
    page: Page, cfg: HumanConfig, *, rng=random, sleep: Sleep = asyncio.sleep
) -> dict[str, float]:
    """Act like a person looking at the page: glance, scroll, read. Returns what was done."""
    if not cfg.enabled:
        return {}
    moves = await move_mouse(page, cfg, rng=rng)
    scrolls = await scroll(page, cfg, rng=rng, sleep=sleep)
    read = await pause(*cfg.read_seconds, rng=rng, sleep=sleep)
    return {"mouse_moves": moves, "scrolls": scrolls, "read_seconds": round(read, 2)}
