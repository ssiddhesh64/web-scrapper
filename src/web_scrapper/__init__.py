from .browser import build_launch_kwargs, open_browser
from .config import BrowserConfig, HumanConfig, ProxyConfig, ThrottleConfig
from .fetcher import BlockKind, FetchOptions, FetchResult, fetch
from .session import ScrapeSession, Visit, open_session
from .throttle import DomainBlocked, SessionBudgetExceeded, Throttle, ThrottleStop

__all__ = [
    "BlockKind",
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


def main() -> None:
    print("Hello from web-scrapper!")
