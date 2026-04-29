from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ScoreResult:
    score: int
    reasons: list[str]


def _contains(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword.casefold() in text for keyword in keywords)


def _field(offer: Mapping[str, object], name: str) -> str:
    return str(offer.get(name) or "")


def score_offer(offer: Mapping[str, object]) -> ScoreResult:
    """Score an offer against Julien's current constraints.

    The score starts neutral at 50 and applies transparent deterministic rules.
    Reasons are intentionally readable French lines so stored legacy offers can
    be rescored and displayed without extra schema changes.
    """
    text = " ".join(
        _field(offer, field)
        for field in (
            "title",
            "company",
            "location",
            "description",
            "salary",
            "remote",
            "contract_type",
            "notes",
            "raw_extracted_text",
        )
    )
    normalized = text.casefold()
    location = _field(offer, "location").casefold()
    description = _field(offer, "description").casefold()
    remote = _field(offer, "remote").casefold()
    contract = _field(offer, "contract_type").casefold()
    salary = _field(offer, "salary").casefold()

    score = 50
    reasons: list[str] = []

    def add(delta: int, reason: str) -> None:
        nonlocal score
        if reason not in reasons:
            score += delta
            reasons.append(reason)

    # Role fit: Python/support/admin roles are a good match for Julien.
    if _contains(normalized, ("python", "support", "informatique", "helpdesk", "technicien", "admin système", "administrateur système", "linux", "windows")):
        add(18, "support/informatique")
        add(10, "Métier: Python/support/admin compatible avec le profil visé")

    # Remote: valuable from Bogève; explicit no-remote/presential is risky.
    remote_negative = _contains(
        f"{remote} {description}",
        ("pas de télétravail", "sans télétravail", "présentiel obligatoire", "presentiel obligatoire", "100% présentiel", "sur site obligatoire"),
    )
    if remote_negative:
        add(-18, "Remote: présentiel obligatoire ou télétravail absent")
    elif _contains(f"{remote} {normalized}", ("télétravail", "teletravail", "remote", "à distance", "a distance", "hybride")):
        add(16, "télétravail")
        add(14, "Remote: télétravail explicite, très adapté depuis Bogève")

    # Bogève/location constraints: local/remote is good; car-heavy local travel is risky.
    local_positive = _contains(
        f"{location} {normalized}",
        ("bogève", "bogeve", "bonneville", "annemasse", "haute-savoie", "genevois", "genève", "geneve", "télétravail", "teletravail", "remote"),
    )
    transport_risk = _contains(
        normalized,
        (
            "permis b",
            "véhicule obligatoire",
            "vehicule obligatoire",
            "permis obligatoire",
            "déplacements fréquents",
            "deplacements frequents",
            "déplacements quotidiens",
            "deplacements quotidiens",
            "itinérant",
            "itinerant",
            "mobilité régionale",
            "mobilite regionale",
        ),
    )
    if transport_risk:
        add(-20, "permis/véhicule obligatoire")
        add(-16, "Localisation: trajet risqué depuis Bogève sans transport fiable")
    elif local_positive:
        add(8, "Localisation: Bogève/Haute-Savoie ou zone proche identifiable")

    # Contract type: stable contracts first; less stable paths lower priority.
    contract_text = f"{contract} {normalized}"
    if _contains(contract_text, ("cdi", "cdd")):
        add(8, "contrat stable")
        add(6, "Contrat: CDI ou CDD stable")
    elif _contains(contract_text, ("freelance", "indépendant", "independant", "stage", "alternance", "service civique")):
        add(-10, "Contrat: freelance/stage/alternance moins prioritaire")

    # Salary signal: an indicated salary is useful; lack of signal is a small negative.
    if salary or _contains(normalized, ("€", "eur", "k€", "brut annuel", "salaire", "rémunération", "remuneration")):
        add(5, "salaire indiqué")
        add(5, "Salaire: rémunération indiquée")
    else:
        add(-3, "Salaire: aucune rémunération indiquée")

    # Realistic match: beginner/training is positive; senior-only requirements are not.
    unrealistic = _contains(
        normalized,
        ("7 ans", "6 ans", "5 ans", "4 ans", "3 ans", "senior", "expert", "expérience exigée", "experience exigee", "bac+5 obligatoire"),
    )
    if unrealistic:
        add(-18, "expérience longue obligatoire")
        add(-16, "Réalisme: exigences trop élevées pour le profil visé")
    elif _contains(normalized, ("débutant accepté", "debutant accepte", "débutant", "debutant", "junior", "formation assurée", "formation possible")):
        add(12, "débutant accepté")
        add(10, "Réalisme: profil junior/formation compatible")

    # Candidature effort: easy paths are positive; paperwork/friction is negative.
    if _contains(normalized, ("candidature simple", "postuler simplement", "par email", "cv suffit", "réponse rapide", "reponse rapide", "easy apply")):
        add(7, "Candidature: démarche simple")
    if _contains(normalized, ("lettre manuscrite", "dossier complet", "relances téléphoniques", "relances telephoniques", "portfolio obligatoire", "test technique long")):
        add(-10, "Candidature: effort élevé ou friction importante")

    # Existing negative labels kept for backward-compatible expectations/display.
    if _contains(normalized, ("déplacements fréquents", "deplacements frequents", "itinérant", "itinerant", "mobilité régionale", "mobilite regionale")):
        add(-10, "déplacements fréquents")
    if _contains(normalized, ("commercial terrain", "prospection terrain")):
        add(-18, "commercial terrain")

    return ScoreResult(score=max(0, min(100, score)), reasons=reasons)
