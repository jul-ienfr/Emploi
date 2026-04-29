import sqlite3

import pytest
from typer.testing import CliRunner

from emploi.cli import app
from emploi.db import (
    FEATURE_OPTIONS,
    connect,
    get_boolean_option,
    init_db,
    list_options,
    set_boolean_option,
    toggle_boolean_option,
    validate_option_key,
)


runner = CliRunner()


def test_init_db_creates_settings_table_and_default_options_are_permissive(tmp_path):
    db_path = tmp_path / "emploi.sqlite"
    with connect(db_path) as conn:
        init_db(conn)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(settings)")}
        assert {"key", "value", "updated_at"}.issubset(columns)

        options = list_options(conn)

    assert [option["key"] for option in options] == sorted(FEATURE_OPTIONS)
    assert all(option["enabled"] is True for option in options)
    assert all(option["source"] == "default" for option in options)


def test_boolean_option_helpers_validate_set_get_and_toggle(tmp_path):
    with connect(tmp_path / "emploi.sqlite") as conn:
        init_db(conn)
        assert validate_option_key("import.enabled") == "import.enabled"
        assert get_boolean_option(conn, "import.enabled") is True

        disabled = set_boolean_option(conn, "import.enabled", False)
        assert disabled["key"] == "import.enabled"
        assert disabled["enabled"] is False
        assert disabled["value"] == "false"
        assert disabled["source"] == "stored"
        assert get_boolean_option(conn, "import.enabled") is False

        enabled = toggle_boolean_option(conn, "import.enabled")
        assert enabled["enabled"] is True
        assert get_boolean_option(conn, "import.enabled") is True

        with pytest.raises(ValueError, match="Option inconnue"):
            validate_option_key("unknown.enabled")
        with pytest.raises(ValueError, match="Option inconnue"):
            get_boolean_option(conn, "unknown.enabled")

        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("brief.enabled", "maybe"))
        conn.commit()
        with pytest.raises(ValueError, match="Valeur booléenne invalide"):
            get_boolean_option(conn, "brief.enabled")


def test_migration_is_idempotent_for_existing_database(tmp_path):
    db_path = tmp_path / "legacy.sqlite"
    raw = sqlite3.connect(db_path)
    raw.execute("CREATE TABLE offers (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, company TEXT NOT NULL DEFAULT '')")
    raw.execute("CREATE TABLE applications (id INTEGER PRIMARY KEY AUTOINCREMENT, offer_id INTEGER NOT NULL)")
    raw.commit()
    raw.close()

    with connect(db_path) as conn:
        init_db(conn)
        init_db(conn)
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("drafts.enabled", "false"))
        conn.commit()
        assert get_boolean_option(conn, "drafts.enabled") is False


def test_option_cli_list_get_enable_disable_toggle(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))

    list_result = runner.invoke(app, ["option", "list"])
    assert list_result.exit_code == 0
    assert "france_travail.enabled" in list_result.stdout
    assert "oui" in list_result.stdout

    disable = runner.invoke(app, ["option", "disable", "france_travail.enabled"])
    assert disable.exit_code == 0
    assert "désactivée" in disable.stdout

    get_disabled = runner.invoke(app, ["option", "get", "france_travail.enabled"])
    assert get_disabled.exit_code == 0
    assert "false" in get_disabled.stdout.lower()

    toggle = runner.invoke(app, ["option", "toggle", "france_travail.enabled"])
    assert toggle.exit_code == 0
    assert "activée" in toggle.stdout

    enable = runner.invoke(app, ["option", "enable", "france_travail.enabled"])
    assert enable.exit_code == 0
    assert "activée" in enable.stdout


def test_option_cli_rejects_unknown_key(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))

    result = runner.invoke(app, ["option", "disable", "unknown.enabled"])

    assert result.exit_code != 0
    assert "Option inconnue" in result.stdout
