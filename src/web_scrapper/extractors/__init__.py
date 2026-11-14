"""Extractors turn page HTML into records. They are pure functions: (html, url) -> list[dict].

Keeping them free of browser code means they can be tested on saved HTML, re-run on old
pages after a fix, and used with any fetcher. Register new ones in EXTRACTORS; the Lab lists them.
"""

from collections.abc import Callable

from .reddit import RedditPost, has_more_posts, parse_listing, subreddit_url
from .reddit import parse_listing_records as _reddit_listing_records

Extractor = Callable[[str, str], list[dict]]

EXTRACTORS: dict[str, Extractor] = {
    "reddit_listing": _reddit_listing_records,
}

# The record kind each extractor produces (the `kind` used by storage sinks).
EXTRACTOR_KINDS: dict[str, str] = {
    "reddit_listing": "reddit_post",
}

__all__ = [
    "EXTRACTORS",
    "EXTRACTOR_KINDS",
    "Extractor",
    "RedditPost",
    "has_more_posts",
    "parse_listing",
    "subreddit_url",
]
