import json

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
    assert payload["managed_browser"]["command"] == "definitely-missing-managed-browser"
    assert payload["recommended_actions"]


def test_doctor_text_is_human_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_COMMAND", "definitely-missing-managed-browser")

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Diagnostic Emploi" in result.stdout
    assert "Base SQLite" in result.stdout
    assert "Managed Browser" in result.stdout
    assert "degraded" in result.stdout
