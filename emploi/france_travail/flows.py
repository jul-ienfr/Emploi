from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

from emploi.browser.client import ManagedBrowserClient
from emploi.browser.models import DEFAULT_PROFILE, DEFAULT_SITE, BrowserCommandResult
from emploi.applications import create_application_draft
from emploi.db import add_offer, add_offer_event, get_offer, get_saved_search, update_saved_search_last_run
from emploi.france_travail.extractors import ExtractedOffer, _offer_from_mapping, extract_offer_detail, extract_offers
from emploi.scoring import score_offer

FT_SEARCH_URL = "https://candidat.francetravail.fr/offres/recherche"
EXTERNAL_SOURCE = "france-travail"
FT_LOCATION_CODES = {
    "bogeve": "74040",
    "bogève": "74040",
    "bogève 74250": "74040",
}


class BrowserLike(Protocol):
    def open(self, url: str, *, site: str = DEFAULT_SITE, profile: str = DEFAULT_PROFILE) -> BrowserCommandResult: ...
    def lifecycle_open(self, url: str, *, site: str = DEFAULT_SITE, profile: str = DEFAULT_PROFILE) -> BrowserCommandResult: ...
    def snapshot(self, *, label: str | None = None, site: str = DEFAULT_SITE, profile: str = DEFAULT_PROFILE) -> BrowserCommandResult: ...


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


@dataclass(frozen=True)
class DraftResult:
    offer_id: int
    application_id: int
    draft_path: Path


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
        value = nested.get("value") if isinstance(nested, dict) else []
    offers: list[ExtractedOffer] = []
    for item in value:
        if isinstance(item, dict):
            offer = _offer_from_mapping(item)
            if offer:
                offers.append(offer)
    return offers


def build_search_url(query: str, location: str = "", radius: int = 0, contract: str = "") -> str:
    params: dict[str, object] = {"motsCles": _normalize_query(query)}
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
        return conn.execute(
            "SELECT * FROM offers WHERE external_source = ? AND browser_url = ?",
            (EXTERNAL_SOURCE, offer.browser_url),
        ).fetchone()
    return None


def _matches_terms(text: str, query: str) -> bool:
    normalized = text.casefold()
    positives: list[str] = []
    negatives: list[str] = []
    for quoted in re.findall(r'(-?)"([^"]+)"', query):
        (negatives if quoted[0] else positives).append(quoted[1].casefold())
    remainder = re.sub(r'-?"[^"]+"', ' ', query)
    for token in re.findall(r"-?\w+", remainder, re.U):
        if token.startswith('-'):
            negatives.append(token[1:].casefold())
        else:
            positives.append(token.casefold())
    return all(term in normalized for term in positives) and not any(term and term in normalized for term in negatives)


def _offer_is_relevant(offer: ExtractedOffer, *, query: str, contract: str = "") -> bool:
    text = " ".join((offer.title, offer.company, offer.location, offer.description, offer.contract_type, offer.raw_text))
    if query and not _matches_terms(text, query):
        return False
    if contract and contract.casefold() not in offer.contract_type.casefold() and contract.casefold() not in text.casefold():
        return False
    return True


