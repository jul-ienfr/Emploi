from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterable

from emploi.migrations import migrate
from emploi.scoring import score_offer


DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "emploi" / "emploi.sqlite"


def db_path() -> Path:
    return Path(os.environ.get("EMPLOI_DB", DEFAULT_DB_PATH)).expanduser()


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    resolved = Path(path) if path is not None else db_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(resolved)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS offers (
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

        CREATE TABLE IF NOT EXISTS applications (
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
    migrate(conn)
    conn.commit()


def add_offer(
    conn: sqlite3.Connection,
    *,
    title: str,
    company: str = "",
    location: str = "",
    url: str = "",
    source: str = "manual",
    description: str = "",
    salary: str = "",
    remote: str = "",
    contract_type: str = "",
    notes: str = "",
    external_source: str = "",
    external_id: str = "",
    browser_url: str = "",
    apply_url: str = "",
    is_active: bool = True,
    last_seen_at: str = "",
    last_refreshed_at: str = "",
    raw_browser_snapshot: str = "",
    raw_extracted_text: str = "",
) -> int:
    scored = score_offer(
        {
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "notes": notes,
        }
    )
    cursor = conn.execute(
        """
        INSERT INTO offers (
            title, company, location, url, source, description, salary, remote,
            contract_type, score, score_reasons, notes, external_source,
            external_id, browser_url, apply_url, is_active, last_seen_at,
            last_refreshed_at, raw_browser_snapshot, raw_extracted_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title,
            company,
            location,
            url,
            source,
            description,
            salary,
            remote,
            contract_type,
            scored.score,
            "\n".join(scored.reasons),
            notes,
            external_source,
            external_id,
            browser_url,
            apply_url,
            1 if is_active else 0,
            last_seen_at,
            last_refreshed_at,
            raw_browser_snapshot,
            raw_extracted_text,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_offer(conn: sqlite3.Connection, offer_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()


def list_offers(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    min_score: int | None = None,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[object] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if min_score is not None:
        clauses.append("score >= ?")
        params.append(min_score)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return list(
        conn.execute(
            f"SELECT * FROM offers {where} ORDER BY score DESC, id DESC",
            params,
        ).fetchall()
    )


def update_offer_status(conn: sqlite3.Connection, offer_id: int, status: str) -> None:
    conn.execute(
        "UPDATE offers SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, offer_id),
    )
    conn.commit()


def rescore_offer(conn: sqlite3.Connection, offer_id: int) -> sqlite3.Row:
    offer = get_offer(conn, offer_id)
    if offer is None:
        raise ValueError(f"Offre introuvable: {offer_id}")
    result = score_offer(dict(offer))
    conn.execute(
        "UPDATE offers SET score = ?, score_reasons = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (result.score, "\n".join(result.reasons), offer_id),
    )
    conn.commit()
    updated = get_offer(conn, offer_id)
    assert updated is not None
    return updated


def add_application(
    conn: sqlite3.Connection,
    offer_id: int,
    *,
    status: str = "sent",
    notes: str = "",
) -> int:
    if get_offer(conn, offer_id) is None:
        raise ValueError(f"Offre introuvable: {offer_id}")
    cursor = conn.execute(
        "INSERT INTO applications (offer_id, status, notes) VALUES (?, ?, ?)",
        (offer_id, status, notes),
    )
    update_offer_status(conn, offer_id, "applied")
    conn.commit()
    return int(cursor.lastrowid)


def record_browser_session(
    conn: sqlite3.Connection,
    *,
    site: str,
    profile: str,
    status: str = "",
    current_url: str = "",
    last_snapshot_label: str = "",
    raw_status_json: str = "",
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO browser_sessions (
            site, profile, status, current_url, last_snapshot_label, raw_status_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(site, profile) DO UPDATE SET
            status = excluded.status,
            current_url = excluded.current_url,
            last_snapshot_label = excluded.last_snapshot_label,
            raw_status_json = excluded.raw_status_json,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        (site, profile, status, current_url, last_snapshot_label, raw_status_json),
    )
    session_id = int(cursor.fetchone()["id"])
    conn.commit()
    return session_id


def get_browser_session(
    conn: sqlite3.Connection,
    *,
    site: str,
    profile: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM browser_sessions WHERE site = ? AND profile = ?",
        (site, profile),
    ).fetchone()


def add_offer_event(
    conn: sqlite3.Connection,
    offer_id: int,
    *,
    event_type: str,
    message: str = "",
    payload_json: str = "",
) -> int:
    if get_offer(conn, offer_id) is None:
        raise ValueError(f"Offre introuvable: {offer_id}")
    cursor = conn.execute(
        """
        INSERT INTO offer_events (offer_id, event_type, message, payload_json)
        VALUES (?, ?, ?, ?)
        """,
        (offer_id, event_type, message, payload_json),
    )
    conn.commit()
    return int(cursor.lastrowid)


def list_offer_events(conn: sqlite3.Connection, offer_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT * FROM offer_events
            WHERE offer_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (offer_id,),
        ).fetchall()
    )


def add_saved_search(
    conn: sqlite3.Connection,
    *,
    name: str,
    query: str,
    where_text: str = "",
    radius: int = 0,
    contract: str = "",
    enabled: bool = True,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO saved_searches (name, query, where_text, radius, contract, enabled)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, query, where_text, radius, contract, 1 if enabled else 0),
    )
    conn.commit()
    return int(cursor.lastrowid)


def list_saved_searches(conn: sqlite3.Connection, *, enabled: bool | None = None) -> list[sqlite3.Row]:
    if enabled is None:
        return list(conn.execute("SELECT * FROM saved_searches ORDER BY name").fetchall())
    return list(
        conn.execute(
            "SELECT * FROM saved_searches WHERE enabled = ? ORDER BY name",
            (1 if enabled else 0,),
        ).fetchall()
    )


def get_saved_search(conn: sqlite3.Connection, search_id_or_name: int | str) -> sqlite3.Row | None:
    if isinstance(search_id_or_name, int) or str(search_id_or_name).isdigit():
        row = conn.execute("SELECT * FROM saved_searches WHERE id = ?", (int(search_id_or_name),)).fetchone()
        if row is not None:
            return row
    return conn.execute("SELECT * FROM saved_searches WHERE name = ?", (str(search_id_or_name),)).fetchone()


def update_saved_search_last_run(
    conn: sqlite3.Connection,
    search_id: int,
    timestamp: str | None = None,
) -> None:
    if timestamp is None:
        conn.execute("UPDATE saved_searches SET last_run_at = CURRENT_TIMESTAMP WHERE id = ?", (search_id,))
    else:
        conn.execute("UPDATE saved_searches SET last_run_at = ? WHERE id = ?", (timestamp, search_id))
    conn.commit()


def list_next_actions(conn: sqlite3.Connection, *, limit: int = 10) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    draft_rows = conn.execute(
        """
        SELECT applications.id AS application_id, offers.id AS offer_id, offers.title, offers.company, offers.score
        FROM applications
        JOIN offers ON offers.id = applications.offer_id
        WHERE applications.status = 'draft'
        ORDER BY offers.score DESC, applications.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    for row in draft_rows:
        actions.append(
            {
                "action": "Finaliser brouillon",
                "offer_id": row["offer_id"],
                "title": row["title"],
                "company": row["company"],
                "score": row["score"],
            }
        )

    remaining = max(0, limit - len(actions))
    if remaining:
        offer_rows = conn.execute(
            """
            SELECT * FROM offers
            WHERE external_source = 'france-travail'
              AND is_active = 1
              AND score >= 70
              AND status NOT IN ('applied', 'draft', 'rejected', 'archived')
              AND id NOT IN (SELECT offer_id FROM applications)
            ORDER BY score DESC, id DESC
            LIMIT ?
            """,
            (remaining,),
        ).fetchall()
        for row in offer_rows:
            actions.append(
                {
                    "action": "Vérifier/candidater FT",
                    "offer_id": row["id"],
                    "title": row["title"],
                    "company": row["company"],
                    "score": row["score"],
                }
            )
    return actions


def list_applications(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT applications.*, offers.title, offers.company
            FROM applications
            JOIN offers ON offers.id = applications.offer_id
            ORDER BY applications.id DESC
            """
        ).fetchall()
    )


def application_summary(conn: sqlite3.Connection) -> dict[str, int]:
    offer_count = conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
    interesting = conn.execute("SELECT COUNT(*) FROM offers WHERE status = 'interesting'").fetchone()[0]
    applied = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    rejected = conn.execute("SELECT COUNT(*) FROM offers WHERE status = 'rejected'").fetchone()[0]
    followup = conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'followup'").fetchone()[0]
    ft_offers = conn.execute("SELECT COUNT(*) FROM offers WHERE external_source = 'france-travail'").fetchone()[0]
    active_ft_offers = conn.execute(
        "SELECT COUNT(*) FROM offers WHERE external_source = 'france-travail' AND is_active = 1"
    ).fetchone()[0]
    draft_applications = conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'draft'").fetchone()[0]
    sent_applications = conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'sent'").fetchone()[0]
    return {
        "offers": int(offer_count),
        "interesting": int(interesting),
        "applied": int(applied),
        "rejected": int(rejected),
        "followup": int(followup),
        "ft_offers": int(ft_offers),
        "active_ft_offers": int(active_ft_offers),
        "draft_applications": int(draft_applications),
        "sent_applications": int(sent_applications),
    }
