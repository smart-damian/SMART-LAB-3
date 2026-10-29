"""Storage layer: keep a history of results in a local SQLite file.

SQLite is built into Python (no install, no server), which makes it ideal
for a phone. The history lets you see how levels move from day to day.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from levels import SymbolLevels

# PRIMARY KEY (symbol, run_date): one row per symbol per day. Running the
# scan twice in one day updates the row instead of creating duplicates.
SCHEMA = """
CREATE TABLE IF NOT EXISTS levels (
    symbol      TEXT    NOT NULL,
    run_date    TEXT    NOT NULL,   -- ISO date, e.g. 2026-10-24
    high_level  REAL    NOT NULL,
    low_level   REAL    NOT NULL,
    last_close  REAL,
    bars        INTEGER NOT NULL,
    PRIMARY KEY (symbol, run_date)
)
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (and create if needed) the database."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    return conn


def save_levels(conn: sqlite3.Connection, results: list[SymbolLevels],
                run_date: str | None = None) -> None:
    """Insert or replace today's rows. run_date is a parameter so tests can control it."""
    run_date = run_date or date.today().isoformat()
    rows = [(r.symbol, run_date, r.high, r.low, r.last_close, r.bars) for r in results]
    # The ? placeholders (never f-strings!) stop SQL injection and quoting bugs.
    # `with conn` commits on success and rolls back on error.
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO levels "
            "(symbol, run_date, high_level, low_level, last_close, bars) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )


def history(conn: sqlite3.Connection, symbol: str, limit: int = 10) -> list[tuple]:
    """Newest-first rows: (run_date, high_level, low_level, last_close)."""
    cur = conn.execute(
        "SELECT run_date, high_level, low_level, last_close FROM levels "
        "WHERE symbol = ? ORDER BY run_date DESC LIMIT ?",
        (symbol, limit),
    )
    return cur.fetchall()