def _upsert_extracted_offer(conn, offer: ExtractedOffer, snapshot_payload: dict) -> SearchImportResult:
    timestamp = _now()
    raw_snapshot = _raw_json(snapshot_payload)
    scored = score_offer(
        {
            "title": offer.title,
            "company": offer.company,
            "location": offer.location,
            "description": offer.description,
        }
    )
    existing = _find_existing(conn, offer)
    if existing:
        conn.execute(
            """
            UPDATE offers
            SET title = ?, company = ?, location = ?, url = ?, source = ?, description = ?,
                salary = ?, remote = ?, contract_type = ?, score = ?, score_reasons = ?,
                browser_url = ?, apply_url = ?, is_active = 1, last_seen_at = ?,
                raw_browser_snapshot = ?, raw_extracted_text = ?, updated_at = CURRENT_TIMESTAMP
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
                scored.score,
                "\n".join(scored.reasons),
                offer.browser_url,
                offer.apply_url,
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
    return SearchImportResult(offer_id, True, offer.title, int(row["score"] if row else scored.score), offer.browser_url)


def search_offers(
    conn,
    *,
    query: str,
    location: str = "",
    radius: int = 0,
    contract: str = "",
    browser: BrowserLike | None = None,
    site: str = DEFAULT_SITE,
    profile: str = DEFAULT_PROFILE,
) -> list[SearchImportResult]:
    client = _browser(browser)
    normalized_query = _normalize_query(query)
    url = build_search_url(normalized_query, location, radius, contract)
    client.lifecycle_open(url, site=site, profile=profile)
    snapshot = client.snapshot(label="ft-search", site=site, profile=profile)
    extracted = extract_offers(snapshot.payload)
    if not extracted:
        extracted = _extract_browser_dom_offers(client, site=site, profile=profile)
    relevant = [offer for offer in extracted if _offer_is_relevant(offer, query=normalized_query, contract=contract)]
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
        browser=browser,
        site=site,
        profile=profile,
    )
    update_saved_search_last_run(conn, int(saved["id"]), _now())
    return results


def _offer_url(offer) -> str:
    return offer["browser_url"] or offer["url"]


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
    client.open(url, site=site, profile=profile)
    snapshot = client.snapshot(label=f"ft-offer-{offer_id}", site=site, profile=profile)
    detail = extract_offer_detail(snapshot.payload)
    timestamp = _now()
    conn.execute(
        """
        UPDATE offers
        SET is_active = ?, last_refreshed_at = ?, raw_browser_snapshot = ?,
            raw_extracted_text = ?, apply_url = COALESCE(NULLIF(?, ''), apply_url), updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (1 if detail.is_active else 0, timestamp, _raw_json(snapshot.payload), detail.text, detail.apply_url, offer_id),
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


def _has_application(conn, offer_id: int) -> bool:
    row = conn.execute("SELECT 1 FROM applications WHERE offer_id = ? LIMIT 1", (offer_id,)).fetchone()
    return row is not None


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
    already_applied = _has_application(conn, offer_id) or offer["status"] == "applied"
    stored_active = bool(offer["is_active"])
    url = _offer_url(offer)
    detail_active = stored_active
    has_apply_signal = False
    if url:
        client = _browser(browser)
        client.open(url, site=site, profile=profile)
        snapshot = client.snapshot(label=f"ft-apply-check-{offer_id}", site=site, profile=profile)
        detail = extract_offer_detail(snapshot.payload)
        detail_active = detail.is_active
        has_apply_signal = detail.can_apply
        conn.execute(
            """
            UPDATE offers
            SET is_active = ?, last_refreshed_at = ?, raw_browser_snapshot = ?, raw_extracted_text = ?,
                apply_url = COALESCE(NULLIF(?, ''), apply_url), updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (1 if detail.is_active else 0, _now(), _raw_json(snapshot.payload), detail.text, detail.apply_url, offer_id),
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
    can_apply = detail_active and not already_applied and has_apply_signal
    add_offer_event(
        conn,
        offer_id,
        event_type="apply_check",
        message="Candidature possible" if can_apply else "Candidature non disponible",
        payload_json=json.dumps({"can_apply": can_apply, "reasons": reasons}, ensure_ascii=False),
    )
    return ApplyCheckResult(offer_id, can_apply, detail_active, already_applied, has_apply_signal, reasons, url)


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-").lower()
    return slug[:80] or "offre"


def draft_application(conn, offer_id: int, *, drafts_dir: str | Path | None = None) -> DraftResult:
    result = create_application_draft(conn, offer_id, drafts_dir=drafts_dir)
    return DraftResult(result.offer_id, result.application_id, result.draft_path)


def open_offer(conn, offer_id: int, *, browser: BrowserLike | None = None, site: str = DEFAULT_SITE, profile: str = DEFAULT_PROFILE) -> str:
    offer = get_offer(conn, offer_id)
    if offer is None:
        raise ValueError(f"Offre introuvable: {offer_id}")
    url = _offer_url(offer)
    if not url:
        raise ValueError(f"Offre #{offer_id} sans URL navigateur")
    _browser(browser).open(url, site=site, profile=profile)
    add_offer_event(conn, offer_id, event_type="opened", message=url)
    return url
