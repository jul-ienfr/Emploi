"""Tests des tâches Nextcloud : entretiens (interview) et tâches génériques."""

from __future__ import annotations

import re

from typer.testing import CliRunner

import emploi.config as emploi_config
from emploi.cli import app
from emploi.db import add_offer, connect, init_db, list_offer_events
from emploi.nextcloud_tasks import build_vtodo, create_interview_task, create_manual_task

runner = CliRunner()


class FakeTasksClient:
    def __init__(self, *args, **kwargs) -> None:
        self.created: list[dict[str, str]] = []

    def create_task(self, *, uid: str, summary: str, description: str, due_date: str) -> dict[str, str]:
        self.created.append({"uid": uid, "summary": summary, "description": description, "due_date": due_date})
        return {"uid": uid, "href": f"/remote.php/dav/calendars/test-user/tasks/{uid}.ics"}


def _seed_offer(conn) -> int:
    return add_offer(
        conn,
        title="Chauffeur PL régional",
        company="Transports Dupont",
        location="Bogève",
        contract_type="CDI",
        source="france-travail",
        external_id="XYZ789",
        url="https://example.test/offres/XYZ789",
    )


def _setup_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    emploi_config.set_nextcloud_tasks_endpoint(
        "emploi",
        base_url="https://nextcloud.test",
        username_pass="nextcloud/username",
        password_pass="nextcloud/password",
        make_default=True,
    )


# ---------------------------------------------------------------------------
# build_vtodo: DATE-TIME support for interviews
# ---------------------------------------------------------------------------


def test_build_vtodo_date_only():
    payload = build_vtodo(uid="u1", summary="Relancer", description="desc", due_date="2026-05-16")
    assert "DUE;VALUE=DATE:20260516" in payload
    assert "DUE;VALUE=DATE-TIME" not in payload


def test_build_vtodo_with_time_uses_date_time_due():
    payload = build_vtodo(uid="u2", summary="Entretien", description="desc", due_date="2026-08-20", due_time="14:30")
    assert "DUE;VALUE=DATE-TIME:20260820T143000" in payload
    assert "DUE;VALUE=DATE:20260820" not in payload


# ---------------------------------------------------------------------------
# create_interview_task
# ---------------------------------------------------------------------------


def test_create_interview_task_dry_run_builds_payload_without_network():
    with connect(":memory:") as conn:
        init_db(conn)
        offer_id = _seed_offer(conn)
        client = FakeTasksClient()

        result = create_interview_task(
            conn,
            offer_id,
            due_date="2026-08-20",
            due_time="14:30",
            endpoint={"name": "emploi", "calendar": "tasks"},
            location="Agen",
            notes="Préparer le cas pratique",
            client=client,
            dry_run=True,
        )

        assert result.dry_run is True
        assert result.offer_id == offer_id
        assert result.summary == "Entretien — Chauffeur PL régional — Transports Dupont"
        assert "Lieu d'entretien : Agen" in result.description
        assert "Préparer le cas pratique" in result.description
        assert client.created == []
        assert list_offer_events(conn, offer_id) == []


def test_create_interview_task_live_records_event_and_reuses():
    with connect(":memory:") as conn:
        init_db(conn)
        offer_id = _seed_offer(conn)
        client = FakeTasksClient()

        first = create_interview_task(
            conn,
            offer_id,
            due_date="2026-08-20",
            due_time="14:30",
            endpoint={"name": "emploi", "calendar": "tasks"},
            client=client,
        )

        assert first.reused_existing is False
        assert len(client.created) == 1
        events = list_offer_events(conn, offer_id)
        assert any(e["event_type"] == "nextcloud_interview_task" for e in events)

        second = create_interview_task(
            conn,
            offer_id,
            due_date="2026-08-20",
            due_time="14:30",
            endpoint={"name": "emploi", "calendar": "tasks"},
            client=client,
        )

        assert second.reused_existing is True
        assert len(client.created) == 1  # pas de nouvel appel réseau


def test_create_interview_task_unknown_offer_raises():
    with connect(":memory:") as conn:
        init_db(conn)
        try:
            create_interview_task(
                conn,
                999,
                due_date="2026-08-20",
                due_time="14:30",
                endpoint={"name": "emploi"},
            )
        except ValueError as exc:
            assert "introuvable" in str(exc)
        else:
            raise AssertionError("expected ValueError")


