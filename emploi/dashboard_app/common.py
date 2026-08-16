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
