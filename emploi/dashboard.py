"""Lightweight web dashboard for Emploi CLI — view offers, stats, and filters.

Usage:
    emploi dashboard              # starts on http://0.0.0.0:8050
    emploi dashboard --port 9000   # custom port
    emploi dashboard --host 127.0.0.1 # localhost only

Requires Flask: pip install flask
"""

from __future__ import annotations

import os
import time

from emploi.dashboard_app.common import _get_db, _start_time, logger


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

    # ── API routes ──────────────────────────────────────────────────────

    # ── Export ──────────────────────────────────────────────────────────

    # ── Undo/Redo and history ───────────────────────────────────────────

    # ── Offer age and stale cleanup ─────────────────────────────────────

    # ── Rémunération totale ─────────────────────────────────────────────

    # ── City comparison ─────────────────────────────────────────────────

    # ── Share offers ────────────────────────────────────────────────────

    # ── Duplicate detection ─────────────────────────────────────────────

    # ── Credibility score ───────────────────────────────────────────────

    # ── Personal goals ──────────────────────────────────────────────────

    # ── Benefits / visa / commute ───────────────────────────────────────

    # ── Semantic search (basic) ─────────────────────────────────────────

    # ── Translation (stub) ─────────────────────────────────────────────

    # ── Alert creation ──────────────────────────────────────────────────

    # ── Outgoing webhooks ───────────────────────────────────────────────

    # ── Voice notes ─────────────────────────────────────────────────────

    # ── i18n ────────────────────────────────────────────────────────────

    # ── Import sources status ───────────────────────────────────────────

    # ── Search history ──────────────────────────────────────────────────

    # ── RSS feed ────────────────────────────────────────────────────────

    # ── Profiles, daemon, searches ──────────────────────────────────────

    # ── Compare offers ──────────────────────────────────────────────────

    # ── Bookmarks and tags ──────────────────────────────────────────────

    # ── Batch operations ────────────────────────────────────────────────

    # ── Stats and charts ────────────────────────────────────────────────

    # ── Offer detail ────────────────────────────────────────────────────

    # ── Prochaines actions ──────────────────────────────────────────────

    # ── Applications Kanban ─────────────────────────────────────────────

    # ── Phase 18: Geo map ───────────────────────────────────────────────

    # ── Phase 19: Company profiles ──────────────────────────────────────

    # ── Phase 29: Multi-user profiles ───────────────────────────────────

    # ── Phase 30: Advanced analytics ────────────────────────────────────

    # ── Phase 40: Company following ─────────────────────────────────────

    # ── Phase 23: Clipboard import ────────────────────────────────────────

    # ── Phase 26: Cover letter generation ─────────────────────────────────

    # ── Phase 37: Contract analysis ───────────────────────────────────────

    # ── Phase 41: Multi-format import ─────────────────────────────────────

    # ── Phase 42: Assisted application wizard ─────────────────────────────

    # -- Phase 17: Skills matching + salary analysis + user profile ----

    # -- Phase 36: Interview prep ----------------------------------------

    # -- Phase 38: Follow-up timeline ------------------------------------

    # -- Phase 39: Response rate analytics -------------------------------

    # -- Phase 47: Smart reminders ---------------------------------------

    # ── Blueprints (pages + api) ──────────────────────────────────────
    from emploi.dashboard_app.api_misc import api_misc_bp
    from emploi.dashboard_app.api_offers import api_offers_bp
    from emploi.dashboard_app.pages import pages_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_offers_bp)
    app.register_blueprint(api_misc_bp)

    return app


def run_dashboard(host: str = "0.0.0.0", port: int = 8050) -> None:
    """Start the dashboard server."""
    app = create_app()
    logger.info("Dashboard starting on http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False)  # type: ignore[attr-defined]
