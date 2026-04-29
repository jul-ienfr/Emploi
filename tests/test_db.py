from emploi.db import add_offer, connect, init_db, list_offers, get_offer, update_offer_status


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
