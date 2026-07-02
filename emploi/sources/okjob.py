"""okjob.ch job scraper — Swiss job aggregator by JobCloud.

okjob.ch is a WordPress-based Swiss job aggregator. Job listings are rendered
as <article class="post"> elements with links to detail pages.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from emploi.logging import get_logger
from emploi.retry import with_retry

logger = get_logger("sources.okjob")

OKJOB_SEARCH_URL = "https://www.okjob.ch/offres-demplois/"


@dataclass(frozen=True)
class OkjobOffer:
    title: str
    company: str
    location: str
    url: str
    description: str
    contract_type: str = ""
    salary: str = ""


def _build_search_url(query: str, location: str = "", page: int = 1) -> str:
    url = OKJOB_SEARCH_URL
    if location:
        # okjob uses city slugs in the URL path
        slug = re.sub(r"[^a-z0-9]+", "-", location.strip().lower()).strip("-")
        url = f"{url}{slug}/"
    params: dict[str, object] = {"search": query}
    if page > 1:
        params["jp"] = page
    return f"{url}?{urllib.parse.urlencode(params)}"


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


def _parse_offers_from_html(html: str) -> list[OkjobOffer]:
    """Parse okjob.ch search results from HTML.

    okjob uses WordPress with <article class="post"> job cards.
    Each card has: <a href="..."> → <h3>title</h3> + <span class="status">city / type</span>
    """
    offers: list[OkjobOffer] = []

    # Parse WordPress article cards
    # Pattern: <article class="post"> ... <a href="URL"> ... <h3>TITLE</h3> ... <span class="status">LOCATION / TYPE</span>
    card_pattern = re.compile(
        r'<article\s+class="post"[^>]*>\s*'
        r'<a\s+href="([^"]+)"[^>]*>.*?'
        r"<h3[^>]*>([^<]+)</h3>.*?"
        r'<span\s+class="status"[^>]*>([^<]*)</span>.*?'
        r'(?:<div\s+class="detail"[^>]*>\s*<p>([^<]*)</p>)?',
        re.S | re.I,
    )
    for url, title, status, description in card_pattern.findall(html):
        # status is typically "City / ContractType"
        parts = [p.strip() for p in status.split("/")]
        loc = parts[0] if parts else ""
        contract = parts[1] if len(parts) > 1 else ""
        offers.append(
            OkjobOffer(
                title=title.strip(),
                company="",
                location=loc,
                url=url.strip(),
                description=(description or "").strip()[:500],
                contract_type=contract,
            )
        )

    return offers


def search_okjob(
    query: str,
    location: str = "",
    max_results: int = 50,
) -> list[OkjobOffer]:
    """Search okjob.ch for job offers."""
    all_offers: list[OkjobOffer] = []
    page = 1
    while len(all_offers) < max_results:
        url = _build_search_url(query, location, page)
        try:
            html = _fetch_html(url)
        except Exception as exc:
            logger.warning("okjob search failed (page %d): %s", page, exc)
            break
        offers = _parse_offers_from_html(html)
        if not offers:
            break
        all_offers.extend(offers)
        page += 1
        if page > 10:
            break

    return all_offers[:max_results]
