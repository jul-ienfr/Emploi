import pytest
from typer.testing import CliRunner

from emploi.cli import app
from emploi.db import (
    add_application,
    add_offer,
    connect,
    get_auto_followup_config,
    get_offer,
    init_db,
    list_applications,
)
from emploi.nextcloud_deck import DeckCardResult
from emploi.nextcloud_files import NextcloudExportResult

_has_nextcloud_pipeline = False
try:
    from tests.test_application_nextcloud_pipeline import configure_endpoints, reload_config  # noqa: F401
    _has_nextcloud_pipeline = True
except ModuleNotFoundError:
    pass


def test_auto_followup_config_can_be_enabled_disabled_and_delayed(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))

    runner = CliRunner()
    show = runner.invoke(app, ["application", "followup-config", "show"])
    assert show.exit_code == 0
    assert "désactivée" in show.stdout
    assert "10 jour(s)" in show.stdout

    enabled = runner.invoke(app, ["application", "followup-config", "enable", "--after", "7d"])
    assert enabled.exit_code == 0, enabled.stdout
    assert "activée" in enabled.stdout
    assert "7 jour(s)" in enabled.stdout

    with connect(db_path) as conn:
        init_db(conn)
        assert get_auto_followup_config(conn) == {"enabled": True, "delay_days": 7}

    disabled = runner.invoke(app, ["application", "followup-config", "disable"])
    assert disabled.exit_code == 0
    assert "désactivée" in disabled.stdout

    with connect(db_path) as conn:
        init_db(conn)
        assert get_auto_followup_config(conn) == {"enabled": False, "delay_days": 7}


