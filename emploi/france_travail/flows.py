from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode, urljoin

from emploi.applications import create_application_draft
from emploi.browser.client import ManagedBrowserClient
from emploi.browser.models import DEFAULT_PROFILE, DEFAULT_SITE, BrowserCommandResult
from emploi.db import add_offer, add_offer_event, get_offer, get_saved_search, update_saved_search_last_run
from emploi.france_travail.distance import within_requested_radius
from emploi.france_travail.extractors import ExtractedOffer, _offer_from_mapping, extract_offer_detail, extract_offers
from emploi.scoring import score_offer
from emploi.utils import _matches_terms

FT_SEARCH_URL = "https://candidat.francetravail.fr/offres/recherche"
EXTERNAL_SOURCE = "france-travail"
FT_LOCATION_CODES = {
    "bogeve": "74038",
    "bogève": "74038",
    "bogève 74250": "74038",
}


class BrowserLike(Protocol):
    def open(self, url: str, *, site: str = DEFAULT_SITE, profile: str = DEFAULT_PROFILE) -> BrowserCommandResult: ...
    def lifecycle_open(
        self, url: str, *, site: str = DEFAULT_SITE, profile: str = DEFAULT_PROFILE
    ) -> BrowserCommandResult: ...
    def snapshot(
        self, *, label: str | None = None, site: str = DEFAULT_SITE, profile: str = DEFAULT_PROFILE
    ) -> BrowserCommandResult: ...


@dataclass(frozen=True)
class SearchImportResult:
    offer_id: int
    created: bool
    title: str
    score: int
    browser_url: str


@dataclass(frozen=True)
class RefreshResult:
    offer_id: int
    is_active: bool
    browser_url: str


@dataclass(frozen=True)
class ApplyCheckResult:
    offer_id: int
    can_apply: bool
    is_active: bool
    already_applied: bool
    has_apply_signal: bool
    reasons: list[str]
    browser_url: str
    partner_handoff: list[dict[str, str]] | None = None


@dataclass(frozen=True)
class DraftResult:
    offer_id: int
    application_id: int
    draft_path: Path


@dataclass(frozen=True)
class PartnerOpenResult:
    offer_id: int
    partner_name: str
    url: str


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _raw_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _browser(browser: BrowserLike | None) -> BrowserLike:
    return browser or ManagedBrowserClient()


def _normalize_location(location: str) -> str:
    normalized = re.sub(r"\s+", " ", location.strip()).casefold()
    return FT_LOCATION_CODES.get(normalized, location)


def _normalize_query(query: str) -> str:
    previous = query
    for _ in range(3):
        decoded = html.unescape(previous)
        if decoded == previous:
            break
        previous = decoded
    return previous.replace("“", '"').replace("”", '"').strip()


def _france_travail_keywords(query: str) -> str:
    """Return UI-safe France Travail keywords; keep exclusions for client filtering only."""
    normalized = _normalize_query(query)
    positives: list[str] = []
    for quoted in re.findall(r'(-?)"([^"]+)"', normalized):
        if not quoted[0]:
            positives.append(quoted[1])
    remainder = re.sub(r'-?"[^"]+"', " ", normalized)
    for token in re.findall(r"-?\w+", remainder, re.U):
        if not token.startswith("-"):
            positives.append(token)
    return " ".join(positives).strip() or normalized


def _extract_browser_dom_offers(browser: BrowserLike, *, site: str, profile: str) -> list[ExtractedOffer]:
    if not hasattr(browser, "console_eval"):
        return []
    expression = r"""
Array.from(document.querySelectorAll('li.result')).map(li => {
  const link = li.querySelector('a[href*="/offres/recherche/detail/"]');
  const title = li.querySelector('.media-heading-title')?.innerText || '';
  const subtext = li.querySelector('.subtext')?.innerText || '';
  const description = li.querySelector('.description')?.innerText || '';
  const contract = li.querySelector('.contrat')?.innerText || '';
  return {title, href: link?.href || '', text: li.innerText || '', description, contract_type: contract, html: li.outerHTML, subtext};
})
""".strip()
    try:
        result = browser.console_eval(expression, site=site, profile=profile)  # type: ignore[attr-defined]
    except Exception:
        return []
    value = result.payload.get("value") if isinstance(result.payload, dict) else None
    if not isinstance(value, list):
        nested = result.payload.get("result") if isinstance(result.payload, dict) else None
        value = nested.get("value", []) if isinstance(nested, dict) else []
    if value is None:
        value = []
    offers: list[ExtractedOffer] = []
    for item in value:  # type: ignore[union-attr]
        if isinstance(item, dict):
            offer = _offer_from_mapping(item)
            if offer:
                offers.append(offer)
    return offers


