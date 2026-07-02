from __future__ import annotations

import html as html_module
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from emploi.browser.models import DEFAULT_PROFILE, DEFAULT_SITE
from emploi.db import add_offer, add_offer_event, get_offer
from emploi.france_travail.flows import SearchImportResult
from emploi.scoring import score_offer
from emploi.utils import _matches_terms

HELLOWORK_BASE_URL = "https://www.hellowork.com"
SEARCH_URL = f"{HELLOWORK_BASE_URL}/fr-fr/emploi/recherche.html"
EXTERNAL_SOURCE = "hellowork"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _raw_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _browser(browser: Any = None) -> Any:
    if browser is not None:
        return browser
    from emploi.browser.client import ManagedBrowserClient

    return ManagedBrowserClient()


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------

def _extract_positive_query(query: str) -> str:
    """Extract only positive terms from a query (strip exclusions like -Mécanicien).

    Example: 'poids lourd -SPL -"super poids lourd" -Formateur -Mécanicien'
    → 'poids lourd'
    """
    positives: list[str] = []
    # Walk through query token by token
    i = 0
    while i < len(query):
        # Skip whitespace
        if query[i].isspace():
            i += 1
            continue
        # Check for negative prefix
        neg = False
        if query[i] == "-":
            neg = True
            i += 1
            if i >= len(query):
                break
        # Quoted term
        if query[i] == '"':
            end = query.find('"', i + 1)
            if end == -1:
                break
            term = query[i + 1 : end]
            if not neg:
                positives.append(term)
            i = end + 1
        # Unquoted word
        elif query[i].isalnum() or query[i] == "_":
            j = i
            while j < len(query) and (query[j].isalnum() or query[j] == "_"):
                j += 1
            term = query[i:j]
            if not neg:
                positives.append(term)
            i = j
        else:
            i += 1
    return " ".join(positives)


def build_hellowork_search_url(query: str, location: str = "", contract: str = "") -> str:
    """Build a HelloWork search URL from query, location and contract type.

    Only positive terms are sent to HW (exclusions are applied after parsing).
    Pattern: https://www.hellowork.com/fr-fr/emploi/recherche.html?k={keywords}&l={location}&c={contract}
    """
    positive_query = _extract_positive_query(query)
    params: dict[str, str] = {"k": positive_query}
    if location:
        params["l"] = location
    if contract:
        params["c"] = contract
    return f"{SEARCH_URL}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# HTML parsing (extract offers from search results page)
# ---------------------------------------------------------------------------

def _parse_salary(aria_label: str) -> str:
    """Extract salary from aria-label like 'avec un salaire de 2 700 - 3 300 € / mois'."""
    match = re.search(r"avec un salaire de ([^,]+)", aria_label)
    if match:
        return match.group(1).strip()
    return ""


# ---------------------------------------------------------------------------
# HTML parsing (extract offers from search results page)
# ---------------------------------------------------------------------------

SERP_CARD_RE = re.compile(
    r'<div\b[^>]*data-cy="serpCard"[^>]*>(.*?)</div>\s*(?=<div\b[^>]*data-cy="serpCard"|</div>|\Z)',
    re.I | re.S,
)
ANALYTICS_JSON_RE = re.compile(r'data-analytics-values-param=\'([^\']+)\'')
OFFER_TITLE_LINK_RE = re.compile(
    r'<a\b[^>]*data-cy="offerTitle"[^>]*>.*?</a>', re.I | re.S
)
TITLE_ATTR_RE = re.compile(r'title="([^"]*)"')
HREF_RE = re.compile(r'href="([^"]*)"')
ARIA_LABEL_RE = re.compile(r'aria-label="([^"]*)"')
LOCATION_CARD_RE = re.compile(r'<div\b[^>]*data-cy="localisationCard"[^>]*>(.*?)</div>', re.I | re.S)
CONTRACT_CARD_RE = re.compile(r'<div\b[^>]*data-cy="contractCard"[^>]*>(.*?)</div>', re.I | re.S)


def _get_text_between_tags(match: re.Match | None) -> str:
    """Extract clean text content from a regex match."""
    if not match:
        return ""
    content = match.group(1)
    # Remove inner tags
    content = re.sub(r"<[^>]+>", " ", content)
    return re.sub(r"\s+", " ", content).strip()


