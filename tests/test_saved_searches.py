import subprocess
import json

from typer.testing import CliRunner

from emploi.browser.models import BrowserCommandResult
from emploi.cli import app
from emploi.db import (
    add_application,
    add_offer,
    add_saved_search,
    application_summary,
    connect,
    get_saved_search,
    init_db,
    list_saved_searches,
    update_saved_search_last_run,
)
from emploi.france_travail.flows import run_saved_search


runner = CliRunner()


class FakeBrowser:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.opened = []

    def open(self, url, *, site="france-travail", profile="emploi"):
        self.opened.append(url)
        return BrowserCommandResult("open", site, profile, {"ok": True, "url": url})

    def snapshot(self, *, label=None, site="france-travail", profile="emploi"):
        return BrowserCommandResult("snapshot", site, profile, self.snapshots.pop(0))


def test_saved_search_helpers_roundtrip_and_last_run(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)

    search_id = add_saved_search(
        conn,
        name="support-annecy",
        query="technicien support",
        where_text="Annecy",
        radius=15,
        contract="CDI",
    )

    saved = get_saved_search(conn, search_id)
    assert saved is not None
    assert saved["name"] == "support-annecy"
    assert saved["query"] == "technicien support"
    assert saved["where_text"] == "Annecy"
    assert saved["radius"] == 15
    assert saved["contract"] == "CDI"
    assert saved["enabled"] == 1
    assert saved["created_at"]
    assert saved["last_run_at"] == ""

    update_saved_search_last_run(conn, search_id, "2026-04-29T12:00:00+00:00")
    assert get_saved_search(conn, search_id)["last_run_at"] == "2026-04-29T12:00:00+00:00"
    assert [row["name"] for row in list_saved_searches(conn)] == ["support-annecy"]


def test_run_saved_search_uses_france_travail_flow_and_updates_timestamp(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    search_id = add_saved_search(conn, name="support", query="support", where_text="Annecy")
    browser = FakeBrowser([
        {
            "cards": [
                {
                    "title": "Technicien support",
                    "company": "Acme",
                    "location": "Annecy",
                    "url": "https://candidat.francetravail.fr/offres/recherche/detail/ABC123",
                    "description": "Support informatique",
                }
            ],
            "text": "Technicien support Acme Annecy",
        }
    ])

    results = run_saved_search(conn, search_id, browser=browser)

    assert len(results) == 1
    assert results[0].title == "Technicien support"
    assert "motsCles=support" in browser.opened[0]
    assert "lieux=Annecy" in browser.opened[0]
    assert get_saved_search(conn, search_id)["last_run_at"]


def test_search_profile_cli_add_list_and_run(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1] == "open":
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"ok": True, "url": args[-2]}), stderr="")
        if args[1] == "snapshot":
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps(
                    {
                        "cards": [
                            {
                                "title": "Technicien support",
                                "company": "Acme",
                                "location": "Annecy",
                                "url": "https://candidat.francetravail.fr/offres/recherche/detail/ABC123",
                            }
                        ],
                        "text": "Technicien support Acme Annecy",
                    }
                ),
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(subprocess, "run", fake_run)

    added = runner.invoke(
        app,
        [
            "search-profile",
            "add",
            "support",
            "--query",
            "support",
            "--where",
            "Annecy",
            "--radius",
            "20",
            "--contract",
            "CDI",
        ],
    )
    listed = runner.invoke(app, ["search-profile", "list"])
    ran = runner.invoke(app, ["search-profile", "run", "support"])

    assert added.exit_code == 0
    assert "Profil de recherche ajouté" in added.stdout
    assert listed.exit_code == 0
    assert "support" in listed.stdout
    assert "Annecy" in listed.stdout
    assert ran.exit_code == 0
    assert "1 offre" in ran.stdout
    assert any(call[1] == "open" for call in calls)


def test_report_summary_and_next_actions_include_ft_operator_counts(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    conn = connect(db_path)
    init_db(conn)
    high = add_offer(
        conn,
        title="Technicien support",
        company="Acme",
        description="Support informatique débutant accepté CDI",
        external_source="france-travail",
        browser_url="https://candidat.francetravail.fr/offres/recherche/detail/ABC123",
        is_active=True,
    )
    low = add_offer(conn, title="Vendeur", external_source="france-travail", is_active=True)
    inactive = add_offer(conn, title="Inactive", external_source="france-travail", is_active=False)
    add_application(conn, high, status="draft")
    add_application(conn, low, status="sent")

    summary = application_summary(conn)
    assert summary["ft_offers"] == 3
    assert summary["active_ft_offers"] == 2
    assert summary["draft_applications"] == 1
    assert summary["sent_applications"] == 1

    report = runner.invoke(app, ["report"])
    next_result = runner.invoke(app, ["next"])

    assert report.exit_code == 0
    assert "Offres France Travail" in report.stdout
    assert "FT actives" in report.stdout
    assert "Brouillons" in report.stdout
    assert next_result.exit_code == 0
    assert "Prochaines actions" in next_result.stdout
    assert "Finaliser brouillon" in next_result.stdout
    assert "Technicien support" in next_result.stdout
