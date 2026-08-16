"""Tests identité locale + pré-remplissage automatique HelloWork."""

from __future__ import annotations

from typer.testing import CliRunner

import emploi.config as emploi_config
from emploi.cli import app
from emploi.db import add_offer, connect, init_db
from emploi.hellowork import _inspect_expression, apply_hellowork

runner = CliRunner()


# ---------------------------------------------------------------------------
# Config identité
# ---------------------------------------------------------------------------


def test_identity_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    assert emploi_config.get_identity() == {"firstname": "", "lastname": "", "email": ""}

    saved = emploi_config.set_identity(firstname="Julien", lastname="Frendo-Rossi", email="julien@example.test")
    assert saved == {"firstname": "Julien", "lastname": "Frendo-Rossi", "email": "julien@example.test"}

    # mise à jour partielle : les champs vides conservent l'existant
    updated = emploi_config.set_identity(email="autre@example.test")
    assert updated["firstname"] == "Julien"
    assert updated["lastname"] == "Frendo-Rossi"
    assert updated["email"] == "autre@example.test"

    assert emploi_config.get_identity() == updated


def test_cli_identity_set_and_show(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    result = runner.invoke(
        app,
        ["identity", "set", "--firstname", "Julien", "--lastname", "Frendo-Rossi", "--email", "j@example.test"],
    )
    assert result.exit_code == 0, result.stdout
    assert "Identité enregistrée" in result.stdout
    assert "Julien" in result.stdout

    show = runner.invoke(app, ["identity", "show", "--json"])
    assert show.exit_code == 0, show.stdout
    assert '"firstname": "Julien"' in show.stdout
    assert '"email": "j@example.test"' in show.stdout


def test_cli_identity_show_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    result = runner.invoke(app, ["identity", "show"])

    assert result.exit_code == 0, result.stdout
    assert "Aucune identité configurée" in result.stdout


# ---------------------------------------------------------------------------
# Pré-remplissage : l'expression JS embarque identité + CV
# ---------------------------------------------------------------------------


def test_inspect_expression_includes_identity_and_cv_upload():
    expr = _inspect_expression(
        "123",
        "",
        identity={"firstname": "Julien", "lastname": "Frendo-Rossi", "email": "j@example.test"},
        cv_b64="QUJDRA==",
        cv_name="cv.pdf",
    )

    assert '"Julien"' in expr
    assert '"Frendo-Rossi"' in expr
    assert '"j@example.test"' in expr
    assert "setField('Firstname', identity.firstname)" in expr
    assert "setField('Email', identity.email)" in expr
    assert 'cvB64 = "QUJDRA=="' in expr
    assert "/fr-fr/uploadcv?formId=offer-detail-main-step-form&isRequired=True" in expr
    assert "JweHashResume" in expr


def test_inspect_expression_escapes_identity_specials():
    expr = _inspect_expression("123", "", identity={"firstname": "Jo\"\\'x", "lastname": "A;B", "email": "a@b.c"})
    # JSON-encodé dans le JS : les guillemets/backslashes sont échappés,
    # le littéral JS reste valide (les ; dans une chaîne sont inoffensifs)
    assert 'Jo\\"' in expr
    assert "\\\\'" in expr
    assert '"A;B"' in expr


def test_apply_hellowork_threads_identity_and_cv(tmp_path, monkeypatch):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn, title="Chauffeur PL", company="Slash Intérim", url="https://www.hellowork.com/fr-fr/emplois/123.html"
    )
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF-1.4 fake cv")

    class FakeBrowser:
        def __init__(self) -> None:
            self.expressions: list[str] = []

        def lifecycle_open(self, url, *, site, profile):
            return {"success": True}

        def console_eval(self, expression, *, site, profile):
            self.expressions.append(expression)
            payload = {
                "url": "https://www.hellowork.com/fr-fr/emplois/123.html#postuler",
                "offerExternalId": "123",
                "initialStatus": 200,
                "formPresent": True,
                "funnelIdPresent": True,
                "firstnamePresent": True,
                "lastnamePresent": True,
                "emailPresent": True,
                "motivationPresent": True,
                "submitButtonPresent": True,
                "cvPresent": True,
                "cguConsentRequired": True,
                "cguConsentChecked": True,
            }
            from emploi.browser.models import BrowserCommandResult

            return BrowserCommandResult(
                command="console_eval",
                site=site,
                profile=profile,
                payload={"result": __import__("json").dumps(payload)},
            )

    browser = FakeBrowser()
    result = apply_hellowork(
        conn,
        offer_id,
        browser=browser,
        site="france-travail",
        profile="emploi-candidature",
        kanban=False,
        identity={"firstname": "Julien", "lastname": "Frendo-Rossi", "email": "j@example.test"},
        cv_path=str(cv_path),
    )

    assert result.dry_run is True
    expr = browser.expressions[0]
    assert '"Julien"' in expr
    assert "JweHashResume" in expr
    # le CV est lu en base64 depuis le disque
    import base64

    assert base64.b64encode(b"%PDF-1.4 fake cv").decode() in expr
