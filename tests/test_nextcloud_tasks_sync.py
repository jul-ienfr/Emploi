import json

from emploi.db import add_offer, connect, init_db, list_offer_events, schedule_application_followup
from emploi.nextcloud_tasks import create_followup_task, sync_due_followup_tasks


class FakeTasksClient:
    def __init__(self):
        self.created = []

    def create_task(self, *, uid, summary, description, due_date):
        self.created.append({"uid": uid, "summary": summary, "description": description, "due_date": due_date})
        return {"uid": uid, "href": f"/remote.php/dav/calendars/test-user/tasks/{uid}.ics"}


def _seed_followup(conn):
    offer_id = add_offer(
        conn,
        title="Chauffeur Poids Lourd H/F",
        company="Slash Intérim",
        location="Bonneville",
        contract_type="CDI",
        source="france-travail",
        external_id="123ABC",
        url="https://example.test/offres/123ABC",
    )
    app_id = conn.execute(
        "INSERT INTO applications (offer_id, status) VALUES (?, 'sent')",
        (offer_id,),
    ).lastrowid
    conn.commit()
    schedule_application_followup(conn, int(app_id), "2026-05-16")
    return offer_id, int(app_id)


def test_create_followup_task_dry_run_does_not_write_event():
    with connect(":memory:") as conn:
        init_db(conn)
        offer_id, app_id = _seed_followup(conn)
        result = create_followup_task(
            conn,
            application_id=app_id,
            endpoint={"name": "emploi", "calendar": "tasks"},
            dry_run=True,
        )

        assert result.dry_run is True
        assert result.application_id == app_id
        assert result.offer_id == offer_id
        assert result.due_date == "2026-05-16"
        assert result.summary == "Relancer — Chauffeur Poids Lourd H/F — Slash Intérim"
        assert "Offre : https://example.test/offres/123ABC" in result.description
        assert list_offer_events(conn, offer_id) == []


def test_create_followup_task_live_records_event_and_reuses_existing():
    with connect(":memory:") as conn:
        init_db(conn)
        offer_id, app_id = _seed_followup(conn)
        client = FakeTasksClient()

        first = create_followup_task(
            conn,
            application_id=app_id,
            endpoint={"name": "emploi", "calendar": "tasks"},
            client=client,
        )
        second = create_followup_task(
            conn,
            application_id=app_id,
            endpoint={"name": "emploi", "calendar": "tasks"},
            client=client,
        )

        assert first.reused_existing is False
        assert second.reused_existing is True
        assert second.href == first.href
        assert len(client.created) == 1
        events = list_offer_events(conn, offer_id)
        assert len([event for event in events if event["event_type"] == "nextcloud_followup_task"]) == 1
        payload = json.loads(events[0]["payload_json"])
        assert payload["application_id"] == app_id
        assert payload["endpoint"] == "emploi"
        assert payload["calendar"] == "tasks"


def test_sync_due_followup_tasks_only_syncs_due_items():
    with connect(":memory:") as conn:
        init_db(conn)
        _offer_id, app_id = _seed_followup(conn)
        client = FakeTasksClient()

        early = sync_due_followup_tasks(
            conn,
            endpoint={"name": "emploi", "calendar": "tasks"},
            today="2026-05-15",
            client=client,
        )
        due = sync_due_followup_tasks(
            conn,
            endpoint={"name": "emploi", "calendar": "tasks"},
            today="2026-05-16",
            client=client,
        )

        assert early == []
        assert [result.application_id for result in due] == [app_id]
        assert len(client.created) == 1
