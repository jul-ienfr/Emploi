from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterable

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
            contract_type, score, score_reasons, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    return {
        "offers": int(offer_count),
        "interesting": int(interesting),
        "applied": int(applied),
        "rejected": int(rejected),
        "followup": int(followup),
    }
