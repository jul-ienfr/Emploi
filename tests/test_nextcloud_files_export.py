from __future__ import annotations

import importlib
import json
from pathlib import Path

from typer.testing import CliRunner

import emploi.config as emploi_config
from emploi.cli import app
from emploi.db import add_offer, connect, init_db, list_offer_events
from emploi.nextcloud_files import export_application_to_nextcloud


class FakeWebDAVClient:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.uploaded: dict[str, str | bytes] = {}

    def ensure_dir(self, remote_path: str) -> None:
        self.created.append(remote_path)

    def upload_text(self, remote_path: str, content: str, content_type: str = "text/plain; charset=utf-8") -> None:
        self.uploaded[remote_path] = content

    def upload_file(self, remote_path: str, local_path: str | Path, content_type: str = "application/octet-stream") -> None:
        self.uploaded[remote_path] = Path(local_path).read_bytes()


def reload_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return importlib.reload(emploi_config)


def test_export_application_bundle_uploads_offer_and_draft_and_records_event(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn,
        title="Chauffeur PL régional",
        company="Transports Dupont",
        location="Bogève",
        url="https://example.test/offre/123",
        description="Livraison régionale en porteur.",
        contract_type="CDI",
        external_source="france-travail",
        external_id="123ABC",
    )
    client = FakeWebDAVClient()
    endpoint = {
        "name": "emploi",
        "remote_root": "/Emploi",
        "webdav_root_url": "https://nextcloud.test/remote.php/dav/files/test-user/Emploi",
    }

    result = export_application_to_nextcloud(conn, offer_id, endpoint=endpoint, client=client, drafts_dir=tmp_path / "drafts")

    assert result.offer_id == offer_id
    assert result.remote_dir == "/Emploi/Candidatures/0001-chauffeur-pl-regional"
    assert client.created == ["/Emploi", "/Emploi/Candidatures", result.remote_dir]
    assert set(client.uploaded) == {
        f"{result.remote_dir}/offre.md",
        f"{result.remote_dir}/brouillon.md",
    }
    assert "Chauffeur PL régional" in str(client.uploaded[f"{result.remote_dir}/offre.md"])
    assert "Transports Dupont" in str(client.uploaded[f"{result.remote_dir}/offre.md"])
    assert "Sécurité : Aucune soumission automatique" in str(client.uploaded[f"{result.remote_dir}/brouillon.md"])
    events = list_offer_events(conn, offer_id)
    assert events[0]["event_type"] == "nextcloud_exported"
    payload = json.loads(events[0]["payload_json"])
    assert payload["remote_dir"] == result.remote_dir
    assert payload["files"] == ["offre.md", "brouillon.md"]


def test_export_application_bundle_uploads_document_profile_files(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(conn, title="Chauffeur PL", company="Dupont")
    cv = tmp_path / "CV Profil PL.pdf"
    letter = tmp_path / "LM Profil PL.pdf"
    cv.write_bytes(b"CV-PDF")
    letter.write_bytes(b"LM-PDF")
    client = FakeWebDAVClient()
    endpoint = {"name": "emploi", "remote_root": "/Emploi"}
    profile = {"name": "poids-lourd", "cv_path": str(cv), "cover_letter_path": str(letter)}

    result = export_application_to_nextcloud(
        conn,
        offer_id,
        endpoint=endpoint,
        client=client,
        document_profile=profile,
        include_documents=True,
    )

    assert result.uploaded_files == ["offre.md", "brouillon.md", "CV-cv-profil-pl.pdf", "LM-lm-profil-pl.pdf"]
    assert client.uploaded[f"{result.remote_dir}/CV-cv-profil-pl.pdf"] == b"CV-PDF"
    assert client.uploaded[f"{result.remote_dir}/LM-lm-profil-pl.pdf"] == b"LM-PDF"
    payload = json.loads(list_offer_events(conn, offer_id)[0]["payload_json"])
    assert payload["document_profile"] == "poids-lourd"
    assert payload["files"] == result.uploaded_files


def test_export_application_bundle_can_run_dry_without_uploading(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(conn, title="Support", company="Acme")
    client = FakeWebDAVClient()
    endpoint = {"name": "emploi", "remote_root": "/Emploi"}

    result = export_application_to_nextcloud(conn, offer_id, endpoint=endpoint, client=client, dry_run=True)

    assert result.remote_dir == "/Emploi/Candidatures/0001-support"
    assert result.uploaded_files == ["offre.md", "brouillon.md"]
    assert client.created == []
    assert client.uploaded == {}
    assert list_offer_events(conn, offer_id) == []


def test_application_export_cli_dry_run_uses_configured_nextcloud_files(monkeypatch, tmp_path):
    config = reload_config(monkeypatch, tmp_path)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    config.set_nextcloud_files_endpoint(
        "emploi",
        base_url="https://nextcloud.test",
        remote_root="/Emploi",
        username_pass="nextcloud/username",
        password_pass="nextcloud/password",
        make_default=True,
    )
    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Dupont")

    result = CliRunner().invoke(app, ["application", "export", str(offer_id), "--to-nextcloud", "--dry-run"])

    assert result.exit_code == 0
    assert "Export Nextcloud préparé" in result.stdout
    assert "/Emploi/Candidatures/0001-chauffeur-pl" in result.stdout
    assert "offre.md" in result.stdout
    assert "brouillon.md" in result.stdout
    assert "nextcloud/password" not in result.stdout


def test_application_export_cli_dry_run_can_include_document_profile(monkeypatch, tmp_path):
    config = reload_config(monkeypatch, tmp_path)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    cv = tmp_path / "cv.pdf"
    letter = tmp_path / "lm.pdf"
    cv.write_bytes(b"CV")
    letter.write_bytes(b"LM")
    config.set_nextcloud_files_endpoint("emploi", base_url="https://nextcloud.test", remote_root="/Emploi", make_default=True)
    config.set_document_profile("poids-lourd", cv_path=str(cv), cover_letter_path=str(letter), make_default=True)
    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Dupont")

    result = CliRunner().invoke(
        app,
        [
            "application",
            "export",
            str(offer_id),
            "--to-nextcloud",
            "--dry-run",
            "--include-documents",
            "--document-profile",
            "poids-lourd",
        ],
    )

    assert result.exit_code == 0
    assert "CV-cv.pdf" in result.stdout
    assert "LM-lm.pdf" in result.stdout
    assert str(cv) not in result.stdout
    assert str(letter) not in result.stdout
