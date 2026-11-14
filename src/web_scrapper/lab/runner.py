"""Run one scrape experiment and record everything about it.

Each run is saved to output/runs/<id>/ (request.json, result.json, page.html, screenshot.png),
so you can compare runs after changing code or config.

Loading + block detection + retries go through the real `fetcher.fetch()`, so improving the
fetcher improves the Lab automatically. Visits go through `ScrapeSession`, so the throttle and
human behaviour are the real ones too. The Lab only adds extras on top (extraction, JS,
identity probe, network/console capture).

Several URLs in one run = one session: they share a tab, cookies and the throttle, so you can
watch gaps, breaks, cool-downs and the circuit breaker at work.
"""

import json
import shutil
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..browser import build_launch_kwargs, open_browser
from ..config import BrowserConfig, HumanConfig, ThrottleConfig
from ..fetcher import FetchOptions
from ..session import ScrapeSession
from ..throttle import ThrottleStop

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = PROJECT_ROOT / "output" / "runs"

TEXT_LIMIT = 50_000
EXTRACT_LIMIT = 200
NETWORK_LIMIT = 1_500

IDENTITY_JS = """() => ({
  userAgent: navigator.userAgent,
  platform: navigator.platform,
  languages: navigator.languages,
  hardwareConcurrency: navigator.hardwareConcurrency,
  webdriver: navigator.webdriver,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  locale: Intl.DateTimeFormat().resolvedOptions().locale,
  screen: [screen.width, screen.height],
  window: [innerWidth, innerHeight],
  devicePixelRatio: devicePixelRatio,
})"""

EXTRACT_JS = """(els, [attr, limit]) => els.slice(0, limit).map(e =>
  attr === '' ? (e.innerText || e.textContent || '').trim()
  : attr === 'html' ? e.innerHTML
  : e.getAttribute(attr))"""


@dataclass
class ExtractRule:
    name: str
    selector: str
    attr: str = ""  # "" = text, "html" = innerHTML, anything else = attribute name


@dataclass
class RunRequest:
    url: str
    config: BrowserConfig
    wait_until: str = "domcontentloaded"  # commit | domcontentloaded | load | networkidle
    wait_for: str = "networkidle"  # networkidle | selector | none
    wait_selector: str = ""
    timeout_ms: int = 30_000
    settle_ms: int = 0
    retries: int = 1
    backoff_base: float = 5.0
    human: HumanConfig = field(default_factory=HumanConfig)
    throttle: ThrottleConfig = field(default_factory=ThrottleConfig)
    extra_urls: list[str] = field(default_factory=list)  # more pages in the same session
    screenshot: bool = True
    full_page: bool = False
    extract: list[ExtractRule] = field(default_factory=list)
    js: str = ""


