"""Dashboard pages — routes HTML (blueprint ``pages``).

Extrait de ``dashboard.py`` (tranche 1 du découpage du monolithe).
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from flask import Blueprint, render_template, request

from emploi.dashboard_app.common import _get_db, _get_sources

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
def index():
    q = request.args.get("q", "").strip()
    source_filter = request.args.get("source", "").strip()
    status = request.args.get("status", "").strip()
    sort = request.args.get("sort", "score").strip()
    min_score = request.args.get("min_score", "").strip()
    max_score = request.args.get("max_score", "").strip()
    page = max(1, int(request.args.get("page", 1)))
    per_page = int(os.environ.get("EMPLOI_DASHBOARD_PER_PAGE", "30"))

    conn = _get_db()
    try:
        where = ["is_active = 1"]
        params: list = []

        if q:
            where.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        if source_filter:
            where.append("(external_source = ? OR source = ?)")
            params.extend([source_filter, source_filter])
        if status:
            where.append("status = ?")
            params.append(status)
        if min_score:
            where.append("score >= ?")
            params.append(int(min_score))
        if max_score:
            where.append("score <= ?")
            params.append(int(max_score))

        where_clause = "WHERE " + " AND ".join(where)

        # Sort
        sort_map = {
            "score": "score DESC, id DESC",
            "date": "created_at DESC, id DESC",
            "company": "company ASC, score DESC",
            "location": "location ASC, score DESC",
            "title": "title ASC",
        }
        order = sort_map.get(sort, "score DESC, id DESC")

        # Count
        count_row = conn.execute(f"SELECT COUNT(*) FROM offers {where_clause}", params).fetchone()
        total = count_row[0]
        total_pages = max(1, (total + per_page - 1) // per_page)

        # Fetch page
        offset = (page - 1) * per_page
        offers = conn.execute(
            f"SELECT * FROM offers {where_clause} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

        sources = _get_sources(conn)

        # Build params string for pagination
        param_parts = []
        if q:
            param_parts.append(f"q={q}")
        if source_filter:
            param_parts.append(f"source={source_filter}")
        if status:
            param_parts.append(f"status={status}")
        if sort != "score":
            param_parts.append(f"sort={sort}")
        if min_score:
            param_parts.append(f"min_score={min_score}")
        if max_score:
            param_parts.append(f"max_score={max_score}")
        params_str = "&".join(param_parts)

        # Use application_summary for header stats
        from emploi.db import application_summary

        stats = application_summary(conn)

        return render_template(
            "index.html",
            offers=offers,
            sources=sources,
            q=q,
            selected_source=source_filter,
            status=status,
            sort=sort,
            min_score=min_score,
            max_score=max_score,
            page=page,
            total_pages=total_pages,
            total=total,
            params=params_str,
            stats=stats,
        )
    finally:
        conn.close()


@pages_bp.route("/share/<token>")
def share_public(token):
    # Simple share page — reads offer_id from token (reverse lookup)
    conn = _get_db()
    try:
        offers = conn.execute("SELECT * FROM offers WHERE is_active = 1 ORDER BY score DESC LIMIT 50").fetchall()
        # For simplicity, show first offer matching token hash
        for o in offers:
            expected = hashlib.sha1(f"{o['id']}-share".encode()).hexdigest()[:12]
            if expected == token:
                return render_template("offer.html", offer=o, events=[], notes=[])
        return render_template("error.html", code=404, message="Offre introuvable")
    finally:
        conn.close()


@pages_bp.route("/rss")
def rss_feed():
    conn = _get_db()
    try:
        offers = conn.execute(
            "SELECT * FROM offers WHERE is_active = 1 "
            "AND created_at >= datetime('now', '-1 day') "
            "ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        items = ""
        for o in offers:
            url = o["url"] or f"/offer/{o['id']}"
            items += f"""<item>
                <title>{o["title"]}</title>
                <link>{url}</link>
                <description>{(o["description"] or "")[:500]}</description>
                <pubDate>{o["created_at"]}</pubDate>
            </item>\n"""
        from flask import Response

        rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Emploi — Nouvelles offres</title>
<link>/</link>
<description>Dernières offres d'emploi</description>
{items}
</channel>
</rss>"""
        return Response(rss, mimetype="application/rss+xml")
    finally:
        conn.close()


