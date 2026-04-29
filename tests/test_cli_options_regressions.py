import json
import subprocess

from typer.testing import CliRunner

from emploi.cli import app
from emploi.db import add_offer, connect, init_db, set_boolean_option


runner = CliRunner()


def _disable_option(db_path, key: str) -> None:
    with connect(db_path) as conn:
        init_db(conn)
        set_boolean_option(conn, key, False)


def test_import_offers_refuses_when_import_option_disabled(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    _disable_option(db_path, "import.enabled")
    source_path = tmp_path / "offers.json"
    source_path.write_text(json.dumps([{"title": "Support"}]), encoding="utf-8")

    result = runner.invoke(app, ["import", "offers", str(source_path), "--source", "local", "--json"])

    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "disabled"
    assert payload["option"] == "import.enabled"


def test_brief_json_refuses_parseably_when_brief_option_disabled(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    _disable_option(db_path, "brief.enabled")

    result = runner.invoke(app, ["brief", "--json"])

    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "disabled"
    assert payload["option"] == "brief.enabled"


def test_application_draft_refuses_when_drafts_option_disabled(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        add_offer(conn, title="Support")
        set_boolean_option(conn, "drafts.enabled", False)

    result = runner.invoke(app, ["application", "draft", "1"])

    assert result.exit_code != 0
    assert "drafts.enabled" in result.stdout
    assert "désactivée" in result.stdout


def test_ft_search_refuses_without_browser_when_france_travail_disabled(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    _disable_option(db_path, "france_travail.enabled")

    def fake_run(args, **kwargs):  # pragma: no cover - should never be called
        raise AssertionError(args)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.invoke(app, ["ft", "search", "support"])

    assert result.exit_code != 0
    assert "france_travail.enabled" in result.stdout
    assert "désactivée" in result.stdout


def test_ft_smoke_json_disabled_remains_parseable_and_does_not_call_browser(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    _disable_option(db_path, "france_travail.enabled")

    def fake_run(args, **kwargs):  # pragma: no cover - should never be called
        raise AssertionError(args)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.invoke(app, ["ft", "smoke", "support", "--json"])

    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "disabled"
    assert payload["option"] == "france_travail.enabled"


def test_search_profile_run_refuses_when_france_travail_disabled(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    runner.invoke(app, ["search-profile", "add", "local", "--query", "support"])
    _disable_option(db_path, "france_travail.enabled")

    result = runner.invoke(app, ["search-profile", "run", "--all"])

    assert result.exit_code != 0
    assert "france_travail.enabled" in result.stdout


def test_offer_score_refuses_when_scoring_option_disabled(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    runner.invoke(app, ["offer", "add", "--title", "Support"])
    _disable_option(db_path, "scoring.enabled")

    result = runner.invoke(app, ["offer", "score", "1"])

    assert result.exit_code != 0
    assert "scoring.enabled" in result.stdout
