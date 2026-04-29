import json
import os
import subprocess

from typer.testing import CliRunner

from emploi.cli import app


runner = CliRunner()


def test_doctor_json_reports_ready_core_and_missing_browser(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_COMMAND", "definitely-missing-managed-browser")

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "degraded"
    assert payload["database"]["status"] == "ok"
    assert payload["database"]["path"] == str(db_path)
    assert payload["managed_browser"]["status"] == "missing"
    assert payload["managed_browser"]["available"] is False
    assert payload["managed_browser"]["probe"] == "not_run"
    assert payload["managed_browser"]["can_run_smoke"] is False
    assert payload["managed_browser"]["command"] == "definitely-missing-managed-browser"
    assert "EMPLOI_MANAGED_BROWSER_COMMAND" in payload["managed_browser"]["remediation"]
    assert payload["recommended_actions"]


def test_doctor_json_reports_healthy_browser_when_status_probe_succeeds(tmp_path, monkeypatch):
    browser_command = tmp_path / "managed-browser"
    browser_command.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "assert sys.argv[1:] == ['status', '--site', 'france-travail', '--profile', 'emploi', '--json']\n"
        "print(json.dumps({'ok': True, 'state': 'ready'}))\n"
    )
    browser_command.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_COMMAND", "managed-browser")

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["managed_browser"]["status"] == "ok"
    assert payload["managed_browser"]["available"] is True
    assert payload["managed_browser"]["probe"] == "ok"
    assert payload["managed_browser"]["can_run_smoke"] is True
    assert payload["managed_browser"]["payload"] == {"ok": True, "state": "ready"}
    assert payload["managed_browser"]["remediation"] == ""
    assert payload["recommended_actions"] == []


def test_doctor_json_reports_browser_probe_error(tmp_path, monkeypatch):
    browser_command = tmp_path / "managed-browser"
    browser_command.write_text("#!/bin/sh\necho 'profile locked' >&2\nexit 7\n")
    browser_command.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_COMMAND", "managed-browser")

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "degraded"
    assert payload["managed_browser"]["status"] == "error"
    assert payload["managed_browser"]["available"] is True
    assert payload["managed_browser"]["probe"] == "failed"
    assert payload["managed_browser"]["can_run_smoke"] is False
    assert "profile locked" in payload["managed_browser"]["error"]
    assert "emploi browser smoke" in payload["managed_browser"]["remediation"]
    assert payload["recommended_actions"] == [
        "Relancer `emploi browser smoke --json` et vérifier que le profil Managed Browser emploi/france-travail est disponible et connecté."
    ]


def test_doctor_text_is_human_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_COMMAND", "definitely-missing-managed-browser")

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Diagnostic Emploi" in result.stdout
    assert "Base SQLite" in result.stdout
    assert "Managed Browser" in result.stdout
    assert "degraded" in result.stdout
