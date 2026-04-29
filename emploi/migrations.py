from __future__ import annotations

import sqlite3


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate(conn: sqlite3.Connection) -> None:
    """Apply idempotent SQLite schema migrations."""
    _add_column_if_missing(conn, "offers", "external_source", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "offers", "external_id", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "offers", "browser_url", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "offers", "apply_url", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "offers", "is_active", "INTEGER NOT NULL DEFAULT 1")
    _add_column_if_missing(conn, "offers", "last_seen_at", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "offers", "last_refreshed_at", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "offers", "raw_browser_snapshot", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "offers", "raw_extracted_text", "TEXT NOT NULL DEFAULT ''")

    conn.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_offers_external_source_id
        ON offers (external_source, external_id)
        WHERE external_id IS NOT NULL AND external_id != '';

        CREATE TABLE IF NOT EXISTS browser_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site TEXT NOT NULL,
            profile TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '',
            current_url TEXT NOT NULL DEFAULT '',
            last_snapshot_label TEXT NOT NULL DEFAULT '',
            raw_status_json TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(site, profile)
        );

        CREATE TABLE IF NOT EXISTS offer_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            offer_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (offer_id) REFERENCES offers(id)
        );

        CREATE INDEX IF NOT EXISTS idx_offer_events_offer_id_created
        ON offer_events (offer_id, created_at, id);

        CREATE TABLE IF NOT EXISTS saved_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            query TEXT NOT NULL,
            where_text TEXT NOT NULL DEFAULT '',
            radius INTEGER NOT NULL DEFAULT 0,
            contract TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_run_at TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_saved_searches_enabled_name
        ON saved_searches (enabled, name);
        """
    )
    _add_column_if_missing(conn, "saved_searches", "notes", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "applications", "draft_path", "TEXT NOT NULL DEFAULT ''")
    conn.commit()
