"""Where scraped records go. Two sinks with the same interface: write(kind, records) -> stats.

    with SqliteStore("data/scraper.db") as store:          # queryable, de-duplicated, upserts
        stats = store.write("reddit_post", [p.to_dict() for p in posts])
        print(stats)   # WriteStats(inserted=3, updated=5, unchanged=52)

    with JsonlSink("data/jsonl") as sink:                  # plain files, append-only
        sink.write("reddit_post", records)                 # -> data/jsonl/reddit_post.jsonl

Every record must have an "id" (for Reddit posts: the "t3_..." fullname). A "kind" names the
record type, so one database can hold posts, comments, etc. side by side.

SqliteStore also keeps the per-site daily page budget (see throttle.BudgetStore) and a log of
runs, so limits and history survive restarts.
"""

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

DEFAULT_DB = Path("data/scraper.db")
DEFAULT_JSONL_DIR = Path("data/jsonl")
SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class WriteStats:
    inserted: int = 0  # new ids
    updated: int = 0  # known id, data changed (e.g. score went up)
    unchanged: int = 0  # known id, identical data
    skipped: int = 0  # no "id", or (JSONL) already written

    def __add__(self, other: WriteStats) -> WriteStats:
        return WriteStats(
            self.inserted + other.inserted,
            self.updated + other.updated,
            self.unchanged + other.unchanged,
            self.skipped + other.skipped,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
        }


class Sink(Protocol):
    def write(self, kind: str, records: Iterable[dict[str, Any]]) -> WriteStats: ...
    def close(self) -> None: ...


def _check_kind(kind: str) -> str:
    if not kind or not kind.replace("_", "").isalnum():
        raise ValueError(f"kind must be letters/digits/underscores, got {kind!r}")
    return kind


# ---------------------------------------------------------------- JSONL


class JsonlSink:
    """One append-only file per kind: <directory>/<kind>.jsonl, one JSON object per line.

    De-duplicated by id across runs: ids already in the file are skipped, so the FIRST version
    of a record wins. (Use SqliteStore if you want updates such as new scores.) Each line gets a
    "_scraped_at" UTC timestamp.
    """

    def __init__(self, directory: str | Path = DEFAULT_JSONL_DIR):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._seen: dict[str, set[str]] = {}

    def path(self, kind: str) -> Path:
        return self.directory / f"{_check_kind(kind)}.jsonl"

    def _ids(self, kind: str) -> set[str]:
        if kind not in self._seen:
            ids: set[str] = set()
            path = self.path(kind)
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        ids.add(json.loads(line)["id"])
                    except json.JSONDecodeError, KeyError, TypeError:
                        continue  # tolerate a torn last line from a crash
            self._seen[kind] = ids
        return self._seen[kind]

    def write(self, kind: str, records: Iterable[dict[str, Any]]) -> WriteStats:
        stats, ids, now = WriteStats(), self._ids(kind), utc_now()
        with self.path(kind).open("a", encoding="utf-8") as f:
            for rec in records:
                rid = rec.get("id")
                if not rid or rid in ids:
                    stats.skipped += 1
                    continue
                f.write(json.dumps({**rec, "_scraped_at": now}, ensure_ascii=False) + "\n")
                ids.add(rid)
                stats.inserted += 1
        return stats

    def has(self, kind: str, rid: str) -> bool:
        return rid in self._ids(kind)

    def close(self) -> None:
        pass

    def __enter__(self) -> JsonlSink:
        return self

    def __exit__(self, *_) -> None:
        self.close()


# ---------------------------------------------------------------- SQLite

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    kind          TEXT NOT NULL,
    id            TEXT NOT NULL,
    data          TEXT NOT NULL CHECK (json_valid(data)),
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    seen_count    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (kind, id)
);

-- Reddit posts as normal columns, straight from the JSON (query this, not `records`).
CREATE VIEW IF NOT EXISTS reddit_posts AS
SELECT
    id,
    data ->> '$.subreddit'     AS subreddit,
    data ->> '$.title'         AS title,
    data ->> '$.author'        AS author,
    data ->> '$.score'         AS score,
    data ->> '$.comment_count' AS comment_count,
    data ->> '$.upvote_ratio'  AS upvote_ratio,
    data ->> '$.created_at'    AS created_at,
    data ->> '$.post_type'     AS post_type,
    data ->> '$.flair'         AS flair,
    data ->> '$.url'           AS url,
    data ->> '$.permalink'     AS permalink,
    data ->> '$.nsfw'          AS nsfw,
    data ->> '$.spoiler'       AS spoiler,
    first_seen_at, last_seen_at, seen_count
