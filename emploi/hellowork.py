from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from emploi import config as emploi_config
from emploi.applications import DEFAULT_DRAFTS_DIR
from emploi.db import add_application, add_offer_event, get_offer, list_offer_events, update_offer_status
from emploi.logging import get_logger
from emploi.nextcloud_deck import DeckCardResult, create_offer_card

logger = get_logger("hellowork")

HELLOWORK_INITIAL_FORM_PATH = "/fr-fr/offres/getinitialformframeview"
HELLOWORK_FINAL_POST_PATH = "/fr-fr/offres/postcandidateinformationfromstepframeview"
HELLOWORK_DISSUASION_POST_PATH = "/fr-fr/offres/postcertificationdissuasionformstepframeview"
HELLOWORK_CV_UPLOADER_PATH = "/fr-fr/GetUploaderCvFrameView"


class HelloWorkBrowser(Protocol):
    def lifecycle_open(self, url: str, *, site: str, profile: str): ...

    def console_eval(self, expression: str, *, site: str, profile: str): ...


@dataclass(frozen=True)
class HelloWorkFormState:
    url: str
    offer_external_id: str
    funnel_id: str
    firstname_present: bool
    lastname_present: bool
    email_present: bool
    motivation_present: bool
    cv_present: bool
    submit_button_present: bool
    dissuasion_required: bool = False
    dissuasion_skills: tuple[str, ...] = ()
    cgu_consent_required: bool = False
    cgu_consent_checked: bool = False

    @property
    def required_fields_present(self) -> bool:
        return bool(
            self.funnel_id
            and self.firstname_present
            and self.lastname_present
            and self.email_present
            and self.cv_present
            and self.submit_button_present
        )

    @property
    def cgu_consent_ok(self) -> bool:
        """True si la case CGU est absente (pas requise) ou cochée."""
        return not self.cgu_consent_required or self.cgu_consent_checked


@dataclass(frozen=True)
class HelloWorkApplyResult:
    offer_id: int
    url: str
    dry_run: bool
    submitted: bool
    status: str
    message: str
    form: HelloWorkFormState
    application_id: int | None = None
    deck_card: DeckCardResult | None = None


def _extract_hellowork_offer_id(url: str, fallback: str = "") -> str:
    match = re.search(r"/emplois/(\d+)\.html", url or "")
    if match:
        return match.group(1)
    match = re.search(r"(?:offerId|id)=(\d+)", url or "")
    if match:
        return match.group(1)
    return fallback


def _first_hellowork_url_from_events(conn, offer_id: int) -> str:
    for event in list_offer_events(conn, offer_id):
        payload = event["payload_json"] or ""
        if "hellowork.com" not in payload.lower():
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            for key in ("url", "partner_url", "external_url"):
                value = data.get(key)
                if isinstance(value, str):
                    match = re.search(r"https://www\.hellowork\.com/[^\"'\s<>]+", value)
                    if match:
                        return match.group(0).replace("\\/", "/")
            partners = data.get("partner_handoff") or data.get("partners")
            if isinstance(partners, list):
                for partner in partners:
                    if isinstance(partner, dict):
                        p_url = partner.get("url")
                        if isinstance(p_url, str):
                            match = re.search(r"https://www\.hellowork\.com/[^\"'\s<>]+", p_url)
                            if match:
                                return match.group(0).replace("\\/", "/")
        match = re.search(r"https://www\.hellowork\.com/[^\"'\s<>]+", payload)
        if match:
            return match.group(0).replace("\\/", "/")
    return ""


def resolve_hellowork_url(conn, offer_id: int, explicit_url: str = "") -> str:
    if explicit_url.strip():
        return explicit_url.strip()
    offer = get_offer(conn, offer_id)
    if offer is None:
        raise ValueError(f"Offre introuvable: {offer_id}")
    for key in ("apply_url", "browser_url", "url"):
        value = str(offer[key] or "") if key in offer.keys() else ""
        if "hellowork.com" in value.lower():
            return value.strip()
    found = _first_hellowork_url_from_events(conn, offer_id)
    if found:
        return found
    raise ValueError(f"URL HelloWork introuvable pour l'offre #{offer_id}")


def _read_draft_message(offer_id: int, *, drafts_dir: str | None = None) -> str:
    directory = Path(drafts_dir).expanduser() if drafts_dir else DEFAULT_DRAFTS_DIR
    candidates = sorted(directory.glob(f"{offer_id}-*.md"))
    if not candidates:
        return ""
    text = candidates[0].read_text(encoding="utf-8")
    match = re.search(r"## (?:Message proposé|Message court à adapter)\n(.+?)(?:\n(?=## )|\Z)", text, re.S)
    return match.group(1).strip() if match else ""


