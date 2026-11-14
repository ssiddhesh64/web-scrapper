import asyncio
import logging
from pathlib import Path

from web_scrapper import BrowserConfig, FetchOptions, ThrottleStop, open_session

OUTPUT_DIR = Path("output")
URLS = [
    "https://www.siddheshsawant.in/",
    "https://www.siddheshsawant.in/#experience",
]


async def main(urls=URLS, cfg: BrowserConfig | None = None, opts: FetchOptions | None = None):
    # One session = one browser, one tab, one throttle. Human browsing after each OK page.
    async with open_session(cfg, fetch_opts=opts or FetchOptions()) as session:
        for url in urls:
            try:
                visit = await session.visit(url)
            except ThrottleStop as stop:
                print("Throttle says stop:", stop)
                break

            r = visit.result
            print(f"\n{url}\n  status={r.status} block={r.block} attempts={r.attempts}")
            print(f"  waited={visit.waited}s fetch={r.elapsed}s browse={visit.browse}")
            if not r.ok:
                print("  not OK:", r.signals or r.error, "| artifacts:", r.artifacts)
                continue
            OUTPUT_DIR.mkdir(exist_ok=True)
            (OUTPUT_DIR / "page.html").write_text(r.html, encoding="utf-8")
            print(f"  title={r.title!r} html={len(r.html):,} chars -> {OUTPUT_DIR / 'page.html'}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
    # asyncio.run(main(["https://old.reddit.com/r/python/"]))   # -> login_wall
    # asyncio.run(main(cfg=BrowserConfig(profile_dir=Path("profiles/default"))))
