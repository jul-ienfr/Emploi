"""Load tests — batch import performance validation."""

from __future__ import annotations

import json
import time

from emploi.db import add_offer, connect, init_db, list_offers
from emploi.importers import import_offers_file


def _generate_offers_json(count: int, tmp_path) -> str:
    """Generate a JSON file with *count* synthetic offers."""
    offers = []
    for i in range(count):
        offers.append(
            {
                "title": f"Offre {i}: Technicien support informatique",
                "company": f"Entreprise {i % 100}",
                "location": f"Ville {i % 50}",
                "description": f"Description de l'offre {i}. Support informatique, Python, CDI.",
                "contract_type": "CDI",
                "salary": f"{30000 + (i % 20) * 1000}€",
                "source": "load-test",
                "external_id": f"load-test-{i}",
            }
        )
    path = tmp_path / "offers.json"
    path.write_text(json.dumps(offers), encoding="utf-8")
    return str(path)


def test_batch_import_100_offers(tmp_path, monkeypatch):
    """Import 100 offers and verify timing."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    path = _generate_offers_json(100, tmp_path)

    with connect(db_path) as conn:
        init_db(conn)
        start = time.monotonic()
        result = import_offers_file(conn, path, source="load-test")
        elapsed = time.monotonic() - start

    assert result.created == 100
    assert result.skipped == 0
    assert elapsed < 10  # should complete in under 10 seconds


def test_batch_import_500_offers(tmp_path, monkeypatch):
    """Import 500 offers and verify timing."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    path = _generate_offers_json(500, tmp_path)

    with connect(db_path) as conn:
        init_db(conn)
        start = time.monotonic()
        result = import_offers_file(conn, path, source="load-test")
        elapsed = time.monotonic() - start

    assert result.created == 500
    assert elapsed < 30  # under 30 seconds


def test_batch_import_dedup(tmp_path, monkeypatch):
    """Import same file twice — second pass should create 0 new offers."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    path = _generate_offers_json(100, tmp_path)

    with connect(db_path) as conn:
        init_db(conn)
        import_offers_file(conn, path, source="load-test")
        result2 = import_offers_file(conn, path, source="load-test")

    assert result2.created == 0
    assert result2.updated == 100


def test_single_offer_insert_performance(tmp_path, monkeypatch):
    """Insert 1000 offers one by one and verify total time."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)
        start = time.monotonic()
        for i in range(1000):
            add_offer(
                conn,
                title=f"Offer {i}",
                company=f"Company {i % 100}",
                description="Support informatique Python CDI",
            )
        elapsed = time.monotonic() - start

    with connect(db_path) as conn:
        count = len(list_offers(conn, include_inactive=True))

    assert count == 1000
    assert elapsed < 60  # under 60 seconds for 1000 individual inserts