def _browser_result_payload(result: Any) -> dict[str, Any]:
    payload = getattr(result, "payload", result)
    if isinstance(payload, dict):
        return payload
    return {}


def _browser_result_value(result: Any) -> Any:
    """Extract the evaluated value from a ``console_eval`` response payload.

    The server nests the real value under ``result.result`` (the outer
    ``result`` object mirrors the replay step); older clients read it from
    ``value`` directly.  Accept both, plus the legacy ``raw`` wrapper and
    string-encoded JSON.  (Même logique que ``flows._eval_value``.)
    """
    payload = _browser_result_payload(result)
    if not isinstance(payload, dict):
        return payload
    if "value" in payload:
        return payload["value"]
    raw = payload.get("raw")
    nested = payload.get("result")
    for wrapper in (raw, nested):
        if isinstance(wrapper, dict):
            for key in ("result", "value"):
                if key in wrapper:
                    return wrapper[key]
    if isinstance(nested, str):
        return nested  # legacy: JSON string direct
    if isinstance(raw, str):
        return raw
    return payload


def _json_from_browser_result(result: Any) -> dict[str, Any]:
    value = _browser_result_value(result)
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("Réponse HelloWork illisible depuis le navigateur") from error
        if isinstance(decoded, dict):
            return decoded
    raise ValueError("Réponse HelloWork invalide depuis le navigateur")


