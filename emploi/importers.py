from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from emploi.db import add_offer, get_offer
from emploi.scoring import score_offer

OFFER_FIELDS = (
    "title",
    "company",
    "location",
    "url",
    "source",
    "description",
    "salary",
    "remote",
    "contract_type",
    "notes",
    "external_id",
)

FUTURE_SOURCE_ADAPTERS: dict[str, str] = {
    "indeed": "Generic export/import source for Indeed offers; no direct scraping.",
    "welcome-to-the-jungle": "Generic export/import source for Welcome to the Jungle offers; no direct scraping.",
    "linkedin": "Generic export/import source for LinkedIn offers; no direct scraping.",
    "local-site": "Generic export/import source for local company or regional job sites.",
    "remote-freelance": "Generic export/import source for remote and freelance boards.",
}


@dataclass(frozen=True)
class ImportedOffer:
    offer_id: int
    created: bool
    title: str
    url: str


@dataclass(frozen=True)
class ImportSummary:
    source: str
    path: str
    file_format: str
    created: int = 0
    updated: int = 0
    skipped: int = 0
    offers: list[ImportedOffer] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.created + self.updated + self.skipped

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "path": self.path,
            "format": self.file_format,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "total": self.total,
            "offers": [
                {"id": offer.offer_id, "created": offer.created, "title": offer.title, "url": offer.url}
                for offer in self.offers
            ],
        }


def detect_format(path: str | Path, file_format: str = "auto") -> str:
    normalized = file_format.strip().lower()
    if normalized in {"json", "csv"}:
        return normalized
    if normalized != "auto":
        raise ValueError("Format import invalide: utilise auto, json ou csv")
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".csv":
        return "csv"
    raise ValueError("Impossible de détecter le format: utilise --format json ou --format csv")


def import_offers_file(
    conn: sqlite3.Connection,
    path: str | Path,
    *,
    source: str,
    file_format: str = "auto",
) -> ImportSummary:
    source_name = source.strip()
    if not source_name:
        raise ValueError("La source est obligatoire")
    resolved = Path(path)
    fmt = detect_format(resolved, file_format)
    rows = _read_rows(resolved, fmt)

    created = 0
    updated = 0
    skipped = 0
    imported: list[ImportedOffer] = []
    for raw in rows:
        data = normalize_offer(raw, source=source_name)
        if not data["title"]:
            skipped += 1
            continue
        existing = find_existing_offer(conn, source=source_name, external_id=data["external_id"], url=data["url"])
        if existing is None:
            offer_id = add_offer(conn, **data)
            created += 1
            imported.append(ImportedOffer(offer_id=offer_id, created=True, title=data["title"], url=data["url"]))
        else:
            offer_id = int(existing["id"])
            update_imported_offer(conn, offer_id, data)
            updated += 1
            imported.append(ImportedOffer(offer_id=offer_id, created=False, title=data["title"], url=data["url"]))

    return ImportSummary(
        source=source_name,
        path=str(resolved),
        file_format=fmt,
        created=created,
        updated=updated,
        skipped=skipped,
        offers=imported,
    )


def normalize_offer(raw: dict[str, Any], *, source: str) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for field_name in OFFER_FIELDS:
        value = raw.get(field_name, "")
        normalized[field_name] = "" if value is None else str(value).strip()
    normalized["source"] = normalized["source"] or source
    normalized["external_source"] = source
    return normalized


def find_existing_offer(
    conn: sqlite3.Connection,
    *,
    source: str,
    external_id: str = "",
    url: str = "",
) -> sqlite3.Row | None:
    if external_id:
        row = conn.execute(
            "SELECT * FROM offers WHERE external_source = ? AND external_id = ? ORDER BY id DESC LIMIT 1",
            (source, external_id),
        ).fetchone()
        if row is not None:
            return row
    if url:
        return conn.execute("SELECT * FROM offers WHERE url = ? ORDER BY id DESC LIMIT 1", (url,)).fetchone()
    return None


def update_imported_offer(conn: sqlite3.Connection, offer_id: int, data: dict[str, str]) -> None:
    existing = get_offer(conn, offer_id)
    if existing is None:
        raise ValueError(f"Offre introuvable: {offer_id}")
    scored = score_offer({**dict(existing), **data})
    conn.execute(
        """
        UPDATE offers
        SET title = ?, company = ?, location = ?, url = ?, source = ?, description = ?,
            salary = ?, remote = ?, contract_type = ?, notes = ?, external_source = ?,
            external_id = ?, score = ?, score_reasons = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            data["title"],
            data["company"],
            data["location"],
            data["url"],
            data["source"],
            data["description"],
            data["salary"],
            data["remote"],
            data["contract_type"],
            data["notes"],
            data["external_source"],
            data["external_id"],
            scored.score,
            "\n".join(scored.reasons),
            offer_id,
        ),
    )
    conn.commit()


def _read_rows(path: Path, file_format: str) -> list[dict[str, Any]]:
    if not path.exists():
        raise ValueError(f"Fichier introuvable: {path}")
    if file_format == "json":
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return _rows_from_json(payload)
    if file_format == "csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    raise ValueError("Format import invalide: utilise auto, json ou csv")


def _rows_from_json(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("offers"), list):
        rows = payload["offers"]
    else:
        raise ValueError("Le JSON doit être une liste d'offres ou un objet avec une clé 'offers'")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Chaque offre importée doit être un objet JSON")
    return list(rows)
