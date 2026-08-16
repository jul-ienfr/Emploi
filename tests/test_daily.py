"""Tests de la commande daily (rituel quotidien en une commande)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from emploi.cli import app

runner = CliRunner()


def _seed_accounts(tmp_path) -> None:
    accounts_dir = tmp_path / "config" / "emploi"
    accounts_dir.mkdir(parents=True)
    (accounts_dir / "accounts.json").write_text(
        json.dumps(
            {"profiles": {"candidature": "emploi-candidature", "officiel": "emploi-officiel"}, "default": "candidature"}
        )
    )


def _isolate_external_state(monkeypatch) -> None:
    """Isolate the doctor from machine state (Managed Browser, stray DBs).

    ``daily`` runs the full doctor; without these, the tests depend on the
    real server and on stray ``emploi.sqlite`` files present on the machine
    (``doctor.RESIDUAL_DB_LOCATIONS``), which made the suite flaky.
    """
    monkeypatch.setattr(
        "emploi.doctor._check_managed_browser",
        lambda **k: {"status": "available", "probe": "skipped", "available": True},
    )
    monkeypatch.setattr("emploi.doctor._check_residual_databases", lambda: {"status": "ok", "residual": []})


def test_daily_no_run_reports_ok_on_fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    _isolate_external_state(monkeypatch)
    _seed_accounts(tmp_path)

    result = runner.invoke(app, ["daily", "--no-run"])

    assert result.exit_code == 0, result.stdout
    assert "[Daily] Diagnostic : ok" in result.stdout
    assert "[Daily]" in result.stdout


def test_daily_no_run_reports_degraded_when_accounts_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    _isolate_external_state(monkeypatch)

    result = runner.invoke(app, ["daily", "--no-run"])

    assert result.exit_code == 0, result.stdout
    assert "[Daily] Diagnostic : degraded" in result.stdout
    assert "corrige" in result.stdout.lower() or "Configurer les comptes" in result.stdout


def test_daily_accepts_today_for_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPLOI_DB", str(tmp_path / "emploi.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    _isolate_external_state(monkeypatch)

    result = runner.invoke(app, ["daily", "--no-run", "--today", "2026-08-16"])

    assert result.exit_code == 0, result.stdout
    assert "2026-08-16" in result.stdout
