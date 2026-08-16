"""Helpers partagés du dashboard (extraits de ``dashboard.py``)."""

from __future__ import annotations

import sqlite3
import time

from emploi.logging import get_logger

logger = get_logger("dashboard")

_start_time = time.monotonic()
_SOURCE_CACHE: list[str] | None = None
_SOURCE_CACHE_TS: float = 0


def _get_db() -> sqlite3.Connection:
    from emploi.db import connect

    conn = connect()
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _get_sources(conn: sqlite3.Connection) -> list[str]:
    global _SOURCE_CACHE, _SOURCE_CACHE_TS
    now = time.monotonic()
    if _SOURCE_CACHE is not None and now - _SOURCE_CACHE_TS < 300:
        return _SOURCE_CACHE
    _SOURCE_CACHE = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT COALESCE(NULLIF(external_source,''), source) FROM offers "
            "WHERE COALESCE(NULLIF(external_source,''), source) != '' ORDER BY 1"
        ).fetchall()
    ]
    _SOURCE_CACHE_TS = now
    return _SOURCE_CACHE


# ── Helpers métier (extraits de ``dashboard.py``) ──────────────────


def _ensure_history_table(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS offer_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            offer_id INTEGER NOT NULL,
            field TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            user TEXT DEFAULT 'dashboard',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (offer_id) REFERENCES offers(id)
        )"""
    )


def _log_change(conn, offer_id: int, field: str, old_value: str, new_value: str):
    _ensure_history_table(conn)
    conn.execute(
        "INSERT INTO offer_history (offer_id, field, old_value, new_value) VALUES (?, ?, ?, ?)",
        (offer_id, field, old_value, new_value),
    )


def _ensure_user_profiles(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            skills_json TEXT DEFAULT '[]',
            preferences_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )


def _ensure_followed_companies(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS followed_companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            followed_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
