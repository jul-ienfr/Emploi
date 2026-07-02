"""
Boucle de veille automatique pour exécuter périodiquement les profils de recherche.

Usage:
    Embarquée via `emploi search-profile watch --interval 30`
"""

from __future__ import annotations

import signal
import sys
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlite3 import Connection

from emploi.browser.models import DEFAULT_PROFILE, DEFAULT_SITE
from emploi.db import connect, init_db, list_saved_searches
from emploi.france_travail.flows import run_saved_search
from emploi.logging import get_logger
from emploi.monitoring import report_cycle_result, send_alert

logger = get_logger("daemon")

POLL_INTERVAL_S = 5  # vérification arrêt tous les 5s


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _print(msg: str) -> None:
    print(f"[{_now_iso()}] {msg}", flush=True)


def _run_all_profiles(conn: Connection, site: str, profile: str) -> tuple[int, int, int, list[str]]:
    profiles = list_saved_searches(conn, enabled=True)
    if not profiles:
        _print("Aucun profil actif — rien à exécuter")
        logger.info("Aucun profil actif")
        return 0, 0, 0, []

    total = 0
    created = 0
    updated = 0
    errors: list[str] = []
    for saved in profiles:
        try:
            results = run_saved_search(conn, int(saved["id"]), site=site, profile=profile)
        except Exception as exc:
            _print(f"ERREUR {saved['name']}: {exc}")
            logger.error("Erreur profil %s: %s", saved["name"], exc, exc_info=True)
            errors.append(f"{saved['name']}: {exc}")
            continue
        total += len(results)
        created += sum(1 for r in results if r.created)
        updated += sum(1 for r in results if not r.created)
        _print(f"  {saved['name']}: {len(results)} offre(s) traitée(s)")
        logger.info("Profil %s: %d offre(s)", saved["name"], len(results))

    _print(f"Total: {total} offre(s) — créée(s): {created} — mise(s) à jour: {updated}")
    logger.info("Cycle terminé: total=%d created=%d updated=%d errors=%d", total, created, updated, len(errors))
    return total, created, updated, errors


def watch_loop(
    interval_minutes: int = 30,
    *,
    once: bool = False,
    site: str = DEFAULT_SITE,
    profile: str = DEFAULT_PROFILE,
) -> None:
    """Boucle infinie : exécute les profils actifs toutes les N minutes.

    Si once=True, exécute un seul cycle puis s'arrête.
    """
    interval_seconds = interval_minutes * 60
    shutdown = False

    def _on_sigint(sig, frame):
        nonlocal shutdown
        if shutdown:
            _print("Second signal — arrêt immédiat")
            sys.exit(1)
        _print("Signal reçu — arrêt après le cycle en cours... (Ctrl+C encore pour forcer)")
        shutdown = True

    signal.signal(signal.SIGINT, _on_sigint)
    signal.signal(signal.SIGTERM, _on_sigint)

    label = "mode one-shot" if once else f"intervalle {interval_minutes} min"
    _print(f"Watch lancé — {label} — profils actifs exécutés à chaque cycle")
    logger.info("Watch lancé: %s", label)
    if not once:
        _print("Ctrl+C pour arrêter")

    while not shutdown:
        _print("--- Cycle ---")
        logger.debug("Cycle démarré")
        import time as _time

        cycle_start = _time.monotonic()
        try:
            with connect() as conn:
                init_db(conn)
                total, created, updated, errors = _run_all_profiles(conn, site=site, profile=profile)
                duration = _time.monotonic() - cycle_start
                report_cycle_result(
                    total_offers=total,
                    created=created,
                    updated=updated,
                    errors=errors,
                    duration_seconds=duration,
                )
        except Exception as exc:
            _print(f"ERREUR cycle: {exc}")
            logger.error("Erreur cycle: %s", exc, exc_info=True)
            send_alert(title="Daemon cycle crash", details=str(exc))

        if shutdown or once:
            break

        # Attente par intervalles de 5s pour réactivité à Ctrl+C
        remaining = interval_seconds
        while remaining > 0 and not shutdown:
            time.sleep(min(POLL_INTERVAL_S, remaining))
            remaining -= POLL_INTERVAL_S

    _print("Watch arrêté proprement")
    logger.info("Watch arrêté")
