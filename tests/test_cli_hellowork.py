from __future__ import annotations

import importlib
import json
import subprocess

from typer.testing import CliRunner

import emploi.config as emploi_config
from emploi.cli import app
from emploi.db import add_offer, connect, init_db, list_offer_events


def reload_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return importlib.reload(emploi_config)


def _fake_completed(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        ["managed-browser"],
        0,
        stdout=json.dumps({"success": True, "result": json.dumps(payload, ensure_ascii=False)}),
        stderr="",
    )


def test_hellowork_apply_dry_run_cli_does_not_submit(monkeypatch, tmp_path):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    calls: list[list[str]] = []

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Slash", url="https://www.hellowork.com/fr-fr/emplois/123.html")

    def fake_run(args, capture_output=True, text=True, check=False):
        calls.append(args)
        if args[1:3] == ["lifecycle", "open"]:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"success": True}), stderr="")
        expression = args[args.index("--expression") + 1]
        assert "postcandidateinformationfromstepframeview" not in expression
        return _fake_completed(
            {
                "initialStatus": 200,
                "funnelIdPresent": True,
                "firstnamePresent": True,
                "lastnamePresent": True,
                "emailPresent": True,
                "motivationPresent": True,
                "submitButtonPresent": True,
                "cvPresent": True,
                "dissuasionRequired": False,
                "dissuasionSkills": [],
            }
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = CliRunner().invoke(app, ["hellowork", "apply", str(offer_id), "--no-kanban"])

    assert result.exit_code == 0, result.output
    assert "Dry-run" in result.stdout
    assert "aucune candidature envoyée" in result.stdout
    assert any(call[1:3] == ["lifecycle", "open"] for call in calls)
    assert not any("postcandidateinformationfromstepframeview" in " ".join(call) for call in calls)


def test_hellowork_apply_incomplete_form_returns_clean_error_without_submit(monkeypatch, tmp_path):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    calls: list[list[str]] = []

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Slash", url="https://www.hellowork.com/fr-fr/emplois/123.html")

    def fake_run(args, capture_output=True, text=True, check=False):
        calls.append(args)
        if args[1:3] == ["lifecycle", "open"]:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"success": True}), stderr="")
        return _fake_completed(
            {
                "initialStatus": 200,
                "funnelIdPresent": True,
                "firstnamePresent": True,
                "lastnamePresent": False,
                "emailPresent": True,
                "motivationPresent": True,
                "submitButtonPresent": True,
                "cvPresent": True,
                "dissuasionRequired": False,
                "dissuasionSkills": [],
            }
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = CliRunner().invoke(app, ["hellowork", "apply", str(offer_id), "--submit"])

    assert result.exit_code == 1
    assert "Error: Formulaire HelloWork incomplet: Lastname" in result.stdout
    assert "Traceback" not in result.output
    assert "Invalid value" not in result.output
    assert not any("postcandidateinformationfromstepframeview" in " ".join(call) for call in calls)
    with connect(db_path) as conn:
        assert list_offer_events(conn, offer_id) == []


def test_hellowork_apply_unconfirmed_submit_returns_clean_error_without_local_records(monkeypatch, tmp_path):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    calls: list[list[str]] = []

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Slash", url="https://www.hellowork.com/fr-fr/emplois/123.html")

    def fake_run(args, capture_output=True, text=True, check=False):
        calls.append(args)
        if args[1:3] == ["lifecycle", "open"]:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"success": True}), stderr="")
        expression = args[args.index("--expression") + 1]
        if "postcandidateinformationfromstepframeview" in expression:
            return _fake_completed({"submitStatus": 200, "confirmed": False, "textPreview": "Erreur temporaire HelloWork"})
        return _fake_completed(
            {
                "initialStatus": 200,
                "funnelIdPresent": True,
                "firstnamePresent": True,
                "lastnamePresent": True,
                "emailPresent": True,
                "motivationPresent": True,
                "submitButtonPresent": True,
                "cvPresent": True,
                "dissuasionRequired": False,
                "dissuasionSkills": [],
            }
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = CliRunner().invoke(app, ["hellowork", "apply", str(offer_id), "--submit"])

    assert result.exit_code == 1
    assert "Error: Confirmation HelloWork non détectée après soumission" in result.stdout
    assert "Traceback" not in result.output
    assert "Invalid value" not in result.output
    assert any("postcandidateinformationfromstepframeview" in " ".join(call) for call in calls)
    with connect(db_path) as conn:
        assert list_offer_events(conn, offer_id) == []


def test_hellowork_apply_unconfirmed_submit_does_not_create_deck_card(monkeypatch, tmp_path):
    config = reload_config(monkeypatch, tmp_path)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    config.set_kanban_endpoint(
        "recherche-emploi",
        base_url="https://nextcloud.test",
        board_id=21,
        username_pass="nextcloud/username",
        password_pass="nextcloud/password",
        stacks={"candidature-envoyee": 49},
        make_default=True,
    )
    deck_requests: list[object] = []

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Slash", url="https://www.hellowork.com/fr-fr/emplois/123.html")

    def fake_run(args, capture_output=True, text=True, check=False):
        if args[0] == "pass":
            return subprocess.CompletedProcess(args, 0, stdout="secret\n", stderr="")
        if args[1:3] == ["lifecycle", "open"]:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"success": True}), stderr="")
        expression = args[args.index("--expression") + 1]
        if "postcandidateinformationfromstepframeview" in expression:
            return _fake_completed({"submitStatus": 200, "confirmed": False, "textPreview": "Erreur temporaire HelloWork"})
        return _fake_completed(
            {
                "initialStatus": 200,
                "funnelIdPresent": True,
                "firstnamePresent": True,
                "lastnamePresent": True,
                "emailPresent": True,
                "motivationPresent": True,
                "submitButtonPresent": True,
                "cvPresent": True,
                "dissuasionRequired": False,
                "dissuasionSkills": [],
            }
        )

    def fake_urlopen(request, timeout=30):
        deck_requests.append(request)
        raise AssertionError("Deck ne doit pas être appelé sans confirmation HelloWork")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = CliRunner().invoke(app, ["hellowork", "apply", str(offer_id), "--submit"])

    assert result.exit_code == 1
    assert "Confirmation HelloWork non détectée" in result.stdout
    assert deck_requests == []
    with connect(db_path) as conn:
        assert list_offer_events(conn, offer_id) == []


