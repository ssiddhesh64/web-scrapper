"""Scraper Lab server. Run: `uv run scraper-lab` and open http://127.0.0.1:8765

The server reloads automatically when anything under src/ changes, so edit the scraper code,
then just click Run again in the UI.
"""

import asyncio
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from ..browser import build_launch_kwargs
from ..config import HumanConfig, ThrottleConfig
from .runner import (
    PROJECT_ROOT,
    RUNS_DIR,
    ExtractRule,
    RunRequest,
    delete_run,
    describe_kwargs,
    list_runs,
    load_run,
    run_experiment,
)
from .schema import all_schemas, config_from_dict

STATIC_DIR = Path(__file__).parent / "static"
PROFILES_DIR = PROJECT_ROOT / "profiles"
RUN_ID = re.compile(r"^[0-9-]+$")

PRESETS: list[dict[str, Any]] = [
    {
        "name": "My site",
        "url": "https://www.siddheshsawant.in/",
        "extract": [{"name": "headings", "selector": "h1, h2", "attr": ""}],
    },
    {
        "name": "old.reddit: r/python (now redirects to login)",
        "url": "https://old.reddit.com/r/python/",
        "wait_for": "selector",
        "wait_selector": "div.thing",
        "extract": [
            {"name": "titles", "selector": "div.thing a.title", "attr": ""},
            {"name": "permalinks", "selector": "div.thing", "attr": "data-permalink"},
            {"name": "scores", "selector": "div.thing", "attr": "data-score"},
        ],
    },
    {
        "name": "reddit (new UI): r/python",
        "url": "https://www.reddit.com/r/python/",
        "wait_for": "selector",
        "wait_selector": "shreddit-post",
        "extract": [
            {"name": "titles", "selector": "shreddit-post", "attr": "post-title"},
            {"name": "permalinks", "selector": "shreddit-post", "attr": "permalink"},
            {"name": "scores", "selector": "shreddit-post", "attr": "score"},
        ],
    },
    {
        "name": "reddit JSON: r/python",
        "url": "https://www.reddit.com/r/python/.json",
        "wait_for": "none",
        "js": "(async () => { const r = await fetch(location.href); const j = await r.json();"
        " return j.data.children.slice(0, 10).map(c => c.data.title) })()",
    },
    {
        "name": "Session demo: 3 pages of my site (watch the throttle)",
        "url": "https://www.siddheshsawant.in/",
        "extra_urls": [
            "https://www.siddheshsawant.in/#experience",
            "https://www.siddheshsawant.in/#projects",
        ],
        "extract": [{"name": "headings", "selector": "h1, h2", "attr": ""}],
    },
    {
        "name": "Fingerprint: browserscan",
        "url": "https://www.browserscan.net/",
        "settle_ms": 8000,
        "full_page": True,
    },
    {
        "name": "Fingerprint: pixelscan",
        "url": "https://pixelscan.net/",
        "settle_ms": 15000,
        "full_page": True,
    },
    {
        "name": "Headers echo (httpbin)",
        "url": "https://httpbin.org/headers",
        "wait_for": "none",
    },
    {
        "name": "My public IP",
        "url": "https://api.ipify.org/?format=json",
        "wait_for": "none",
    },
]

app = FastAPI(title="Scraper Lab")
_run_lock = asyncio.Lock()


def _check_id(run_id: str) -> None:
    if not RUN_ID.match(run_id):
        raise HTTPException(400, "bad run id")


def _parse_run_request(body: dict[str, Any]) -> RunRequest:
    url = str(body.get("url", "")).strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "URL must start with http:// or https://")
    try:
        cfg = config_from_dict(body.get("config") or {})
        human = config_from_dict(body.get("human") or {}, HumanConfig)
        throttle = config_from_dict(body.get("throttle") or {}, ThrottleConfig)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"Invalid config: {e}") from e
    extra_urls = [u.strip() for u in body.get("extra_urls") or [] if str(u).strip()]
    for u in extra_urls:
        if not re.match(r"^https?://", u):
            raise HTTPException(400, f"Extra URL must start with http(s)://: {u}")
    rules = [
        ExtractRule(
            name=str(r.get("name") or r.get("selector")),
            selector=str(r["selector"]),
            attr=str(r.get("attr") or ""),
        )
        for r in body.get("extract") or []
        if r.get("selector")
    ]
    return RunRequest(
        url=url,
        config=cfg,
        wait_until=body.get("wait_until", "domcontentloaded"),
        wait_for=body.get("wait_for", "networkidle"),
        wait_selector=str(body.get("wait_selector") or ""),
        timeout_ms=int(body.get("timeout_ms") or 30_000),
        settle_ms=int(body.get("settle_ms") or 0),
        retries=max(0, min(5, int(body.get("retries") or 0))),
        backoff_base=max(0.0, float(body.get("backoff_base") or 0)),
        human=human,
        throttle=throttle,
        extra_urls=extra_urls[:20],
        screenshot=bool(body.get("screenshot", True)),
        full_page=bool(body.get("full_page", False)),
        extract=rules,
        js=str(body.get("js") or ""),
    )


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/schema")
async def schema():
    profiles = (
        sorted(f"profiles/{p.name}" for p in PROFILES_DIR.iterdir() if p.is_dir())
        if PROFILES_DIR.exists()
        else []
    )
    return {"sections": all_schemas(), "presets": PRESETS, "profiles": profiles}


@app.post("/api/preview")
async def preview(request: Request):
    body = await request.json()
    try:
        cfg = config_from_dict(body.get("config") or {})
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"Invalid browser config: {e}") from e
    return {"launch_kwargs": describe_kwargs(build_launch_kwargs(cfg))}


@app.post("/api/run")
async def run(request: Request):
    if _run_lock.locked():
        raise HTTPException(409, "A run is already in progress")
    body = await request.json()
    req = _parse_run_request(body)
    async with _run_lock:
        return await run_experiment(req, body)


@app.get("/api/status")
async def status():
    return {"running": _run_lock.locked()}


@app.get("/api/runs")
async def runs():
    return list_runs()


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    _check_id(run_id)
    data = load_run(run_id)
    if not data:
        raise HTTPException(404, "run not found")
    return data


@app.delete("/api/runs/{run_id}")
async def remove_run(run_id: str):
    _check_id(run_id)
    if not delete_run(run_id):
        raise HTTPException(404, "run not found")
    return {"deleted": run_id}


@app.get("/runs/{run_id}/screenshot.png")
async def screenshot(run_id: str):
    _check_id(run_id)
    path = RUNS_DIR / run_id / "screenshot.png"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/runs/{run_id}/page.html")
async def page_html(run_id: str, download: bool = False):
    _check_id(run_id)
    path = RUNS_DIR / run_id / "page.html"
    if not path.exists():
        raise HTTPException(404)
    # Scraped HTML is untrusted: render it with scripts disabled, in a sandboxed origin.
    headers = {"Content-Security-Policy": "sandbox; script-src 'none'"}
    if download:
        return FileResponse(path, filename=f"{run_id}.html", headers=headers)
    return FileResponse(path, media_type="text/html", headers=headers)


def main() -> None:
    import uvicorn

    print("Scraper Lab → http://127.0.0.1:8765")
    uvicorn.run(
        "web_scrapper.lab.server:app",
        host="127.0.0.1",
        port=8765,
        reload=True,
        reload_dirs=[str(PROJECT_ROOT / "src")],
    )


if __name__ == "__main__":
    main()
