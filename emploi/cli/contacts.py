from __future__ import annotations

import re

import typer
from rich.console import Console
from rich.table import Table

from emploi import config as emploi_config
from emploi.cli import contacts_app, journal_app
from emploi.nextcloud_contacts import NextcloudContactsClient
from emploi.nextcloud_files import append_journal_note

console = Console(soft_wrap=True)


@contacts_app.command("set")
def contact_set(
    name: str,
    base_url: str = typer.Option(..., "--base-url", help="URL racine Nextcloud, sans chemin app"),
    addressbook: str = typer.Option("contacts", "--addressbook", help="Nom du carnet d'adresses CardDAV"),
    username_pass: str = typer.Option("", "--username-pass", help="Entrée pass contenant le login"),
    password_pass: str = typer.Option("", "--password-pass", help="Entrée pass contenant le mot de passe/app password"),
    carddav_base_path: str = typer.Option(
        "/remote.php/dav/addressbooks", "--carddav-base-path", help="Chemin API CardDAV"
    ),
    make_default: bool = typer.Option(False, "--default", help="Définir comme endpoint contacts par défaut"),
) -> None:
    """Enregistre un endpoint Nextcloud Contacts (CardDAV) pour les recruteurs."""
    try:
        endpoint = emploi_config.set_nextcloud_contacts_endpoint(
            name,
            base_url=base_url,
            addressbook=addressbook,
            username_pass=username_pass,
            password_pass=password_pass,
            carddav_base_path=carddav_base_path,
            make_default=make_default,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    marker = " (défaut)" if endpoint.get("default") else ""
    console.print(f"Nextcloud Contacts enregistré : {endpoint['name']}{marker}")
    console.print(f"Adresse URL : {endpoint['addressbook_home_url']}")
    if endpoint.get("username_pass") or endpoint.get("password_pass"):
        console.print("Auth: pass (secrets non affichés)")


@contacts_app.command("show")
def contact_show(
    name: str = typer.Argument("", help="Nom de l'endpoint; vide = défaut"),
    json_output: bool = typer.Option(False, "--json", help="Afficher en JSON"),
) -> None:
    """Affiche l'endpoint Nextcloud Contacts configuré."""
    endpoint = (
        emploi_config.get_nextcloud_contacts_endpoint(name)
        if name
        else emploi_config.get_default_nextcloud_contacts_endpoint()
    )
    if endpoint is None:
        message = "Aucun endpoint Contacts configuré" if not name else f"Endpoint Contacts introuvable: {name}"
        if json_output:
            console.print_json(data={"status": "missing", "message": message})
        else:
            console.print(message)
        raise typer.Exit(1)
    if json_output:
        console.print_json(data=endpoint)
        return
    marker = " (défaut)" if endpoint.get("default") else ""
    console.print(f"Nextcloud Contacts : {endpoint['name']}{marker}")
    console.print(f"Adresse : {endpoint['addressbook_home_url']}")
    if endpoint.get("username_pass") or endpoint.get("password_pass"):
        console.print("Auth pass : configurée")


@contacts_app.command("list")
def contact_list(json_output: bool = typer.Option(False, "--json", help="Afficher en JSON")) -> None:
    """Liste les endpoints Contacts enregistrés."""
    endpoints = emploi_config.list_nextcloud_contacts_endpoints()
    if json_output:
        console.print_json(data={"endpoints": endpoints})
        return
    if not endpoints:
        console.print("Aucun endpoint Contacts configuré")
        return
    table = Table("Nom", "Défaut", "Adresse")
    for endpoint in endpoints:
        table.add_row(endpoint["name"], endpoint.get("default", ""), endpoint["addressbook_home_url"])
    console.print(table)


@contacts_app.command("browse")
def contact_browse(
    name: str = typer.Argument("", help="Nom de l'endpoint; vide = défaut"),
    json_output: bool = typer.Option(False, "--json", help="Afficher en JSON"),
) -> None:
    """Liste les contacts du carnet (lecture live via CardDAV)."""
    endpoint = (
        emploi_config.get_nextcloud_contacts_endpoint(name)
        if name
        else emploi_config.get_default_nextcloud_contacts_endpoint()
    )
    if endpoint is None:
        message = "Aucun endpoint Contacts configuré" if not name else f"Endpoint Contacts introuvable: {name}"
        if json_output:
            console.print_json(data={"status": "missing", "message": message})
        else:
            console.print(message)
        raise typer.Exit(1)
    client = NextcloudContactsClient(endpoint)
    contacts = client.list_contacts()
    if json_output:
        console.print_json(data={"endpoint": endpoint["name"], "contacts": [c.__dict__ for c in contacts]})
        return
    if not contacts:
        console.print("Aucun contact trouvé dans ce carnet")
        return
    table = Table("Nom", "Organisation", "Email", "Téléphone")
    for contact in contacts:
        table.add_row(contact.name, contact.org, contact.email, contact.phone)
    console.print(table)


@contacts_app.command("add")
def contact_add(
    name: str = typer.Argument(..., help="Nom du contact (ex: Recruteur Dupont)"),
    org: str = typer.Option("", "--org", "--company", help="Entreprise/agence"),
    email: str = typer.Option("", "--email", help="Email"),
    phone: str = typer.Option("", "--phone", help="Téléphone"),
    note: str = typer.Option("", "--note", help="Note libre (offre, contexte…)"),
    endpoint_name: str = typer.Option("", "--endpoint", help="Endpoint contacts; vide = défaut"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Prévisualiser sans créer de contact"),
) -> None:
    """Ajoute un contact recruteur dans le carnet CardDAV."""
    endpoint = (
        emploi_config.get_nextcloud_contacts_endpoint(endpoint_name)
        if endpoint_name
        else emploi_config.get_default_nextcloud_contacts_endpoint()
    )
    if endpoint is None:
        raise typer.BadParameter("Aucun endpoint Contacts configuré. Utilise `emploi contact set ...`.")
    uid = re.sub(r"[^a-zA-Z0-9_-]", "-", name.lower()).strip("-") or "contact"
    if dry_run:
        console.print(f"Dry-run : créerait le contact « {name} » ({uid}.vcf)")
        return
    client = NextcloudContactsClient(endpoint)
    href = client.add_contact(uid=uid, name=name, org=org, email=email, phone=phone, note=note)
    console.print(f"Contact créé : {name} → {href}")


@journal_app.command("add")
def journal_add(
    content: str = typer.Argument(..., help="Texte de l'entrée de journal"),
    remote_path: str = typer.Option(
        "/Emploi/Journal.md", "--path", help="Chemin distant du journal (relatif au root WebDAV)"
    ),
    endpoint_name: str = typer.Option("", "--files-endpoint", help="Endpoint nextcloud-files; vide = défaut"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Prévisualiser sans écrire"),
) -> None:
    """Ajoute une entrée datée au journal candidature (Nextcloud Files)."""
    endpoint = (
        emploi_config.get_nextcloud_files_endpoint(endpoint_name)
        if endpoint_name
        else emploi_config.get_default_nextcloud_files_endpoint()
    )
    if endpoint is None:
        raise typer.BadParameter("Aucun endpoint Nextcloud Files configuré. Utilise `emploi nextcloud-files set ...`.")
    result = append_journal_note(endpoint, content=content, remote_path=remote_path, dry_run=dry_run)
    verb = "préparée" if dry_run else "ajoutée"
    console.print(f"Entrée de journal {verb} : {result.remote_path} (entrée #{result.entry_count})")
    if dry_run:
        console.print(result.entry.rstrip())
        console.print("Dry-run : aucun écrit réseau.")
