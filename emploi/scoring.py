from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ScoreResult:
    score: int
    reasons: list[str]


POSITIVE_RULES: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("support/informatique", 20, ("support", "informatique", "it", "technicien")),
    ("débutant accepté", 12, ("débutant accepté", "débutant", "formation assurée")),
    ("télétravail", 10, ("télétravail", "remote", "hybride")),
    ("contrat stable", 8, ("cdi", "cdd")),
    ("salaire indiqué", 5, ("€", "eur", "salaire", "rémunération")),
)

NEGATIVE_RULES: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("permis/véhicule obligatoire", -30, ("permis b", "véhicule obligatoire", "permis obligatoire")),
    ("déplacements fréquents", -20, ("déplacements fréquents", "itinérant", "mobilité régionale")),
    ("commercial terrain", -18, ("commercial terrain", "prospection terrain")),
    ("expérience longue obligatoire", -15, ("5 ans", "3 ans", "expérience exigée")),
)


def score_offer(offer: Mapping[str, object]) -> ScoreResult:
    """Score an offer against Julien's current constraints.

    The score starts neutral at 50, then applies transparent keyword rules.
    This is intentionally simple for the MVP and easy to replace later.
    """
    text = " ".join(str(offer.get(field) or "") for field in ("title", "company", "location", "description", "notes"))
    normalized = text.casefold()

    score = 50
    reasons: list[str] = []

    for reason, delta, keywords in (*POSITIVE_RULES, *NEGATIVE_RULES):
        if any(keyword.casefold() in normalized for keyword in keywords):
            score += delta
            reasons.append(reason)

    return ScoreResult(score=max(0, min(100, score)), reasons=reasons)
