"""APEC job scraper — searches via the APEC public search API.

APEC (Association Pour l'Emploi des Cadres) is a major French job board for
executive and professional positions. Uses a public JSON API.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from emploi.logging import get_logger
from emploi.retry import with_retry

logger = get_logger("sources.apec")

APEC_SEARCH_URL = "https://www.apec.fr/candidat/recherche-emploi/offres-emploi.html"
APEC_API_URL = "https://www.apec.fr/bin/apec/search/offres"


@dataclass(frozen=True)
class ApecOffer:
    title: str
    company: str
    location: str
    url: str
    description: str
    contract_type: str = ""
    salary: str = ""


def _build_search_url(query: str, location: str = "", page: int = 1) -> str:
    params: dict[str, object] = {
        "motsCles": query,
        "page": page,
        "nbOffresParPage": 20,
    }
    if location:
        params["lieu"] = location
    return f"{APEC_API_URL}?{urllib.parse.urlencode(params)}"


@with_retry(max_retries=2, base_delay=1.0, retryable_exceptions=(urllib.error.URLError, OSError))
def _fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
            "Accept": "application/json, text/html",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")


def _parse_offers_from_html(html: str) -> list[ApecOffer]:
    """Parse APEC search results from HTML/JSON response."""
    offers: list[ApecOffer] = []

    # Try JSON first (API response)
    try:
        data = json.loads(html)
        if isinstance(data, dict) and "offres" in data:
            for item in data["offres"]:
                offers.append(
                    ApecOffer(
                        title=str(item.get("intitule", "") or ""),
                        company=str(item.get("entreprise", {}).get("nom", "") or ""),
                        location=str(item.get("lieu", "") or ""),
                        url=str(item.get("urlOffre", "") or ""),
                        description=str(item.get("description", "") or ""),
                        contract_type=str(item.get("typeContratLibelle", "") or ""),
                        salary=str(item.get("salaire", {}).get("libelle", "") or ""),
                    )
                )
            return offers
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: regex-based HTML parsing
    title_pattern = re.compile(r'<h2[^>]*class="[^"]*title[^"]*"[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', re.I)
    matches = title_pattern.findall(html)
    for url, title in matches:
        if not url.startswith("http"):
            url = "https://www.apec.fr" + url
        offers.append(
            ApecOffer(
                title=title.strip(),
                company="",
                location="",
                url=url,
                description="",
            )
        )

    return offers


def search_apec(
    query: str,
    location: str = "",
    max_results: int = 50,
) -> list[ApecOffer]:
    """Search APEC for job offers.

    Returns a list of ApecOffer dataclass instances.
    """
    all_offers: list[ApecOffer] = []
    page = 1
    while len(all_offers) < max_results:
        url = _build_search_url(query, location, page)
        try:
            html = _fetch_html(url)
        except Exception as exc:
            logger.warning("APEC search failed (page %d): %s", page, exc)
            break
        offers = _parse_offers_from_html(html)
        if not offers:
            break
        all_offers.extend(offers)
        page += 1
        if page > 10:  # safety limit
            break

    return all_offers[:max_results]
