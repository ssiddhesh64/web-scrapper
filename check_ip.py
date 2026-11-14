import asyncio
from camoufox.async_api import AsyncCamoufox

async def main():
    async with AsyncCamoufox(headless=True) as browser:
        page = await browser.new_page()

        await page.goto("https://api.ipify.org")
        ip = await page.locator("body").inner_text()

        print("Camoufox public IP:", ip)

        await browser.close()

asyncio.run(main())