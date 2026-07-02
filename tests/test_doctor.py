from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from emploi.browser.errors import ManagedBrowserUnavailableError
from emploi.browser.models import BrowserCommandResult
from emploi.cli import app

runner = CliRunner()


def _ok(payload: dict) -> BrowserCommandResult:
    return BrowserCommandResult(command="test", site="france-travail", profile="emploi-candidature", payload=payload)


def _missing_doctor_browser(*args, **kwargs):
    return {
        "status": "error",
        "available": False,
        "probe": "not_run",
        "can_run_smoke": False,
        "command": None,
        "path": None,
        "error": "Command 'definitely-missing-managed-browser' not found",
        "remediation": "Vérifier la configuration du Managed Browser.",
    }


@pytest.fixture(autouse=True)
def clean_managed_browser_timeout(monkeypatch):
    monkeypatch.delenv("EMPLOI_MANAGED_BROWSER_TIMEOUT", raising=False)


# ---------------------------------------------------------------------------
# Doctor: browser is missing (mocked doctor check returns error)
# ---------------------------------------------------------------------------


def test_doctor_json_reports_ready_core_and_missing_browser(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    monkeypatch.setattr("emploi.doctor._check_managed_browser", _missing_doctor_browser)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "degraded"
    assert payload["database"]["status"] == "ok"
    assert payload["database"]["path"] == str(db_path)
    assert payload["managed_browser"]["status"] == "error"
    assert payload["managed_browser"]["available"] is False
    assert payload["managed_browser"]["probe"] == "not_run"
    assert payload["managed_browser"]["can_run_smoke"] is False
    assert payload["recommended_actions"]


# ---------------------------------------------------------------------------
# Doctor: no-browser-probe flag skips status check
# ---------------------------------------------------------------------------


def test_doctor_json_no_browser_probe_skips_managed_browser_command(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
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


# ---------------------------------------------------------------------------
# Doctor: browser status probe succeeds
# ---------------------------------------------------------------------------


def test_doctor_json_reports_healthy_browser_when_status_probe_succeeds(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    accounts_dir = tmp_path / "config" / "emploi"
    accounts_dir.mkdir(parents=True)
    (accounts_dir / "accounts.json").write_text(
        json.dumps({"profiles": {"candidature": "emploi-candidature", "officiel": "emploi-officiel"}, "default": "candidature"})
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    def fake_status(self, **kw):
        return _ok({"state": "ready"})

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.status", fake_status)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["managed_browser"]["status"] == "ok"
    assert payload["managed_browser"]["available"] is True
    assert payload["managed_browser"]["probe"] == "ok"
    assert payload["managed_browser"]["can_run_smoke"] is True
    assert payload["managed_browser"]["payload"]["state"] == "ready"
    assert payload["recommended_actions"] == []


# ---------------------------------------------------------------------------
# Doctor: degraded when accounts file missing
# ---------------------------------------------------------------------------


def test_doctor_json_reports_degraded_when_accounts_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    # No accounts.json created → accounts missing
    # Also mock browser status so it doesn't block the test
    def fake_status(self, **kw):
        return _ok({"state": "ready"})

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.status", fake_status)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "degraded"
    assert payload["database"]["status"] == "ok"
    # Browser should be ok since we mocked it
    assert payload["managed_browser"]["status"] in ("ok", "available")
    assert payload["accounts"]["status"] == "missing"
    assert payload["recommended_actions"] == [
        "Configurer les comptes France Travail : créer ~/.config/emploi/accounts.json avec les deux profils (candidature, officiel)."
    ]


# ---------------------------------------------------------------------------
# Doctor: invalid EMPLOI_MANAGED_BROWSER_TIMEOUT
# ---------------------------------------------------------------------------


def test_doctor_json_reports_invalid_browser_timeout_without_traceback(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_TIMEOUT", "slow")

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "degraded"
    assert payload["managed_browser"]["status"] == "error"
    assert "EMPLOI_MANAGED_BROWSER_TIMEOUT" in payload["managed_browser"]["error"]
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# Doctor: successful probe with custom base_url (no command, just URL)
# ---------------------------------------------------------------------------


def test_doctor_json_reports_shell_like_browser_command_when_status_probe_succeeds(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_URL", "http://custom-browser:9999")
    accounts_dir = tmp_path / "config" / "emploi"
    accounts_dir.mkdir(parents=True)
    (accounts_dir / "accounts.json").write_text(
        json.dumps({"profiles": {"candidature": "emploi-candidature", "officiel": "emploi-officiel"}, "default": "candidature"})
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    def fake_status(self, **kw):
        return _ok({"state": "ready"})

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.status", fake_status)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["managed_browser"]["status"] == "ok"
    assert payload["managed_browser"]["command"] == "http://custom-browser:9999"
    assert payload["managed_browser"]["payload"]["state"] == "ready"


# ---------------------------------------------------------------------------
# Doctor: browser probe error (status raises)
# ---------------------------------------------------------------------------


def test_doctor_json_reports_browser_probe_error(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    accounts_dir = tmp_path / "config" / "emploi"
    accounts_dir.mkdir(parents=True)
    (accounts_dir / "accounts.json").write_text(
        json.dumps({"profiles": {"candidature": "emploi-candidature", "officiel": "emploi-officiel"}, "default": "candidature"})
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    def fake_status(self, **kw):
        raise ManagedBrowserUnavailableError("profile locked")

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.status", fake_status)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "degraded"
    assert payload["managed_browser"]["status"] == "missing"
    assert payload["managed_browser"]["available"] is False
    assert payload["managed_browser"]["probe"] == "failed"
    assert payload["managed_browser"]["can_run_smoke"] is False
    assert "profile locked" in payload["managed_browser"]["error"]
    assert payload["recommended_actions"] == [
        "Installer/configurer Managed Browser ou définir EMPLOI_MANAGED_BROWSER_COMMAND."
    ]


# ---------------------------------------------------------------------------
# Doctor: human-readable text output
# ---------------------------------------------------------------------------


def test_doctor_text_is_human_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    monkeypatch.setattr("emploi.doctor._check_managed_browser", _missing_doctor_browser)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Diagnostic Emploi" in result.stdout
    assert "Base SQLite" in result.stdout
    assert "Managed Browser" in result.stdout
    assert "degraded" in result.stdout or "error" in result.stdout
