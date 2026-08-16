"""Tests Nextcloud Phase 4 : contacts CardDAV et journal candidature."""

from __future__ import annotations

import re

from typer.testing import CliRunner

import emploi.config as emploi_config
from emploi.cli import app
from emploi.nextcloud_contacts import NextcloudContactsClient, _build_vcard, _parse_vcard
from emploi.nextcloud_files import append_journal_note

runner = CliRunner()


# ---------------------------------------------------------------------------
# Config : endpoint contacts
# ---------------------------------------------------------------------------


def test_contacts_endpoint_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    saved = emploi_config.set_nextcloud_contacts_endpoint(
        "recruteurs",
        base_url="https://nextcloud.test",
        username_pass="nextcloud/username",
        password_pass="nextcloud/password",
        make_default=True,
    )

    assert saved["name"] == "recruteurs"
    assert saved["addressbook"] == "contacts"
    assert saved["addressbook_home_url"] == "https://nextcloud.test/remote.php/dav/addressbooks/{username}/contacts"
    loaded = emploi_config.get_default_nextcloud_contacts_endpoint()
    assert loaded == saved


def test_contacts_cli_set_show_browse_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    result = runner.invoke(
        app,
        [
            "contact",
            "set",
            "recruteurs",
            "--base-url",
            "https://nextcloud.test",
            "--username-pass",
            "nextcloud/username",
            "--password-pass",
            "nextcloud/password",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "Nextcloud Contacts enregistré" in result.stdout

    show = runner.invoke(app, ["contact", "show", "recruteurs", "--json"])
    assert show.exit_code == 0, show.stdout
    assert '"addressbook": "contacts"' in show.stdout


def test_contacts_browse_requires_configured_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    missing = runner.invoke(app, ["contact", "browse", "--json"])
    assert missing.exit_code == 1
    assert "Aucun endpoint Contacts" in missing.stdout


# ---------------------------------------------------------------------------
# Client CardDAV : parsing + VCARD
# ---------------------------------------------------------------------------


VCARD_STREAM = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:recruteur-dupont\r\nFN:Marie Dupont\r\n"
    "N:Dupont;Marie;;;\r\nORG:Agence ABC\r\nEMAIL;TYPE=INTERNET:marie@abc.test\r\n"
    "TEL;TYPE=CELL:0600000000\r\nNOTE:Offre 63\r\nEND:VCARD\r\n"
    "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:recruteur-martin\r\nFN:Paul Martin\r\n"
    "N:Martin;Paul;;;\r\nEND:VCARD\r\n"
)


def test_parse_vcard_extracts_fields():
    contact = _parse_vcard(VCARD_STREAM.split("END:VCARD")[0] + "END:VCARD")
    assert contact.uid == "recruteur-dupont"
    assert contact.name == "Marie Dupont"
    assert contact.org == "Agence ABC"
    assert contact.email == "marie@abc.test"
    assert contact.phone == "0600000000"
    assert contact.note == "Offre 63"


def test_build_vcard_escapes_special_chars():
    vcard = _build_vcard(uid="u1", name="Dupont, Marie", org="Agence;X", email="a@b.test", note="Note\nligne2")
    assert "FN:Dupont\\, Marie" in vcard
    assert "ORG:Agence\\;X" in vcard
    assert "NOTE:Note\\nligne2" in vcard
    assert vcard.startswith("BEGIN:VCARD")
    assert vcard.endswith("END:VCARD\r\n")


def test_client_list_contacts_parses_stream(monkeypatch):
    client = NextcloudContactsClient({"base_url": "https://nextcloud.test"}, username="u", password="p")
    monkeypatch.setattr(
        NextcloudContactsClient, "_request", lambda self, method, url, data=b"", content_type="": VCARD_STREAM.encode()
    )

    contacts = client.list_contacts()

    assert len(contacts) == 2
    assert contacts[0].name == "Marie Dupont"
    assert contacts[1].name == "Paul Martin"


def test_client_add_contact_puts_vcard(monkeypatch):
    client = NextcloudContactsClient({"base_url": "https://nextcloud.test"}, username="u", password="p")
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        NextcloudContactsClient,
        "_request",
        lambda self, method, url, data=b"", content_type="": captured.update(method=method, url=url, data=data) or b"",
    )

    href = client.add_contact(uid="recruteur-dupont", name="Marie Dupont", org="Agence ABC", email="marie@abc.test")

    assert captured["method"] == "PUT"
    assert captured["url"] == "https://nextcloud.test/remote.php/dav/addressbooks/u/contacts/recruteur-dupont.vcf"
    assert b"FN:Marie Dupont" in captured["data"]
    assert href.endswith("recruteur-dupont.vcf")


