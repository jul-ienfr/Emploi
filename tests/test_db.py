from emploi.db import add_offer, connect, init_db, list_offers, get_offer, rescore_offer, update_offer_status


def test_init_db_and_add_offer_roundtrip(tmp_path):
    db_path = tmp_path / "emploi.sqlite"
    conn = connect(db_path)
    init_db(conn)

    offer_id = add_offer(
        conn,
        title="Technicien support",
        company="Entreprise X",
        location="Bonneville",
        url="https://example.test/offre",
        source="manual",
        description="Support informatique débutant accepté",
    )

    offer = get_offer(conn, offer_id)

    assert offer is not None
    assert offer["id"] == offer_id
    assert offer["title"] == "Technicien support"
    assert offer["status"] == "new"
    assert offer["score"] is not None


def test_list_offers_can_filter_by_status(tmp_path):
    db_path = tmp_path / "emploi.sqlite"
    conn = connect(db_path)
    init_db(conn)
    first = add_offer(conn, title="Support", company="A")
    add_offer(conn, title="Vente", company="B")
    update_offer_status(conn, first, "interesting")

    offers = list_offers(conn, status="interesting")

    assert len(offers) == 1
    assert offers[0]["title"] == "Support"


def test_add_offer_and_rescore_use_richer_v2_reason_lines(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn,
        title="Support Python",
        location="Bogève",
        description="CDI télétravail, débutant accepté, candidature simple.",
        salary="30k€",
        remote="remote",
        contract_type="CDI",
    )

    offer = get_offer(conn, offer_id)
    assert offer is not None
    assert "Remote: télétravail explicite, très adapté depuis Bogève" in offer["score_reasons"]
    assert "\n" in offer["score_reasons"]

    conn.execute(
        "UPDATE offers SET description = ?, remote = ?, contract_type = ?, score_reasons = '' WHERE id = ?",
        ("Présentiel obligatoire, 5 ans exigés, dossier complet.", "pas de télétravail", "freelance", offer_id),
    )
    conn.commit()

    rescored = rescore_offer(conn, offer_id)

    assert "Remote: présentiel obligatoire ou télétravail absent" in rescored["score_reasons"]
    assert "Réalisme: exigences trop élevées pour le profil visé" in rescored["score_reasons"]
