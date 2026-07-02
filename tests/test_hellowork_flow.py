from __future__ import annotations

import json

from emploi.db import (
    add_application,
    add_offer,
    connect,
    get_offer,
    init_db,
    list_applications,
    list_offer_events,
    update_offer_status,
    upsert_draft_application,
)
from emploi.hellowork import _read_draft_message, apply_hellowork, inspect_hellowork_form


class FakeBrowserResult:
    def __init__(self, result: dict[str, object], *, key: str = "result") -> None:
        self.payload = {key: json.dumps(result, ensure_ascii=False), "success": True}


class FakeBrowser:
    def __init__(self, *, confirm: bool = True, dissuasion_required: bool = False, result_key: str = "result") -> None:
        self.opened: list[str] = []
        self.expressions: list[str] = []
        self.confirm = confirm
        self.dissuasion_required = dissuasion_required
        self.result_key = result_key

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
                },
                key=self.result_key,
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
                "dissuasionRequired": self.dissuasion_required,
                "dissuasionSkills": ["FIMO", "FCO", "CARTE DE CONDUCTEUR"] if self.dissuasion_required else [],
            },
            key=self.result_key,
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
    browser = FakeBrowser(dissuasion_required=True)

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


def test_inspect_hellowork_form_accepts_console_eval_value_payload(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html")
    browser = FakeBrowser(result_key="value")

    form = inspect_hellowork_form(
        conn,
        offer_id,
        browser=browser,
        site="france-travail",
        profile="emploi-candidature",
    )

    assert form.required_fields_present is True
    assert form.cv_present is True


def test_read_draft_message_supports_generic_and_driver_headings(tmp_path):
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir()
    (drafts_dir / "1-generic.md").write_text("# Draft\n\n## Message court à adapter\nBonjour générique\n\n## À vérifier\n- item\n", encoding="utf-8")
    (drafts_dir / "2-driver.md").write_text("# Draft\n\n## Message proposé\nBonjour conducteur\n\n## À vérifier\n- item\n", encoding="utf-8")

    assert _read_draft_message(1, drafts_dir=str(drafts_dir)) == "Bonjour générique"
    assert _read_draft_message(2, drafts_dir=str(drafts_dir)) == "Bonjour conducteur"


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


def test_apply_hellowork_submit_does_not_duplicate_existing_draft_application(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html")
    draft_id = upsert_draft_application(conn, offer_id, draft_path="/tmp/draft.md")
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
    assert result.application_id == draft_id
    applications = list_applications(conn)
    assert len(applications) == 1
    assert applications[0]["id"] == draft_id
    assert applications[0]["status"] == "sent"
    assert applications[0]["draft_path"] == "/tmp/draft.md"
    assert get_offer(conn, offer_id)["status"] == "sent"


def test_apply_hellowork_submit_refuses_dissuasion_without_ack(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html")
    browser = FakeBrowser(dissuasion_required=True)

    try:
        apply_hellowork(
            conn,
            offer_id,
            browser=browser,
            submit=True,
            site="france-travail",
            profile="emploi-candidature",
            kanban=False,
        )
    except ValueError as error:
        assert "Dissuasion HelloWork détectée" in str(error)
        assert "--ack-dissuasion" in str(error)
    else:
        raise AssertionError("Expected dissuasion HelloWork submit to be refused")

    assert list_applications(conn) == []
    assert not any("postcandidateinformationfromstepframeview" in expr for expr in browser.expressions)


def test_apply_hellowork_submit_allows_dissuasion_with_ack(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html")
    browser = FakeBrowser(dissuasion_required=True)

    result = apply_hellowork(
        conn,
        offer_id,
        browser=browser,
        submit=True,
        site="france-travail",
        profile="emploi-candidature",
        kanban=False,
        ack_dissuasion=True,
    )

    assert result.submitted is True
    submit_expressions = [expr for expr in browser.expressions if "postcandidateinformationfromstepframeview" in expr]
    assert submit_expressions
    assert "fetch(dissuasion.action" not in submit_expressions[-1]


def test_apply_hellowork_submit_refuses_when_already_sent(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html")
    add_application(conn, offer_id, status="sent", notes="Déjà envoyée")
    browser = FakeBrowser()

    try:
        apply_hellowork(
            conn,
            offer_id,
            browser=browser,
            submit=True,
            site="france-travail",
            profile="emploi-candidature",
            kanban=False,
        )
    except ValueError as error:
        assert "déjà envoyée" in str(error)
    else:
        raise AssertionError("Expected duplicate HelloWork submit to be refused")

    assert len(list_applications(conn)) == 1
    assert not any("postcandidateinformationfromstepframeview" in expr for expr in browser.expressions)
    assert get_offer(conn, offer_id)["status"] == "applied"


def test_apply_hellowork_submit_refuses_when_offer_status_already_sent_without_application(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html")
    update_offer_status(conn, offer_id, "sent")
    browser = FakeBrowser()

    try:
        apply_hellowork(
            conn,
            offer_id,
            browser=browser,
            submit=True,
            site="france-travail",
            profile="emploi-candidature",
            kanban=False,
        )
    except ValueError as error:
        assert "déjà envoyée" in str(error)
    else:
        raise AssertionError("Expected duplicate HelloWork submit to be refused")

    assert browser.expressions == []


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
    assert get_offer(conn, offer_id)["status"] == "sent"
    events = list_offer_events(conn, offer_id)
    assert events[0]["event_type"] == "application_submitted"
    payload = json.loads(events[0]["payload_json"])
    assert payload["confirmation_detected"] is True
    assert payload["source"] == "hellowork"
    assert "FunnelId" not in events[0]["payload_json"]
    assert any("postcandidateinformationfromstepframeview" in expr for expr in browser.expressions)
