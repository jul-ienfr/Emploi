from typer.testing import CliRunner

from emploi.cli import app


runner = CliRunner()


def test_cli_init_offer_add_list_and_report(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0
    assert "Base initialisée" in init_result.stdout

    add_result = runner.invoke(
        app,
        [
            "offer",
            "add",
            "--title",
            "Technicien support",
            "--company",
            "Entreprise X",
            "--location",
            "Bonneville",
            "--description",
            "Support informatique débutant accepté",
        ],
    )
    assert add_result.exit_code == 0
    assert "Offre ajoutée" in add_result.stdout

    list_result = runner.invoke(app, ["offer", "list"])
    assert list_result.exit_code == 0
    assert "Technicien support" in list_result.stdout
    assert "new" in list_result.stdout

    report_result = runner.invoke(app, ["report"])
    assert report_result.exit_code == 0
    assert "Offres enregistrées : 1" in report_result.stdout


def test_cli_apply_creates_application(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    runner.invoke(app, ["init"])
    runner.invoke(app, ["offer", "add", "--title", "Support", "--company", "A"])

    apply_result = runner.invoke(app, ["apply", "1"])

    assert apply_result.exit_code == 0
    assert "Candidature créée" in apply_result.stdout

    applications = runner.invoke(app, ["application", "list"])
    assert applications.exit_code == 0
    assert "sent" in applications.stdout
    assert "Support" in applications.stdout