def test_auto_followup_schedule_uses_configured_delay(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Transport Test")

    runner = CliRunner()
    runner.invoke(app, ["application", "followup-config", "enable", "--after", "10d"])
    result = runner.invoke(app, ["application", "followup", "schedule", str(offer_id), "--today", "2026-05-06"])

    assert result.exit_code == 0, result.stdout
    assert "offre #1" in result.stdout
    assert "2026-05-16" in result.stdout

    due = runner.invoke(app, ["application", "due", "--today", "2026-05-16"])
    assert due.exit_code == 0
    assert "Chauffeur PL" in due.stdout


def test_auto_followup_schedule_skips_when_disabled_unless_forced(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Transport Test")

    runner = CliRunner()
    skipped = runner.invoke(app, ["application", "followup", "schedule", str(offer_id), "--today", "2026-05-06"])
    assert skipped.exit_code == 0
    assert "désactivée" in skipped.stdout

    forced = runner.invoke(
        app,
        ["application", "followup", "schedule", str(offer_id), "--after", "3d", "--force", "--today", "2026-05-06"],
    )
    assert forced.exit_code == 0, forced.stdout
    assert "2026-05-09" in forced.stdout

@pytest.mark.skipif(not _has_nextcloud_pipeline, reason="test_application_nextcloud_pipeline module not found")
def test_pipeline_can_schedule_followup_when_enabled(monkeypatch, tmp_path):
    from tests.test_application_nextcloud_pipeline import configure_endpoints, reload_config

    config = reload_config(monkeypatch, tmp_path)
    configure_endpoints(config)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Transport Test")

    runner = CliRunner()
    runner.invoke(app, ["application", "followup-config", "enable", "--after", "8d"])
    result = runner.invoke(
        app,
        [
            "application",
            "pipeline",
            str(offer_id),
            "--stack",
            "a-postuler",
            "--dry-run",
            "--schedule-followup",
            "--today",
            "2026-05-06",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Relance : prévue le 2026-05-14" in result.stdout
    with connect(db_path) as conn:
        assert list_applications(conn) == []

@pytest.mark.skipif(not _has_nextcloud_pipeline, reason="test_application_nextcloud_pipeline module not found")
def test_pipeline_live_followup_does_not_create_sent_application_without_mark_sent(monkeypatch, tmp_path):
    from tests.test_application_nextcloud_pipeline import configure_endpoints, reload_config

    config = reload_config(monkeypatch, tmp_path)
    configure_endpoints(config)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Transport Test")
    monkeypatch.setattr(
        "emploi.cli.application.export_application_to_nextcloud",
        lambda *args, **kwargs: NextcloudExportResult(offer_id, "/Emploi/Candidatures/test", ["offre.md"], "https://nextcloud.test/f", False),
    )
    monkeypatch.setattr(
        "emploi.cli.application.create_offer_card",
        lambda *args, **kwargs: DeckCardResult(offer_id, 49, "Chauffeur PL", "", 123, False, False),
    )

    result = CliRunner().invoke(
        app,
        ["application", "pipeline", str(offer_id), "--stack", "a-postuler", "--schedule-followup", "--today", "2026-05-06"],
    )

    assert result.exit_code == 0, result.stdout
    assert "Relance : non planifiée" in result.stdout
    with connect(db_path) as conn:
        assert list_applications(conn) == []


@pytest.mark.skipif(not _has_nextcloud_pipeline, reason="test_application_nextcloud_pipeline module not found")
def test_pipeline_live_followup_uses_existing_sent_application(monkeypatch, tmp_path):
    from tests.test_application_nextcloud_pipeline import configure_endpoints, reload_config

    config = reload_config(monkeypatch, tmp_path)
    configure_endpoints(config)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Transport Test")
        add_application(conn, offer_id, status="sent")
    monkeypatch.setattr(
        "emploi.cli.application.export_application_to_nextcloud",
        lambda *args, **kwargs: NextcloudExportResult(offer_id, "/Emploi/Candidatures/test", ["offre.md"], "https://nextcloud.test/f", False),
    )
    monkeypatch.setattr(
        "emploi.cli.application.create_offer_card",
        lambda *args, **kwargs: DeckCardResult(offer_id, 49, "Chauffeur PL", "", 123, False, False),
    )

    result = CliRunner().invoke(
        app,
        ["application", "pipeline", str(offer_id), "--stack", "a-postuler", "--schedule-followup", "--today", "2026-05-06"],
    )

    assert result.exit_code == 0, result.stdout
    assert "Relance : prévue le 2026-05-16" in result.stdout
    with connect(db_path) as conn:
        applications = list_applications(conn)
    assert len(applications) == 1
    assert applications[0]["status"] == "followup"
    assert applications[0]["next_action_at"] == "2026-05-16"


@pytest.mark.skipif(not _has_nextcloud_pipeline, reason="test_application_nextcloud_pipeline module not found")
def test_pipeline_live_followup_marks_sent_only_with_mark_sent(monkeypatch, tmp_path):
    from tests.test_application_nextcloud_pipeline import configure_endpoints, reload_config

    config = reload_config(monkeypatch, tmp_path)
    configure_endpoints(config)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Transport Test")
    monkeypatch.setattr(
        "emploi.cli.application.export_application_to_nextcloud",
        lambda *args, **kwargs: NextcloudExportResult(offer_id, "/Emploi/Candidatures/test", ["offre.md"], "https://nextcloud.test/f", False),
    )
    monkeypatch.setattr(
        "emploi.cli.application.create_offer_card",
        lambda *args, **kwargs: DeckCardResult(offer_id, 49, "Chauffeur PL", "", 123, False, False),
    )

    result = CliRunner().invoke(
        app,
        [
            "application",
            "pipeline",
            str(offer_id),
            "--stack",
            "a-postuler",
            "--schedule-followup",
            "--mark-sent",
            "--today",
            "2026-05-06",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Relance : prévue le 2026-05-16" in result.stdout
    with connect(db_path) as conn:
        applications = list_applications(conn)
    assert len(applications) == 1
    assert applications[0]["status"] == "followup"
    assert applications[0]["next_action_at"] == "2026-05-16"


@pytest.mark.skipif(not _has_nextcloud_pipeline, reason="test_application_nextcloud_pipeline module not found")
def test_pipeline_mark_sent_records_sent_application_without_followup(monkeypatch, tmp_path):
    from tests.test_application_nextcloud_pipeline import configure_endpoints, reload_config

    config = reload_config(monkeypatch, tmp_path)
    configure_endpoints(config)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Transport Test")
    monkeypatch.setattr(
        "emploi.cli.application.export_application_to_nextcloud",
        lambda *args, **kwargs: NextcloudExportResult(offer_id, "/Emploi/Candidatures/test", ["offre.md"], "https://nextcloud.test/f", False),
    )
    monkeypatch.setattr(
        "emploi.cli.application.create_offer_card",
        lambda *args, **kwargs: DeckCardResult(offer_id, 49, "Chauffeur PL", "", 123, False, False),
    )

    result = CliRunner().invoke(
        app,
        ["application", "pipeline", str(offer_id), "--stack", "a-postuler", "--mark-sent", "--no-schedule-followup"],
    )

    assert result.exit_code == 0, result.stdout
    with connect(db_path) as conn:
        applications = list_applications(conn)
        offer = get_offer(conn, offer_id)
    assert len(applications) == 1
    assert applications[0]["status"] == "sent"
    assert offer["status"] == "sent"



@pytest.mark.skipif(not _has_nextcloud_pipeline, reason="test_application_nextcloud_pipeline module not found")
def test_pipeline_followup_can_be_disabled_per_run(monkeypatch, tmp_path):
    from tests.test_application_nextcloud_pipeline import configure_endpoints, reload_config

    config = reload_config(monkeypatch, tmp_path)
    configure_endpoints(config)
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        offer_id = add_offer(conn, title="Chauffeur PL", company="Transport Test")

    runner = CliRunner()
    runner.invoke(app, ["application", "followup-config", "enable", "--after", "8d"])
    result = runner.invoke(
        app,
        [
            "application",
            "pipeline",
            str(offer_id),
            "--stack",
            "a-postuler",
            "--dry-run",
            "--no-schedule-followup",
            "--today",
            "2026-05-06",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Relance : ignorée" in result.stdout
