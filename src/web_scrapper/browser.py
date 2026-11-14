"""The only module that imports Camoufox.

Usage:
    async with open_browser(BrowserConfig()) as context:
        page = await context.new_page()
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from camoufox.async_api import AsyncCamoufox
from camoufox.fingerprints import Screen  # camoufox 0.5.x has its own Screen (not browserforge's)
from playwright.async_api import BrowserContext

from .config import BrowserConfig


def build_launch_kwargs(cfg: BrowserConfig) -> dict[str, Any]:
    """Translate our config into AsyncCamoufox keyword arguments."""
    max_w, max_h = cfg.max_screen
    kwargs: dict[str, Any] = {
        "os": cfg.os,
        "headless": cfg.headless,
        "humanize": cfg.humanize,
        "screen": Screen(max_width=max_w, max_height=max_h),
        "block_images": cfg.block_images,
    }
    if cfg.proxy:
        kwargs["proxy"] = cfg.proxy.to_playwright()
    if cfg.use_geoip:
        kwargs["geoip"] = True
    if cfg.profile_dir:
        kwargs["persistent_context"] = True
        kwargs["user_data_dir"] = str(cfg.profile_dir)
    kwargs.update(cfg.extra)
    return kwargs


@asynccontextmanager
async def open_browser(cfg: BrowserConfig) -> AsyncIterator[BrowserContext]:
    """Launch Camoufox and yield a BrowserContext; everything is closed on exit.

    Always yields a context, so callers don't care whether a persistent profile is used:
    - with `profile_dir`, Camoufox returns a persistent context directly;
    - without it, we open a fresh context on the browser.
    """
    kwargs = build_launch_kwargs(cfg)
    if cfg.profile_dir:
        cfg.profile_dir.mkdir(parents=True, exist_ok=True)
    async with AsyncCamoufox(**kwargs) as browser_or_context:
        if kwargs.get("persistent_context"):
            yield browser_or_context
        else:
            context = await browser_or_context.new_context()
            try:
                yield context
            finally:
                await context.close()
