from __future__ import annotations

import importlib

from typer.testing import CliRunner

import emploi.config as emploi_config
from emploi.cli import app

runner = CliRunner()


def reload_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return importlib.reload(emploi_config)


def test_nextcloud_files_config_round_trip(monkeypatch, tmp_path):
    config = reload_config(monkeypatch, tmp_path)

    endpoint = config.set_nextcloud_files_endpoint(
        "emploi",
        base_url="http://nextcloud.local/",
        remote_root="/Emploi/",
        username_pass="nextcloud/username",
        password_pass="nextcloud/password",
        make_default=True,
    )

    assert endpoint["name"] == "emploi"
    assert endpoint["base_url"] == "http://nextcloud.local"
    assert endpoint["remote_root"] == "/Emploi"
    assert endpoint["username_pass"] == "nextcloud/username"
    assert endpoint["password_pass"] == "nextcloud/password"
    assert endpoint["webdav_root_url"] == "http://nextcloud.local/remote.php/dav/files/{username}/Emploi"
    assert endpoint["default"] == "✓"
    assert config.get_default_nextcloud_files_endpoint()["name"] == "emploi"


def test_nextcloud_files_cli_set_show_list(monkeypatch, tmp_path):
    reload_config(monkeypatch, tmp_path)

    result = runner.invoke(
        app,
        [
            "nextcloud-files",
            "set",
            "emploi",
            "--base-url",
            "http://nextcloud.local/",
            "--remote-root",
            "/Emploi/",
            "--username-pass",
            "nextcloud/username",
            "--password-pass",
            "nextcloud/password",
            "--default",
        ],
    )

    assert result.exit_code == 0
    assert "Nextcloud Files enregistré : emploi" in result.stdout
    assert "secrets non affichés" in result.stdout
    assert "v8wr" not in result.stdout

    show = runner.invoke(app, ["nextcloud-files", "show", "emploi", "--json"])
    assert show.exit_code == 0
    assert '"name": "emploi"' in show.stdout
    assert '"remote_root": "/Emploi"' in show.stdout
    assert '"webdav_root_url": "http://nextcloud.local/remote.php/dav/files/{username}/Emploi"' in show.stdout

    listing = runner.invoke(app, ["nextcloud-files", "list", "--json"])
    assert listing.exit_code == 0
    assert '"endpoints"' in listing.stdout
    assert '"emploi"' in listing.stdout


def test_nextcloud_files_requires_base_url(monkeypatch, tmp_path):
    config = reload_config(monkeypatch, tmp_path)

    try:
        config.set_nextcloud_files_endpoint("bad", base_url="", remote_root="/Emploi")
    except ValueError as error:
        assert "URL Nextcloud obligatoire" in str(error)
    else:
        raise AssertionError("Expected ValueError")
