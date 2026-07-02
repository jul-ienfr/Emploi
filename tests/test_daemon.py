from __future__ import annotations

from unittest.mock import patch

from emploi.daemon import _now_iso, _run_all_profiles
from emploi.db import add_saved_search, connect, init_db


def test_now_iso_returns_valid_iso_format():
    result = _now_iso()
    assert "T" in result
    assert len(result) >= 10


def test_run_all_profiles_no_profiles(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        # No profiles installed — should not raise
        _run_all_profiles(conn, site="default", profile="default")


def test_run_all_profiles_with_profiles(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        add_saved_search(conn, name="test-profile", query="python", where_text="Paris", enabled=True)

    with patch("emploi.daemon.run_saved_search") as mock_run:
        mock_run.return_value = []
        with connect(db_path) as conn:
            _run_all_profiles(conn, site="default", profile="default")
        mock_run.assert_called_once()


def test_run_all_profiles_handles_error_in_profile(tmp_path, monkeypatch):
    db_path = tmp_path / "emploi.sqlite"
    monkeypatch.setenv("EMPLOI_DB", str(db_path))
    with connect(db_path) as conn:
        init_db(conn)
        add_saved_search(conn, name="failing-profile", query="python", enabled=True)
        add_saved_search(conn, name="good-profile", query="java", enabled=True)

    call_count = 0

    def fake_run(conn, search_id, *, site, profile):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Network error")
        return []

    with patch("emploi.daemon.run_saved_search", side_effect=fake_run):
        with connect(db_path) as conn:
            # Should not raise — error in first profile is caught
            _run_all_profiles(conn, site="default", profile="default")
