"""Tests for the web dashboard."""

from __future__ import annotations

import json

from emploi.db import add_offer, connect, init_db


def _create_test_db(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
    return db_path


def _get_app():
    from emploi.dashboard import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app


# ── Index ───────────────────────────────────────────────────────────────────


def test_dashboard_index_empty(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Aucune offre" in resp.data


def test_dashboard_shows_offers(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="Dev Python", company="Acme", location="Paris", description="Poste Python")
    with _get_app().test_client() as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Dev Python" in resp.data
        assert b"Acme" in resp.data


def test_dashboard_filters(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="Dev Python", company="Acme", location="Paris")
        add_offer(conn, title="Dev Java", company="Beta", location="Lyon")
    with _get_app().test_client() as client:
        resp = client.get("/?q=Python")
        assert resp.status_code == 200
        assert b"Dev Python" in resp.data
        assert b"Dev Java" not in resp.data


def test_dashboard_sort_by_company(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="A", company="Zebra", location="X")
        add_offer(conn, title="B", company="Alpha", location="Y")
    with _get_app().test_client() as client:
        resp = client.get("/?sort=company")
        assert resp.status_code == 200
        data = resp.data.decode()
        assert data.index("Alpha") < data.index("Zebra")


def test_dashboard_score_filter(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="High", company="Co", location="X", description="support python remote CDI")
        add_offer(conn, title="Low", company="Co", location="Y", description="vente")
    with _get_app().test_client() as client:
        resp = client.get("/?min_score=60")
        assert resp.status_code == 200
        assert b"High" in resp.data


# ── Health check ────────────────────────────────────────────────────────────


def test_health_check(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"
        assert data["db"] == "ok"
        assert "version" in data
        assert "uptime_seconds" in data


# ── Error handlers ──────────────────────────────────────────────────────────


def test_404(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/nonexistent")
        assert resp.status_code == 404


def test_404_api(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/api/nonexistent")
        assert resp.status_code == 404
        data = json.loads(resp.data)
        assert "error" in data


# ── Security headers ────────────────────────────────────────────────────────


def test_security_headers(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/")
        assert "Content-Security-Policy" in resp.headers
        assert "X-Content-Type-Options" in resp.headers
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert "X-Frame-Options" in resp.headers


# ── API routes ──────────────────────────────────────────────────────────────


def test_api_stats(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="A", company="Co", location="Paris", source="apec")
        add_offer(conn, title="B", company="Co", location="Lausanne", external_source="okjob")
    with _get_app().test_client() as client:
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["offers"] == 2
        assert "by_source" in data


def test_api_offers(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="Test", company="Co", location="City")
    with _get_app().test_client() as client:
        resp = client.get("/api/offers?limit=5")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) == 1
        assert data[0]["title"] == "Test"


def test_api_applications(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/api/applications")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list)


def test_api_actions(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/api/actions")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list)


def test_api_searches(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/api/searches")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list)


# ── Inactive offers filtered ────────────────────────────────────────────────


def test_inactive_offers_hidden(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="Active", company="Co", location="X")
        offer_id = add_offer(conn, title="Inactive", company="Co", location="Y")
        conn.execute("UPDATE offers SET is_active = 0 WHERE id = ?", (offer_id,))
        conn.commit()
    with _get_app().test_client() as client:
        resp = client.get("/")
        assert b"Active" in resp.data
        assert b"Inactive" not in resp.data


# ── Applications Kanban ─────────────────────────────────────────────────────


def test_applications_page(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/applications")
        assert resp.status_code == 200
        assert b"Candidatures" in resp.data


def test_applications_page_with_apps(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Dev Python", company="Acme", location="Paris")
        from emploi.db import add_application

        add_application(conn, offer_id, status="draft")
        add_application(conn, offer_id, status="sent")
    with _get_app().test_client() as client:
        resp = client.get("/applications")
        assert resp.status_code == 200
        assert b"Dev Python" in resp.data
        assert b"Brouillon" in resp.data
        assert b"Envoy" in resp.data


def test_api_update_application_status(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Dev Python", company="Acme", location="Paris")
        from emploi.db import add_application

        app_id = add_application(conn, offer_id, status="draft")
    with _get_app().test_client() as client:
        resp = client.post(
            f"/api/applications/{app_id}/status",
            json={"status": "sent"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["status"] == "sent"


def test_api_update_application_status_invalid(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/applications/999/status",
            json={"status": "sent"},
            content_type="application/json",
        )
        assert resp.status_code == 400


# ── Actions ─────────────────────────────────────────────────────────────────


def test_actions_page(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/actions")
        assert resp.status_code == 200
        assert b"actions" in resp.data.lower() or b"Aucune" in resp.data
