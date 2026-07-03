"""jobs.ch job scraper — Major Swiss job portal.

jobs.ch is one of Switzerland's largest job boards. Uses HTTP scraping
with JSON-LD structured data extraction.
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

logger = get_logger("sources.jobs_ch")

JOBS_CH_SEARCH_URL = "https://www.jobs.ch/offers/"


@dataclass(frozen=True)
class JobsChOffer:
    title: str
    company: str
    location: str
    url: str
    description: str
    contract_type: str = ""
    salary: str = ""


def _build_search_url(query: str, location: str = "", page: int = 1) -> str:
    params: dict[str, object] = {
        "term": query,
        "page": page,
    }
    if location:
        params["location"] = location
    return f"{JOBS_CH_SEARCH_URL}?{urllib.parse.urlencode(params)}"


@with_retry(max_retries=2, base_delay=1.0, retryable_exceptions=(urllib.error.URLError, OSError))  # type: ignore[arg-type,misc]
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


def _parse_offers_from_html(html: str) -> list[JobsChOffer]:
    """Parse jobs.ch search results from HTML."""
    offers: list[JobsChOffer] = []

    # Strategy 1: JSON-LD
    json_pattern = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
    for match in json_pattern.finditer(html):
        try:
            data = json.loads(match.group(1))
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") in ("JobPosting", "jobPosting"):
                    offers.append(
                        JobsChOffer(
                            title=str(item.get("name", "") or ""),
                            company=str(item.get("hiringOrganization", {}).get("name", "") or ""),
                            location=str(
                                item.get("jobLocation", {}).get("address", {}).get("addressLocality", "") or ""
                            ),
                            url=str(item.get("url", "") or ""),
                            description=str(item.get("description", "") or "")[:500],
                            contract_type=str(item.get("employmentType", "") or ""),
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
            jobs = page_props.get("jobs", []) or page_props.get("offers", []) or page_props.get("searchResults", [])
            for job in jobs:
                offers.append(
                    JobsChOffer(
                        title=str(job.get("title", "") or ""),
                        company=str(job.get("company", {}).get("name", "") or job.get("company", "") or ""),
                        location=str(job.get("location", "") or job.get("city", "") or ""),
                        url=str(job.get("url", "") or job.get("link", "") or ""),
                        description=str(job.get("description", "") or "")[:500],
                        contract_type=str(job.get("contractType", "") or ""),
                        salary=str(job.get("salary", "") or ""),
                    )
                )
        except (json.JSONDecodeError, TypeError):
            continue

    if offers:
        return offers

    # Strategy 3: regex fallback
    card_pattern = re.compile(
        r'<a[^>]+href="(https?://www\.jobs\.ch/offers/[^"]+)"[^>]*>.*?'
        r"<h[23][^>]*>([^<]+)</h[23]>.*?"
        r"(?:class=\"[^\"]*company[^\"]*\"[^>]*>([^<]+)<)?.*?"
        r"(?:class=\"[^\"]*location[^\"]*\"[^>]*>([^<]+)<)?",
        re.S | re.I,
    )
    for url, title, company, location in card_pattern.findall(html):
        offers.append(
            JobsChOffer(
                title=title.strip(),
                company=(company or "").strip(),
                location=(location or "").strip(),
                url=url.strip(),
                description="",
            )
        )

    return offers


def search_jobs_ch(
    query: str,
    location: str = "",
    max_results: int = 50,
) -> list[JobsChOffer]:
    """Search jobs.ch for job offers."""
    all_offers: list[JobsChOffer] = []
    page = 1
    while len(all_offers) < max_results:
        url = _build_search_url(query, location, page)
        try:
            html = _fetch_html(url)  # type: ignore[misc]
        except Exception as exc:
            logger.warning("jobs.ch search failed (page %d): %s", page, exc)
            break
        offers = _parse_offers_from_html(html)
        if not offers:
            break
        all_offers.extend(offers)
        page += 1
        if page > 10:
            break

    return all_offers[:max_results]
