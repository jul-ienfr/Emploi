"""Lightweight web dashboard for Emploi CLI — view offers, stats, and filters.

Usage:
    emploi dashboard              # starts on http://localhost:8050
    emploi dashboard --port 9000   # custom port
    emploi dashboard --host 0.0.0.0 # accessible from network

Requires Flask: pip install flask
"""

from __future__ import annotations

import sqlite3

from emploi.logging import get_logger

logger = get_logger("dashboard")


def _get_db() -> sqlite3.Connection:
    from emploi.db import connect

    return connect()


def create_app() -> object:
    try:
        from flask import Flask, jsonify, render_template_string, request
    except ImportError:
        raise ImportError("Flask requis pour le dashboard. Installe-le avec: pip install flask")

    app = Flask(__name__)

    INDEX_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Emploi Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; color: #333; }
        .header { background: #1a1a2e; color: white; padding: 1.5rem 2rem; }
        .header h1 { font-size: 1.5rem; }
        .header .stats { display: flex; gap: 2rem; margin-top: 0.5rem; font-size: 0.9rem; opacity: 0.8; }
        .container { max-width: 1200px; margin: 0 auto; padding: 1.5rem; }
        .filters { display: flex; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
        .filters input, .filters select { padding: 0.5rem 1rem; border: 1px solid #ddd; border-radius: 6px; font-size: 0.9rem; }
        .filters input { flex: 1; min-width: 200px; }
        .filters button { padding: 0.5rem 1.5rem; background: #1a1a2e; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 0.9rem; }
        .filters button:hover { background: #16213e; }
        .offer-grid { display: grid; gap: 1rem; }
        .offer-card { background: white; border-radius: 8px; padding: 1.2rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 4px solid #1a1a2e; }
        .offer-card[data-score="high"] { border-left-color: #27ae60; }
        .offer-card[data-score="mid"] { border-left-color: #f39c12; }
        .offer-card[data-score="low"] { border-left-color: #e74c3c; }
        .offer-title { font-size: 1.1rem; font-weight: 600; margin-bottom: 0.3rem; }
        .offer-title a { color: #1a1a2e; text-decoration: none; }
        .offer-title a:hover { text-decoration: underline; }
        .offer-meta { font-size: 0.85rem; color: #666; display: flex; gap: 1rem; flex-wrap: wrap; }
        .offer-meta span { display: inline-flex; align-items: center; gap: 0.3rem; }
        .offer-desc { font-size: 0.85rem; color: #555; margin-top: 0.5rem; line-height: 1.4; }
        .source-badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 10px; font-size: 0.75rem; font-weight: 600; }
        .source-fr { background: #dbeafe; color: #1e40af; }
        .source-ch { background: #fef3c7; color: #92400e; }
        .score-badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 10px; font-size: 0.75rem; font-weight: 600; }
        .score-high { background: #d1fae5; color: #065f46; }
        .score-mid { background: #fef3c7; color: #92400e; }
        .score-low { background: #fee2e2; color: #991b1b; }
        .pagination { display: flex; justify-content: center; gap: 0.5rem; margin-top: 1.5rem; }
        .pagination a, .pagination span { padding: 0.4rem 0.8rem; border-radius: 4px; text-decoration: none; font-size: 0.85rem; }
        .pagination a { background: white; color: #333; border: 1px solid #ddd; }
        .pagination a:hover { background: #f0f0f0; }
        .pagination .active { background: #1a1a2e; color: white; border-color: #1a1a2e; }
        .empty { text-align: center; padding: 3rem; color: #999; }
    </style>
</head>
<body>
    <div class="header">
        <h1>📋 Emploi Dashboard</h1>
        <div class="stats">
            <span id="stat-total">Chargement...</span>
        </div>
    </div>
    <div class="container">
        <form class="filters" method="GET">
            <input type="text" name="q" placeholder="Rechercher..." value="{{ q }}">
            <select name="source">
                <option value="">Toutes les sources</option>
                {% for s in sources %}
                <option value="{{ s }}" {{ 'selected' if s == selected_source }}>{{ s }}</option>
                {% endfor %}
            </select>
            <select name="status">
                <option value="">Tous les statuts</option>
                <option value="new" {{ 'selected' if status == 'new' }}>Nouveau</option>
                <option value="interesting" {{ 'selected' if status == 'interesting' }}>Intéressant</option>
                <option value="applied" {{ 'selected' if status == 'applied' }}>Postulé</option>
            </select>
            <button type="submit">Filtrer</button>
        </form>

        <div class="offer-grid">
            {% if offers %}
            {% for offer in offers %}
            <div class="offer-card" data-score="{{ 'high' if offer.score >= 70 else ('mid' if offer.score >= 50 else 'low') }}">
                <div class="offer-title">
                    {% if offer.url %}<a href="{{ offer.url }}" target="_blank">{{ offer.title }}</a>
                    {% else %}{{ offer.title }}{% endif %}
                </div>
                <div class="offer-meta">
                    {% if offer.company %}<span>🏢 {{ offer.company }}</span>{% endif %}
                    {% if offer.location %}<span>📍 {{ offer.location }}</span>{% endif %}
                    {% if offer.contract_type %}<span>📄 {{ offer.contract_type }}</span>{% endif %}
                    {% if offer.salary %}<span>💰 {{ offer.salary }}</span>{% endif %}
                    <span class="source-badge {{ 'source-ch' if offer.external_source in ['okjob','jobup','jobs.ch','comparis'] else 'source-fr' }}">{{ offer.external_source or offer.source }}</span>
                    <span class="score-badge {{ 'score-high' if offer.score >= 70 else ('score-mid' if offer.score >= 50 else 'score-low') }}">{{ offer.score }}/100</span>
                </div>
                {% if offer.description %}
                <div class="offer-desc">{{ offer.description[:200] }}{% if offer.description|length > 200 %}...{% endif %}</div>
                {% endif %}
            </div>
            {% endfor %}
            </div>

            {% if total_pages > 1 %}
            <div class="pagination">
                {% if page > 1 %}<a href="?{{ params }}&page={{ page-1 }}">← Précédent</a>{% endif %}
                {% for p in range(1, total_pages+1) %}
                {% if p == page %}<span class="active">{{ p }}</span>
                {% else %}<a href="?{{ params }}&page={{ p }}">{{ p }}</a>{% endif %}
                {% endfor %}
                {% if page < total_pages %}<a href="?{{ params }}&page={{ page+1 }}">Suivant →</a>{% endif %}
            </div>
            {% endif %}

            {% else %}
            <div class="empty">
                <p>Aucune offre trouvée.</p>
                <p style="margin-top:0.5rem;font-size:0.85rem">Lance <code>emploi search-all "python"</code> pour remplir la base.</p>
            </div>
            {% endif %}
        </div>
    </div>
    <script>
        fetch('/api/stats').then(r=>r.json()).then(d=>{
            document.getElementById('stat-total').textContent = d.total + ' offres';
        });
    </script>
</body>
</html>
"""

    @app.route("/")
    def index():
        q = request.args.get("q", "").strip()
        source_filter = request.args.get("source", "").strip()
        status = request.args.get("status", "").strip()
        page = max(1, int(request.args.get("page", 1)))
        per_page = 30

        conn = _get_db()
        try:
            # Build query
            where = []
            params: list = []
            if q:
                where.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
                params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
            if source_filter:
                where.append("(external_source = ? OR source = ?)")
                params.extend([f"%{source_filter}%", f"%{source_filter}%"])
            if status:
                where.append("status = ?")
                params.append(status)

            where_clause = "WHERE " + " AND ".join(where) if where else ""

            # Count
            count_row = conn.execute(f"SELECT COUNT(*) FROM offers {where_clause}", params).fetchone()
            total = count_row[0]
            total_pages = max(1, (total + per_page - 1) // per_page)

            # Fetch page
            offset = (page - 1) * per_page
            offers = conn.execute(
                f"SELECT * FROM offers {where_clause} ORDER BY score DESC, id DESC LIMIT ? OFFSET ?",
                params + [per_page, offset],
            ).fetchall()

            # Get distinct sources for filter
            sources = [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT COALESCE(NULLIF(external_source,''), source) FROM offers WHERE COALESCE(NULLIF(external_source,''), source) != '' ORDER BY 1"
                ).fetchall()
            ]

            # Build params string for pagination links
            param_parts = []
            if q:
                param_parts.append(f"q={q}")
            if source_filter:
                param_parts.append(f"source={source_filter}")
            if status:
                param_parts.append(f"status={status}")
            params_str = "&".join(param_parts)

            return render_template_string(
                INDEX_HTML,
                offers=offers,
                sources=sources,
                q=q,
                selected_source=source_filter,
                status=status,
                page=page,
                total_pages=total_pages,
                params=params_str,
            )
        finally:
            conn.close()

    @app.route("/api/stats")
    def api_stats():
        conn = _get_db()
        try:
            total = conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
            by_source = dict(
                conn.execute(
                    "SELECT COALESCE(NULLIF(external_source,''), source), COUNT(*) FROM offers GROUP BY 1"
                ).fetchall()
            )
            return jsonify({"total": total, "by_source": by_source})
        finally:
            conn.close()

    @app.route("/api/offers")
    def api_offers():
        conn = _get_db()
        try:
            limit = min(int(request.args.get("limit", 50)), 200)
            offers = conn.execute("SELECT * FROM offers ORDER BY score DESC, id DESC LIMIT ?", (limit,)).fetchall()
            return jsonify([dict(row) for row in offers])
        finally:
            conn.close()

    return app


def run_dashboard(host: str = "127.0.0.1", port: int = 8050) -> None:
    """Start the dashboard server."""
    app = create_app()
    logger.info("Dashboard starting on http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False)
