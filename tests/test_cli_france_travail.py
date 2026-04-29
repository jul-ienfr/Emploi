import json
import subprocess

from typer.testing import CliRunner

from emploi.cli import app
from emploi.db import add_offer, connect, init_db


runner = CliRunner()


def test_ft_search_imports_offers_via_managed_browser(tmp_path, monkeypatch):
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
                                "description": "Support informatique",
                            }
                        ],
                        "text": "Technicien support Acme Annecy",
                    }
                ),
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.invoke(app, ["ft", "search", "support", "--location", "Annecy"])

    assert result.exit_code == 0
    assert "1 offre" in result.stdout
    assert "Technicien support" in result.stdout
    assert calls[0][1] == "open"
    assert calls[1][1] == "snapshot"


def test_ft_refresh_updates_offer(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    conn = connect(db_path)
    init_db(conn)
    add_offer(
        conn,
        title="Support",
        external_source="france-travail",
        browser_url="https://candidat.francetravail.fr/offres/recherche/detail/ABC123",
    )

    def fake_run(args, **kwargs):
        payload = {"ok": True, "url": args[-2]} if args[1] == "open" else {"text": "Cette offre n'est plus disponible"}
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.invoke(app, ["ft", "refresh", "1"])

    assert result.exit_code == 0
    assert "inactive" in result.stdout


def test_ft_apply_check_draft_and_open(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    drafts = tmp_path / "drafts"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    conn = connect(db_path)
    init_db(conn)
    add_offer(
        conn,
        title="Support",
        company="Acme",
        external_source="france-travail",
        browser_url="https://candidat.francetravail.fr/offres/recherche/detail/ABC123",
    )
    opened = []

    def fake_run(args, **kwargs):
        opened.append(args)
        if args[1] == "open":
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"ok": True}), stderr="")
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"text": "Candidater maintenant"}), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    check = runner.invoke(app, ["ft", "apply", "1", "--check"])
    draft = runner.invoke(app, ["ft", "apply", "1", "--draft", "--drafts-dir", str(drafts)])
    open_result = runner.invoke(app, ["ft", "apply", "1", "--open"])

    assert check.exit_code == 0
    assert "candidature possible" in check.stdout.lower()
    assert draft.exit_code == 0
    assert "Brouillon" in draft.stdout
    assert any(drafts.iterdir())
    assert open_result.exit_code == 0
    assert "ouverte" in open_result.stdout
    assert any(call[1] == "open" for call in opened)


def test_ft_smoke_dry_run_json_does_not_touch_database_or_submit(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    def fake_run(args, **kwargs):  # pragma: no cover - should never be called
        raise AssertionError(args)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.invoke(app, ["ft", "smoke", "support", "--location", "Annecy", "--dry-run", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "dry-run"
    assert payload["query"] == "support"
    assert payload["location"] == "Annecy"
    assert payload["submit_application"] is False
    assert payload["database_write"] is False
    assert "candidat.francetravail.fr/offres/recherche" in payload["search_url"]
    assert not db_path.exists()


def test_ft_smoke_json_opens_search_and_snapshots_without_importing(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1] == "open":
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"ok": True, "url": args[-2]}), stderr="")
        if args[1] == "snapshot":
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"ok": True, "cards": [{"title": "Support"}]}), stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.invoke(app, ["ft", "smoke", "support", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["offer_count"] == 1
    assert payload["database_write"] is False
    assert payload["submit_application"] is False
    assert [call[1] for call in calls] == ["open", "snapshot"]
    assert not db_path.exists()
