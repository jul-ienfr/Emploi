from emploi.france_travail.extractors import extract_offer_detail, extract_offers


def test_extract_offers_from_snapshot_payload_cards():
    snapshot = {
        "url": "https://candidat.francetravail.fr/offres/recherche?motsCles=support",
        "cards": [
            {
                "title": "Technicien support informatique",
                "company": "Acme",
                "location": "Annecy",
                "url": "https://candidat.francetravail.fr/offres/recherche/detail/123ABC",
                "description": "Support utilisateurs débutant accepté",
                "contract_type": "CDI",
                "salary": "23000 EUR",
            }
        ],
        "text": "Technicien support informatique Acme Annecy",
    }

    offers = extract_offers(snapshot)

    assert len(offers) == 1
    offer = offers[0]
    assert offer.title == "Technicien support informatique"
    assert offer.company == "Acme"
    assert offer.location == "Annecy"
    assert offer.external_id == "123ABC"
    assert offer.browser_url.endswith("/123ABC")
    assert offer.raw_text


def test_extract_offers_from_html_links_and_text():
    html = """
    <article class="result">
      <h2>Développeur Python H/F</h2>
      <p class="company">Beta SAS</p>
      <p class="location">Cluses</p>
      <a href="/offres/recherche/detail/456DEF">Voir l'offre</a>
      <p>CDI Télétravail possible Django API</p>
    </article>
    """

    offers = extract_offers({"html": html})

    assert len(offers) == 1
    assert offers[0].title == "Développeur Python H/F"
    assert offers[0].company == "Beta SAS"
    assert offers[0].location == "Cluses"
    assert offers[0].external_id == "456DEF"
    assert offers[0].browser_url == "https://candidat.francetravail.fr/offres/recherche/detail/456DEF"
    assert "Django" in offers[0].description


def test_extract_offer_detail_detects_unavailable_and_apply_signal():
    active = extract_offer_detail({"text": "Offre active. Postuler / Candidater maintenant."})
    unavailable = extract_offer_detail({"text": "Cette offre n'est plus disponible"})

    assert active.is_active is True
    assert active.can_apply is True
    assert unavailable.is_active is False
    assert unavailable.can_apply is False
