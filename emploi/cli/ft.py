from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from emploi.browser.client import ManagedBrowserClient
from emploi.browser.errors import ManagedBrowserError
from emploi.browser.models import DEFAULT_PROFILE, DEFAULT_SITE
from emploi.cli import (
    _ensure_option_enabled,
    _handle_browser_error,
    _print_json_or_text,
    ft_app,
)
from emploi.db import connect, init_db
from emploi.france_travail.extractors import extract_offers
from emploi.france_travail.flows import (
    apply_check_offer,
    build_search_url,
    draft_application,
    open_offer,
    open_partner_offer,
    refresh_offer,
    search_offers,
)

console = Console(soft_wrap=True)


@ft_app.command("smoke")
def ft_smoke(
    query: str = typer.Argument(..., help="Mots-clés France Travail"),
    location: str = typer.Option("", "--location", "-l"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Afficher ce qui serait vérifié sans navigateur ni base"),
    json_output: bool = typer.Option(False, "--json", help="Afficher un résultat JSON parseable"),
    site: str = typer.Option(DEFAULT_SITE, "--site"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Vérifie le flux France Travail sans import en base ni soumission."""
    _ensure_option_enabled("france_travail.enabled", json_output=json_output)
    _ensure_option_enabled("managed_browser.enabled", json_output=json_output)
    search_url = build_search_url(query, location)
    base_payload = {
        "query": query,
        "location": location,
        "site": site,
        "profile": profile,
        "search_url": search_url,
        "database_write": False,
        "submit_application": False,
    }
    if dry_run:
        payload = {**base_payload, "status": "dry-run", "would_run": ["open", "snapshot"]}
        _print_json_or_text(payload, json_output=json_output, text=f"Dry-run France Travail: ouvrir {search_url}, snapshot; aucun import/candidature.")
        return

    try:
        client = ManagedBrowserClient()
        opened = client.lifecycle_open(search_url, site=site, profile=profile)
        snapshot = client.snapshot(label="ft-smoke", site=site, profile=profile)
    except ManagedBrowserError as error:
        _handle_browser_error(error)
    offers = extract_offers(snapshot.payload)
    payload = {
        **base_payload,
        "status": "ok",
        "offer_count": len(offers),
        "checks": {
            "open": {"payload": opened.payload},
            "snapshot": {"payload": snapshot.payload},
        },
    }
    _print_json_or_text(payload, json_output=json_output, text=f"France Travail smoke OK — {len(offers)} offre(s) détectée(s), aucun import/candidature.")


@ft_app.command("search")
def ft_search(
    query: str = typer.Argument(..., help="Mots-clés France Travail"),
    location: str = typer.Option("", "--location", "-l"),
    site: str = typer.Option(DEFAULT_SITE, "--site"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Recherche France Travail via Managed Browser et importe les offres."""
    _ensure_option_enabled("france_travail.enabled")
    _ensure_option_enabled("managed_browser.enabled")
    try:
        with connect() as conn:
            init_db(conn)
            results = search_offers(conn, query=query, location=location, site=site, profile=profile)
    except ManagedBrowserError as error:
        _handle_browser_error(error)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error

    console.print(f"{len(results)} offre(s) France Travail traitée(s)")
    table = Table("ID", "Action", "Score", "Titre", "URL")
    for result in results:
        table.add_row(
            str(result.offer_id),
            "créée" if result.created else "mise à jour",
            str(result.score),
            result.title,
            result.browser_url,
        )
    console.print(table)


@ft_app.command("refresh")
def ft_refresh(
    offer_id: int,
    site: str = typer.Option(DEFAULT_SITE, "--site"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Rafraîchit l'état d'une offre France Travail stockée."""
    _ensure_option_enabled("france_travail.enabled")
    _ensure_option_enabled("managed_browser.enabled")
    try:
        with connect() as conn:
            init_db(conn)
            result = refresh_offer(conn, offer_id, site=site, profile=profile)
    except ManagedBrowserError as error:
        _handle_browser_error(error)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error

    console.print(f"Offre #{result.offer_id} : {'active' if result.is_active else 'inactive'}")


@ft_app.command("apply")
def ft_apply(
    offer_id: int,
    check: bool = typer.Option(False, "--check", help="Vérifier seulement la possibilité de candidater"),
    draft: bool = typer.Option(False, "--draft", help="Créer un brouillon local sans soumission"),
    open_browser: bool = typer.Option(False, "--open", help="Ouvrir l'offre dans le Managed Browser"),
    partner: str | None = typer.Option(None, "--partner", help="Ouvrir le partenaire externe choisi (ex: Meteojob, HelloWork)"),
    drafts_dir: str | None = typer.Option(None, "--drafts-dir", help="Répertoire des brouillons"),
    site: str = typer.Option(DEFAULT_SITE, "--site"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Vérifie, prépare ou ouvre une candidature France Travail; ne soumet jamais automatiquement."""
    _ensure_option_enabled("france_travail.enabled")
    if draft:
        _ensure_option_enabled("drafts.enabled")
    if check or open_browser or partner or not any((check, draft, open_browser, partner)):
        _ensure_option_enabled("managed_browser.enabled")
    if not any((check, draft, open_browser, partner)):
        check = True
    try:
        with connect() as conn:
            init_db(conn)
            if check:
                result = apply_check_offer(conn, offer_id, site=site, profile=profile)
                console.print(
                    f"Offre #{offer_id} : {'candidature possible' if result.can_apply else 'candidature non disponible'}"
                )
                for reason in result.reasons:
                    console.print(f"- {reason}")
            if draft:
                draft_result = draft_application(conn, offer_id, drafts_dir=drafts_dir)
                console.print(f"Brouillon créé : {draft_result.draft_path}")
            if open_browser:
                url = open_offer(conn, offer_id, site=site, profile=profile)
                console.print(f"Offre #{offer_id} ouverte : {url}")
            if partner:
                partner_result = open_partner_offer(conn, offer_id, partner, site=site, profile=profile)
                console.print(f"Partenaire {partner_result.partner_name} ouvert pour l'offre #{offer_id} : {partner_result.url}")
    except ManagedBrowserError as error:
        _handle_browser_error(error)
    except ValueError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(1) from error
