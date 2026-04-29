from emploi.scoring import score_offer


def test_score_offer_rewards_support_remote_and_beginner_friendly():
    offer = {
        "title": "Technicien support informatique",
        "location": "Bonneville",
        "description": "Support informatique, débutant accepté, télétravail partiel, CDI.",
    }

    result = score_offer(offer)

    assert result.score > 70
    assert "support/informatique" in result.reasons
    assert "débutant accepté" in result.reasons
    assert "télétravail" in result.reasons


def test_score_offer_penalizes_car_and_field_sales_constraints():
    offer = {
        "title": "Commercial terrain",
        "location": "Annecy",
        "description": "Permis B et véhicule obligatoire. Déplacements fréquents.",
    }

    result = score_offer(offer)

    assert result.score < 50
    assert "permis/véhicule obligatoire" in result.reasons
    assert "déplacements fréquents" in result.reasons
    assert "commercial terrain" in result.reasons


def test_score_offer_stays_between_zero_and_one_hundred():
    offer = {
        "title": "Commercial terrain",
        "description": "Permis B obligatoire. Véhicule obligatoire. Déplacements fréquents. Expérience 5 ans obligatoire.",
    }

    result = score_offer(offer)

    assert 0 <= result.score <= 100


def test_score_offer_v2_rewards_julien_remote_local_salary_and_easy_application():
    offer = {
        "title": "Développeur Python support automatisation",
        "location": "Télétravail depuis Bogève",
        "description": "CDI junior, candidature simple par email, formation possible.",
        "salary": "32 000 € brut annuel",
        "remote": "100% télétravail",
        "contract_type": "CDI",
    }

    result = score_offer(offer)

    assert result.score >= 90
    assert "Remote: télétravail explicite, très adapté depuis Bogève" in result.reasons
    assert "Localisation: Bogève/Haute-Savoie ou zone proche identifiable" in result.reasons
    assert "Contrat: CDI ou CDD stable" in result.reasons
    assert "Salaire: rémunération indiquée" in result.reasons
    assert "Réalisme: profil junior/formation compatible" in result.reasons
    assert "Candidature: démarche simple" in result.reasons


def test_score_offer_v2_penalizes_location_transport_unrealistic_and_high_effort():
    offer = {
        "title": "Ingénieur senior commercial terrain",
        "location": "Annecy, déplacements quotidiens",
        "description": "Présentiel obligatoire, permis B et véhicule obligatoire. 7 ans d'expérience exigée. Lettre manuscrite, dossier complet et relances téléphoniques demandés.",
        "remote": "pas de télétravail",
        "contract_type": "freelance",
    }

    result = score_offer(offer)

    assert result.score <= 20
    assert "Remote: présentiel obligatoire ou télétravail absent" in result.reasons
    assert "Localisation: trajet risqué depuis Bogève sans transport fiable" in result.reasons
    assert "Contrat: freelance/stage/alternance moins prioritaire" in result.reasons
    assert "Réalisme: exigences trop élevées pour le profil visé" in result.reasons
    assert "Candidature: effort élevé ou friction importante" in result.reasons
