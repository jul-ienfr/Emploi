import json
import os
import subprocess

import pytest
from typer.testing import CliRunner

from emploi.cli import app


runner = CliRunner()


@pytest.fixture(autouse=True)
def clean_managed_browser_timeout(monkeypatch):
    monkeypatch.delenv("EMPLOI_MANAGED_BROWSER_TIMEOUT", raising=False)


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


def test_doctor_json_no_browser_probe_skips_managed_browser_command(tmp_path, monkeypatch):
    browser_command = tmp_path / "managed-browser"
    invoked = tmp_path / "invoked"
    browser_command.write_text(f"#!/bin/sh\ntouch {invoked}\nexit 1\n")
    browser_command.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_COMMAND", "managed-browser")
    accounts_dir = tmp_path / "config" / "emploi"
    accounts_dir.mkdir(parents=True)
    (accounts_dir / "accounts.json").write_text(
        json.dumps({"profiles": {"candidature": "emploi-candidature", "officiel": "emploi-officiel"}, "default": "candidature"})
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    result = runner.invoke(app, ["doctor", "--json", "--no-browser-probe"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["managed_browser"]["probe"] == "skipped"
    assert payload["managed_browser"]["status"] == "available"
    assert payload["recommended_actions"] == []
    assert not invoked.exists()


def test_doctor_json_reports_healthy_browser_when_status_probe_succeeds(tmp_path, monkeypatch):
    browser_command = tmp_path / "managed-browser"
    browser_command.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "assert sys.argv[1:] == ['profile', 'status', '--profile', 'emploi-candidature', '--site', 'france-travail', '--json']\n"
        "print(json.dumps({'ok': True, 'state': 'ready'}))\n"
    )
    browser_command.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_COMMAND", "managed-browser")
    accounts_dir = tmp_path / "config" / "emploi"
    accounts_dir.mkdir(parents=True)
    (accounts_dir / "accounts.json").write_text(
        json.dumps({"profiles": {"candidature": "emploi-candidature", "officiel": "emploi-officiel"}, "default": "candidature"})
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

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


def test_doctor_json_reports_degraded_when_accounts_missing(tmp_path, monkeypatch):
    browser_command = tmp_path / "managed-browser"
    browser_command.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "assert sys.argv[1:] == ['profile', 'status', '--profile', 'emploi-candidature', '--site', 'france-travail', '--json']\n"
        "print(json.dumps({'ok': True, 'state': 'ready'}))\n"
    )
    browser_command.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_COMMAND", "managed-browser")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "degraded"
    assert payload["database"]["status"] == "ok"
    assert payload["managed_browser"]["status"] == "ok"
    assert payload["accounts"]["status"] == "missing"
    assert payload["recommended_actions"] == [
        "Configurer les comptes France Travail : créer ~/.config/emploi/accounts.json avec les deux profils (candidature, officiel)."
    ]


def test_doctor_json_reports_invalid_browser_timeout_without_traceback(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_COMMAND", "managed-browser")
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_TIMEOUT", "slow")

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "degraded"
    assert payload["managed_browser"]["status"] == "error"
    assert payload["managed_browser"]["probe"] == "not_run"
    assert "EMPLOI_MANAGED_BROWSER_TIMEOUT" in payload["managed_browser"]["error"]
    assert "60" in payload["managed_browser"]["remediation"]
    assert "Traceback" not in result.output


def test_doctor_json_reports_shell_like_browser_command_when_status_probe_succeeds(tmp_path, monkeypatch):
    browser_script = tmp_path / "managed-browser.js"
    browser_script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "assert sys.argv[1:] == ['profile', 'status', '--profile', 'emploi-candidature', '--site', 'france-travail', '--json']\n"
        "print(json.dumps({'ok': True, 'state': 'ready'}))\n"
    )
    browser_script.chmod(0o755)
    node = tmp_path / "node"
    node.write_text("#!/bin/sh\nexec python3 \"$@\"\n")
    node.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_COMMAND", f"node {browser_script}")
    accounts_dir = tmp_path / "config" / "emploi"
    accounts_dir.mkdir(parents=True)
    (accounts_dir / "accounts.json").write_text(
        json.dumps({"profiles": {"candidature": "emploi-candidature", "officiel": "emploi-officiel"}, "default": "candidature"})
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["managed_browser"]["status"] == "ok"
    assert payload["managed_browser"]["command"] == f"node {browser_script}"
    assert payload["managed_browser"]["payload"] == {"ok": True, "state": "ready"}


def test_doctor_json_reports_browser_probe_error(tmp_path, monkeypatch):
    browser_command = tmp_path / "managed-browser"
    browser_command.write_text("#!/bin/sh\necho 'profile locked' >&2\nexit 7\n")
    browser_command.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_COMMAND", "managed-browser")
    accounts_dir = tmp_path / "config" / "emploi"
    accounts_dir.mkdir(parents=True)
    (accounts_dir / "accounts.json").write_text(
        json.dumps({"profiles": {"candidature": "emploi-candidature", "officiel": "emploi-officiel"}, "default": "candidature"})
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

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
        "Relancer `emploi browser smoke --json` et vérifier que le profil Managed Browser (défaut: emploi-candidature/france-travail) est disponible et connecté."
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
