from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from emploi import config as emploi_config
from emploi.cli import kanban_app, kanban_card_app
from emploi.db import connect, init_db
from emploi.nextcloud_deck import create_offer_card

console = Console(soft_wrap=True)


@kanban_app.command("set")
def kanban_set(
    name: str,
    base_url: str = typer.Option(..., "--base-url", help="URL racine Nextcloud, sans chemin app"),
    board_id: int = typer.Option(..., "--board-id", help="ID du board Deck"),
    board_url: str = typer.Option("", "--board-url", help="URL UI du board Deck"),
    username_pass: str = typer.Option("", "--username-pass", help="Entrée pass contenant le login"),
    password_pass: str = typer.Option("", "--password-pass", help="Entrée pass contenant le mot de passe/app password"),
    title: str = typer.Option("", "--title", help="Titre lisible du board"),
    api_base_path: str = typer.Option("/index.php/apps/deck/api/v1.0", "--api-base-path", help="Chemin API Deck"),
    stack_options: Annotated[list[str] | None, typer.Option("--stack", help="Alias de colonne Deck au format alias=ID; répétable")] = None,
    make_default: bool = typer.Option(False, "--default", help="Définir comme endpoint kanban par défaut"),
) -> None:
    """Enregistre un endpoint API Nextcloud Deck pour le suivi kanban emploi."""
    try:
        stacks = emploi_config.parse_kanban_stack_options(stack_options)
        endpoint = emploi_config.set_kanban_endpoint(
            name,
            base_url=base_url,
            board_id=board_id,
            board_url=board_url,
            username_pass=username_pass,
            password_pass=password_pass,
            title=title,
            api_base_path=api_base_path,
            make_default=make_default,
            stacks=stacks,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    marker = " (défaut)" if endpoint.get("default") else ""
    console.print(f"Kanban enregistré : {endpoint['name']}{marker}")
    console.print(f"UI  : {endpoint['board_url']}")
    console.print(f"API : {endpoint['api_stacks_url']}")
    if endpoint.get("stacks"):
        console.print(f"Stacks: {', '.join(f'{alias}={stack_id}' for alias, stack_id in endpoint['stacks'].items())}")
    if endpoint.get("username_pass") or endpoint.get("password_pass"):
        console.print("Auth: pass (secrets non affichés)")


@kanban_app.command("show")
def kanban_show(
    name: str = typer.Argument("", help="Nom de l'endpoint; vide = défaut"),
    json_output: bool = typer.Option(False, "--json", help="Afficher en JSON"),
) -> None:
    """Affiche l'endpoint kanban configuré et les URLs API dérivées."""
    endpoint = emploi_config.get_kanban_endpoint(name) if name else emploi_config.get_default_kanban_endpoint()
    if endpoint is None:
        message = "Aucun endpoint kanban configuré" if not name else f"Endpoint kanban introuvable: {name}"
        if json_output:
            console.print_json(data={"status": "missing", "message": message})
        else:
            console.print(message)
        raise typer.Exit(1)
    if json_output:
        console.print_json(data=endpoint)
        return
    marker = " (défaut)" if endpoint.get("default") else ""
    console.print(f"Kanban : {endpoint['name']}{marker}")
    if endpoint.get("title"):
        console.print(f"Titre  : {endpoint['title']}")
    console.print(f"Board  : {endpoint['board_url']}")
    console.print(f"API board  : {endpoint['api_board_url']}")
    console.print(f"API stacks : {endpoint['api_stacks_url']}")
    if endpoint.get("stacks"):
        console.print(f"Stacks : {', '.join(f'{alias}={stack_id}' for alias, stack_id in endpoint['stacks'].items())}")
    if endpoint.get("username_pass") or endpoint.get("password_pass"):
        console.print("Auth pass : configurée")


@kanban_app.command("list")
def kanban_list(json_output: bool = typer.Option(False, "--json", help="Afficher en JSON")) -> None:
    """Liste les endpoints kanban enregistrés."""
    endpoints = emploi_config.list_kanban_endpoints()
    if json_output:
        console.print_json(data={"endpoints": endpoints})
        return
    if not endpoints:
        console.print("Aucun endpoint kanban configuré")
        return
    table = Table("Nom", "Défaut", "Board", "API stacks")
    for endpoint in endpoints:
        table.add_row(endpoint["name"], endpoint.get("default", ""), str(endpoint["board_id"]), endpoint["api_stacks_url"])
    console.print(table)


@kanban_card_app.command("add-offer")
def kanban_card_add_offer(
    offer_id: int,
    stack: str = typer.Option(..., "--stack", "--stack-id", help="Alias ou ID de la colonne/stack Deck cible"),
    endpoint_name: str = typer.Option("", "--endpoint", help="Endpoint kanban; vide = défaut"),
    nextcloud_folder_url: str = typer.Option("", "--nextcloud-folder-url", help="Lien dossier Nextcloud à ajouter à la description"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Prévisualiser sans créer de carte"),
    force: bool = typer.Option(False, "--force", help="Créer une nouvelle carte même si un événement existe déjà"),
) -> None:
    """Prépare ou crée une carte Deck depuis une offre locale."""
    endpoint = emploi_config.get_kanban_endpoint(endpoint_name) if endpoint_name else emploi_config.get_default_kanban_endpoint()
    if endpoint is None:
        raise typer.BadParameter("Aucun endpoint kanban configuré. Utilise `emploi kanban set ...`.")
    try:
        stack_id = emploi_config.resolve_kanban_stack(endpoint, stack)
        with connect() as conn:
            init_db(conn)
            result = create_offer_card(
                conn,
                offer_id,
                endpoint=endpoint,
                stack_id=stack_id,
                nextcloud_folder_url=nextcloud_folder_url,
                dry_run=dry_run,
                force=force,
            )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    verb = "préparée" if dry_run else "créée"
    console.print(f"Carte Deck {verb} : offre #{result.offer_id} → stack {result.stack_id}")
    console.print(f"Titre : {result.title}")
    if result.card_id is not None:
        console.print(f"Carte ID : {result.card_id}")
    if result.reused_existing:
        console.print("Déjà enregistré : aucune nouvelle carte créée. Utilise --force pour recréer.")
    if dry_run:
        console.print("Dry-run : aucune carte créée.")