FROM records WHERE kind = 'reddit_post';

CREATE TABLE IF NOT EXISTS page_budget (
    day   TEXT NOT NULL,     -- local date, YYYY-MM-DD
    site  TEXT NOT NULL,     -- throttle.site_key(), e.g. reddit.com
    pages INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, site)
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    target      TEXT NOT NULL,   -- e.g. https://www.reddit.com/r/puzzles/new/
    reason      TEXT,            -- why it stopped (CollectResult.reason)
    records     INTEGER,
    inserted    INTEGER,
    updated     INTEGER,
    meta        TEXT CHECK (meta IS NULL OR json_valid(meta))
);
"""


class SqliteStore:
    """Records (upserted by kind+id), the daily page budget, and a run log, in one SQLite file.

    Re-seeing a record updates its data (scores/comments change over time), bumps seen_count and
    last_seen_at, and keeps first_seen_at, so you can tell when a post first appeared.
    """

    def __init__(self, path: str | Path = DEFAULT_DB):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")  # readers (e.g. the Lab) don't block writers
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)
        self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.db.commit()

    # --- records
    def write(self, kind: str, records: Iterable[dict[str, Any]]) -> WriteStats:
        kind, stats, now = _check_kind(kind), WriteStats(), utc_now()
        with self.db:  # one transaction per batch
            for rec in records:
                rid = rec.get("id")
                if not rid:
                    stats.skipped += 1
                    continue
                data = json.dumps(rec, ensure_ascii=False, sort_keys=True)
                old = self.db.execute(
                    "SELECT data FROM records WHERE kind=? AND id=?", (kind, rid)
                ).fetchone()
                self.db.execute(
                    """INSERT INTO records (kind, id, data, first_seen_at, last_seen_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT (kind, id) DO UPDATE SET
                           data = excluded.data,
                           last_seen_at = excluded.last_seen_at,
                           seen_count = seen_count + 1""",
                    (kind, rid, data, now, now),
                )
                if old is None:
                    stats.inserted += 1
                elif old["data"] != data:
                    stats.updated += 1
                else:
                    stats.unchanged += 1
        return stats

    def has(self, kind: str, rid: str) -> bool:
        q = "SELECT 1 FROM records WHERE kind=? AND id=?"
        return self.db.execute(q, (kind, rid)).fetchone() is not None

    def get(self, kind: str, rid: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT data FROM records WHERE kind=? AND id=?", (kind, rid)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def count(self, kind: str | None = None) -> int:
        if kind is None:
            return self.db.execute("SELECT count(*) FROM records").fetchone()[0]
        return self.db.execute("SELECT count(*) FROM records WHERE kind=?", (kind,)).fetchone()[0]

    def summary(self) -> dict[str, Any]:
        kinds = {
            r["kind"]: r["n"]
            for r in self.db.execute("SELECT kind, count(*) n FROM records GROUP BY kind")
        }
        subs = [
            dict(r)
            for r in self.db.execute(
                "SELECT subreddit, count(*) AS posts, max(last_seen_at) AS last_seen "
                "FROM reddit_posts GROUP BY subreddit ORDER BY posts DESC LIMIT 20"
            )
        ]
        runs = [dict(r) for r in self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 10")]
        return {"path": str(self.path), "kinds": kinds, "subreddits": subs, "runs": runs}

    # --- daily page budget (implements throttle.BudgetStore)
    def pages_on(self, day: str, site: str) -> int:
        row = self.db.execute(
            "SELECT pages FROM page_budget WHERE day=? AND site=?", (day, site)
        ).fetchone()
        return row["pages"] if row else 0

    def add_pages(self, day: str, site: str, n: int = 1) -> None:
        with self.db:
            self.db.execute(
                """INSERT INTO page_budget (day, site, pages) VALUES (?, ?, ?)
                   ON CONFLICT (day, site) DO UPDATE SET pages = pages + excluded.pages""",
                (day, site, n),
            )

    # --- run log
    def start_run(self, target: str, meta: dict[str, Any] | None = None) -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO runs (started_at, target, meta) VALUES (?, ?, ?)",
                (utc_now(), target, json.dumps(meta) if meta else None),
            )
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, reason: str, records: int, stats: WriteStats) -> None:
        with self.db:
            self.db.execute(
                "UPDATE runs SET finished_at=?, reason=?, records=?, inserted=?, updated=? "
                "WHERE id=?",
                (utc_now(), reason, records, stats.inserted, stats.updated, run_id),
            )

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> SqliteStore:
        return self

    def __exit__(self, *_) -> None:
        self.close()