@pages_bp.route("/profiles")
def profiles_page():
    from emploi.config import list_accounts
    from emploi.db import list_saved_searches

    browser_profiles = list_accounts()
    searches = list_saved_searches(conn=_get_db()) if True else []
    conn = _get_db()
    try:
        searches = list_saved_searches(conn)
    finally:
        conn.close()

    # Daemon status (basic)
    daemon_status = {"ok": False, "last_cycle": "N/A", "total_offers": 0, "errors": 0}

    return render_template(
        "profiles.html",
        browser_profiles=browser_profiles,
        searches=searches,
        daemon_status=daemon_status,
    )


@pages_bp.route("/compare")
def compare_page():
    ids_str = request.args.get("ids", "")
    ids = [int(x) for x in ids_str.split(",") if x.strip().isdigit()]
    if not ids:
        return render_template("error.html", code=400, message="Aucune offre sélectionnée")
    conn = _get_db()
    try:
        placeholders = ",".join("?" * len(ids))
        offers = conn.execute(
            f"SELECT * FROM offers WHERE id IN ({placeholders}) ORDER BY score DESC",
            ids,
        ).fetchall()
        return render_template("compare.html", offers=offers)
    finally:
        conn.close()


@pages_bp.route("/stats")
def stats_page():
    conn = _get_db()
    try:
        from emploi.db import application_summary

        stats = application_summary(conn)
        return render_template("stats.html", stats=stats)
    finally:
        conn.close()


@pages_bp.route("/offer/<int:offer_id>")
def offer_detail(offer_id):
    conn = _get_db()
    try:
        offer = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
        if offer is None:
            from flask import abort

            abort(404)
        events = conn.execute(
            "SELECT * FROM offer_events WHERE offer_id = ? ORDER BY created_at DESC",
            (offer_id,),
        ).fetchall()
        notes = []
        try:
            notes = conn.execute(
                "SELECT * FROM offer_notes WHERE offer_id = ? ORDER BY created_at DESC",
                (offer_id,),
            ).fetchall()
        except Exception:
            pass  # table may not exist yet
        return render_template("offer.html", offer=offer, events=events, notes=notes)
    finally:
        conn.close()


@pages_bp.route("/actions")
def actions_page():
    conn = _get_db()
    try:
        from emploi.db import list_next_actions

        actions = list_next_actions(conn)
        today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        overdue = sum(1 for a in actions if a.get("due_date") and str(a["due_date"]) < today)
        due_soon = sum(1 for a in actions if a.get("due_date") and str(a["due_date"]) >= today)
        return render_template(
            "actions.html",
            actions=actions,
            today=today,
            overdue=overdue,
            due_soon=due_soon,
        )
    finally:
        conn.close()


@pages_bp.route("/applications")
def applications_page():
    conn = _get_db()
    try:
        # Fetch applications with offer scores
        rows = conn.execute(
            "SELECT a.*, o.title, o.company, o.score, o.url "
            "FROM applications a JOIN offers o ON o.id = a.offer_id "
            "ORDER BY o.score DESC"
        ).fetchall()
        columns = [
            {"status": "draft", "label": "Brouillon", "icon": "📝", "offers": []},
            {"status": "sent", "label": "Envoyé", "icon": "📤", "offers": []},
            {"status": "followup", "label": "Relance", "icon": "🔄", "offers": []},
            {"status": "interview", "label": "Entretien", "icon": "🎤", "offers": []},
            {"status": "rejected", "label": "Refusé", "icon": "❌", "offers": []},
        ]
        status_map = {c["status"]: c for c in columns}
        for row in rows:
            s = row["status"]
            if s in status_map:
                status_map[s]["offers"].append(row)  # type: ignore[attr-defined]
        return render_template("applications.html", columns=columns)
    finally:
        conn.close()


@pages_bp.route("/map")
def map_page():
    return render_template("map.html")


@pages_bp.route("/company/<name>")
def company_page(name):
    conn = _get_db()
    try:
        offers = conn.execute(
            "SELECT * FROM offers WHERE company = ? AND is_active = 1 ORDER BY score DESC",
            (name,),
        ).fetchall()
        if not offers:
            from flask import abort

            abort(404)
        stats = conn.execute(
            "SELECT COUNT(*) as count, AVG(score) as avg_score, "
            "GROUP_CONCAT(DISTINCT location) as locations "
            "FROM offers WHERE company = ? AND is_active = 1",
            (name,),
        ).fetchone()
        # Check if followed
        followed = False
        try:
            row = conn.execute("SELECT 1 FROM followed_companies WHERE name = ?", (name,)).fetchone()
            followed = row is not None
        except Exception:
            pass
        return render_template("company.html", company=name, offers=offers, stats=stats, followed=followed)
    finally:
        conn.close()
