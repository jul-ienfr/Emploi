from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from emploi.browser.models import BrowserCommandResult
from emploi.cli import app
from emploi.db import add_offer, connect, init_db, list_applications, list_offer_events

runner = CliRunner()


def _ok(payload: dict) -> BrowserCommandResult:
    return BrowserCommandResult(command="test", site="france-travail", profile="emploi-candidature", payload=payload)


@pytest.fixture(autouse=True)
def clean_managed_browser_timeout(monkeypatch):
    monkeypatch.delenv("EMPLOI_MANAGED_BROWSER_TIMEOUT", raising=False)


# ---------------------------------------------------------------------------
# France Travail search via Managed Browser
# ---------------------------------------------------------------------------


def test_ft_search_imports_offers_via_managed_browser(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    calls: list[str] = []

    def fake_lifecycle_open(self, url, *, site="france-travail", profile="emploi-candidature"):
        calls.append("lifecycle_open")
        return _ok({"url": url})

    def fake_snapshot(self, *, label=None, site="france-travail", profile="emploi-candidature"):
        calls.append("snapshot")
        return _ok({
            "cards": [
                {
                    "title": "Technicien support",
                    "company": "Acme",
                    "location": "Annecy",
                    "url": "https://candidat.francetravail.fr/offres/recherche/detail/ABC123",
                    "description": "Support informatique",
                }
            ],
            "text": "Technicien support Acme Annecy",
        })

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.lifecycle_open", fake_lifecycle_open)
    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.snapshot", fake_snapshot)

    result = runner.invoke(app, ["ft", "search", "support", "--location", "Annecy"])

    assert result.exit_code == 0, result.stdout
    assert "1 offre" in result.stdout
    assert "Technicien support" in result.stdout
    assert calls == ["lifecycle_open", "snapshot"]


def test_ft_refresh_updates_offer(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    conn = connect(db_path)
    init_db(conn)
    add_offer(
        conn,
        title="Support",
        external_source="france-travail",
        browser_url="https://candidat.francetravail.fr/offres/recherche/detail/ABC123",
    )

    def fake_open(self, url, **kw):
        return _ok({"url": url})

    def fake_snapshot(self, **kw):
        return _ok({"text": "Cette offre n'est plus disponible"})

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.lifecycle_open", fake_open)
    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.open", fake_open)
    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.snapshot", fake_snapshot)

    result = runner.invoke(app, ["ft", "refresh", "1"])

    assert result.exit_code == 0
    assert "inactive" in result.stdout


def test_ft_apply_check_draft_and_open(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    drafts = tmp_path / "drafts"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    conn = connect(db_path)
    init_db(conn)
    add_offer(
        conn,
        title="Support",
        company="Acme",
        external_source="france-travail",
        browser_url="https://candidat.francetravail.fr/offres/recherche/detail/ABC123",
    )
    calls: list[str] = []

    def fake_lifecycle_open(self, url, **kw):
        calls.append("lifecycle_open")
        return _ok({"url": url})

    def fake_snapshot(self, **kw):
        return _ok({"text": "Candidater maintenant"})

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.lifecycle_open", fake_lifecycle_open)
    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.snapshot", fake_snapshot)

    check = runner.invoke(app, ["ft", "apply", "1", "--check"])
    draft = runner.invoke(app, ["ft", "apply", "1", "--draft", "--drafts-dir", str(drafts)])
    open_result = runner.invoke(app, ["ft", "apply", "1", "--open"])

    assert check.exit_code == 0
    assert "candidature possible" in check.stdout.lower()
    assert draft.exit_code == 0
    assert "Brouillon" in draft.stdout
    assert any(drafts.iterdir())
    assert open_result.exit_code == 0
    assert "ouverte" in open_result.stdout
    assert "lifecycle_open" in calls


def test_ft_apply_submit_is_rejected_without_managed_browser_or_records(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    conn = connect(db_path)
    init_db(conn)
    offer_id = add_offer(
        conn,
        title="Support",
        external_source="france-travail",
        browser_url="https://candidat.francetravail.fr/offres/recherche/detail/ABC123",
    )

    result = runner.invoke(app, ["ft", "apply", str(offer_id), "--submit"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    with connect(db_path) as verify_conn:
        assert list_offer_events(verify_conn, offer_id) == []
        assert list_applications(verify_conn) == []


def test_ft_apply_partner_opens_selected_external_partner(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    conn = connect(db_path)
    init_db(conn)
    add_offer(
        conn,
        title="Support",
        external_source="france-travail",
        browser_url="https://candidat.francetravail.fr/offres/recherche/detail/ABC123",
    )
    opened_urls: list[str] = []

    def fake_lifecycle_open(self, url, **kw):
        opened_urls.append(url)
        return _ok({"url": url})

    # Snapshot text must contain "site de HelloWork" and an HTML <a> href for _detect_partner_handoff
    PARTNER_HTML = (
        '<div>Candidater maintenant</div>'
        '<div>site de HelloWork</div>'
        '<a href="https://www.hellowork.com/fr-fr/emplois/123.html">HelloWork</a>'
    )

    def fake_snapshot(self, **kw):
        return _ok({"text": PARTNER_HTML})

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.lifecycle_open", fake_lifecycle_open)
    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.snapshot", fake_snapshot)

    result = runner.invoke(app, ["ft", "apply", "1", "--partner", "hellowork"])

    assert result.exit_code == 0
    assert "Partenaire HelloWork ouvert" in result.stdout
    assert opened_urls[-1] == "https://www.hellowork.com/fr-fr/emplois/123.html"


def test_ft_apply_partner_missing_partner_returns_clean_cli_error_without_external_open(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    conn = connect(db_path)
    init_db(conn)
    add_offer(
        conn,
        title="Support",
        external_source="france-travail",
        browser_url="https://candidat.francetravail.fr/offres/recherche/detail/ABC123",
    )
    opened_urls: list[str] = []

    def fake_lifecycle_open(self, url, **kw):
        opened_urls.append(url)
        return _ok({"url": url})

    def fake_snapshot(self, **kw):
        return _ok({"text": "Pas de partenaire ici"})

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.lifecycle_open", fake_lifecycle_open)
    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.snapshot", fake_snapshot)

    result = runner.invoke(app, ["ft", "apply", "1", "--partner", "hellowork"])

    assert result.exit_code != 0
    assert "Error: Partenaire introuvable" in result.output
    assert "Invalid value" not in result.output
    assert "Traceback" not in result.output
    assert opened_urls == ["https://candidat.francetravail.fr/offres/recherche/detail/ABC123"]
    with connect(db_path) as verify_conn:
        assert all(event["event_type"] != "partner_opened" for event in list_offer_events(verify_conn, 1))


def test_ft_smoke_dry_run_json_does_not_touch_database_or_submit(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    result = runner.invoke(app, ["ft", "smoke", "support", "--location", "Annecy", "--dry-run", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "dry-run"
    assert payload["query"] == "support"
    assert payload["location"] == "Annecy"
    assert payload["submit_application"] is False
    assert payload["database_write"] is False
    assert "candidat.francetravail.fr/offres/recherche" in payload["search_url"]
    assert not db_path.exists()


def test_ft_smoke_json_opens_search_and_snapshots_without_importing(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    def fake_lifecycle_open(self, url, **kw):
        return _ok({"url": url})

    def fake_snapshot(self, **kw):
        return _ok({"cards": [{"title": "Support"}]})

    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.lifecycle_open", fake_lifecycle_open)
    monkeypatch.setattr("emploi.browser.client.ManagedBrowserClient.snapshot", fake_snapshot)

    result = runner.invoke(app, ["ft", "smoke", "support", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["offer_count"] == 1
    assert payload["database_write"] is False
    assert payload["submit_application"] is False
    assert not db_path.exists()
