from __future__ import annotations

import json

from emploi.db import add_offer, connect, init_db, list_applications, list_offer_events
from emploi.hellowork import apply_hellowork, inspect_hellowork_form


class FakeBrowserResult:
    def __init__(self, result: dict[str, object]) -> None:
        self.payload = {"result": json.dumps(result, ensure_ascii=False), "success": True}


class FakeBrowser:
    def __init__(self, *, confirm: bool = True) -> None:
        self.opened: list[str] = []
        self.expressions: list[str] = []
        self.confirm = confirm

    def lifecycle_open(self, url: str, *, site: str, profile: str):
        self.opened.append(url)
        return {"success": True}

    def console_eval(self, expression: str, *, site: str, profile: str):
        self.expressions.append(expression)
        if "postcandidateinformationfromstepframeview" in expression:
            return FakeBrowserResult(
                {
                    "submitStatus": 200,
                    "confirmed": self.confirm,
                    "textPreview": "Votre candidature est envoyée, vous allez être redirigé·e",
                }
            )
        return FakeBrowserResult(
            {
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
                "dissuasionRequired": True,
                "dissuasionSkills": ["FIMO", "FCO", "CARTE DE CONDUCTEUR"],
            }
        )


def test_inspect_hellowork_form_resolves_url_from_offer_and_detects_required_fields(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn,
        title="Chauffeur PL",
        company="Slash Intérim",
        url="https://www.hellowork.com/fr-fr/emplois/123.html",
    )
    browser = FakeBrowser()

    form = inspect_hellowork_form(
        conn,
        offer_id,
        browser=browser,
        site="france-travail",
        profile="emploi-candidature",
    )

    assert form.required_fields_present is True
    assert form.cv_present is True
    assert form.dissuasion_required is True
    assert "FIMO" in form.dissuasion_skills
    assert browser.opened == ["https://www.hellowork.com/fr-fr/emplois/123.html"]


def test_apply_hellowork_dry_run_records_preview_without_submission_or_application(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html")
    browser = FakeBrowser()

    result = apply_hellowork(
        conn,
        offer_id,
        browser=browser,
        site="france-travail",
        profile="emploi-candidature",
        kanban=False,
    )

    assert result.dry_run is True
    assert result.submitted is False
    assert list_applications(conn) == []
    events = list_offer_events(conn, offer_id)
    assert events[0]["event_type"] == "hellowork_apply_dry_run"
    payload = json.loads(events[0]["payload_json"])
    assert payload["submit_application"] is False
    assert "FunnelId" not in events[0]["payload_json"]
    assert not any("postcandidateinformationfromstepframeview" in expr for expr in browser.expressions)


def test_apply_hellowork_submit_records_application_and_deck_card(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html")
    browser = FakeBrowser()

    result = apply_hellowork(
        conn,
        offer_id,
        browser=browser,
        submit=True,
        site="france-travail",
        profile="emploi-candidature",
        kanban=False,
    )

    assert result.submitted is True
    assert result.status == "sent"
    applications = list_applications(conn)
    assert len(applications) == 1
    assert applications[0]["status"] == "sent"
    events = list_offer_events(conn, offer_id)
    assert events[0]["event_type"] == "application_submitted"
    payload = json.loads(events[0]["payload_json"])
    assert payload["confirmation_detected"] is True
    assert payload["source"] == "hellowork"
    assert "FunnelId" not in events[0]["payload_json"]
    assert any("postcandidateinformationfromstepframeview" in expr for expr in browser.expressions)
