from __future__ import annotations

from unittest.mock import MagicMock

from typer.testing import CliRunner

from emploi.cli import app
from emploi.db import connect, init_db

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixture: fake HTTP response for HelloWork search
# ---------------------------------------------------------------------------

def _hw_search_html() -> str:
    return """<div data-cy="serpCard" data-analytics-values-param='{"event":"generic_product","event_name":"product.click","product_data":[{"product_id":"80308933","product_variant":"URL_DO_CLASSIQUE_CLIENT","product_list":"LO-Suggest-Detail","product_position":1}]}'>
  <a href="/fr-fr/emplois/80308933.html"
     title="Carreleur H/F - Therm-Sanit Groupe"
     data-cy="offerTitle"
     aria-label="Voir offre de Carreleur H/F à Sillingy - 74, chez Therm-Sanit Groupe, pour un CDI, avec un salaire de 2 700 - 3 300 € / mois, en temps plein">
    <h3 class="inline">
      <p class="typo-l">Carreleur H/F</p>
      <p class="typo-s inline">Therm-Sanit Groupe</p>
    </h3>
  </a>
  <div data-cy="localisationCard">Sillingy - 74</div>
  <div data-cy="contractCard">CDI</div>
</div>"""


def _mock_urlopen(html: str):
    """Monkeypatch urllib.request.urlopen to return a given HTML response."""
    def fake_urlopen(*args, **kwargs):
        return MagicMock(
            read=lambda: html.encode("utf-8"),
            __enter__=lambda s: s,
            __exit__=lambda *a: None,
        )
    return fake_urlopen


def test_hellowork_search_cli_basic(monkeypatch, tmp_path):
    """'hellowork search' CLI command performs a search and displays results."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)

    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen(_hw_search_html()))

    result = runner.invoke(app, ["hellowork", "search", "carreleur", "--location", "Sillingy", "--contract", "CDI"])

    assert result.exit_code == 0, result.output
    assert "Carreleur H/F" in result.stdout


def test_hellowork_search_cli_empty_results(monkeypatch, tmp_path):
    """'hellowork search' with no results shows 0 offers."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    with connect(db_path) as conn:
        init_db(conn)

    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen(""))

    result = runner.invoke(app, ["hellowork", "search", "inexistant"])

    assert result.exit_code == 0, result.output
    assert "0 offre" in result.stdout


def test_search_profile_add_with_source_hellowork(tmp_path, monkeypatch):
    """search-profile add --source hellowork stores source correctly."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    result = runner.invoke(
        app,
        [
            "search-profile",
            "add",
            "hw-profile",
            "--query",
            "carreleur",
            "--where",
            "Sillingy",
            "--source",
            "hellowork",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Profil de recherche ajouté" in result.stdout

    with connect(db_path) as conn:
        init_db(conn)
        from emploi.db import get_saved_search
        saved = get_saved_search(conn, "hw-profile")
        assert saved is not None
        assert saved["source"] == "hellowork"


def test_search_profile_list_displays_source(tmp_path, monkeypatch):
    """search-profile list displays the source column."""
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    runner.invoke(
        app,
        ["search-profile", "add", "ft-profile", "--query", "support", "--source", "france-travail"],
    )
    runner.invoke(
        app,
        ["search-profile", "add", "hw-profile", "--query", "carreleur", "--source", "hellowork"],
    )

    listed = runner.invoke(app, ["search-profile", "list"])

    assert listed.exit_code == 0
    assert "Source" in listed.stdout
    assert "france-travail" in listed.stdout
    assert "hellowork" in listed.stdout
