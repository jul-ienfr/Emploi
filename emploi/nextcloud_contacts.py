"""Client Nextcloud CardDAV — contacts recruteurs/entreprises.

Plan Nextcloud Phase 4. Endpoint configuré via ``nextcloud_contacts.json``
(``emploi contact set``) ; les credentials restent des références ``pass``.
"""

from __future__ import annotations

import base64
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from emploi.retry import with_retry
from emploi.utils import _pass_show


@dataclass(frozen=True)
class Contact:
    uid: str
    name: str
    org: str = ""
    email: str = ""
    phone: str = ""
    note: str = ""


class ContactsClientProtocol(Protocol):
    def list_contacts(self) -> list[Contact]: ...

    def add_contact(
        self, *, uid: str, name: str, org: str = "", email: str = "", phone: str = "", note: str = ""
    ) -> str: ...


class NextcloudContactsClient:
    def __init__(self, endpoint: dict[str, object], *, username: str = "", password: str = "") -> None:
        self.endpoint = endpoint
        self.username = username or _pass_show(str(endpoint.get("username_pass", "") or ""))
        self.password = password or _pass_show(str(endpoint.get("password_pass", "") or ""))
        self.base_url = str(endpoint.get("base_url", "") or "").rstrip("/")
        self.carddav_base_path = str(
            endpoint.get("carddav_base_path", "/remote.php/dav/addressbooks") or "/remote.php/dav/addressbooks"
        )
        self.addressbook = str(endpoint.get("addressbook", "") or "contacts").strip("/") or "contacts"
        if not self.base_url or not self.username or not self.password:
            raise ValueError("Endpoint Nextcloud Contacts incomplet")

    @property
    def addressbook_url(self) -> str:
        encoded_user = urllib.parse.quote(self.username, safe="")
        encoded_book = urllib.parse.quote(self.addressbook, safe="")
        return f"{self.base_url}{self.carddav_base_path}/{encoded_user}/{encoded_book}"

    @with_retry(  # type: ignore[misc,arg-type]
        max_retries=3,
        base_delay=1.0,
        max_delay=15.0,
        retryable_exceptions=(urllib.error.URLError, ConnectionError, OSError),
    )
    def _request(
        self, method: str, url: str, data: bytes = b"", content_type: str = "text/vcard; charset=utf-8"
    ) -> bytes:
        request = urllib.request.Request(url, data=data if data else None, method=method)
        if data:
            request.add_header("Content-Type", content_type)
        request.add_header("Accept", "text/vcard, text/plain;q=0.9, */*;q=0.5")
        token = f"{self.username}:{self.password}".encode()
        request.add_header("Authorization", "Basic " + base64.b64encode(token).decode())
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504):
                raise ConnectionError(f"Nextcloud CardDAV HTTP {exc.code}") from exc
            raise

    def list_contacts(self) -> list[Contact]:
        """List contacts by GETting the addressbook (Nextcloud returns concatenated VCARDs)."""
        text: str = self._request("GET", self.addressbook_url).decode("utf-8", errors="replace")  # type: ignore[misc]
        return [_parse_vcard(block) for block in _split_vcards(text) if "UID:" in block]

    def add_contact(
        self, *, uid: str, name: str, org: str = "", email: str = "", phone: str = "", note: str = ""
    ) -> str:
        href = f"{self.addressbook_url}/{urllib.parse.quote(uid, safe='')}.vcf"
        self._request(
            "PUT", href, _build_vcard(uid=uid, name=name, org=org, email=email, phone=phone, note=note).encode("utf-8")
        )  # type: ignore[misc]
        return href


def _split_vcards(text: str) -> list[str]:
    """Split a concatenated text/vcard stream into individual VCARD blocks."""
    parts = re.split(r"(?=BEGIN:VCARD)", text)
    return [part for part in parts if part.strip()]


def _vcard_field(block: str, key: str) -> str:
    for line in block.splitlines():
        if line.upper().startswith(key.upper() + ":"):
            return line.split(":", 1)[1].strip()
        if line.upper().startswith(key.upper() + ";"):
            return line.split(":", 1)[1].strip() if ":" in line else ""
    return ""


def _parse_vcard(block: str) -> Contact:
    uid = _vcard_field(block, "UID") or re.sub(r"[^a-zA-Z0-9_-]", "", block[:40])
    name = _vcard_field(block, "FN") or _vcard_field(block, "N")
    email = _vcard_field(block, "EMAIL")
    phone = _vcard_field(block, "TEL")
    org = _vcard_field(block, "ORG")
    note = _vcard_field(block, "NOTE")
    return Contact(uid=uid, name=name, org=org, email=email, phone=phone, note=note)


def _escape_vcard(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
    )


def _build_vcard(*, uid: str, name: str, org: str = "", email: str = "", phone: str = "", note: str = "") -> str:
    return "\r\n".join(
        [
            "BEGIN:VCARD",
            "VERSION:3.0",
            f"UID:{_escape_vcard(uid)}",
            f"FN:{_escape_vcard(name)}",
            f"N:{_escape_vcard(name)}",
            f"ORG:{_escape_vcard(org)}" if org else "ORG:",
            f"EMAIL;TYPE=INTERNET:{_escape_vcard(email)}" if email else "EMAIL:",
            f"TEL;TYPE=CELL:{_escape_vcard(phone)}" if phone else "TEL:",
            f"NOTE:{_escape_vcard(note)}" if note else "NOTE:",
            "END:VCARD",
            "",
        ]
    )
