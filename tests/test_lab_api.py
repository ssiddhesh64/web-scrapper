"""Lab server API tests (FastAPI TestClient). Nothing here launches a browser: /api/run is only
exercised for its input validation, which happens before any browser starts."""

import json

import pytest
from fastapi.testclient import TestClient

from web_scrapper.config import BrowserConfig, HumanConfig, ThrottleConfig
from web_scrapper.lab import runner, server
from web_scrapper.lab.schema import config_from_dict, config_schema
from web_scrapper.storage import SqliteStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    monkeypatch.setattr(runner, "RUNS_DIR", runs)
    monkeypatch.setattr(server, "RUNS_DIR", runs)
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "scraper.db")
    monkeypatch.setattr(server, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.delenv("SCRAPER_PROXY_SERVER", raising=False)
    return TestClient(server.app)


def make_run(run_id: str, html: str = "<html><script>alert(1)</script></html>"):
    d = runner.RUNS_DIR / run_id
    d.mkdir(parents=True)
    (d / "result.json").write_text(
        json.dumps({"id": run_id, "url": "https://x.com", "status": 200, "ok": True})
    )
    (d / "request.json").write_text(json.dumps({"url": "https://x.com"}))
    (d / "page.html").write_text(html)
    (d / "top.png").write_bytes(b"png")
    return d


# ---------- schema ----------


def test_schema_lists_sections_presets_extractors(client):
    d = client.get("/api/schema").json()
    assert set(d["sections"]) == {"config", "human", "throttle"}
    names = [f["name"] for f in d["sections"]["throttle"]]
    assert "max_pages_per_day" in names
    assert d["extractors"] == ["reddit_listing"] and len(d["presets"]) >= 5


def test_schema_generation_kinds():
    kinds = {f["name"]: f["kind"] for f in config_schema(BrowserConfig)}
    assert kinds == {
        "os": "choice",
        "headless": "choice",
        "humanize": "boolnum",
        "max_screen": "ints",
        "proxy": "group",
        "geoip": "choice",
        "block_images": "bool",
        "profile_dir": "text",
        "extra": "json",
    }


def test_config_from_dict_round_trip():
    cfg = config_from_dict(
        {"max_screen": [1440, 900], "profile_dir": "", "proxy": {"server": ""}, "geoip": None}
    )
    assert cfg.max_screen == (1440, 900) and cfg.profile_dir is None and cfg.proxy is None
    assert config_from_dict({"scrolls": [2, 5]}, HumanConfig).scrolls == (2, 5)
    assert config_from_dict({"break_every": [5, 9]}, ThrottleConfig).break_every == (5, 9)


# ---------- preview / validation ----------


def test_preview_masks_proxy_password(client):
    body = {"config": {"proxy": {"server": "http://p:1", "username": "u", "password": "secret"}}}
    kw = client.post("/api/preview", json=body).json()["launch_kwargs"]
    assert kw["proxy"]["password"] == "••••••" and kw["geoip"] is True  # auto geoip with proxy


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"url": "file:///etc/passwd"}, "http"),
        ({"url": "https://x.com", "config": {"headless": "virtual"}}, "Linux"),
        ({"url": "https://x.com", "throttle": {"min_interval": 9, "max_interval": 1}}, "interval"),
        ({"url": "https://x.com", "extra_urls": ["ftp://x"]}, "Extra URL"),
        ({"url": "https://x.com", "extractor": "nope"}, "Unknown extractor"),
    ],
)
def test_run_rejects_bad_input_before_launching(client, body, fragment):
    r = client.post("/api/run", json=body)
    assert r.status_code == 400 and fragment in r.json()["detail"]


def test_run_refuses_while_busy(client):
    import asyncio

    async def hold():
        await server._run_lock.acquire()

    asyncio.run(hold())
    try:
        assert client.post("/api/run", json={"url": "https://x.com"}).status_code == 409
        assert client.get("/api/status").json() == {"running": True}
    finally:
        server._run_lock.release()


# ---------- runs / files ----------


def test_runs_list_get_delete(client):
    make_run("20261114-100000-001")
    make_run("20261114-110000-002")
    ids = [r["id"] for r in client.get("/api/runs").json()]
    assert ids == ["20261114-110000-002", "20261114-100000-001"]  # newest first
    assert client.get("/api/runs/20261114-100000-001").json()["request"]["url"] == "https://x.com"
    assert client.delete("/api/runs/20261114-100000-001").status_code == 200
    assert client.get("/api/runs/20261114-100000-001").status_code == 404


@pytest.mark.parametrize("bad", ["..", "abc", "20261114-x"])
def test_run_ids_are_validated(client, bad):
    assert client.get(f"/api/runs/{bad}").status_code in (400, 404)
    assert client.delete(f"/api/runs/{bad}").status_code in (400, 404, 405)


def test_scraped_html_is_served_sandboxed(client):
    make_run("20261114-120000-003")
    r = client.get("/runs/20261114-120000-003/page.html")
    assert r.status_code == 200 and "sandbox" in r.headers["content-security-policy"]
    assert client.get("/runs/20261114-120000-003/top.png").content == b"png"
    assert client.get("/runs/20261114-120000-003/other.png").status_code == 404


# ---------- database summary ----------


def test_db_summary_empty_then_filled(client):
    assert client.get("/api/db").json()["kinds"] == {}
    with SqliteStore(server.DB_PATH) as store:
        store.write("reddit_post", [{"id": "t3_a", "subreddit": "puzzles", "title": "A"}])
    d = client.get("/api/db").json()
    assert d["kinds"] == {"reddit_post": 1} and d["subreddits"][0]["subreddit"] == "puzzles"


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "Scraper Lab" in r.text
