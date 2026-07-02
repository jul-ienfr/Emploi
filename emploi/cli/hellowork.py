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
    hellowork_app,
)
from emploi.db import connect, init_db
from emploi.hellowork import apply_hellowork
from emploi.hellowork_search import search_hellowork

console = Console(soft_wrap=True)


@hellowork_app.command("apply")
def hellowork_apply(
    offer_id: int,
    submit: bool = typer.Option(False, "--submit", help="Envoyer réellement la candidature HelloWork"),
    yes: bool = typer.Option(False, "--yes", help="Confirme explicitement l'envoi réel HelloWork"),
    url: str = typer.Option("", "--url", help="URL HelloWork explicite si elle n'est pas en base"),
    motivation: str = typer.Option("", "--motivation", help="Message de motivation explicite; vide = brouillon local"),
    drafts_dir: str | None = typer.Option(None, "--drafts-dir", help="Répertoire des brouillons"),
    no_kanban: bool = typer.Option(False, "--no-kanban", help="Ne pas créer/mettre à jour la carte Deck après envoi"),
    ack_dissuasion: bool = typer.Option(False, "--ack-dissuasion", help="Confirme l'envoi malgré un avertissement compétences HelloWork"),
    kanban_stack: str = typer.Option("", "--kanban-stack", help="Alias/ID stack Deck candidature envoyée"),
    kanban_endpoint: str = typer.Option("", "--kanban-endpoint", help="Endpoint kanban; vide = défaut"),
    site: str = typer.Option(DEFAULT_SITE, "--site"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Prévisualise ou envoie une candidature HelloWork via Managed Browser."""
    if submit and not yes:
        console.print("[red]Error:[/red] --submit HelloWork exige --yes pour confirmer l'envoi réel")
        raise typer.Exit(1)
    _ensure_option_enabled("managed_browser.enabled")
    try:
        browser = ManagedBrowserClient()
        with connect() as conn:
            init_db(conn)
            result = apply_hellowork(
                conn,
                offer_id,
                browser=browser,
                submit=submit,
                url=url,
                motivation=motivation,
                drafts_dir=drafts_dir,
                site=site,
                profile=profile,
                kanban=not no_kanban,
                kanban_stack=kanban_stack,
                kanban_endpoint=kanban_endpoint,
                ack_dissuasion=ack_dissuasion,
            )
    except ManagedBrowserError as error:
        _handle_browser_error(error)
    except ValueError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(1) from error

    console.print(f"HelloWork offre #{result.offer_id} : {result.status}")
    console.print(result.message)
    console.print(f"URL : {result.url}")
    console.print("CV : détecté" if result.form.cv_present else "CV : manquant")
    if result.form.dissuasion_required:
        skills = ", ".join(result.form.dissuasion_skills) or "non précisées"
        console.print(f"Avertissement compétences HelloWork : {skills}")
    if result.application_id is not None:
        console.print(f"Candidature locale : #{result.application_id}")
    if result.deck_card is not None:
        verb = "préparée" if result.deck_card.dry_run else "créée/enregistrée"
        console.print(f"Carte Deck {verb} : stack {result.deck_card.stack_id}")
        if result.deck_card.card_id is not None:
            console.print(f"Carte ID : {result.deck_card.card_id}")
        if result.deck_card.reused_existing:
            console.print("Carte Deck déjà existante : réutilisée.")
    elif not no_kanban:
        console.print("Kanban : non configuré ou non applicable en dry-run.")
    if not submit:
        console.print("Dry-run : aucune candidature envoyée. Ajoute --submit --yes pour envoyer.")


@hellowork_app.command("search")
def hellowork_search(
    query: str = typer.Argument(..., help="Mots-clés de recherche"),
    location: str = typer.Option("", "--location", "-l", help="Lieu"),
    contract: str = typer.Option("", "--contract", "-c", help="Type de contrat (CDI, CDD, etc.)"),
    site: str = typer.Option("hellowork", "--site"),
    profile: str = typer.Option("emploi-hellowork", "--profile"),
) -> None:
    """Recherche des offres sur HelloWork directement."""
    _ensure_option_enabled("managed_browser.enabled")
    try:
        browser = ManagedBrowserClient()
        with connect() as conn:
            init_db(conn)
            results = search_hellowork(
                conn,
                query=query,
                location=location,
                contract=contract,
                browser=browser,
                site=site,
                profile=profile,
            )
    except ManagedBrowserError as error:
        _handle_browser_error(error)

    console.print(f"{len(results)} offre(s) HelloWork trouvée(s)")
    created = sum(1 for result in results if result.created)
    updated = len(results) - created
    console.print(f"créée(s): {created} — mise(s) à jour: {updated}")
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