def test_hellowork_apply_submit_cli_records_sent_and_uses_configured_deck_stack(monkeypatch, tmp_path):
    config = reload_config(monkeypatch, tmp_path)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    config.set_kanban_endpoint(
        "recherche-emploi",
        base_url="https://nextcloud.test",
        board_id=21,
        username_pass="nextcloud/username",
        password_pass="nextcloud/password",
        stacks={"candidature-envoyee": 49},
        make_default=True,
    )
    calls: list[list[str]] = []
    deck_requests: list[object] = []

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Slash", url="https://www.hellowork.com/fr-fr/emplois/123.html")

    def fake_run(args, capture_output=True, text=True, check=False):
        calls.append(args)
        if args[0] == "pass":
            return subprocess.CompletedProcess(args, 0, stdout="secret\n", stderr="")
        if args[1:3] == ["lifecycle", "open"]:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"success": True}), stderr="")
        expression = args[args.index("--expression") + 1]
        if "postcandidateinformationfromstepframeview" in expression:
            return _fake_completed({"submitStatus": 200, "confirmed": True, "textPreview": "Votre candidature est envoyée"})
        return _fake_completed(
            {
                "initialStatus": 200,
                "funnelIdPresent": True,
                "firstnamePresent": True,
                "lastnamePresent": True,
                "emailPresent": True,
                "motivationPresent": True,
                "submitButtonPresent": True,
                "cvPresent": True,
                "dissuasionRequired": True,
                "dissuasionSkills": ["FIMO"],
            }
        )

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"id": 777}).encode()

    def fake_urlopen(request, timeout=30):
        deck_requests.append(request)
        return FakeResponse()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = CliRunner().invoke(app, ["hellowork", "apply", str(offer_id), "--submit"])

    assert result.exit_code == 0, result.output
    assert "Candidature locale" in result.stdout
    assert "Carte Deck créée/enregistrée : stack 49" in result.stdout
    assert "nextcloud/password" not in result.stdout
    with connect(db_path) as conn:
        events = list_offer_events(conn, offer_id)
    assert events[0]["event_type"] == "nextcloud_deck_card"
    assert events[1]["event_type"] == "application_submitted"
    assert deck_requests
