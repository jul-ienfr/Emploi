"""End-to-end integration tests: import → score → draft → export → kanban."""

from __future__ import annotations

from unittest.mock import MagicMock

from emploi.applications import create_application_draft
from emploi.db import (
    add_offer,
    connect,
    get_offer,
    init_db,
    list_applications,
    list_offer_events,
)
from emploi.nextcloud_deck import create_offer_card
from emploi.nextcloud_files import export_application_to_nextcloud


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

        high_id = add_offer(
            conn,
            title="Python Developer Remote",
            company="TechCo",
            description="Python Django React remote CDI",
            remote="full remote",
        )
        low_id = add_offer(
            conn, title="Vendeur", company="Magasin", description="Vente en magasin", contract_type="CDD"
        )

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


def test_draft_then_export_nextcloud_full_pipeline(tmp_path, monkeypatch):
    """Full pipeline: offer → draft → export to Nextcloud (mocked WebDAV)."""
    db_path = tmp_path / "emploi.sqlite"
    drafts_dir = tmp_path / "drafts"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(
            conn,
            title="Support Python",
            company="TestCo",
            location="Annemasse",
            description="Support informatique Python CDI",
            contract_type="CDI",
        )

        # Step 1: Create draft
        draft = create_application_draft(conn, offer_id, drafts_dir=drafts_dir)
        assert draft.draft_path.exists()

        # Step 2: Export to Nextcloud (mocked WebDAV)
        fake_client = MagicMock()
        result = export_application_to_nextcloud(
            conn,
            offer_id,
            endpoint={"base_url": "https://nextcloud.test", "remote_root": "/Emploi"},
            client=fake_client,
            dry_run=False,
        )
        assert result.remote_dir.startswith("/Emploi/Candidatures/")
        assert "offre.md" in result.uploaded_files
        assert "brouillon.md" in result.uploaded_files

        # Step 3: Verify WebDAV calls were made
        assert fake_client.ensure_dir.call_count >= 3  # root, Candidatures, offer dir
        assert fake_client.upload_text.call_count >= 2  # offre.md, brouillon.md

        # Step 4: Verify events
        events = list_offer_events(conn, offer_id)
        event_types = [e["event_type"] for e in events]
        assert "nextcloud_exported" in event_types


def test_draft_then_kanban_card_full_pipeline(tmp_path, monkeypatch):
    """Full pipeline: offer → draft → create Deck card (mocked)."""
    db_path = tmp_path / "emploi.sqlite"
    drafts_dir = tmp_path / "drafts"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(
            conn,
            title="Chauffeur PL",
            company="Transport Co",
            location="Bonneville",
            description="Conduite poids lourd",
            contract_type="CDI",
        )

        # Step 1: Create draft
        draft = create_application_draft(conn, offer_id, drafts_dir=drafts_dir)
        assert draft.draft_path.exists()

        # Step 2: Create Deck card (mocked)
        fake_client = MagicMock()
        fake_client.create_card.return_value = {"id": 42}
        result = create_offer_card(
            conn,
            offer_id,
            endpoint={"name": "test", "board_id": 1, "base_url": "https://nc.test"},
            stack_id=10,
            client=fake_client,
        )
        assert result.card_id == 42
        assert "Chauffeur PL" in result.title
        assert "Transport Co" in result.title

        # Step 3: Verify Deck API was called
        fake_client.create_card.assert_called_once()
        call_kwargs = fake_client.create_card.call_args[1]
        assert call_kwargs["stack_id"] == 10

        # Step 4: Verify events
        events = list_offer_events(conn, offer_id)
        event_types = [e["event_type"] for e in events]
        assert "nextcloud_deck_card" in event_types


def test_full_daily_workflow(tmp_path, monkeypatch):
    """Simulate a full daily workflow: search profiles → brief → next actions."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)

        # Step 1: Install search profiles
        from emploi.db import install_default_julien_search_profiles

        result = install_default_julien_search_profiles(conn)
        assert len(result["created"]) > 0

        # Step 2: Add some offers manually
        high_id = add_offer(
            conn,
            title="Support Python Remote",
            company="TechCo",
            description="Python Django support CDI télétravail",
            remote="full remote",
            contract_type="CDI",
        )
        low_id = add_offer(
            conn,
            title="Vendeur",
            company="Magasin",
            description="Vente en magasin physique",
            contract_type="CDD",
        )

        # Step 3: Verify scoring order
        high = get_offer(conn, high_id)
        low = get_offer(conn, low_id)
        assert high["score"] > low["score"]

        # Step 4: Create a draft for the best offer
        draft = create_application_draft(conn, high_id, drafts_dir=tmp_path / "drafts")
        assert draft.draft_path.exists()

        # Step 5: Verify next actions
        from emploi.db import list_next_actions

        actions = list_next_actions(conn)
        assert len(actions) >= 1
        assert any(a["action"] == "Finaliser brouillon" for a in actions)
