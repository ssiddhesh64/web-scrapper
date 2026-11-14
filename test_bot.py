import asyncio
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from web_scrapper import BrowserConfig, open_browser

TARGET_URL = "https://www.siddheshsawant.in/experience"
OUTPUT_DIR = Path("output")


async def main(
    url: str = TARGET_URL,
    cfg: BrowserConfig | None = None,
    wait_selector: str | None = None,
):
    cfg = cfg or BrowserConfig.from_env()

    async with open_browser(cfg) as context:
        page = await context.new_page()

        response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        status = response.status if response else None
        print(f"Status: {status}  Final URL: {page.url}")
        if status is None or status >= 400:
            print("Request failed or was blocked; stopping.")
            return

        # Wait for the page to be ready instead of sleeping a fixed time.
        try:
            if wait_selector:
                await page.wait_for_selector(wait_selector, timeout=15_000)
            else:
                await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeoutError:
            print("Timed out waiting for the page to settle; continuing with what loaded.")

        title = await page.title()
        html = await page.content()
        text = await page.locator("body").inner_text()

        OUTPUT_DIR.mkdir(exist_ok=True)
        html_path = OUTPUT_DIR / "page.html"
        html_path.write_text(html, encoding="utf-8")

        print("Title:", title)
        print(f"HTML: {len(html):,} chars saved to {html_path}")
        print("Text preview:\n", text[:500])


if __name__ == "__main__":
    # asyncio.run(main())
    # asyncio.run(main(url="https://old.reddit.com/r/python/"))
    # Keep cookies between runs:
    # asyncio.run(main(cfg=BrowserConfig(profile_dir=Path("profiles/default"))))
    asyncio.run(main(url="https://www.siddheshsawant.in/"))
