from __future__ import annotations

import importlib
import json

import pytest
from typer.testing import CliRunner

import emploi.config as emploi_config
from emploi.cli import app
from emploi.db import add_application, add_offer, connect, init_db, list_offer_events


class _FakeHTTPResponse:
    """Minimal urllib response that returns pre-built JSON."""

    def __init__(self, payload: dict) -> None:
        self._data = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._data


def _browser_response(result: dict) -> _FakeHTTPResponse:
    """Build a Managed Browser HTTP response wrapping *result*."""
    return _FakeHTTPResponse({"success": True, "result": json.dumps(result, ensure_ascii=False)})


def _ok_response() -> _FakeHTTPResponse:
    return _FakeHTTPResponse({"success": True})


def _make_form_result(**overrides) -> dict:
    base = {
        "url": "https://www.hellowork.com/fr-fr/emplois/123.html#postuler",
        "offerExternalId": "123",
        "initialStatus": 200,
        "initialLength": 4000,
        "formPresent": True,
        "funnelIdPresent": True,
        "firstnamePresent": True,
        "lastnamePresent": True,
        "emailPresent": True,
        "motivationPresent": True,
        "submitButtonPresent": True,
        "cvStatus": 200,
        "cvLength": 1000,
        "cvPresent": True,
        "dissuasionRequired": False,
        "dissuasionSkills": [],
    }
    base.update(overrides)
    return base


# Shared mutable state for the HTTP mock — tests configure before invoking CLI.
_form_result: dict = _make_form_result()
_submit_result: dict = {"submitStatus": 200, "confirmed": True, "textPreview": "Envoyé"}
_extra_urlopen = None  # optional secondary mock for nextcloud calls


def _universal_urlopen(request, timeout=30):
    """Universal urlopen mock that dispatches based on the request URL."""
    getattr(request, "full_url", "") if hasattr(request, "full_url") else str(request)

    # If there's an extra mock configured (for nextcloud calls), use it
    if _extra_urlopen is not None:
        result = _extra_urlopen(request, timeout)
        if result is not None:
            return result

    # Managed Browser — console_eval
    if hasattr(request, "data") and request.data:
        try:
            body = json.loads(request.data)
            expression = body.get("params", {}).get("expression", "")
            if "postcandidateinformationfromstepframeview" in expression:
                return _browser_response(_submit_result)
        except (json.JSONDecodeError, AttributeError):
            pass

    # Managed Browser — open/snapshot/etc
    return _browser_response(_form_result)


@pytest.fixture(autouse=True)
def _patch_everything(monkeypatch):
    """Patch all external I/O to prevent real network/subprocess calls."""
    global _form_result, _submit_result, _extra_urlopen
    _form_result = _make_form_result()
    _submit_result = {"submitStatus": 200, "confirmed": True, "textPreview": "Envoyé"}
    _extra_urlopen = None

    monkeypatch.delenv("EMPLOI_MANAGED_BROWSER_TIMEOUT", raising=False)
    monkeypatch.setattr("emploi.browser.client.urlopen", _universal_urlopen)
    monkeypatch.setattr("emploi.nextcloud_deck.urllib.request.urlopen", _universal_urlopen)
    monkeypatch.setattr("emploi.nextcloud_files.urllib.request.urlopen", _universal_urlopen)
    monkeypatch.setattr("emploi.nextcloud_tasks.urllib.request.urlopen", _universal_urlopen)
    monkeypatch.setattr("emploi.utils._pass_show", lambda entry: "fake-secret")
    monkeypatch.setattr("emploi.retry.time.sleep", lambda _: None)


def reload_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return importlib.reload(emploi_config)


def test_hellowork_apply_dry_run_cli_does_not_submit(monkeypatch, tmp_path):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Slash", url="https://www.hellowork.com/fr-fr/emplois/123.html")

    result = CliRunner().invoke(app, ["hellowork", "apply", str(offer_id), "--no-kanban"])

    assert result.exit_code == 0, result.output
    assert "Dry-run" in result.stdout
    assert "aucune candidature envoyée" in result.stdout


def test_hellowork_apply_dry_run_continues_when_kanban_stack_missing(monkeypatch, tmp_path):
    config = reload_config(monkeypatch, tmp_path)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    config.set_kanban_endpoint(
        "recherche-emploi",
        base_url="https://nextcloud.test",
        board_id=21,
        username_pass="nextcloud/username",
        password_pass="nextcloud/password",
        stacks={"autre-stack": 49},
        make_default=True,
    )

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Slash", url="https://www.hellowork.com/fr-fr/emplois/123.html")

    result = CliRunner().invoke(app, ["hellowork", "apply", str(offer_id)])

    assert result.exit_code == 0, result.output
    assert "Dry-run" in result.stdout
    with connect(db_path) as conn:
        events = list_offer_events(conn, offer_id)
    assert [event["event_type"] for event in events] == ["nextcloud_deck_preview_failed", "hellowork_apply_dry_run"]


