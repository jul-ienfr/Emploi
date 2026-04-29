from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from emploi.db import add_offer_event, get_offer, upsert_draft_application

DEFAULT_DRAFTS_DIR = Path.home() / ".local" / "share" / "emploi" / "drafts"


@dataclass(frozen=True)
class ApplicationDraftResult:
    offer_id: int
    application_id: int
    draft_path: Path


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-").lower()
    return slug[:80] or "offre"


def _first_non_empty(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return "non précisé"


def _compose_draft(offer) -> str:
    title = _first_non_empty(offer["title"])
    company = _first_non_empty(offer["company"], "l'entreprise")
    location = _first_non_empty(offer["location"])
    contract = _first_non_empty(offer["contract_type"])
    remote = _first_non_empty(offer["remote"])
    salary = _first_non_empty(offer["salary"])
    url = _first_non_empty(offer["browser_url"] if "browser_url" in offer.keys() else "", offer["url"])
    description = _first_non_empty(offer["description"], offer["notes"])
    if len(description) > 220:
        description = description[:217].rstrip() + "..."

    return "\n".join(
        [
            f"# Brouillon de candidature — {title}",
            "",
            f"Offre : {title}",
            f"Entreprise : {company}",
            f"Lieu : {location}",
            f"Contrat / rythme : {contract} — {remote}",
            f"Salaire : {salary}",
            f"Lien : {url}",
            "",
            "## Message court à adapter",
            "Bonjour,",
            "",
            f"Votre offre de {title} chez {company} m'intéresse, notamment pour son contexte ({location}) et les missions décrites.",
            "Je peux apporter une approche sérieuse en support informatique, automatisation Python et suivi utilisateur.",
            "Je serais heureux d'échanger pour vérifier l'adéquation avec vos besoins.",
            "",
            "Cordialement,",
            "Julien",
            "",
            "## À vérifier avant envoi",
            f"- Relire l'annonce : {description}",
            "- Adapter 1 phrase avec une mission précise de l'offre.",
            "- Joindre le CV à jour et vérifier les coordonnées.",
            "- Ouvrir le lien puis envoyer manuellement si tout est correct.",
            "",
            "Sécurité : Aucune soumission automatique n'a été effectuée par emploi.",
            "",
        ]
    )


def create_application_draft(conn, offer_id: int, *, drafts_dir: str | Path | None = None) -> ApplicationDraftResult:
    offer = get_offer(conn, offer_id)
    if offer is None:
        raise ValueError(f"Offre introuvable: {offer_id}")
    base = Path(drafts_dir) if drafts_dir is not None else DEFAULT_DRAFTS_DIR
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{offer_id}-{_safe_slug(offer['title'])}.md"
    path.write_text(_compose_draft(offer), encoding="utf-8")
    application_id = upsert_draft_application(
        conn,
        offer_id,
        draft_path=str(path),
        notes=f"Draft: {path}",
    )
    add_offer_event(
        conn,
        offer_id,
        event_type="application_draft_created",
        message=f"Brouillon créé: {path}",
        payload_json=json.dumps(
            {"application_id": application_id, "draft_path": str(path), "submit_application": False},
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return ApplicationDraftResult(offer_id=offer_id, application_id=application_id, draft_path=path)
