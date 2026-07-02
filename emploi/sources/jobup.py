"""jobup.ch job scraper — Swiss job board by JobCloud.

jobup.ch is a React SPA with server-side rendering. Job listings are embedded
in the page as server-rendered JSON and use data-cy attributes for test automation.
Search URL: /fr/emplois/?term={keyword}&location={city}&page={n}
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

logger = get_logger("sources.jobup")

JOBUP_SEARCH_URL = "https://www.jobup.ch/fr/emplois/"


@dataclass(frozen=True)
class JobupOffer:
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
    return f"{JOBUP_SEARCH_URL}?{urllib.parse.urlencode(params)}"


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


def _parse_offers_from_html(html: str) -> list[JobupOffer]:
    """Parse jobup.ch search results from HTML.

    jobup uses React SSR. Job data is embedded in the page JavaScript
    as server-rendered JSON. Cards use data-cy="item-container" and
    data-cy="job-link" attributes.
    """
    offers: list[JobupOffer] = []

    # Strategy 1: Embedded JSON in script tags (SSR data)
    # jobup embeds job listings as a JSON array in a <script> tag
    embedded_pattern = re.compile(r"<script[^>]*>\s*window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", re.S)
    for match in embedded_pattern.finditer(html):
        try:
            data = json.loads(match.group(1))
            jobs = data.get("searchResults", {}).get("hits", []) or data.get("jobs", []) or data.get("offers", [])
            for job in jobs:
                offers.append(_jobup_offer_from_dict(job))
        except (json.JSONDecodeError, TypeError):
            continue

    if offers:
        return offers

    # Strategy 2: data-cy="job-link" HTML cards
    # Each card: <a data-cy="job-link" id="vacancy-link-{UUID}" href="/fr/emplois/detail/{UUID}/" title="{title}">
    card_pattern = re.compile(
        r'<a[^>]*data-cy="job-link"[^>]*href="([^"]+)"[^>]*title="([^"]*)"',
        re.S | re.I,
    )
    for href, title in card_pattern.findall(html):
        url = href if href.startswith("http") else f"https://www.jobup.ch{href}"
        offers.append(
            JobupOffer(
                title=title.strip(),
                company="",
                location="",
                url=url,
                description="",
            )
        )

    if offers:
        return offers

    # Strategy 3: JSON-LD (only on detail pages, but check anyway)
    json_pattern = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
    for match in json_pattern.finditer(html):
        try:
            data = json.loads(match.group(1))
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") in ("JobPosting", "jobPosting"):
                    salary = ""
                    if isinstance(item.get("baseSalary"), dict):
                        val = item["baseSalary"].get("value", {})
                        salary = f"{val.get('minValue', '')}-{val.get('maxValue', '')} {item['baseSalary'].get('currency', 'CHF')}"
                    offers.append(
                        JobupOffer(
                            title=str(item.get("title", "") or item.get("name", "") or ""),
                            company=str(item.get("hiringOrganization", {}).get("name", "") or ""),
                            location=str(
                                item.get("jobLocation", {}).get("address", {}).get("addressLocality", "") or ""
                            ),
                            url=str(item.get("url", "") or ""),
                            description=str(item.get("description", "") or "")[:500],
                            contract_type=str(item.get("employmentType", "") or ""),
                            salary=salary,
                        )
                    )
        except (json.JSONDecodeError, TypeError):
            continue

    return offers


def _jobup_offer_from_dict(job: dict) -> JobupOffer:
    """Convert a jobup job dict to a JobupOffer."""
    company = ""
    if isinstance(job.get("company"), dict):
        company = str(job["company"].get("name", "") or "")
    elif isinstance(job.get("company"), str):
        company = job["company"]

    location = str(job.get("place", "") or job.get("location", "") or "")
    if not location and isinstance(job.get("locations"), list) and job["locations"]:
        location = str(job["locations"][0].get("city", "") or "")

    salary = ""
    if isinstance(job.get("salary"), dict):
        s = job["salary"]
        currency = s.get("currency", "CHF")
        rng = s.get("range", {})
        min_v = rng.get("minValue", "")
        max_v = rng.get("maxValue", "")
        if min_v or max_v:
            salary = f"{min_v}-{max_v} {currency}"

    job_id = str(job.get("id", "") or "")
    url = f"https://www.jobup.ch/fr/emplois/detail/{job_id}/" if job_id else ""

    return JobupOffer(
        title=str(job.get("title", "") or ""),
        company=company,
        location=location,
        url=url,
        description=str(job.get("description", "") or job.get("snippet", "") or "")[:500],
        contract_type=str(job.get("employmentType", "") or job.get("contractType", "") or ""),
        salary=salary,
    )


def search_jobup(
    query: str,
    location: str = "",
    max_results: int = 50,
) -> list[JobupOffer]:
    """Search jobup.ch for job offers."""
    all_offers: list[JobupOffer] = []
    page = 1
    while len(all_offers) < max_results:
        url = _build_search_url(query, location, page)
        try:
            html = _fetch_html(url)
        except Exception as exc:
            logger.warning("jobup search failed (page %d): %s", page, exc)
            break
        offers = _parse_offers_from_html(html)
        if not offers:
            break
        all_offers.extend(offers)
        page += 1
        if page > 10:
            break

    return all_offers[:max_results]
