"""Low-level SQLite access helpers.

The rest of the application should use these helpers instead of opening raw
connections ad hoc throughout the codebase.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from ..config import DB_PATH

@contextmanager
def get_conn():
    """Provide a SQLite connection with commit/close handling."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def qone(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def qall(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def execute(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    cur = conn.execute(sql, params)
    return cur.lastrowid