def build_search_url(query: str, location: str = "", radius: int = 0, contract: str = "") -> str:
    params: dict[str, object] = {"motsCles": _france_travail_keywords(query)}
    if location:
        params["lieux"] = _normalize_location(location)
    if radius > 0:
        params["rayon"] = radius
    if contract:
        params["typeContrat"] = contract
    return f"{FT_SEARCH_URL}?{urlencode(params)}"


def _find_existing(conn, offer: ExtractedOffer):
    if offer.external_id:
        row = conn.execute(
            "SELECT * FROM offers WHERE external_source = ? AND external_id = ?",
            (EXTERNAL_SOURCE, offer.external_id),
        ).fetchone()
        if row:
            return row
    if offer.browser_url:
        row = conn.execute(
            "SELECT * FROM offers WHERE external_source = ? AND browser_url = ?",
            (EXTERNAL_SOURCE, offer.browser_url),
        ).fetchone()
        if row:
            return row
        return conn.execute(
            "SELECT * FROM offers WHERE external_source = '' AND browser_url = ?",
            (offer.browser_url,),
        ).fetchone()
    return None


def _offer_is_relevant(
    offer: ExtractedOffer,
    *,
    query: str,
    contract: str = "",
    origin_location: str = "",
    requested_radius: int = 0,
) -> bool:
    text = " ".join(
        (offer.title, offer.company, offer.location, offer.description, offer.contract_type, offer.raw_text)
    )
    if query and not _matches_terms(text, query):
        return False
    if (
        contract
        and contract.casefold() not in offer.contract_type.casefold()
        and contract.casefold() not in text.casefold()
    ):
        return False
    if not within_requested_radius(origin_location, offer.location or offer.raw_text, requested_radius):
        return False
    return True


def _archive_excluded_existing_offer(conn, offer_id: int) -> None:
    conn.execute(
        """
        UPDATE offers
        SET is_active = 0,
            status = CASE WHEN status = 'new' THEN 'archived' ELSE status END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (offer_id,),
    )
    add_offer_event(
        conn,
        offer_id,
        event_type="search_excluded",
        message="Excluded by France Travail saved-search client filters",
    )


def _mark_existing_excluded_offers_inactive(
    conn,
    extracted: list[ExtractedOffer],
    relevant: list[ExtractedOffer],
    *,
    query: str,
    contract: str = "",
    origin_location: str = "",
    requested_radius: int = 0,
) -> None:
    """Deactivate previously imported FT offers excluded by current client filters."""
    relevant_keys = {
        (offer.external_id or offer.browser_url) for offer in relevant if offer.external_id or offer.browser_url
    }
    for offer in extracted:
        key = offer.external_id or offer.browser_url
        if not key or key in relevant_keys:
            continue
        existing = _find_existing(conn, offer)
        if existing:
            _archive_excluded_existing_offer(conn, int(existing["id"]))
    conn.commit()


def _upsert_extracted_offer(conn, offer: ExtractedOffer, snapshot_payload: dict) -> SearchImportResult:
    timestamp = _now()
    raw_snapshot = _raw_json(snapshot_payload)
    existing = _find_existing(conn, offer)
    merged = dict(existing) if existing else {}
    merged.update(
        {"title": offer.title, "company": offer.company, "location": offer.location, "description": offer.description}
    )
    scored = score_offer(merged)
    if existing:
        external_id = offer.external_id or str(existing["external_id"] or "")
        apply_url = offer.apply_url or str(existing["apply_url"] or "")
        conn.execute(
            """
            UPDATE offers
            SET title = ?, company = ?, location = ?, url = ?, source = ?, description = ?,
                salary = ?, remote = ?, contract_type = ?, external_source = ?, external_id = ?,
                score = ?, score_reasons = ?, browser_url = ?, apply_url = ?, is_active = 1,
                last_seen_at = ?, raw_browser_snapshot = ?, raw_extracted_text = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                offer.title,
                offer.company,
                offer.location,
                offer.browser_url,
                EXTERNAL_SOURCE,
                offer.description,
                offer.salary,
                offer.remote,
                offer.contract_type,
                EXTERNAL_SOURCE,
                external_id,
                scored.score,
                "\n".join(scored.reasons),
                offer.browser_url,
                apply_url,
                timestamp,
                raw_snapshot,
                offer.raw_text,
                existing["id"],
            ),
        )
        conn.commit()
        add_offer_event(conn, int(existing["id"]), event_type="search_seen", message="Seen in France Travail search")
        return SearchImportResult(int(existing["id"]), False, offer.title, scored.score, offer.browser_url)

    offer_id = add_offer(
        conn,
        title=offer.title,
        company=offer.company,
        location=offer.location,
        url=offer.browser_url,
        source=EXTERNAL_SOURCE,
        description=offer.description,
        salary=offer.salary,
        remote=offer.remote,
        contract_type=offer.contract_type,
        external_source=EXTERNAL_SOURCE,
        external_id=offer.external_id,
        browser_url=offer.browser_url,
        apply_url=offer.apply_url,
        is_active=True,
        last_seen_at=timestamp,
        raw_browser_snapshot=raw_snapshot,
        raw_extracted_text=offer.raw_text,
    )
    add_offer_event(conn, offer_id, event_type="search_imported", message="Imported from France Travail search")
    row = get_offer(conn, offer_id)
    return SearchImportResult(
        offer_id, True, offer.title, int(row["score"] if row else scored.score), offer.browser_url
    )