def _js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _inspect_expression(
    offer_external_id: str,
    motivation: str,
    *,
    identity: dict[str, str] | None = None,
    cv_b64: str = "",
    cv_name: str = "",
) -> str:
    identity = identity or {}
    return f"""
(async () => {{
  const offerId = {_js_string(offer_external_id)};
  const motivation = {_js_string(motivation)};
  const identity = {json.dumps({k: identity.get(k, "") for k in ("firstname", "lastname", "email")}, ensure_ascii=False)};
  const cvB64 = {_js_string(cv_b64)};
  const cvName = {_js_string(cv_name)};
  const out = {{url: location.href, offerExternalId: offerId}};
  const initialUrl = `/fr-fr/offres/getinitialformframeview?offerId=${{encodeURIComponent(offerId)}}&ts=${{Date.now()}}`;
  const initialResponse = await fetch(initialUrl, {{credentials: 'include', headers: {{'Turbo-Frame': 'funnel-frame'}}}});
  out.initialStatus = initialResponse.status;
  const initialHtml = await initialResponse.text();
  out.initialLength = initialHtml.length;
  const parser = new DOMParser();
  let doc = parser.parseFromString(initialHtml, 'text/html');
  let form = doc.querySelector('#offer-detail-main-step-form') || doc.querySelector('form');
  if (form) {{
    const target = document.querySelector('#funnel-frame') || document.body;
    target.innerHTML = initialHtml;
    form = document.querySelector('#offer-detail-main-step-form') || document.querySelector('form');
  }}
  const getValue = (name) => form ? (form.querySelector(`[name="${{name}}"]`)?.value || '') : '';
  if (form) {{
    const setField = (name, value) => {{
      if (!value) return;
      const el = form.querySelector(`[name="${{name}}"]`);
      if (el) el.value = value;
    }};
    setField('Firstname', identity.firstname);
    setField('Lastname', identity.lastname);
    setField('LastName', identity.lastname);
    setField('Email', identity.email);
  }}
  out.formPresent = !!form;
  out.funnelIdPresent = !!getValue('FunnelId');
  out.firstnamePresent = !!getValue('Firstname');
  out.lastnamePresent = !!(getValue('Lastname') || getValue('LastName'));
  out.emailPresent = !!getValue('Email');
  out.motivationPresent = !!(form && form.querySelector('[name="MotivationLetter"]'));
  const cguField = form ? form.querySelector('[name="HasAcceptedCGU"]') : null;
  out.cguConsentRequired = !!cguField;
  out.cguConsentChecked = !!(cguField && cguField.checked);
  out.submitButtonPresent = !!((form && (form.querySelector('button[data-cy="submitButton"],button[type="submit"]') || Array.from(form.querySelectorAll('button')).find(button => /postuler/i.test(button.innerText || button.value || '')))) || /Postuler/i.test(initialHtml));
  if (form && motivation && form.querySelector('[name="MotivationLetter"]')) {{
    form.querySelector('[name="MotivationLetter"]').value = motivation;
  }}
  const cvUrl = `/fr-fr/GetUploaderCvFrameView?formId=offer-detail-main-step-form&isRequired=true&turboFrameId=funnel-resume-uploader-frame&ts=${{Date.now()}}`;
  const cvResponse = await fetch(cvUrl, {{credentials: 'include', headers: {{'Turbo-Frame': 'funnel-resume-uploader-frame'}}}});
  out.cvStatus = cvResponse.status;
  const cvHtml = await cvResponse.text();
  out.cvLength = cvHtml.length;
  const cvDoc = parser.parseFromString(cvHtml, 'text/html');
  const cvText = cvHtml.replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim();
  const uploadedCvSelector = [
    '[data-cy*="uploaded" i]',
    '[data-testid*="uploaded" i]',
    '[class*="uploaded" i]',
    '[class*="selected" i]',
    '[href*="cv" i]',
    '[value*=".pdf" i]'
  ].join(',');
  const uploadedCvElements = Array.from(cvDoc.querySelectorAll(uploadedCvSelector));
  out.cvPresent = uploadedCvElements.some((element) => /\\.pdf|cv|curriculum/i.test(
    element.textContent + ' ' + element.getAttribute('href') + ' ' + element.getAttribute('value')
  ));
  if (cvB64 && !out.cvPresent) {{
    try {{
      const bin = atob(cvB64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const file = new File([bytes], cvName || 'cv.pdf', {{type: 'application/pdf'}});
      const fd = new FormData();
      fd.append('upload', file);
      fd.append('uploaderApp', '41');
      fd.append('uploaderType', 'RO');
      const upRes = await fetch('/fr-fr/uploadcv?formId=offer-detail-main-step-form&isRequired=True', {{method: 'POST', credentials: 'include', body: fd, headers: {{'Turbo-Frame': 'funnel-resume-uploader-frame', 'X-Requested-With': 'XMLHttpRequest'}}}});
      const upText = await upRes.text();
      out.cvUploadStatus = upRes.status;
      let hash = '';
      try {{ const j = JSON.parse(upText); hash = j.jweHashResume || j.resumeHash || j.hash || ''; }} catch (e) {{ /* html */ }}
      if (!hash) {{
        const upDoc = new DOMParser().parseFromString(upText, 'text/html');
        const hashInput = upDoc.querySelector('[name="JweHashResume"]');
        hash = hashInput ? (hashInput.value || '') : '';
      }}
      if (hash) {{
        let target = document.querySelector('[name="JweHashResume"]') || (form ? form.querySelector('[name="JweHashResume"]') : null);
        if (!target && form) {{
          target = document.createElement('input');
          target.type = 'hidden';
          target.name = 'JweHashResume';
          form.appendChild(target);
        }}
        if (target) target.value = hash;
        out.cvUploaded = true;
        out.cvPresent = true;
      }} else {{
        out.cvUploadError = ((upText.match(/<title>(.*?)<\\/title>/) || [])[1] || upText.slice(0, 120)).trim();
      }}
    }} catch (err) {{
      out.cvUploadError = String(err).slice(0, 200);
    }}
  }}
  out.cvTextPreview = cvText.slice(0, 240);
  out.dissuasionRequired = /postcertificationdissuasionformstepframeview|compétences? (?:absentes?|manquantes?)/i.test(initialHtml + ' ' + document.body.innerText);
  const skills = Array.from((initialHtml + ' ' + document.body.innerText).matchAll(/(?:FIMO|FCO|Carte de conducteur)/gi)).map(m => m[0]);
  out.dissuasionSkills = Array.from(new Set(skills.map(s => s.toUpperCase())));
  return JSON.stringify(out);
}})()
"""


