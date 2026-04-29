import json

from typer.testing import CliRunner

from emploi.cli import app
from emploi.db import connect, get_offer, init_db, list_offers
from emploi.importers import import_offers_file


runner = CliRunner()


def test_import_offers_json_creates_and_updates_by_external_id(tmp_path):
    source_path = tmp_path / "offers.json"
    source_path.write_text(
        json.dumps(
            [
                {
                    "title": "Support Python",
                    "company": "Acme",
                    "location": "Remote",
                    "url": "https://jobs.example/support-python",
                    "description": "Support client Python en télétravail",
                    "salary": "32k€",
                    "remote": "remote",
                    "contract_type": "CDI",
                    "notes": "import initial",
                    "external_id": "ACME-1",
                }
            ]
        ),
        encoding="utf-8",
    )
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)

    first = import_offers_file(conn, source_path, source="indeed", file_format="json")
    second_path = tmp_path / "offers-update.json"
    second_path.write_text(
        json.dumps(
            [
                {
                    "title": "Support Python N2",
                    "company": "Acme",
                    "location": "Annecy",
                    "url": "https://jobs.example/support-python",
                    "external_id": "ACME-1",
                }
            ]
        ),
        encoding="utf-8",
    )
    second = import_offers_file(conn, second_path, source="indeed", file_format="auto")

    offers = list_offers(conn)
    assert first.created == 1
    assert first.updated == 0
    assert second.created == 0
    assert second.updated == 1
    assert len(offers) == 1
    offer = offers[0]
    assert offer["title"] == "Support Python N2"
    assert offer["external_source"] == "indeed"
    assert offer["external_id"] == "ACME-1"
    assert offer["source"] == "indeed"


def test_import_offers_csv_deduplicates_by_url_without_external_id(tmp_path):
    source_path = tmp_path / "offers.csv"
    source_path.write_text(
        "title,company,location,url,remote,contract_type\n"
        "Admin système,Local SARL,Bogève,https://local.example/admin,hybride,CDD\n",
        encoding="utf-8",
    )
    conn = connect(tmp_path / "emploi.sqlite")
    init_db(conn)

    first = import_offers_file(conn, source_path, source="local-site", file_format="csv")
    source_path.write_text(
        "title,company,location,url,remote,contract_type\n"
        "Admin système Linux,Local SARL,Bonneville,https://local.example/admin,hybride,CDI\n",
        encoding="utf-8",
    )
    second = import_offers_file(conn, source_path, source="local-site", file_format="csv")

    offers = list_offers(conn)
    assert first.created == 1
    assert second.updated == 1
    assert len(offers) == 1
    assert offers[0]["title"] == "Admin système Linux"
    assert offers[0]["external_id"] == ""


def test_cli_import_offers_json_summary_and_json_output(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    source_path = tmp_path / "linkedin.json"
    source_path.write_text(
        json.dumps({"offers": [{"title": "Dev support", "company": "Beta", "url": "https://linkedin.example/jobs/1"}]}),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["import", "offers", str(source_path), "--source", "linkedin"])

    assert result.exit_code == 0
    assert "Import linkedin" in result.stdout
    assert "créée(s): 1" in result.stdout
    with connect(db_path) as conn:
        init_db(conn)
        offer = get_offer(conn, 1)
    assert offer is not None
    assert offer["source"] == "linkedin"
    assert offer["external_source"] == "linkedin"

    json_result = runner.invoke(app, ["import", "offers", str(source_path), "--source", "linkedin", "--json"])
    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert payload["created"] == 0
    assert payload["updated"] == 1
    assert payload["source"] == "linkedin"
