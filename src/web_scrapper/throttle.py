"""Decide *when* the next page load may happen, per site, and when to stop entirely.

    throttle = Throttle(ThrottleConfig())
    waited = await throttle.wait(url)       # sleeps if we're going too fast; may raise ThrottleStop
    result = await fetch(page, url)
    throttle.record(url, result.block)      # feeds cool-downs and the circuit breaker

Rules (all per site, except the session budget and breaks):
- Gap between page loads: log-normal between min_interval and max_interval.
- Every ~break_every pages: a longer break (break_seconds).
- After max_pages_per_session: SessionBudgetExceeded. Start a fresh session later.
- After max_pages_per_day for a site (counted across runs in a BudgetStore, e.g. SqliteStore):
  DailyBudgetExceeded. Without a store there is no daily limit (nothing to count across runs).
- 429 / 5xx: cool-down of cooldown_base, doubling on each repeat, capped at cooldown_max.
- 403 / captcha: the site is stopped for this session (stop_on_hard_block), so DomainBlocked.
- max_consecutive_failures non-OK results in a row: the site is stopped (circuit breaker).
- Any OK result resets the failure streak and the cool-down doubling.
"""

import asyncio
import ipaddress
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol
from urllib.parse import urlparse

from .config import ThrottleConfig
from .fetcher import BlockKind
from .human import lognormal_between, randint_between

SOFT = {BlockKind.RATE_LIMITED, BlockKind.SERVER_ERROR}
HARD = {BlockKind.FORBIDDEN, BlockKind.CAPTCHA}


class ThrottleStop(Exception):
    """Base class: the throttle refuses further requests."""


class SessionBudgetExceeded(ThrottleStop):
    pass


class DailyBudgetExceeded(ThrottleStop):
    pass


class BudgetStore(Protocol):
    """Persists pages per (day, site) across runs. SqliteStore implements it."""

    def pages_on(self, day: str, site: str) -> int: ...
    def add_pages(self, day: str, site: str, n: int = 1) -> None: ...


class DomainBlocked(ThrottleStop):
    def __init__(self, domain: str, reason: str):
        super().__init__(f"{domain}: {reason}")
        self.domain = domain
        self.reason = reason


def site_key(url: str) -> str:
    """Group hosts by site: www.reddit.com, old.reddit.com -> reddit.com.

    Naive (last two labels), so it's wrong for e.g. example.co.uk. Good enough for our targets.
    """
    host = (urlparse(url).hostname or "").lower()
    try:
        ipaddress.ip_address(host)
        return host  # an IP address is its own site (never "0.1" for 127.0.0.1)
    except ValueError:
        pass
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


@dataclass
class _Site:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_allowed: float = 0.0
    cooldown_until: float = 0.0
    soft_streak: int = 0
    failures: int = 0
    stopped: str | None = None  # reason, once the breaker trips


class Throttle:
    def __init__(
        self,
        cfg: ThrottleConfig = ThrottleConfig(),
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random = random,
        budget_store: BudgetStore | None = None,
        today: Callable[[], str] = lambda: date.today().isoformat(),
    ):
        self.cfg = cfg
        self._clock, self._sleep, self._rng = clock, sleep, rng
        self.budget_store, self._today = budget_store, today
        self._sites: dict[str, _Site] = {}
        self.pages = 0
        self._next_break_at = randint_between(cfg.break_every, rng)
        self.log: list[str] = []  # human-readable decisions, shown in the Lab

    def _site(self, url: str) -> tuple[str, _Site]:
        key = site_key(url)
        return key, self._sites.setdefault(key, _Site())

    async def wait(self, url: str) -> float:
        """Block until a request to `url` is allowed. Returns seconds waited."""
        if self.pages >= self.cfg.max_pages_per_session:
            raise SessionBudgetExceeded(
                f"session budget of {self.cfg.max_pages_per_session} pages reached"
            )
        key, site = self._site(url)
        if site.stopped:
            raise DomainBlocked(key, site.stopped)
        if self.budget_store is not None:
            used = self.budget_store.pages_on(self._today(), key)
            if used >= self.cfg.max_pages_per_day:
                raise DailyBudgetExceeded(
                    f"{key}: daily budget of {self.cfg.max_pages_per_day} pages used ({used} today)"
                )

        async with site.lock:  # concurrent callers for one site queue up here
            now = self._clock()
            target = max(site.next_allowed, site.cooldown_until)
            if target == site.cooldown_until and site.cooldown_until > now:
                self.log.append(f"{key}: cool-down {site.cooldown_until - now:.0f}s")
            if self.pages and self.pages >= self._next_break_at and self.cfg.break_seconds[1] > 0:
                brk = lognormal_between(*self.cfg.break_seconds, self._rng)
                target = max(target, now + brk)
                self._next_break_at = self.pages + randint_between(self.cfg.break_every, self._rng)
                self.log.append(f"break {brk:.0f}s after {self.pages} pages")
            delay = max(0.0, target - now)
            if delay:
                await self._sleep(delay)
            gap = lognormal_between(self.cfg.min_interval, self.cfg.max_interval, self._rng)
            site.next_allowed = self._clock() + gap
            self.pages += 1
            if self.budget_store is not None:
                self.budget_store.add_pages(self._today(), key)
        return delay

    def record(self, url: str, block: BlockKind) -> None:
        """Report the outcome of the request that followed wait()."""
        key, site = self._site(url)
        if block is BlockKind.NONE:
            site.failures = site.soft_streak = 0
            return
        site.failures += 1
        if block in SOFT:
            site.soft_streak += 1
            cool = min(self.cfg.cooldown_max, self.cfg.cooldown_base * 2 ** (site.soft_streak - 1))
            site.cooldown_until = self._clock() + cool
            self.log.append(f"{key}: {block} -> cool-down {cool:.0f}s")
        if block in HARD and self.cfg.stop_on_hard_block:
            site.stopped = f"hard block ({block}); stopped for this session"
        elif site.failures >= self.cfg.max_consecutive_failures:
            site.stopped = f"{site.failures} failures in a row (last: {block})"
        if site.stopped:
            self.log.append(f"{key}: STOP, {site.stopped}")

    def is_stopped(self, url: str) -> bool:
        return self._site(url)[1].stopped is not None
