"""Print the public IP the scraper's browser uses (e.g. to check a proxy's exit IP).

uv run python check_ip.py            # uses BrowserConfig.from_env(), so SCRAPER_PROXY_* applies
"""

import asyncio

from web_scrapper import BrowserConfig, open_browser


async def main() -> None:
    async with open_browser(BrowserConfig.from_env(headless=True)) as context:
        page = await context.new_page()
        await page.goto("https://api.ipify.org")
        print("Camoufox public IP:", await page.locator("body").inner_text())


if __name__ == "__main__":
    asyncio.run(main())
