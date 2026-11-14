"""Collect posts from a subreddit feed by scrolling like a person.

    async with open_session(BrowserConfig(profile_dir=Path("profiles/reddit"))) as session:
        res = await collect_subreddit(session, "puzzles", sort="new", limit=50)
        print(res.reason, len(res.posts))

Pagination = infinite scroll. New Reddit loads the next batch of posts (an HTML fragment from
/svc/shreddit/community-more-posts/...) when you scroll near the end of the feed. We scroll with
human timing and re-parse the page after each round, instead of calling that internal endpoint
directly, which would look nothing like a person reading a feed.

Every round goes through the session's throttle (gaps, budget, cool-downs) and checks for
visible block banners, so a block during scrolling stops the run instead of being ignored.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from playwright.async_api import Error as PlaywrightError

from . import human
from .config import HumanConfig
from .extractors.reddit import RedditPost, has_more_posts, parse_listing, subreddit_url
from .fetcher import BlockKind, visible_block_texts
from .session import ScrapeSession, Visit
from .throttle import ThrottleStop

# [scrollY + innerHeight, scrollHeight]: how far down the page we are.
SCROLL_POS_JS = "() => [Math.round(scrollY + innerHeight), document.documentElement.scrollHeight]"
# "Near the bottom" = within this many viewport heights of the end. Only then can a round that
# found nothing count as stale: higher up, the next batch simply hasn't been requested yet.
NEAR_BOTTOM_VIEWPORTS = 1.5

# How we scroll a feed: further and more often than when "reading" a single page.
FEED_SCROLL = HumanConfig(
    read_seconds=(1.0, 4.0),
    scrolls=(2, 4),
    scroll_px=(600, 1200),
    scroll_pause=(0.4, 1.5),
    scroll_back_chance=0.1,
    mouse_moves=(0, 1),
)


@dataclass
class CollectResult:
    url: str
    posts: list[RedditPost] = field(default_factory=list)
    rounds: int = 0  # scroll rounds after the initial load
    reason: str = ""  # why we stopped
    log: list[dict] = field(default_factory=list)  # per round: posts, position, near_bottom
    already_seen: int = 0  # collected posts that `seen()` reported as already stored
    visit: Visit | None = None  # the initial page load

    @property
    def ok(self) -> bool:
        return (
            self.visit is not None
            and self.visit.result.ok
            and not self.reason.startswith(("blocked", "throttle"))
        )


async def collect_subreddit(
    session: ScrapeSession,
    name: str,
    *,
    sort: str = "new",
    t: str | None = None,
    limit: int = 50,
    max_rounds: int | None = None,
    stale_rounds: int = 3,
    scroll_cfg: HumanConfig = FEED_SCROLL,
    seen: Callable[[str], bool] | None = None,
    stop_after_seen: int = 10,
) -> CollectResult:
    """Load r/<name>, then scroll until `limit` posts, the end of the feed, or a block.

    `max_rounds` defaults to about 16 scroll rounds per 25 posts wanted (measured live: a batch
    of 25 posts is ~13,000 px, about 12-13 rounds of scrolling).

    Pacing: the throttle paces *requests*. Reddit only requests a new batch now and then while
    you scroll, so we call throttle.wait() before the first round and after every round that
    brought a new batch, not before every scroll. Scrolling in between is paced by human pauses.

    Incremental runs: pass `seen` (e.g. `lambda i: store.has("reddit_post", i)`). Once
    `stop_after_seen` of the collected posts are already stored, we stop with "caught up":
    with sort="new" everything further down is older and was collected before. Saves requests.
    (For hot/top the order changes over time, so leave `seen` unset there.)

    Stops when: limit reached | caught up | feed exhausted | `stale_rounds` rounds in a row add
    nothing near the bottom | `max_rounds` reached | a block banner appears | the throttle stops.
    """
    url = subreddit_url(name, sort, t)
    res = CollectResult(url=url)
    try:
        res.visit = await session.visit(url, browse=False)
    except ThrottleStop as stop:
        res.reason = f"throttle: {stop}"
        return res
    if not res.visit.result.ok:
        res.reason = f"blocked on load: {res.visit.result.block} {res.visit.result.signals}"
        return res

    found: dict[str, RedditPost] = {p.id: p for p in parse_listing(res.visit.result.html)}
    page = await session.get_page()
    stale = 0
    known: set[str] = set()

    def caught_up() -> bool:
        if seen is None:
            return False
        known.update(pid for pid in found if pid not in known and seen(pid))
        res.already_seen = len(known)
        return len(known) >= stop_after_seen

    if max_rounds is None:
        max_rounds = max(10, math.ceil(limit / 25) * 16)
    need_wait = True  # pace the request the next scrolls may trigger
    while len(found) < limit:
        if caught_up():
            res.reason = f"caught up ({len(known)} posts already stored)"
            break
        if res.rounds >= max_rounds:
            res.reason = f"max_rounds ({max_rounds}) reached"
            break
        if need_wait:
            try:
                await session.throttle.wait(url)
            except ThrottleStop as stop:
                res.reason = f"throttle: {stop}"
                break
        res.rounds += 1
        await human.scroll(page, scroll_cfg, rng=session.rng, sleep=session.sleep)
        await human.pause(*scroll_cfg.read_seconds, rng=session.rng, sleep=session.sleep)

        banners = await visible_block_texts(page)
        if banners:
            session.throttle.record(url, BlockKind.FORBIDDEN)
            res.reason = f"blocked while scrolling: {banners[0][:120]}"
            break

        html = await page.content()
        before = len(found)
        for post in parse_listing(html):
            found.setdefault(post.id, post)
        bottom, height, near = await _scroll_position(page)
        res.log.append(
            {
                "round": res.rounds,
                "posts": len(found),
                "new": len(found) - before,
                "bottom": bottom,
                "height": height,
                "near_bottom": near,
            }
        )
        loaded_batch = len(found) > before
        if need_wait or loaded_batch:
            session.throttle.record(url, BlockKind.NONE)
        need_wait = loaded_batch
        if loaded_batch or not near:
            stale = 0  # progress, or simply not at the end of the loaded feed yet
            continue
        stale += 1
        if not has_more_posts(html):
            res.reason = "end of feed"
            break
        if stale >= stale_rounds:
            res.reason = f"no new posts after {stale_rounds} scroll rounds"
            break

    if not res.reason:
        res.reason = "limit reached"
    res.posts = list(found.values())[:limit]
    return res


async def _scroll_position(page) -> tuple[int, int, bool]:
    try:
        bottom, height = await page.evaluate(SCROLL_POS_JS)
        viewport = (await page.evaluate("() => innerHeight")) or 800
    except PlaywrightError, TypeError, ValueError:  # unknown -> treat as "at the bottom"
        return 0, 0, True
    return int(bottom), int(height), height - bottom <= NEAR_BOTTOM_VIEWPORTS * viewport
