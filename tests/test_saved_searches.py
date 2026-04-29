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
    set_saved_search_enabled,
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


def test_set_saved_search_enabled_updates_by_name_or_id_and_errors(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    search_id = add_saved_search(conn, name="support-annecy", query="support", where_text="Annecy")

    disabled = set_saved_search_enabled(conn, "support-annecy", False)
    assert disabled["id"] == search_id
    assert disabled["name"] == "support-annecy"
    assert disabled["enabled"] == 0
    assert get_saved_search(conn, search_id)["enabled"] == 0

    enabled = set_saved_search_enabled(conn, search_id, True)
    assert enabled["id"] == search_id
    assert enabled["enabled"] == 1

    try:
        set_saved_search_enabled(conn, "absent", False)
    except ValueError as error:
        assert "Profil de recherche introuvable: absent" in str(error)
    else:
        raise AssertionError("set_saved_search_enabled should reject missing profiles")


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
    monkeypatch.delenv("EMPLOI_MANAGED_BROWSER_COMMAND", raising=False)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1] == "navigate":
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"ok": True, "url": args[args.index("--url") + 1]}), stderr="")
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
    assert any(call[1] == "navigate" for call in calls)


def test_search_profile_cli_enable_disable_toggle_existing_profile(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    added = runner.invoke(
        app,
        ["search-profile", "add", "support", "--query", "support", "--where", "Annecy", "--disabled"],
    )
    enabled = runner.invoke(app, ["search-profile", "enable", "support"])
    disabled = runner.invoke(app, ["search-profile", "disable", "support"])
    toggled = runner.invoke(app, ["search-profile", "toggle", "support"])
    missing = runner.invoke(app, ["search-profile", "enable", "absent"])

    assert added.exit_code == 0
    assert enabled.exit_code == 0
    assert "Profil de recherche activé" in enabled.stdout
    assert "support" in enabled.stdout
    assert disabled.exit_code == 0
    assert "Profil de recherche désactivé" in disabled.stdout
    assert toggled.exit_code == 0
    assert "Profil de recherche activé" in toggled.stdout
    assert missing.exit_code != 0
    assert "Profil de recherche introuvable: absent" in missing.output

    with connect(db_path) as conn:
        init_db(conn)
        assert get_saved_search(conn, "support")["enabled"] == 1


def test_search_profile_run_all_skips_disabled_profiles(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        active_id = add_saved_search(conn, name="active", query="support", where_text="Annecy")
        disabled_id = add_saved_search(conn, name="disabled", query="python", where_text="Lyon", enabled=False)
    seen = []

    def fake_run_saved_search(conn, search_id_or_name, *, site="france-travail", profile="emploi"):
        seen.append(search_id_or_name)
        return []

    monkeypatch.setattr("emploi.cli.run_saved_search", fake_run_saved_search)

    ran = runner.invoke(app, ["search-profile", "run", "--all"])

    assert ran.exit_code == 0
    assert "1 profil(s) actif(s)" in ran.stdout
    assert seen == [active_id]
    assert disabled_id not in seen


def test_install_default_julien_search_profiles_is_idempotent_and_contextual(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)

    from emploi.db import install_default_julien_search_profiles

    first = install_default_julien_search_profiles(conn)
    second = install_default_julien_search_profiles(conn)
    searches = list_saved_searches(conn)

    assert len(first["created"]) >= 4
    assert first["skipped"] == []
    assert second["created"] == []
    assert {item["name"] for item in second["skipped"]} == {row["name"] for row in searches}
    assert len(searches) == len(first["created"])
    combined = "\n".join(
        f"{row['name']} {row['query']} {row['where_text']} {row['contract']} {row['notes']}" for row in searches
    ).lower()
    assert "bogève" in combined
    assert "télétravail" in combined
    assert "python" in combined
    assert "support" in combined
    assert "admin système" in combined
    assert "sans voiture" in combined
    assert all(row["enabled"] == 1 for row in searches)


def test_search_profile_cli_install_defaults_and_improved_list_output(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    installed = runner.invoke(app, ["search-profile", "install-julien-defaults"])
    installed_again = runner.invoke(app, ["search-profile", "install-julien-defaults"])
    listed = runner.invoke(app, ["search-profile", "list"])

    assert installed.exit_code == 0
    assert "créé" in installed.stdout
    assert "Bogève" in installed.stdout
    assert installed_again.exit_code == 0
    assert "ignoré" in installed_again.stdout
    assert listed.exit_code == 0
    assert "Actif" in listed.stdout
    assert "Dernier run" in listed.stdout
    assert "jamais" in listed.stdout
    assert "Notes" in listed.stdout
    assert "sans voiture" in listed.stdout


def test_search_profile_run_all_reports_created_updated_enabled_and_last_run(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    add = runner.invoke(app, ["search-profile", "add", "support", "--query", "support", "--where", "Annecy"])
    assert add.exit_code == 0

    def fake_run_saved_search(conn, search_id_or_name, *, site="france-travail", profile="emploi"):
        saved = get_saved_search(conn, search_id_or_name)
        update_saved_search_last_run(conn, int(saved["id"]), "2026-04-29T12:00:00+00:00")
        from emploi.france_travail.flows import SearchImportResult

        return [
            SearchImportResult(1, True, "Technicien support", 80, "https://example.test/1"),
            SearchImportResult(2, False, "Support N2", 70, "https://example.test/2"),
        ]

    monkeypatch.setattr("emploi.cli.run_saved_search", fake_run_saved_search)

    ran = runner.invoke(app, ["search-profile", "run", "--all"])
    listed = runner.invoke(app, ["search-profile", "list"])

    assert ran.exit_code == 0
    assert "profil(s) actif(s)" in ran.stdout
    assert "créée(s): 1" in ran.stdout
    assert "mise(s) à jour: 1" in ran.stdout
    assert "Dernier run" in ran.stdout
    assert "2026-04-29T12:00:00+00:00" in ran.stdout
    assert listed.exit_code == 0
    assert "2026-04-29T12:00:00+00:00" in listed.stdout


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
