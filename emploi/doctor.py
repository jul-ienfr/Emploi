from __future__ import annotations

from typing import Any

from emploi import __version__, config as _config
from emploi.browser.client import ManagedBrowserClient
from emploi.browser.errors import ManagedBrowserError, ManagedBrowserUnavailableError
from emploi.db import connect, db_path, init_db


def build_doctor_report(*, probe_browser: bool = True) -> dict[str, Any]:
    """Build an operator-facing health report for the Emploi CLI."""
    database = _check_database()
    accounts = _check_accounts()
    managed_browser = _check_managed_browser(probe=probe_browser)

    managed_browser_ok = managed_browser["status"] == "ok" or (
        managed_browser["status"] == "available" and managed_browser.get("probe") == "skipped"
    )
    status = "ok"
    if database["status"] != "ok" or accounts["status"] != "ok" or not managed_browser_ok:
        status = "degraded"

    actions: list[str] = []
    if database["status"] != "ok":
        actions.append("Corriger l'accès à la base SQLite ou définir EMPLOI_DB vers un chemin writable.")
    if managed_browser["status"] == "missing":
        actions.append("Installer/configurer Managed Browser ou définir EMPLOI_MANAGED_BROWSER_COMMAND.")
    elif not managed_browser_ok:
        actions.append("Relancer `emploi browser smoke --json` et vérifier que le profil Managed Browser (défaut: emploi-candidature/france-travail) est disponible et connecté.")
    if accounts.get("status") != "ok":
        actions.append(f"Configurer les comptes France Travail : créer ~/.config/emploi/accounts.json avec les deux profils (candidature, officiel).")

    return {
        "status": status,
        "version": __version__,
        "database": database,
        "accounts": accounts,
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


def _check_accounts() -> dict[str, Any]:
    """Check configured France Travail accounts from local config."""
    accounts = _config.list_accounts()
    if not accounts:
        return {
            "status": "missing",
            "accounts": [],
            "default_profile": "emploi",
            "error": "Aucun compte configuré. Créer ~/.config/emploi/accounts.json",
        }
    default = _config.get_default_profile()
    return {
        "status": "ok",
        "accounts": accounts,
        "default_profile": default,
        "count": len(accounts),
    }


def _check_managed_browser(*, probe: bool) -> dict[str, Any]:
    try:
        client = ManagedBrowserClient()
    except ManagedBrowserError as exc:
        return {
            "status": "error",
            "available": False,
            "probe": "not_run",
            "can_run_smoke": False,
            "command": None,
            "path": None,
            "error": str(exc),
            "remediation": "Vérifier la configuration du Managed Browser.",
        }
    result: dict[str, Any] = {
        "status": "available",
        "available": True,
        "probe": "skipped",
        "can_run_smoke": True,
        "command": client.base_url,
        "path": client.base_url,
        "remediation": "Lancer `emploi browser smoke --json` pour vérifier le profil avant un flux réel.",
    }
    if not probe:
        return result
    try:
        status = client.status()
    except ManagedBrowserUnavailableError as exc:
        return {
            "status": "missing",
            "available": False,
            "probe": "failed",
            "can_run_smoke": False,
            "command": client.base_url,
            "path": client.base_url,
            "error": str(exc),
            "remediation": "Vérifier que le serveur Managed Browser est lancé sur " + client.base_url,
        }
    except ManagedBrowserError as exc:
        return {
            "status": "error",
            "available": True,
            "probe": "failed",
            "can_run_smoke": False,
            "command": client.base_url,
            "path": client.base_url,
            "error": str(exc),
            "remediation": "Relancer `emploi browser smoke --json`, déverrouiller/connecter le profil Managed Browser emploi/france-travail si nécessaire.",
        }
    return {
        "status": "ok",
        "available": True,
        "probe": "ok",
        "can_run_smoke": True,
        "command": client.base_url,
        "path": client.base_url,
        "payload": status.payload,
        "remediation": "",
    }
