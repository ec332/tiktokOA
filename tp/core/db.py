"""SQLite + filesystem layout under data/ (override with TP_DATA)."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sut_ref TEXT, mode TEXT NOT NULL, stub INTEGER NOT NULL, unattended INTEGER NOT NULL,
    started TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stage_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL, stage TEXT NOT NULL, stage_version TEXT,
    status TEXT NOT NULL, inputs TEXT NOT NULL DEFAULT '{}', outputs TEXT NOT NULL DEFAULT '{}',
    reason TEXT, started TEXT, finished TEXT
);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY, type TEXT NOT NULL, schema_version INTEGER NOT NULL,
    stage TEXT, stage_version TEXT, parents TEXT NOT NULL, labels TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL, stage_execution INTEGER NOT NULL, payload TEXT NOT NULL,
    status TEXT NOT NULL, decision TEXT, detail TEXT, decided_by TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_item INTEGER, decision TEXT NOT NULL, detail TEXT, decided_by TEXT, reason TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER, stage_execution INTEGER, kind TEXT NOT NULL, payload TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    key TEXT PRIMARY KEY, model TEXT NOT NULL, params TEXT NOT NULL,
    response_hash TEXT NOT NULL, at TEXT NOT NULL
);
"""


def data_dir() -> Path:
    d = Path(os.environ.get("TP_DATA", "data"))
    for sub in ("artifacts", "runs", "llm_cache"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Autocommit connection, closed on exit. Use `with c:` inside for a transaction."""
    conn = sqlite3.connect(data_dir() / "platform.db", timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # WAL: the API process and a CLI process can both use the db at once.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    try:
        yield conn
    finally:
        conn.close()


def now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
