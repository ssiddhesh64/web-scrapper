"""CLI tests: parsing, config building, db commands, and `reddit` exit codes (browser faked)."""

import csv
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from web_scrapper import cli
from web_scrapper.extractors.reddit import RedditPost
from web_scrapper.fetcher import BlockKind, FetchResult
from web_scrapper.reddit import CollectResult
from web_scrapper.session import Visit
from web_scrapper.storage import SqliteStore


def make_post(i: int, score: int = 1) -> RedditPost:
    return RedditPost(
        id=f"t3_p{i}",
        subreddit="puzzles",
        title=f"Post {i}",
        permalink=f"https://www.reddit.com/r/puzzles/comments/p{i}/",
        score=score,
        comment_count=i,
    )


@pytest.fixture
def db(tmp_path) -> Path:
    path = tmp_path / "scraper.db"
    with SqliteStore(path) as store:
        store.write("reddit_post", [make_post(i, score=i * 10).to_dict() for i in range(1, 4)])
        run = store.start_run("https://www.reddit.com/r/puzzles/new/")
        from web_scrapper.storage import WriteStats

        store.finish_run(run, "limit reached", 3, WriteStats(inserted=3))
    return path


# ---------- parsing / config ----------


def test_parser_defaults():
    args = cli.build_parser().parse_args(["reddit", "puzzles"])
    assert (args.sort, args.limit, args.t, args.no_incremental) == ("new", 50, None, False)
    assert args.profile == Path("profiles/reddit") and args.locale == "en-IN"


def test_parser_rejects_bad_sort():
    with pytest.raises(SystemExit) as e:
        cli.build_parser().parse_args(["reddit", "puzzles", "--sort", "latest"])
    assert e.value.code == 2


def test_browser_config_from_flags(monkeypatch):
    monkeypatch.delenv("SCRAPER_PROXY_SERVER", raising=False)
    args = cli.build_parser().parse_args(["fetch", "https://x.com", "--headless", "--no-profile"])
    cfg = cli.browser_config(args)
    assert cfg.headless is True and cfg.profile_dir is None and cfg.extra == {"locale": "en-IN"}
    args = cli.build_parser().parse_args(["fetch", "https://x.com", "--profile", "profiles/a"])
    assert cli.browser_config(args).profile_dir == Path("profiles/a")


def test_browser_config_keeps_env_proxy(monkeypatch):
    monkeypatch.setenv("SCRAPER_PROXY_SERVER", "http://proxy:1")
    args = cli.build_parser().parse_args(["fetch", "https://x.com"])
    assert cli.browser_config(args).proxy.server == "http://proxy:1"


# ---------- db commands ----------


