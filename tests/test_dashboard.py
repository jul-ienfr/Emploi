"""Tests for the web dashboard."""

from __future__ import annotations

import json

from emploi.db import add_offer, connect, init_db


def test_dashboard_index_empty(tmp_path, monkeypatch):
    """Dashboard should render with no offers."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)

    from emploi.dashboard import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Aucune offre" in resp.data


def test_dashboard_shows_offers(tmp_path, monkeypatch):
    """Dashboard should display offers from the DB."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)
        add_offer(conn, title="Dev Python", company="Acme", location="Paris", description="Poste Python")

    from emploi.dashboard import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Dev Python" in resp.data
        assert b"Acme" in resp.data


def test_dashboard_filters(tmp_path, monkeypatch):
    """Dashboard should filter by search query."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)
        add_offer(conn, title="Dev Python", company="Acme", location="Paris")
        add_offer(conn, title="Dev Java", company="Beta", location="Lyon")

    from emploi.dashboard import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.get("/?q=Python")
        assert resp.status_code == 200
        assert b"Dev Python" in resp.data
        assert b"Dev Java" not in resp.data


def test_dashboard_api_stats(tmp_path, monkeypatch):
    """API stats should return correct counts."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)
        add_offer(conn, title="A", company="Co", location="Paris", source="apec")
        add_offer(conn, title="B", company="Co", location="Lausanne", external_source="okjob")

    from emploi.dashboard import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["total"] == 2
        assert "apec" in data["by_source"] or "okjob" in data["by_source"]


def test_dashboard_api_offers(tmp_path, monkeypatch):
    """API offers should return JSON list."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)
        add_offer(conn, title="Test", company="Co", location="City")

    from emploi.dashboard import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.get("/api/offers?limit=5")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) == 1
        assert data[0]["title"] == "Test"
