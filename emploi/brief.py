from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from emploi.db import list_next_actions, list_saved_searches
from emploi.doctor import build_doctor_report


def build_brief(conn, *, today: str | None = None, limit: int = 5) -> dict[str, Any]:
    """Build Julien's daily operator brief from local SQLite state."""
    today_date = date.fromisoformat(today) if today else date.today()
    today_text = today_date.isoformat()
    since_text = (today_date - timedelta(days=6)).isoformat()

    best_offers = [_offer_to_dict(row) for row in conn.execute(
        """
        SELECT * FROM offers
        WHERE score >= 70
          AND is_active = 1
          AND status NOT IN ('rejected', 'archived', 'applied', 'sent', 'interview')
        ORDER BY score DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()]

    actions = list_next_actions(conn, limit=limit, today=today_text)
    due_followups = [_application_offer_to_dict(row) for row in conn.execute(
        """
        SELECT applications.id AS application_id, applications.next_action_at,
               offers.id AS offer_id, offers.title, offers.company, offers.score
        FROM applications
        JOIN offers ON offers.id = applications.offer_id
        WHERE applications.status = 'followup'
          AND applications.next_action_at != ''
          AND date(applications.next_action_at) <= date(?)
        ORDER BY date(applications.next_action_at) ASC, offers.score DESC, applications.id DESC
        LIMIT ?
        """,
        (today_text, limit),
    ).fetchall()]

    stale_sent = [_application_offer_to_dict(row) for row in conn.execute(
        """
        SELECT applications.id AS application_id,
               COALESCE(NULLIF(applications.last_contact_at, ''), applications.applied_at) AS due_date,
               offers.id AS offer_id, offers.title, offers.company, offers.score
        FROM applications
        JOIN offers ON offers.id = applications.offer_id
        WHERE applications.status = 'sent'
          AND date(COALESCE(NULLIF(applications.last_contact_at, ''), applications.applied_at)) <= date(?, '-14 days')
        ORDER BY date(COALESCE(NULLIF(applications.last_contact_at, ''), applications.applied_at)) ASC,
                 offers.score DESC, applications.id DESC
        LIMIT ?
        """,
        (today_text, limit),
    ).fetchall()]

    blockers = _build_blockers(conn)

    weekly_stats = {
        "since": since_text,
        "offers_created": _count(conn, "SELECT COUNT(*) FROM offers WHERE date(created_at) >= date(?)", (since_text,)),
        "applications_created": _count(conn, "SELECT COUNT(*) FROM applications WHERE date(applied_at) >= date(?)", (since_text,)),
        "drafts": _count(conn, "SELECT COUNT(*) FROM applications WHERE status = 'draft'"),
        "sent": _count(conn, "SELECT COUNT(*) FROM applications WHERE status = 'sent'"),
        "followups_due": len(due_followups),
        "stale_sent": len(stale_sent),
    }

    return {
        "date": today_text,
        "best_offers": best_offers,
        "actions": actions,
        "due_followups": due_followups,
        "stale_sent": stale_sent,
        "blockers": blockers,
        "weekly_stats": weekly_stats,
    }


def _count(conn, query: str, params: tuple[object, ...] = ()) -> int:
    return int(conn.execute(query, params).fetchone()[0])


def _offer_to_dict(row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "company": row["company"],
        "location": row["location"],
        "score": int(row["score"]),
        "status": row["status"],
        "source": row["external_source"] or row["source"],
        "url": row["browser_url"] or row["url"],
    }


def _application_offer_to_dict(row) -> dict[str, Any]:
    payload = {
        "application_id": int(row["application_id"]),
        "offer_id": int(row["offer_id"]),
        "title": row["title"],
        "company": row["company"],
        "score": int(row["score"]),
    }
    if "next_action_at" in row.keys():
        payload["due_date"] = row["next_action_at"]
    elif "due_date" in row.keys():
        payload["due_date"] = row["due_date"]
    return payload


def _build_blockers(conn) -> list[str]:
    blockers: list[str] = []
    doctor = build_doctor_report(probe_browser=False)
    browser = doctor.get("managed_browser", {})
    if browser.get("status") not in {"ok", "available"}:
        remediation = browser.get("remediation") or "Configurer EMPLOI_MANAGED_BROWSER_COMMAND."
        blockers.append(f"Managed Browser indisponible ({browser.get('status')}): {remediation}")
    if not list_saved_searches(conn, enabled=True):
        blockers.append("Aucun profil de recherche actif: lancer `emploi search-profile install-julien-defaults`.")
    return blockers
