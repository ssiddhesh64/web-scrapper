"""One browsing session: throttle -> fetch -> browse like a human, for every visit.

    async with open_session(BrowserConfig()) as session:
        for url in urls:
            try:
                visit = await session.visit(url)
            except ThrottleStop as stop:
                print("stopping:", stop)
                break
            if visit.result.ok:
                ...  # parse visit.result.html, or session.page while still on it

Uses ONE tab for all visits, like a person, instead of opening a new page per URL.
"""

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime

from playwright.async_api import BrowserContext, Page

from . import human
from .browser import open_browser
from .config import BrowserConfig, HumanConfig, ThrottleConfig
from .fetcher import FetchOptions, FetchResult, fetch
from .throttle import Throttle


@dataclass
class Visit:
    url: str
    result: FetchResult
    waited: float  # seconds the throttle made us wait before loading
    browse: dict = field(default_factory=dict)  # what human.browse() did
    started_at: str = ""


class ScrapeSession:
    def __init__(
        self,
        context: BrowserContext,
        *,
        fetch_opts: FetchOptions = FetchOptions(),
        human_cfg: HumanConfig = HumanConfig(),
        throttle: Throttle | ThrottleConfig = ThrottleConfig(),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random = random,
    ):
        self.context = context
        self.fetch_opts = fetch_opts
        self.human_cfg = human_cfg
        self.throttle = (
            throttle if isinstance(throttle, Throttle) else Throttle(throttle, sleep=sleep)
        )
        self._sleep, self._rng = sleep, rng
        self.page: Page | None = None
        self.visits: list[Visit] = []

    async def get_page(self) -> Page:
        if self.page is None or self.page.is_closed():
            self.page = await self.context.new_page()
        return self.page

    async def visit(self, url: str, *, browse: bool = True) -> Visit:
        """Wait for the throttle, load the page, then (if OK) browse it like a person.

        Raises ThrottleStop (SessionBudgetExceeded / DomainBlocked) when we must stop.
        """
        page = await self.get_page()
        started = datetime.now().isoformat(timespec="seconds")
        waited = await self.throttle.wait(url)
        result = await fetch(page, url, self.fetch_opts, sleep=self._sleep)
        self.throttle.record(url, result.block)
        did = {}
        if browse and result.ok:
            did = await human.browse(page, self.human_cfg, rng=self._rng, sleep=self._sleep)
        v = Visit(url=url, result=result, waited=round(waited, 2), browse=did, started_at=started)
        self.visits.append(v)
        return v


@asynccontextmanager
async def open_session(
    browser_cfg: BrowserConfig | None = None,
    *,
    fetch_opts: FetchOptions = FetchOptions(),
    human_cfg: HumanConfig = HumanConfig(),
    throttle: Throttle | ThrottleConfig = ThrottleConfig(),
) -> AsyncIterator[ScrapeSession]:
    """Launch a browser and give back a ready ScrapeSession; everything closes on exit."""
    async with open_browser(browser_cfg or BrowserConfig.from_env()) as context:
        yield ScrapeSession(context, fetch_opts=fetch_opts, human_cfg=human_cfg, throttle=throttle)
