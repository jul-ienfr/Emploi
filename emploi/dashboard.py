"""Lightweight web dashboard for Emploi CLI — view offers, stats, and filters.

Usage:
    emploi dashboard              # starts on http://0.0.0.0:8050
    emploi dashboard --port 9000   # custom port
    emploi dashboard --host 127.0.0.1 # localhost only

Requires Flask: pip install flask
"""

from __future__ import annotations

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

    return app


def run_dashboard(host: str = "0.0.0.0", port: int = 8050) -> None:
    """Start the dashboard server."""
    app = create_app()
    logger.info("Dashboard starting on http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False)