def _submit_expression(offer_external_id: str, motivation: str, *, identity: dict[str, str] | None = None) -> str:
    identity = identity or {}
    return f"""
(async () => {{
  const motivation = {_js_string(motivation)};
  const identity = {json.dumps({k: identity.get(k, "") for k in ("firstname", "lastname", "email")}, ensure_ascii=False)};
  const out = {{urlBefore: location.href}};
  let form = document.querySelector('#offer-detail-main-step-form');
  if (!form) {{
    const initialUrl = `/fr-fr/offres/getinitialformframeview?offerId=${{encodeURIComponent({_js_string(offer_external_id)})}}&ts=${{Date.now()}}`;
    const initialResponse = await fetch(initialUrl, {{credentials: 'include', headers: {{'Turbo-Frame': 'funnel-frame'}}}});
    const initialHtml = await initialResponse.text();
    const target = document.querySelector('#funnel-frame') || document.body;
    target.innerHTML = initialHtml;
    form = document.querySelector('#offer-detail-main-step-form');
  }}
  if (!form) throw new Error('HelloWork form missing');
  const field = form.querySelector('[name="MotivationLetter"]');
  if (field && motivation) field.value = motivation;
  const setField = (name, value) => {{
    if (!value) return;
    const el = form.querySelector(`[name="${{name}}"]`);
    if (el) el.value = value;
  }};
  setField('Firstname', identity.firstname);
  setField('Lastname', identity.lastname);
  setField('LastName', identity.lastname);
  setField('Email', identity.email);
  let responseText = '';
  const submitResponse = await fetch(form.action || '/fr-fr/offres/postcandidateinformationfromstepframeview', {{
    method: 'POST', credentials: 'include', body: new FormData(form),
    headers: {{'Turbo-Frame': 'funnel-frame'}}
  }});
  out.submitStatus = submitResponse.status;
  responseText = await submitResponse.text();
  const target = document.querySelector('#funnel-frame') || document.body;
  target.innerHTML = responseText;
  await new Promise(r => setTimeout(r, 800));
  const text = document.body.innerText || '';
  out.urlAfter = location.href;
  out.confirmed = /candidature\\s+est\\s+envoy|candidature\\s+envoy/i.test(text);
  out.textPreview = text.replace(/\\s+/g, ' ').slice(0, 500);
  return JSON.stringify(out);
}})()
"""


CV_MAX_BYTES = 1_900_000  # HelloWork: 2 Mo max — marge de sécurité


def _load_cv_base64(cv_path: str | Path | None) -> tuple[str, str]:
    """Lit un CV local en base64 pour l'upload automatique ("" si aucun/refusé)."""
    if not cv_path:
        return "", ""
    path = Path(cv_path).expanduser()
    if not path.exists():
        logger.warning("CV introuvable: %s", path)
        return "", ""
    size = path.stat().st_size
    if size > CV_MAX_BYTES:
        logger.warning("CV trop volumineux (%d o, max %d) — upload automatique ignoré", size, CV_MAX_BYTES)
        return "", ""
    import base64

    return base64.b64encode(path.read_bytes()).decode("ascii"), path.name


def inspect_hellowork_form(
    conn,
    offer_id: int,
    *,
    browser: HelloWorkBrowser,
    url: str = "",
    motivation: str = "",
    drafts_dir: str | None = None,
    site: str,
    profile: str,
    identity: dict[str, str] | None = None,
    cv_path: str | Path | None = None,
) -> HelloWorkFormState:
    resolved_url = resolve_hellowork_url(conn, offer_id, url)
    offer_external_id = _extract_hellowork_offer_id(resolved_url)
    if not offer_external_id:
        raise ValueError(f"ID offre HelloWork introuvable dans l'URL: {resolved_url}")
    browser.lifecycle_open(resolved_url, site=site, profile=profile)
    final_motivation = motivation if motivation else _read_draft_message(offer_id, drafts_dir=drafts_dir)
    resolved_identity = identity if identity is not None else emploi_config.get_identity()
    cv_b64, cv_name = _load_cv_base64(cv_path)
    data = _json_from_browser_result(
        browser.console_eval(
            _inspect_expression(
                offer_external_id,
                final_motivation,
                identity=resolved_identity,
                cv_b64=cv_b64,
                cv_name=cv_name,
            ),
            site=site,
            profile=profile,
        )
    )
    if int(data.get("initialStatus") or 0) >= 400:
        raise ValueError(f"HelloWork form inaccessible: HTTP {data.get('initialStatus')}")
    return HelloWorkFormState(
        url=resolved_url,
        offer_external_id=offer_external_id,
        funnel_id="present" if data.get("funnelIdPresent") else "",
        firstname_present=bool(data.get("firstnamePresent")),
        lastname_present=bool(data.get("lastnamePresent")),
        email_present=bool(data.get("emailPresent")),
        motivation_present=bool(data.get("motivationPresent")),
        cv_present=bool(data.get("cvPresent")),
        submit_button_present=bool(data.get("submitButtonPresent")),
        dissuasion_required=bool(data.get("dissuasionRequired")),
        dissuasion_skills=tuple(str(skill) for skill in data.get("dissuasionSkills") or ()),
        cgu_consent_required=bool(data.get("cguConsentRequired")),
        cgu_consent_checked=bool(data.get("cguConsentChecked")),
    )


