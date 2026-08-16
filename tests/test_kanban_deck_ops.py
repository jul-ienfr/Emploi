"""Tests des opérations Deck avancées : stacks, card add, move."""

from __future__ import annotations

from typer.testing import CliRunner

import emploi.config as emploi_config
from emploi.cli import app

runner = CliRunner()


class FakeDeckClient:
    """Fake client Deck : aucune requête réseau, pistage des appels."""

    def __init__(self, *args, **kwargs) -> None:
        self.created: list[dict[str, object]] = []
        self.moved: list[dict[str, object]] = []
        self.stacks = [
            {"id": 1, "title": "À postuler", "cards": [{"id": 101, "title": "Chauffeur PL", "order": 1}]},
            {"id": 2, "title": "Candidatures envoyées", "cards": []},
        ]

    def create_card(self, *, stack_id: int, title: str, description: str, order: int = 999) -> dict[str, object]:
        payload = {"id": 999, "stack_id": stack_id, "title": title, "description": description}
        self.created.append(payload)
        return payload

    def list_stacks(self) -> list[dict[str, object]]:
        return self.stacks

    def move_card(self, *, card_id: int, stack_id: int, title: str, order: int = 999) -> dict[str, object]:
        payload = {"id": card_id, "stack_id": stack_id, "title": title, "order": order}
        self.moved.append(payload)
        return payload

    def find_card(self, card_id: int) -> dict[str, object] | None:
        for stack in self.stacks:
            for card in stack.get("cards", []):
                if int(card.get("id") or 0) == int(card_id):
                    return card
        return None


def _setup_endpoint(tmp_path, monkeypatch) -> None:
    """Enregistre un endpoint kanban de test isolé dans XDG_CONFIG_HOME."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    emploi_config.set_kanban_endpoint(
        "recherche-emploi",
        base_url="https://nextcloud.test",
        board_id=17,
        username_pass="nextcloud/username",
        password_pass="nextcloud/password",
        stacks={"a-postuler": 1, "envoyees": 2},
        make_default=True,
    )


def test_kanban_stacks_lists_board_stacks_live(tmp_path, monkeypatch):
    _setup_endpoint(tmp_path, monkeypatch)
    monkeypatch.setattr("emploi.cli.kanban.NextcloudDeckClient", FakeDeckClient)

    result = runner.invoke(app, ["kanban", "stacks", "--json"])

    assert result.exit_code == 0, result.stdout
    assert '"title": "À postuler"' in result.stdout
    assert '"id": 1' in result.stdout


def test_kanban_card_add_dry_run_does_not_call_api(tmp_path, monkeypatch):
    _setup_endpoint(tmp_path, monkeypatch)
    fake = FakeDeckClient()
    monkeypatch.setattr("emploi.cli.kanban.NextcloudDeckClient", lambda *a, **k: fake)

    result = runner.invoke(app, ["kanban", "card", "add", "Rappel relance", "--stack", "envoyees", "--dry-run"])

    assert result.exit_code == 0, result.stdout
    assert "Dry-run" in result.stdout
    assert fake.created == []


def test_kanban_card_add_creates_manual_card(tmp_path, monkeypatch):
    _setup_endpoint(tmp_path, monkeypatch)
    fake = FakeDeckClient()
    monkeypatch.setattr("emploi.cli.kanban.NextcloudDeckClient", lambda *a, **k: fake)

    result = runner.invoke(
        app, ["kanban", "card", "add", "Rappel relance", "--stack", "envoyees", "--description", "Relancer le 15"]
    )

    assert result.exit_code == 0, result.stdout
    assert len(fake.created) == 1
    assert fake.created[0]["title"] == "Rappel relance"
    assert fake.created[0]["stack_id"] == 2


def test_kanban_move_dry_run_does_not_call_api(tmp_path, monkeypatch):
    _setup_endpoint(tmp_path, monkeypatch)
    fake = FakeDeckClient()
    monkeypatch.setattr("emploi.cli.kanban.NextcloudDeckClient", lambda *a, **k: fake)

    result = runner.invoke(app, ["kanban", "move", "101", "--stack", "envoyees", "--dry-run"])

    assert result.exit_code == 0, result.stdout
    assert "Dry-run" in result.stdout
    assert fake.moved == []


def test_kanban_move_moves_card_to_target_stack(tmp_path, monkeypatch):
    _setup_endpoint(tmp_path, monkeypatch)
    fake = FakeDeckClient()
    monkeypatch.setattr("emploi.cli.kanban.NextcloudDeckClient", lambda *a, **k: fake)

    result = runner.invoke(app, ["kanban", "move", "101", "--stack", "envoyees"])

    assert result.exit_code == 0, result.stdout
    assert len(fake.moved) == 1
    moved = fake.moved[0]
    assert moved["id"] == 101
    assert moved["stack_id"] == 2
    assert moved["title"] == "Chauffeur PL"
    assert "Carte déplacée" in result.stdout


def test_kanban_move_unknown_card_raises_clean_error(tmp_path, monkeypatch):
    _setup_endpoint(tmp_path, monkeypatch)
    fake = FakeDeckClient()
    monkeypatch.setattr("emploi.cli.kanban.NextcloudDeckClient", lambda *a, **k: fake)

    result = runner.invoke(app, ["kanban", "move", "4242", "--stack", "envoyees"])

    assert result.exit_code != 0
    assert "introuvable" in result.stderr
    assert fake.moved == []


def test_kanban_stacks_requires_configured_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    result = runner.invoke(app, ["kanban", "stacks", "--json"])

    assert result.exit_code == 1
    assert "missing" in result.stdout
