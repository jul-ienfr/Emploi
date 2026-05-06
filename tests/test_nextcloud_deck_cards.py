from __future__ import annotations

import importlib
import json
from pathlib import Path

from typer.testing import CliRunner

import emploi.config as emploi_config
from emploi.cli import app
from emploi.db import add_offer, connect, init_db, list_offer_events
from emploi.nextcloud_deck import create_offer_card


class FakeDeckClient:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    def create_card(self, *, stack_id: int, title: str, description: str, order: int = 999) -> dict[str, object]:
        payload = {"id": 1234, "stack_id": stack_id, "title": title, "description": description, "order": order}
        self.created.append(payload)
        return payload


def reload_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return importlib.reload(emploi_config)


def test_create_offer_card_dry_run_builds_deck_payload_without_network(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn,
        title="Chauffeur PL régional",
        company="Transports Dupont",
        location="Bogève",
        url="https://example.test/offre/123",
        description="Livraison régionale en porteur.",
        contract_type="CDI",
        external_source="france-travail",
        external_id="123ABC",
    )
    endpoint = {"name": "chauffeur-pl", "board_id": 21, "base_url": "https://nextcloud.test"}
    client = FakeDeckClient()

    result = create_offer_card(
        conn,
        offer_id,
        endpoint=endpoint,
        stack_id=49,
        client=client,
        nextcloud_folder_url="https://nextcloud.test/apps/files/files?dir=/Emploi/Candidatures/0001-test",
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.card_id is None
    assert result.stack_id == 49
    assert result.title == "Chauffeur PL régional — Transports Dupont"
    assert "Livraison régionale" in result.description
    assert "Dossier Nextcloud" in result.description
    assert client.created == []
    assert list_offer_events(conn, offer_id) == []


def test_create_offer_card_posts_to_deck_client_and_records_event(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(conn, title="Chauffeur PL", company="Dupont")
    endpoint = {"name": "chauffeur-pl", "board_id": 21, "base_url": "https://nextcloud.test"}
    client = FakeDeckClient()

    result = create_offer_card(conn, offer_id, endpoint=endpoint, stack_id=49, client=client)

    assert result.card_id == 1234
    assert result.reused_existing is False
    assert client.created[0]["stack_id"] == 49
    assert client.created[0]["title"] == "Chauffeur PL — Dupont"
    events = list_offer_events(conn, offer_id)
    assert events[0]["event_type"] == "nextcloud_deck_card"
    payload = json.loads(events[0]["payload_json"])
    assert payload["card_id"] == 1234
    assert payload["endpoint"] == "chauffeur-pl"
    assert payload["stack_id"] == 49


def test_create_offer_card_reuses_existing_event_unless_forced(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(conn, title="Chauffeur PL", company="Dupont")
    endpoint = {"name": "chauffeur-pl", "board_id": 21, "base_url": "https://nextcloud.test"}
    first_client = FakeDeckClient()
    second_client = FakeDeckClient()

    first = create_offer_card(conn, offer_id, endpoint=endpoint, stack_id=49, client=first_client)
    second = create_offer_card(conn, offer_id, endpoint=endpoint, stack_id=49, client=second_client)
    forced = create_offer_card(conn, offer_id, endpoint=endpoint, stack_id=49, client=second_client, force=True)

    assert first.card_id == 1234
    assert second.card_id == 1234
    assert second.reused_existing is True
    assert second_client.created == [{"id": 1234, "stack_id": 49, "title": "Chauffeur PL — Dupont", "description": second.description, "order": 999}]
    assert forced.reused_existing is False
    assert len(list_offer_events(conn, offer_id)) == 2


def test_kanban_card_add_offer_cli_dry_run_uses_configured_endpoint(monkeypatch, tmp_path):
    config = reload_config(monkeypatch, tmp_path)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    config.set_kanban_endpoint(
        "chauffeur-pl",
        base_url="https://nextcloud.test",
        board_id=21,
        username_pass="nextcloud/username",
        password_pass="nextcloud/password",
        make_default=True,
    )
    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Dupont")

    result = CliRunner().invoke(
        app,
        ["kanban", "card", "add-offer", str(offer_id), "--stack-id", "49", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "Carte Deck préparée" in result.stdout
    assert "Chauffeur PL — Dupont" in result.stdout
    assert "stack 49" in result.stdout
    assert "nextcloud/password" not in result.stdout