def search_offers(
    conn,
    *,
    query: str,
    location: str = "",
    radius: int = 0,
    contract: str = "",
    requested_radius: int | None = None,
    browser: BrowserLike | None = None,
    site: str = DEFAULT_SITE,
    profile: str = DEFAULT_PROFILE,
) -> list[SearchImportResult]:
    client = _browser(browser)
    normalized_query = _normalize_query(query)
    effective_requested_radius = radius if requested_radius is None else requested_radius
    url = build_search_url(normalized_query, location, radius, contract)
    client.lifecycle_open(url, site=site, profile=profile)
    snapshot = client.snapshot(label="ft-search", site=site, profile=profile)
    extracted = extract_offers(snapshot.payload)
    if not extracted:
        extracted = _extract_browser_dom_offers(client, site=site, profile=profile)
    relevant = [
        offer
        for offer in extracted
        if _offer_is_relevant(
            offer,
            query=normalized_query,
            contract=contract,
            origin_location=location,
            requested_radius=effective_requested_radius,
        )
    ]
    _mark_existing_excluded_offers_inactive(
        conn,
        extracted,
        relevant,
        query=normalized_query,
        contract=contract,
        origin_location=location,
        requested_radius=effective_requested_radius,
    )
    return [_upsert_extracted_offer(conn, offer, snapshot.payload) for offer in relevant]


def run_saved_search(
    conn,
    search_id_or_name: int | str,
    *,
    browser: BrowserLike | None = None,
    site: str = DEFAULT_SITE,
    profile: str = DEFAULT_PROFILE,
) -> list[SearchImportResult]:
    saved = get_saved_search(conn, search_id_or_name)
    if saved is None:
        raise ValueError(f"Profil de recherche introuvable: {search_id_or_name}")
    if not saved["enabled"]:
        raise ValueError(f"Profil de recherche désactivé: {saved['name']}")
    results = search_offers(
        conn,
        query=saved["query"],
        location=saved["where_text"],
        radius=int(saved["radius"]),
        contract=saved["contract"],
        requested_radius=int(saved["requested_radius"] or saved["radius"]),
        browser=browser,
        site=site,
        profile=profile,
    )
    update_saved_search_last_run(conn, int(saved["id"]), _now())
    return results


def _offer_url(offer) -> str:
    return offer["browser_url"] or offer["url"]


def _extract_detail_text(snapshot_payload: dict, detail_text: str) -> str:
    for key in ("text", "markdown", "content"):
        value = snapshot_payload.get(key) if isinstance(snapshot_payload, dict) else None
        if value:
            return str(value)
    return detail_text


def _extract_browser_dom_offer_detail(browser: BrowserLike, *, site: str, profile: str) -> str:
    if not hasattr(browser, "console_eval"):
        return ""
    expression = r"""
(() => {
  const selectors = [
    '[data-testid="offre-detail"]',
    '#contents',
    '#content',
    'main',
    'article',
    'body'
  ];
  const node = selectors.map(sel => document.querySelector(sel)).find(Boolean);
  return node?.innerText || document.body?.innerText || '';
})()
""".strip()
    try:
        result = browser.console_eval(expression, site=site, profile=profile)  # type: ignore[attr-defined]
    except Exception:
        return ""
    if not isinstance(result.payload, dict):
        return ""
    value = result.payload.get("value")
    if value is None:
        nested = result.payload.get("result")
        value = nested.get("value") if isinstance(nested, dict) else None
    return str(value or "").strip()