def _resolve_sent_stack(endpoint: dict[str, Any], stack: str) -> int:
    candidates = [stack] if stack else ["candidature-envoyee", "candidatures-envoyees", "envoyee", "envoyees", "sent"]
    errors: list[str] = []
    for candidate in candidates:
        try:
            return emploi_config.resolve_kanban_stack(endpoint, candidate)
        except ValueError as error:
            errors.append(str(error))
    aliases = ", ".join((endpoint.get("stacks") or {}).keys()) or "aucun"
    raise ValueError(f"Stack Kanban candidature envoyée introuvable (alias disponibles: {aliases})")


def _create_sent_deck_card(
    conn,
    offer_id: int,
    *,
    kanban_stack: str = "",
    kanban_endpoint: str = "",
    dry_run: bool = False,
) -> DeckCardResult | None:
    endpoint = (
        emploi_config.get_kanban_endpoint(kanban_endpoint)
        if kanban_endpoint
        else emploi_config.get_default_kanban_endpoint()
    )
    if endpoint is None:
        if dry_run:
            return None
        raise ValueError("Aucun endpoint kanban configuré. Utilise `emploi kanban set ...`.")
    stack_id = _resolve_sent_stack(endpoint, kanban_stack)
    return create_offer_card(conn, offer_id, endpoint=endpoint, stack_id=stack_id, dry_run=dry_run)


SUBMITTED_APPLICATION_STATUSES = ("sent", "submitted", "followup", "response", "interview")
SUBMITTED_OFFER_STATUSES = ("applied", "sent", "followup", "response", "interview")


def _ensure_not_already_submitted(conn, offer_id: int) -> None:
    offer = get_offer(conn, offer_id)
    if offer is not None and str(offer["status"] or "") in SUBMITTED_OFFER_STATUSES:
        raise ValueError(f"Candidature HelloWork déjà envoyée pour l'offre #{offer_id}")
    placeholders = ",".join("?" for _ in SUBMITTED_APPLICATION_STATUSES)
    existing = conn.execute(
        f"""
        SELECT id FROM applications
        WHERE offer_id = ? AND status IN ({placeholders})
        ORDER BY id DESC
        LIMIT 1
        """,
        (offer_id, *SUBMITTED_APPLICATION_STATUSES),
    ).fetchone()
    if existing is not None:
        raise ValueError(f"Candidature HelloWork déjà envoyée pour l'offre #{offer_id}")


def _record_sent_application(conn, offer_id: int, *, notes: str) -> int:
    existing = conn.execute(
        """
        SELECT id FROM applications
        WHERE offer_id = ? AND status = 'draft'
        ORDER BY id DESC
        LIMIT 1
        """,
        (offer_id,),
    ).fetchone()
    if existing is not None:
        application_id = int(existing["id"])
        conn.execute(
            "UPDATE applications SET status = ?, applied_at = CURRENT_TIMESTAMP, last_contact_at = CURRENT_TIMESTAMP, notes = ? WHERE id = ?",
            ("sent", notes, application_id),
        )
        update_offer_status(conn, offer_id, "sent")
        conn.commit()
        return application_id
    application_id = add_application(conn, offer_id, status="sent", notes=notes)
    update_offer_status(conn, offer_id, "sent")
    return application_id


