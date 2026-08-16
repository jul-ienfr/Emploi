"""Daemon monitoring — alert on failures via email or webhook.

Configured via environment variables:
    EMPLOI_ALERT_WEBHOOK_URL    – POST JSON payload on failure (Slack, Telegram, etc.)
    EMPLOI_ALERT_EMAIL_TO       – Send email on failure (requires sendmail)
    EMPLOI_ALERT_EMAIL_FROM     – Sender address (default: emploi@localhost)
    EMPLOI_ALERT_HIGH_SCORE     – Alert on newly created offers with score >= 70
                                 (default: enabled; set to 0/false to disable)
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

from emploi.logging import get_logger

logger = get_logger("monitoring")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def send_alert(title: str, details: str, *, level: str = "error") -> None:
    """Send an alert through configured channels.

    Silently skips if no alert channel is configured.
    """
    payload: dict[str, Any] = {
        "level": level,
        "title": title,
        "details": details,
        "timestamp": _now_iso(),
        "source": "emploi-daemon",
    }

    _send_webhook(payload)
    _send_email(title, details)


def _send_webhook(payload: dict[str, Any]) -> None:
    url = os.environ.get("EMPLOI_ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return
    try:
        from urllib.request import Request, urlopen

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as resp:
            logger.info("Webhook alert sent: %s", resp.status)
    except Exception as exc:
        logger.warning("Failed to send webhook alert: %s", exc)


def _send_email(title: str, details: str) -> None:
    to_addr = os.environ.get("EMPLOI_ALERT_EMAIL_TO", "").strip()
    if not to_addr:
        return
    from_addr = os.environ.get("EMPLOI_ALERT_EMAIL_FROM", "emploi@localhost")
    try:
        body = f"Subject: [Emploi] {title}\n\n{details}\n\nTimestamp: {_now_iso()}"
        proc = subprocess.run(
            ["sendmail", "-t", "-f", from_addr],
            input=body.encode("utf-8"),
            timeout=10,
            capture_output=True,
        )
        if proc.returncode == 0:
            logger.info("Email alert sent to %s", to_addr)
        else:
            logger.warning("sendmail failed: %s", proc.stderr.decode(errors="replace"))
    except FileNotFoundError:
        logger.debug("sendmail not available, skipping email alert")
    except Exception as exc:
        logger.warning("Failed to send email alert: %s", exc)


HIGH_SCORE_ALERT_THRESHOLD = 70


def high_score_alerts_enabled() -> bool:
    raw = os.environ.get("EMPLOI_ALERT_HIGH_SCORE", "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def report_new_high_score_offers(offers: list[dict[str, Any]]) -> None:
    """Alert on newly created offers with a score above the threshold.

    ``offers`` items must have: offer_id, title, score, url (best effort).
    Silently skips when alerts are disabled or no channel is configured.
    """
    if not high_score_alerts_enabled():
        return
    interesting = [offer for offer in offers if int(offer.get("score") or 0) >= HIGH_SCORE_ALERT_THRESHOLD]
    if not interesting:
        return
    lines = [
        f"- #{offer['offer_id']} {offer['title']} (score {offer['score']}) — {offer.get('url') or 'sans lien'}"
        for offer in interesting
    ]
    send_alert(
        title=f"Nouvelle(s) offre(s) à fort potentiel: {len(interesting)}",
        details="\n".join(lines),
        level="info",
    )


def report_cycle_result(
    *,
    total_offers: int,
    created: int,
    updated: int,
    errors: list[str],
    duration_seconds: float,
) -> None:
    """Report daemon cycle results. Sends alert if there were errors."""
    if errors:
        error_summary = "\n".join(f"  - {e}" for e in errors[:10])
        send_alert(
            title=f"Daemon cycle: {len(errors)} erreur(s)",
            details=f"Offres traitées: {total_offers}\nCréées: {created}\nMises à jour: {updated}\nDurée: {duration_seconds:.1f}s\n\nErreurs:\n{error_summary}",
        )
    else:
        logger.info(
            "Cycle OK: %d offres (%d créées, %d mises à jour) en %.1fs",
            total_offers,
            created,
            updated,
            duration_seconds,
        )
