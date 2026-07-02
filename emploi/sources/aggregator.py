"""Cross-source job aggregator — deduplicates and merges results from all sources."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from emploi.logging import get_logger

logger = get_logger("sources.aggregator")


@dataclass(frozen=True)
class AggregatedOffer:
    """Unified offer from any source, with dedup key and source tracking."""

    title: str
    company: str
    location: str
    url: str
    description: str
    contract_type: str = ""
    salary: str = ""
    source: str = ""
    dedup_key: str = ""

    @property
    def display_title(self) -> str:
        tag = f"[{self.source}]" if self.source else ""
        return f"{tag} {self.title}".strip()

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "description": self.description,
            "contract_type": self.contract_type,
            "salary": self.salary,
            "source": self.source,
        }


def _normalize_text(text: str) -> str:
    """Normalize text for dedup comparison: lowercase, strip accents, collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", stripped).strip()


def _make_dedup_key(title: str, company: str, location: str) -> str:
    """Create a dedup key from normalized title + company + location."""
    parts = [_normalize_text(title), _normalize_text(company), _normalize_text(location)]
    combined = "|".join(p for p in parts if p)
    return hashlib.md5(combined.encode("utf-8")).hexdigest()[:16]


def offer_to_aggregated(offer: Any, source: str) -> AggregatedOffer:
    """Convert any source offer type to an AggregatedOffer."""
    return AggregatedOffer(
        title=str(offer.title or ""),
        company=str(offer.company or ""),
        location=str(offer.location or ""),
        url=str(offer.url or ""),
        description=str(offer.description or ""),
        contract_type=str(offer.contract_type or ""),
        salary=str(offer.salary or ""),
        source=source,
        dedup_key=_make_dedup_key(str(offer.title or ""), str(offer.company or ""), str(offer.location or "")),
    )


def deduplicate_offers(offers: list[AggregatedOffer]) -> list[AggregatedOffer]:
    """Remove duplicate offers across sources, keeping the first occurrence."""
    seen: dict[str, AggregatedOffer] = {}
    for offer in offers:
        key = offer.dedup_key
        if key in seen:
            existing = seen[key]
            # Merge: prefer the one with more data
            if len(offer.description) > len(existing.description):
                seen[key] = offer
        else:
            seen[key] = offer
    return list(seen.values())


# Source registry — maps source names to their search functions
SOURCE_REGISTRY: dict[str, tuple[Callable[..., list], str]] = {}


def register_source(name: str, search_fn: Callable[..., list], country: str) -> None:
    """Register a job source for the aggregator."""
    SOURCE_REGISTRY[name] = (search_fn, country)


def list_sources() -> list[dict[str, str]]:
    """List all registered sources with their country."""
    return [{"name": name, "country": country} for name, (_, country) in SOURCE_REGISTRY.items()]


def search_all(
    query: str,
    location: str = "",
    *,
    countries: list[str] | None = None,
    max_per_source: int = 20,
) -> list[AggregatedOffer]:
    """Search all registered sources and return deduplicated results.

    Args:
        query: Search keywords
        location: Location filter (optional)
        countries: Filter to specific countries (e.g. ["FR", "CH"]). None = all.
        max_per_source: Max results per source
    """
    all_offers: list[AggregatedOffer] = []

    for name, (search_fn, country) in SOURCE_REGISTRY.items():
        if countries and country not in countries:
            continue
        try:
            results = search_fn(query, location=location, max_results=max_per_source)
            for offer in results:
                all_offers.append(offer_to_aggregated(offer, source=name))
            logger.info("Source %s: %d offers", name, len(results))
        except Exception as exc:
            logger.warning("Source %s failed: %s", name, exc)

    deduplicated = deduplicate_offers(all_offers)
    logger.info("Total: %d offers (%d after dedup)", len(all_offers), len(deduplicated))
    return deduplicated


# Auto-register all built-in sources
def _auto_register() -> None:
    from emploi.sources.apec import search_apec
    from emploi.sources.cadremploi import search_cadremploi
    from emploi.sources.comparis import search_comparis
    from emploi.sources.jobs_ch import search_jobs_ch
    from emploi.sources.jobup import search_jobup
    from emploi.sources.monster import search_monster
    from emploi.sources.okjob import search_okjob

    register_source("apec", search_apec, "FR")
    register_source("monster", search_monster, "FR")
    register_source("cadremploi", search_cadremploi, "FR")
    register_source("okjob", search_okjob, "CH")
    register_source("jobup", search_jobup, "CH")
    register_source("jobs.ch", search_jobs_ch, "CH")
    register_source("comparis", search_comparis, "CH")


_auto_register()