def extract_hellowork_offers(html_text: str) -> list[dict[str, str]]:
    """Parse HelloWork search result HTML and extract offer data.

    Returns a list of dicts with keys:
        external_id, title, company, location, contract_type, browser_url, salary
    """
    if not html_text:
        return []

    # Find all serpCard blocks using a non-greedy approach
    cards: list[str] = []
    pos = 0
    while True:
        start = html_text.find('<div', pos)
        if start == -1:
            break
        # Check if this div is a serpCard
        chunk = html_text[start:start + 1000]
        if 'data-cy="serpCard"' in chunk:
            # Find the matching closing </div>
            depth = 1
            i = start + len('<div')
            while i < len(html_text) and depth > 0:
                if html_text[i:i+4] == '<div' and html_text[i+4] not in '0123456789' and html_text[i-1] not in '>':
                    depth += 1
                    i += 4
                elif html_text[i:i+6] == '</div>':
                    depth -= 1
                    i += 6
                else:
                    i += 1
            end = i
            cards.append(html_text[start:end])
            pos = end
        else:
            pos = start + 1

    if not cards:
        return []

    results: list[dict[str, str]] = []
    for card_html in cards:
        # External ID from analytics JSON
        analytics_match = ANALYTICS_JSON_RE.search(card_html)
        external_id = ""
        if analytics_match:
            try:
                analytics_data = json.loads(analytics_match.group(1))
                product_data = analytics_data.get("product_data") or []
                if product_data and isinstance(product_data, list) and isinstance(product_data[0], dict):
                    pid = product_data[0].get("product_id")
                    if pid:
                        external_id = str(pid)
            except (json.JSONDecodeError, ValueError):
                pass

        # Title and company from offerTitle link title attribute
        title = ""
        company = ""
        link_match = OFFER_TITLE_LINK_RE.search(card_html)
        if link_match:
            link_html = link_match.group(0)
            title_attr_match = TITLE_ATTR_RE.search(link_html)
            if title_attr_match:
                title_attr = html_module.unescape(title_attr_match.group(1))
                if " - " in title_attr:
                    parts = title_attr.split(" - ", 1)
                    title = parts[0].strip()
                    company = parts[1].strip()
                else:
                    title = title_attr.strip()

        # Location
        location_match = LOCATION_CARD_RE.search(card_html)
        location = _get_text_between_tags(location_match)

        # Contract type
        contract_match = CONTRACT_CARD_RE.search(card_html)
        contract_type = _get_text_between_tags(contract_match)

        # Browser URL
        browser_url = ""
        href_match = HREF_RE.search(link_match.group(0)) if link_match else None
        if href_match:
            href = html_module.unescape(href_match.group(1))
            if href and not href.startswith("http"):
                browser_url = f"{HELLOWORK_BASE_URL}{href}"
            else:
                browser_url = href

        # Salary from aria-label
        salary = ""
        if link_match:
            aria_match = ARIA_LABEL_RE.search(link_match.group(0))
            if aria_match:
                salary = _parse_salary(aria_match.group(1))

        results.append({
            "external_id": external_id,
            "title": title,
            "company": company,
            "location": location,
            "contract_type": contract_type,
            "browser_url": browser_url,
            "salary": salary,
        })

    return results


# ---------------------------------------------------------------------------
# Relevance filter (same pattern as France Travail)
# ---------------------------------------------------------------------------

def _offer_is_relevant(offer: dict[str, str], *, query: str) -> bool:
    """Check if an offer is relevant to the query."""
    text = " ".join((offer["title"], offer["company"], offer["location"], offer["contract_type"]))
    if not query:
        return True
    return _matches_terms(text, query)


# ---------------------------------------------------------------------------
# DB upsert (same pattern as _upsert_extracted_offer in france_travail/flows.py)
# ---------------------------------------------------------------------------

def _find_existing(conn, external_id: str, browser_url: str):
    """Look up an existing offer by external_id or browser_url."""
    if external_id:
        row = conn.execute(
            "SELECT * FROM offers WHERE external_source = ? AND external_id = ?",
            (EXTERNAL_SOURCE, external_id),
        ).fetchone()
        if row:
            return row
    if browser_url:
        row = conn.execute(
            "SELECT * FROM offers WHERE external_source = ? AND browser_url = ?",
            (EXTERNAL_SOURCE, browser_url),
        ).fetchone()
        if row:
            return row
        row = conn.execute(
            "SELECT * FROM offers WHERE external_source = '' AND browser_url = ?",
            (browser_url,),
        ).fetchone()
        if row:
            return row
    return None