def apply_hellowork(
    conn,
    offer_id: int,
    *,
    browser: HelloWorkBrowser,
    submit: bool = False,
    url: str = "",
    motivation: str = "",
    drafts_dir: str | None = None,
    site: str,
    profile: str,
    kanban: bool = True,
    kanban_stack: str = "",
    kanban_endpoint: str = "",
    ack_dissuasion: bool = False,
    identity: dict[str, str] | None = None,
    cv_path: str | Path | None = None,
) -> HelloWorkApplyResult:
    if submit:
        _ensure_not_already_submitted(conn, offer_id)
    form = inspect_hellowork_form(
        conn,
        offer_id,
        browser=browser,
        url=url,
        motivation=motivation,
        drafts_dir=drafts_dir,
        site=site,
        profile=profile,
        identity=identity,
        cv_path=cv_path,
    )
    if not form.required_fields_present:
        missing = []
        if not form.funnel_id:
            missing.append("FunnelId")
        if not form.firstname_present:
            missing.append("Firstname")
        if not form.lastname_present:
            missing.append("Lastname")
        if not form.email_present:
            missing.append("Email")
        if not form.cv_present:
            missing.append("CV")
        if not form.submit_button_present:
            missing.append("bouton submit")
        raise ValueError("Formulaire HelloWork incomplet: " + ", ".join(missing))
    if submit and form.dissuasion_required and not ack_dissuasion:
        skills = ", ".join(form.dissuasion_skills) or "non précisées"
        raise ValueError(f"Dissuasion HelloWork détectée ({skills}); ajoute --ack-dissuasion pour confirmer l'envoi")
    if submit and not form.cgu_consent_ok:
        raise ValueError(
            "Consentement CGU HelloWork requis mais non coché (champ HasAcceptedCGU); "
            "le CLI ne coche jamais ce champ à ta place — coche-le dans le navigateur puis relance"
        )
    if not submit:
        add_offer_event(
            conn,
            offer_id,
            event_type="hellowork_apply_dry_run",
            message="Prévisualisation candidature HelloWork prête",
            payload_json=json.dumps(
                {
                    "source": "hellowork",
                    "url": form.url,
                    "external_offer_id": form.offer_external_id,
                    "submit_application": False,
                    "cv_present": form.cv_present,
                    "dissuasion_required": form.dissuasion_required,
                    "dissuasion_skills": list(form.dissuasion_skills),
                    "cgu_consent_required": form.cgu_consent_required,
                    "cgu_consent_checked": form.cgu_consent_checked,
                },
                ensure_ascii=False,
            ),
        )
        deck = None
        if kanban:
            try:
                deck = _create_sent_deck_card(
                    conn, offer_id, kanban_stack=kanban_stack, kanban_endpoint=kanban_endpoint, dry_run=True
                )
            except Exception as error:
                add_offer_event(
                    conn,
                    offer_id,
                    event_type="nextcloud_deck_preview_failed",
                    message="Prévisualisation carte Deck HelloWork non disponible",
                    payload_json=json.dumps(
                        {
                            "source": "hellowork",
                            "url": form.url,
                            "external_offer_id": form.offer_external_id,
                            "error": str(error),
                        },
                        ensure_ascii=False,
                    ),
                )
        return HelloWorkApplyResult(
            offer_id=offer_id,
            url=form.url,
            dry_run=True,
            submitted=False,
            status="ready",
            message="Dry-run HelloWork prêt: aucun envoi effectué",
            form=form,
            deck_card=deck,
        )
    final_motivation = motivation if motivation else _read_draft_message(offer_id, drafts_dir=drafts_dir)
    data = _json_from_browser_result(
        browser.console_eval(
            _submit_expression(form.offer_external_id, final_motivation, identity=identity), site=site, profile=profile
        )
    )
    if not data.get("confirmed"):
        raise ValueError("Confirmation HelloWork non détectée après soumission")
    application_id = _record_sent_application(conn, offer_id, notes="Candidature envoyée via HelloWork")
    add_offer_event(
        conn,
        offer_id,
        event_type="application_submitted",
        message="Candidature envoyée via HelloWork",
        payload_json=json.dumps(
            {
                "source": "hellowork",
                "url": form.url,
                "external_offer_id": form.offer_external_id,
                "application_id": application_id,
                "confirmation_detected": True,
                "submit_status": data.get("submitStatus"),
                "dissuasion_required": form.dissuasion_required,
                "dissuasion_skills": list(form.dissuasion_skills),
            },
            ensure_ascii=False,
        ),
    )
    deck = None
    if kanban:
        try:
            deck = _create_sent_deck_card(
                conn, offer_id, kanban_stack=kanban_stack, kanban_endpoint=kanban_endpoint, dry_run=False
            )
        except Exception as error:
            add_offer_event(
                conn,
                offer_id,
                event_type="nextcloud_deck_card_failed",
                message="Carte Deck non créée après candidature HelloWork envoyée",
                payload_json=json.dumps(
                    {
                        "source": "hellowork",
                        "url": form.url,
                        "external_offer_id": form.offer_external_id,
                        "application_id": application_id,
                        "error": str(error),
                    },
                    ensure_ascii=False,
                ),
            )
    return HelloWorkApplyResult(
        offer_id=offer_id,
        url=form.url,
        dry_run=False,
        submitted=True,
        status="sent",
        message="Candidature HelloWork envoyée",
        form=form,
        application_id=application_id,
        deck_card=deck,
    )
