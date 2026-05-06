from __future__ import annotations

import base64
import json
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from emploi.db import add_offer_event, get_offer, list_offer_events


@dataclass(frozen=True)
class DeckCardResult:
    offer_id: int
    stack_id: int
    title: str
    description: str
    card_id: int | None = None
    dry_run: bool = False
    reused_existing: bool = False


class DeckClientProtocol(Protocol):
    def create_card(self, *, stack_id: int, title: str, description: str, order: int = 999) -> dict[str, object]: ...


def _pass_show(entry: str) -> str:
    if not entry:
        return ""
    result = subprocess.run(["pass", "show", entry], check=True, text=True, capture_output=True)
    return result.stdout.splitlines()[0].strip()


class NextcloudDeckClient:
    def __init__(self, endpoint: dict[str, object], *, username: str = "", password: str = "") -> None:
        self.endpoint = endpoint
        self.username = username or _pass_show(str(endpoint.get("username_pass", "") or ""))
        self.password = password or _pass_show(str(endpoint.get("password_pass", "") or ""))
        self.base_url = str(endpoint.get("base_url", "") or "").rstrip("/")
        self.api_base_path = str(endpoint.get("api_base_path", "/index.php/apps/deck/api/v1.0") or "/index.php/apps/deck/api/v1.0")
        self.board_id = int(endpoint.get("board_id", 0) or 0)
        if not self.base_url or self.board_id <= 0:
            raise ValueError("Endpoint Deck incomplet")

    def _request_json(self, method: str, path: str, payload: dict[str, object]) -> dict[str, object]:
        url = f"{self.base_url}{self.api_base_path}/boards/{self.board_id}{path}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        token = f"{self.username}:{self.password}".encode()
        request.add_header("Authorization", "Basic " + base64.b64encode(token).decode())
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
        return json.loads(text) if text.strip() else {}

    def create_card(self, *, stack_id: int, title: str, description: str, order: int = 999) -> dict[str, object]:
        # Nextcloud Deck cards are created under the target stack.
        return self._request_json(
            "POST",
            f"/stacks/{int(stack_id)}/cards",
            {"title": title, "description": description, "type": "plain", "order": int(order)},
        )


def _first_url(offer) -> str:
    for key in ("browser_url", "apply_url", "url"):
        if key in offer.keys() and str(offer[key] or "").strip():
            return str(offer[key]).strip()
    return ""


def compose_deck_card_title(offer) -> str:
    company = str(offer["company"] or "").strip()
    title = str(offer["title"] or "Offre").strip()
    return f"{title} — {company}" if company else title


def compose_deck_card_description(offer, *, nextcloud_folder_url: str = "") -> str:
    lines = [
        f"Entreprise : {offer['company'] or 'non précisé'}",
        f"Lieu : {offer['location'] or 'non précisé'}",
        f"Contrat : {offer['contract_type'] or 'non précisé'}",
        f"Source : {offer['external_source'] or offer['source'] or 'manual'}",
    ]
    url = _first_url(offer)
    if url:
        lines.append(f"Offre : {url}")
    if nextcloud_folder_url:
        lines.append(f"Dossier Nextcloud : {nextcloud_folder_url}")
    description = str(offer["description"] or offer["raw_extracted_text"] or offer["notes"] or "").strip()
    if description:
        lines.extend(["", "Description :", description[:2000]])
    return "\n".join(lines)


def _existing_deck_card_event(conn, offer_id: int, *, endpoint_name: str, stack_id: int):
    for event in list_offer_events(conn, offer_id):
        if event["event_type"] != "nextcloud_deck_card":
            continue
        try:
            payload = json.loads(event["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if payload.get("endpoint") == endpoint_name and int(payload.get("stack_id") or 0) == int(stack_id):
            return payload
    return None


def create_offer_card(
    conn,
    offer_id: int,
    *,
    endpoint: dict[str, object],
    stack_id: int,
    client: DeckClientProtocol | None = None,
    nextcloud_folder_url: str = "",
    dry_run: bool = False,
    force: bool = False,
) -> DeckCardResult:
    offer = get_offer(conn, offer_id)
    if offer is None:
        raise ValueError(f"Offre introuvable: {offer_id}")
    title = compose_deck_card_title(offer)
    description = compose_deck_card_description(offer, nextcloud_folder_url=nextcloud_folder_url)
    result = DeckCardResult(
        offer_id=offer_id,
        stack_id=int(stack_id),
        title=title,
        description=description,
        dry_run=dry_run,
    )
    if dry_run:
        return result
    endpoint_name = str(endpoint.get("name", "") or "")
    existing = None if force else _existing_deck_card_event(conn, offer_id, endpoint_name=endpoint_name, stack_id=int(stack_id))
    if existing is not None:
        card_id = existing.get("card_id")
        return DeckCardResult(
            offer_id=offer_id,
            stack_id=int(stack_id),
            title=title,
            description=description,
            card_id=int(card_id) if card_id is not None else None,
            dry_run=False,
            reused_existing=True,
        )
    deck = client or NextcloudDeckClient(endpoint)
    created = deck.create_card(stack_id=int(stack_id), title=title, description=description)
    card_id = created.get("id")
    normalized_card_id = int(card_id) if card_id is not None else None
    add_offer_event(
        conn,
        offer_id,
        event_type="nextcloud_deck_card",
        message=f"Carte Deck créée: {normalized_card_id or 'id inconnu'}",
        payload_json=json.dumps(
            {
                "endpoint": endpoint_name,
                "board_id": int(endpoint.get("board_id", 0) or 0),
                "stack_id": int(stack_id),
                "card_id": normalized_card_id,
                "title": title,
            },
            ensure_ascii=False,
        ),
    )
    return DeckCardResult(
        offer_id=offer_id,
        stack_id=int(stack_id),
        title=title,
        description=description,
        card_id=normalized_card_id,
        dry_run=False,
    )
