from __future__ import annotations

import typer
from rich.console import Console

from emploi.browser.client import ManagedBrowserClient
from emploi.browser.errors import ManagedBrowserError
from emploi.browser.models import DEFAULT_PROFILE, DEFAULT_SITE
from emploi.cli import (
    _ensure_option_enabled,
    _handle_browser_error,
    _print_browser_result,
    _print_json_or_text,
    browser_app,
)

console = Console(soft_wrap=True)


@browser_app.command("status")
def browser_status(
    site: str = typer.Option(DEFAULT_SITE, "--site"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Affiche l'état du Managed Browser."""
    _ensure_option_enabled("managed_browser.enabled")
    try:
        _print_browser_result(ManagedBrowserClient().status(site=site, profile=profile))
    except ManagedBrowserError as error:
        _handle_browser_error(error)


@browser_app.command("open")
def browser_open(
    url: str,
    site: str = typer.Option(DEFAULT_SITE, "--site"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Ouvre une URL dans le Managed Browser."""
    _ensure_option_enabled("managed_browser.enabled")
    try:
        _print_browser_result(ManagedBrowserClient().open(url, site=site, profile=profile))
    except ManagedBrowserError as error:
        _handle_browser_error(error)


@browser_app.command("snapshot")
def browser_snapshot(
    label: str | None = typer.Option(None, "--label"),
    site: str = typer.Option(DEFAULT_SITE, "--site"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Capture un snapshot depuis le Managed Browser."""
    _ensure_option_enabled("managed_browser.enabled")
    try:
        _print_browser_result(ManagedBrowserClient().snapshot(label=label, site=site, profile=profile))
    except ManagedBrowserError as error:
        _handle_browser_error(error)


@browser_app.command("checkpoint")
def browser_checkpoint(
    name: str,
    site: str = typer.Option(DEFAULT_SITE, "--site"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Enregistre un checkpoint nommé dans le Managed Browser."""
    _ensure_option_enabled("managed_browser.enabled")
    try:
        _print_browser_result(ManagedBrowserClient().checkpoint(name, site=site, profile=profile))
    except ManagedBrowserError as error:
        _handle_browser_error(error)


@browser_app.command("smoke")
def browser_smoke(
    dry_run: bool = typer.Option(False, "--dry-run", help="Afficher ce qui serait vérifié sans appeler le navigateur"),
    json_output: bool = typer.Option(False, "--json", help="Afficher un résultat JSON parseable"),
    site: str = typer.Option(DEFAULT_SITE, "--site"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Vérifie le câblage Managed Browser sans soumettre de candidature."""
    _ensure_option_enabled("managed_browser.enabled", json_output=json_output)
    if dry_run:
        payload = {
            "status": "dry-run",
            "site": site,
            "profile": profile,
            "would_run": ["status", "snapshot"],
            "submit_application": False,
        }
        _print_json_or_text(payload, json_output=json_output, text="Dry-run Managed Browser: status, snapshot; aucune candidature.")
        return

    try:
        client = ManagedBrowserClient()
        status = client.status(site=site, profile=profile)
        snapshot = client.snapshot(label="emploi-smoke", site=site, profile=profile)
    except ManagedBrowserError as error:
        _handle_browser_error(error)
    payload = {
        "status": "ok",
        "site": site,
        "profile": profile,
        "checks": {
            "status": {"payload": status.payload},
            "snapshot": {"payload": snapshot.payload},
        },
        "submit_application": False,
    }
    _print_json_or_text(payload, json_output=json_output, text=f"Managed Browser smoke OK — site={site} profile={profile}; aucune candidature.")