def mask_secrets(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: ("••••••" if v and ("password" in k or "token" in k) else mask_secrets(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [mask_secrets(v) for v in obj]
    return obj


def describe_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe, secret-free view of what Camoufox receives."""
    safe = {}
    for k, v in kwargs.items():
        safe[k] = v if isinstance(v, (str, int, float, bool, type(None), dict, list)) else repr(v)
    return mask_secrets(safe)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]


async def run_experiment(req: RunRequest, raw_request: dict[str, Any]) -> dict[str, Any]:
    run_id = new_run_id()
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "id": run_id,
        "url": req.url,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "ok": False,
        "status": None,
        "timings": {},
        "warnings": [],
        "block_signals": [],
    }
    console: list[dict] = []
    network: list[dict] = []
    marks = {"start": time.perf_counter()}

    def mark(name: str):
        marks[name] = time.perf_counter()

    try:
        result["launch_kwargs"] = describe_kwargs(build_launch_kwargs(req.config))
        async with open_browser(req.config) as context:
            mark("launched")
            browser = context.browser
            result["browser_version"] = browser.version if browser else None
            page = await context.new_page()

            page.on("console", lambda m: console.append({"type": m.type, "text": m.text[:1000]}))
            page.on(
                "response",
                lambda r: (
                    len(network) < NETWORK_LIMIT
                    and network.append(
                        {
                            "method": r.request.method,
                            "type": r.request.resource_type,
                            "status": r.status,
                            "url": r.url,
                        }
                    )
                ),
            )
            page.on(
                "requestfailed",
                lambda r: (
                    len(network) < NETWORK_LIMIT
                    and network.append(
                        {
                            "method": r.method,
                            "type": r.resource_type,
                            "status": "failed",
                            "error": r.failure,
                            "url": r.url,
                        }
                    )
                ),
            )

            opts = FetchOptions(
                wait_until=req.wait_until,
                wait_for=req.wait_for,
                wait_selector=req.wait_selector,
                timeout_ms=req.timeout_ms,
                settle_ms=req.settle_ms,
                retries=req.retries,
                backoff_base=req.backoff_base,
                failure_dir=None,  # the Lab already saves everything into the run folder
            )
            session = ScrapeSession(
                context, fetch_opts=opts, human_cfg=req.human, throttle=req.throttle
            )
            session.page = page  # reuse the tab we attached listeners to

            visits = []
            for url in [req.url, *req.extra_urls]:
                try:
                    v = await session.visit(url)
                except ThrottleStop as stop:
                    result["warnings"].append(f"Throttle stopped the session before {url}: {stop}")
                    visits.append({"url": url, "skipped": str(stop)})
                    break
                visits.append(
                    {
                        "url": url,
                        "started_at": v.started_at,
                        "waited": v.waited,
                        "status": v.result.status,
                        "block": v.result.block.value,
                        "attempts": v.result.attempts,
                        "fetch_seconds": v.result.elapsed,
                        "browse": v.browse,
                        "title": v.result.title,
                    }
                )
            result["visits"] = visits
            result["throttle_log"] = session.throttle.log
            mark("loaded")

            # Everything below describes the LAST page loaded (the one the tab is on now).
            last = session.visits[-1].result if session.visits else None
            if last is None:
                raise RuntimeError("No page was loaded")
            result.update(
                url=last.url,
                status=last.status,
                response_headers=last.headers,
                final_url=last.final_url or page.url,
                title=last.title,
                block=last.block.value,
                block_signals=last.signals,
                attempts=last.attempts,
            )
            result["warnings"] += last.warnings
            if last.error:
                result["error"] = last.error
            if any(v.result.blocked for v in session.visits[:-1]):
                result["warnings"].append(
                    "An earlier page in this session was blocked; see Session tab"
                )

            # Re-read: human browsing (scrolling) may have loaded more content.
            if last.error:
                html, text = last.html, last.text
            else:
                html = await page.content()
                text = await page.locator("body").inner_text()
            (run_dir / "page.html").write_text(html, encoding="utf-8")
            result["html_chars"] = len(html)
            result["text_chars"] = len(text)
            result["text"] = text[:TEXT_LIMIT]

            try:
                result["identity"] = await page.evaluate(IDENTITY_JS)
            except Exception as e:  # noqa: BLE001 - diagnostics only
                result["warnings"].append(f"Identity probe failed: {e}")

            extracted = []
            for rule in req.extract:
                entry = {"name": rule.name, "selector": rule.selector, "attr": rule.attr}
                try:
                    loc = page.locator(rule.selector)
                    entry["count"] = await loc.count()
                    entry["values"] = await loc.evaluate_all(EXTRACT_JS, [rule.attr, EXTRACT_LIMIT])
                except Exception as e:  # noqa: BLE001 - user-supplied selector
                    entry["error"] = str(e).splitlines()[0]
                extracted.append(entry)
            result["extract"] = extracted

            if req.js.strip():
                try:
                    result["js_result"] = await page.evaluate(req.js)
                except Exception as e:  # noqa: BLE001 - user-supplied JS
                    result["js_error"] = str(e).splitlines()[0]
            mark("extracted")

            if req.screenshot:
                await page.screenshot(path=run_dir / "screenshot.png", full_page=req.full_page)
                result["screenshot"] = True

            result["cookies"] = [
                {k: c.get(k) for k in ("name", "domain", "path", "expires", "httpOnly", "secure")}
                for c in await context.cookies()
            ]
            result["ok"] = last.ok
    except Exception as e:  # noqa: BLE001 - surface every failure in the UI
        result["error"] = f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"
        result["traceback"] = traceback.format_exc()
    finally:
        mark("end")
        start = marks["start"]
        order = ["launched", "loaded", "extracted", "end"]
        prev = start
        for name in order:
            if name in marks:
                result["timings"][name] = round(marks[name] - prev, 2)
                prev = marks[name]
        result["timings"]["total"] = round(marks["end"] - start, 2)
        result["console"] = console
        result["network"] = network
        result["network_summary"] = {
            "responses": sum(1 for n in network if n["status"] != "failed"),
            "failed": sum(1 for n in network if n["status"] == "failed"),
            "errors": sum(
                1 for n in network if isinstance(n["status"], int) and n["status"] >= 400
            ),
        }
        (run_dir / "request.json").write_text(json.dumps(mask_secrets(raw_request), indent=2))
        (run_dir / "result.json").write_text(json.dumps(result, indent=2, default=str))
    return result


def list_runs(limit: int = 100) -> list[dict[str, Any]]:
    if not RUNS_DIR.exists():
        return []
    runs = []
    for d in sorted(RUNS_DIR.iterdir(), reverse=True)[:limit]:
        f = d / "result.json"
        if not f.exists():
            continue
        try:
            r = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        runs.append(
            {
                "id": r["id"],
                "url": r.get("url"),
                "status": r.get("status"),
                "ok": r.get("ok"),
                "error": bool(r.get("error")),
                "blocked": bool(r.get("block_signals")),
                "started_at": r.get("started_at"),
                "total": r.get("timings", {}).get("total"),
            }
        )
    return runs


def load_run(run_id: str) -> dict[str, Any] | None:
    d = RUNS_DIR / run_id
    if not (d / "result.json").exists():
        return None
    request = json.loads((d / "request.json").read_text()) if (d / "request.json").exists() else {}
    return {"result": json.loads((d / "result.json").read_text()), "request": request}


def delete_run(run_id: str) -> bool:
    d = RUNS_DIR / run_id
    if d.is_dir() and d.parent == RUNS_DIR:
        shutil.rmtree(d)
        return True
    return False
