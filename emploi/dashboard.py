"""Lightweight web dashboard for Emploi CLI — view offers, stats, and filters.

Usage:
    emploi dashboard              # starts on http://0.0.0.0:8050
    emploi dashboard --port 9000   # custom port
    emploi dashboard --host 127.0.0.1 # localhost only

Requires Flask: pip install flask
"""

from __future__ import annotations

import os
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


def create_app() -> object:
    try:
        from flask import Flask, jsonify, render_template, request
    except ImportError:
        raise ImportError("Flask requis pour le dashboard. Installe-le avec: pip install flask")

    _basedir = os.path.dirname(os.path.abspath(__file__))
    app = Flask(
        __name__,
        template_folder=os.path.join(_basedir, "dashboard", "templates"),
        static_folder=os.path.join(_basedir, "dashboard", "static"),
    )

    # ── Security headers ────────────────────────────────────────────────

    @app.after_request
    def _set_security_headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data:;"
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

    return app


def run_dashboard(host: str = "0.0.0.0", port: int = 8050) -> None:
    """Start the dashboard server."""
    app = create_app()
    logger.info("Dashboard starting on http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False)
