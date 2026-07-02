from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

from emploi.applications import create_application_draft
from emploi.db import add_offer_event, get_saved_search, list_saved_searches
from emploi.france_travail.flows import _matches_terms


@dataclass(frozen=True)
class AutoApplyRunResult:
    saved_search_id: int
    profile_name: str
    mode: str
    strategy: str
    status: str
    message: str
    offer_id: int | None = None
    title: str = ""
    application_id: int | None = None
    draft_path: Path | None = None


def period_key(period: str, today: str | None = None) -> str:
    normalized = period.strip().lower()
    if normalized == "run":
        return f"run:{uuid4().hex}"
    today_date = date.fromisoformat(today) if today else date.today()
    if normalized == "daily":
        return f"daily:{today_date.isoformat()}"
    if normalized == "weekly":
        iso = today_date.isocalendar()
        return f"weekly:{iso.year}-W{iso.week:02d}"
    if normalized == "monthly":
        return f"monthly:{today_date.year}-{today_date.month:02d}"
    raise ValueError(f"Période auto-apply invalide: {period}")


def _quota_used(conn, saved_search_id: int, key: str) -> int:
    return int(
        conn.execute(
            """
            SELECT COUNT(*) FROM auto_apply_runs
            WHERE saved_search_id = ?
              AND period_key = ?
              AND status IN ('drafted', 'opened', 'submitted')
            """,
            (saved_search_id, key),
        ).fetchone()[0]
    )


def _candidate_order(strategy: str) -> str:
    if strategy == "worst-score":
        return "offers.score ASC, offers.id ASC"
    if strategy == "newest":
        return "datetime(offers.created_at) DESC, offers.id DESC"
    if strategy == "oldest":
        return "datetime(offers.created_at) ASC, offers.id ASC"
    return "offers.score DESC, offers.id DESC"


def _select_candidate(conn, saved, *, strategy: str, min_score: int):
    order_by = _candidate_order(strategy)
    filters = [
        "offers.external_source = 'france-travail'",
        "offers.is_active = 1",
        "offers.score >= ?",
        "offers.status NOT IN ('draft', 'applied', 'sent', 'followup', 'response', 'rejected', 'interview', 'archived')",
        "NOT EXISTS (SELECT 1 FROM applications WHERE applications.offer_id = offers.id)",
    ]
    params: list[object] = [min_score]
    query = str(saved["query"] or "").strip()
    contract = str(saved["contract"] or "").strip().lower()
    if contract:
        filters.append("LOWER(offers.contract_type) LIKE ?")
        params.append(f"%{contract}%")
    where_tokens = [token for token in str(saved["where_text"] or "").strip().lower().split() if len(token) >= 3]
    if where_tokens:
        filters.append("(" + " OR ".join(["LOWER(offers.location) LIKE ?"] * len(where_tokens)) + ")")
        params.extend(f"%{token}%" for token in where_tokens)
    where_clause = "\n          AND ".join(filters)
    candidates = conn.execute(
        f"""
        SELECT offers.*
        FROM offers
        WHERE {where_clause}
        ORDER BY {order_by}
        """,
        params,
    ).fetchall()
    if not query:
        return candidates[0] if candidates else None
    for candidate in candidates:
        text = " ".join([candidate["title"] or "", candidate["description"] or "", candidate["company"] or ""])
        if _matches_terms(text, query):
            return candidate
    return None


def _record_run(
    conn,
    *,
    saved_search_id: int,
    offer_id: int,
    application_id: int | None,
    mode: str,
    strategy: str,
    key: str,
    status: str,
    message: str,
) -> None:
    conn.execute(
        """
        INSERT INTO auto_apply_runs (
            saved_search_id, offer_id, application_id, mode, strategy, period_key, status, message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (saved_search_id, offer_id, application_id, mode, strategy, key, status, message),
    )
    conn.commit()
    add_offer_event(conn, offer_id, event_type="auto_apply", message=message)


def run_auto_apply_for_saved_search(
    conn,
    search_id_or_name: int | str,
    *,
    drafts_dir: str | Path | None = None,
    today: str | None = None,
) -> AutoApplyRunResult:
    saved = get_saved_search(conn, search_id_or_name)
    if saved is None:
        raise ValueError(f"Profil de recherche introuvable: {search_id_or_name}")
    mode = str(saved["auto_apply_mode"] or "off")
    strategy = str(saved["auto_apply_strategy"] or "best-score")
    profile_name = str(saved["name"])
    saved_search_id = int(saved["id"])
    if mode == "off":
        return AutoApplyRunResult(saved_search_id, profile_name, mode, strategy, "skipped", "Auto-apply désactivé")

    limit = int(saved["auto_apply_limit"] or 0)
    key = period_key(str(saved["auto_apply_period"] or "weekly"), today=today)
    used = _quota_used(conn, saved_search_id, key)
    if used >= limit:
        return AutoApplyRunResult(
            saved_search_id,
            profile_name,
            mode,
            strategy,
            "quota_reached",
            f"Quota atteint ({used}/{limit}) pour {key}",
        )

    offer = _select_candidate(conn, saved, strategy=strategy, min_score=int(saved["auto_apply_min_score"] or 0))
    if offer is None:
        return AutoApplyRunResult(saved_search_id, profile_name, mode, strategy, "no_candidate", "Aucune offre éligible")

    offer_id = int(offer["id"])
    title = str(offer["title"])
    if mode == "submit":
        message = "Mode submit configuré mais non exécuté: soumission automatique live non encore supportée"
        _record_run(
            conn,
            saved_search_id=saved_search_id,
            offer_id=offer_id,
            application_id=None,
            mode=mode,
            strategy=strategy,
            key=key,
            status="guarded",
            message=message,
        )
        return AutoApplyRunResult(saved_search_id, profile_name, mode, strategy, "guarded", message, offer_id, title)

    draft = create_application_draft(conn, offer_id, drafts_dir=drafts_dir)
    status = "drafted" if mode == "draft" else "opened"
    message = f"Auto-apply {mode}: offre sélectionnée #{offer_id} — {title}"
    _record_run(
        conn,
        saved_search_id=saved_search_id,
        offer_id=offer_id,
        application_id=draft.application_id,
        mode=mode,
        strategy=strategy,
        key=key,
        status=status,
        message=message,
    )
    return AutoApplyRunResult(
        saved_search_id,
        profile_name,
        mode,
        strategy,
        status,
        message,
        offer_id,
        title,
        draft.application_id,
        draft.draft_path,
    )


def run_auto_apply_for_enabled_profiles(
    conn,
    *,
    drafts_dir: str | Path | None = None,
    today: str | None = None,
) -> list[AutoApplyRunResult]:
    results: list[AutoApplyRunResult] = []
    for saved in list_saved_searches(conn, enabled=True):
        if str(saved["auto_apply_mode"] or "off") == "off":
            continue
        results.append(run_auto_apply_for_saved_search(conn, int(saved["id"]), drafts_dir=drafts_dir, today=today))
    return results
