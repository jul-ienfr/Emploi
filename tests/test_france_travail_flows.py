from emploi.browser.models import BrowserCommandResult
from emploi.db import add_application, add_offer, connect, get_offer, init_db, list_offer_events
from emploi.france_travail.flows import apply_check_offer, build_search_url, draft_application, refresh_offer, search_offers


class FakeBrowser:
    def __init__(self, snapshots, console_values=None):
        self.snapshots = list(snapshots)
        self.console_values = list(console_values or [])
        self.opened = []

    def open(self, url, *, site="france-travail", profile="emploi"):
        self.opened.append((url, site, profile))
        return BrowserCommandResult("open", site, profile, {"ok": True, "url": url})

    def lifecycle_open(self, url, *, site="france-travail", profile="emploi"):
        self.opened.append((url, site, profile))
        return BrowserCommandResult("lifecycle_open", site, profile, {"ok": True, "url": url})

    def snapshot(self, *, label=None, site="france-travail", profile="emploi"):
        payload = self.snapshots.pop(0)
        return BrowserCommandResult("snapshot", site, profile, payload)

    def console_eval(self, expression, *, site="france-travail", profile="emploi"):
        return BrowserCommandResult("console_eval", site, profile, {"value": self.console_values.pop(0)})


def test_build_search_url_normalizes_bogeve_and_contract_filter():
    url = build_search_url('poids lourd', 'Bogève', 10, 'CDI')

    assert 'lieux=74038' in url
    assert 'rayon=10' in url
    assert 'typeContrat=CDI' in url


def test_build_search_url_keeps_only_positive_keywords_for_france_travail_ui():
    url = build_search_url('poids lourd -SPL -&amp;#34;super poids lourd&amp;#34;', 'Bogève', 10, 'CDI')

    assert '%26amp' not in url
    assert '%2334' not in url
    assert '-SPL' not in url
    assert 'super+poids+lourd' not in url
    assert 'motsCles=poids+lourd' in url


