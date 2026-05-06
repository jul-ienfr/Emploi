from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from emploi.db import add_offer_event, get_application, get_offer, list_next_actions, list_offer_events
from emploi.nextcloud_deck import compose_deck_card_title


@dataclass(frozen=True)
class FollowupTaskResult:
    application_id: int
    offer_id: int
    uid: str
    summary: str
    description: str
    due_date: str
    href: str = ""
    dry_run: bool = False
    reused_existing: bool = False


class TasksClientProtocol(Protocol):
    def create_task(self, *, uid: str, summary: str, description: str, due_date: str) -> dict[str, object]: ...


def _pass_show(entry: str) -> str:
    if not entry:
        return ""
    result = subprocess.run(["pass", "show", entry], check=True, text=True, capture_output=True)
    return result.stdout.splitlines()[0].strip()


class NextcloudTasksClient:
    def __init__(self, endpoint: dict[str, object], *, username: str = "", password: str = "") -> None:
        self.endpoint = endpoint
        self.username = username or _pass_show(str(endpoint.get("username_pass", "") or ""))
        self.password = password or _pass_show(str(endpoint.get("password_pass", "") or ""))
        self.base_url = str(endpoint.get("base_url", "") or "").rstrip("/")
        self.caldav_base_path = str(endpoint.get("caldav_base_path", "/remote.php/dav/calendars") or "/remote.php/dav/calendars")
        self.calendar = str(endpoint.get("calendar", "tasks") or "tasks").strip("/") or "tasks"
        if not self.base_url or not self.username or not self.password:
            raise ValueError("Endpoint Nextcloud Tasks incomplet")

    @property
    def calendar_url(self) -> str:
        encoded_user = urllib.parse.quote(self.username, safe="")
        encoded_calendar = urllib.parse.quote(self.calendar, safe="")
        return f"{self.base_url}{self.caldav_base_path}/{encoded_user}/{encoded_calendar}"

    def _request(self, method: str, url: str, data: bytes = b"", content_type: str = "text/calendar; charset=utf-8") -> bytes:
        request = urllib.request.Request(url, data=data if data else None, method=method)
        if data:
            request.add_header("Content-Type", content_type)
        token = f"{self.username}:{self.password}".encode()
        request.add_header("Authorization", "Basic " + base64.b64encode(token).decode())
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()

    def create_task(self, *, uid: str, summary: str, description: str, due_date: str) -> dict[str, object]:
        href = f"{self.calendar_url}/{urllib.parse.quote(uid, safe='')}.ics"
        ics = build_vtodo(uid=uid, summary=summary, description=description, due_date=due_date)
        self._request("PUT", href, ics.encode("utf-8"))
        return {"uid": uid, "href": href}


def _escape_ical_text(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
    )


def _fold_ical_line(line: str) -> list[str]:
    if len(line) <= 73:
        return [line]
    folded: list[str] = []
    current = line
    while len(current) > 73:
        folded.append(current[:73])
        current = " " + current[73:]
    folded.append(current)
    return folded


def build_vtodo(*, uid: str, summary: str, description: str, due_date: str) -> str:
    date.fromisoformat(due_date)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    raw_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Emploi CLI//Followups//FR",
        "BEGIN:VTODO",
        f"UID:{_escape_ical_text(uid)}",
        f"DTSTAMP:{stamp}",
        f"DUE;VALUE=DATE:{due_date.replace('-', '')}",
        "STATUS:NEEDS-ACTION",
        f"SUMMARY:{_escape_ical_text(summary)}",
        f"DESCRIPTION:{_escape_ical_text(description)}",
        "END:VTODO",
        "END:VCALENDAR",
    ]
    lines: list[str] = []
    for line in raw_lines:
        lines.extend(_fold_ical_line(line))
    return "\r\n".join(lines) + "\r\n"


def _first_url(offer) -> str:
    for key in ("browser_url", "apply_url", "url"):
        if key in offer.keys() and str(offer[key] or "").strip():
            return str(offer[key]).strip()
    return ""


def _followup_uid(endpoint_name: str, application_id: int, due_date: str) -> str:
    digest = hashlib.sha1(f"{endpoint_name}:{application_id}:{due_date}".encode("utf-8")).hexdigest()[:12]
    return f"emploi-followup-{application_id}-{digest}"


