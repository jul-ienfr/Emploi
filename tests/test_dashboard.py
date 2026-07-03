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


# ── Offer detail ────────────────────────────────────────────────────────────


def test_offer_detail(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Dev Python", company="Acme", location="Paris", description="Poste Python CDI")
    with _get_app().test_client() as client:
        resp = client.get(f"/offer/{offer_id}")
        assert resp.status_code == 200
        assert b"Dev Python" in resp.data
        assert b"Acme" in resp.data
        assert b"Paris" in resp.data


def test_offer_detail_404(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/offer/999")
        assert resp.status_code == 404


def test_offer_update_status(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.post(
            f"/api/offer/{offer_id}/status",
            json={"status": "interesting"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["status"] == "interesting"


def test_offer_add_note(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.post(
            f"/api/offer/{offer_id}/note",
            json={"note": "Bonne offre, à suivre"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        # Verify note was saved
        resp2 = client.get(f"/offer/{offer_id}")
        assert b"Bonne offre" in resp2.data


def test_offer_detail_with_events(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Test", company="Co", location="X")
        from emploi.db import add_offer_event

        add_offer_event(conn, offer_id, event_type="search_seen", message="Found in search")
    with _get_app().test_client() as client:
        resp = client.get(f"/offer/{offer_id}")
        assert resp.status_code == 200
        assert b"search_seen" in resp.data


# ── Stats and charts ────────────────────────────────────────────────────────


def test_stats_page(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/stats")
        assert resp.status_code == 200
        assert b"Statistiques" in resp.data


def test_chart_data(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="A", company="Co", location="X", source="apec", contract_type="CDI")
        add_offer(conn, title="B", company="Co", location="Y", external_source="okjob", contract_type="CDD")
    with _get_app().test_client() as client:
        resp = client.get("/api/chart-data")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "by_source" in data
        assert "by_score" in data
        assert "by_status" in data
        assert "by_contract" in data


# ── Export ──────────────────────────────────────────────────────────────────


def test_export_csv(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="Dev Python", company="Acme", location="Paris")
    with _get_app().test_client() as client:
        resp = client.get("/api/export?format=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type
        assert b"Dev Python" in resp.data


def test_export_json(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.get("/api/export?format=json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) == 1
        assert data[0]["title"] == "Test"


def test_export_markdown(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="Dev Python", company="Acme", location="Paris")
    with _get_app().test_client() as client:
        resp = client.get("/api/export?format=markdown")
        assert resp.status_code == 200
        assert b"Dev Python" in resp.data
        assert b"Acme" in resp.data


# ── Batch operations ────────────────────────────────────────────────────────


def test_batch_status(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        id1 = add_offer(conn, title="A", company="Co", location="X")
        id2 = add_offer(conn, title="B", company="Co", location="Y")
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/offers/batch/status",
            json={"ids": [id1, id2], "status": "interesting"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["updated"] == 2


def test_batch_archive(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        id1 = add_offer(conn, title="A", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/offers/batch/archive",
            json={"ids": [id1]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True


# ── Bookmarks and tags ──────────────────────────────────────────────────────


def test_toggle_bookmark(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        oid = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.post(f"/api/offer/{oid}/bookmark", content_type="application/json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["bookmarked"] is True
        # Toggle again
        resp2 = client.post(f"/api/offer/{oid}/bookmark", content_type="application/json")
        data2 = json.loads(resp2.data)
        assert data2["bookmarked"] is False


def test_list_bookmarks(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        oid = add_offer(conn, title="Bookmarked", company="Co", location="X")
    with _get_app().test_client() as client:
        client.post(f"/api/offer/{oid}/bookmark", content_type="application/json")
        resp = client.get("/api/bookmarks")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) == 1
        assert data[0]["title"] == "Bookmarked"


def test_set_and_get_tags(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        oid = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.post(
            f"/api/offer/{oid}/tags",
            json={"tags": ["python", "urgent"]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp2 = client.get(f"/api/offer/{oid}/tags")
        tags = json.loads(resp2.data)
        assert "python" in tags
        assert "urgent" in tags


def test_all_tags(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        oid = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        client.post(
            f"/api/offer/{oid}/tags",
            json={"tags": ["python"]},
            content_type="application/json",
        )
        resp = client.get("/api/tags")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) >= 1


# ── Compare ─────────────────────────────────────────────────────────────────


def test_compare_page(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        id1 = add_offer(conn, title="A", company="Co1", location="X", description="Desc A")
        id2 = add_offer(conn, title="B", company="Co2", location="Y", description="Desc B")
    with _get_app().test_client() as client:
        resp = client.get(f"/compare?ids={id1},{id2}")
        assert resp.status_code == 200
        assert b"A" in resp.data
        assert b"B" in resp.data


def test_compare_api(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        id1 = add_offer(conn, title="A", company="Co", location="X")
        id2 = add_offer(conn, title="B", company="Co", location="Y")
    with _get_app().test_client() as client:
        resp = client.get(f"/api/compare?ids={id1},{id2}")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) == 2


# ── Profiles, daemon, searches ──────────────────────────────────────────────


def test_profiles_page(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/profiles")
        assert resp.status_code == 200
        assert b"Profils" in resp.data


# ── Auth ────────────────────────────────────────────────────────────────────


def test_auth_no_config_open_access(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/")
        assert resp.status_code == 200


def test_auth_api_key_rejects(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    monkeypatch.setenv("EMPLOI_DASHBOARD_API_KEY", "secret123")
    from emploi.dashboard import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.get("/")
        assert resp.status_code == 401


def test_auth_api_key_accepts(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    monkeypatch.setenv("EMPLOI_DASHBOARD_API_KEY", "secret123")
    from emploi.dashboard import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.get("/?api_key=secret123")
        assert resp.status_code == 200


def test_health_skips_auth(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    monkeypatch.setenv("EMPLOI_DASHBOARD_API_KEY", "secret123")
    from emploi.dashboard import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.get("/health")
        assert resp.status_code == 200


# ── Undo/Redo ───────────────────────────────────────────────────────────────


def test_offer_history_and_undo(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        oid = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        # Change status
        client.post(f"/api/offer/{oid}/status", json={"status": "interesting"}, content_type="application/json")
        # Check history
        resp = client.get(f"/api/offer/{oid}/history")
        assert resp.status_code == 200
        history = json.loads(resp.data)
        assert len(history) >= 1
        # Undo
        resp = client.post(f"/api/offer/{oid}/undo", content_type="application/json")
        assert resp.status_code == 200


# ── Cleanup, RSS, search history ────────────────────────────────────────────


def test_cleanup_stale(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post("/api/offers/cleanup", content_type="application/json")
        assert resp.status_code == 200


def test_rss_feed(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="New Offer", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.get("/rss")
        assert resp.status_code == 200
        assert b"New Offer" in resp.data


def test_search_history(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/search-history",
            json={"query": "python", "results_count": 5},
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp2 = client.get("/api/search-history")
        assert resp2.status_code == 200
        data = json.loads(resp2.data)
        assert len(data) == 1
        assert data[0]["query"] == "python"


# ── Compensation ────────────────────────────────────────────────────────────


def test_compensation(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        oid = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.put(
            f"/api/offer/{oid}/compensation",
            json={"salary_brut": 60000, "bonus": 5000},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        # Get
        resp2 = client.get(f"/api/offer/{oid}/compensation")
        assert resp2.status_code == 200


# ── Cities compare ──────────────────────────────────────────────────────────


def test_cities_compare(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="A", company="Co", location="Lausanne")
        add_offer(conn, title="B", company="Co", location="Genève")
    with _get_app().test_client() as client:
        resp = client.get("/api/cities/compare?cities=Lausanne,Genève")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "Lausanne" in data
        assert "Genève" in data


# ── Share ───────────────────────────────────────────────────────────────────


def test_share_offer(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        oid = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.get(f"/api/offer/{oid}/share")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "token" in data
        assert "url" in data


# ── Duplicates ──────────────────────────────────────────────────────────────


def test_duplicates(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="Dev Python A", company="Acme", location="Paris", source="apec")
        add_offer(conn, title="Dev Python B", company="Acme", location="Paris", source="monster")
    with _get_app().test_client() as client:
        resp = client.get("/api/offers/duplicates")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) >= 1


# ── Credibility ─────────────────────────────────────────────────────────────


def test_credibility(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        oid = add_offer(
            conn,
            title="Test",
            company="Acme",
            location="X",
            description="Offre détaillée avec plus de 100 caractères pour le test",
            salary="50k",
        )
    with _get_app().test_client() as client:
        resp = client.get(f"/api/offer/{oid}/credibility")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "score" in data
        assert data["score"] > 50


# ── Goals ───────────────────────────────────────────────────────────────────


def test_goals(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/goals",
            json={"title": "Postuler à 5 offres", "target_value": 5},
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp2 = client.get("/api/goals")
        assert resp2.status_code == 200
        data = json.loads(resp2.data)
        assert len(data) == 1


# ── Alerts ──────────────────────────────────────────────────────────────────


def test_alerts(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/alerts",
            json={"query": "python", "min_score": 70},
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp2 = client.get("/api/alerts")
        data = json.loads(resp2.data)
        assert len(data) == 1


# ── Webhooks ────────────────────────────────────────────────────────────────


def test_webhooks(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/webhooks",
            json={"url": "https://hooks.test", "events": ["new_offer"]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp2 = client.get("/api/webhooks")
        data = json.loads(resp2.data)
        assert len(data) == 1


# ── Import sources ──────────────────────────────────────────────────────────


def test_import_sources(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/api/import-sources")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) >= 7


# ── Semantic search ─────────────────────────────────────────────────────────


def test_semantic_search(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="Développeur Python", company="Co", location="X", description="Python Django Flask")
    with _get_app().test_client() as client:
        resp = client.get("/api/search/semantic?q=python")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) >= 1


# ── Phase 18: Geo map ────────────────────────────────────────────────────


def test_map_page(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/map")
        assert resp.status_code == 200
        assert b"Carte des offres" in resp.data
        assert b"leaflet" in resp.data


def test_api_map_data(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="Dev Python", company="Acme", location="Paris", description="Poste Python")
        add_offer(conn, title="Dev Java", company="Beta", location="Lyon")
    with _get_app().test_client() as client:
        resp = client.get("/api/map-data")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) == 2
        assert data[0]["location"] in ("Paris", "Lyon")


def test_api_map_data_empty_location_excluded(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="No loc", company="Co", location="")
        add_offer(conn, title="Has loc", company="Co", location="Paris")
    with _get_app().test_client() as client:
        resp = client.get("/api/map-data")
        data = json.loads(resp.data)
        assert len(data) == 1
        assert data[0]["title"] == "Has loc"


# ── Phase 19: Company profiles ────────────────────────────────────────────


def test_api_companies(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="A1", company="Acme", location="Paris")
        add_offer(conn, title="A2", company="Acme", location="Lyon")
        add_offer(conn, title="B1", company="Beta", location="Paris")
    with _get_app().test_client() as client:
        resp = client.get("/api/companies")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) == 2
        acme = [c for c in data if c["company"] == "Acme"][0]
        assert acme["offer_count"] == 2


def test_company_page(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="Dev Python", company="Acme", location="Paris", description="CDI Python")
    with _get_app().test_client() as client:
        resp = client.get("/company/Acme")
        assert resp.status_code == 200
        assert b"Acme" in resp.data
        assert b"Dev Python" in resp.data


def test_company_page_404(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/company/NoSuchCompany")
        assert resp.status_code == 404


# ── Phase 29: Multi-user profiles ─────────────────────────────────────────


def test_user_profiles_crud(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        # Create
        resp = client.post(
            "/api/profiles/users",
            json={"name": "Alice", "skills": ["Python", "SQL"], "preferences": {"remote": True}},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        profile_id = data["id"]

        # List
        resp = client.get("/api/profiles/users")
        assert resp.status_code == 200
        profiles = json.loads(resp.data)
        assert len(profiles) == 1
        assert profiles[0]["name"] == "Alice"
        assert "Python" in profiles[0]["skills"]

        # Get
        resp = client.get(f"/api/profiles/users/{profile_id}")
        assert resp.status_code == 200
        p = json.loads(resp.data)
        assert p["name"] == "Alice"
        assert p["preferences"]["remote"] is True

        # Update
        resp = client.put(
            f"/api/profiles/users/{profile_id}",
            json={"name": "Alice v2", "skills": ["Python", "Go"]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp = client.get(f"/api/profiles/users/{profile_id}")
        p = json.loads(resp.data)
        assert p["name"] == "Alice v2"
        assert "Go" in p["skills"]

        # Delete
        resp = client.delete(f"/api/profiles/users/{profile_id}")
        assert resp.status_code == 200
        resp = client.get("/api/profiles/users")
        assert len(json.loads(resp.data)) == 0


def test_user_profiles_create_name_required(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/profiles/users",
            json={"name": ""},
            content_type="application/json",
        )
        assert resp.status_code == 400


def test_user_profiles_get_not_found(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/api/profiles/users/999")
        assert resp.status_code == 404


# ── Phase 30: Advanced analytics ──────────────────────────────────────────


def test_api_analytics_conversion(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="A", company="Co", location="X")
        add_offer(conn, title="B", company="Co", location="Y")
    with _get_app().test_client() as client:
        resp = client.get("/api/analytics/conversion")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["discovered"] == 2
        assert "bookmarked" in data
        assert "applied" in data
        assert "interview" in data


def test_api_analytics_source_roi(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="A", company="Co", location="X", source="apec")
        add_offer(conn, title="B", company="Co", location="Y", source="apec")
        add_offer(conn, title="C", company="Co", location="Z", external_source="okjob")
    with _get_app().test_client() as client:
        resp = client.get("/api/analytics/source-roi")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) == 2
        assert data[0]["src"] == "apec"
        assert data[0]["total"] == 2
        assert "engagement_rate" in data[0]


# ── Phase 40: Company following ───────────────────────────────────────────


def test_follow_unfollow_company(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        # Follow
        resp = client.post("/api/company/Acme/follow", content_type="application/json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["followed"] is True

        # Follow again (idempotent)
        resp = client.post("/api/company/Acme/follow", content_type="application/json")
        data = json.loads(resp.data)
        assert data["already"] is True

        # List followed
        resp = client.get("/api/companies/followed")
        assert resp.status_code == 200
        followed = json.loads(resp.data)
        assert len(followed) == 1
        assert followed[0]["name"] == "Acme"

        # Unfollow
        resp = client.delete("/api/company/Acme/follow", content_type="application/json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["followed"] is False

        # List empty
        resp = client.get("/api/companies/followed")
        assert len(json.loads(resp.data)) == 0


def test_company_page_shows_follow_status(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="Dev", company="Acme", location="Paris", description="Test")
    with _get_app().test_client() as client:
        # Before follow
        resp = client.get("/company/Acme")
        assert resp.status_code == 200
        assert b"Suivre" in resp.data

        # Follow
        client.post("/api/company/Acme/follow", content_type="application/json")

        # After follow
        resp = client.get("/company/Acme")
        assert resp.status_code == 200
        assert b"Ne plus suivre" in resp.data


# ── Phase 23: Clipboard import ─────────────────────────────────────────────


def test_clipboard_import(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/import/clipboard",
            json={"text": "Dev Python\nAcme Corp\nParis\nPoste Python CDI"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert "offer_id" in data
        assert data["parsed"]["title"] == "Dev Python"
        assert data["parsed"]["company"] == "Acme Corp"
        assert data["parsed"]["location"] == "Paris"
        assert data["parsed"]["description"] == "Poste Python CDI"


def test_clipboard_import_empty(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/import/clipboard",
            json={"text": ""},
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "error" in data


def test_clipboard_import_single_line(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/import/clipboard",
            json={"text": "Dev Python"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["parsed"]["title"] == "Dev Python"
        assert data["parsed"]["company"] == ""
        assert data["parsed"]["location"] == ""


# ── Phase 26: Cover letter generation ───────────────────────────────────────


def test_cover_letter_generation(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Dev Python", company="Acme", location="Paris")
    with _get_app().test_client() as client:
        resp = client.post(
            f"/api/offer/{offer_id}/cover-letter",
            json={"sender_name": "Jean Dupont", "sender_email": "jean@example.com"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert "cover_letter" in data
        assert "Dev Python" in data["cover_letter"]
        assert "Acme" in data["cover_letter"]
        assert "Jean Dupont" in data["cover_letter"]
        assert "jean@example.com" in data["cover_letter"]


def test_cover_letter_default_sender(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Dev Java", company="Beta", location="Lyon")
    with _get_app().test_client() as client:
        resp = client.post(
            f"/api/offer/{offer_id}/cover-letter",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "[Votre nom]" in data["cover_letter"]


def test_cover_letter_not_found(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/offer/999/cover-letter",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 404


# ── Phase 37: Contract analysis ─────────────────────────────────────────────


def test_contract_analyze_trial_period(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Dev", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.post(
            f"/api/offer/{offer_id}/contract/analyze",
            json={"text": "Période d'essai de 3 mois. Salaire 45000 euros brut."},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert "trial_period" in data["clauses"]
        assert "3 mois" in data["clauses"]["trial_period"]
        assert "salary" in data["clauses"]


def test_contract_analyze_non_compete(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Dev", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.post(
            f"/api/offer/{offer_id}/contract/analyze",
            json={"text": "Clause de non-concurrence pendant 12 mois."},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "non_compete" in data["clauses"]


def test_contract_analyze_empty_text(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Dev", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.post(
            f"/api/offer/{offer_id}/contract/analyze",
            json={"text": ""},
            content_type="application/json",
        )
        assert resp.status_code == 400


def test_contract_analyze_not_found(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/offer/999/contract/analyze",
            json={"text": "some text"},
            content_type="application/json",
        )
        assert resp.status_code == 404


def test_contract_analyze_no_clauses(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Dev", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.post(
            f"/api/offer/{offer_id}/contract/analyze",
            json={"text": "Ce contrat est un CDI de droit français."},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["clauses"] == {}


# ── Phase 41: Multi-format import ───────────────────────────────────────────


def test_import_text_structured(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/import/text",
            json={
                "text": "Titre: Dev Python\nEntreprise: Acme\nLieu: Paris\nSalaire: 45000 brut\nContrat: CDI\nDescription du poste"
            },
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["parsed"]["title"] == "Dev Python"
        assert data["parsed"]["company"] == "Acme"
        assert data["parsed"]["location"] == "Paris"
        assert data["parsed"]["salary"] == "45000 brut"
        assert data["parsed"]["contract_type"] == "CDI"


def test_import_text_unstructured(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/import/text",
            json={"text": "Dev Python\nAcme Corp\nParis\nDetails here"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["parsed"]["title"] == "Dev Python"
        assert data["parsed"]["company"] == "Acme Corp"


def test_import_text_empty(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/import/text",
            json={"text": ""},
            content_type="application/json",
        )
        assert resp.status_code == 400


def test_import_url_empty(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/import/url",
            json={"url": ""},
            content_type="application/json",
        )
        assert resp.status_code == 400


def test_import_url_unreachable(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/import/url",
            json={"url": "http://nonexistent.example.invalid/page"},
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "error" in data


# ── Phase 42: Assisted application wizard ───────────────────────────────────


def test_apply_wizard_steps(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Dev Python", company="Acme", location="Paris")
    with _get_app().test_client() as client:
        resp = client.get(f"/api/apply/{offer_id}/steps")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert len(data["steps"]) == 5
        assert data["steps"][0]["step"] == 1
        assert data["steps"][0]["completed"] is False


def test_apply_wizard_complete_step(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Dev Python", company="Acme", location="Paris")
    with _get_app().test_client() as client:
        resp = client.post(
            f"/api/apply/{offer_id}/step/1",
            json={"notes": "Offre analysée"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["step"] == 1
        assert data["completed"] is True
        assert data["notes"] == "Offre analysée"
        # Verify step shows as completed in GET
        resp2 = client.get(f"/api/apply/{offer_id}/steps")
        data2 = json.loads(resp2.data)
        assert data2["steps"][0]["completed"] is True
        assert data2["steps"][0]["notes"] == "Offre analysée"


def test_apply_wizard_invalid_step(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Dev Python", company="Acme", location="Paris")
    with _get_app().test_client() as client:
        resp = client.post(
            f"/api/apply/{offer_id}/step/6",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400


def test_apply_wizard_not_found(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/api/apply/999/steps")
        assert resp.status_code == 404


def test_apply_wizard_step_not_found(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/apply/999/step/1",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 404


def test_apply_wizard_update_step(tmp_path, monkeypatch):
    """Test that completing a step twice updates (upserts) correctly."""
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Dev Python", company="Acme", location="Paris")
    with _get_app().test_client() as client:
        # Complete step 1 first time
        client.post(
            f"/api/apply/{offer_id}/step/1",
            json={"notes": "First pass"},
            content_type="application/json",
        )
        # Complete step 1 again (update)
        resp = client.post(
            f"/api/apply/{offer_id}/step/1",
            json={"notes": "Updated notes", "completed": False},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["notes"] == "Updated notes"
        assert data["completed"] is False
        # Verify via GET
        resp2 = client.get(f"/api/apply/{offer_id}/steps")
        data2 = json.loads(resp2.data)
        step1 = next(s for s in data2["steps"] if s["step"] == 1)
        assert step1["notes"] == "Updated notes"
        assert step1["completed"] is False


# -- Phase 17: Skills matching + salary analysis + user profile -----------


def test_save_profile_skills(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/profile/skills",
            json={"skills": ["python", "flask"], "experience_years": 5, "salary_min": 40000, "salary_max": 60000},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["skills"] == ["python", "flask"]


def test_save_profile_skills_upsert(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        client.post(
            "/api/profile/skills",
            json={"skills": ["python"], "experience_years": 3},
            content_type="application/json",
        )
        resp = client.post(
            "/api/profile/skills",
            json={"skills": ["python", "react"], "experience_years": 5},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["skills"] == ["python", "react"]


def test_skill_match(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(
            conn, title="Dev Python", company="Acme", location="Paris", description="Python Django CDI"
        )
    with _get_app().test_client() as client:
        client.post(
            "/api/profile/skills",
            json={"skills": ["python", "django", "react"], "experience_years": 5},
            content_type="application/json",
        )
        resp = client.get(f"/api/skill-match/{offer_id}")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["offer_id"] == offer_id
        assert "match_score" in data
        assert "python" in data["matched_skills"]
        assert "django" in data["matched_skills"]
        assert "react" in data["missing_skills"]


def test_skill_match_no_profile(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Dev Python", company="Acme", location="Paris")
    with _get_app().test_client() as client:
        resp = client.get(f"/api/skill-match/{offer_id}")
        assert resp.status_code == 400


def test_skill_match_offer_not_found(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/api/skill-match/999")
        assert resp.status_code == 404


def test_salary_analysis_empty(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/api/salary-analysis")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["count"] == 0


def test_salary_analysis_with_data(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="A", company="Co", location="Paris", salary="50000", contract_type="CDI", source="apec")
        add_offer(conn, title="B", company="Co", location="Paris", salary="60000", contract_type="CDI", source="apec")
    with _get_app().test_client() as client:
        resp = client.get("/api/salary-analysis")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["count"] == 2
        assert data["avg"] == 55000.0
        assert data["min"] == 50000
        assert data["max"] == 60000


def test_salary_analysis_filter_source(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="A", company="Co", location="Paris", salary="50000", source="apec")
        add_offer(conn, title="B", company="Co", location="Paris", salary="60000", external_source="okjob")
    with _get_app().test_client() as client:
        resp = client.get("/api/salary-analysis?source=apec")
        data = json.loads(resp.data)
        assert data["count"] == 1


def test_salary_analysis_filter_location(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        add_offer(conn, title="A", company="Co", location="Paris", salary="50000")
        add_offer(conn, title="B", company="Co", location="Lyon", salary="60000")
    with _get_app().test_client() as client:
        resp = client.get("/api/salary-analysis?location=Paris")
        data = json.loads(resp.data)
        assert data["count"] == 1


# -- Phase 36: Interview prep -------------------------------------------


def test_get_interview_prep_empty(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.get(f"/api/offer/{offer_id}/interview")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["notes"] == ""
        assert data["checklist"] == []


def test_save_interview_prep(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.put(
            f"/api/offer/{offer_id}/interview",
            json={"notes": "Preparer presentation", "checklist": [{"text": "Test 1", "done": False}]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        # Verify via GET
        resp2 = client.get(f"/api/offer/{offer_id}/interview")
        data2 = json.loads(resp2.data)
        assert data2["notes"] == "Preparer presentation"
        assert len(data2["checklist"]) == 1


def test_interview_prep_default_checklist(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.put(
            f"/api/offer/{offer_id}/interview",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp2 = client.get(f"/api/offer/{offer_id}/interview")
        data = json.loads(resp2.data)
        assert len(data["checklist"]) == 4
        texts = [c["text"] for c in data["checklist"]]
        assert "Relire l'annonce" in texts
        assert "Preparer questions" in texts
        assert "Verifier transport" in texts
        assert "Imprimer CV" in texts


def test_delete_interview_prep(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        client.put(
            f"/api/offer/{offer_id}/interview",
            json={"notes": "test"},
            content_type="application/json",
        )
        resp = client.delete(f"/api/offer/{offer_id}/interview")
        assert resp.status_code == 200
        # Verify deleted
        resp2 = client.get(f"/api/offer/{offer_id}/interview")
        data = json.loads(resp2.data)
        assert data["notes"] == ""


# -- Phase 38: Follow-up timeline ---------------------------------------


def test_application_timeline_empty(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/api/application/1/timeline")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data == []


def test_add_followup_and_timeline(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Test", company="Co", location="X")
        from emploi.db import add_application

        app_id = add_application(conn, offer_id, status="sent")
    with _get_app().test_client() as client:
        resp = client.post(
            f"/api/application/{app_id}/followup",
            json={"type": "email", "notes": "Relance envoyee"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        # Check timeline
        resp2 = client.get(f"/api/application/{app_id}/timeline")
        data2 = json.loads(resp2.data)
        assert len(data2) == 1
        assert data2[0]["type"] == "email"
        assert data2[0]["notes"] == "Relance envoyee"


def test_followup_default_type(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Test", company="Co", location="X")
        from emploi.db import add_application

        app_id = add_application(conn, offer_id, status="sent")
    with _get_app().test_client() as client:
        resp = client.post(
            f"/api/application/{app_id}/followup",
            json={"notes": "Juste une note"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp2 = client.get(f"/api/application/{app_id}/timeline")
        data = json.loads(resp2.data)
        assert data[0]["type"] == "note"


# -- Phase 39: Response rate analytics -----------------------------------


def test_response_rate_empty(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/api/analytics/response-rate")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["total_applications"] == 0
        assert data["response_rate"] == 0


def test_response_rate_with_data(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Test", company="Co", location="X")
        from emploi.db import add_application

        add_application(conn, offer_id, status="draft")
        add_application(conn, offer_id, status="sent")
        add_application(conn, offer_id, status="interview")
        add_application(conn, offer_id, status="rejected")
    with _get_app().test_client() as client:
        resp = client.get("/api/analytics/response-rate")
        data = json.loads(resp.data)
        assert data["total_applications"] == 4
        assert data["interviews"] == 1
        assert data["rejected"] == 1
        assert data["response_rate"] == 50.0  # 2 out of 4 responded (interview + rejected)


def test_weekly_analytics(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/api/analytics/weekly")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "applications_per_week" in data
        assert "new_offers_per_week" in data
        assert len(data["applications_per_week"]) == 4
        assert len(data["new_offers_per_week"]) == 4


# -- Phase 47: Smart reminders -------------------------------------------


def test_list_reminders_empty(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/api/reminders")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data == []


def test_create_reminder(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/reminders",
            json={"title": "Relance Acme", "remind_at": "2026-07-10T09:00:00"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert "id" in data


def test_create_reminder_validation(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/reminders",
            json={"title": ""},
            content_type="application/json",
        )
        assert resp.status_code == 400


def test_get_reminder(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/reminders",
            json={"title": "Test", "remind_at": "2026-07-10T09:00:00", "type": "interview"},
            content_type="application/json",
        )
        rid = json.loads(resp.data)["id"]
        resp2 = client.get(f"/api/reminders/{rid}")
        assert resp2.status_code == 200
        data = json.loads(resp2.data)
        assert data["title"] == "Test"
        assert data["type"] == "interview"


def test_get_reminder_not_found(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/api/reminders/999")
        assert resp.status_code == 404


def test_update_reminder(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/reminders",
            json={"title": "Old", "remind_at": "2026-07-10T09:00:00"},
            content_type="application/json",
        )
        rid = json.loads(resp.data)["id"]
        resp2 = client.put(
            f"/api/reminders/{rid}",
            json={"title": "New", "completed": 1},
            content_type="application/json",
        )
        assert resp2.status_code == 200
        resp3 = client.get(f"/api/reminders/{rid}")
        data = json.loads(resp3.data)
        assert data["title"] == "New"
        assert data["completed"] == 1


def test_delete_reminder(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/reminders",
            json={"title": "To delete", "remind_at": "2026-07-10T09:00:00"},
            content_type="application/json",
        )
        rid = json.loads(resp.data)["id"]
        resp2 = client.delete(f"/api/reminders/{rid}")
        assert resp2.status_code == 200
        resp3 = client.get(f"/api/reminders/{rid}")
        assert resp3.status_code == 404


def test_delete_reminder_not_found(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.delete("/api/reminders/999")
        assert resp.status_code == 404


def test_reminders_with_offer(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        offer_id = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.post(
            "/api/reminders",
            json={"title": "Relance", "remind_at": "2026-07-10", "offer_id": offer_id},
            content_type="application/json",
        )
        assert resp.status_code == 200
        rid = json.loads(resp.data)["id"]
        resp2 = client.get(f"/api/reminders/{rid}")
        data = json.loads(resp2.data)
        assert data["offer_id"] == offer_id


# ── Contract / benefits / visa / commute / voice / i18n ─────────────────────


def test_contract_analyze(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        oid = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.post(
            f"/api/offer/{oid}/contract/analyze",
            json={"text": "Période d'essai de 3 mois. Salaire 50000 euros brut."},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert "clauses" in json.loads(resp.data)


def test_benefits(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        oid = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.put(
            f"/api/offer/{oid}/benefits", json={"benefits": {"mutuelle": True}}, content_type="application/json"
        )
        assert resp.status_code == 200


def test_visa_info(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        oid = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        resp = client.put(
            f"/api/offer/{oid}/visa", json={"visa_sponsorship": 1, "languages": "FR"}, content_type="application/json"
        )
        assert resp.status_code == 200


def test_commute(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        resp = client.get("/api/commute?from=Bogève&to=Annemasse")
        assert resp.status_code == 200


def test_voice_notes(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with connect(tmp_path / "emploi.sqlite") as conn:
        oid = add_offer(conn, title="Test", company="Co", location="X")
    with _get_app().test_client() as client:
        client.post(f"/api/offer/{oid}/voice-notes", json={"transcript": "Note"}, content_type="application/json")
        resp = client.get(f"/api/offer/{oid}/voice-notes")
        assert resp.status_code == 200
        assert len(json.loads(resp.data)) == 1


def test_i18n(tmp_path, monkeypatch):
    _create_test_db(tmp_path, monkeypatch)
    with _get_app().test_client() as client:
        data_fr = json.loads(client.get("/api/i18n/fr").data)
        assert data_fr["offers"] == "Offres"
        data_en = json.loads(client.get("/api/i18n/en").data)
        assert data_en["offers"] == "Offers"
