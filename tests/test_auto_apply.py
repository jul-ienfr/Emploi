from typer.testing import CliRunner

from emploi.cli import app
from emploi.db import add_offer, add_saved_search, connect, get_saved_search, init_db

runner = CliRunner()


def test_search_profile_auto_apply_config_roundtrip_supports_worst_strategy(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        add_saved_search(conn, name="pl", query="poids lourd", where_text="Bogève", radius=10, contract="CDI")

    configured = runner.invoke(
        app,
        [
            "search-profile",
            "auto-apply",
            "pl",
            "--mode",
            "draft",
            "--limit",
            "1",
            "--period",
            "weekly",
            "--strategy",
            "worst-score",
            "--min-score",
            "65",
        ],
    )
    listed = runner.invoke(app, ["search-profile", "list"])

    assert configured.exit_code == 0
    assert "Auto-apply configuré" in configured.stdout
    assert "worst-score" in configured.stdout
    assert listed.exit_code == 0
    assert "Auto-apply" in listed.stdout
    assert "draft 1/weekly worst-score ≥65" in listed.stdout
    with connect(db_path) as conn:
        saved = get_saved_search(conn, "pl")
        assert saved["auto_apply_mode"] == "draft"
        assert saved["auto_apply_limit"] == 1
        assert saved["auto_apply_period"] == "weekly"
        assert saved["auto_apply_strategy"] == "worst-score"
        assert saved["auto_apply_min_score"] == 65


def test_auto_apply_run_selects_worst_eligible_offer_and_creates_draft_once_per_week(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    drafts_dir = tmp_path / "drafts"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        add_saved_search(conn, name="pl", query="poids lourd", where_text="Bogève", radius=10, contract="CDI")
        conn.execute(
            """
            UPDATE saved_searches
            SET auto_apply_mode = 'draft', auto_apply_limit = 1, auto_apply_period = 'weekly',
                auto_apply_strategy = 'worst-score', auto_apply_min_score = 60
            WHERE name = 'pl'
            """
        )
        best = add_offer(
            conn,
            title="Chauffeur poids lourd premium",
            company="BestCo",
            location="Bogève",
            description="poids lourd CDI permis C support informatique télétravail Python",
            contract_type="CDI",
            external_source="france-travail",
            is_active=True,
        )
        worst = add_offer(
            conn,
            title="Chauffeur poids lourd local",
            company="WorstCo",
            location="Bogève",
            description="poids lourd CDI permis C",
            contract_type="CDI - Temps plein",
            external_source="france-travail",
            is_active=True,
        )
        conn.commit()
        best_score = conn.execute("SELECT score FROM offers WHERE id = ?", (best,)).fetchone()["score"]
        worst_score = conn.execute("SELECT score FROM offers WHERE id = ?", (worst,)).fetchone()["score"]
        assert best_score > worst_score >= 60

    first = runner.invoke(
        app,
        ["auto-apply", "run", "--profile", "pl", "--drafts-dir", str(drafts_dir), "--today", "2026-05-04"],
    )
    second = runner.invoke(
        app,
        ["auto-apply", "run", "--profile", "pl", "--drafts-dir", str(drafts_dir), "--today", "2026-05-05"],
    )

    assert first.exit_code == 0
    assert "sélectionnée" in first.stdout
    assert "Chauffeur poids lourd local" in first.stdout
    assert "Brouillon créé" in first.stdout
    assert second.exit_code == 0
    assert "Quota atteint" in second.stdout
    draft_content = next(drafts_dir.glob("*.md")).read_text(encoding="utf-8")
    assert "Chauffeur poids lourd local" in draft_content
    with connect(db_path) as conn:
        selected = conn.execute("SELECT * FROM applications").fetchall()
        assert len(selected) == 1
        assert selected[0]["status"] == "draft"
        assert selected[0]["offer_id"] == worst


def test_auto_apply_run_scopes_candidate_to_requested_profile(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    drafts_dir = tmp_path / "drafts"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        add_saved_search(conn, name="pl", query='"poids lourd" -SPL', where_text="Bogève 74250", radius=10, contract="CDI")
        add_saved_search(conn, name="dev", query="python remote", where_text="Paris", radius=10, contract="CDI")
        conn.execute(
            """
            UPDATE saved_searches
            SET auto_apply_mode = 'draft', auto_apply_limit = 1, auto_apply_period = 'weekly',
                auto_apply_strategy = 'best-score', auto_apply_min_score = 0
            WHERE name IN ('pl', 'dev')
            """
        )
        target = add_offer(
            conn,
            title="Chauffeur poids lourd local",
            company="TransCo",
            location="Bogève",
            description="poids lourd CDI permis C",
            contract_type="CDI",
            external_source="france-travail",
            is_active=True,
        )
        unrelated = add_offer(
            conn,
            title="Développeur Python remote",
            company="TechCo",
            location="Paris",
            description="python remote CDI",
            contract_type="CDI",
            external_source="france-travail",
            is_active=True,
        )
        conn.execute("UPDATE offers SET score = 60 WHERE id = ?", (target,))
        conn.execute("UPDATE offers SET score = 100 WHERE id = ?", (unrelated,))
        conn.commit()

    result = runner.invoke(
        app,
        ["auto-apply", "run", "--profile", "pl", "--drafts-dir", str(drafts_dir), "--today", "2026-05-04"],
    )

    assert result.exit_code == 0
    assert "Chauffeur poids lourd local" in result.stdout
    with connect(db_path) as conn:
        selected = conn.execute("SELECT * FROM applications").fetchall()
        assert len(selected) == 1
        assert selected[0]["offer_id"] == target



def test_auto_apply_open_mode_creates_draft_but_records_opened_status(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    drafts_dir = tmp_path / "drafts"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        add_saved_search(conn, name="pl", query="poids lourd", where_text="Bogève", radius=10, contract="CDI")
        conn.execute(
            """
            UPDATE saved_searches
            SET auto_apply_mode = 'open', auto_apply_limit = 1, auto_apply_period = 'weekly',
                auto_apply_strategy = 'best-score', auto_apply_min_score = 0
            WHERE name = 'pl'
            """
        )
        offer_id = add_offer(
            conn,
            title="Chauffeur poids lourd",
            company="TransCo",
            location="Bogève",
            description="poids lourd CDI permis C",
            contract_type="CDI",
            external_source="france-travail",
            is_active=True,
        )
        conn.commit()

    result = runner.invoke(
        app,
        ["auto-apply", "run", "--profile", "pl", "--drafts-dir", str(drafts_dir), "--today", "2026-05-04"],
    )

    assert result.exit_code == 0
    assert "open/best-score" in result.stdout
    assert "offre sélectionnée" in result.stdout
    with connect(db_path) as conn:
        applications = conn.execute("SELECT * FROM applications").fetchall()
        runs = conn.execute("SELECT * FROM auto_apply_runs").fetchall()
        assert len(applications) == 1
        assert applications[0]["status"] == "draft"
        assert applications[0]["offer_id"] == offer_id
        assert len(runs) == 1
        assert runs[0]["status"] == "opened"
        assert runs[0]["application_id"] == applications[0]["id"]


def test_auto_apply_run_period_allows_each_invocation_to_use_its_own_quota(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    drafts_dir = tmp_path / "drafts"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        add_saved_search(conn, name="pl", query="poids lourd", where_text="Bogève", radius=10, contract="CDI")
        conn.execute(
            """
            UPDATE saved_searches
            SET auto_apply_mode = 'draft', auto_apply_limit = 1, auto_apply_period = 'run',
                auto_apply_strategy = 'best-score', auto_apply_min_score = 0
            WHERE name = 'pl'
            """
        )
        first_offer = add_offer(
            conn,
            title="Chauffeur poids lourd matin",
            company="TransCo",
            location="Bogève",
            description="poids lourd CDI permis C",
            contract_type="CDI",
            external_source="france-travail",
            is_active=True,
        )
        second_offer = add_offer(
            conn,
            title="Chauffeur poids lourd soir",
            company="TransCo",
            location="Bogève",
            description="poids lourd CDI permis C",
            contract_type="CDI",
            external_source="france-travail",
            is_active=True,
        )
        conn.execute("UPDATE offers SET score = 100 WHERE id = ?", (first_offer,))
        conn.execute("UPDATE offers SET score = 90 WHERE id = ?", (second_offer,))
        conn.commit()

    first = runner.invoke(
        app,
        ["auto-apply", "run", "--profile", "pl", "--drafts-dir", str(drafts_dir), "--today", "2026-05-04"],
    )
    second = runner.invoke(
        app,
        ["auto-apply", "run", "--profile", "pl", "--drafts-dir", str(drafts_dir), "--today", "2026-05-04"],
    )

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "Quota atteint" not in second.stdout
    with connect(db_path) as conn:
        selected = conn.execute("SELECT offer_id FROM applications ORDER BY id").fetchall()
        runs = conn.execute("SELECT period_key FROM auto_apply_runs ORDER BY id").fetchall()
        assert [row["offer_id"] for row in selected] == [first_offer, second_offer]
        assert len({row["period_key"] for row in runs}) == 2


def test_auto_apply_submit_mode_is_guarded_until_live_submission_is_supported(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        add_saved_search(conn, name="pl", query="poids lourd", where_text="Bogève", radius=10, contract="CDI")
        conn.execute(
            """
            UPDATE saved_searches
            SET auto_apply_mode = 'submit', auto_apply_limit = 1, auto_apply_period = 'weekly',
                auto_apply_strategy = 'best-score', auto_apply_min_score = 0
            WHERE name = 'pl'
            """
        )
        add_offer(
            conn,
            title="Chauffeur poids lourd",
            company="TransCo",
            location="Bogève",
            description="poids lourd CDI permis C",
            contract_type="CDI",
            external_source="france-travail",
            is_active=True,
        )
        conn.commit()

    first = runner.invoke(app, ["auto-apply", "run", "--profile", "pl", "--today", "2026-05-04"])
    second = runner.invoke(app, ["auto-apply", "run", "--profile", "pl", "--today", "2026-05-05"])

    assert first.exit_code == 0
    assert "Mode submit configuré mais non exécuté" in first.stdout
    assert second.exit_code == 0
    assert "Mode submit configuré mais non exécuté" in second.stdout
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0
        runs = conn.execute("SELECT status FROM auto_apply_runs ORDER BY id").fetchall()
        assert [run["status"] for run in runs] == ["guarded", "guarded"]