def test_db_summary(db, capsys):
    assert cli.main(["db", "--db", str(db), "summary"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "3  reddit_post" in out and "r/puzzles: 3" in out and "limit reached" in out


def test_db_posts_sorted_and_filtered(db, capsys):
    assert cli.main(["db", "--db", str(db), "posts", "--by", "score", "--limit", "2"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2 and "Post 3" in lines[0] and "Post 2" in lines[1]
    assert cli.main(["db", "--db", str(db), "posts", "--subreddit", "nope"]) == 0
    assert capsys.readouterr().out == ""


def test_db_export_csv_and_jsonl(db, tmp_path):
    out_csv, out_jsonl = tmp_path / "x" / "posts.csv", tmp_path / "posts.jsonl"
    assert cli.main(["db", "--db", str(db), "export", "-o", str(out_csv)]) == 0
    rows = list(csv.DictReader(out_csv.open()))
    assert len(rows) == 3 and {"id", "title", "score"} <= set(rows[0])
    assert cli.main(["db", "--db", str(db), "export", "-o", str(out_jsonl)]) == 0
    assert [json.loads(x)["id"] for x in out_jsonl.read_text().splitlines()] == [
        "t3_p1",
        "t3_p2",
        "t3_p3",
    ]
    assert cli.main(["db", "--db", str(db), "export", "-o", str(tmp_path / "a.txt")]) == 2


def test_db_missing_file(tmp_path, capsys):
    assert cli.main(["db", "--db", str(tmp_path / "none.db"), "summary"]) == cli.EXIT_ERROR
    assert "no database" in capsys.readouterr().err


# ---------- reddit command (browser + collector faked) ----------


def fake_result(reason: str, status: int = 200, block=BlockKind.NONE, n: int = 3):
    fr = FetchResult(url="https://www.reddit.com/r/puzzles/new/", status=status, block=block)
    res = CollectResult(url=fr.url, reason=reason, rounds=2)
    res.visit = Visit(url=fr.url, result=fr, waited=0)
    res.posts = [make_post(i) for i in range(n)]
    return res


def patch_browser(monkeypatch, result, calls):
    @asynccontextmanager
    async def fake_open_session(cfg, **kwargs):
        calls["cfg"], calls["kwargs"] = cfg, kwargs
        yield object()

    async def fake_collect(session, name, **kwargs):
        calls["collect"] = kwargs
        return result

    monkeypatch.setattr(cli, "open_session", fake_open_session)
    monkeypatch.setattr(cli, "collect_subreddit", fake_collect)


def run_reddit(tmp_path, *extra):
    argv = ["reddit", "puzzles", "--db", str(tmp_path / "s.db"), "--jsonl", str(tmp_path / "j")]
    return cli.main([*argv, *extra])


def test_reddit_ok_stores_and_is_incremental(monkeypatch, tmp_path, capsys):
    calls: dict = {}
    patch_browser(monkeypatch, fake_result("limit reached"), calls)
    assert run_reddit(tmp_path, "--limit", "3") == cli.EXIT_OK
    assert "3 new" in capsys.readouterr().out
    assert calls["collect"]["seen"] is not None  # incremental for sort=new
    assert calls["kwargs"]["budget_store"] is not None  # daily budget enforced
    with SqliteStore(tmp_path / "s.db") as store:
        assert store.count("reddit_post") == 3 and store.summary()["runs"][0]["reason"]
    assert (tmp_path / "j" / "reddit_post.jsonl").exists()


def test_reddit_json_output_goes_to_stdout(monkeypatch, tmp_path, capsys):
    patch_browser(monkeypatch, fake_result("limit reached"), {})
    assert run_reddit(tmp_path, "--json", "--no-jsonl") == 0
    out, err = capsys.readouterr()
    assert [json.loads(x)["id"] for x in out.splitlines()] == ["t3_p0", "t3_p1", "t3_p2"]
    assert "stopped: limit reached" in err
    assert not (tmp_path / "j").exists()


def test_reddit_not_incremental_for_top(monkeypatch, tmp_path):
    calls: dict = {}
    patch_browser(monkeypatch, fake_result("limit reached"), calls)
    assert run_reddit(tmp_path, "--sort", "top", "--time", "week") == 0
    assert calls["collect"]["seen"] is None and calls["collect"]["t"] == "week"


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (fake_result("blocked on load: forbidden", 403, BlockKind.FORBIDDEN, 0), cli.EXIT_BLOCKED),
        (fake_result("blocked while scrolling: Your request has been blocked"), cli.EXIT_BLOCKED),
        (fake_result("throttle: reddit.com: daily budget of 400 pages used"), cli.EXIT_STOPPED),
        (fake_result("caught up (12 posts already stored)"), cli.EXIT_OK),
    ],
)
def test_reddit_exit_codes(monkeypatch, tmp_path, result, code):
    patch_browser(monkeypatch, result, {})
    assert run_reddit(tmp_path) == code


def test_reddit_bad_input(tmp_path, capsys):
    assert cli.main(["reddit", "bad name!", "--db", str(tmp_path / "s.db")]) == cli.EXIT_USAGE
    assert cli.main(["reddit", "puzzles", "--limit", "0"]) == cli.EXIT_USAGE
    assert cli.main(["reddit", "puzzles", "--sort", "new", "--time", "week"]) == cli.EXIT_USAGE
