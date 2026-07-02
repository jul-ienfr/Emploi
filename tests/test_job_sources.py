"""Tests for APEC, Monster, and Cadremploi job source scrapers."""

from __future__ import annotations

import json
from unittest.mock import patch

from emploi.sources.apec import ApecOffer, _parse_offers_from_html, search_apec
from emploi.sources.cadremploi import CadremploiOffer, search_cadremploi
from emploi.sources.cadremploi import _parse_offers_from_html as parse_cadremploi
from emploi.sources.monster import MonsterOffer, search_monster
from emploi.sources.monster import _parse_offers_from_html as parse_monster

# ── APEC ──────────────────────────────────────────────────────────────────────


class TestApecParsing:
    def test_parse_json_response(self):
        data = {
            "offres": [
                {
                    "intitule": "Développeur Python",
                    "entreprise": {"nom": "TechCo"},
                    "lieu": "Paris",
                    "urlOffre": "https://www.apec.fr/offre/123",
                    "description": "Poste de développeur Python",
                    "typeContratLibelle": "CDI",
                    "salaire": {"libelle": "45k€"},
                }
            ]
        }
        offers = _parse_offers_from_html(json.dumps(data))
        assert len(offers) == 1
        assert offers[0].title == "Développeur Python"
        assert offers[0].company == "TechCo"
        assert offers[0].contract_type == "CDI"

    def test_parse_empty_json(self):
        offers = _parse_offers_from_html(json.dumps({"offres": []}))
        assert offers == []

    def test_parse_invalid_json(self):
        offers = _parse_offers_from_html("<html>not json</html>")
        assert isinstance(offers, list)

    def test_search_returns_list(self):
        with patch("emploi.sources.apec._fetch_html", return_value=json.dumps({"offres": []})):
            result = search_apec("python", max_results=5)
            assert isinstance(result, list)


# ── Monster ───────────────────────────────────────────────────────────────────


class TestMonsterParsing:
    def test_parse_json_ld(self):
        html = """
        <script type="application/ld+json">
        [{"@type": "JobPosting", "name": "Dev Python", "url": "https://monster.fr/emploi/1",
          "hiringOrganization": {"name": "Acme"}, "jobLocation": {"address": {"addressLocality": "Lyon"}}}]
        </script>
        """
        offers = parse_monster(html)
        assert len(offers) == 1
        assert offers[0].title == "Dev Python"
        assert offers[0].company == "Acme"

    def test_parse_empty_html(self):
        offers = parse_monster("<html></html>")
        assert offers == []

    def test_search_returns_list(self):
        with patch("emploi.sources.monster._fetch_html", return_value="<html></html>"):
            result = search_monster("python", max_results=5)
            assert isinstance(result, list)


# ── Cadremploi ────────────────────────────────────────────────────────────────


class TestCadremploiParsing:
    def test_parse_json_ld(self):
        html = """
        <script type="application/ld+json">
        {"@type": "JobPosting", "name": "Chef de projet", "url": "https://cadremploi.fr/emploi/456",
         "hiringOrganization": {"name": "GroupeX"}, "jobLocation": {"address": {"addressLocality": "Marseille"}}}
        </script>
        """
        offers = parse_cadremploi(html)
        assert len(offers) == 1
        assert offers[0].title == "Chef de projet"
        assert offers[0].company == "GroupeX"

    def test_parse_empty_html(self):
        offers = parse_cadremploi("<html></html>")
        assert offers == []

    def test_search_returns_list(self):
        with patch("emploi.sources.cadremploi._fetch_html", return_value="<html></html>"):
            result = search_cadremploi("python", max_results=5)
            assert isinstance(result, list)


# ── Unified interface ─────────────────────────────────────────────────────────


class TestUnifiedInterface:
    def test_all_sources_exported(self):
        from emploi.sources import (
            search_apec,
            search_cadremploi,
            search_monster,
        )

        assert callable(search_apec)
        assert callable(search_monster)
        assert callable(search_cadremploi)

    def test_all_offers_have_common_fields(self):
        """All offer types should have title, company, location, url, description."""
        for cls in (ApecOffer, MonsterOffer, CadremploiOffer):
            offer = cls(title="Test", company="Co", location="Paris", url="http://x", description="desc")
            assert offer.title == "Test"
            assert offer.company == "Co"
            assert offer.location == "Paris"
