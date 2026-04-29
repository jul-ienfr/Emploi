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
