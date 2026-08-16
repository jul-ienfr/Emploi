from __future__ import annotations

import json

import pytest

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
    def __init__(
        self,
        *,
        confirm: bool = True,
        dissuasion_required: bool = False,
        cgu_consent_required: bool = True,
        cgu_consent_checked: bool = True,
        otp_confirm: bool = True,
        smart_apply_confirm_after: int = 1,
        smart_apply_question: bool = False,
        fill_remaining: list[str] | None = None,
        dissuasion_confirm: bool = True,
        main_submit_step: str = "otp",
        result_key: str = "result",
    ) -> None:
        self.opened: list[str] = []
        self.expressions: list[str] = []
        self.confirm = confirm
        self.dissuasion_required = dissuasion_required
        self.otp_confirm = otp_confirm
        self.cgu_consent_required = cgu_consent_required
        self.cgu_consent_checked = cgu_consent_checked
        self.smart_apply_confirm_after = smart_apply_confirm_after
        self.smart_apply_attempts = 0
        self.smart_apply_question = smart_apply_question
        self.fill_remaining = fill_remaining if fill_remaining is not None else []
        self.dissuasion_confirm = dissuasion_confirm
        self.main_submit_step = main_submit_step
        self.result_key = result_key

    def lifecycle_open(self, url: str, *, site: str, profile: str):
        self.opened.append(url)
        return {"success": True}

    def console_eval(self, expression: str, *, site: str, profile: str):
        self.expressions.append(expression)
        if "Formulaire de dissuasion HelloWork introuvable" in expression:
            return FakeBrowserResult(
                {
                    "submitStatus": 200,
                    "confirmed": self.dissuasion_confirm,
                    "textPreview": "Votre candidature est envoyée, vous allez être redirigé·e"
                    if self.dissuasion_confirm
                    else "Est-ce bien votre email ? Pour valider votre candidature, saisissez le code envoyé à j@mail.fr",
                },
                key=self.result_key,
            )
        if "SMART_APPLY_FILL" in expression:
            return FakeBrowserResult(
                {"filled": [], "remaining": list(self.fill_remaining)},
                key=self.result_key,
            )
        if "postsav2formstepframeview" in expression:
            self.smart_apply_attempts += 1
            confirmed = self.smart_apply_attempts >= self.smart_apply_confirm_after
            return FakeBrowserResult(
                {
                    "submitStatus": 200,
                    "confirmed": confirmed,
                    "textPreview": "Votre candidature est envoyée"
                    if confirmed
                    else "Temporis Interim a besoin d'une information complémentaire pour enregistrer votre candidature",
                    "questionInputs": 1 if self.smart_apply_question else 0,
                    "questionLabels": ["Avez-vous le permis EC ?"] if self.smart_apply_question else [],
                },
                key=self.result_key,
            )
        if "postotpformstepframeview" in expression:
            return FakeBrowserResult(
                {
                    "submitStatus": 200,
                    "confirmed": self.otp_confirm,
                    "textPreview": "Votre candidature est envoyée, vous allez être redirigé·e"
                    if self.otp_confirm
                    else "Code invalide",
                },
                key=self.result_key,
            )
        if "postcandidateinformationfromstepframeview" in expression:
            return FakeBrowserResult(
                {
                    "submitStatus": 200,
                    "confirmed": self.confirm,
                    "textPreview": "Votre candidature est envoyée, vous allez être redirigé·e"
                    if self.confirm
                    else (
                        "Temporis Interim a besoin d'une information complémentaire pour enregistrer votre candidature"
                        if self.main_submit_step == "smart-apply"
                        else (
                            "Ce job demande des compétences précises : FIMO FCO"
                            if self.main_submit_step == "dissuasion"
                            else "Est-ce bien votre email ? Pour valider votre candidature, saisissez le code envoyé à j@mail.fr"
                        )
                    ),
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
                "cguConsentRequired": self.cgu_consent_required,
                "cguConsentChecked": self.cgu_consent_checked,
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
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
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
    (drafts_dir / "1-generic.md").write_text(
        "# Draft\n\n## Message court à adapter\nBonjour générique\n\n## À vérifier\n- item\n", encoding="utf-8"
    )
    (drafts_dir / "2-driver.md").write_text(
        "# Draft\n\n## Message proposé\nBonjour conducteur\n\n## À vérifier\n- item\n", encoding="utf-8"
    )

    assert _read_draft_message(1, drafts_dir=str(drafts_dir)) == "Bonjour générique"
    assert _read_draft_message(2, drafts_dir=str(drafts_dir)) == "Bonjour conducteur"


def test_apply_hellowork_dry_run_records_preview_without_submission_or_application(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
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
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
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
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
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
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
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
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
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
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
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
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
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


# ---------------------------------------------------------------------------
# CGU consent (HasAcceptedCGU) — nouveau champ requis depuis août 2026
# ---------------------------------------------------------------------------


def test_inspect_hellowork_form_detects_cgu_consent_field(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
    browser = FakeBrowser(cgu_consent_required=True, cgu_consent_checked=False)

    form = inspect_hellowork_form(
        conn,
        offer_id,
        browser=browser,
        site="france-travail",
        profile="emploi-candidature",
    )

    assert form.cgu_consent_required is True
    assert form.cgu_consent_checked is False
    assert form.cgu_consent_ok is False


def test_apply_hellowork_submit_refuses_unchecked_cgu_consent(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
    browser = FakeBrowser(cgu_consent_required=True, cgu_consent_checked=False)

    with pytest.raises(ValueError) as excinfo:
        apply_hellowork(
            conn,
            offer_id,
            browser=browser,
            submit=True,
            site="france-travail",
            profile="emploi-candidature",
            kanban=False,
        )

    assert "HasAcceptedCGU" in str(excinfo.value)
    assert "--ack-cgu" in str(excinfo.value)
    # aucune expression de soumission ne doit avoir été envoyée au navigateur
    assert not any("postcandidateinformationfromstepframeview" in expr for expr in browser.expressions)
    assert list_applications(conn) == []


def test_apply_hellowork_dry_run_allowed_with_unchecked_cgu_consent(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
    browser = FakeBrowser(cgu_consent_required=True, cgu_consent_checked=False)

    result = apply_hellowork(
        conn,
        offer_id,
        browser=browser,
        site="france-travail",
        profile="emploi-candidature",
        kanban=False,
    )

    assert result.dry_run is True
    events = list_offer_events(conn, offer_id)
    payload = json.loads(events[0]["payload_json"])
    assert payload["cgu_consent_required"] is True
    assert payload["cgu_consent_checked"] is False


def test_apply_hellowork_submit_allowed_with_ack_cgu(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
    browser = FakeBrowser(cgu_consent_required=True, cgu_consent_checked=False)

    result = apply_hellowork(
        conn,
        offer_id,
        browser=browser,
        submit=True,
        site="france-travail",
        profile="emploi-candidature",
        kanban=False,
        ack_cgu=True,
    )

    assert result.submitted is True
    # l'expression de soumission doit cocher la case CGU (consentement explicite)
    submit_expr = [e for e in browser.expressions if "postcandidateinformationfromstepframeview" in e]
    assert submit_expr
    assert "ackCgu = true" in submit_expr[0]
    assert "HasAcceptedCGU" in submit_expr[0]


# ---------------------------------------------------------------------------
# Étape de vérification email (OTP) — flux validé en live (2026-08-16)
# ---------------------------------------------------------------------------


def test_is_otp_step_detects_email_verification_page():
    from emploi.hellowork import _is_otp_step

    assert _is_otp_step("Est-ce bien votre email ? Pour valider votre candidature, saisissez le code envoyé")
    assert _is_otp_step("Demander un nouveau code dans 00:30")
    assert not _is_otp_step("Votre candidature est envoyée")


def test_apply_hellowork_submit_otp_step_requires_code(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
    browser = FakeBrowser(confirm=False)  # le POST principal mène à l'étape OTP

    with pytest.raises(ValueError) as excinfo:
        apply_hellowork(
            conn,
            offer_id,
            browser=browser,
            submit=True,
            site="france-travail",
            profile="emploi-candidature",
            kanban=False,
        )

    assert "--otp-code" in str(excinfo.value)
    assert "Vérification email" in str(excinfo.value)
    assert list_applications(conn) == []  # rien d'enregistré tant que non validé


def test_apply_hellowork_submit_finalizes_with_otp_code(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
    browser = FakeBrowser(confirm=False, otp_confirm=True)

    result = apply_hellowork(
        conn,
        offer_id,
        browser=browser,
        submit=True,
        site="france-travail",
        profile="emploi-candidature",
        kanban=False,
        otp_code="123456",
    )

    assert result.submitted is True
    otp_expr = [e for e in browser.expressions if "postotpformstepframeview" in e]
    assert otp_expr
    assert '"123456"' in otp_expr[0]
    apps = list_applications(conn)
    assert len(apps) == 1
    assert apps[0]["status"] == "sent"


# ---------------------------------------------------------------------------
# Étape Smart Apply (SAv2) — information complémentaire du recruteur
# ---------------------------------------------------------------------------


def test_is_smart_apply_step_detection():
    from emploi.hellowork import _is_smart_apply_step

    assert _is_smart_apply_step(
        "Temporis Interim a besoin d'une information complémentaire pour enregistrer votre candidature"
    )
    assert not _is_smart_apply_step("Est-ce bien votre email ?")


def test_apply_hellowork_submit_smart_apply_finalizes_after_loop(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
    # le POST principal mène à l'étape smart-apply ; la 2e soumission confirme
    browser = FakeBrowser(confirm=False, main_submit_step="smart-apply", smart_apply_confirm_after=2)

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
    smart_exprs = [e for e in browser.expressions if "postsav2formstepframeview" in e]
    assert len(smart_exprs) == 2
    apps = list_applications(conn)
    assert len(apps) == 1
    assert apps[0]["status"] == "sent"


def test_apply_hellowork_submit_smart_apply_question_requires_manual_answer(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
    browser = FakeBrowser(
        confirm=False, main_submit_step="smart-apply", smart_apply_confirm_after=99, smart_apply_question=True
    )

    with pytest.raises(ValueError) as excinfo:
        apply_hellowork(
            conn,
            offer_id,
            browser=browser,
            submit=True,
            site="france-travail",
            profile="emploi-candidature",
            kanban=False,
        )

    assert "Question du recruteur" in str(excinfo.value)
    assert "permis EC" in str(excinfo.value)
    assert list_applications(conn) == []


def test_apply_hellowork_submit_smart_apply_stuck_reports_cleanly(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
    browser = FakeBrowser(confirm=False, main_submit_step="smart-apply", smart_apply_confirm_after=99)

    with pytest.raises(ValueError) as excinfo:
        apply_hellowork(
            conn,
            offer_id,
            browser=browser,
            submit=True,
            site="france-travail",
            profile="emploi-candidature",
            kanban=False,
        )

    assert "Smart Apply" in str(excinfo.value)
    assert list_applications(conn) == []


def test_apply_hellowork_submit_smart_apply_missing_identity_reports(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
    browser = FakeBrowser(
        confirm=False, main_submit_step="smart-apply", smart_apply_confirm_after=99, fill_remaining=["Votre téléphone"]
    )

    with pytest.raises(ValueError) as excinfo:
        apply_hellowork(
            conn,
            offer_id,
            browser=browser,
            submit=True,
            site="france-travail",
            profile="emploi-candidature",
            kanban=False,
        )

    assert "à renseigner" in str(excinfo.value)
    assert "téléphone" in str(excinfo.value)
    assert "identity set --phone" in str(excinfo.value)
    assert list_applications(conn) == []


def test_is_dissuasion_step_detection():
    from emploi.hellowork import _is_dissuasion_step

    assert _is_dissuasion_step("Ce job demande des compétences précises : FIMO FCO")
    assert _is_dissuasion_step("Envoyer ma candidature")
    assert not _is_dissuasion_step("Est-ce bien votre email ?")


def test_apply_hellowork_submit_dissuasion_requires_ack(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
    browser = FakeBrowser(
        confirm=False,
        main_submit_step="dissuasion",
    )

    with pytest.raises(ValueError) as excinfo:
        apply_hellowork(
            conn,
            offer_id,
            browser=browser,
            submit=True,
            site="france-travail",
            profile="emploi-candidature",
            kanban=False,
        )

    assert "--ack-dissuasion" in str(excinfo.value)
    assert list_applications(conn) == []


def test_apply_hellowork_submit_dissuasion_ack_finalizes(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
    browser = FakeBrowser(
        confirm=False,
        main_submit_step="dissuasion",
        dissuasion_confirm=True,
    )

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
    apps = list_applications(conn)
    assert len(apps) == 1
    assert apps[0]["status"] == "sent"