# ---------------------------------------------------------------------------
# create_manual_task (tâche générique)
# ---------------------------------------------------------------------------


def test_create_manual_task_dry_run_and_deterministic_uid():
    client = FakeTasksClient()

    first = create_manual_task(
        summary="Préparer le dossier", due_date="2026-09-01", endpoint={"name": "emploi"}, dry_run=True
    )
    assert first.dry_run is True
    assert first.uid.startswith("emploi-task-")
    assert client.created == []

    second = create_manual_task(
        summary="Préparer le dossier", due_date="2026-09-01", endpoint={"name": "emploi"}, client=client
    )
    assert second.dry_run is False
    assert second.uid == first.uid  # UID déterministe → idempotence PUT
    assert len(client.created) == 1


# ---------------------------------------------------------------------------
# CLI : application interview add / application task add
# ---------------------------------------------------------------------------


def test_cli_interview_add_dry_run(tmp_path, monkeypatch):
    _setup_endpoint(tmp_path, monkeypatch)
    fake = FakeTasksClient()
    monkeypatch.setattr("emploi.nextcloud_tasks.NextcloudTasksClient", lambda *a, **k: fake)
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    with connect(str(tmp_path / "emploi.sqlite")) as conn:
        init_db(conn)
        offer_id = _seed_offer(conn)

    result = runner.invoke(
        app,
        [
            "application",
            "interview",
            "add",
            str(offer_id),
            "--date",
            "2026-08-20 14:30",
            "--location",
            "Agen",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "préparée" in result.stdout
    assert fake.created == []


def test_cli_interview_add_live(tmp_path, monkeypatch):
    _setup_endpoint(tmp_path, monkeypatch)
    fake = FakeTasksClient()
    monkeypatch.setattr("emploi.nextcloud_tasks.NextcloudTasksClient", lambda *a, **k: fake)
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    with connect(str(tmp_path / "emploi.sqlite")) as conn:
        init_db(conn)
        offer_id = _seed_offer(conn)

    result = runner.invoke(app, ["application", "interview", "add", str(offer_id), "--date", "2026-08-20 14:30"])

    assert result.exit_code == 0, result.stdout
    assert len(fake.created) == 1
    assert fake.created[0]["summary"].startswith("Entretien — ")
    assert "14:30" in result.stdout


def test_cli_interview_add_rejects_bad_datetime(tmp_path, monkeypatch):
    _setup_endpoint(tmp_path, monkeypatch)
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    with connect(str(tmp_path / "emploi.sqlite")) as conn:
        init_db(conn)
        offer_id = _seed_offer(conn)

    result = runner.invoke(app, ["application", "interview", "add", str(offer_id), "--date", "pas-une-date"])

    assert result.exit_code != 0
    assert "Format attendu" in result.stderr


def test_cli_task_add_dry_run_and_live(tmp_path, monkeypatch):
    _setup_endpoint(tmp_path, monkeypatch)
    fake = FakeTasksClient()
    monkeypatch.setattr("emploi.nextcloud_tasks.NextcloudTasksClient", lambda *a, **k: fake)
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    with connect(str(tmp_path / "emploi.sqlite")) as conn:
        init_db(conn)

    dry = runner.invoke(app, ["application", "task", "add", "Préparer le dossier", "--due", "2026-09-01", "--dry-run"])
    assert dry.exit_code == 0, dry.stdout
    assert "préparée" in dry.stdout
    assert fake.created == []

    live = runner.invoke(app, ["application", "task", "add", "Préparer le dossier", "--due", "2026-09-01"])
    assert live.exit_code == 0, live.stdout
    assert len(fake.created) == 1
    assert fake.created[0]["uid"].startswith("emploi-task-")


def test_cli_interview_requires_endpoint_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    with connect(str(tmp_path / "emploi.sqlite")) as conn:
        init_db(conn)
        offer_id = _seed_offer(conn)

    result = runner.invoke(app, ["application", "interview", "add", str(offer_id), "--date", "2026-08-20 14:30"])

    assert result.exit_code != 0
    # Rich peut envelopper le texte selon la largeur du terminal → comparer sans sauts
    assert re.search(r"Aucun endpoint Nextcloud Tasks configur", re.sub(r"\s+", " ", result.stderr))
