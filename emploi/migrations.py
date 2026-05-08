from __future__ import annotations

import sqlite3


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _execute_script(conn: sqlite3.Connection, script: str) -> None:
    for statement in script.split(";"):
        if statement.strip():
            conn.execute(statement)


def _ensure_unique_external_ids(conn: sqlite3.Connection) -> int:
    duplicate = conn.execute(
        """
        SELECT external_source, external_id, COUNT(*) AS duplicate_count
        FROM offers
        WHERE external_source IS NOT NULL AND external_id IS NOT NULL AND external_id != ''
        GROUP BY external_source, external_id
        HAVING COUNT(*) > 1
        ORDER BY duplicate_count DESC, external_source, external_id
        LIMIT 1
        """
    ).fetchone()
    if duplicate:
        import warnings
        warnings.warn(
            "Cannot create idx_offers_external_source_id: duplicate external offer ids found "
            f"(source={duplicate[0]!r}, external_id={duplicate[1]!r}, count={duplicate[2]}). "
            "Deduplicate these offers or clear duplicate external_id values to enable the index."
        )
        return duplicate[2]
    return 0


def migrate(conn: sqlite3.Connection) -> None:
    """Apply idempotent SQLite schema migrations."""
    was_in_transaction = conn.in_transaction
    conn.execute("SAVEPOINT migrate_schema")
    try:
        _add_column_if_missing(conn, "offers", "external_source", "TEXT NOT NULL DEFAULT ''")
        if "source" in _table_columns(conn, "offers"):
            conn.execute(
                """
                UPDATE offers
                SET external_source = source
                WHERE external_source = '' AND source IS NOT NULL AND source != '' AND source != 'manual'
                """
            )
        _add_column_if_missing(conn, "offers", "external_id", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "offers", "browser_url", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "offers", "apply_url", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "offers", "is_active", "INTEGER NOT NULL DEFAULT 1")
        _add_column_if_missing(conn, "offers", "last_seen_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "offers", "last_refreshed_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "offers", "raw_browser_snapshot", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "offers", "raw_extracted_text", "TEXT NOT NULL DEFAULT ''")

        if not _ensure_unique_external_ids(conn):
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_offers_external_source_id
                ON offers (external_source, external_id)
                WHERE external_id IS NOT NULL AND external_id != ''
                """
            )

        _execute_script(
            conn,
            """
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

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        _add_column_if_missing(conn, "saved_searches", "requested_radius", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "saved_searches", "notes", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "saved_searches", "auto_apply_mode", "TEXT NOT NULL DEFAULT 'off'")
        _add_column_if_missing(conn, "saved_searches", "auto_apply_limit", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "saved_searches", "auto_apply_period", "TEXT NOT NULL DEFAULT 'weekly'")
        _add_column_if_missing(conn, "saved_searches", "auto_apply_strategy", "TEXT NOT NULL DEFAULT 'best-score'")
        _add_column_if_missing(conn, "saved_searches", "auto_apply_min_score", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "applications", "draft_path", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "offers", "score", "INTEGER NOT NULL DEFAULT 50")
        _add_column_if_missing(conn, "offers", "status", "TEXT NOT NULL DEFAULT 'new'")
        _add_column_if_missing(conn, "offers", "url", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "applications", "status", "TEXT NOT NULL DEFAULT 'draft'")
        _add_column_if_missing(conn, "applications", "next_action_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "applications", "last_contact_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "applications", "applied_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP")
        _execute_script(
            conn,
            """
            CREATE TABLE IF NOT EXISTS auto_apply_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                saved_search_id INTEGER NOT NULL,
                offer_id INTEGER NOT NULL,
                application_id INTEGER,
                mode TEXT NOT NULL,
                strategy TEXT NOT NULL,
                period_key TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (saved_search_id) REFERENCES saved_searches(id),
                FOREIGN KEY (offer_id) REFERENCES offers(id),
                FOREIGN KEY (application_id) REFERENCES applications(id)
            );

            CREATE INDEX IF NOT EXISTS idx_auto_apply_runs_profile_period
            ON auto_apply_runs (saved_search_id, period_key, status);

            CREATE INDEX IF NOT EXISTS idx_offers_active_score_id
            ON offers (is_active, score DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_offers_active_status_score_id
            ON offers (is_active, status, score DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_offers_source_active_score_id
            ON offers (external_source, is_active, score DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_offers_source_active_status_id
            ON offers (external_source, is_active, status, id);

            CREATE INDEX IF NOT EXISTS idx_offers_source_browser_url
            ON offers (external_source, browser_url)
            WHERE browser_url != '';

            CREATE INDEX IF NOT EXISTS idx_offers_url_id
            ON offers (url, id DESC)
            WHERE url != '';

            CREATE INDEX IF NOT EXISTS idx_offers_status
            ON offers (status);

            CREATE INDEX IF NOT EXISTS idx_applications_offer_status_id
            ON applications (offer_id, status, id DESC);

            CREATE INDEX IF NOT EXISTS idx_applications_status_next_action_id
            ON applications (status, next_action_at, id DESC, offer_id);

            CREATE INDEX IF NOT EXISTS idx_applications_status_contact_id
            ON applications (status, last_contact_at, applied_at, id DESC, offer_id);
            """
        )
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT migrate_schema")
        conn.execute("RELEASE SAVEPOINT migrate_schema")
        raise
    conn.execute("RELEASE SAVEPOINT migrate_schema")
    if not was_in_transaction:
        conn.commit()
