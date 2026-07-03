from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from emploi.applications import create_application_draft
from emploi.db import add_offer_event, get_offer
from emploi.retry import with_retry
from emploi.utils import _pass_show, _safe_slug

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NextcloudExportResult:
    offer_id: int
    remote_dir: str
    uploaded_files: list[str]
    web_url: str = ""
    dry_run: bool = False


class WebDAVClientProtocol(Protocol):
    def ensure_dir(self, remote_path: str) -> None: ...

    def upload_text(self, remote_path: str, content: str, content_type: str = "text/plain; charset=utf-8") -> None: ...

    def upload_file(
        self, remote_path: str, local_path: str | Path, content_type: str = "application/octet-stream"
    ) -> None: ...


def _join_remote(*parts: str) -> str:
    cleaned: list[str] = []
    for part in parts:
        text = str(part or "").strip("/")
        if text:
            cleaned.append(text)
    return "/" + "/".join(cleaned)


class NextcloudWebDAVClient:
    """Minimal WebDAV client for Nextcloud Files.

    Secrets are resolved from pass entries at runtime and never printed by the CLI.
    """

    def __init__(self, endpoint: dict[str, object], *, username: str = "", password: str = "") -> None:
        self.endpoint = endpoint
        self.username = username or _pass_show(str(endpoint.get("username_pass", "") or ""))
        self.password = password or _pass_show(str(endpoint.get("password_pass", "") or ""))
        root_url = str(endpoint.get("webdav_root_url", "") or "")
        if "{username}" in root_url:
            root_url = root_url.replace("{username}", urllib.parse.quote(self.username, safe=""))
        self.root_url = root_url.rstrip("/")
        if not self.root_url:
            raise ValueError("Endpoint Nextcloud Files sans webdav_root_url")

    def _url_for(self, remote_path: str) -> str:
        remote_root = str(self.endpoint.get("remote_root", "") or "").rstrip("/")
        path = str(remote_path or "").strip()
        if remote_root and path.startswith(remote_root):
            path = path[len(remote_root) :]
        path = path.strip("/")
        return self.root_url if not path else f"{self.root_url}/{urllib.parse.quote(path, safe='/') }"

    @with_retry(  # type: ignore[misc,arg-type]
        max_retries=3,
        base_delay=1.0,
        max_delay=15.0,
        retryable_exceptions=(urllib.error.URLError, ConnectionError, OSError),
    )
    def _request(self, method: str, remote_path: str, *, data: bytes | None = None, content_type: str = "") -> None:
        url = self._url_for(remote_path)
        request = urllib.request.Request(url, data=data, method=method)
        token = f"{self.username}:{self.password}".encode()
        import base64

        request.add_header("Authorization", "Basic " + base64.b64encode(token).decode())
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            urllib.request.urlopen(request, timeout=30).read()
        except urllib.error.HTTPError as error:
            if method == "MKCOL" and error.code in {405, 409}:
                return
            if error.code in (429, 500, 502, 503, 504):
                raise ConnectionError(f"Nextcloud WebDAV HTTP {error.code}") from error
            raise

    def ensure_dir(self, remote_path: str) -> None:
        self._request("MKCOL", remote_path)  # type: ignore[misc]

    def upload_text(self, remote_path: str, content: str, content_type: str = "text/plain; charset=utf-8") -> None:
        self._request("PUT", remote_path, data=content.encode("utf-8"), content_type=content_type)  # type: ignore[misc]

    def upload_file(
        self, remote_path: str, local_path: str | Path, content_type: str = "application/octet-stream"
    ) -> None:
        self._request("PUT", remote_path, data=Path(local_path).read_bytes(), content_type=content_type)  # type: ignore[misc]


