"""Lightweight web dashboard for Emploi CLI — view offers, stats, and filters.

Usage:
    emploi dashboard              # starts on http://0.0.0.0:8050
    emploi dashboard --port 9000   # custom port
    emploi dashboard --host 127.0.0.1 # localhost only

Requires Flask: pip install flask
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone

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


def create_app() -> object:
    try:
        from flask import Flask, jsonify, render_template, request
    except ImportError:
        raise ImportError("Flask requis pour le dashboard. Installe-le avec: pip install flask")

    _basedir = os.path.dirname(os.path.abspath(__file__))
    app = Flask(
        __name__,
        template_folder=os.path.join(_basedir, "_dashboard_ui", "templates"),
        static_folder=os.path.join(_basedir, "_dashboard_ui", "static"),
    )

    # ── Auth middleware ──────────────────────────────────────────────────
    from emploi.dashboard_auth import setup_auth

    setup_auth(app)

    # ── Security headers ────────────────────────────────────────────────

    @app.after_request
    def _set_security_headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com;"
            " img-src 'self' data: https://*.tile.openstreetmap.org;"
            " font-src 'self' https://cdn.jsdelivr.net https://unpkg.com;"
            " connect-src 'self';"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    # ── Request timing middleware ────────────────────────────────────────

    @app.before_request
    def _start_timer():
        request._start_time = time.monotonic()  # type: ignore[attr-defined]

    @app.after_request
    def _log_slow(response):
        if hasattr(request, "_start_time"):
            elapsed = (time.monotonic() - request._start_time) * 1000
            if elapsed > 500:
                logger.warning("Slow request: %s %s (%.0fms)", request.method, request.path, elapsed)
            response.headers["X-Response-Time"] = f"{elapsed:.0f}ms"
        return response

    # ── Error handlers ──────────────────────────────────────────────────

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found"}), 404
        return render_template("error.html", code=404, message="Page introuvable"), 404

    @app.errorhandler(500)
    def server_error(e):
        logger.error("Internal error: %s", e)
        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal server error"}), 500
        return render_template("error.html", code=500, message="Erreur interne"), 500

    # ── Health check ────────────────────────────────────────────────────

    @app.route("/health")
    def health():
        try:
            conn = _get_db()
            conn.execute("SELECT 1")
            conn.close()
            db_ok = True
        except Exception:
            db_ok = False
        uptime = time.monotonic() - _start_time
        from emploi import __version__

        return jsonify(
            {
                "status": "ok" if db_ok else "degraded",
                "db": "ok" if db_ok else "error",
                "version": __version__,
                "uptime_seconds": round(uptime, 1),
            }
        )

    # ── Main index ──────────────────────────────────────────────────────

    @app.route("/")
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

    # ── API routes ──────────────────────────────────────────────────────

    @app.route("/api/stats")
    def api_stats():
        conn = _get_db()
        try:
            from emploi.db import application_summary

            stats = application_summary(conn)
            by_source = dict(
                conn.execute(
                    "SELECT COALESCE(NULLIF(external_source,''), source), COUNT(*) "
                    "FROM offers WHERE is_active = 1 GROUP BY 1"
                ).fetchall()
            )
            return jsonify({**stats, "by_source": by_source})
        finally:
            conn.close()

    @app.route("/api/offers")
    def api_offers():
        conn = _get_db()
        try:
            limit = min(int(request.args.get("limit", 50)), 200)
            offers = conn.execute(
                "SELECT * FROM offers WHERE is_active = 1 ORDER BY score DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return jsonify([dict(row) for row in offers])
        finally:
            conn.close()

    @app.route("/api/applications")
    def api_applications():
        conn = _get_db()
        try:
            from emploi.db import list_applications

            apps = list_applications(conn)
            return jsonify([dict(row) for row in apps])
        finally:
            conn.close()

    @app.route("/api/actions")
    def api_actions():
        conn = _get_db()
        try:
            from emploi.db import list_next_actions

            actions = list_next_actions(conn)
            return jsonify(actions)
        finally:
            conn.close()

    @app.route("/api/searches")
    def api_searches():
        conn = _get_db()
        try:
            from emploi.db import list_saved_searches

            searches = list_saved_searches(conn)
            return jsonify([dict(row) for row in searches])
        finally:
            conn.close()

    # ── Export ──────────────────────────────────────────────────────────

    @app.route("/api/export")
    def api_export():
        fmt = request.args.get("format", "csv").strip()
        q = request.args.get("q", "").strip()
        source_filter = request.args.get("source", "").strip()
        status = request.args.get("status", "").strip()

        conn = _get_db()
        try:
            where = ["is_active = 1"]
            params: list = []
            if q:
                where.append("(title LIKE ? OR company LIKE ?)")
                params.extend([f"%{q}%", f"%{q}%"])
            if source_filter:
                where.append("(external_source = ? OR source = ?)")
                params.extend([source_filter, source_filter])
            if status:
                where.append("status = ?")
                params.append(status)
            where_clause = "WHERE " + " AND ".join(where)

            offers = conn.execute(
                f"SELECT title, company, location, url, contract_type, salary, remote, "
                f"source, external_source, score, status, created_at "
                f"FROM offers {where_clause} ORDER BY score DESC",
                params,
            ).fetchall()

            if fmt == "json":
                from flask import Response

                data = [dict(row) for row in offers]
                return Response(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=emploi_offers.json"},
                )
            elif fmt == "markdown":
                lines = ["# Offres Emploi\n"]
                for row in offers:
                    lines.append(f"## {row['title']}")
                    lines.append(f"- **Entreprise** : {row['company']}")
                    lines.append(f"- **Lieu** : {row['location']}")
                    lines.append(f"- **Score** : {row['score']}/100")
                    lines.append(f"- **Source** : {row['external_source'] or row['source']}")
                    if row["url"]:
                        lines.append(f"- **Lien** : {row['url']}")
                    lines.append("")
                from flask import Response

                return Response(
                    "\n".join(lines),
                    mimetype="text/markdown",
                    headers={"Content-Disposition": "attachment; filename=emploi_offers.md"},
                )
            else:  # csv
                import csv
                import io

                buf = io.StringIO()
                writer = csv.DictWriter(
                    buf,
                    fieldnames=[
                        "title",
                        "company",
                        "location",
                        "url",
                        "contract_type",
                        "salary",
                        "remote",
                        "source",
                        "external_source",
                        "score",
                        "status",
                        "created_at",
                    ],
                )
                writer.writeheader()
                for row in offers:
                    writer.writerow(dict(row))
                from flask import Response

                return Response(
                    buf.getvalue(),
                    mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=emploi_offers.csv"},
                )
        finally:
            conn.close()

    # ── Undo/Redo and history ───────────────────────────────────────────

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

    @app.route("/api/offer/<int:offer_id>/history")
    def api_offer_history(offer_id):
        conn = _get_db()
        try:
            _ensure_history_table(conn)
            rows = conn.execute(
                "SELECT * FROM offer_history WHERE offer_id = ? ORDER BY created_at DESC",
                (offer_id,),
            ).fetchall()
            return jsonify([dict(row) for row in rows])
        finally:
            conn.close()

    @app.route("/api/offer/<int:offer_id>/undo", methods=["POST"])
    def api_offer_undo(offer_id):
        conn = _get_db()
        try:
            _ensure_history_table(conn)
            last = conn.execute(
                "SELECT * FROM offer_history WHERE offer_id = ? ORDER BY id DESC LIMIT 1",
                (offer_id,),
            ).fetchone()
            if last is None:
                return jsonify({"error": "Nothing to undo"}), 400
            # Restore old value
            conn.execute(
                f"UPDATE offers SET {last['field']} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (last["old_value"], offer_id),
            )
            # Log the undo
            _log_change(conn, offer_id, last["field"], last["new_value"], last["old_value"])
            conn.commit()
            return jsonify({"ok": True, "undone": dict(last)})
        finally:
            conn.close()

    # ── Offer age and stale cleanup ─────────────────────────────────────

    @app.route("/api/offers/cleanup", methods=["POST"])
    def api_cleanup_stale():
        stale_days = int(os.environ.get("EMPLOI_DASHBOARD_STALE_DAYS", "30"))
        conn = _get_db()
        try:
            conn.execute(
                f"UPDATE offers SET is_active = 0, status = 'archived', "
                f"updated_at = CURRENT_TIMESTAMP "
                f"WHERE is_active = 1 AND created_at < datetime('now', '-{stale_days} days')"
            )
            conn.commit()
            return jsonify({"ok": True, "stale_days": stale_days})
        finally:
            conn.close()

    # ── Rémunération totale ─────────────────────────────────────────────

    @app.route("/api/offer/<int:offer_id>/compensation", methods=["GET", "PUT"])
    def api_compensation(offer_id):
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS offer_compensation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offer_id INTEGER UNIQUE NOT NULL,
                    salary_brut REAL DEFAULT 0,
                    bonus REAL DEFAULT 0,
                    benefits_json TEXT DEFAULT '{}',
                    total_annual REAL DEFAULT 0,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (offer_id) REFERENCES offers(id)
                )"""
            )
            if request.method == "GET":
                row = conn.execute("SELECT * FROM offer_compensation WHERE offer_id = ?", (offer_id,)).fetchone()
                return jsonify(dict(row) if row else {"offer_id": offer_id, "total_annual": 0})
            else:
                data = request.get_json(force=True)
                salary = float(data.get("salary_brut", 0))
                bonus = float(data.get("bonus", 0))
                benefits = float(data.get("benefits", 0))
                total = salary + bonus + benefits
                conn.execute(
                    """INSERT INTO offer_compensation (offer_id, salary_brut, bonus, benefits_json, total_annual)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(offer_id) DO UPDATE SET
                        salary_brut=excluded.salary_brut, bonus=excluded.bonus,
                        benefits_json=excluded.benefits_json, total_annual=excluded.total_annual,
                        updated_at=CURRENT_TIMESTAMP""",
                    (offer_id, salary, bonus, json.dumps(data.get("benefits", {})), total),
                )
                conn.commit()
                return jsonify({"ok": True, "total_annual": total})
        finally:
            conn.close()

    # ── City comparison ─────────────────────────────────────────────────

    @app.route("/api/cities/compare")
    def api_cities_compare():
        cities_str = request.args.get("cities", "")
        cities = [c.strip() for c in cities_str.split(",") if c.strip()]
        if not cities:
            return jsonify({"error": "cities required"}), 400
        conn = _get_db()
        try:
            results = {}
            for city in cities:
                row = conn.execute(
                    "SELECT COUNT(*) as offers, AVG(score) as avg_score FROM offers "
                    "WHERE is_active = 1 AND location LIKE ?",
                    (f"%{city}%",),
                ).fetchone()
                results[city] = {
                    "offers": row["offers"],
                    "avg_score": round(row["avg_score"] or 0, 1),
                }
            return jsonify(results)
        finally:
            conn.close()

    # ── Share offers ────────────────────────────────────────────────────

    @app.route("/api/offer/<int:offer_id>/share")
    def api_share_offer(offer_id):
        import hashlib

        token = hashlib.sha1(f"{offer_id}-share".encode()).hexdigest()[:12]
        url = f"/share/{token}"
        return jsonify({"ok": True, "url": url, "token": token})

    @app.route("/share/<token>")
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

    # ── Duplicate detection ─────────────────────────────────────────────

    @app.route("/api/offers/duplicates")
    def api_duplicates():
        conn = _get_db()
        try:
            # Find offers with similar titles from different sources
            rows = conn.execute(
                """SELECT a.id as id_a, a.title as title_a, a.company as company_a,
                          b.id as id_b, b.title as title_b, b.company as company_b
                FROM offers a JOIN offers b ON a.id < b.id
                WHERE a.is_active = 1 AND b.is_active = 1
                AND a.company = b.company AND a.title != b.title
                AND (a.location = b.location OR a.location = '' OR b.location = '')
                LIMIT 50"""
            ).fetchall()
            return jsonify([dict(r) for r in rows])
        finally:
            conn.close()

    # ── Credibility score ───────────────────────────────────────────────

    @app.route("/api/offer/<int:offer_id>/credibility")
    def api_credibility(offer_id):
        conn = _get_db()
        try:
            offer = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
            if not offer:
                return jsonify({"error": "Not found"}), 404
            score = 50
            reasons = []
            if offer["company"]:
                score += 10
                reasons.append("Entreprise renseignée (+10)")
            if offer["description"] and len(offer["description"]) > 100:
                score += 10
                reasons.append("Description détaillée (+10)")
            if offer["salary"]:
                score += 10
                reasons.append("Salaire indiqué (+10)")
            if offer["url"] and offer["url"].startswith("http"):
                score += 5
                reasons.append("URL valide (+5)")
            if not offer["company"]:
                score -= 15
                reasons.append("Pas d'entreprise (-15)")
            if offer["description"] and len(offer["description"]) < 50:
                score -= 10
                reasons.append("Description trop courte (-10)")
            score = max(0, min(100, score))
            return jsonify({"score": score, "reasons": reasons})
        finally:
            conn.close()

    # ── Personal goals ──────────────────────────────────────────────────

    @app.route("/api/goals", methods=["GET", "POST"])
    def api_goals():
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    target_value INTEGER DEFAULT 1,
                    current_value INTEGER DEFAULT 0,
                    period TEXT DEFAULT 'weekly',
                    completed_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            if request.method == "GET":
                rows = conn.execute("SELECT * FROM goals ORDER BY created_at DESC").fetchall()
                return jsonify([dict(r) for r in rows])
            else:
                data = request.get_json(force=True)
                conn.execute(
                    "INSERT INTO goals (title, target_value, period) VALUES (?, ?, ?)",
                    (data.get("title", ""), data.get("target_value", 1), data.get("period", "weekly")),
                )
                conn.commit()
                return jsonify({"ok": True})
        finally:
            conn.close()

    # ── Semantic search (basic) ─────────────────────────────────────────

    @app.route("/api/search/semantic")
    def api_semantic_search():
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify([])
        # Basic synonym expansion
        synonyms = {
            "dev": ["développeur", "developer", "engineer"],
            "python": ["python", "django", "flask"],
            "support": ["support", "helpdesk", "assistance"],
            "admin": ["administrateur", "sysadmin", "infrastructure"],
        }
        terms = [q]
        for key, syns in synonyms.items():
            if key in q.lower():
                terms.extend(syns)
        conn = _get_db()
        try:
            conditions = []
            params = []
            for term in set(terms):
                conditions.append("(title LIKE ? OR description LIKE ?)")
                params.extend([f"%{term}%", f"%{term}%"])
            where = "WHERE is_active = 1 AND (" + " OR ".join(conditions) + ")"
            rows = conn.execute(f"SELECT * FROM offers {where} ORDER BY score DESC LIMIT 50", params).fetchall()
            return jsonify([dict(r) for r in rows])
        finally:
            conn.close()

    # ── Translation (stub) ─────────────────────────────────────────────

    @app.route("/api/offer/<int:offer_id>/translate", methods=["POST"])
    def api_translate(offer_id):
        data = request.get_json(force=True)
        text = data.get("text", "")
        target_lang = data.get("lang", "fr")
        # Stub: return original text (real translation needs API key)
        return jsonify({"ok": True, "translated": text, "lang": target_lang})

    # ── Alert creation ──────────────────────────────────────────────────

    @app.route("/api/alerts", methods=["GET", "POST", "DELETE"])
    def api_alerts():
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    min_score INTEGER DEFAULT 0,
                    location TEXT DEFAULT '',
                    active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            if request.method == "GET":
                rows = conn.execute("SELECT * FROM alerts ORDER BY created_at DESC").fetchall()
                return jsonify([dict(r) for r in rows])
            elif request.method == "DELETE":
                alert_id = request.args.get("id", "").strip()
                if alert_id:
                    conn.execute("DELETE FROM alerts WHERE id = ?", (int(alert_id),))
                    conn.commit()
                return jsonify({"ok": True})
            else:
                data = request.get_json(force=True)
                conn.execute(
                    "INSERT INTO alerts (query, min_score, location) VALUES (?, ?, ?)",
                    (data.get("query", ""), data.get("min_score", 0), data.get("location", "")),
                )
                conn.commit()
                return jsonify({"ok": True})
        finally:
            conn.close()

    # ── Outgoing webhooks ───────────────────────────────────────────────

    @app.route("/api/webhooks", methods=["GET", "POST"])
    def api_webhooks():
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS webhooks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    events_json TEXT DEFAULT '[]',
                    active INTEGER DEFAULT 1,
                    last_triggered TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            if request.method == "GET":
                rows = conn.execute("SELECT * FROM webhooks ORDER BY created_at DESC").fetchall()
                return jsonify([dict(r) for r in rows])
            else:
                data = request.get_json(force=True)
                conn.execute(
                    "INSERT INTO webhooks (url, events_json) VALUES (?, ?)",
                    (data.get("url", ""), json.dumps(data.get("events", []))),
                )
                conn.commit()
                return jsonify({"ok": True})
        finally:
            conn.close()

    # ── Import sources status ───────────────────────────────────────────

    @app.route("/api/import-sources")
    def api_import_sources():
        from emploi.sources.aggregator import list_sources

        sources = list_sources()
        return jsonify(sources)

    # ── Search history ──────────────────────────────────────────────────

    @app.route("/api/search-history")
    def api_search_history():
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    filters_json TEXT DEFAULT '{}',
                    results_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            rows = conn.execute("SELECT * FROM search_history ORDER BY created_at DESC LIMIT 20").fetchall()
            return jsonify([dict(row) for row in rows])
        finally:
            conn.close()

    @app.route("/api/search-history", methods=["POST"])
    def api_add_search_history():
        from flask import request as req

        data = req.get_json(force=True)
        query = data.get("query", "").strip()
        if not query:
            return jsonify({"error": "query required"}), 400
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    filters_json TEXT DEFAULT '{}',
                    results_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            conn.execute(
                "INSERT INTO search_history (query, filters_json, results_count) VALUES (?, ?, ?)",
                (query, json.dumps(data.get("filters", {})), data.get("results_count", 0)),
            )
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    # ── RSS feed ────────────────────────────────────────────────────────

    @app.route("/rss")
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
                    <title>{o['title']}</title>
                    <link>{url}</link>
                    <description>{(o['description'] or '')[:500]}</description>
                    <pubDate>{o['created_at']}</pubDate>
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

    # ── Profiles, daemon, searches ──────────────────────────────────────

    @app.route("/profiles")
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

    @app.route("/api/profiles/<key>/default", methods=["POST"])
    def api_set_default_profile(key):
        from emploi.config import _accounts_file, _load_json, _write_json

        data = _load_json(_accounts_file()) or {}
        if key not in data.get("profiles", {}):
            return jsonify({"error": "Profile not found"}), 404
        data["default"] = key
        _write_json(_accounts_file(), data)
        return jsonify({"ok": True})

    @app.route("/api/profiles/<key>/status")
    def api_profile_status(key):
        from emploi.browser.client import ManagedBrowserClient

        try:
            client = ManagedBrowserClient()
            result = client.status(profile=key)
            return jsonify({"ok": True, "status": result.payload.get("status", "unknown")})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})

    @app.route("/api/searches/<int:search_id>/toggle", methods=["POST"])
    def api_toggle_search(search_id):
        conn = _get_db()
        try:
            from emploi.db import get_saved_search, set_saved_search_enabled

            saved = get_saved_search(conn, search_id)
            if saved is None:
                return jsonify({"error": "Search not found"}), 404
            new_enabled = not saved["enabled"]
            set_saved_search_enabled(conn, search_id, new_enabled)
            conn.commit()
            return jsonify({"ok": True, "enabled": new_enabled})
        finally:
            conn.close()

    # ── Compare offers ──────────────────────────────────────────────────

    @app.route("/compare")
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

    @app.route("/api/compare")
    def api_compare():
        ids_str = request.args.get("ids", "")
        ids = [int(x) for x in ids_str.split(",") if x.strip().isdigit()]
        if not ids:
            return jsonify({"error": "ids required"}), 400
        conn = _get_db()
        try:
            placeholders = ",".join("?" * len(ids))
            offers = conn.execute(
                f"SELECT * FROM offers WHERE id IN ({placeholders}) ORDER BY score DESC",
                ids,
            ).fetchall()
            return jsonify([dict(row) for row in offers])
        finally:
            conn.close()

    # ── Bookmarks and tags ──────────────────────────────────────────────

    @app.route("/api/offer/<int:offer_id>/bookmark", methods=["POST"])
    def api_toggle_bookmark(offer_id):
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS offer_bookmarks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offer_id INTEGER UNIQUE NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (offer_id) REFERENCES offers(id)
                )"""
            )
            existing = conn.execute("SELECT id FROM offer_bookmarks WHERE offer_id = ?", (offer_id,)).fetchone()
            if existing:
                conn.execute("DELETE FROM offer_bookmarks WHERE offer_id = ?", (offer_id,))
                conn.commit()
                return jsonify({"ok": True, "bookmarked": False})
            else:
                conn.execute("INSERT INTO offer_bookmarks (offer_id) VALUES (?)", (offer_id,))
                conn.commit()
                return jsonify({"ok": True, "bookmarked": True})
        finally:
            conn.close()

    @app.route("/api/bookmarks")
    def api_bookmarks():
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT o.* FROM offers o JOIN offer_bookmarks b ON o.id = b.offer_id " "ORDER BY b.created_at DESC"
            ).fetchall()
            return jsonify([dict(row) for row in rows])
        finally:
            conn.close()

    @app.route("/api/offer/<int:offer_id>/tags", methods=["POST"])
    def api_set_tags(offer_id):
        from flask import request as req

        data = req.get_json(force=True)
        tags = data.get("tags", [])
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS offer_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offer_id INTEGER NOT NULL,
                    tag TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (offer_id) REFERENCES offers(id),
                    UNIQUE(offer_id, tag)
                )"""
            )
            conn.execute("DELETE FROM offer_tags WHERE offer_id = ?", (offer_id,))
            for tag in tags:
                tag = str(tag).strip().lower()
                if tag:
                    conn.execute(
                        "INSERT INTO offer_tags (offer_id, tag) VALUES (?, ?)",
                        (offer_id, tag),
                    )
            conn.commit()
            return jsonify({"ok": True, "tags": tags})
        finally:
            conn.close()

    @app.route("/api/offer/<int:offer_id>/tags")
    def api_get_tags(offer_id):
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT tag FROM offer_tags WHERE offer_id = ? ORDER BY tag",
                (offer_id,),
            ).fetchall()
            return jsonify([row["tag"] for row in rows])
        finally:
            conn.close()

    @app.route("/api/tags")
    def api_all_tags():
        conn = _get_db()
        try:
            rows = conn.execute("SELECT tag, COUNT(*) as cnt FROM offer_tags GROUP BY tag ORDER BY cnt DESC").fetchall()
            return jsonify([{"tag": row["tag"], "count": row["cnt"]} for row in rows])
        finally:
            conn.close()

    # ── Batch operations ────────────────────────────────────────────────

    @app.route("/api/offers/batch/status", methods=["POST"])
    def api_batch_status():
        from flask import request as req

        data = req.get_json(force=True)
        ids = data.get("ids", [])
        status = data.get("status", "").strip()
        if not ids or not status:
            return jsonify({"error": "ids and status required"}), 400
        conn = _get_db()
        try:
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE offers SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                [status] + ids,
            )
            conn.commit()
            return jsonify({"ok": True, "updated": len(ids)})
        finally:
            conn.close()

    @app.route("/api/offers/batch/archive", methods=["POST"])
    def api_batch_archive():
        from flask import request as req

        data = req.get_json(force=True)
        ids = data.get("ids", [])
        if not ids:
            return jsonify({"error": "ids required"}), 400
        conn = _get_db()
        try:
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE offers SET status = 'archived', is_active = 0, "
                f"updated_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                ids,
            )
            conn.commit()
            return jsonify({"ok": True, "archived": len(ids)})
        finally:
            conn.close()

    # ── Stats and charts ────────────────────────────────────────────────

    @app.route("/stats")
    def stats_page():
        conn = _get_db()
        try:
            from emploi.db import application_summary

            stats = application_summary(conn)
            return render_template("stats.html", stats=stats)
        finally:
            conn.close()

    @app.route("/api/chart-data")
    def api_chart_data():
        conn = _get_db()
        try:
            # By source
            by_source = dict(
                conn.execute(
                    "SELECT COALESCE(NULLIF(external_source,''), source), COUNT(*) "
                    "FROM offers WHERE is_active = 1 GROUP BY 1 ORDER BY COUNT(*) DESC"
                ).fetchall()
            )

            # By score ranges
            score_ranges = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
            for row in conn.execute("SELECT score FROM offers WHERE is_active = 1").fetchall():
                s = row[0]
                if s <= 20:
                    score_ranges["0-20"] += 1
                elif s <= 40:
                    score_ranges["21-40"] += 1
                elif s <= 60:
                    score_ranges["41-60"] += 1
                elif s <= 80:
                    score_ranges["61-80"] += 1
                else:
                    score_ranges["81-100"] += 1

            # By status
            by_status = dict(
                conn.execute("SELECT status, COUNT(*) FROM offers WHERE is_active = 1 GROUP BY 1").fetchall()
            )

            # By contract type
            by_contract = dict(
                conn.execute(
                    "SELECT COALESCE(NULLIF(contract_type,''), 'N/A'), COUNT(*) "
                    "FROM offers WHERE is_active = 1 GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 8"
                ).fetchall()
            )

            return jsonify(
                {
                    "by_source": by_source,
                    "by_score": score_ranges,
                    "by_status": by_status,
                    "by_contract": by_contract,
                }
            )
        finally:
            conn.close()

    # ── Offer detail ────────────────────────────────────────────────────

    @app.route("/offer/<int:offer_id>")
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

    @app.route("/api/offer/<int:offer_id>/status", methods=["POST"])
    def api_update_offer_status(offer_id):
        from flask import request as req

        data = req.get_json(force=True)
        new_status = data.get("status", "").strip()
        if not new_status:
            return jsonify({"error": "status required"}), 400
        conn = _get_db()
        try:
            old = conn.execute("SELECT status FROM offers WHERE id = ?", (offer_id,)).fetchone()
            if old:
                _log_change(conn, offer_id, "status", str(old["status"]), new_status)
            conn.execute(
                "UPDATE offers SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_status, offer_id),
            )
            conn.commit()
            return jsonify({"ok": True, "status": new_status})
        finally:
            conn.close()

    @app.route("/api/offer/<int:offer_id>/note", methods=["POST"])
    def api_add_offer_note(offer_id):
        from flask import request as req

        data = req.get_json(force=True)
        note_text = data.get("note", "").strip()
        if not note_text:
            return jsonify({"error": "note required"}), 400
        conn = _get_db()
        try:
            # Ensure table exists
            conn.execute(
                """CREATE TABLE IF NOT EXISTS offer_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offer_id INTEGER NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (offer_id) REFERENCES offers(id)
                )"""
            )
            conn.execute(
                "INSERT INTO offer_notes (offer_id, note) VALUES (?, ?)",
                (offer_id, note_text),
            )
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    # ── Prochaines actions ──────────────────────────────────────────────

    @app.route("/actions")
    def actions_page():
        conn = _get_db()
        try:
            from emploi.db import list_next_actions

            actions = list_next_actions(conn)
            today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
            overdue = sum(1 for a in actions if a.get("due_date") and a["due_date"] < today)
            due_soon = sum(1 for a in actions if a.get("due_date") and a["due_date"] >= today)
            return render_template(
                "actions.html",
                actions=actions,
                today=today,
                overdue=overdue,
                due_soon=due_soon,
            )
        finally:
            conn.close()

    # ── Applications Kanban ─────────────────────────────────────────────

    @app.route("/applications")
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
                    status_map[s]["offers"].append(row)
            return render_template("applications.html", columns=columns)
        finally:
            conn.close()

    @app.route("/api/applications/<int:app_id>/status", methods=["POST"])
    def api_update_application_status(app_id):
        from flask import request as req

        data = req.get_json(force=True)
        new_status = data.get("status", "").strip()
        if not new_status:
            return jsonify({"error": "status required"}), 400
        conn = _get_db()
        try:
            from emploi.db import update_application_status

            try:
                update_application_status(conn, app_id, new_status)
                conn.commit()
                return jsonify({"ok": True, "status": new_status})
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
        finally:
            conn.close()

    # ── Phase 18: Geo map ───────────────────────────────────────────────

    @app.route("/api/map-data")
    def api_map_data():
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT id, title, company, location, score, url, status "
                "FROM offers WHERE is_active = 1 AND location != '' "
                "ORDER BY score DESC"
            ).fetchall()
            return jsonify([dict(row) for row in rows])
        finally:
            conn.close()

    @app.route("/map")
    def map_page():
        return render_template("map.html")

    # ── Phase 19: Company profiles ──────────────────────────────────────

    @app.route("/api/companies")
    def api_companies():
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT company, COUNT(*) as offer_count, "
                "AVG(score) as avg_score, "
                "GROUP_CONCAT(DISTINCT location) as locations "
                "FROM offers WHERE is_active = 1 AND company != '' "
                "GROUP BY company ORDER BY offer_count DESC"
            ).fetchall()
            return jsonify([dict(row) for row in rows])
        finally:
            conn.close()

    @app.route("/company/<name>")
    def company_page(name):
        conn = _get_db()
        try:
            offers = conn.execute(
                "SELECT * FROM offers WHERE company = ? AND is_active = 1 " "ORDER BY score DESC",
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

    # ── Phase 29: Multi-user profiles ───────────────────────────────────

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

    @app.route("/api/profiles/users", methods=["GET"])
    def api_list_user_profiles():
        conn = _get_db()
        try:
            _ensure_user_profiles(conn)
            rows = conn.execute("SELECT * FROM user_profiles ORDER BY name").fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["skills"] = json.loads(d.pop("skills_json", "[]") or "[]")
                d["preferences"] = json.loads(d.pop("preferences_json", "{}") or "{}")
                result.append(d)
            return jsonify(result)
        finally:
            conn.close()

    @app.route("/api/profiles/users", methods=["POST"])
    def api_create_user_profile():
        from flask import request as req

        data = req.get_json(force=True)
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        skills = data.get("skills", [])
        preferences = data.get("preferences", {})
        conn = _get_db()
        try:
            _ensure_user_profiles(conn)
            cur = conn.execute(
                "INSERT INTO user_profiles (name, skills_json, preferences_json) VALUES (?, ?, ?)",
                (name, json.dumps(skills), json.dumps(preferences)),
            )
            conn.commit()
            return jsonify({"ok": True, "id": cur.lastrowid, "name": name})
        finally:
            conn.close()

    @app.route("/api/profiles/users/<int:profile_id>", methods=["GET"])
    def api_get_user_profile(profile_id):
        conn = _get_db()
        try:
            _ensure_user_profiles(conn)
            row = conn.execute("SELECT * FROM user_profiles WHERE id = ?", (profile_id,)).fetchone()
            if row is None:
                return jsonify({"error": "Profile not found"}), 404
            d = dict(row)
            d["skills"] = json.loads(d.pop("skills_json", "[]") or "[]")
            d["preferences"] = json.loads(d.pop("preferences_json", "{}") or "{}")
            return jsonify(d)
        finally:
            conn.close()

    @app.route("/api/profiles/users/<int:profile_id>", methods=["PUT"])
    def api_update_user_profile(profile_id):
        from flask import request as req

        data = req.get_json(force=True)
        conn = _get_db()
        try:
            _ensure_user_profiles(conn)
            row = conn.execute("SELECT * FROM user_profiles WHERE id = ?", (profile_id,)).fetchone()
            if row is None:
                return jsonify({"error": "Profile not found"}), 404
            name = data.get("name", row["name"]).strip()
            skills = data.get("skills", json.loads(row["skills_json"] or "[]"))
            preferences = data.get("preferences", json.loads(row["preferences_json"] or "{}"))
            conn.execute(
                "UPDATE user_profiles SET name = ?, skills_json = ?, preferences_json = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (name, json.dumps(skills), json.dumps(preferences), profile_id),
            )
            conn.commit()
            return jsonify({"ok": True, "id": profile_id, "name": name})
        finally:
            conn.close()

    @app.route("/api/profiles/users/<int:profile_id>", methods=["DELETE"])
    def api_delete_user_profile(profile_id):
        conn = _get_db()
        try:
            _ensure_user_profiles(conn)
            row = conn.execute("SELECT id FROM user_profiles WHERE id = ?", (profile_id,)).fetchone()
            if row is None:
                return jsonify({"error": "Profile not found"}), 404
            conn.execute("DELETE FROM user_profiles WHERE id = ?", (profile_id,))
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    # ── Phase 30: Advanced analytics ────────────────────────────────────

    @app.route("/api/analytics/conversion")
    def api_analytics_conversion():
        conn = _get_db()
        try:
            total = conn.execute("SELECT COUNT(*) FROM offers WHERE is_active = 1").fetchone()[0]
            bookmarked = 0
            try:
                bookmarked = conn.execute("SELECT COUNT(DISTINCT offer_id) FROM offer_bookmarks").fetchone()[0]
            except Exception:
                pass
            # Applications as "applied" stage
            applied = 0
            try:
                applied = conn.execute("SELECT COUNT(DISTINCT offer_id) FROM applications").fetchone()[0]
            except Exception:
                pass
            interview = 0
            try:
                interview = conn.execute(
                    "SELECT COUNT(DISTINCT offer_id) FROM applications WHERE status = 'interview'"
                ).fetchone()[0]
            except Exception:
                pass
            return jsonify(
                {
                    "discovered": total,
                    "bookmarked": bookmarked,
                    "applied": applied,
                    "interview": interview,
                }
            )
        finally:
            conn.close()

    @app.route("/api/analytics/source-roi")
    def api_analytics_source_roi():
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT COALESCE(NULLIF(external_source,''), source) as src, "
                "COUNT(*) as total, AVG(score) as avg_score, "
                "SUM(CASE WHEN status = 'interesting' OR status = 'applied' THEN 1 ELSE 0 END) as engaged "
                "FROM offers WHERE is_active = 1 AND COALESCE(NULLIF(external_source,''), source) != '' "
                "GROUP BY src ORDER BY total DESC"
            ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                total = d["total"]
                d["engagement_rate"] = round(d["engaged"] / total * 100, 1) if total else 0
                result.append(d)
            return jsonify(result)
        finally:
            conn.close()

    # ── Phase 40: Company following ─────────────────────────────────────

    def _ensure_followed_companies(conn):
        conn.execute(
            """CREATE TABLE IF NOT EXISTS followed_companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                followed_at TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )

    @app.route("/api/company/<name>/follow", methods=["POST"])
    def api_follow_company(name):
        conn = _get_db()
        try:
            _ensure_followed_companies(conn)
            existing = conn.execute("SELECT id FROM followed_companies WHERE name = ?", (name,)).fetchone()
            if existing:
                return jsonify({"ok": True, "followed": True, "already": True})
            conn.execute("INSERT INTO followed_companies (name) VALUES (?)", (name,))
            conn.commit()
            return jsonify({"ok": True, "followed": True})
        finally:
            conn.close()

    @app.route("/api/company/<name>/follow", methods=["DELETE"])
    def api_unfollow_company(name):
        conn = _get_db()
        try:
            _ensure_followed_companies(conn)
            conn.execute("DELETE FROM followed_companies WHERE name = ?", (name,))
            conn.commit()
            return jsonify({"ok": True, "followed": False})
        finally:
            conn.close()

    @app.route("/api/companies/followed")
    def api_followed_companies():
        conn = _get_db()
        try:
            _ensure_followed_companies(conn)
            rows = conn.execute("SELECT name, followed_at FROM followed_companies ORDER BY followed_at DESC").fetchall()
            return jsonify([dict(row) for row in rows])
        finally:
            conn.close()

    # ── Phase 23: Clipboard import ────────────────────────────────────────

    @app.route("/api/import/clipboard", methods=["POST"])
    def api_import_clipboard():
        data = request.get_json(force=True)
        text = data.get("text", "").strip()
        if not text:
            return jsonify({"error": "text required"}), 400

        lines = [line.strip() for line in text.splitlines() if line.strip()]

        title = ""
        company = ""
        location = ""
        description = ""

        if lines:
            title = lines[0]
        if len(lines) > 1:
            company = lines[1]
        if len(lines) > 2:
            location = lines[2]
        if len(lines) > 3:
            description = "\n".join(lines[3:])

        conn = _get_db()
        try:
            from emploi.db import add_offer

            offer_id = add_offer(
                conn,
                title=title,
                company=company,
                location=location,
                description=description,
                source="clipboard",
            )
            conn.commit()
            return jsonify(
                {
                    "ok": True,
                    "offer_id": offer_id,
                    "parsed": {
                        "title": title,
                        "company": company,
                        "location": location,
                        "description": description,
                    },
                }
            )
        finally:
            conn.close()

    # ── Phase 26: Cover letter generation ─────────────────────────────────

    @app.route("/api/offer/<int:offer_id>/cover-letter", methods=["POST"])
    def api_cover_letter(offer_id):
        conn = _get_db()
        try:
            offer = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
            if offer is None:
                return jsonify({"error": "Offer not found"}), 404

            data = request.get_json(force=True) if request.data else {}
            sender_name = data.get("sender_name", "[Votre nom]")
            sender_email = data.get("sender_email", "[votre.email@example.com]")

            cover_letter = (
                f"Objet : Candidature au poste de {offer['title']}\n\n"
                f"{sender_name}\n"
                f"{sender_email}\n\n"
                f"{datetime.now().strftime('%d/%m/%Y')}\n\n"
                f"{offer['company']}\n"
                f"{offer['location']}\n\n"
                f"Madame, Monsieur,\n\n"
                f"Je me permets de vous adresser ma candidature pour le poste de "
                f"{offer['title']} au sein de {offer['company']}, situé à {offer['location']}.\n\n"
                f"[Décrivez votre parcours et vos compétences pertinentes ici]\n\n"
                f"[Mettez en avant vos réalisations clés et votre motivation pour ce poste]\n\n"
                f"Je serais ravi(e) de pouvoir échanger avec vous lors d'un entretien afin de "
                f"vous exposer plus en détail mes motivations.\n\n"
                f"Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.\n\n"
                f"{sender_name}"
            )

            return jsonify({"ok": True, "cover_letter": cover_letter})
        finally:
            conn.close()

    # ── Phase 37: Contract analysis ───────────────────────────────────────

    @app.route("/api/offer/<int:offer_id>/contract/analyze", methods=["POST"])
    def api_contract_analyze(offer_id):
        conn = _get_db()
        try:
            offer = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
            if offer is None:
                return jsonify({"error": "Offer not found"}), 404

            data = request.get_json(force=True)
            contract_text = data.get("text", "").strip()
            if not contract_text:
                return jsonify({"error": "text required"}), 400

            import re

            clauses = {}

            # Trial period
            trial_match = re.search(
                r"(?:p[ée]riode|essai)\s+d['’]essai\s*[:\s]*(\d+)\s*(mois|jours?|semaines?)",
                contract_text,
                re.IGNORECASE,
            )
            if trial_match:
                clauses["trial_period"] = f"{trial_match.group(1)} {trial_match.group(2)}"
            else:
                trial_match2 = re.search(
                    r"essai\s*(?:de\s*)?(\d+)\s*(mois|jours?|semaines?)",
                    contract_text,
                    re.IGNORECASE,
                )
                if trial_match2:
                    clauses["trial_period"] = f"{trial_match2.group(1)} {trial_match2.group(2)}"

            # Salary
            salary_match = re.search(
                r"(?:salaire|r[ée]mun[ée]ration)\s*[:\s]*(\d[\d\s,.]*)\s*(?:euros?|€|EUR|brut|net)",
                contract_text,
                re.IGNORECASE,
            )
            if salary_match:
                clauses["salary"] = salary_match.group(0).strip()

            # Non-compete
            noncompete_match = re.search(
                r"(?:clause|engagement)\s+(?:de\s+)?non[\s-]*concurrence\s*(?:pendant\s+)?(\d+)\s*(mois|ann[ée]es?)?",
                contract_text,
                re.IGNORECASE,
            )
            if noncompete_match:
                clauses["non_compete"] = noncompete_match.group(0).strip()

            if "non-concurrence" in contract_text.lower() and "non_compete" not in clauses:
                clauses["non_compete"] = "Clause de non-concurrence présente"

            return jsonify({"ok": True, "clauses": clauses})
        finally:
            conn.close()

    # ── Phase 41: Multi-format import ─────────────────────────────────────

    @app.route("/api/import/url", methods=["POST"])
    def api_import_url():
        data = request.get_json(force=True)
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"error": "url required"}), 400

        import re

        try:
            import urllib.request

            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            return jsonify({"error": f"Failed to fetch URL: {e}"}), 400

        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else url

        lines = [line.strip() for line in text.splitlines() if line.strip()]

        company = ""
        location = ""
        description = ""

        for line in lines[:20]:
            if re.search(r"(?:entreprise|soci[ée]t[ée]|company)", line, re.IGNORECASE):
                company = re.sub(
                    r"(?:entreprise|soci[ée]t[ée]|company)\s*[:\s]*", "", line, flags=re.IGNORECASE
                ).strip()
            if re.search(r"(?:lieu|location|ville|adresse)", line, re.IGNORECASE):
                location = re.sub(r"(?:lieu|location|ville|adresse)\s*[:\s]*", "", line, flags=re.IGNORECASE).strip()

        if not company and len(lines) > 1:
            company = lines[1]
        if not location and len(lines) > 2:
            location = lines[2]
        if len(lines) > 3:
            description = "\n".join(lines[:30])

        conn = _get_db()
        try:
            from emploi.db import add_offer

            offer_id = add_offer(
                conn,
                title=title,
                company=company,
                location=location,
                description=description,
                url=url,
                source="url_import",
            )
            conn.commit()
            return jsonify(
                {
                    "ok": True,
                    "offer_id": offer_id,
                    "parsed": {
                        "title": title,
                        "company": company,
                        "location": location,
                        "description": description[:200] + "..." if len(description) > 200 else description,
                    },
                }
            )
        finally:
            conn.close()

    @app.route("/api/import/text", methods=["POST"])
    def api_import_text():
        data = request.get_json(force=True)
        text = data.get("text", "").strip()
        if not text:
            return jsonify({"error": "text required"}), 400

        import re

        lines = [line.strip() for line in text.splitlines() if line.strip()]

        title = ""
        company = ""
        location = ""
        description = ""
        salary = ""
        contract_type = ""

        for line in lines:
            lower = line.lower()
            if re.match(r"^(?:titre|title|intitul[ée])\s*[:\s]+", lower):
                title = re.sub(r"^(?:titre|title|intitul[ée])\s*[:\s]+", "", line, flags=re.IGNORECASE).strip()
            elif re.match(r"^(?:entreprise|soci[ée]t[ée]|company)\s*[:\s]+", lower):
                company = re.sub(
                    r"^(?:entreprise|soci[ée]t[ée]|company)\s*[:\s]+", "", line, flags=re.IGNORECASE
                ).strip()
            elif re.match(r"^(?:lieu|location|ville|adresse)\s*[:\s]+", lower):
                location = re.sub(r"^(?:lieu|location|ville|adresse)\s*[:\s]+", "", line, flags=re.IGNORECASE).strip()
            elif re.match(r"^(?:salaire|r[ée]mun[ée]ration|salary)\s*[:\s]+", lower):
                salary = re.sub(
                    r"^(?:salaire|r[ée]mun[ée]ration|salary)\s*[:\s]+", "", line, flags=re.IGNORECASE
                ).strip()
            elif re.match(r"^(?:contrat|contract|type)\s*[:\s]+", lower):
                contract_type = re.sub(r"^(?:contrat|contract|type)\s*[:\s]+", "", line, flags=re.IGNORECASE).strip()

        if not title and lines:
            title = lines[0]
        if not company and len(lines) > 1:
            company = lines[1]
        if not location and len(lines) > 2:
            location = lines[2]
        if len(lines) > 3:
            description = "\n".join(lines[3:])

        conn = _get_db()
        try:
            from emploi.db import add_offer

            offer_id = add_offer(
                conn,
                title=title,
                company=company,
                location=location,
                description=description,
                salary=salary,
                contract_type=contract_type,
                source="text_import",
            )
            conn.commit()
            return jsonify(
                {
                    "ok": True,
                    "offer_id": offer_id,
                    "parsed": {
                        "title": title,
                        "company": company,
                        "location": location,
                        "salary": salary,
                        "contract_type": contract_type,
                    },
                }
            )
        finally:
            conn.close()

    # ── Phase 42: Assisted application wizard ─────────────────────────────

    @app.route("/api/apply/<int:offer_id>/steps")
    def api_apply_steps(offer_id):
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS apply_wizard (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offer_id INTEGER NOT NULL,
                    step INTEGER NOT NULL,
                    completed INTEGER DEFAULT 0,
                    notes TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (offer_id) REFERENCES offers(id),
                    UNIQUE(offer_id, step)
                )"""
            )

            offer = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
            if offer is None:
                return jsonify({"error": "Offer not found"}), 404

            steps = [
                {"step": 1, "label": "Analyser l’offre", "description": "Lire et comprendre les exigences du poste"},
                {"step": 2, "label": "Préparer le CV", "description": "Adapter le CV au poste visé"},
                {"step": 3, "label": "Rédiger la lettre", "description": "Rédiger la lettre de motivation"},
                {"step": 4, "label": "Vérifier le dossier", "description": "Relire et corriger les documents"},
                {"step": 5, "label": "Postuler", "description": "Envoyer la candidature"},
            ]

            completed_rows = conn.execute(
                "SELECT step, completed, notes FROM apply_wizard WHERE offer_id = ?",
                (offer_id,),
            ).fetchall()
            completed_map = {
                row["step"]: {"completed": bool(row["completed"]), "notes": row["notes"]} for row in completed_rows
            }

            for step in steps:
                if step["step"] in completed_map:
                    step["completed"] = completed_map[step["step"]]["completed"]
                    step["notes"] = completed_map[step["step"]]["notes"]
                else:
                    step["completed"] = False
                    step["notes"] = ""

            return jsonify({"ok": True, "offer_id": offer_id, "steps": steps})
        finally:
            conn.close()

    @app.route("/api/apply/<int:offer_id>/step/<int:n>", methods=["POST"])
    def api_apply_step_complete(offer_id, n):
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS apply_wizard (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offer_id INTEGER NOT NULL,
                    step INTEGER NOT NULL,
                    completed INTEGER DEFAULT 0,
                    notes TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (offer_id) REFERENCES offers(id),
                    UNIQUE(offer_id, step)
                )"""
            )

            offer = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
            if offer is None:
                return jsonify({"error": "Offer not found"}), 404

            if n < 1 or n > 5:
                return jsonify({"error": "Step must be between 1 and 5"}), 400

            data = request.get_json(force=True) if request.data else {}
            notes = data.get("notes", "")
            completed = data.get("completed", True)

            conn.execute(
                "INSERT INTO apply_wizard (offer_id, step, completed, notes) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(offer_id, step) DO UPDATE SET completed = ?, notes = ?",
                (offer_id, n, int(completed), notes, int(completed), notes),
            )
            conn.commit()
            return jsonify(
                {
                    "ok": True,
                    "offer_id": offer_id,
                    "step": n,
                    "completed": completed,
                    "notes": notes,
                }
            )
        finally:
            conn.close()

    # -- Phase 17: Skills matching + salary analysis + user profile ----

    @app.route("/api/profile/skills", methods=["POST"])
    def api_save_profile_skills():
        from flask import request as req

        data = req.get_json(force=True)
        skills = data.get("skills", [])
        experience_years = data.get("experience_years", 0)
        salary_min = data.get("salary_min")
        salary_max = data.get("salary_max")
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS user_profile (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skills_json TEXT DEFAULT '[]',
                    experience_years INTEGER DEFAULT 0,
                    salary_min INTEGER,
                    salary_max INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            existing = conn.execute("SELECT id FROM user_profile LIMIT 1").fetchone()
            if existing:
                conn.execute(
                    "UPDATE user_profile SET skills_json = ?, experience_years = ?, "
                    "salary_min = ?, salary_max = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps(skills), experience_years, salary_min, salary_max, existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO user_profile (skills_json, experience_years, salary_min, salary_max) "
                    "VALUES (?, ?, ?, ?)",
                    (json.dumps(skills), experience_years, salary_min, salary_max),
                )
            conn.commit()
            return jsonify({"ok": True, "skills": skills})
        finally:
            conn.close()

    @app.route("/api/skill-match/<int:offer_id>")
    def api_skill_match(offer_id):
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS user_profile (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skills_json TEXT DEFAULT '[]',
                    experience_years INTEGER DEFAULT 0,
                    salary_min INTEGER,
                    salary_max INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            offer = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
            if offer is None:
                return jsonify({"error": "Offer not found"}), 404
            profile = conn.execute("SELECT * FROM user_profile LIMIT 1").fetchone()
            if profile is None:
                return jsonify({"error": "No user profile set. POST /api/profile/skills first."}), 400
            user_skills = json.loads(profile["skills_json"] or "[]")
            description = (offer["description"] or "").lower()
            title = (offer["title"] or "").lower()
            offer_text = f"{title} {description}"
            matched = [s for s in user_skills if s.lower() in offer_text]
            missing = [s for s in user_skills if s.lower() not in offer_text]
            all_skills_lower = {s.lower() for s in user_skills}
            if all_skills_lower:
                match_score = round(len(matched) / len(all_skills_lower) * 100)
            else:
                match_score = 0
            return jsonify(
                {
                    "offer_id": offer_id,
                    "match_score": match_score,
                    "matched_skills": matched,
                    "missing_skills": missing,
                    "experience_years": profile["experience_years"],
                }
            )
        finally:
            conn.close()

    @app.route("/api/salary-analysis")
    def api_salary_analysis():
        conn = _get_db()
        try:
            source = request.args.get("source", "").strip()
            location = request.args.get("location", "").strip()
            contract = request.args.get("contract", "").strip()

            where = ["is_active = 1", "salary IS NOT NULL", "salary != ''"]
            params: list = []
            if source:
                where.append("(external_source = ? OR source = ?)")
                params.extend([source, source])
            if location:
                where.append("location LIKE ?")
                params.append(f"%{location}%")
            if contract:
                where.append("contract_type = ?")
                params.append(contract)
            where_clause = "WHERE " + " AND ".join(where)

            rows = conn.execute(
                f"SELECT salary, location, contract_type, source, external_source " f"FROM offers {where_clause}",
                params,
            ).fetchall()

            salaries = []
            for row in rows:
                try:
                    s = row["salary"]
                    if s and str(s).replace(".", "").replace("-", "").strip().isdigit():
                        salaries.append(
                            {
                                "salary": float(s),
                                "location": row["location"],
                                "contract_type": row["contract_type"],
                                "source": row["external_source"] or row["source"],
                            }
                        )
                except (ValueError, TypeError):
                    pass

            if not salaries:
                return jsonify({"count": 0, "avg": 0, "min": 0, "max": 0, "salaries": []})

            values = [s["salary"] for s in salaries]
            return jsonify(
                {
                    "count": len(values),
                    "avg": round(sum(values) / len(values), 2),
                    "min": min(values),
                    "max": max(values),
                    "salaries": salaries,
                }
            )
        finally:
            conn.close()

    # -- Phase 36: Interview prep ----------------------------------------

    @app.route("/api/offer/<int:offer_id>/interview", methods=["GET"])
    def api_get_interview_prep(offer_id):
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS interview_prep (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offer_id INTEGER UNIQUE NOT NULL,
                    notes TEXT DEFAULT '',
                    checklist_json TEXT DEFAULT '[]',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (offer_id) REFERENCES offers(id)
                )"""
            )
            row = conn.execute("SELECT * FROM interview_prep WHERE offer_id = ?", (offer_id,)).fetchone()
            if row is None:
                return jsonify({"offer_id": offer_id, "notes": "", "checklist": []})
            return jsonify(
                {
                    "offer_id": offer_id,
                    "notes": row["notes"],
                    "checklist": json.loads(row["checklist_json"] or "[]"),
                }
            )
        finally:
            conn.close()

    @app.route("/api/offer/<int:offer_id>/interview", methods=["PUT"])
    def api_save_interview_prep(offer_id):
        from flask import request as req

        data = req.get_json(force=True)
        notes = data.get("notes", "")
        checklist = data.get(
            "checklist",
            [
                {"text": "Relire l'annonce", "done": False},
                {"text": "Preparer questions", "done": False},
                {"text": "Verifier transport", "done": False},
                {"text": "Imprimer CV", "done": False},
            ],
        )
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS interview_prep (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offer_id INTEGER UNIQUE NOT NULL,
                    notes TEXT DEFAULT '',
                    checklist_json TEXT DEFAULT '[]',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (offer_id) REFERENCES offers(id)
                )"""
            )
            existing = conn.execute("SELECT id FROM interview_prep WHERE offer_id = ?", (offer_id,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE interview_prep SET notes = ?, checklist_json = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE offer_id = ?",
                    (notes, json.dumps(checklist), offer_id),
                )
            else:
                conn.execute(
                    "INSERT INTO interview_prep (offer_id, notes, checklist_json) VALUES (?, ?, ?)",
                    (offer_id, notes, json.dumps(checklist)),
                )
            conn.commit()
            return jsonify({"ok": True, "offer_id": offer_id})
        finally:
            conn.close()

    @app.route("/api/offer/<int:offer_id>/interview", methods=["DELETE"])
    def api_delete_interview_prep(offer_id):
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS interview_prep (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offer_id INTEGER UNIQUE NOT NULL,
                    notes TEXT DEFAULT '',
                    checklist_json TEXT DEFAULT '[]',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (offer_id) REFERENCES offers(id)
                )"""
            )
            conn.execute("DELETE FROM interview_prep WHERE offer_id = ?", (offer_id,))
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    # -- Phase 38: Follow-up timeline ------------------------------------

    @app.route("/api/application/<int:app_id>/timeline")
    def api_application_timeline(app_id):
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS followups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    application_id INTEGER NOT NULL,
                    type TEXT NOT NULL DEFAULT 'note',
                    notes TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (application_id) REFERENCES applications(id)
                )"""
            )
            rows = conn.execute(
                "SELECT * FROM followups WHERE application_id = ? ORDER BY created_at DESC",
                (app_id,),
            ).fetchall()
            return jsonify([dict(row) for row in rows])
        finally:
            conn.close()

    @app.route("/api/application/<int:app_id>/followup", methods=["POST"])
    def api_add_followup(app_id):
        from flask import request as req

        data = req.get_json(force=True)
        followup_type = data.get("type", "note").strip()
        notes = data.get("notes", "").strip()
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS followups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    application_id INTEGER NOT NULL,
                    type TEXT NOT NULL DEFAULT 'note',
                    notes TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (application_id) REFERENCES applications(id)
                )"""
            )
            conn.execute(
                "INSERT INTO followups (application_id, type, notes) VALUES (?, ?, ?)",
                (app_id, followup_type, notes),
            )
            conn.commit()
            return jsonify({"ok": True, "application_id": app_id})
        finally:
            conn.close()

    # -- Phase 39: Response rate analytics -------------------------------

    @app.route("/api/analytics/response-rate")
    def api_response_rate():
        conn = _get_db()
        try:
            total = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            responded = conn.execute(
                "SELECT COUNT(*) FROM applications WHERE status NOT IN ('draft', 'sent')"
            ).fetchone()[0]
            interviews = conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'interview'").fetchone()[0]
            rejected = conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'rejected'").fetchone()[0]
            response_rate = round(responded / total * 100, 1) if total > 0 else 0
            interview_rate = round(interviews / total * 100, 1) if total > 0 else 0
            return jsonify(
                {
                    "total_applications": total,
                    "responded": responded,
                    "interviews": interviews,
                    "rejected": rejected,
                    "response_rate": response_rate,
                    "interview_rate": interview_rate,
                }
            )
        finally:
            conn.close()

    @app.route("/api/analytics/weekly")
    def api_weekly_analytics():
        conn = _get_db()
        try:
            weeks = []
            for i in range(4):
                weeks.append(
                    conn.execute(
                        "SELECT COUNT(*) FROM applications "
                        "WHERE applied_at >= date('now', ?) AND applied_at < date('now', ?)",
                        (f"-{(i + 1) * 7} days", f"-{i * 7} days"),
                    ).fetchone()[0]
                )
            new_offers = []
            for i in range(4):
                new_offers.append(
                    conn.execute(
                        "SELECT COUNT(*) FROM offers "
                        "WHERE created_at >= date('now', ?) AND created_at < date('now', ?)",
                        (f"-{(i + 1) * 7} days", f"-{i * 7} days"),
                    ).fetchone()[0]
                )
            return jsonify(
                {
                    "applications_per_week": list(reversed(weeks)),
                    "new_offers_per_week": list(reversed(new_offers)),
                }
            )
        finally:
            conn.close()

    # -- Phase 47: Smart reminders ---------------------------------------

    @app.route("/api/reminders", methods=["GET"])
    def api_list_reminders():
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offer_id INTEGER,
                    title TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    type TEXT DEFAULT 'general',
                    completed INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (offer_id) REFERENCES offers(id)
                )"""
            )
            rows = conn.execute("SELECT * FROM reminders ORDER BY remind_at ASC").fetchall()
            return jsonify([dict(row) for row in rows])
        finally:
            conn.close()

    @app.route("/api/reminders", methods=["POST"])
    def api_create_reminder():
        from flask import request as req

        data = req.get_json(force=True)
        title = data.get("title", "").strip()
        remind_at = data.get("remind_at", "").strip()
        if not title or not remind_at:
            return jsonify({"error": "title and remind_at required"}), 400
        offer_id = data.get("offer_id")
        reminder_type = data.get("type", "general")
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offer_id INTEGER,
                    title TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    type TEXT DEFAULT 'general',
                    completed INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (offer_id) REFERENCES offers(id)
                )"""
            )
            cursor = conn.execute(
                "INSERT INTO reminders (offer_id, title, remind_at, type) VALUES (?, ?, ?, ?)",
                (offer_id, title, remind_at, reminder_type),
            )
            conn.commit()
            return jsonify({"ok": True, "id": cursor.lastrowid})
        finally:
            conn.close()

    @app.route("/api/reminders/<int:reminder_id>", methods=["GET"])
    def api_get_reminder(reminder_id):
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offer_id INTEGER,
                    title TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    type TEXT DEFAULT 'general',
                    completed INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (offer_id) REFERENCES offers(id)
                )"""
            )
            row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
            if row is None:
                return jsonify({"error": "Reminder not found"}), 404
            return jsonify(dict(row))
        finally:
            conn.close()

    @app.route("/api/reminders/<int:reminder_id>", methods=["PUT"])
    def api_update_reminder(reminder_id):
        from flask import request as req

        data = req.get_json(force=True)
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offer_id INTEGER,
                    title TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    type TEXT DEFAULT 'general',
                    completed INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (offer_id) REFERENCES offers(id)
                )"""
            )
            row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
            if row is None:
                return jsonify({"error": "Reminder not found"}), 404
            title = data.get("title", row["title"])
            remind_at = data.get("remind_at", row["remind_at"])
            completed = data.get("completed", row["completed"])
            reminder_type = data.get("type", row["type"])
            conn.execute(
                "UPDATE reminders SET title = ?, remind_at = ?, completed = ?, type = ? WHERE id = ?",
                (title, remind_at, completed, reminder_type, reminder_id),
            )
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/reminders/<int:reminder_id>", methods=["DELETE"])
    def api_delete_reminder(reminder_id):
        conn = _get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    offer_id INTEGER,
                    title TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    type TEXT DEFAULT 'general',
                    completed INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (offer_id) REFERENCES offers(id)
                )"""
            )
            row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
            if row is None:
                return jsonify({"error": "Reminder not found"}), 404
            conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    return app


def run_dashboard(host: str = "0.0.0.0", port: int = 8050) -> None:
    """Start the dashboard server."""
    app = create_app()
    logger.info("Dashboard starting on http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False)
