"""Shared utility functions used across the Emploi codebase."""

from __future__ import annotations

import re
import subprocess
import unicodedata


def _pass_show(entry: str) -> str:
    """Resolve a secret from the ``pass`` password manager."""
    if not entry:
        return ""
    result = subprocess.run(["pass", "show", entry], check=True, text=True, capture_output=True)
    return result.stdout.splitlines()[0].strip()


def _safe_slug(value: str) -> str:
    """Convert a string to a filesystem-safe slug (Unicode-normalized)."""
    ascii_value = unicodedata.normalize("NFKD", value.strip()).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_value).strip("-").lower()
    return slug[:80] or "offre"


def _normalize(text: str) -> str:
    """Strip accents and lowercase for fair comparison."""
    nfkd = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def _matches_terms(text: str, query: str) -> bool:
    """Check if *text* matches all positive terms and zero negative terms from *query*.

    Supports quoted phrases and ``-`` prefix for negative terms.
    """
    normalized = _normalize(text)
    positives: list[str] = []
    negatives: list[str] = []
    for quoted in re.findall(r'(-?)"([^"]+)"', query):
        term = _normalize(quoted[1])
        (negatives if quoted[0] else positives).append(term)
    remainder = re.sub(r'-?"[^"]+"', ' ', query)
    for token in re.findall(r"-?\w+", remainder, re.U):
        term = _normalize(token.lstrip("-"))
        if not term:
            continue
        if token.startswith("-"):
            negatives.append(term)
        else:
            positives.append(term)
    return all(term in normalized for term in positives) and not any(term and term in normalized for term in negatives)


def _first_url(offer) -> str:
    """Return the first non-empty URL from an offer row (browser_url > apply_url > url)."""
    for key in ("browser_url", "apply_url", "url"):
        if key in offer.keys() and str(offer[key] or "").strip():
            return str(offer[key]).strip()
    return ""