def test_hellowork_apply_submit_without_yes_fails_before_browser_or_records(monkeypatch, tmp_path):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Slash", url="https://www.hellowork.com/fr-fr/emplois/123.html")

    def reject_all(request, timeout=30):
        raise AssertionError("Managed Browser ne doit pas être appelé sans --yes")

    monkeypatch.setattr("emploi.browser.client.urlopen", reject_all)

    result = CliRunner().invoke(app, ["hellowork", "apply", str(offer_id), "--submit"])

    assert result.exit_code == 1
    assert "--submit HelloWork exige --yes" in result.stdout
    assert "Traceback" not in result.output
    with connect(db_path) as conn:
        assert list_offer_events(conn, offer_id) == []


def test_hellowork_apply_incomplete_form_returns_clean_error_without_submit(monkeypatch, tmp_path):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Slash", url="https://www.hellowork.com/fr-fr/emplois/123.html")

    global _form_result
    _form_result = _make_form_result(lastnamePresent=False)

    result = CliRunner().invoke(app, ["hellowork", "apply", str(offer_id), "--submit", "--yes"])

    assert result.exit_code == 1
    assert "Formulaire HelloWork incomplet: Lastname" in result.stdout
    assert "Traceback" not in result.output
    assert "Invalid value" not in result.output
    with connect(db_path) as conn:
        assert list_offer_events(conn, offer_id) == []


def test_hellowork_apply_unconfirmed_submit_returns_clean_error_without_local_records(monkeypatch, tmp_path):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Slash", url="https://www.hellowork.com/fr-fr/emplois/123.html")

    global _submit_result
    _submit_result = {"submitStatus": 200, "confirmed": False, "textPreview": "Erreur temporaire"}

    result = CliRunner().invoke(app, ["hellowork", "apply", str(offer_id), "--submit", "--yes"])

    assert result.exit_code == 1
    assert "Confirmation HelloWork non détectée après soumission" in result.stdout
    assert "Traceback" not in result.output
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

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Slash", url="https://www.hellowork.com/fr-fr/emplois/123.html")

    global _submit_result
    _submit_result = {"submitStatus": 200, "confirmed": False, "textPreview": "Erreur temporaire"}

    result = CliRunner().invoke(app, ["hellowork", "apply", str(offer_id), "--submit", "--yes"])

    assert result.exit_code == 1
    assert "Confirmation HelloWork non détectée" in result.stdout
    with connect(db_path) as conn:
        assert list_offer_events(conn, offer_id) == []


def test_hellowork_apply_submit_cli_refuses_already_sent_without_second_post(monkeypatch, tmp_path):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Slash", url="https://www.hellowork.com/fr-fr/emplois/123.html")
        add_application(conn, offer_id, status="sent", notes="Déjà envoyée")

    result = CliRunner().invoke(app, ["hellowork", "apply", str(offer_id), "--submit", "--yes", "--no-kanban"])

    assert result.exit_code == 1
    assert "déjà envoyée" in result.stdout
    assert "Traceback" not in result.output
    with connect(db_path) as conn:
        assert list_offer_events(conn, offer_id) == []


def test_hellowork_apply_submit_succeeds_when_default_kanban_is_missing(monkeypatch, tmp_path):
    reload_config(monkeypatch, tmp_path)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Slash", url="https://www.hellowork.com/fr-fr/emplois/123.html")

    result = CliRunner().invoke(app, ["hellowork", "apply", str(offer_id), "--submit", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Candidature locale" in result.stdout
    with connect(db_path) as conn:
        events = list_offer_events(conn, offer_id)
        applications = conn.execute("SELECT * FROM applications").fetchall()
    assert len(applications) == 1
    assert applications[0]["status"] == "sent"
    assert [event["event_type"] for event in events] == ["nextcloud_deck_card_failed", "application_submitted"]


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

    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Slash", url="https://www.hellowork.com/fr-fr/emplois/123.html")

    global _form_result
    _form_result = _make_form_result(dissuasionRequired=True, dissuasionSkills=["FIMO"])

    deck_requests: list[object] = []

    def nextcloud_mock(request, timeout=30):
        url = getattr(request, "full_url", "") if hasattr(request, "full_url") else str(request)
        if "nextcloud.test" in str(url):
            deck_requests.append(request)
            return _FakeHTTPResponse({"id": 777})
        return None  # fall through to universal mock

    global _extra_urlopen
    _extra_urlopen = nextcloud_mock

    result = CliRunner().invoke(app, ["hellowork", "apply", str(offer_id), "--submit", "--yes", "--ack-dissuasion"])

    assert result.exit_code == 0, result.output
    assert "Candidature locale" in result.stdout
    with connect(db_path) as conn:
        events = list_offer_events(conn, offer_id)
    assert events[0]["event_type"] == "nextcloud_deck_card"
    assert events[1]["event_type"] == "application_submitted"
    assert deck_requests