def _upsert_hellowork_offer(conn, offer: dict[str, str], snapshot_payload) -> SearchImportResult:
    """Upsert a single HelloWork offer into the database."""
    timestamp = _now()
    raw_snapshot = _raw_json(snapshot_payload)
    existing = _find_existing(conn, offer["external_id"], offer["browser_url"])
    merged: dict[str, object] = dict(existing) if existing else {}
    merged.update({
        "title": offer["title"],
        "company": offer["company"],
        "location": offer["location"],
        "description": "",
    })
    scored = score_offer(merged)

    if existing:
        external_id = offer["external_id"] or str(existing["external_id"] or "")
        conn.execute(
            """UPDATE offers
               SET title = ?, company = ?, location = ?, url = ?, source = ?, description = ?,
                   salary = ?, remote = ?, contract_type = ?, external_source = ?, external_id = ?,
                   score = ?, score_reasons = ?, browser_url = ?, apply_url = ?, is_active = 1,
                   last_seen_at = ?, raw_browser_snapshot = ?, raw_extracted_text = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (
                offer["title"],
                offer["company"],
                offer["location"],
                offer["browser_url"],
                EXTERNAL_SOURCE,
                "",  # description - not extracted from search results
                offer["salary"],
                "",  # remote
                offer["contract_type"],
                EXTERNAL_SOURCE,
                external_id,
                scored.score,
                "\n".join(scored.reasons),
                offer["browser_url"],
                "",  # apply_url
                timestamp,
                raw_snapshot,
                "",  # raw_extracted_text
                existing["id"],
            ),
        )
        conn.commit()
        add_offer_event(
            conn,
            int(existing["id"]),
            event_type="search_seen",
            message="Seen in HelloWork search",
        )
        return SearchImportResult(int(existing["id"]), False, offer["title"], scored.score, offer["browser_url"])

    offer_id = add_offer(
        conn,
        title=offer["title"],
        company=offer["company"],
        location=offer["location"],
        url=offer["browser_url"],
        source=EXTERNAL_SOURCE,
        description=offer.get("description", ""),
        salary=offer["salary"],
        remote="",
        contract_type=offer["contract_type"],
        external_source=EXTERNAL_SOURCE,
        external_id=offer["external_id"],
        browser_url=offer["browser_url"],
        apply_url="",
        is_active=True,
        last_seen_at=timestamp,
        raw_browser_snapshot=raw_snapshot,
        raw_extracted_text="",
    )
    add_offer_event(
        conn,
        offer_id,
        event_type="search_imported",
        message="Imported from HelloWork search",
    )
    row = get_offer(conn, offer_id)
    score = int(row["score"]) if row else scored.score
    return SearchImportResult(offer_id, True, offer["title"], score, offer["browser_url"])


# ---------------------------------------------------------------------------
# HTTP fetcher (no browser needed — HW serves SSR pages)
# ---------------------------------------------------------------------------

_HELLOWORK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
}


def _fetch_hellowork_html(url: str) -> str:
    """Fetch HelloWork search page HTML via HTTP GET."""
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    req = Request(url, headers=_HELLOWORK_HEADERS)
    try:
        with urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"Failed to fetch HelloWork search: {exc}") from exc


# ---------------------------------------------------------------------------
# Main search function
# ---------------------------------------------------------------------------

def search_hellowork(
    conn,
    *,
    query: str,
    location: str = "",
    contract: str = "",
    browser: Any = None,
    site: str = DEFAULT_SITE,
    profile: str = DEFAULT_PROFILE,
) -> list[SearchImportResult]:
    """Search HelloWork for offers matching query/location/contract.

    1. Build search URL
    2. Fetch HTML via HTTP (no browser needed — HW serves SSR)
    3. Parse HTML to extract offers
    4. Filter by relevance
    5. Upsert into DB
    """
    url = build_hellowork_search_url(query, location, contract)
    raw_html = _fetch_hellowork_html(url)

    extracted = extract_hellowork_offers(raw_html)

    # Filter by relevance
    relevant = [
        offer
        for offer in extracted
        if _offer_is_relevant(offer, query=query)
    ]

    return [_upsert_hellowork_offer(conn, offer, raw_html) for offer in relevant]


# ---------------------------------------------------------------------------
# Saved search dispatch
# ---------------------------------------------------------------------------

def run_hellowork_saved_search(
    conn,
    search_id_or_name: int | str,
    *,
    browser: Any = None,
    site: str = DEFAULT_SITE,
    profile: str = DEFAULT_PROFILE,
) -> list[SearchImportResult]:
    """Run a saved search whose source is 'hellowork'."""
    from emploi.db import get_saved_search, update_saved_search_last_run

    saved = get_saved_search(conn, search_id_or_name)
    if saved is None:
        raise ValueError(f"Profil de recherche introuvable: {search_id_or_name}")
    if not saved["enabled"]:
        raise ValueError(f"Profil de recherche désactivé: {saved['name']}")

    results = search_hellowork(
        conn,
        query=saved["query"],
        location=saved["where_text"],
        contract=saved["contract"],
        browser=browser,
        site=site,
        profile=profile,
    )
    update_saved_search_last_run(conn, int(saved["id"]), _now())
    return results
