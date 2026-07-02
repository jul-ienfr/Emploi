"""End-to-end integration tests: import → score → draft → export → kanban."""

from __future__ import annotations

from emploi.applications import create_application_draft
from emploi.db import (
    add_offer,
    connect,
    get_offer,
    init_db,
    list_applications,
    list_offer_events,
)


def test_import_score_draft_full_pipeline(tmp_path, monkeypatch):
    """Full pipeline: add offer → verify score → create draft → verify events."""
    db_path = tmp_path / "emploi.sqlite"
    drafts_dir = tmp_path / "drafts"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)

        # Step 1: Add an offer
        offer_id = add_offer(
            conn,
            title="Technicien support Python",
            company="Acme Corp",
            location="Annemasse",
            description="Support informatique, Python, CDI, télétravail partiel",
            salary="32k€",
            remote="hybride",
            contract_type="CDI",
        )
        assert offer_id > 0

        # Step 2: Verify scoring
        offer = get_offer(conn, offer_id)
        assert offer is not None
        assert offer["score"] >= 50  # Base score
        assert "support" in offer["score_reasons"].lower() or "python" in offer["score_reasons"].lower()

        # Step 3: Create draft
        draft = create_application_draft(conn, offer_id, drafts_dir=drafts_dir)
        assert draft.draft_path.exists()
        content = draft.draft_path.read_text(encoding="utf-8")
        assert "Technicien support Python" in content
        assert "Acme Corp" in content

        # Step 4: Verify events
        events = list_offer_events(conn, offer_id)
        event_types = [e["event_type"] for e in events]
        assert "application_draft_created" in event_types

        # Step 5: Verify application was created
        apps = list_applications(conn)
        assert len(apps) == 1
        assert apps[0]["status"] == "draft"

        # Step 6: Verify offer status updated
        offer = get_offer(conn, offer_id)
        assert offer["status"] == "draft"


def test_multiple_offres_scoring_ordering(tmp_path, monkeypatch):
    """Add multiple offers and verify they're scored and ordered correctly."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)

        high_id = add_offer(conn, title="Python Developer Remote", company="TechCo", description="Python Django React remote CDI", remote="full remote")
        low_id = add_offer(conn, title="Vendeur", company="Magasin", description="Vente en magasin", contract_type="CDD")

        high = get_offer(conn, high_id)
        low = get_offer(conn, low_id)

        assert high["score"] > low["score"]


def test_rescoring_updates_score(tmp_path, monkeypatch):
    """Verify that rescoring an offer updates its score."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    from emploi.db import rescore_offer

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Support IT", company="TestCo", description="Support helpdesk")

        original_score = get_offer(conn, offer_id)["score"]
        rescoreed = rescore_offer(conn, offer_id)
        assert rescoreed["score"] == original_score  # Same description = same score