def compose_followup_task_description(offer, application) -> str:
    lines = [
        f"Candidature #{application['id']} — offre #{offer['id']}",
        f"Entreprise : {offer['company'] or 'non précisé'}",
        f"Lieu : {offer['location'] or 'non précisé'}",
        f"Contrat : {offer['contract_type'] or 'non précisé'}",
    ]
    url = _first_url(offer)
    if url:
        lines.append(f"Offre : {url}")
    if application["notes"]:
        lines.extend(["", str(application["notes"])])
    return "\n".join(lines)


def _existing_followup_task_event(conn, offer_id: int, *, application_id: int, endpoint_name: str, due_date: str):
    for event in list_offer_events(conn, offer_id):
        if event["event_type"] != "nextcloud_followup_task":
            continue
        try:
            payload = json.loads(event["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if (
            int(payload.get("application_id") or 0) == int(application_id)
            and payload.get("endpoint") == endpoint_name
            and payload.get("due_date") == due_date
        ):
            return payload
    return None


def create_followup_task(
    conn,
    *,
    application_id: int,
    endpoint: dict[str, object],
    client: TasksClientProtocol | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> FollowupTaskResult:
    application = get_application(conn, application_id)
    if application is None:
        raise ValueError(f"Candidature introuvable: {application_id}")
    if not application["next_action_at"]:
        raise ValueError(f"Candidature sans date de relance: {application_id}")
    due_date = str(application["next_action_at"])
    date.fromisoformat(due_date)
    offer_id = int(application["offer_id"])
    offer = get_offer(conn, offer_id)
    if offer is None:
        raise ValueError(f"Offre introuvable: {offer_id}")
    endpoint_name = str(endpoint.get("name", "") or "")
    calendar = str(endpoint.get("calendar", "tasks") or "tasks")
    uid = _followup_uid(endpoint_name, int(application_id), due_date)
    summary = f"Relancer — {compose_deck_card_title(offer)}"
    description = compose_followup_task_description(offer, application)
    existing = None if force else _existing_followup_task_event(
        conn,
        offer_id,
        application_id=int(application_id),
        endpoint_name=endpoint_name,
        due_date=due_date,
    )
    if existing is not None:
        return FollowupTaskResult(
            application_id=int(application_id),
            offer_id=offer_id,
            uid=str(existing.get("uid") or uid),
            summary=summary,
            description=description,
            due_date=due_date,
            href=str(existing.get("href") or ""),
            dry_run=False,
            reused_existing=True,
        )
    if dry_run:
        return FollowupTaskResult(
            application_id=int(application_id),
            offer_id=offer_id,
            uid=uid,
            summary=summary,
            description=description,
            due_date=due_date,
            dry_run=True,
        )
    tasks = client or NextcloudTasksClient(endpoint)
    created = tasks.create_task(uid=uid, summary=summary, description=description, due_date=due_date)
    href = str(created.get("href", "") or "")
    add_offer_event(
        conn,
        offer_id,
        event_type="nextcloud_followup_task",
        message=f"Tâche Nextcloud créée: {summary}",
        payload_json=json.dumps(
            {
                "endpoint": endpoint_name,
                "calendar": calendar,
                "application_id": int(application_id),
                "offer_id": offer_id,
                "uid": uid,
                "href": href,
                "due_date": due_date,
                "summary": summary,
            },
            ensure_ascii=False,
        ),
    )
    return FollowupTaskResult(
        application_id=int(application_id),
        offer_id=offer_id,
        uid=uid,
        summary=summary,
        description=description,
        due_date=due_date,
        href=href,
    )


def sync_due_followup_tasks(
    conn,
    *,
    endpoint: dict[str, object],
    today: str | None = None,
    client: TasksClientProtocol | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> list[FollowupTaskResult]:
    current = today or date.today().isoformat()
    results: list[FollowupTaskResult] = []
    for action in list_next_actions(conn, today=current):
        if action.get("action") != "Relancer candidature":
            continue
        application_id = int(action["application_id"])
        results.append(
            create_followup_task(
                conn,
                application_id=application_id,
                endpoint=endpoint,
                client=client,
                dry_run=dry_run,
                force=force,
            )
        )
    return results
