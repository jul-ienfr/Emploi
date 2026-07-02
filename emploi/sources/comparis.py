"""comparis.ch job scraper — Swiss job comparison platform.

comparis.ch aggregates job listings and allows comparison across multiple
Swiss job boards. Uses HTTP scraping with JSON-LD extraction.
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

logger = get_logger("sources.comparis")

COMPARIS_SEARCH_URL = "https://www.comparis.ch/stellenangebote/suche"


@dataclass(frozen=True)
class ComparisOffer:
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
        params["loc"] = location
    return f"{COMPARIS_SEARCH_URL}?{urllib.parse.urlencode(params)}"


@with_retry(max_retries=2, base_delay=1.0, retryable_exceptions=(urllib.error.URLError, OSError))
def _fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
            "Accept": "text/html",
            "Accept-Language": "de-CH,de;q=0.9,fr;q=0.8,en;q=0.7",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")


def _parse_offers_from_html(html: str) -> list[ComparisOffer]:
    """Parse comparis.ch search results from HTML."""
    offers: list[ComparisOffer] = []

    # Strategy 1: JSON-LD
    json_pattern = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
    for match in json_pattern.finditer(html):
        try:
            data = json.loads(match.group(1))
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") in ("JobPosting", "jobPosting"):
                    offers.append(
                        ComparisOffer(
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

    # Strategy 2: __NEXT_DATA__
    next_data_pattern = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
    for match in next_data_pattern.finditer(html):
        try:
            data = json.loads(match.group(1))
            page_props = data.get("props", {}).get("pageProps", {})
            jobs = page_props.get("jobs", []) or page_props.get("offers", []) or page_props.get("results", [])
            for job in jobs:
                offers.append(
                    ComparisOffer(
                        title=str(job.get("title", "") or job.get("name", "") or ""),
                        company=str(job.get("company", {}).get("name", "") or job.get("company", "") or ""),
                        location=str(job.get("location", "") or job.get("city", "") or ""),
                        url=str(job.get("url", "") or job.get("link", "") or ""),
                        description=str(job.get("description", "") or "")[:500],
                        contract_type=str(job.get("contractType", "") or job.get("type", "") or ""),
                        salary=str(job.get("salary", "") or ""),
                    )
                )
        except (json.JSONDecodeError, TypeError):
            continue

    if offers:
        return offers

    # Strategy 3: regex fallback
    card_pattern = re.compile(
        r'<a[^>]+href="(https?://www\.comparis\.ch/[^"]*stellenangebote[^"]*)"[^>]*>.*?'
        r"<h[23][^>]*>([^<]+)</h[23]>.*?"
        r"(?:class=\"[^\"]*company[^\"]*\"[^>]*>([^<]+)<)?.*?"
        r"(?:class=\"[^\"]*location[^\"]*\"[^>]*>([^<]+)<)?",
        re.S | re.I,
    )
    for url, title, company, location in card_pattern.findall(html):
        offers.append(
            ComparisOffer(
                title=title.strip(),
                company=(company or "").strip(),
                location=(location or "").strip(),
                url=url.strip(),
                description="",
            )
        )

    return offers


def search_comparis(
    query: str,
    location: str = "",
    max_results: int = 50,
) -> list[ComparisOffer]:
    """Search comparis.ch for job offers."""
    all_offers: list[ComparisOffer] = []
    page = 1
    while len(all_offers) < max_results:
        url = _build_search_url(query, location, page)
        try:
            html = _fetch_html(url)
        except Exception as exc:
            logger.warning("comparis search failed (page %d): %s", page, exc)
            break
        offers = _parse_offers_from_html(html)
        if not offers:
            break
        all_offers.extend(offers)
        page += 1
        if page > 10:
            break

    return all_offers[:max_results]