def _looks_like_snapshot_metadata(text: str) -> bool:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return False
    return '"operation": "snapshot"' in stripped or '"observable_state"' in stripped


def _best_detail_text(
    snapshot_payload: dict, detail_text: str, browser: BrowserLike, *, site: str, profile: str
) -> str:
    text = _extract_detail_text(snapshot_payload, detail_text).strip()
    if _looks_like_snapshot_metadata(text):
        dom_text = _extract_browser_dom_offer_detail(browser, site=site, profile=profile)
        if dom_text:
            return dom_text
    return text or detail_text


def refresh_offer(
    conn,
    offer_id: int,
    *,
    browser: BrowserLike | None = None,
    site: str = DEFAULT_SITE,
    profile: str = DEFAULT_PROFILE,
) -> RefreshResult:
    offer = get_offer(conn, offer_id)
    if offer is None:
        raise ValueError(f"Offre introuvable: {offer_id}")
    url = _offer_url(offer)
    if not url:
        raise ValueError(f"Offre #{offer_id} sans URL navigateur")
    client = _browser(browser)
    client.lifecycle_open(url, site=site, profile=profile)
    snapshot = client.snapshot(label=f"ft-offer-{offer_id}", site=site, profile=profile)
    detail = extract_offer_detail(snapshot.payload)
    timestamp = _now()
    detail_text = _best_detail_text(snapshot.payload, detail.text, client, site=site, profile=profile)
    conn.execute(
        """
        UPDATE offers
        SET is_active = ?, last_refreshed_at = ?, raw_browser_snapshot = ?,
            raw_extracted_text = ?, description = CASE WHEN length(?) > length(COALESCE(description, '')) THEN ? ELSE description END,
            apply_url = COALESCE(NULLIF(?, ''), apply_url), updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            1 if detail.is_active else 0,
            timestamp,
            _raw_json(snapshot.payload),
            detail_text,
            detail_text,
            detail_text,
            detail.apply_url,
            offer_id,
        ),
    )
    conn.commit()
    add_offer_event(
        conn,
        offer_id,
        event_type="refresh",
        message="France Travail offer is active" if detail.is_active else "France Travail offer is inactive",
        payload_json=json.dumps({"is_active": detail.is_active}, ensure_ascii=False),
    )
    return RefreshResult(offer_id, detail.is_active, url)


def _application_statuses(conn, offer_id: int) -> list[str]:
    rows = conn.execute("SELECT status FROM applications WHERE offer_id = ?", (offer_id,)).fetchall()
    return [str(row["status"]).strip().lower() for row in rows]


def _has_submitted_application(conn, offer_id: int) -> bool:
    draft_like = {"draft", "cancelled", "rejected"}
    return any(status not in draft_like for status in _application_statuses(conn, offer_id))


def _payload_text(payload: object) -> str:
    if isinstance(payload, dict):
        parts = [str(payload.get(key) or "") for key in ("text", "snapshot", "html", "markdown", "content")]
        result = payload.get("result")
        if result is not None:
            parts.append(_payload_text(result))
        return "\n".join(part for part in parts if part)
    return str(payload or "")


def _extract_partner_handoff_from_dom(browser: BrowserLike, *, site: str, profile: str) -> list[dict[str, str]]:
    if not hasattr(browser, "console_eval"):
        return []
    expression = r"""
(() => {
  const names = ['Meteojob', 'HelloWork'];
  const partners = [];
  const links = Array.from(document.querySelectorAll('a[href]'));
  for (const name of names) {
    const normalized = name.toLowerCase();
    const direct = links.find((a) => ((a.innerText || a.textContent || '') + ' ' + (a.href || '')).toLowerCase().includes(normalized));
    if (direct) {
      partners.push({name, url: direct.href || direct.getAttribute('href') || ''});
      continue;
    }
    const card = Array.from(document.querySelectorAll('div, section, article, li')).find((el) => (el.innerText || el.textContent || '').trim().toLowerCase() === normalized);
    const cardLink = card?.querySelector('a[href]') || card?.closest('a[href]') || card?.parentElement?.querySelector('a[href]');
    if (cardLink) {
      partners.push({name, url: cardLink.href || cardLink.getAttribute('href') || ''});
    }
  }
  return partners;
})()
"""
    result = browser.console_eval(expression, site=site, profile=profile)
    payload = None
    if isinstance(result.payload, dict):
        payload = result.payload.get("value")
        if payload is None:
            payload = result.payload.get("result")
    if isinstance(payload, dict) and isinstance(payload.get("value"), list):
        payload = payload["value"]
    if not isinstance(payload, list):
        return []
    partners: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        url = str(item.get("url") or "")
        if name not in {"Meteojob", "HelloWork"} or name in seen:
            continue
        partner = {"name": name}
        if url:
            partner["url"] = url
        partners.append(partner)
        seen.add(name)
    return partners


def _detect_partner_handoff(payload: object) -> list[dict[str, str]]:
    text = _payload_text(payload)
    lower = text.casefold()
    if "choisissez le partenaire" not in lower and "site de meteojob" not in lower and "site de hellowork" not in lower:
        return []
    partners: list[dict[str, str]] = []
    for name in ("Meteojob", "HelloWork"):
        name_lower = name.casefold()
        if name_lower not in lower:
            continue
        url = ""
        link_pattern = re.compile("href=[\\\"']([^\\\"']+)[\\\"'][^>]*>[^<]*(?:" + re.escape(name) + ")", re.I)
        link_match = link_pattern.search(text)
        if not link_match:
            labelled_link_pattern = re.compile(
                "<a\\b[^>]*href=[\\\"']([^\\\"']+)[\\\"'][^>]*>(?:(?!</a>).)*" + re.escape(name) + "(?:(?!</a>).)*</a>",
                re.I | re.S,
            )
            link_match = labelled_link_pattern.search(text)
        if link_match:
            url = urljoin("https://candidat.francetravail.fr", html.unescape(link_match.group(1)))
        partner = {"name": name}
        if url:
            partner["url"] = url
        partners.append(partner)
    return partners


def _expand_apply_options(browser: BrowserLike, *, site: str, profile: str) -> bool:
    if not hasattr(browser, "console_eval"):
        return False
    expression = r"""
(() => {
  const labels = ["postuler à l'offre", "postuler", "candidater"];
  const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'));
  const target = candidates.find((el) => {
    const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().toLowerCase();
    return labels.some((label) => text.includes(label));
  });
  if (!target) return {clicked: false, reason: 'apply button not found'};
  target.click();
  return {clicked: true};
})()
""".strip()
    try:
        result = browser.console_eval(expression, site=site, profile=profile)  # type: ignore[attr-defined]
    except Exception:
        return False
    value = result.payload.get("value") if isinstance(result.payload, dict) else None
    if isinstance(value, dict):
        return bool(value.get("clicked"))
    nested = result.payload.get("result") if isinstance(result.payload, dict) else None
    if isinstance(nested, dict):
        if "clicked" in nested:
            return bool(nested.get("clicked"))
        if isinstance(nested.get("value"), dict):
            return bool(nested["value"].get("clicked"))
    return False


def apply_check_offer(
    conn,
    offer_id: int,
    *,
    browser: BrowserLike | None = None,
    site: str = DEFAULT_SITE,
    profile: str = DEFAULT_PROFILE,
) -> ApplyCheckResult:
    offer = get_offer(conn, offer_id)
    if offer is None:
        raise ValueError(f"Offre introuvable: {offer_id}")
    reasons: list[str] = []
    application_statuses = _application_statuses(conn, offer_id)
    already_applied = _has_submitted_application(conn, offer_id) or (
        offer["status"] == "applied" and not application_statuses
    )
    stored_active = bool(offer["is_active"])
    url = _offer_url(offer)
    detail_active = stored_active
    has_apply_signal = False
    partner_handoff: list[dict[str, str]] = []
    if url:
        client = _browser(browser)
        client.lifecycle_open(url, site=site, profile=profile)
        snapshot = client.snapshot(label=f"ft-apply-check-{offer_id}", site=site, profile=profile)
        detail = extract_offer_detail(snapshot.payload)
        detail_active = detail.is_active
        has_apply_signal = detail.can_apply
        partner_handoff = _detect_partner_handoff(snapshot.payload)
        if (
            has_apply_signal
            and not any(partner.get("url") for partner in partner_handoff)
            and _expand_apply_options(client, site=site, profile=profile)
        ):
            expanded_partner_handoff: list[dict[str, str]] = []
            expanded_snapshot = snapshot
            expanded_detail = detail
            for attempt in range(3):
                if attempt:
                    time.sleep(0.5)
                expanded_snapshot = client.snapshot(
                    label=f"ft-apply-check-{offer_id}-expanded", site=site, profile=profile
                )
                expanded_detail = extract_offer_detail(expanded_snapshot.payload)
                expanded_partner_handoff = _detect_partner_handoff(expanded_snapshot.payload)
                dom_partner_handoff = _extract_partner_handoff_from_dom(client, site=site, profile=profile)
                if dom_partner_handoff:
                    expanded_partner_handoff = dom_partner_handoff
                if expanded_partner_handoff:
                    break
            partner_handoff = expanded_partner_handoff
            snapshot = expanded_snapshot
            detail = expanded_detail
            detail_active = expanded_detail.is_active
            has_apply_signal = expanded_detail.can_apply
        conn.execute(
            """
            UPDATE offers
            SET is_active = ?, last_refreshed_at = ?, raw_browser_snapshot = ?, raw_extracted_text = ?,
                apply_url = COALESCE(NULLIF(?, ''), apply_url), updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                1 if detail.is_active else 0,
                _now(),
                _raw_json(snapshot.payload),
                detail.text,
                detail.apply_url,
                offer_id,
            ),
        )
        conn.commit()
    if not stored_active or not detail_active:
        reasons.append("Offre inactive")
    if already_applied:
        reasons.append("Candidature déjà enregistrée")
    if has_apply_signal:
        reasons.append("Signal de candidature détecté")
    else:
        reasons.append("Aucun signal de candidature détecté")
    if partner_handoff:
        partner_names = [partner["name"] for partner in partner_handoff]
        reasons.append(f"Partenaire(s) externe(s) détecté(s) : {', '.join(partner_names)}")
    can_apply = detail_active and not already_applied and has_apply_signal
    add_offer_event(
        conn,
        offer_id,
        event_type="apply_check",
        message="Candidature possible" if can_apply else "Candidature non disponible",
        payload_json=json.dumps(
            {"can_apply": can_apply, "reasons": reasons, "partner_handoff": partner_handoff},
            ensure_ascii=False,
        ),
    )
    return ApplyCheckResult(
        offer_id,
        can_apply,
        detail_active,
        already_applied,
        has_apply_signal,
        reasons,
        url,
        partner_handoff or None,
    )


