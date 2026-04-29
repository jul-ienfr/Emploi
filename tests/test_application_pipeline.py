from typer.testing import CliRunner

from emploi.cli import app
from emploi.db import add_application, add_offer, connect, init_db, list_next_actions


runner = CliRunner()


def test_application_status_command_validates_and_updates_pipeline_status(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    conn = connect(db_path)
    init_db(conn)
    offer_id = add_offer(conn, title="Support Python", company="Acme")
    application_id = add_application(conn, offer_id, status="analyzed")
    conn.close()

    result = runner.invoke(app, ["application", "status", str(application_id), "interview"])

    assert result.exit_code == 0
    assert "interview" in result.stdout
    conn = connect(db_path)
    application = conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
    offer = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
    assert application["status"] == "interview"
    assert offer["status"] == "interview"

    invalid = runner.invoke(app, ["application", "status", str(application_id), "hired"])
    assert invalid.exit_code != 0
    assert "Statut invalide" in invalid.stdout


def test_application_update_alias_and_followup_schedule_store_iso_date(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    conn = connect(db_path)
    init_db(conn)
    offer_id = add_offer(conn, title="Support IT", company="Beta")
    application_id = add_application(conn, offer_id, status="sent")
    conn.close()

    update = runner.invoke(app, ["application", "update", str(application_id), "response"])
    assert update.exit_code == 0
    assert "response" in update.stdout

    followup = runner.invoke(app, ["application", "followup", str(application_id), "2026-05-04"])
    assert followup.exit_code == 0
    assert "2026-05-04" in followup.stdout
    conn = connect(db_path)
    application = conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
    assert application["status"] == "followup"
    assert application["next_action_at"] == "2026-05-04"

    bad_date = runner.invoke(app, ["application", "followup", str(application_id), "04/05/2026"])
    assert bad_date.exit_code != 0
    assert "YYYY-MM-DD" in bad_date.stdout


def test_next_actions_prioritize_drafts_due_followups_stale_sent_then_new_ft_offers(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    new_ft = add_offer(
        conn,
        title="Remote Python",
        company="NewCo",
        description="Python CDI télétravail débutant accepté candidature simple",
        remote="remote",
        contract_type="CDI",
        external_source="france-travail",
        is_active=True,
    )
    stale_offer = add_offer(conn, title="Sent stale", company="OldCo")
    stale_app = add_application(conn, stale_offer, status="sent")
    due_offer = add_offer(conn, title="Due followup", company="DueCo")
    due_app = add_application(conn, due_offer, status="followup")
    future_offer = add_offer(conn, title="Future followup", company="FutureCo")
    future_app = add_application(conn, future_offer, status="followup")
    draft_offer = add_offer(conn, title="Draft app", company="DraftCo")
    draft_app = add_application(conn, draft_offer, status="draft")

    conn.execute("UPDATE applications SET draft_path = ? WHERE id = ?", ("/tmp/draft.md", draft_app))
    conn.execute("UPDATE applications SET next_action_at = ? WHERE id = ?", ("2026-04-29", due_app))
    conn.execute("UPDATE applications SET next_action_at = ? WHERE id = ?", ("2026-05-01", future_app))
    conn.execute("UPDATE applications SET applied_at = ?, last_contact_at = ? WHERE id = ?", ("2026-04-10", "2026-04-10", stale_app))
    conn.commit()

    actions = list_next_actions(conn, today="2026-04-29", stale_after_days=14)

    assert [action["offer_id"] for action in actions[:4]] == [draft_offer, due_offer, stale_offer, new_ft]
    assert actions[0]["action"] == "Finaliser brouillon"
    assert actions[1]["action"] == "Relancer candidature"
    assert actions[1]["due_date"] == "2026-04-29"
    assert actions[2]["action"] == "Relancer candidature envoyée"
    assert future_offer not in [action["offer_id"] for action in actions]
