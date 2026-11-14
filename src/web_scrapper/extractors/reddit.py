"""Parse new-Reddit ("shreddit") HTML into RedditPost records. Pure: HTML in, data out.

New Reddit renders each feed item as a <shreddit-post> web component whose *attributes* carry
the data (id, post-title, permalink, author, score, comment-count, created-timestamp, ...).
Attributes are far more stable than CSS classes or visible text, so we read only those.
Ads are <shreddit-ad-post> (a different tag), so they're never picked up.
"""

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URL = "https://www.reddit.com"
SORTS = {"hot", "new", "top", "rising", "best", "controversial"}
TIME_FILTERS = {"hour", "day", "week", "month", "year", "all"}
SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]{1,20}$")


@dataclass(frozen=True)
class RedditPost:
    id: str  # "t3_1xvgyzh" (Reddit fullname)
    subreddit: str  # "puzzles"
    title: str
    permalink: str  # absolute URL of the comments page
    author: str | None = None  # None for deleted accounts
    score: int | None = None
    comment_count: int | None = None
    upvote_ratio: float | None = None
    created_at: datetime | None = None  # UTC
    post_type: str | None = None  # text | image | link | video | gallery | multi_media ...
    url: str | None = None  # what the post links to (image, article...). Self posts: permalink
    domain: str | None = None
    flair: str | None = None
    language: str | None = None
    nsfw: bool = False
    spoiler: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat() if self.created_at else None
        return d


def subreddit_url(name: str, sort: str = "hot", t: str | None = None) -> str:
    """https://www.reddit.com/r/<name>/<sort>/[?t=...] with validation."""
    name = name.removeprefix("r/").removeprefix("/r/").strip("/")
    if not SUBREDDIT_RE.match(name):
        raise ValueError(f"not a valid subreddit name: {name!r}")
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {sorted(SORTS)}")
    url = f"{BASE_URL}/r/{name}/{sort}/"
    if t is not None:
        if sort not in ("top", "controversial"):
            raise ValueError("t (time filter) only applies to sort='top' or 'controversial'")
        if t not in TIME_FILTERS:
            raise ValueError(f"t must be one of {sorted(TIME_FILTERS)}")
        url += f"?t={t}"
    return url


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _time(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _flag(el, name: str) -> bool:
    """Boolean HTML attributes: present (any value except 'false') means true."""
    value = el.get(name)
    return value is not None and str(value).lower() != "false"


def parse_listing(html: str, base_url: str = BASE_URL) -> list[RedditPost]:
    """Every <shreddit-post> in the page, in feed order, de-duplicated by id."""
    soup = BeautifulSoup(html, "html.parser")
    posts: dict[str, RedditPost] = {}
    for el in soup.find_all("shreddit-post"):
        post_id, title, permalink = el.get("id"), el.get("post-title"), el.get("permalink")
        if not (post_id and title and permalink) or post_id in posts:
            continue
        flair_el = el.find("shreddit-post-flair")
        flair = " ".join(flair_el.get_text(" ").split()) if flair_el else ""
        subreddit = el.get("subreddit-name") or (el.get("subreddit-prefixed-name") or "")
        posts[post_id] = RedditPost(
            id=post_id,
            subreddit=subreddit.removeprefix("r/"),
            title=" ".join(title.split()),
            permalink=urljoin(base_url, permalink),
            author=el.get("author") or None,
            score=_int(el.get("score")),
            comment_count=_int(el.get("comment-count")),
            upvote_ratio=_float(el.get("upvote-ratio")),
            created_at=_time(el.get("created-timestamp")),
            post_type=el.get("post-type") or None,
            url=urljoin(base_url, el["content-href"]) if el.get("content-href") else None,
            domain=el.get("domain") or None,
            flair=flair or None,
            language=el.get("post-language") or None,
            nsfw=_flag(el, "nsfw") or _flag(el, "is-nsfw"),
            spoiler=_flag(el, "spoiler"),
        )
    return list(posts.values())


def has_more_posts(html: str) -> bool:
    """True if the page still has a 'load more posts' placeholder (the feed isn't exhausted)."""
    return 'id="partial-more-posts' in html


def parse_listing_records(html: str, url: str = BASE_URL) -> list[dict]:
    """Registry adapter: HTML -> list of plain dicts."""
    return [p.to_dict() for p in parse_listing(html)]
