from __future__ import annotations

import shutil
from typing import Any

from emploi import __version__
from emploi.browser.client import ManagedBrowserClient
from emploi.browser.errors import ManagedBrowserError, ManagedBrowserUnavailableError
from emploi.db import connect, db_path, init_db


def build_doctor_report(*, probe_browser: bool = True) -> dict[str, Any]:
    """Build an operator-facing health report for the Emploi CLI."""
    database = _check_database()
    managed_browser = _check_managed_browser(probe=probe_browser)

    status = "ok"
    if database["status"] != "ok" or managed_browser["status"] != "ok":
        status = "degraded"

    actions: list[str] = []
    if database["status"] != "ok":
        actions.append("Corriger l'accès à la base SQLite ou définir EMPLOI_DB vers un chemin writable.")
    if managed_browser["status"] == "missing":
        actions.append("Installer/configurer Managed Browser ou définir EMPLOI_MANAGED_BROWSER_COMMAND.")
    elif managed_browser["status"] != "ok":
        actions.append("Vérifier que le profil Managed Browser emploi/france-travail est disponible et connecté.")

    return {
        "status": status,
        "version": __version__,
        "database": database,
        "managed_browser": managed_browser,
        "recommended_actions": actions,
    }


def _check_database() -> dict[str, Any]:
    path = db_path()
    try:
        with connect(path) as conn:
            init_db(conn)
            offer_count = int(conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0])
            application_count = int(conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0])
        return {
            "status": "ok",
            "path": str(path),
            "offers": offer_count,
            "applications": application_count,
        }
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        return {"status": "error", "path": str(path), "error": str(exc)}


def _check_managed_browser(*, probe: bool) -> dict[str, Any]:
    client = ManagedBrowserClient()
    executable = shutil.which(client.command)
    if executable is None:
        return {
            "status": "missing",
            "command": client.command,
            "error": f"Command not found: {client.command}",
        }
    result: dict[str, Any] = {"status": "available", "command": client.command, "path": executable}
    if not probe:
        return result
    try:
        status = client.status()
    except ManagedBrowserUnavailableError as exc:
        return {"status": "missing", "command": client.command, "path": executable, "error": str(exc)}
    except ManagedBrowserError as exc:
        return {"status": "error", "command": client.command, "path": executable, "error": str(exc)}
    return {
        "status": "ok",
        "command": client.command,
        "path": executable,
        "payload": status.payload,
    }