def test_search_offers_filters_with_unescaped_saved_query(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    browser = FakeBrowser(
        [{"snapshot": "url: ...\ntitle: 60 offres", "refsCount": 118}],
        console_values=[
            [
                {
                    "title": "Chauffeur de poids lourd (H/F)",
                    "href": "https://candidat.francetravail.fr/offres/recherche/detail/207LRYQ",
                    "text": "Chauffeur de poids lourd CDI Temps plein",
                    "description": "Permis C obligatoire",
                    "contract_type": "CDI Temps plein",
                }
            ]
        ],
    )

    results = search_offers(
        conn,
        query='poids lourd -SPL -&amp;#34;super poids lourd&amp;#34;',
        location="Bogève",
        radius=10,
        contract="CDI",
        browser=browser,
    )

    assert len(results) == 1
    assert '%26amp' not in browser.opened[0][0]
    assert '%2334' not in browser.opened[0][0]


def test_search_offers_marks_existing_offers_inactive_when_excluded_by_profile(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    spl_id = add_offer(
        conn,
        title="Conducteur SPL H/F (H/F)",
        company="EXCOFFIER SERVICES",
        location="74 - Bons-en-Chablais",
        description="Mission recyclage",
        contract_type="CDI - Temps plein",
        external_source="france-travail",
        external_id="205JDWR",
        browser_url="https://candidat.francetravail.fr/offres/recherche/detail/205JDWR",
        raw_extracted_text="Conducteur SPL H/F (H/F) CDI Temps plein",
        is_active=True,
    )
    browser = FakeBrowser(
        [{"snapshot": "url: ...\ntitle: 18 offres", "refsCount": 118}],
        console_values=[
            [
                {
                    "title": "Conducteur SPL H/F (H/F)",
                    "href": "https://candidat.francetravail.fr/offres/recherche/detail/205JDWR",
                    "text": "Conducteur SPL H/F (H/F) EXCOFFIER SERVICES CDI Temps plein",
                    "description": "Mission recyclage",
                    "contract_type": "CDI Temps plein",
                },
                {
                    "title": "Chauffeur de poids lourd (H/F)",
                    "href": "https://candidat.francetravail.fr/offres/recherche/detail/207LRYQ",
                    "text": "Chauffeur de poids lourd CDI Temps plein",
                    "description": "Permis C obligatoire",
                    "contract_type": "CDI Temps plein",
                },
            ]
        ],
    )

    results = search_offers(
        conn,
        query='poids lourd -SPL -"super poids lourd"',
        location="Bogève",
        radius=10,
        contract="CDI",
        browser=browser,
    )

    assert [result.title for result in results] == ["Chauffeur de poids lourd (H/F)"]
    excluded = get_offer(conn, spl_id)
    assert excluded["is_active"] == 0
    assert excluded["status"] == "archived"


def test_search_offers_uses_browser_and_upserts_france_travail_offers(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    browser = FakeBrowser([
        {
            "url": "https://candidat.francetravail.fr/offres/recherche?motsCles=support",
            "cards": [
                {
                    "title": "Technicien support",
                    "company": "Acme",
                    "location": "Annecy",
                    "url": "https://candidat.francetravail.fr/offres/recherche/detail/ABC123",
                    "description": "Support informatique débutant accepté",
                }
            ],
            "text": "Technicien support Acme Annecy Candidater",
        }
    ])

    results = search_offers(conn, query="support", location="Annecy", browser=browser)
    again = search_offers(conn, query="support", location="Annecy", browser=FakeBrowser([browser.snapshots[0] if browser.snapshots else {
        "cards": [{"title": "Technicien support", "company": "Acme", "url": "https://candidat.francetravail.fr/offres/recherche/detail/ABC123"}],
        "text": "Technicien support Acme",
    }]))

    assert len(results) == 1
    assert results[0].created is True
    assert again[0].created is False
    assert len(browser.opened) == 1
    assert "motsCles=support" in browser.opened[0][0]
    offer = get_offer(conn, results[0].offer_id)
    assert offer["external_source"] == "france-travail"
    assert offer["external_id"] == "ABC123"
    assert offer["browser_url"].endswith("ABC123")
    assert offer["raw_browser_snapshot"]
    assert offer["score"] >= 0


def test_search_offers_falls_back_to_live_dom_when_snapshot_is_summary_only_and_filters_client_side(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    browser = FakeBrowser(
        [{"snapshot": "url: ...\ntitle: 60 offres", "refsCount": 118}],
        console_values=[
            [
                {
                    "title": "Chauffeur de poids lourd (H/F)",
                    "href": "https://candidat.francetravail.fr/offres/recherche/detail/207LRYQ",
                    "text": "Chauffeur de poids lourd TRANSPORTS F.A.B. 74 - ANNEMASSE CDI Temps plein",
                    "description": "Permis C obligatoire",
                    "contract_type": "CDI Temps plein",
                }
            ]
        ],
    )

    results = search_offers(conn, query="poids lourd", location="Bogève", radius=10, contract="CDI", browser=browser)

    assert len(results) == 1
    assert results[0].title == "Chauffeur de poids lourd (H/F)"
    assert "lieux=74038" in browser.opened[0][0]
    assert "typeContrat=CDI" in browser.opened[0][0]
    offer = get_offer(conn, results[0].offer_id)
    assert offer["external_id"] == "207LRYQ"
    assert offer["contract_type"] == "CDI Temps plein"


def test_refresh_offer_updates_active_state_and_records_event(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(
        conn,
        title="Support",
        external_source="france-travail",
        external_id="ABC123",
        browser_url="https://candidat.francetravail.fr/offres/recherche/detail/ABC123",
    )
    browser = FakeBrowser([{"text": "Cette offre n'est plus disponible"}])

    result = refresh_offer(conn, offer_id, browser=browser)

    assert result.offer_id == offer_id
    assert result.is_active is False
    offer = get_offer(conn, offer_id)
    assert offer["is_active"] == 0
    assert offer["last_refreshed_at"]
    assert list_offer_events(conn, offer_id)[0]["event_type"] == "refresh"


def test_apply_check_blocks_inactive_or_existing_application_and_detects_signal(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    active_id = add_offer(
        conn,
        title="Support",
        external_source="france-travail",
        browser_url="https://candidat.francetravail.fr/offres/recherche/detail/ABC123",
    )
    inactive_id = add_offer(conn, title="Inactive", is_active=False)
    browser = FakeBrowser([{"text": "Offre active. Candidater maintenant"}])

    ok = apply_check_offer(conn, active_id, browser=browser)
    add_application(conn, active_id, status="sent")
    already = apply_check_offer(conn, active_id, browser=FakeBrowser([{"text": "Candidater"}]))
    inactive = apply_check_offer(conn, inactive_id, browser=FakeBrowser([{"text": "Candidater"}]))

    assert ok.can_apply is True
    assert any("signal" in reason.lower() for reason in ok.reasons)
    assert already.can_apply is False
    assert any("déjà" in reason.lower() for reason in already.reasons)
    assert inactive.can_apply is False
    assert any("inactive" in reason.lower() for reason in inactive.reasons)


def test_draft_application_creates_artifact_and_draft_row(tmp_path):
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)
    offer_id = add_offer(conn, title="Support", company="Acme")

    result = draft_application(conn, offer_id, drafts_dir=tmp_path / "drafts")

    assert result.application_id > 0
    assert result.draft_path.exists()
    assert "Support" in result.draft_path.read_text()
    row = conn.execute("SELECT * FROM applications WHERE id = ?", (result.application_id,)).fetchone()
    assert row["status"] == "draft"
