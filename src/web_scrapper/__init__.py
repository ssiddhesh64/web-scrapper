from .browser import build_launch_kwargs, open_browser
from .config import BrowserConfig, HumanConfig, ProxyConfig, ThrottleConfig
from .extractors import EXTRACTORS, RedditPost, parse_listing, subreddit_url
from .fetcher import BlockKind, FetchOptions, FetchResult, fetch
from .reddit import CollectResult, collect_subreddit
from .session import ScrapeSession, Visit, open_session
from .storage import JsonlSink, Sink, SqliteStore, WriteStats
from .throttle import (
    BudgetStore,
    DailyBudgetExceeded,
    DomainBlocked,
    SessionBudgetExceeded,
    Throttle,
    ThrottleStop,
)

__all__ = [
    "EXTRACTORS",
    "BudgetStore",
    "DailyBudgetExceeded",
    "JsonlSink",
    "Sink",
    "SqliteStore",
    "WriteStats",
    "BlockKind",
    "CollectResult",
    "RedditPost",
    "collect_subreddit",
    "parse_listing",
    "subreddit_url",
    "BrowserConfig",
    "DomainBlocked",
    "FetchOptions",
    "FetchResult",
    "HumanConfig",
    "ProxyConfig",
    "ScrapeSession",
    "SessionBudgetExceeded",
    "Throttle",
    "ThrottleConfig",
    "ThrottleStop",
    "Visit",
    "build_launch_kwargs",
    "fetch",
    "open_browser",
    "open_session",
    "main",
]


def main() -> int:
    """Kept for backwards compatibility; the CLI lives in web_scrapper.cli."""
    from .cli import main as cli_main

    return cli_main()
