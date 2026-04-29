import json

from emploi.applications import create_application_draft
from emploi.db import add_offer, connect, init_db, list_offer_events


def test_create_application_draft_persists_path_and_event(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(conn, title="Support Python", company="Acme", location="Remote", description="Support et Python")

    result = create_application_draft(conn, offer_id, drafts_dir=tmp_path / "drafts")

    assert result.draft_path.exists()
    assert "Support Python" in result.draft_path.read_text(encoding="utf-8")
    application = conn.execute("SELECT * FROM applications WHERE id = ?", (result.application_id,)).fetchone()
    assert application["status"] == "draft"
    assert application["draft_path"] == str(result.draft_path)
    events = list_offer_events(conn, offer_id)
    assert events[0]["event_type"] == "application_draft_created"
    assert json.loads(events[0]["payload_json"])["submit_application"] is False
