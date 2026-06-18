from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from emploi.browser.errors import ManagedBrowserCommandError, ManagedBrowserUnavailableError
from emploi.browser.models import BrowserCommandResult
from emploi.cli import app


runner = CliRunner()


def _ok(payload: dict) -> BrowserCommandResult:
    return BrowserCommandResult(command="test", site="france-travail", profile="emploi-candidature", payload=payload)


def _err(msg: str) -> BrowserCommandResult:
    return BrowserCommandResult(command="test", site="france-travail", profile="emploi-candidature", payload={"ok": False, "error": msg})


# ---------------------------------------------------------------------------
# Tests for `emploi browser status`
# ---------------------------------------------------------------------------


def test_browser_status_prints_json(monkeypatch):
    """status returns ok JSON with site/profile info."""
    monkeypatch.setattr(
        "emploi.browser.client.ManagedBrowserClient.status",
        lambda self, **kw: _ok({"state": "ready"}),
    )
    result = runner.invoke(app, ["browser", "status"])
    assert result.exit_code == 0
    assert "ready" in result.stdout
    assert "france-travail" in result.stdout


def test_browser_open_accepts_url_and_profile_options(monkeypatch):
    """open delegates URL and custom profile/site to the client."""
    seen: dict = {}

    def fake_open(self, url, **kw):
        seen["url"] = url
        seen["profile"] = kw.get("profile")
        seen["site"] = kw.get("site")
        return _ok({"url": url})

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.open", fake_open)

    result = runner.invoke(
        app,
        ["browser", "open", "https://example.test", "--site", "custom-site", "--profile", "custom-profile"],
    )
    assert result.exit_code == 0
    assert seen["url"] == "https://example.test"
    assert seen["profile"] == "custom-profile"
    assert seen["site"] == "custom-site"
    assert "https://example.test" in result.stdout


def test_browser_snapshot_and_checkpoint_commands(monkeypatch):
    """snapshot and checkpoint delegate correctly."""
    calls: list[str] = []

    def fake_snapshot(self, **kw):
        calls.append("snapshot")
        return _ok({"text": "jobs"})

    def fake_checkpoint(self, name, **kw):
        calls.append(f"checkpoint:{name}")
        return _ok({"id": 1})

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.snapshot", fake_snapshot)
    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.checkpoint", fake_checkpoint)

    snap = runner.invoke(app, ["browser", "snapshot", "--label", "jobs"])
    ckpt = runner.invoke(app, ["browser", "checkpoint", "after-login"])

    assert snap.exit_code == 0
    assert ckpt.exit_code == 0
    assert calls == ["snapshot", "checkpoint:after-login"]


def test_browser_unavailable_shows_clear_error_without_traceback(monkeypatch):
    """Client error is shown cleanly, no traceback."""
    def fake_status(self, **kw):
        raise ManagedBrowserUnavailableError("Server not running")

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.status", fake_status)

    result = runner.invoke(app, ["browser", "status"])
    assert result.exit_code != 0
    assert "Traceback" not in result.stdout
    assert isinstance(result.exception, SystemExit)


def test_browser_status_invalid_timeout_shows_clear_error_without_traceback(monkeypatch):
    """Invalid EMPLOI_MANAGED_BROWSER_TIMEOUT shows clean error."""
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_TIMEOUT", "slow")
    result = runner.invoke(app, ["browser", "status"])
    assert result.exit_code != 0
    assert "EMPLOI_MANAGED_BROWSER_TIMEOUT" in result.stdout
    assert "Traceback" not in result.stdout
    assert isinstance(result.exception, SystemExit)


def test_browser_status_subprocess_timeout_shows_clear_error_without_traceback(monkeypatch):
    """HTTP timeout shows 'timed out' error cleanly."""
    def fake_status(self, **kw):
        raise ManagedBrowserUnavailableError("HTTP request timed out after 3s")

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.status", fake_status)

    result = runner.invoke(app, ["browser", "status"])
    assert result.exit_code != 0
    assert "timed out" in result.stdout
    assert "Traceback" not in result.stdout
    assert isinstance(result.exception, SystemExit)


def test_browser_smoke_json_reports_status_and_snapshot(monkeypatch):
    """smoke --json returns ok with status + snapshot payloads."""
    calls: list[str] = []

    def fake_status(self, **kw):
        calls.append("status")
        return _ok({"state": "ready"})

    def fake_snapshot(self, **kw):
        calls.append("snapshot")
        return _ok({"text": "France Travail"})

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.status", fake_status)
    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.snapshot", fake_snapshot)

    result = runner.invoke(app, ["browser", "smoke", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["site"] == "france-travail"
    assert payload["profile"] == "emploi-candidature"
    assert payload["checks"]["status"]["payload"]["state"] == "ready"
    assert payload["checks"]["snapshot"]["payload"]["text"] == "France Travail"
    assert calls == ["status", "snapshot"]


def test_browser_smoke_dry_run_json_does_not_call_managed_browser(monkeypatch):
    """smoke --dry-run --json never touches the client."""
    def fake_status(self, **kw):
        raise AssertionError("should not be called")

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.status", fake_status)

    result = runner.invoke(app, ["browser", "smoke", "--dry-run", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "dry-run"
    assert payload["would_run"] == ["status", "snapshot"]
    assert payload["submit_application"] is False