def draft_application(conn, offer_id: int, *, drafts_dir: str | Path | None = None) -> DraftResult:
    result = create_application_draft(conn, offer_id, drafts_dir=drafts_dir)
    return DraftResult(result.offer_id, result.application_id, result.draft_path)


def open_offer(
    conn, offer_id: int, *, browser: BrowserLike | None = None, site: str = DEFAULT_SITE, profile: str = DEFAULT_PROFILE
) -> str:
    offer = get_offer(conn, offer_id)
    if offer is None:
        raise ValueError(f"Offre introuvable: {offer_id}")
    url = _offer_url(offer)
    if not url:
        raise ValueError(f"Offre #{offer_id} sans URL navigateur")
    _browser(browser).lifecycle_open(url, site=site, profile=profile)
    add_offer_event(conn, offer_id, event_type="opened", message=url)
    return url


def _partner_name_matches(candidate: str, requested: str) -> bool:
    return candidate.casefold().replace(" ", "") == requested.casefold().replace(" ", "")


def open_partner_offer(
    conn,
    offer_id: int,
    partner: str,
    *,
    browser: BrowserLike | None = None,
    site: str = DEFAULT_SITE,
    profile: str = DEFAULT_PROFILE,
) -> PartnerOpenResult:
    result = apply_check_offer(conn, offer_id, browser=browser, site=site, profile=profile)
    partners = result.partner_handoff or []
    match = next((item for item in partners if _partner_name_matches(str(item.get("name") or ""), partner)), None)
    if not match:
        available = ", ".join(str(item.get("name") or "") for item in partners if item.get("name")) or "aucun"
        raise ValueError(f"Partenaire introuvable pour l'offre #{offer_id}: {partner} (disponible(s): {available})")
    url = str(match.get("url") or "")
    if not url:
        raise ValueError(f"URL partenaire introuvable pour l'offre #{offer_id}: {match.get('name')}")
    _browser(browser).lifecycle_open(url, site=site, profile=profile)
    partner_name = str(match.get("name") or partner)
    add_offer_event(
        conn,
        offer_id,
        event_type="partner_opened",
        message=partner_name,
        payload_json=json.dumps({"name": partner_name, "url": url}, ensure_ascii=False),
    )
    return PartnerOpenResult(offer_id=offer_id, partner_name=partner_name, url=url)