# ---------------------------------------------------------------------------
# CLI : contact add (dry-run + live avec client fake)
# ---------------------------------------------------------------------------


def test_cli_contact_add_dry_run(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    emploi_config.set_nextcloud_contacts_endpoint(
        "recruteurs", base_url="https://nextcloud.test", username_pass="n/u", password_pass="n/p", make_default=True
    )

    result = runner.invoke(app, ["contact", "add", "Marie Dupont", "--org", "Agence ABC", "--dry-run"])

    assert result.exit_code == 0, result.stdout
    assert "Dry-run" in result.stdout


def test_cli_contact_add_live(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    emploi_config.set_nextcloud_contacts_endpoint(
        "recruteurs", base_url="https://nextcloud.test", username_pass="n/u", password_pass="n/p", make_default=True
    )
    captured: list[dict[str, object]] = []

    class FakeContactsClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def add_contact(self, *, uid, name, org="", email="", phone="", note="") -> str:
            captured.append({"uid": uid, "name": name, "org": org})
            return f"https://nextcloud.test/addressbooks/u/contacts/{uid}.vcf"

    monkeypatch.setattr("emploi.cli.contacts.NextcloudContactsClient", FakeContactsClient)

    result = runner.invoke(app, ["contact", "add", "Marie Dupont", "--org", "Agence ABC"])

    assert result.exit_code == 0, result.stdout
    assert "Contact créé" in result.stdout
    assert captured[0]["name"] == "Marie Dupont"
    assert captured[0]["uid"] == "marie-dupont"


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------


class FakeWebDAV:
    def __init__(self, existing: str | None = None) -> None:
        self.existing = existing
        self.uploaded: list[tuple[str, str]] = []

    def read_text(self, remote_path: str) -> str | None:
        return self.existing

    def upload_text(self, remote_path: str, content: str, content_type: str = "text/plain") -> None:
        self.uploaded.append((remote_path, content))

    def ensure_dir(self, remote_path: str) -> None:
        pass


def test_journal_append_dry_run_does_not_write():
    client = FakeWebDAV(existing="## 2026-08-01 09:00\n\nAncienne entrée\n")
    result = append_journal_note({}, content="Nouvelle entrée", client=client, dry_run=True)

    assert result.dry_run is True
    assert result.entry_count == 1  # dry-run: aucun accès réseau, comptage non lu
    assert client.uploaded == []


def test_journal_append_prepends_dated_entry():
    client = FakeWebDAV(existing="## 2026-08-01 09:00\n\nAncienne entrée\n")
    result = append_journal_note({}, content="Nouvelle entrée", client=client)

    assert client.uploaded[0][0] == "/Emploi/Journal.md"
    new_content = client.uploaded[0][1]
    assert new_content.startswith("## ")
    assert "Nouvelle entrée" in new_content
    assert "Ancienne entrée" in new_content
    assert "---" in new_content
    assert result.entry_count == 2


def test_journal_append_creates_file_when_missing():
    client = FakeWebDAV(existing=None)
    result = append_journal_note({}, content="Première entrée", client=client)

    assert result.entry_count == 1
    content = client.uploaded[0][1]
    assert "Première entrée" in content
    assert "Ancienne" not in content


def test_cli_journal_add_dry_run(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    emploi_config.set_nextcloud_files_endpoint(
        "emploi",
        base_url="https://nextcloud.test",
        remote_root="/Emploi",
        username_pass="n/u",
        password_pass="n/p",
        make_default=True,
    )

    result = runner.invoke(app, ["journal", "add", "Entretien prévu jeudi", "--dry-run"])

    assert result.exit_code == 0, result.stdout
    assert "préparée" in result.stdout
    assert "Entretien prévu jeudi" in result.stdout


def test_cli_journal_add_requires_files_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    result = runner.invoke(app, ["journal", "add", "Texte", "--dry-run"])

    assert result.exit_code != 0
    assert re.search(r"endpoint Nextcloud Files configur", re.sub(r"\s+", " ", result.stderr), re.IGNORECASE)
