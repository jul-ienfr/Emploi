"""Tests for the cross-source aggregator and search-all command."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from emploi.sources.aggregator import (
    AggregatedOffer,
    _make_dedup_key,
    deduplicate_offers,
    list_sources,
    search_all,
)

runner = CliRunner()


def _make_offer(title, company, location, source):
    return AggregatedOffer(
        title=title,
        company=company,
        location=location,
        url="http://x",
        description="desc",
        source=source,
        dedup_key=_make_dedup_key(title, company, location),
    )


# ── Dedup ────────────────────────────────────────────────────────────────────


class TestDedup:
    def test_same_title_same_company_deduped(self):
        a = _make_offer("Dev Python", "Acme", "Paris", "apec")
        b = _make_offer("Dev Python", "Acme", "Paris", "monster")
        result = deduplicate_offers([a, b])
        assert len(result) == 1
        assert result[0].source == "apec"

    def test_different_offers_kept(self):
        a = _make_offer("Dev Python", "Acme", "Paris", "apec")
        b = _make_offer("Dev Java", "Beta", "Lyon", "monster")
        result = deduplicate_offers([a, b])
        assert len(result) == 2

    def test_same_title_different_company_kept(self):
        a = _make_offer("Dev Python", "Acme", "Paris", "apec")
        b = _make_offer("Dev Python", "Beta", "Paris", "monster")
        result = deduplicate_offers([a, b])
        assert len(result) == 2

    def test_empty_list(self):
        assert deduplicate_offers([]) == []


# ── AggregatedOffer ──────────────────────────────────────────────────────────


class TestAggregatedOffer:
    def test_display_title_with_source(self):
        offer = _make_offer("Dev Python", "Acme", "Paris", "apec")
        assert "[apec]" in offer.display_title

    def test_to_dict(self):
        offer = _make_offer("Dev Python", "Acme", "Paris", "apec")
        d = offer.to_dict()
        assert d["title"] == "Dev Python"
        assert d["source"] == "apec"


# ── Source registry ──────────────────────────────────────────────────────────


class TestSourceRegistry:
    def test_list_sources(self):
        sources = list_sources()
        assert len(sources) >= 7  # 3 FR + 4 CH
        countries = {s["country"] for s in sources}
        assert "FR" in countries
        assert "CH" in countries

    def test_all_sources_have_name_and_country(self):
        for src in list_sources():
            assert "name" in src
            assert "country" in src


# ── search_all ───────────────────────────────────────────────────────────────


class TestSearchAll:
    def test_search_all_with_mocked_sources(self):
        def fake_search(query, location="", max_results=20):
            return [
                type(
                    "Offer",
                    (),
                    {
                        "title": "Test Offer",
                        "company": "Co",
                        "location": "City",
                        "url": "http://x",
                        "description": "desc",
                        "contract_type": "CDI",
                        "salary": "",
                    },
                )()
            ]

        with patch.dict(
            "emploi.sources.aggregator.SOURCE_REGISTRY",
            {
                "test_src": (fake_search, "FR"),
            },
        ):
            results = search_all("python", countries=["FR"])
            assert len(results) == 1
            assert results[0].source == "test_src"

    def test_search_all_filters_by_country(self):
        calls = []

        def fake_search(query, location="", max_results=20):
            calls.append(True)
            return []

        with patch.dict(
            "emploi.sources.aggregator.SOURCE_REGISTRY",
            {
                "src_fr": (fake_search, "FR"),
                "src_ch": (fake_search, "CH"),
            },
        ):
            search_all("python", countries=["FR"])
            assert len(calls) == 1  # Only FR source called

    def test_search_all_handles_source_failure(self):
        def failing_search(query, location="", max_results=20):
            raise RuntimeError("Network error")

        def working_search(query, location="", max_results=20):
            return [
                type(
                    "Offer",
                    (),
                    {
                        "title": "OK",
                        "company": "",
                        "location": "",
                        "url": "",
                        "description": "",
                        "contract_type": "",
                        "salary": "",
                    },
                )()
            ]

        with patch(
            "emploi.sources.aggregator.SOURCE_REGISTRY",
            {
                "failing": (failing_search, "FR"),
                "working": (working_search, "CH"),
            },
        ):
            results = search_all("test")
            assert len(results) == 1  # Only working source returned results


# ── CLI search-all command ───────────────────────────────────────────────────


class TestSearchAllCLI:
    def test_search_all_empty(self):
        with patch("emploi.sources.aggregator.SOURCE_REGISTRY", {}):
            result = runner.invoke(
                __import__("emploi.cli", fromlist=["app"]).app,
                ["search-all", "test"],
            )
            assert result.exit_code == 0
            assert "Aucune offre" in result.stdout

    def test_search_all_json_output(self):
        def fake_search(query, location="", max_results=20):
            return [
                type(
                    "Offer",
                    (),
                    {
                        "title": "Test",
                        "company": "Co",
                        "location": "City",
                        "url": "http://x",
                        "description": "desc",
                        "contract_type": "CDI",
                        "salary": "50k",
                    },
                )()
            ]

        with patch("emploi.sources.aggregator.SOURCE_REGISTRY", {"test": (fake_search, "FR")}):
            result = runner.invoke(
                __import__("emploi.cli", fromlist=["app"]).app,
                ["search-all", "python", "--json"],
            )
            assert result.exit_code == 0
            assert "Test" in result.stdout
