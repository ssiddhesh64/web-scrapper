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
