import json

from typer.testing import CliRunner

from emploi.cli import app
from emploi.config import get_default_document_profile, get_document_profile, list_document_profiles


runner = CliRunner()


def test_document_profiles_config_roundtrip_with_multiple_cvs_and_letters(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    docs_dir = tmp_path / "docs"
    cv_pl = docs_dir / "cv-pl.pdf"
    cv_it = docs_dir / "cv-it.pdf"
    lm_pl = docs_dir / "lm-pl.pdf"
    lm_it = docs_dir / "lm-it.pdf"
    for path in (cv_pl, cv_it, lm_pl, lm_it):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))

    added_pl = runner.invoke(
        app,
        [
            "document-profile",
            "set",
            "poids-lourd",
            "--cv",
            str(cv_pl),
            "--letter",
            str(lm_pl),
            "--notes",
            "Profil chauffeur PL",
            "--default",
        ],
    )
    added_it = runner.invoke(
        app,
        ["document-profile", "set", "support-it", "--cv", str(cv_it), "--letter", str(lm_it)],
    )
    listed_json = runner.invoke(app, ["document-profile", "list", "--json"])
    status = runner.invoke(app, ["document-profile", "status", "poids-lourd", "--json"])

    assert added_pl.exit_code == 0, added_pl.output
    assert added_it.exit_code == 0, added_it.output
    assert "Profil documents enregistré" in added_pl.stdout
    assert listed_json.exit_code == 0
    payload = json.loads(listed_json.stdout)
    assert payload["default"] == "poids-lourd"
    assert {p["name"] for p in payload["profiles"]} == {"poids-lourd", "support-it"}
    assert json.loads(status.stdout)["profile"]["cv_path"] == str(cv_pl)

    profiles = list_document_profiles()
    assert len(profiles) == 2
    assert get_default_document_profile()["name"] == "poids-lourd"
    assert get_document_profile("support-it")["cover_letter_path"] == str(lm_it)


def test_document_profile_status_reports_missing_files(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    missing_cv = tmp_path / "missing-cv.pdf"
    missing_lm = tmp_path / "missing-lm.pdf"

    result = runner.invoke(
        app,
        ["document-profile", "set", "test", "--cv", str(missing_cv), "--letter", str(missing_lm), "--allow-missing"],
    )
    status = runner.invoke(app, ["document-profile", "status", "test", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(status.stdout)
    assert payload["status"] == "missing_files"
    assert payload["profile"]["cv_exists"] is False
    assert payload["profile"]["cover_letter_exists"] is False


def test_document_profile_set_rejects_missing_files_without_override(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    result = runner.invoke(app, ["document-profile", "set", "bad", "--cv", str(tmp_path / "absent.pdf")])

    assert result.exit_code != 0
    assert "Fichier introuvable" in result.output
