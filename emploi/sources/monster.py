"""Monster France job scraper — searches via Monster's public search page.

Monster is a major global job board with a strong French presence.
Uses HTTP scraping of the search results page.
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

logger = get_logger("sources.monster")

MONSTER_SEARCH_URL = "https://www.monster.fr/emploi/recherche"


@dataclass(frozen=True)
class MonsterOffer:
    title: str
    company: str
    location: str
    url: str
    description: str
    contract_type: str = ""
    salary: str = ""


def _build_search_url(query: str, location: str = "", page: int = 1) -> str:
    params: dict[str, object] = {
        "q": query,
        "page": page,
    }
    if location:
        params["where"] = location
    return f"{MONSTER_SEARCH_URL}?{urllib.parse.urlencode(params)}"


@with_retry(max_retries=2, base_delay=1.0, retryable_exceptions=(urllib.error.URLError, OSError))
def _fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
            "Accept": "text/html",
            "Accept-Language": "fr-FR,fr;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")


def _parse_offers_from_html(html: str) -> list[MonsterOffer]:
    """Parse Monster search results from HTML."""
    offers: list[MonsterOffer] = []

    # Try embedded JSON-LD or data attributes
    json_pattern = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
    for match in json_pattern.finditer(html):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, list):
                for item in data:
                    if item.get("@type") == "JobPosting":
                        offers.append(
                            MonsterOffer(
                                title=str(item.get("name", "") or ""),
                                company=str(item.get("hiringOrganization", {}).get("name", "") or ""),
                                location=str(
                                    item.get("jobLocation", {}).get("address", {}).get("addressLocality", "") or ""
                                ),
                                url=str(item.get("url", "") or ""),
                                description=str(item.get("description", "") or "")[:500],
                            )
                        )
        except (json.JSONDecodeError, TypeError):
            continue

    if offers:
        return offers

    # Fallback: regex-based parsing
    card_pattern = re.compile(
        r'<a[^>]+href="(https?://www\.monster\.fr/emploi/[^"]+)"[^>]*>.*?'
        r'class="[^"]*title[^"]*"[^>]*>([^<]+)</.*?'
        r'class="[^"]*company[^"]*"[^>]*>([^<]+)<.*?'
        r'class="[^"]*location[^"]*"[^>]*>([^<]+)<',
        re.S | re.I,
    )
    for url, title, company, location in card_pattern.findall(html):
        offers.append(
            MonsterOffer(
                title=title.strip(),
                company=company.strip(),
                location=location.strip(),
                url=url.strip(),
                description="",
            )
        )

    return offers


def search_monster(
    query: str,
    location: str = "",
    max_results: int = 50,
) -> list[MonsterOffer]:
    """Search Monster France for job offers."""
    all_offers: list[MonsterOffer] = []
    page = 1
    while len(all_offers) < max_results:
        url = _build_search_url(query, location, page)
        try:
            html = _fetch_html(url)
        except Exception as exc:
            logger.warning("Monster search failed (page %d): %s", page, exc)
            break
        offers = _parse_offers_from_html(html)
        if not offers:
            break
        all_offers.extend(offers)
        page += 1
        if page > 10:
            break

    return all_offers[:max_results]
