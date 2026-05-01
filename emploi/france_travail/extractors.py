from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

BASE_URL = "https://candidat.francetravail.fr"
DETAIL_RE = re.compile(r"(?:/offres/recherche/detail/|offre/)([A-Za-z0-9_-]+)")
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class ExtractedOffer:
    title: str
    company: str = ""
    location: str = ""
    browser_url: str = ""
    external_id: str = ""
    description: str = ""
    salary: str = ""
    remote: str = ""
    contract_type: str = ""
    apply_url: str = ""
    raw_text: str = ""


@dataclass(frozen=True)
class OfferDetail:
    is_active: bool
    can_apply: bool
    text: str
    apply_url: str = ""


def _stringify_payload(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("text", "markdown", "html", "content"):
            if payload.get(key):
                return str(payload[key])
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return str(payload or "")


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _first(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value:
            return _clean_text(value)
    return ""


def _absolute_url(url: str) -> str:
    if not url:
        return ""
    return urljoin(BASE_URL, html.unescape(url))


def external_id_from_url(url: str) -> str:
    match = DETAIL_RE.search(url or "")
    return match.group(1) if match else ""


def _offer_from_mapping(card: dict[str, Any], fallback_text: str = "") -> ExtractedOffer | None:
    html_block = str(card.get("html") or "")
    title = _first(card, "title", "titre", "name", "label")
    url = _absolute_url(_first(card, "url", "href", "browser_url", "link"))
    if html_block:
        link_match = re.search(r"href=[\"']([^\"']*(?:/offres/recherche/detail/|offre/)[^\"']*)[\"']", html_block, re.I)
        title_match = re.search(r"class=[\"'][^\"']*media-heading-title[^\"']*[\"'][^>]*>(.*?)<", html_block, re.I | re.S)
        company_location_match = re.search(r"<p[^>]*class=[\"']subtext[\"'][^>]*>(.*?)</p>", html_block, re.I | re.S)
        description_match = re.search(r"<p[^>]*class=[\"']description[\"'][^>]*>(.*?)</p>", html_block, re.I | re.S)
        contract_match = re.search(r"<p[^>]*class=[\"']contrat[^\"']*[\"'][^>]*>(.*?)</p>", html_block, re.I | re.S)
        if link_match:
            url = _absolute_url(link_match.group(1))
        if title_match and not title:
            title = _clean_text(title_match.group(1))
        company = ""
        location = ""
        if company_location_match:
            parts = [part.strip(" -\xa0") for part in _clean_text(company_location_match.group(1)).split(" - ", 1)]
            company = parts[0] if parts else ""
            location = parts[1] if len(parts) > 1 else ""
        description = _clean_text(description_match.group(1)) if description_match else ""
        contract_type = _clean_text(contract_match.group(1)) if contract_match else ""
    else:
        company = _first(card, "company", "entreprise", "companyName", "employer")
        location = _first(card, "location", "lieu", "place")
        subtext = _first(card, "subtext")
        if subtext and not location:
            parts = [part.strip(" -\xa0") for part in subtext.split(" - ", 1)]
            company = company or (parts[0] if parts else "")
            location = parts[1] if len(parts) > 1 else ""
        description = _first(card, "description", "summary", "snippet")
        contract_type = _first(card, "contract_type", "contract", "typeContrat")
    raw_text = _clean_text(card.get("text") or card.get("description") or card.get("innerText") or fallback_text or json.dumps(card, ensure_ascii=False))
    if not title:
        title = _infer_title(raw_text)
    if not title and not url:
        return None
    apply_url = _absolute_url(_first(card, "apply_url", "application_url", "candidature_url"))
    return ExtractedOffer(
        title=title or "Offre France Travail",
        company=company,
        location=location,
        browser_url=url,
        external_id=_first(card, "external_id", "id", "reference") or external_id_from_url(url),
        description=description or raw_text,
        salary=_first(card, "salary", "salaire"),
        remote=_first(card, "remote", "teletravail"),
        contract_type=contract_type,
        apply_url=apply_url,
        raw_text=raw_text,
    )


def _iter_card_mappings(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    cards: list[dict[str, Any]] = []
    for key in ("cards", "offers", "results", "items", "jobs"):
        value = payload.get(key)
        if isinstance(value, list):
            cards.extend(item for item in value if isinstance(item, dict))
    if cards:
        return cards
    html_text = str(payload.get("html", ""))
    if html_text:
        for match in re.finditer(r"<li\b[^>]*class=[\"'][^\"']*\bresult\b[^\"']*[\"'][^>]*>(.*?)</li>", html_text, re.I | re.S):
            cards.append({"html": match.group(0), "text": _clean_text(match.group(0))})
    return cards


def _infer_title(text: str) -> str:
    lines = [line.strip(" -•\t") for line in re.split(r"[\n\r]+", text) if line.strip()]
    return lines[0][:120] if lines else ""


def _extract_html_articles(html_text: str) -> list[ExtractedOffer]:
    offers: list[ExtractedOffer] = []
    article_re = re.compile(r"<(article|li|div)\b[^>]*(?:offre|offer|result|card)[^>]*>(.*?)</\1>", re.I | re.S)
    blocks = [m.group(2) for m in article_re.finditer(html_text)] or [html_text]
    for block in blocks:
        link_match = re.search(r"href=[\"']([^\"']*(?:/offres/recherche/detail/|offre/)[^\"']*)[\"']", block, re.I)
        if not link_match:
            continue
        title_match = re.search(r"<h[1-3][^>]*>(.*?)</h[1-3]>", block, re.I | re.S)
        company_match = re.search(r"class=[\"'][^\"']*(?:company|entreprise)[^\"']*[\"'][^>]*>(.*?)<", block, re.I | re.S)
        location_match = re.search(r"class=[\"'][^\"']*(?:location|lieu)[^\"']*[\"'][^>]*>(.*?)<", block, re.I | re.S)
        text = _clean_text(block)
        url = _absolute_url(link_match.group(1))
        offers.append(
            ExtractedOffer(
                title=_clean_text(title_match.group(1)) if title_match else _infer_title(text),
                company=_clean_text(company_match.group(1)) if company_match else "",
                location=_clean_text(location_match.group(1)) if location_match else "",
                browser_url=url,
                external_id=external_id_from_url(url),
                description=text,
                raw_text=text,
            )
        )
    return offers


def _extract_links_from_text(text: str) -> list[ExtractedOffer]:
    offers: list[ExtractedOffer] = []
    for match in re.finditer(r"https?://\S*(?:/offres/recherche/detail/|offre/)\S+|/offres/recherche/detail/[A-Za-z0-9_-]+", text):
        url = _absolute_url(match.group(0).rstrip(".,;)]"))
        start = max(0, match.start() - 160)
        context = _clean_text(text[start : match.end() + 160])
        offers.append(
            ExtractedOffer(
                title=_infer_title(context) or "Offre France Travail",
                browser_url=url,
                external_id=external_id_from_url(url),
                description=context,
                raw_text=context,
            )
        )
    return offers


def extract_offers(snapshot: Any) -> list[ExtractedOffer]:
    """Extract offer summaries from a Managed Browser snapshot payload/HTML/text."""
    text = _stringify_payload(snapshot)
    offers: list[ExtractedOffer] = []
    for card in _iter_card_mappings(snapshot):
        offer = _offer_from_mapping(card, fallback_text=text)
        if offer:
            offers.append(offer)
    html_text = str(snapshot.get("html", "")) if isinstance(snapshot, dict) else text
    if html_text and "<" in html_text:
        offers.extend(_extract_html_articles(html_text))
    if not offers:
        offers.extend(_extract_links_from_text(text))

    deduped: list[ExtractedOffer] = []
    seen: set[str] = set()
    for offer in offers:
        key = offer.external_id or offer.browser_url or f"{offer.title}|{offer.company}|{offer.location}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(offer)
    return deduped


def extract_offer_detail(snapshot: Any) -> OfferDetail:
    text = _clean_text(_stringify_payload(snapshot))
    lower = text.lower()
    unavailable_markers = (
        "n'est plus disponible",
        "offre expirée",
        "offre pourvue",
        "aucune offre trouvée",
        "page introuvable",
        "404",
    )
    is_active = not any(marker in lower for marker in unavailable_markers)
    apply_signal = any(marker in lower for marker in ("candidater", "postuler", "envoyer ma candidature", "je postule"))
    apply_url = ""
    if isinstance(snapshot, dict):
        apply_url = _absolute_url(_first(snapshot, "apply_url", "application_url", "candidature_url"))
    return OfferDetail(is_active=is_active, can_apply=is_active and apply_signal, text=text, apply_url=apply_url)
