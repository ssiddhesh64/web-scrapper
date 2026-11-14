"""All scraper settings in one place.

This module deliberately knows nothing about Camoufox: it only describes *what* we want.
`browser.py` translates it into Camoufox launch options.
"""

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

OSName = Literal["macos", "windows", "linux"]


@dataclass(frozen=True)
class ProxyConfig:
    server: str  # e.g. "http://gate.example.com:8080"
    username: str | None = None
    password: str | None = None

    def to_playwright(self) -> dict[str, str]:
        proxy = {"server": self.server}
        if self.username:
            proxy["username"] = self.username
        if self.password:
            proxy["password"] = self.password
        return proxy


@dataclass(frozen=True)
class BrowserConfig:
    os: OSName = "macos"
    headless: bool | Literal["virtual"] = False  # "virtual" = Xvfb, Linux only
    humanize: bool | float = True  # True, or max cursor-move seconds
    max_screen: tuple[int, int] = (1920, 1080)
    proxy: ProxyConfig | None = None
    geoip: bool | None = None  # None = on when a proxy is set, off otherwise
    block_images: bool = False
    profile_dir: Path | None = None  # set to keep cookies/storage between runs
    extra: dict = field(default_factory=dict)  # escape hatch: raw Camoufox kwargs

    def __post_init__(self):
        if self.headless == "virtual" and platform.system() != "Linux":
            raise ValueError('headless="virtual" needs Xvfb and only works on Linux')

    @property
    def use_geoip(self) -> bool:
        return self.proxy is not None if self.geoip is None else self.geoip

    @classmethod
    def from_env(cls, **overrides) -> BrowserConfig:
        """Build from SCRAPER_* environment variables, so secrets (proxy creds) stay out of code.

        Explicit keyword overrides win over the environment.
        """
        env = os.environ
        proxy = None
        if server := env.get("SCRAPER_PROXY_SERVER"):
            proxy = ProxyConfig(
                server=server,
                username=env.get("SCRAPER_PROXY_USERNAME"),
                password=env.get("SCRAPER_PROXY_PASSWORD"),
            )

        values: dict = {"proxy": proxy}
        if "SCRAPER_HEADLESS" in env:
            raw = env["SCRAPER_HEADLESS"].lower()
            values["headless"] = "virtual" if raw == "virtual" else raw in ("1", "true", "yes")
        if "SCRAPER_BLOCK_IMAGES" in env:
            values["block_images"] = env["SCRAPER_BLOCK_IMAGES"].lower() in ("1", "true", "yes")
        if profile := env.get("SCRAPER_PROFILE_DIR"):
            values["profile_dir"] = Path(profile)

        values.update(overrides)
        return cls(**values)


@dataclass(frozen=True)
class HumanConfig:
    """How we behave on a page after it loads (see human.py). Ranges are (min, max)."""

    enabled: bool = True
    read_seconds: tuple[float, float] = (2.0, 8.0)  # "reading" pause per page
    scrolls: tuple[int, int] = (1, 4)  # mouse-wheel scrolls per page
    scroll_px: tuple[int, int] = (300, 900)  # distance per scroll
    scroll_pause: tuple[float, float] = (0.6, 2.5)  # pause after each scroll
    scroll_back_chance: float = 0.15  # sometimes scroll up a little, like people do
    mouse_moves: tuple[int, int] = (0, 2)  # idle cursor moves per page

    def __post_init__(self):
        for name in ("read_seconds", "scrolls", "scroll_px", "scroll_pause", "mouse_moves"):
            lo, hi = getattr(self, name)
            if lo < 0 or hi < lo:
                raise ValueError(f"{name} must be (min, max) with 0 <= min <= max, got {(lo, hi)}")
        if not 0 <= self.scroll_back_chance <= 1:
            raise ValueError("scroll_back_chance must be between 0 and 1")


@dataclass(frozen=True)
class ThrottleConfig:
    """When the next page load is allowed (see throttle.py). Times in seconds."""

    min_interval: float = 4.0  # gap between page loads on the same site...
    max_interval: float = 10.0  # ...drawn between these (log-normal, not uniform)
    max_pages_per_session: int = 60  # then stop: start a fresh session later
    max_pages_per_day: int = 400  # per site, across runs (needs a budget store, e.g. SqliteStore)
    break_every: tuple[int, int] = (15, 30)  # take a longer break every N pages...
    break_seconds: tuple[float, float] = (30.0, 120.0)  # ...lasting this long
    cooldown_base: float = 60.0  # after a 429/5xx: wait this, doubling per repeat...
    cooldown_max: float = 900.0  # ...up to this
    stop_on_hard_block: bool = True  # 403/captcha -> stop that site for this session
    max_consecutive_failures: int = 3  # any non-OK outcomes in a row -> stop that site

    def __post_init__(self):
        if not 0 <= self.min_interval <= self.max_interval:
            raise ValueError("need 0 <= min_interval <= max_interval")
        if (
            min(self.max_pages_per_session, self.max_pages_per_day, self.max_consecutive_failures)
            < 1
        ):
            raise ValueError(
                "max_pages_per_session, max_pages_per_day and max_consecutive_failures must be >= 1"
            )
        for name in ("break_every", "break_seconds"):
            lo, hi = getattr(self, name)
            if lo < 0 or hi < lo:
                raise ValueError(f"{name} must be (min, max) with 0 <= min <= max")
