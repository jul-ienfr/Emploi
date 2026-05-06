from __future__ import annotations

import importlib

from typer.testing import CliRunner

import emploi.config as emploi_config
from emploi.cli import app
from emploi.db import add_offer, connect, init_db, list_offer_events


def reload_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return importlib.reload(emploi_config)


def configure_endpoints(config):
    config.set_nextcloud_files_endpoint("emploi", base_url="https://nextcloud.test", remote_root="/Emploi", make_default=True)
    config.set_kanban_endpoint(
        "chauffeur-pl",
        base_url="https://nextcloud.test",
        board_id=21,
        username_pass="nextcloud/username",
        password_pass="nextcloud/password",
        make_default=True,
        stacks={"a-postuler": 49},
    )


def test_application_pipeline_dry_run_previews_files_and_deck_without_events(monkeypatch, tmp_path):
    config = reload_config(monkeypatch, tmp_path)
    configure_endpoints(config)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Dupont")

    result = CliRunner().invoke(
        app,
        ["application", "pipeline", str(offer_id), "--stack", "a-postuler", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "Pipeline candidature préparé" in result.stdout
    assert "Export Nextcloud" in result.stdout
    assert "Carte Deck" in result.stdout
    assert "Dry-run" in result.stdout
    assert "nextcloud/password" not in result.stdout
    with connect(db_path) as conn:
        assert list_offer_events(conn, offer_id) == []


def test_application_pipeline_live_reuses_existing_card_event_without_new_card(monkeypatch, tmp_path):
    config = reload_config(monkeypatch, tmp_path)
    configure_endpoints(config)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Dupont")

    result = CliRunner().invoke(
        app,
        ["application", "pipeline", str(offer_id), "--stack", "a-postuler", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "Carte Deck" in result.stdout
    assert "Deck endpoint : chauffeur-pl" in result.stdout
