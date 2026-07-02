from typer.testing import CliRunner

from emploi import config
from emploi.cli import app

runner = CliRunner()


def test_set_nextcloud_deck_kanban_endpoint_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    saved = config.set_kanban_endpoint(
        "recherche-emploi",
        base_url="https://nextcloud.test",
        board_id=17,
        board_url="https://nextcloud.test/apps/deck/board/17",
        username_pass="nextcloud/username",
        password_pass="nextcloud/password",
        title="Recherche Emploi - CDI Conducteur SPL",
        stacks={"a-postuler": 50, "urgent": 49},
    )

    assert saved["name"] == "recherche-emploi"
    assert saved["base_url"] == "https://nextcloud.test"
    assert saved["board_id"] == 17
    assert saved["board_url"] == "https://nextcloud.test/apps/deck/board/17"
    assert saved["api_board_url"] == "https://nextcloud.test/index.php/apps/deck/api/v1.0/boards/17"
    assert saved["api_stacks_url"] == "https://nextcloud.test/index.php/apps/deck/api/v1.0/boards/17/stacks"
    assert saved["username_pass"] == "nextcloud/username"
    assert saved["password_pass"] == "nextcloud/password"
    assert saved["stacks"] == {"a-postuler": 50, "urgent": 49}
    assert config.resolve_kanban_stack(saved, "a-postuler") == 50
    assert config.resolve_kanban_stack(saved, "50") == 50

    loaded = config.get_kanban_endpoint("recherche-emploi")
    assert loaded == saved
    assert config.get_default_kanban_endpoint() == saved


def test_kanban_cli_set_and_show_json(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(
        app,
        [
            "kanban",
            "set",
            "recherche-emploi",
            "--base-url",
            "https://nextcloud.test",
            "--board-id",
            "17",
            "--board-url",
            "https://nextcloud.test/apps/deck/board/17",
            "--username-pass",
            "nextcloud/username",
            "--password-pass",
            "nextcloud/password",
            "--title",
            "Recherche Emploi - CDI Conducteur SPL",
            "--stack",
            "a-postuler=50",
            "--stack",
            "urgent=49",
            "--default",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "Kanban enregistré" in result.stdout

    show = runner.invoke(app, ["kanban", "show", "recherche-emploi", "--json"])
    assert show.exit_code == 0, show.stdout
    assert '"board_id": 17' in show.stdout
    assert '"api_stacks_url": "https://nextcloud.test/index.php/apps/deck/api/v1.0/boards/17/stacks"' in show.stdout
    assert '"a-postuler": 50' in show.stdout


def test_kanban_cli_requires_explicit_default_when_ambiguous(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    missing = runner.invoke(app, ["kanban", "show"])
    assert missing.exit_code != 0
    assert "Aucun endpoint kanban" in missing.stdout
