from .browser import build_launch_kwargs, open_browser
from .config import BrowserConfig, ProxyConfig

__all__ = ["BrowserConfig", "ProxyConfig", "build_launch_kwargs", "open_browser", "main"]


def main() -> None:
    print("Hello from web-scrapper!")
