import sqlite3

import pytest

from emploi.db import (
    add_offer,
    add_offer_event,
    connect,
    get_browser_session,
    get_offer,
    init_db,
    list_offer_events,
    record_browser_session,
)
from emploi.migrations import migrate


EXPECTED_OFFER_COLUMNS = {
    "external_source",
    "external_id",
    "browser_url",
    "apply_url",
    "is_active",
    "last_seen_at",
    "last_refreshed_at",
    "raw_browser_snapshot",
    "raw_extracted_text",
}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _create_legacy_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            company TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            description TEXT NOT NULL DEFAULT '',
            salary TEXT NOT NULL DEFAULT '',
            remote TEXT NOT NULL DEFAULT '',
            contract_type TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            score INTEGER NOT NULL DEFAULT 50,
            score_reasons TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            offer_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'sent',
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_contact_at TEXT NOT NULL DEFAULT '',
            next_action_at TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (offer_id) REFERENCES offers(id)
        );
        """
    )
    conn.execute("INSERT INTO offers (title, company) VALUES (?, ?)", ("Legacy offer", "Legacy Co"))
    conn.commit()


def test_migrate_upgrades_existing_db_idempotently(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    _create_legacy_schema(conn)

    migrate(conn)
    migrate(conn)

    offer_columns = _column_names(conn, "offers")
    assert EXPECTED_OFFER_COLUMNS.issubset(offer_columns)
    assert {"id", "site", "profile", "status", "current_url", "updated_at"}.issubset(
        _column_names(conn, "browser_sessions")
    )
    assert {"id", "offer_id", "event_type", "message", "payload_json", "created_at"}.issubset(
        _column_names(conn, "offer_events")
    )

    legacy_offer = conn.execute("SELECT * FROM offers WHERE title = 'Legacy offer'").fetchone()
    assert legacy_offer["is_active"] == 1
    assert legacy_offer["external_source"] == ""


def test_init_db_runs_migrations_for_new_databases(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")

    init_db(conn)

    assert EXPECTED_OFFER_COLUMNS.issubset(_column_names(conn, "offers"))
    assert "browser_sessions" in {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def test_external_source_id_unique_when_external_id_present(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)

    add_offer(conn, title="A", external_source="france_travail", external_id="123")
    with pytest.raises(sqlite3.IntegrityError):
        add_offer(conn, title="B", external_source="france_travail", external_id="123")

    first_empty = add_offer(conn, title="Empty 1", external_source="france_travail", external_id="")
    second_empty = add_offer(conn, title="Empty 2", external_source="france_travail", external_id="")
    assert first_empty != second_empty


def test_add_offer_persists_browser_fields(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)

    offer_id = add_offer(
        conn,
        title="Technicien support",
        external_source="france_travail",
        external_id="FT-42",
        browser_url="https://candidat.francetravail.fr/offres/FT-42",
        apply_url="https://candidat.francetravail.fr/candidature/FT-42",
        is_active=False,
        last_seen_at="2026-04-29T10:00:00Z",
        last_refreshed_at="2026-04-29T10:05:00Z",
        raw_browser_snapshot='{"label":"offer"}',
        raw_extracted_text="Offre extraite",
    )

    offer = get_offer(conn, offer_id)
    assert offer is not None
    assert offer["external_source"] == "france_travail"
    assert offer["external_id"] == "FT-42"
    assert offer["browser_url"].endswith("FT-42")
    assert offer["apply_url"].endswith("FT-42")
    assert offer["is_active"] == 0
    assert offer["raw_browser_snapshot"] == '{"label":"offer"}'
    assert offer["raw_extracted_text"] == "Offre extraite"


def test_browser_session_helper_upserts_by_site_and_profile(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)

    first_id = record_browser_session(
        conn,
        site="france_travail",
        profile="default",
        status="open",
        current_url="https://candidat.francetravail.fr",
        raw_status_json='{"ok": true}',
    )
    second_id = record_browser_session(
        conn,
        site="france_travail",
        profile="default",
        status="ready",
        current_url="https://candidat.francetravail.fr/recherche",
        last_snapshot_label="search-results",
    )

    assert second_id == first_id
    session = get_browser_session(conn, site="france_travail", profile="default")
    assert session is not None
    assert session["status"] == "ready"
    assert session["current_url"].endswith("/recherche")
    assert session["last_snapshot_label"] == "search-results"
    assert session["raw_status_json"] == ""


def test_offer_event_helpers_record_and_list_events(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(conn, title="Support")

    event_id = add_offer_event(
        conn,
        offer_id,
        event_type="snapshot_refreshed",
        message="Snapshot updated",
        payload_json='{"snapshot":"abc"}',
    )
    add_offer_event(conn, offer_id, event_type="seen", message="Seen again")

    events = list_offer_events(conn, offer_id)
    assert [event["event_type"] for event in events] == ["seen", "snapshot_refreshed"]
    assert events[1]["id"] == event_id
    assert events[1]["message"] == "Snapshot updated"
    assert events[1]["payload_json"] == '{"snapshot":"abc"}'

    with pytest.raises(ValueError):
        add_offer_event(conn, 999, event_type="missing")