def compose_offer_markdown(offer) -> str:
    url = ""
    for key in ("browser_url", "apply_url", "url"):
        if key in offer.keys() and str(offer[key] or "").strip():
            url = str(offer[key]).strip()
            break
    lines = [
        f"# Offre — {offer['title']}",
        "",
        f"- Entreprise : {offer['company'] or 'non précisé'}",
        f"- Lieu : {offer['location'] or 'non précisé'}",
        f"- Contrat : {offer['contract_type'] or 'non précisé'}",
        f"- Salaire : {offer['salary'] or 'non précisé'}",
        f"- Source : {offer['external_source'] or offer['source'] or 'manual'}",
        f"- ID source : {offer['external_id'] or 'non précisé'}",
        f"- URL : {url or 'non précisé'}",
        "",
        "## Description",
        str(offer["description"] or offer["raw_extracted_text"] or offer["notes"] or "non précisé").strip(),
        "",
    ]
    return "\n".join(lines)


def _remote_dir_for_offer(remote_root: str, offer) -> str:
    return _join_remote(remote_root, "Candidatures", f"{int(offer['id']):04d}-{_safe_slug(str(offer['title']))}")


def _web_url(endpoint: dict[str, object], remote_dir: str) -> str:
    base_url = str(endpoint.get("base_url", "") or "").rstrip("/")
    if not base_url:
        return ""
    return f"{base_url}/apps/files/files?dir={urllib.parse.quote(remote_dir)}"


def _safe_document_filename(prefix: str, path: str | Path) -> str:
    source = Path(path)
    stem = _safe_slug(source.stem).replace("-", "-")
    suffix = source.suffix or ""
    return f"{prefix}-{stem}{suffix}"


def _document_uploads(document_profile: dict[str, object] | None) -> list[tuple[str, Path]]:
    if not document_profile:
        return []
    uploads: list[tuple[str, Path]] = []
    for key, prefix in (("cv_path", "CV"), ("cover_letter_path", "LM")):
        raw = str(document_profile.get(key, "") or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.exists():
            raise ValueError(f"Fichier document introuvable pour {prefix}: {path}")
        uploads.append((_safe_document_filename(prefix, path), path))
    return uploads


def export_application_to_nextcloud(
    conn,
    offer_id: int,
    *,
    endpoint: dict[str, object],
    client: WebDAVClientProtocol | None = None,
    drafts_dir: str | Path | None = None,
    dry_run: bool = False,
    document_profile: dict[str, object] | None = None,
    include_documents: bool = False,
) -> NextcloudExportResult:
    offer = get_offer(conn, offer_id)
    if offer is None:
        raise ValueError(f"Offre introuvable: {offer_id}")
    remote_root = str(endpoint.get("remote_root", "") or "/Emploi")
    remote_dir = _remote_dir_for_offer(remote_root, offer)
    document_files = _document_uploads(document_profile) if include_documents else []
    uploaded = ["offre.md", "brouillon.md"] + [filename for filename, _ in document_files]
    result = NextcloudExportResult(
        offer_id=offer_id,
        remote_dir=remote_dir,
        uploaded_files=uploaded,
        web_url=_web_url(endpoint, remote_dir),
        dry_run=dry_run,
    )
    if dry_run:
        return result

    dav = client or NextcloudWebDAVClient(endpoint)
    dav.ensure_dir(remote_root)
    dav.ensure_dir(_join_remote(remote_root, "Candidatures"))
    dav.ensure_dir(remote_dir)
    draft = create_application_draft(conn, offer_id, drafts_dir=drafts_dir)
    dav.upload_text(_join_remote(remote_dir, "offre.md"), compose_offer_markdown(offer), "text/markdown; charset=utf-8")
    dav.upload_text(
        _join_remote(remote_dir, "brouillon.md"),
        draft.draft_path.read_text(encoding="utf-8"),
        "text/markdown; charset=utf-8",
    )
    for filename, local_path in document_files:
        dav.upload_file(_join_remote(remote_dir, filename), local_path)
    add_offer_event(
        conn,
        offer_id,
        event_type="nextcloud_exported",
        message=f"Export Nextcloud Files: {remote_dir}",
        payload_json=json.dumps(
            {
                "remote_dir": remote_dir,
                "files": uploaded,
                "web_url": result.web_url,
                "document_profile": str(document_profile.get("name", "") or "")
                if include_documents and document_profile
                else "",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return result
