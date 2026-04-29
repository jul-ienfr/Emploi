import json

from typer.testing import CliRunner

from emploi.cli import app
from emploi.db import add_offer, connect, init_db, list_next_actions, list_offer_events


runner = CliRunner()


def test_application_draft_creates_short_french_file_and_persists_metadata(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    drafts_dir = tmp_path / "brouillons"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    conn = connect(db_path)
    init_db(conn)
    offer_id = add_offer(
        conn,
        title="Technicien support Python",
        company="Acme",
        location="Télétravail / Annecy",
        url="https://example.test/offre-1",
        description="Support utilisateurs, Python, CDI, télétravail partiel, candidature simple.",
        salary="30k€",
        remote="hybride",
        contract_type="CDI",
    )
    conn.close()

    result = runner.invoke(app, ["application", "draft", str(offer_id), "--drafts-dir", str(drafts_dir)])

    assert result.exit_code == 0
    assert "Brouillon créé" in result.stdout
    assert "Aucune soumission" in result.stdout
    draft_files = list(drafts_dir.glob("*.md"))
    assert len(draft_files) == 1
    content = draft_files[0].read_text(encoding="utf-8")
    assert "# Brouillon de candidature" in content
    assert "Technicien support Python" in content
    assert "Acme" in content
    assert "Télétravail / Annecy" in content
    assert "Bonjour," in content
    assert "À vérifier avant envoi" in content
    assert "Aucune soumission automatique" in content
    assert len(content.splitlines()) <= 45

    conn = connect(db_path)
    application = conn.execute("SELECT * FROM applications WHERE offer_id = ?", (offer_id,)).fetchone()
    offer = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
    assert application is not None
    assert application["status"] == "draft"
    assert str(draft_files[0]) in application["notes"]
    assert offer["status"] == "draft"
    events = list_offer_events(conn, offer_id)
    assert events[0]["event_type"] == "application_draft_created"
    payload = json.loads(events[0]["payload_json"])
    assert payload["draft_path"] == str(draft_files[0])
    assert payload["submit_application"] is False


def test_application_draft_reuses_existing_draft_application_and_next_shows_path(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    drafts_dir = tmp_path / "drafts"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    conn = connect(db_path)
    init_db(conn)
    offer_id = add_offer(conn, title="Support IT", company="Beta", description="Support CDI télétravail")
    conn.close()

    first = runner.invoke(app, ["application", "draft", str(offer_id), "--drafts-dir", str(drafts_dir)])
    second = runner.invoke(app, ["application", "draft", str(offer_id), "--drafts-dir", str(drafts_dir)])

    assert first.exit_code == 0
    assert second.exit_code == 0
    conn = connect(db_path)
    rows = conn.execute("SELECT * FROM applications WHERE offer_id = ? AND status = 'draft'", (offer_id,)).fetchall()
    assert len(rows) == 1
    draft_path = rows[0]["notes"].split("Draft: ", 1)[1]
    actions = list_next_actions(conn)
    assert actions[0]["action"] == "Finaliser brouillon"
    assert actions[0]["draft_path"] == draft_path
    assert "Relire le brouillon puis envoyer manuellement" in actions[0]["guidance"]

    next_result = runner.invoke(app, ["next"])
    assert next_result.exit_code == 0
    assert draft_path in next_result.stdout
    assert "envoyer manuellement" in next_result.stdout
