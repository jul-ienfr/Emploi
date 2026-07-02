from __future__ import annotations

from emploi.browser.models import BrowserCommandResult
from emploi.db import (
    add_saved_search,
    connect,
    get_offer,
    get_saved_search,
    init_db,
    list_saved_searches,
)
from emploi.france_travail.flows import SearchImportResult
from emploi.hellowork_search import (
    build_hellowork_search_url,
    extract_hellowork_offers,
    search_hellowork,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeBrowser:
    """Minimal browser stub for HelloWork search tests."""

    def __init__(self, snapshot_payload: dict | None = None):
        self.opened: list[str] = []
        self.snapshots: list[dict] = []
        if snapshot_payload is not None:
            self.snapshots.append(snapshot_payload)

    def lifecycle_open(self, url: str, *, site: str, profile: str):
        self.opened.append(url)
        return BrowserCommandResult("lifecycle_open", site, profile, {"ok": True})

    def snapshot(self, *, label: str | None = None, site: str = "france-travail", profile: str = "emploi"):
        payload = self.snapshots.pop(0) if self.snapshots else {"html": "", "text": ""}
        return BrowserCommandResult("snapshot", site, profile, payload)


# ---------------------------------------------------------------------------
# Realistic HelloWork HTML fixture
# ---------------------------------------------------------------------------

SINGLE_CARD_HTML = """
<div data-cy="serpCard" data-analytics-values-param='{"event":"generic_product","event_name":"product.click","product_data":[{"product_id":"80308933","product_variant":"URL_DO_CLASSIQUE_CLIENT","product_list":"LO-Suggest-Detail","product_position":1}]}'>
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
</div>
"""

MULTI_CARD_HTML = SINGLE_CARD_HTML + """
<div data-cy="serpCard" data-analytics-values-param='{"event":"generic_product","event_name":"product.click","product_data":[{"product_id":"99990001","product_variant":"URL_DO_CLASSIQUE_CLIENT","product_list":"LO-Suggest-Detail","product_position":2}]}'>
  <a href="/fr-fr/emplois/99990001.html"
     title="Développeur Python H/F - TechCorp"
     data-cy="offerTitle"
     aria-label="Voir offre de Développeur Python H/F à Annecy - 74, chez TechCorp, pour un CDD, avec un salaire de 3 000 € / mois, en temps plein">
    <h3 class="inline">
      <p class="typo-l">Développeur Python H/F</p>
      <p class="typo-s inline">TechCorp</p>
    </h3>
  </a>
  <div data-cy="localisationCard">Annecy - 74</div>
  <div data-cy="contractCard">CDD</div>
</div>
"""

HTML_NO_SALARY = """
<div data-cy="serpCard" data-analytics-values-param='{"event":"generic_product","event_name":"product.click","product_data":[{"product_id":"11111111","product_variant":"URL_DO_CLASSIQUE_CLIENT","product_list":"LO-Suggest-Detail","product_position":1}]}'>
  <a href="/fr-fr/emplois/11111111.html"
     title="Aide-soignant H/F - Hospice Civil"
     data-cy="offerTitle"
     aria-label="Voir offre de Aide-soignant H/F à Thonon - 74, chez Hospice Civil, pour un CDI, en temps plein">
    <h3 class="inline">
      <p class="typo-l">Aide-soignant H/F</p>
      <p class="typo-s inline">Hospice Civil</p>
    </h3>
  </a>
  <div data-cy="localisationCard">Thonon - 74</div>
  <div data-cy="contractCard">CDI</div>
</div>
"""


# ===========================================================================
# Tests for build_hellowork_search_url
# ===========================================================================

class TestBuildHelloworkSearchUrl:
    def test_query_only(self):
        url = build_hellowork_search_url("carreleur")
        assert url == "https://www.hellowork.com/fr-fr/emploi/recherche.html?k=carreleur"

    def test_query_with_spaces(self):
        url = build_hellowork_search_url("développeur python")
        assert "k=d%C3%A9veloppeur+python" in url

    def test_query_with_location(self):
        url = build_hellowork_search_url("carreleur", location="Sillingy")
        assert "k=carreleur" in url
        assert "l=Sillingy" in url

    def test_query_with_contract(self):
        url = build_hellowork_search_url("carreleur", contract="CDI")
        assert "k=carreleur" in url
        assert "c=CDI" in url

    def test_all_params(self):
        url = build_hellowork_search_url("carreleur", location="Bogève", contract="CDI")
        assert "k=carreleur" in url
        assert "l=Bog%C3%A8ve" in url
        assert "c=CDI" in url

    def test_empty_optional_params_omitted(self):
        url = build_hellowork_search_url("python")
        assert "l=" not in url
        assert "c=" not in url

    def test_empty_location_not_included(self):
        url = build_hellowork_search_url("python", location="", contract="CDI")
        assert "l=" not in url
        assert "c=CDI" in url


# ===========================================================================
# Tests for extract_hellowork_offers
# ===========================================================================

class TestExtractHelloworkOffers:
    def test_single_card(self):
        offers = extract_hellowork_offers(SINGLE_CARD_HTML)
        assert len(offers) == 1
        offer = offers[0]
        assert offer["external_id"] == "80308933"
        assert offer["title"] == "Carreleur H/F"
        assert offer["company"] == "Therm-Sanit Groupe"
        assert offer["location"] == "Sillingy - 74"
        assert offer["contract_type"] == "CDI"
        assert offer["browser_url"] == "https://www.hellowork.com/fr-fr/emplois/80308933.html"
        assert offer["salary"] == "2 700 - 3 300 € / mois"

    def test_multiple_cards(self):
        offers = extract_hellowork_offers(MULTI_CARD_HTML)
        assert len(offers) == 2
        assert offers[0]["external_id"] == "80308933"
        assert offers[0]["title"] == "Carreleur H/F"
        assert offers[1]["external_id"] == "99990001"
        assert offers[1]["title"] == "Développeur Python H/F"
        assert offers[1]["contract_type"] == "CDD"

    def test_no_salary(self):
        offers = extract_hellowork_offers(HTML_NO_SALARY)
        assert len(offers) == 1
        assert offers[0]["salary"] == ""

    def test_empty_html(self):
        offers = extract_hellowork_offers("")
        assert offers == []

    def test_no_cards(self):
        offers = extract_hellowork_offers("<html><body>Nothing here</body></html>")
        assert offers == []


# ===========================================================================
# Tests for search_hellowork (with mocked browser)
# ===========================================================================

class TestSearchHellowork:
    def test_search_hellowork_returns_import_results(self, tmp_path, monkeypatch):
        conn = connect(tmp_path / "emploi.sqlite")
        init_db(conn)
        monkeypatch.setattr(
            "emploi.hellowork_search._fetch_hellowork_html",
            lambda url: SINGLE_CARD_HTML,
        )

        results = search_hellowork(
            conn,
            query="carreleur",
            location="Sillingy",
            contract="CDI",
        )

        assert len(results) == 1
        assert isinstance(results[0], SearchImportResult)
        assert results[0].created is True
        assert results[0].title == "Carreleur H/F"
        assert "hellowork.com" in results[0].browser_url

    def test_search_hellowork_calls_correct_url(self, tmp_path, monkeypatch):
        conn = connect(tmp_path / "emploi.sqlite")
        init_db(conn)
        urls_called = []
        monkeypatch.setattr(
            "emploi.hellowork_search._fetch_hellowork_html",
            lambda url: (urls_called.append(url), SINGLE_CARD_HTML)[1],
        )

        search_hellowork(
            conn,
            query="carreleur",
            location="Sillingy",
            contract="CDI",
        )

        assert len(urls_called) == 1
        url = urls_called[0]
        assert "hellowork.com" in url
        assert "k=carreleur" in url
        assert "/fr-fr/emploi/recherche.html" in url

    def test_search_hellowork_upserts_offer_in_db(self, tmp_path, monkeypatch):
        conn = connect(tmp_path / "emploi.sqlite")
        init_db(conn)
        monkeypatch.setattr(
            "emploi.hellowork_search._fetch_hellowork_html",
            lambda url: SINGLE_CARD_HTML,
        )

        results = search_hellowork(
            conn,
            query="carreleur",
        )

        assert len(results) == 1
        offer = get_offer(conn, results[0].offer_id)
        assert offer is not None
        assert offer["title"] == "Carreleur H/F"
        assert offer["external_source"] == "hellowork"
        assert offer["external_id"] == "80308933"
        assert "hellowork.com" in offer["browser_url"]

    def test_search_hellowork_idempotent_on_repeated_run(self, tmp_path, monkeypatch):
        conn = connect(tmp_path / "emploi.sqlite")
        init_db(conn)
        monkeypatch.setattr(
            "emploi.hellowork_search._fetch_hellowork_html",
            lambda url: SINGLE_CARD_HTML,
        )

        results1 = search_hellowork(conn, query="carreleur")
        assert results1[0].created is True

        results2 = search_hellowork(conn, query="carreleur")
        assert results2[0].created is False
        assert results2[0].offer_id == results1[0].offer_id

    def test_search_hellowork_filters_irrelevant_offers(self, tmp_path, monkeypatch):
        """Offers that don't match the query should be filtered out."""
        conn = connect(tmp_path / "emploi.sqlite")
        init_db(conn)
        monkeypatch.setattr(
            "emploi.hellowork_search._fetch_hellowork_html",
            lambda url: MULTI_CARD_HTML,
        )

        results = search_hellowork(
            conn,
            query="carreleur",
        )

        # Only the carreleur offer should be returned (relevance filtered)
        titles = [r.title for r in results]
        assert "Carreleur H/F" in titles
        assert len(results) >= 1

    def test_search_hellowork_no_results_empty_html(self, tmp_path, monkeypatch):
        conn = connect(tmp_path / "emploi.sqlite")
        init_db(conn)
        monkeypatch.setattr(
            "emploi.hellowork_search._fetch_hellowork_html",
            lambda url: "",
        )

        results = search_hellowork(
            conn,
            query="carreleur",
        )

        assert results == []


# ===========================================================================
# Tests for DB migration: source column on saved_searches
# ===========================================================================

class TestSavedSearchSourceColumn:
    def test_migration_adds_source_column(self, tmp_path):
        """Migration must add 'source' column to saved_searches."""
        from emploi.migrations import _table_columns

        conn = connect(tmp_path / "emploi.sqlite")
        init_db(conn)

        columns = _table_columns(conn, "saved_searches")
        assert "source" in columns

    def test_add_saved_search_default_source(self, tmp_path):
        """add_saved_search defaults source to 'france-travail'."""
        conn = connect(tmp_path / "emploi.sqlite")
        init_db(conn)

        search_id = add_saved_search(
            conn,
            name="test-default",
            query="support",
            where_text="Annecy",
        )
        saved = get_saved_search(conn, search_id)
        assert saved is not None
        assert saved["source"] == "all"

    def test_add_saved_search_explicit_source(self, tmp_path):
        """add_saved_search stores explicit source."""
        conn = connect(tmp_path / "emploi.sqlite")
        init_db(conn)

        search_id = add_saved_search(
            conn,
            name="test-hellowork",
            query="carreleur",
            where_text="Sillingy",
            source="hellowork",
        )
        saved = get_saved_search(conn, search_id)
        assert saved is not None
        assert saved["source"] == "hellowork"

    def test_list_saved_searches_includes_source(self, tmp_path):
        conn = connect(tmp_path / "emploi.sqlite")
        init_db(conn)

        add_saved_search(conn, name="ft-profile", query="support", source="france-travail")
        add_saved_search(conn, name="hw-profile", query="carreleur", source="hellowork")

        searches = list_saved_searches(conn)
        sources = {s["name"]: s["source"] for s in searches}
        assert sources["ft-profile"] == "france-travail"
        assert sources["hw-profile"] == "hellowork"

    def test_install_default_julien_profiles_use_france_travail_source(self, tmp_path):
        from emploi.db import install_default_julien_search_profiles

        conn = connect(tmp_path / "emploi.sqlite")
        init_db(conn)
        install_default_julien_search_profiles(conn)

        searches = list_saved_searches(conn)
        assert all(s["source"] in ("all", "france-travail") for s in searches)


# ===========================================================================
# Tests for search-profile run dispatch
# ===========================================================================

class TestSearchProfileRunDispatch:
    def test_search_profile_run_dispatches_to_hellowork(self, tmp_path, monkeypatch):
        """search-profile run with source=hellowork dispatches to search_hellowork."""
        monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
        conn = connect(tmp_path / "emploi.sqlite")
        init_db(conn)
        add_saved_search(
            conn,
            name="hw-profile",
            query="carreleur",
            where_text="Sillingy",
            source="hellowork",
        )

        dispatched_to: list[str] = []

        def fake_run_saved_search(conn, search_id_or_name, **kwargs):
            dispatched_to.append("france_travail")
            return []

        def fake_run_hellowork_saved_search(conn, search_id_or_name, **kwargs):
            dispatched_to.append("hellowork")
            return []

        monkeypatch.setattr("emploi.cli.search_profile.run_saved_search", fake_run_saved_search)
        monkeypatch.setattr("emploi.cli.search_profile.run_hellowork_saved_search", fake_run_hellowork_saved_search)

        from typer.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(
            __import__("emploi.cli", fromlist=["app"]).app,
            ["search-profile", "run", "--all"],
        )

        assert result.exit_code == 0, result.output
        assert dispatched_to == ["hellowork"]

    def test_search_profile_run_dispatches_to_france_travail_by_default(self, tmp_path, monkeypatch):
        """search-profile run with default source dispatches to run_saved_search."""
        monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
        conn = connect(tmp_path / "emploi.sqlite")
        init_db(conn)
        add_saved_search(
            conn,
            name="ft-profile",
            query="support",
            where_text="Annecy",
        )

        dispatched_to: list[str] = []

        def fake_run_saved_search(conn, search_id_or_name, **kwargs):
            dispatched_to.append("france_travail")
            return []

        def fake_run_hellowork_saved_search(conn, search_id_or_name, **kwargs):
            dispatched_to.append("hellowork")
            return []

        monkeypatch.setattr("emploi.cli.search_profile.run_saved_search", fake_run_saved_search)
        monkeypatch.setattr("emploi.cli.search_profile.run_hellowork_saved_search", fake_run_hellowork_saved_search)

        from typer.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(
            __import__("emploi.cli", fromlist=["app"]).app,
            ["search-profile", "run", "--all"],
        )

        assert result.exit_code == 0, result.output
        assert dispatched_to == ["france_travail", "hellowork"]
