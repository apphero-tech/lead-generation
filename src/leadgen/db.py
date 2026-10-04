"""SQLite staging database. Plain SQL kept close to Postgres syntax for a later migration."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS institutions (
    unitid        TEXT PRIMARY KEY,
    state         TEXT NOT NULL,
    name          TEXT NOT NULL,
    website       TEXT,
    city          TEXT,
    chief_name    TEXT,
    chief_title   TEXT,
    main_phone    TEXT,
    sector        TEXT,
    system_name   TEXT,
    is_system     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pages (
    url           TEXT PRIMARY KEY,
    host          TEXT,
    status        INTEGER,
    fetched_at    TEXT,
    content_type  TEXT,
    html          TEXT,
    source_date   TEXT,
    error         TEXT
);

CREATE TABLE IF NOT EXISTS institution_pages (
    unitid        TEXT NOT NULL,
    url           TEXT NOT NULL,
    depth         INTEGER,
    PRIMARY KEY (unitid, url)
);

CREATE TABLE IF NOT EXISTS persons (
    id                 INTEGER PRIMARY KEY,
    unitid             TEXT NOT NULL,
    person_key         TEXT NOT NULL,
    unit               TEXT,
    first_name         TEXT,
    last_name          TEXT,
    email              TEXT,
    email_status       TEXT,
    email_source       TEXT,
    phone              TEXT,
    phone_status       TEXT,
    phone_source       TEXT,
    responsibilities   TEXT,
    last_verified_date TEXT,
    manual_check       INTEGER NOT NULL DEFAULT 0,
    check_reasons      TEXT,
    UNIQUE (unitid, person_key)
);

CREATE TABLE IF NOT EXISTS person_roles (
    person_id     INTEGER NOT NULL,
    profile_id    TEXT NOT NULL,
    found_title   TEXT,
    match_quality TEXT,
    source_url    TEXT NOT NULL,
    source_date   TEXT,
    PRIMARY KEY (person_id, profile_id, source_url)
);

CREATE TABLE IF NOT EXISTS email_patterns (
    domain        TEXT PRIMARY KEY,
    pattern       TEXT,
    support       INTEGER,
    total         INTEGER,
    examples      TEXT
);

CREATE TABLE IF NOT EXISTS step_status (
    unitid        TEXT NOT NULL,
    step          TEXT NOT NULL,
    status        TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    detail        TEXT,
    PRIMARY KEY (unitid, step)
);

CREATE TABLE IF NOT EXISTS extraction_stats (
    unitid          TEXT PRIMARY KEY,
    raw_candidates  INTEGER,
    persons         INTEGER,
    pages_scanned   INTEGER
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was first created."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(persons)")}
    if "unit" not in cols:
        conn.execute("ALTER TABLE persons ADD COLUMN unit TEXT")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(institutions)")}
    for col in ("city", "chief_name", "chief_title", "main_phone"):
        if col not in cols:
            conn.execute(f"ALTER TABLE institutions ADD COLUMN {col} TEXT")
    conn.commit()


def set_step(conn: sqlite3.Connection, unitid: str, step: str, status: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO step_status (unitid, step, status, updated_at, detail) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (unitid, step) DO UPDATE SET status = excluded.status, "
        "updated_at = excluded.updated_at, detail = excluded.detail",
        (unitid, step, status, now_iso(), detail),
    )
    conn.commit()


def get_step(conn: sqlite3.Connection, unitid: str, step: str):
    row = conn.execute(
        "SELECT status FROM step_status WHERE unitid = ? AND step = ?", (unitid, step)
    ).fetchone()
    return row["status"] if row else None
