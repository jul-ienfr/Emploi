from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from emploi import config as emploi_config
from emploi.cli import nextcloud_files_app, nextcloud_tasks_app

console = Console(soft_wrap=True)


@nextcloud_files_app.command("set")
def nextcloud_files_set(
    name: str,
    base_url: str = typer.Option(..., "--base-url", help="URL racine Nextcloud"),
    remote_root: str = typer.Option("/Emploi", "--remote-root", help="Dossier racine distant WebDAV"),
    username_pass: str = typer.Option("", "--username-pass", help="Entrée pass contenant le login"),
    password_pass: str = typer.Option("", "--password-pass", help="Entrée pass contenant le mot de passe/app password"),
    webdav_base_path: str = typer.Option("/remote.php/dav/files", "--webdav-base-path", help="Chemin WebDAV Nextcloud"),
    make_default: bool = typer.Option(False, "--default", help="Définir comme endpoint Files par défaut"),
) -> None:
    """Enregistre un endpoint Nextcloud Files/WebDAV pour les documents emploi."""
    try:
        endpoint = emploi_config.set_nextcloud_files_endpoint(
            name,
            base_url=base_url,
            remote_root=remote_root,
            username_pass=username_pass,
            password_pass=password_pass,
            webdav_base_path=webdav_base_path,
            make_default=make_default,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    marker = " (défaut)" if endpoint.get("default") else ""
    console.print(f"Nextcloud Files enregistré : {endpoint['name']}{marker}")
    console.print(f"Racine distante : {endpoint['remote_root']}")
    console.print(f"WebDAV : {endpoint['webdav_root_url']}")
    if endpoint.get("username_pass") or endpoint.get("password_pass"):
        console.print("Auth: pass (secrets non affichés)")


@nextcloud_files_app.command("show")
def nextcloud_files_show(
    name: str = typer.Argument("", help="Nom de l'endpoint; vide = défaut"),
    json_output: bool = typer.Option(False, "--json", help="Afficher en JSON"),
) -> None:
    """Affiche l'endpoint Nextcloud Files/WebDAV configuré."""
    endpoint = emploi_config.get_nextcloud_files_endpoint(name) if name else emploi_config.get_default_nextcloud_files_endpoint()
    if endpoint is None:
        message = "Aucun endpoint Nextcloud Files configuré" if not name else f"Endpoint Nextcloud Files introuvable: {name}"
        if json_output:
            console.print_json(data={"status": "missing", "message": message})
        else:
            console.print(message)
        raise typer.Exit(1)
    if json_output:
        console.print_json(data=endpoint)
        return
    marker = " (défaut)" if endpoint.get("default") else ""
    console.print(f"Nextcloud Files : {endpoint['name']}{marker}")
    console.print(f"Racine distante : {endpoint['remote_root']}")
    console.print(f"WebDAV : {endpoint['webdav_root_url']}")
    if endpoint.get("username_pass") or endpoint.get("password_pass"):
        console.print("Auth pass : configurée")


@nextcloud_files_app.command("list")
def nextcloud_files_list(json_output: bool = typer.Option(False, "--json", help="Afficher en JSON")) -> None:
    """Liste les endpoints Nextcloud Files/WebDAV enregistrés."""
    endpoints = emploi_config.list_nextcloud_files_endpoints()
    if json_output:
        console.print_json(data={"endpoints": endpoints})
        return
    if not endpoints:
        console.print("Aucun endpoint Nextcloud Files configuré")
        return
    table = Table("Nom", "Défaut", "Racine", "WebDAV")
    for endpoint in endpoints:
        table.add_row(endpoint["name"], endpoint.get("default", ""), endpoint["remote_root"], endpoint["webdav_root_url"])
    console.print(table)


@nextcloud_tasks_app.command("set")
def nextcloud_tasks_set(
    name: str,
    base_url: str = typer.Option(..., "--base-url", help="URL racine Nextcloud"),
    calendar: str = typer.Option("tasks", "--calendar", help="Nom du calendrier/liste Tasks CalDAV"),
    username_pass: str = typer.Option("", "--username-pass", help="Entrée pass contenant le login"),
    password_pass: str = typer.Option("", "--password-pass", help="Entrée pass contenant le mot de passe/app password"),
    caldav_base_path: str = typer.Option("/remote.php/dav/calendars", "--caldav-base-path", help="Chemin CalDAV Nextcloud"),
    make_default: bool = typer.Option(False, "--default", help="Définir comme endpoint Tasks par défaut"),
) -> None:
    """Enregistre un endpoint Nextcloud Tasks/CalDAV pour les relances emploi."""
    try:
        endpoint = emploi_config.set_nextcloud_tasks_endpoint(
            name,
            base_url=base_url,
            calendar=calendar,
            username_pass=username_pass,
            password_pass=password_pass,
            caldav_base_path=caldav_base_path,
            make_default=make_default,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    marker = " (défaut)" if endpoint.get("default") else ""
    console.print(f"Nextcloud Tasks enregistré : {endpoint['name']}{marker}")
    console.print(f"Calendrier : {endpoint['calendar']}")
    console.print(f"CalDAV : {endpoint['calendar_home_url']}")
    if endpoint.get("username_pass") or endpoint.get("password_pass"):
        console.print("Auth: pass (secrets non affichés)")


@nextcloud_tasks_app.command("show")
def nextcloud_tasks_show(
    name: str = typer.Argument("", help="Nom de l'endpoint; vide = défaut"),
    json_output: bool = typer.Option(False, "--json", help="Afficher en JSON"),
) -> None:
    """Affiche l'endpoint Nextcloud Tasks/CalDAV configuré."""
    endpoint = emploi_config.get_nextcloud_tasks_endpoint(name) if name else emploi_config.get_default_nextcloud_tasks_endpoint()
    if endpoint is None:
        message = "Aucun endpoint Nextcloud Tasks configuré" if not name else f"Endpoint Nextcloud Tasks introuvable: {name}"
        if json_output:
            console.print_json(data={"status": "missing", "message": message})
        else:
            console.print(message)
        raise typer.Exit(1)
    if json_output:
        console.print_json(data=endpoint)
        return
    marker = " (défaut)" if endpoint.get("default") else ""
    console.print(f"Nextcloud Tasks : {endpoint['name']}{marker}")
    console.print(f"Calendrier : {endpoint['calendar']}")
    console.print(f"CalDAV : {endpoint['calendar_home_url']}")
    if endpoint.get("username_pass") or endpoint.get("password_pass"):
        console.print("Auth pass : configurée")


@nextcloud_tasks_app.command("list")
def nextcloud_tasks_list(json_output: bool = typer.Option(False, "--json", help="Afficher en JSON")) -> None:
    """Liste les endpoints Nextcloud Tasks/CalDAV enregistrés."""
    endpoints = emploi_config.list_nextcloud_tasks_endpoints()
    if json_output:
        console.print_json(data={"endpoints": endpoints})
        return
    if not endpoints:
        console.print("Aucun endpoint Nextcloud Tasks configuré")
        return
    table = Table("Nom", "Défaut", "Calendrier", "CalDAV")
    for endpoint in endpoints:
        table.add_row(endpoint["name"], endpoint.get("default", ""), endpoint["calendar"], endpoint["calendar_home_url"])
    console.print(table)
