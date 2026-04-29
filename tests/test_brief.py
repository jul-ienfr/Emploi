import json

from typer.testing import CliRunner

from emploi.cli import app
from emploi.db import add_application, add_offer, add_saved_search, connect, init_db, schedule_application_followup


runner = CliRunner()


def _seed_brief_data(db_path):
    conn = connect(db_path)
    init_db(conn)
    best_offer = add_offer(
        conn,
        title="Support Python remote",
        company="BestCo",
        location="Télétravail",
        description="Python support CDI télétravail débutant accepté candidature simple",
        remote="remote",
        contract_type="CDI",
        external_source="france-travail",
        is_active=True,
    )
    draft_offer = add_offer(conn, title="Draft support", company="DraftCo")
    draft_app = add_application(conn, draft_offer, status="draft")
    conn.execute("UPDATE applications SET draft_path = ? WHERE id = ?", ("/tmp/draft-support.md", draft_app))

    due_offer = add_offer(conn, title="Due followup", company="DueCo")
    due_app = add_application(conn, due_offer, status="sent")
    schedule_application_followup(conn, due_app, "2026-04-29")

    stale_offer = add_offer(conn, title="Stale sent", company="OldCo")
    stale_app = add_application(conn, stale_offer, status="sent")
    conn.execute(
        "UPDATE applications SET applied_at = ?, last_contact_at = ? WHERE id = ?",
        ("2026-04-10", "2026-04-10", stale_app),
    )
    conn.execute("UPDATE offers SET created_at = ? WHERE id = ?", ("2026-04-28 10:00:00", best_offer))
    conn.commit()
    conn.close()
    return {"best_offer": best_offer, "due_offer": due_offer, "stale_offer": stale_offer}


def test_brief_json_is_parseable_and_summarizes_daily_work(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_COMMAND", "definitely-not-managed-browser")
    ids = _seed_brief_data(db_path)

    result = runner.invoke(app, ["brief", "--json", "--today", "2026-04-29"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["date"] == "2026-04-29"
    assert payload["best_offers"][0]["id"] == ids["best_offer"]
    assert payload["best_offers"][0]["title"] == "Support Python remote"
    assert any(action["action"] == "Finaliser brouillon" for action in payload["actions"])
    assert payload["due_followups"][0]["offer_id"] == ids["due_offer"]
    assert payload["stale_sent"][0]["offer_id"] == ids["stale_offer"]
    assert "Managed Browser indisponible" in payload["blockers"][0]
    assert "Aucun profil de recherche actif" in payload["blockers"][1]
    assert payload["weekly_stats"]["offers_created"] >= 1


def test_brief_readable_output_shows_best_actions_followups_blockers_and_stats(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_COMMAND", "definitely-not-managed-browser")
    _seed_brief_data(db_path)
    conn = connect(db_path)
    add_saved_search(conn, name="julien-remote", query="python remote", enabled=True)
    conn.close()

    result = runner.invoke(app, ["brief", "--today", "2026-04-29"])

    assert result.exit_code == 0
    assert "Brief Julien — 2026-04-29" in result.stdout
    assert "Meilleures offres" in result.stdout
    assert "Support Python remote" in result.stdout
    assert "Actions prioritaires" in result.stdout
    assert "Finaliser brouillon" in result.stdout
    assert "Relances dues" in result.stdout
    assert "Due followup" in result.stdout
    assert "Candidatures envoyées sans contact récent" in result.stdout
    assert "Stale sent" in result.stdout
    assert "Blockers" in result.stdout
    assert "Managed Browser indisponible" in result.stdout
    assert "Stats 7 jours" in result.stdout
