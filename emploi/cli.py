from __future__ import annotations

import typer
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

from rich.console import Console
from rich.table import Table

from emploi.applications import create_application_draft
from emploi.auto_apply import run_auto_apply_for_enabled_profiles, run_auto_apply_for_saved_search
from emploi.brief import build_brief
from emploi.doctor import build_doctor_report

from emploi import __version__, config as emploi_config
from emploi.browser.client import ManagedBrowserClient
from emploi.browser.errors import ManagedBrowserError
from emploi.browser.models import DEFAULT_PROFILE, DEFAULT_SITE, BrowserCommandResult
from emploi.db import (
    add_application,
    add_offer,
    FEATURE_OPTIONS,
    add_saved_search,
    application_summary,
    configure_saved_search_auto_apply,
    connect,
    db_path,
    get_auto_followup_config,
    get_boolean_option,
    get_followup_sync_config,
    get_offer,
    validate_option_key,
    get_option,
    normalize_followup_delay,
    get_saved_search,
    init_db,
    install_default_julien_search_profiles,
    list_applications,
    list_next_actions,
    list_offers,
    list_options,
    list_saved_searches,
    rescore_offer,
    schedule_application_followup,
    set_auto_followup_config,
    set_boolean_option,
    set_followup_sync_config,
    set_saved_search_enabled,
    toggle_boolean_option,
    update_application_status,
    update_offer_status,
)
from emploi.france_travail.extractors import extract_offers
from emploi.france_travail.flows import (
    apply_check_offer,
    build_search_url,
    draft_application,
    open_offer,
    open_partner_offer,
    refresh_offer,
    run_saved_search,
    search_offers,
)
from emploi.hellowork import apply_hellowork
from emploi.importers import import_offers_file
from emploi.nextcloud_deck import create_offer_card
from emploi.nextcloud_files import export_application_to_nextcloud
from emploi.nextcloud_tasks import create_followup_task, sync_due_followup_tasks

app = typer.Typer(help="CLI personnel pour chercher, scorer et suivre les offres d'emploi.")
offer_app = typer.Typer(help="Gestion des offres")
application_app = typer.Typer(help="Gestion des candidatures")
browser_app = typer.Typer(help="Commandes Managed Browser")
ft_app = typer.Typer(help="Flux France Travail via Managed Browser")
hellowork_app = typer.Typer(help="Flux HelloWork via Managed Browser")
search_profile_app = typer.Typer(help="Profils de recherche sauvegardés")
auto_apply_app = typer.Typer(help="Sélection/candidature automatique bornée par profil")
import_app = typer.Typer(help="Imports génériques sans scraping")
option_app = typer.Typer(help="Options opérateur activables/désactivables")
document_profile_app = typer.Typer(help="Profils documents emploi: CV et lettres de motivation")
kanban_app = typer.Typer(help="Endpoint kanban externe pour le suivi recherche emploi")
kanban_card_app = typer.Typer(help="Cartes Deck liées aux offres")
nextcloud_files_app = typer.Typer(help="Endpoint Nextcloud Files/WebDAV pour les documents emploi")
nextcloud_tasks_app = typer.Typer(help="Endpoint Nextcloud Tasks/CalDAV pour les relances emploi")
app.add_typer(offer_app, name="offer")
app.add_typer(application_app, name="application")
app.add_typer(browser_app, name="browser")
app.add_typer(ft_app, name="ft")
app.add_typer(hellowork_app, name="hellowork")
app.add_typer(search_profile_app, name="search-profile")
app.add_typer(auto_apply_app, name="auto-apply")
app.add_typer(import_app, name="import")
app.add_typer(option_app, name="option")
app.add_typer(document_profile_app, name="document-profile")
kanban_app.add_typer(kanban_card_app, name="card")
app.add_typer(kanban_app, name="kanban")
app.add_typer(nextcloud_files_app, name="nextcloud-files")
app.add_typer(nextcloud_tasks_app, name="nextcloud-tasks")
console = Console(soft_wrap=True)


def _print_browser_result(result: BrowserCommandResult) -> None:
    console.print(f"Managed Browser {result.command} — site={result.site} profile={result.profile}")
    console.print_json(data=result.payload)


def _print_json_or_text(payload: dict, *, json_output: bool, text: str) -> None:
    if json_output:
        console.print_json(data=payload)
    else:
        console.print(text)


def _handle_browser_error(error: ManagedBrowserError) -> None:
    console.print(f"[red]{error}[/red]")
    raise typer.Exit(1)


def _option_disabled_payload(key: str) -> dict[str, str]:
    return {"status": "disabled", "option": key, "message": f"Option désactivée: {key}"}


def _option_is_enabled_without_creating_db(key: str) -> bool:
    normalized = validate_option_key(key)
    path = db_path()
    if not path.exists():
        return FEATURE_OPTIONS[normalized]
    with connect(path) as conn:
        init_db(conn)
        return get_boolean_option(conn, normalized)


def _ensure_option_enabled(key: str, *, json_output: bool = False) -> None:
    try:
        enabled = _option_is_enabled_without_creating_db(key)
    except ValueError as error:
        if json_output:
            console.print_json(data={"status": "error", "option": key, "message": str(error)})
        else:
            console.print(str(error))
        raise typer.Exit(1) from error
    if enabled:
        return
    payload = _option_disabled_payload(key)
    if json_output:
        console.print_json(data=payload)
    else:
        console.print(f"[red]{payload['message']}[/red]")
    raise typer.Exit(1)


def _print_option_state(option: dict[str, object]) -> None:
    status = "activée" if option["enabled"] else "désactivée"
    console.print(f"Option {status} : {option['key']} = {option['value']}")


def _format_search_radius(saved) -> str:
    radius = int(saved["radius"] or 0)
    requested = int(saved["requested_radius"] or 0) if "requested_radius" in saved.keys() else radius
    if requested and requested != radius:
        return f"{radius} (demandé {requested})"
    return str(radius)


def _format_auto_apply(saved) -> str:
    mode = str(saved["auto_apply_mode"] or "off") if "auto_apply_mode" in saved.keys() else "off"
    if mode == "off":
        return "off"
    limit = int(saved["auto_apply_limit"] or 0)
    period = str(saved["auto_apply_period"] or "weekly")
    strategy = str(saved["auto_apply_strategy"] or "best-score")
    min_score = int(saved["auto_apply_min_score"] or 0)
    return f"{mode} {limit}/{period} {strategy} ≥{min_score}"


@app.callback(invoke_without_command=True)
def main(version: bool = typer.Option(False, "--version", help="Afficher la version")) -> None:
    if version:
        console.print(__version__)
        raise typer.Exit()


@app.command()
def init() -> None:
    """Initialise la base SQLite locale."""
    path = db_path()
    with connect(path) as conn:
        init_db(conn)
    console.print(f"Base initialisée : {path}")


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


@app.command()
def doctor(
    json_output: bool = typer.Option(False, "--json", help="Afficher un diagnostic JSON parseable"),
    probe_browser: bool = typer.Option(True, "--probe-browser/--no-browser-probe", help="Exécuter le probe Managed Browser"),
) -> None:
    """Diagnostique l'état local du CLI, de SQLite et du Managed Browser."""
    report = build_doctor_report(probe_browser=probe_browser)
    if json_output:
        console.print_json(data=report)
        return

    console.print(f"Diagnostic Emploi — {report['status']}")
    console.print(f"Version       : {report['version']}")
    database = report["database"]
    console.print(f"Base SQLite   : {database['status']} — {database['path']}")
    if database["status"] == "ok":
        console.print(f"Offres        : {database['offers']}")
        console.print(f"Candidatures  : {database['applications']}")
    accounts = report.get("accounts", {})
    if accounts.get("status") == "ok":
        accts = accounts.get("accounts", [])
        default_p = accounts.get("default_profile", "?")
        console.print(f"Comptes FT    : {accounts['count']} — défaut: {default_p}")
        for a in accts:
            mark = " (défaut)" if a.get("default") else ""
            console.print(f"  - {a['key']} → {a['profile']}{mark}")
    elif accounts.get("status") == "missing":
        console.print(f"Comptes FT    : aucun configuré — {accounts.get('error', '')}")
    browser = report["managed_browser"]
    console.print(f"Managed Browser : {browser['status']} — {browser['command']}")
    if browser.get("error"):
        console.print(f"Erreur        : {browser['error']}")
    if report["recommended_actions"]:
        console.print("Actions recommandées :")
        for action in report["recommended_actions"]:
            console.print(f"- {action}")


def _document_profile_status(profile: dict[str, object]) -> str:
    has_cv = bool(profile.get("cv_path")) and bool(profile.get("cv_exists"))
    has_letter = bool(profile.get("cover_letter_path")) and bool(profile.get("cover_letter_exists"))
    if has_cv and has_letter:
        return "ok"
    return "missing_files"


def _validate_document_file(path_value: str, *, allow_missing: bool) -> str:
    if not path_value:
        return ""
    path = Path(path_value).expanduser()
    if not allow_missing and not path.exists():
        raise ValueError(f"Fichier introuvable: {path}")
    return str(path)


@document_profile_app.command("set")
def document_profile_set(
    name: str,
    cv_path: str = typer.Option("", "--cv", help="Chemin du CV PDF pour ce profil"),
    cover_letter_path: str = typer.Option("", "--letter", "--lm", help="Chemin de la lettre de motivation PDF"),
    notes: str = typer.Option("", "--notes", help="Notes opérateur pour ce profil"),
    make_default: bool = typer.Option(False, "--default", help="Définir ce profil comme profil documents par défaut"),
    allow_missing: bool = typer.Option(False, "--allow-missing", help="Enregistrer même si les fichiers n'existent pas encore"),
) -> None:
    """Crée ou met à jour un profil documents emploi: CV + lettre de motivation."""
    try:
        cv = _validate_document_file(cv_path, allow_missing=allow_missing)
        letter = _validate_document_file(cover_letter_path, allow_missing=allow_missing)
        profile = emploi_config.set_document_profile(
            name,
            cv_path=cv,
            cover_letter_path=letter,
            notes=notes,
            make_default=make_default,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    marker = " (défaut)" if profile.get("default") else ""
    console.print(f"Profil documents enregistré : {profile['name']}{marker}")
    if profile.get("cv_path"):
        console.print(f"CV : {profile['cv_path']}")
    if profile.get("cover_letter_path"):
        console.print(f"LM : {profile['cover_letter_path']}")


@document_profile_app.command("default")
def document_profile_default(name: str) -> None:
    """Définit le profil documents par défaut."""
    try:
        profile = emploi_config.set_default_document_profile(name)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    console.print(f"Profil documents par défaut : {profile['name']}")


@document_profile_app.command("list")
def document_profile_list(json_output: bool = typer.Option(False, "--json", help="Afficher JSON parseable")) -> None:
    """Liste les profils documents emploi configurés."""
    profiles = emploi_config.list_document_profiles()
    default = emploi_config.get_default_document_profile()
    payload = {"default": default["name"] if default else "", "profiles": profiles}
    if json_output:
        console.print_json(data=payload)
        return
    table = Table("Nom", "Défaut", "CV", "CV OK", "LM", "LM OK", "Notes", width=180)
    for profile in profiles:
        table.add_row(
            str(profile["name"]),
            "oui" if profile.get("default") else "",
            str(profile.get("cv_path", "")),
            "oui" if profile.get("cv_exists") else "non",
            str(profile.get("cover_letter_path", "")),
            "oui" if profile.get("cover_letter_exists") else "non",
            str(profile.get("notes", "")),
        )
    console.print(table)


@document_profile_app.command("status")
def document_profile_status(
    name: str | None = typer.Argument(None, help="Profil à vérifier; défaut si absent"),
    json_output: bool = typer.Option(False, "--json", help="Afficher JSON parseable"),
) -> None:
    """Vérifie les chemins CV/LM d'un profil documents."""
    profile = emploi_config.get_document_profile(name) if name else emploi_config.get_default_document_profile()
    if profile is None:
        payload = {"status": "missing", "profile": None}
        if json_output:
            console.print_json(data=payload)
        else:
            console.print("Aucun profil documents configuré.")
        raise typer.Exit(1)
    payload = {"status": _document_profile_status(profile), "profile": profile}
    if json_output:
        console.print_json(data=payload)
        return
    console.print(f"Profil documents {profile['name']} — {payload['status']}")
    console.print(f"CV : {profile.get('cv_path', '')} ({'ok' if profile.get('cv_exists') else 'manquant'})")
    console.print(f"LM : {profile.get('cover_letter_path', '')} ({'ok' if profile.get('cover_letter_exists') else 'manquante'})")


@option_app.command("list")
def option_list() -> None:
    """Liste les options opérateur et leur état."""
    with connect() as conn:
        init_db(conn)
        options = list_options(conn)
    table = Table("Clé", "Active", "Valeur", "Source", "Défaut", "MAJ")
    for option in options:
        table.add_row(
            str(option["key"]),
            "oui" if option["enabled"] else "non",
            str(option["value"]),
            str(option["source"]),
            "oui" if option["default"] else "non",
            str(option["updated_at"]),
        )
    console.print(table)


@option_app.command("get")
def option_get(key: str) -> None:
    """Affiche une option opérateur."""
    try:
        with connect() as conn:
            init_db(conn)
            option = get_option(conn, key)
    except ValueError as error:
        console.print(str(error))
        raise typer.Exit(1) from error
    _print_option_state(option)


def _set_option_enabled(key: str, enabled: bool) -> None:
    try:
        with connect() as conn:
            init_db(conn)
            option = set_boolean_option(conn, key, enabled)
    except ValueError as error:
        console.print(str(error))
        raise typer.Exit(1) from error
    _print_option_state(option)


@option_app.command("enable")
def option_enable(key: str) -> None:
    """Active une option opérateur."""
    _set_option_enabled(key, True)


@option_app.command("disable")
def option_disable(key: str) -> None:
    """Désactive une option opérateur."""
    _set_option_enabled(key, False)


@option_app.command("toggle")
def option_toggle(key: str) -> None:
    """Inverse une option opérateur."""
    try:
        with connect() as conn:
            init_db(conn)
            option = toggle_boolean_option(conn, key)
    except ValueError as error:
        console.print(str(error))
        raise typer.Exit(1) from error
    _print_option_state(option)


@offer_app.command("add")
def offer_add(
    title: str = typer.Option(..., "--title", "-t"),
    company: str = typer.Option("", "--company", "-c"),
    location: str = typer.Option("", "--location", "-l"),
    url: str = typer.Option("", "--url"),
    source: str = typer.Option("manual", "--source"),
    description: str = typer.Option("", "--description", "-d"),
    salary: str = typer.Option("", "--salary"),
    remote: str = typer.Option("", "--remote"),
    contract_type: str = typer.Option("", "--contract-type"),
    notes: str = typer.Option("", "--notes"),
) -> None:
    """Ajoute une offre manuellement."""
    with connect() as conn:
        init_db(conn)
        offer_id = add_offer(
            conn,
            title=title,
            company=company,
            location=location,
            url=url,
            source=source,
            description=description,
            salary=salary,
            remote=remote,
            contract_type=contract_type,
            notes=notes,
        )
        offer = get_offer(conn, offer_id)
    console.print(f"Offre ajoutée #{offer_id} — score {offer['score'] if offer else '?'}")


@offer_app.command("list")
def offer_list(
    status: str | None = typer.Option(None, "--status"),
    min_score: int | None = typer.Option(None, "--min-score"),
    all_offers: bool = typer.Option(False, "--all", help="Inclure aussi les offres inactives/archivées"),
) -> None:
    """Liste les offres."""
    with connect() as conn:
        init_db(conn)
        offers = list_offers(conn, status=status, min_score=min_score, include_inactive=all_offers)

    table = Table("ID", "Score", "Status", "Titre", "Entreprise", "Lieu")
    for offer in offers:
        table.add_row(
            str(offer["id"]),
            str(offer["score"]),
            offer["status"],
            offer["title"],
            offer["company"],
            offer["location"],
        )
    console.print(table)


@offer_app.command("show")
def offer_show(offer_id: int) -> None:
    """Affiche le détail d'une offre."""
    with connect() as conn:
        init_db(conn)
        offer = get_offer(conn, offer_id)
    if offer is None:
        raise typer.BadParameter(f"Offre introuvable: {offer_id}")

    console.print(f"#{offer['id']} {offer['title']}")
    console.print(f"Entreprise : {offer['company']}")
    console.print(f"Lieu       : {offer['location']}")
    console.print(f"Source     : {offer['source']}")
    console.print(f"URL        : {offer['url']}")
    console.print(f"Statut     : {offer['status']}")
    console.print(f"Score      : {offer['score']}/100")
    if offer["score_reasons"]:
        console.print("Raisons :")
        for reason in offer["score_reasons"].splitlines():
            console.print(f"- {reason}")
    if offer["description"]:
        console.print("\nDescription :")
        console.print(offer["description"])


@offer_app.command("score")
def offer_score(offer_id: int | None = typer.Argument(None), all_offers: bool = typer.Option(False, "--all")) -> None:
    """Recalcule le score d'une offre ou de toutes les offres."""
    _ensure_option_enabled("scoring.enabled")
    with connect() as conn:
        init_db(conn)
        if all_offers:
            offers = list_offers(conn)
            for offer in offers:
                rescore_offer(conn, int(offer["id"]))
            console.print(f"Scores recalculés : {len(offers)} offre(s)")
            return
        if offer_id is None:
            raise typer.BadParameter("Indique un ID ou --all")
        offer = rescore_offer(conn, offer_id)
    console.print(f"Score #{offer['id']} : {offer['score']}/100")
    if offer["score_reasons"]:
        for reason in offer["score_reasons"].splitlines():
            console.print(f"- {reason}")


@offer_app.command("status")
def offer_status(offer_id: int, status: str) -> None:
    """Change le statut d'une offre."""
    with connect() as conn:
        init_db(conn)
        if get_offer(conn, offer_id) is None:
            raise typer.BadParameter(f"Offre introuvable: {offer_id}")
        update_offer_status(conn, offer_id, status)
    console.print(f"Offre #{offer_id} → {status}")


@offer_app.command("reject")
def offer_reject(offer_id: int, reason: str = typer.Option("", "--reason")) -> None:
    """Marque une offre comme refusée."""
    with connect() as conn:
        init_db(conn)
        if get_offer(conn, offer_id) is None:
            raise typer.BadParameter(f"Offre introuvable: {offer_id}")
        update_offer_status(conn, offer_id, "rejected")
    suffix = f" ({reason})" if reason else ""
    console.print(f"Offre #{offer_id} rejetée{suffix}")


@offer_app.command("archive")
def offer_archive(offer_id: int) -> None:
    """Archive une offre."""
    with connect() as conn:
        init_db(conn)
        if get_offer(conn, offer_id) is None:
            raise typer.BadParameter(f"Offre introuvable: {offer_id}")
        update_offer_status(conn, offer_id, "archived")
    console.print(f"Offre #{offer_id} archivée")


@import_app.command("offers")
def import_offers(
    path: str = typer.Argument(..., help="Fichier local JSON ou CSV à importer"),
    source: str = typer.Option(..., "--source", help="Source logique: indeed, linkedin, local-site, etc."),
    file_format: str = typer.Option("auto", "--format", help="auto, json ou csv"),
    json_output: bool = typer.Option(False, "--json", help="Afficher un résumé JSON parseable"),
) -> None:
    """Importe des offres JSON/CSV locales sans scraper de site web."""
    _ensure_option_enabled("import.enabled", json_output=json_output)
    try:
        with connect() as conn:
            init_db(conn)
            summary = import_offers_file(conn, path, source=source, file_format=file_format)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error

    if json_output:
        console.print_json(data=summary.to_dict())
        return

    console.print(
        f"Import {summary.source} ({summary.file_format}) — créée(s): {summary.created} — "
        f"mise(s) à jour: {summary.updated} — ignorée(s): {summary.skipped}"
    )
    table = Table("ID", "Action", "Titre", "URL")
    for offer in summary.offers:
        table.add_row(str(offer.offer_id), "créée" if offer.created else "mise à jour", offer.title, offer.url)
    console.print(table)


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


@search_profile_app.command("add")
def search_profile_add(
    name: str,
    query: str = typer.Option(..., "--query", "-q"),
    where_text: str = typer.Option("", "--where", "--location", "-w"),
    radius: int = typer.Option(0, "--radius"),
    contract: str = typer.Option("", "--contract"),
    disabled: bool = typer.Option(False, "--disabled"),
) -> None:
    """Ajoute un profil de recherche France Travail."""
    with connect() as conn:
        init_db(conn)
        search_id = add_saved_search(
            conn,
            name=name,
            query=query,
            where_text=where_text,
            radius=radius,
            contract=contract,
            enabled=not disabled,
        )
    console.print(f"Profil de recherche ajouté #{search_id} — {name}")


@search_profile_app.command("install-julien-defaults")
def search_profile_install_julien_defaults() -> None:
    """Installe les profils de recherche par défaut pour Julien, sans doublons."""
    with connect() as conn:
        init_db(conn)
        result = install_default_julien_search_profiles(conn)

    console.print("Profils Julien par défaut")
    console.print(
        f"créé(s): {len(result['created'])} — ignoré(s): {len(result['skipped'])} — actif(s): {len(result['enabled'])}"
    )
    table = Table("Action", "ID", "Nom", "Lieu")
    with connect() as conn:
        rows_by_name = {row["name"]: row for row in list_saved_searches(conn)}
    for item in result["created"]:
        row = rows_by_name[str(item["name"])]
        table.add_row("créé", str(item["id"]), str(item["name"]), row["where_text"])
    for item in result["skipped"]:
        row = rows_by_name[str(item["name"])]
        table.add_row("ignoré", str(item["id"]), str(item["name"]), row["where_text"])
    console.print(table)


def _set_search_profile_enabled(name_or_id: str, enabled: bool) -> None:
    try:
        with connect() as conn:
            init_db(conn)
            saved = set_saved_search_enabled(conn, name_or_id, enabled)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    status = "activé" if saved["enabled"] else "désactivé"
    console.print(f"Profil de recherche {status} #{saved['id']} — {saved['name']}")


@search_profile_app.command("enable")
def search_profile_enable(name_or_id: str) -> None:
    """Active un profil de recherche sauvegardé."""
    _set_search_profile_enabled(name_or_id, True)


@search_profile_app.command("disable")
def search_profile_disable(name_or_id: str) -> None:
    """Désactive un profil de recherche sauvegardé."""
    _set_search_profile_enabled(name_or_id, False)


@search_profile_app.command("toggle")
def search_profile_toggle(name_or_id: str) -> None:
    """Inverse l'état actif/inactif d'un profil de recherche sauvegardé."""
    try:
        with connect() as conn:
            init_db(conn)
            current = get_saved_search(conn, name_or_id)
            if current is None:
                raise ValueError(f"Profil de recherche introuvable: {name_or_id}")
            saved = set_saved_search_enabled(conn, int(current["id"]), not bool(current["enabled"]))
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    status = "activé" if saved["enabled"] else "désactivé"
    console.print(f"Profil de recherche {status} #{saved['id']} — {saved['name']}")


@search_profile_app.command("list")
def search_profile_list(enabled_only: bool = typer.Option(False, "--enabled")) -> None:
    """Liste les profils de recherche sauvegardés."""
    with connect() as conn:
        init_db(conn)
        searches = list_saved_searches(conn, enabled=True if enabled_only else None)
    table = Table(
        "ID",
        "Nom",
        "Query",
        "Lieu",
        "Rayon",
        "Contrat",
        "Actif",
        "Dernier run",
        "Auto-apply",
        "Notes",
        width=180,
    )
    console.print("Colonnes: Actif — Dernier run — Auto-apply — Notes")
    for saved in searches:
        auto_apply = _format_auto_apply(saved)
        console.print(
            f"Profil {saved['name']} | Actif: {'oui' if saved['enabled'] else 'non'} | "
            f"Dernier run: {saved['last_run_at'] or 'jamais'} | Auto-apply: {auto_apply} | Notes: {saved['notes']}"
        )
        table.add_row(
            str(saved["id"]),
            saved["name"],
            saved["query"],
            saved["where_text"],
            _format_search_radius(saved),
            saved["contract"],
            "oui" if saved["enabled"] else "non",
            saved["last_run_at"] or "jamais",
            auto_apply,
            saved["notes"],
        )
    console.print(table)


@search_profile_app.command("auto-apply")
def search_profile_auto_apply(
    name_or_id: str,
    mode: str = typer.Option("draft", "--mode", help="off, draft, open, submit"),
    limit: int = typer.Option(1, "--limit", help="Quota par période"),
    period: str = typer.Option("weekly", "--period", help="run, daily, weekly, monthly"),
    strategy: str = typer.Option("best-score", "--strategy", help="best-score, worst-score, newest, oldest"),
    min_score: int = typer.Option(0, "--min-score", help="Score minimal éligible"),
) -> None:
    """Configure l'auto-apply borné pour un profil de recherche."""
    try:
        with connect() as conn:
            init_db(conn)
            saved = configure_saved_search_auto_apply(
                conn,
                name_or_id,
                mode=mode,
                limit=limit,
                period=period,
                strategy=strategy,
                min_score=min_score,
            )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    console.print(f"Auto-apply configuré #{saved['id']} — {saved['name']} : {_format_auto_apply(saved)}")


@search_profile_app.command("run")
def search_profile_run(
    name_or_id: str | None = typer.Argument(None),
    all_profiles: bool = typer.Option(False, "--all", help="Exécuter tous les profils actifs"),
    site: str = typer.Option(DEFAULT_SITE, "--site"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Exécute un profil de recherche via France Travail."""
    _ensure_option_enabled("france_travail.enabled")
    _ensure_option_enabled("managed_browser.enabled")
    try:
        with connect() as conn:
            init_db(conn)
            if all_profiles:
                profiles = list_saved_searches(conn, enabled=True)
                total = 0
                created = 0
                updated = 0
                rows = []
                for saved in profiles:
                    results = run_saved_search(conn, int(saved["id"]), site=site, profile=profile)
                    total += len(results)
                    created += sum(1 for result in results if result.created)
                    updated += sum(1 for result in results if not result.created)
                    refreshed = conn.execute("SELECT * FROM saved_searches WHERE id = ?", (saved["id"],)).fetchone()
                    rows.append((saved, len(results), refreshed["last_run_at"] if refreshed else ""))
                console.print(
                    f"{total} offre(s) France Travail traitée(s) via {len(profiles)} profil(s) actif(s) — "
                    f"créée(s): {created} — mise(s) à jour: {updated}"
                )
                table = Table("Profil", "Actif", "Offres", "Dernier run")
                for saved, count, last_run in rows:
                    table.add_row(saved["name"], "oui", str(count), last_run or "jamais")
                console.print(table)
                return
            if name_or_id is None:
                raise typer.BadParameter("Indique un nom/ID ou --all")
            results = run_saved_search(conn, name_or_id, site=site, profile=profile)
    except ManagedBrowserError as error:
        _handle_browser_error(error)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    console.print(f"{len(results)} offre(s) France Travail traitée(s)")
    created = sum(1 for result in results if result.created)
    updated = len(results) - created
    console.print(f"créée(s): {created} — mise(s) à jour: {updated}")
    table = Table("ID", "Action", "Score", "Titre", "URL")
    for result in results:
        table.add_row(str(result.offer_id), "créée" if result.created else "mise à jour", str(result.score), result.title, result.browser_url)
    console.print(table)


@auto_apply_app.command("run")
def auto_apply_run(
    profile_name: str | None = typer.Option(None, "--profile", help="Nom ou ID du profil à exécuter"),
    all_profiles: bool = typer.Option(False, "--all-enabled", help="Exécuter tous les profils actifs avec auto-apply actif"),
    drafts_dir: str | None = typer.Option(None, "--drafts-dir", help="Répertoire des brouillons"),
    today: str | None = typer.Option(None, "--today", help="Date ISO YYYY-MM-DD pour tests/rejeu"),
) -> None:
    """Exécute l'auto-apply borné: sélectionne une offre et crée un brouillon, sans soumission live."""
    _ensure_option_enabled("drafts.enabled")
    if not profile_name and not all_profiles:
        raise typer.BadParameter("Indique --profile ou --all-enabled")
    try:
        with connect() as conn:
            init_db(conn)
            if all_profiles:
                results = run_auto_apply_for_enabled_profiles(conn, drafts_dir=drafts_dir, today=today)
            else:
                assert profile_name is not None
                results = [run_auto_apply_for_saved_search(conn, profile_name, drafts_dir=drafts_dir, today=today)]
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error

    if not results:
        console.print("Aucun profil auto-apply actif.")
        return
    for result in results:
        prefix = f"{result.profile_name} — {result.mode}/{result.strategy}"
        if result.status in {"drafted", "opened"}:
            console.print(f"{prefix} : offre sélectionnée #{result.offer_id} — {result.title}")
            if result.draft_path:
                console.print(f"Brouillon créé : {result.draft_path}")
        elif result.status == "guarded":
            console.print(f"{prefix} : {result.message}")
        else:
            console.print(f"{prefix} : {result.message}")


@app.command()
def apply(offer_id: int, notes: str = typer.Option("", "--notes")) -> None:
    """Crée une candidature pour une offre."""
    with connect() as conn:
        init_db(conn)
        application_id = add_application(conn, offer_id, notes=notes)
    console.print(f"Candidature créée #{application_id} pour l'offre #{offer_id}")


@application_app.command("draft")
def application_draft(
    offer_id: int,
    drafts_dir: str | None = typer.Option(None, "--drafts-dir", help="Répertoire des brouillons"),
) -> None:
    """Crée un brouillon local court en français, sans soumission."""
    _ensure_option_enabled("drafts.enabled")
    try:
        with connect() as conn:
            init_db(conn)
            result = create_application_draft(conn, offer_id, drafts_dir=drafts_dir)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    console.print(f"Brouillon créé : {result.draft_path}")
    console.print("Aucune soumission automatique : relis puis envoie manuellement.")


def _parse_today(today: str | None = None) -> date:
    if not today:
        return date.today()
    try:
        return date.fromisoformat(today)
    except ValueError as error:
        raise typer.BadParameter("La date --today doit être au format YYYY-MM-DD") from error


def _followup_date_from_delay(*, delay_days: int, today: str | None = None) -> str:
    return (_parse_today(today) + timedelta(days=delay_days)).isoformat()


def _resolve_nextcloud_document_profile(include_documents: bool, document_profile_name: str):
    if not include_documents:
        return None
    document_profile = (
        emploi_config.get_document_profile(document_profile_name)
        if document_profile_name
        else emploi_config.get_default_document_profile()
    )
    if document_profile is None:
        raise typer.BadParameter("Aucun profil documents configuré. Utilise `emploi document-profile set ...`.")
    missing = []
    if document_profile.get("cv_path") and not document_profile.get("cv_exists"):
        missing.append("CV")
    if document_profile.get("cover_letter_path") and not document_profile.get("cover_letter_exists"):
        missing.append("LM")
    if missing:
        raise typer.BadParameter(f"Fichiers documents introuvables: {', '.join(missing)}")
    return document_profile


@application_app.command("export")
def application_export(
    offer_id: int,
    to_nextcloud: bool = typer.Option(False, "--to-nextcloud", help="Exporter le dossier candidature vers Nextcloud Files/WebDAV"),
    endpoint_name: str = typer.Option("", "--endpoint", help="Endpoint nextcloud-files; vide = défaut"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Prévisualiser sans upload ni événement"),
    drafts_dir: str | None = typer.Option(None, "--drafts-dir", help="Répertoire local des brouillons"),
    include_documents: bool = typer.Option(False, "--include-documents", help="Ajouter CV/LM du profil documents"),
    document_profile_name: str = typer.Option("", "--document-profile", help="Profil documents; vide = défaut"),
) -> None:
    """Exporte les éléments d'une candidature vers un backend documentaire."""
    if not to_nextcloud:
        raise typer.BadParameter("Backend requis: utilise --to-nextcloud")
    _ensure_option_enabled("drafts.enabled")
    endpoint = (
        emploi_config.get_nextcloud_files_endpoint(endpoint_name)
        if endpoint_name
        else emploi_config.get_default_nextcloud_files_endpoint()
    )
    if endpoint is None:
        raise typer.BadParameter("Aucun endpoint Nextcloud Files configuré. Utilise `emploi nextcloud-files set ...`.")
    document_profile = _resolve_nextcloud_document_profile(include_documents, document_profile_name)
    try:
        with connect() as conn:
            init_db(conn)
            result = export_application_to_nextcloud(
                conn,
                offer_id,
                endpoint=endpoint,
                drafts_dir=drafts_dir,
                dry_run=dry_run,
                document_profile=document_profile,
                include_documents=include_documents,
            )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    verb = "préparé" if dry_run else "effectué"
    console.print(f"Export Nextcloud {verb} : offre #{result.offer_id}")
    console.print(f"Dossier : {result.remote_dir}")
    if result.web_url:
        console.print(f"Lien : {result.web_url}")
    console.print("Fichiers :")
    for filename in result.uploaded_files:
        console.print(f"- {filename}")
    if dry_run:
        console.print("Dry-run : aucun upload ni événement enregistré.")


@application_app.command("pipeline")
def application_pipeline(
    offer_id: int,
    stack: str = typer.Option(..., "--stack", "--stack-id", help="Alias ou ID de la colonne/stack Deck cible"),
    files_endpoint_name: str = typer.Option("", "--files-endpoint", help="Endpoint nextcloud-files; vide = défaut"),
    kanban_endpoint_name: str = typer.Option("", "--kanban-endpoint", help="Endpoint kanban; vide = défaut"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Prévisualiser sans upload ni création de carte"),
    drafts_dir: str | None = typer.Option(None, "--drafts-dir", help="Répertoire local des brouillons"),
    include_documents: bool = typer.Option(False, "--include-documents", help="Ajouter CV/LM du profil documents"),
    document_profile_name: str = typer.Option("", "--document-profile", help="Profil documents; vide = défaut"),
    force_card: bool = typer.Option(False, "--force-card", help="Créer une nouvelle carte même si un événement existe déjà"),
    mark_sent: bool = typer.Option(False, "--mark-sent", help="Enregistrer une candidature envoyée locale avant relance"),
    schedule_followup: bool | None = typer.Option(None, "--schedule-followup/--no-schedule-followup", help="Planifier une relance selon la config ou désactiver pour ce run"),
    followup_after: str = typer.Option("", "--followup-after", help="Délai de relance pour ce run, ex: 7d; vide = config"),
    sync_followup_task: bool | None = typer.Option(None, "--sync-followup-task/--no-sync-followup-task", help="Créer la tâche Nextcloud de relance selon config ou choix du run"),
    tasks_endpoint_name: str = typer.Option("", "--tasks-endpoint", help="Endpoint nextcloud-tasks; vide = défaut"),
    force_followup_task: bool = typer.Option(False, "--force-followup-task", help="Recréer la tâche de relance même si un événement existe"),
    today: str | None = typer.Option(None, "--today", help="Date ISO YYYY-MM-DD pour tests/rejeu"),
) -> None:
    """Exporte le dossier candidature puis prépare/crée la carte Deck liée."""
    _ensure_option_enabled("drafts.enabled")
    files_endpoint = (
        emploi_config.get_nextcloud_files_endpoint(files_endpoint_name)
        if files_endpoint_name
        else emploi_config.get_default_nextcloud_files_endpoint()
    )
    if files_endpoint is None:
        raise typer.BadParameter("Aucun endpoint Nextcloud Files configuré. Utilise `emploi nextcloud-files set ...`.")
    kanban_endpoint = (
        emploi_config.get_kanban_endpoint(kanban_endpoint_name)
        if kanban_endpoint_name
        else emploi_config.get_default_kanban_endpoint()
    )
    if kanban_endpoint is None:
        raise typer.BadParameter("Aucun endpoint kanban configuré. Utilise `emploi kanban set ...`.")
    document_profile = _resolve_nextcloud_document_profile(include_documents, document_profile_name)
    try:
        stack_id = emploi_config.resolve_kanban_stack(kanban_endpoint, stack)
        with connect() as conn:
            init_db(conn)
            auto_followup = get_auto_followup_config(conn)
            should_schedule_followup = bool(auto_followup["enabled"]) if schedule_followup is None else schedule_followup
            followup_date = ""
            followup_task_result = None
            followup_requires_sent = False
            application_id = None
            should_sync_followup_task = False
            tasks_endpoint = None
            existing_sent = None
            if should_schedule_followup:
                delay_days = normalize_followup_delay(followup_after) if followup_after else int(auto_followup["delay_days"])
                followup_date = _followup_date_from_delay(delay_days=delay_days, today=today)
                if not dry_run:
                    existing_sent = conn.execute(
                        "SELECT id FROM applications WHERE offer_id = ? AND status IN ('sent', 'followup') ORDER BY id DESC LIMIT 1",
                        (offer_id,),
                    ).fetchone()
                sync_config = get_followup_sync_config(conn)
                should_sync_followup_task = bool(sync_config["enabled"]) if sync_followup_task is None else sync_followup_task
                if should_sync_followup_task and (dry_run or existing_sent is not None or mark_sent):
                    tasks_endpoint = (
                        emploi_config.get_nextcloud_tasks_endpoint(tasks_endpoint_name)
                        if tasks_endpoint_name
                        else emploi_config.get_default_nextcloud_tasks_endpoint()
                    )
                    if tasks_endpoint is None:
                        raise ValueError("Aucun endpoint Nextcloud Tasks configuré. Utilise `emploi nextcloud-tasks set ...`.")
            export_result = export_application_to_nextcloud(
                conn,
                offer_id,
                endpoint=files_endpoint,
                drafts_dir=drafts_dir,
                dry_run=dry_run,
                document_profile=document_profile,
                include_documents=include_documents,
            )
            card_result = create_offer_card(
                conn,
                offer_id,
                endpoint=kanban_endpoint,
                stack_id=stack_id,
                nextcloud_folder_url=export_result.web_url,
                dry_run=dry_run,
                force=force_card,
            )
            if not dry_run and mark_sent:
                if existing_sent is None:
                    existing_sent = conn.execute(
                        "SELECT id FROM applications WHERE offer_id = ? AND status IN ('sent', 'followup') ORDER BY id DESC LIMIT 1",
                        (offer_id,),
                    ).fetchone()
                application_id = int(existing_sent["id"]) if existing_sent is not None else add_application(conn, offer_id, status="sent")
                update_offer_status(conn, offer_id, "sent")
            if should_schedule_followup:
                if not dry_run:
                    if existing_sent is not None and application_id is None:
                        application_id = int(existing_sent["id"])
                    if application_id is not None:
                        schedule_application_followup(conn, application_id, followup_date)
                    else:
                        followup_date = ""
                        followup_requires_sent = True
                if should_sync_followup_task and (dry_run or application_id is not None):
                    if dry_run:
                        followup_task_result = "dry-run"
                    else:
                        followup_task_result = create_followup_task(
                            conn,
                            application_id=application_id,
                            endpoint=tasks_endpoint,
                            dry_run=False,
                            force=force_followup_task,
                        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    verb = "préparé" if dry_run else "effectué"
    console.print(f"Pipeline candidature {verb} : offre #{offer_id}")
    console.print(f"Export Nextcloud : {export_result.remote_dir}")
    console.print(f"Lien : {export_result.web_url}" if export_result.web_url else "Lien : non configuré")
    console.print("Fichiers :")
    for filename in export_result.uploaded_files:
        console.print(f"- {filename}")
    console.print(f"Deck endpoint : {kanban_endpoint.get('name', '')}")
    console.print(f"Carte Deck : stack {card_result.stack_id} — {card_result.title}")
    if card_result.card_id is not None:
        console.print(f"Carte ID : {card_result.card_id}")
    if card_result.reused_existing:
        console.print("Carte déjà enregistrée : aucune nouvelle carte créée. Utilise --force-card pour recréer.")
    if followup_date:
        console.print(f"Relance : prévue le {followup_date}")
        if followup_task_result == "dry-run":
            console.print("Tâche Nextcloud : préparée (dry-run)")
        elif followup_task_result is not None:
            console.print(f"Tâche Nextcloud : {followup_task_result.summary}")
            if followup_task_result.reused_existing:
                console.print("Tâche déjà enregistrée : aucune nouvelle tâche créée.")
            elif followup_task_result.href:
                console.print(f"Tâche href : {followup_task_result.href}")
    elif followup_requires_sent:
        console.print("Relance : non planifiée (aucune candidature envoyée locale; utilise --mark-sent)")
    elif schedule_followup is False:
        console.print("Relance : ignorée pour ce run")
    else:
        console.print("Relance : non planifiée (auto désactivé)")
    if dry_run:
        console.print("Dry-run : aucun upload, aucun événement, aucune carte créée.")


def _application_status_update(application_id: int, status: str) -> None:
    try:
        with connect() as conn:
            init_db(conn)
            application = update_application_status(conn, application_id, status)
    except ValueError as error:
        console.print(str(error))
        raise typer.Exit(1) from error
    console.print(f"Candidature #{application_id} → {application['status']}")


@application_app.command("status")
def application_status(application_id: int, status: str) -> None:
    """Change le statut d'une candidature dans le pipeline."""
    _application_status_update(application_id, status)


@application_app.command("update")
def application_update(application_id: int, status: str) -> None:
    """Alias sûr pour changer le statut d'une candidature."""
    _application_status_update(application_id, status)


@application_app.command("followup-sync-config")
def application_followup_sync_config(action: str = typer.Argument("show", help="show|enable|disable")) -> None:
    """Configure la synchronisation des relances vers Nextcloud Tasks."""
    normalized = action.strip().lower()
    if normalized not in {"show", "enable", "disable"}:
        raise typer.BadParameter("Action attendue: show, enable ou disable")
    with connect() as conn:
        init_db(conn)
        if normalized == "show":
            config = get_followup_sync_config(conn)
        else:
            config = set_followup_sync_config(conn, enabled=normalized == "enable")
    state = "activée" if config["enabled"] else "désactivée"
    console.print(f"Synchronisation relances Nextcloud Tasks {state}")


@application_app.command("followup-sync")
def application_followup_sync(
    application_id: int = typer.Argument(0, help="ID candidature; 0 = relances dues"),
    endpoint_name: str = typer.Option("", "--tasks-endpoint", help="Endpoint nextcloud-tasks; vide = défaut"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Prévisualiser sans créer de VTODO"),
    force: bool = typer.Option(False, "--force", help="Recréer même si un événement existe"),
    today: str | None = typer.Option(None, "--today", help="Date ISO YYYY-MM-DD pour tests/rejeu"),
) -> None:
    """Synchronise une relance ou les relances dues vers Nextcloud Tasks."""
    endpoint = emploi_config.get_nextcloud_tasks_endpoint(endpoint_name) if endpoint_name else emploi_config.get_default_nextcloud_tasks_endpoint()
    if endpoint is None:
        raise typer.BadParameter("Aucun endpoint Nextcloud Tasks configuré. Utilise `emploi nextcloud-tasks set ...`.")
    try:
        with connect() as conn:
            init_db(conn)
            if application_id:
                results = [create_followup_task(conn, application_id=application_id, endpoint=endpoint, dry_run=dry_run, force=force)]
            else:
                results = sync_due_followup_tasks(conn, endpoint=endpoint, today=_parse_today(today).isoformat(), dry_run=dry_run, force=force)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    if not results:
        console.print("Aucune relance à synchroniser.")
        return
    for result in results:
        verb = "préparée" if dry_run else ("déjà enregistrée" if result.reused_existing else "créée")
        console.print(f"Tâche Nextcloud {verb} : candidature #{result.application_id} — {result.summary} — échéance {result.due_date}")
        if result.href:
            console.print(f"Href : {result.href}")


@application_app.command("followup-config")
def application_followup_config(
    action: str = typer.Argument("show", help="show|enable|disable"),
    after: str = typer.Option("", "--after", help="Délai par défaut, ex: 7d ou 10"),
) -> None:
    """Configure la planification automatique des relances."""
    normalized = action.strip().lower()
    if normalized not in {"show", "enable", "disable"}:
        raise typer.BadParameter("Action attendue: show, enable ou disable")
    try:
        with connect() as conn:
            init_db(conn)
            if normalized == "show":
                config = get_auto_followup_config(conn)
            elif normalized == "enable":
                config = set_auto_followup_config(conn, enabled=True, delay_days=after or None)
            else:
                config = set_auto_followup_config(conn, enabled=False, delay_days=after or None)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    state = "activée" if config["enabled"] else "désactivée"
    console.print(f"Relance auto {state} — délai: {config['delay_days']} jour(s)")


@application_app.command("followup")
def application_followup(
    target: str,
    value: str | None = typer.Argument(None, help="Date YYYY-MM-DD ou offer_id si `schedule`"),
    after: str = typer.Option("", "--after", help="Délai ex: 7d; utilisé avec `schedule`"),
    force: bool = typer.Option(False, "--force", help="Planifier même si la relance auto est désactivée"),
    today: str | None = typer.Option(None, "--today", help="Date ISO YYYY-MM-DD pour tests/rejeu"),
) -> None:
    """Planifie une relance: `followup APP_ID YYYY-MM-DD` ou `followup schedule OFFER_ID`."""
    if target.strip().lower() == "schedule":
        if value is None:
            raise typer.BadParameter("Indique l'ID de l'offre après `schedule`")
        try:
            offer_id = int(value)
        except ValueError as error:
            raise typer.BadParameter("L'ID offre doit être numérique") from error
        _schedule_followup_for_offer(offer_id, after=after, force=force, today=today)
        return
    if value is None:
        raise typer.BadParameter("Indique une date de relance YYYY-MM-DD")
    try:
        application_id = int(target)
        with connect() as conn:
            init_db(conn)
            application = schedule_application_followup(conn, application_id, value)
    except ValueError as error:
        console.print(str(error))
        raise typer.Exit(1) from error
    console.print(f"Candidature #{application_id} → followup le {application['next_action_at']}")


@application_app.command("followup-schedule")
def application_followup_schedule_alias(
    offer_id: int,
    after: str = typer.Option("", "--after", help="Délai ex: 7d; vide = délai configuré"),
    force: bool = typer.Option(False, "--force", help="Planifier même si la relance auto est désactivée"),
    today: str | None = typer.Option(None, "--today", help="Date ISO YYYY-MM-DD pour tests/rejeu"),
) -> None:
    _schedule_followup_for_offer(offer_id, after=after, force=force, today=today)


@application_app.command("due")
def application_due(today: str | None = typer.Option(None, "--today", help="Date ISO YYYY-MM-DD pour tests/rejeu")) -> None:
    """Liste les relances arrivées à échéance."""
    day = _parse_today(today).isoformat()
    with connect() as conn:
        init_db(conn)
        rows = list_next_actions(conn, today=day, limit=20)
    due = [row for row in rows if row["action"] == "Relancer candidature"]
    if not due:
        console.print("Aucune relance due.")
        return
    table = Table("Offre", "Titre", "Entreprise", "Échéance")
    for row in due:
        table.add_row(str(row["offer_id"]), row["title"], row["company"], row.get("due_date", ""))
    console.print(table)


def _schedule_followup_for_offer(offer_id: int, *, after: str = "", force: bool = False, today: str | None = None) -> None:
    try:
        with connect() as conn:
            init_db(conn)
            config = get_auto_followup_config(conn)
            if not config["enabled"] and not force:
                console.print("Relance auto désactivée. Utilise `application followup-config enable --after 10d` ou --force.")
                return
            delay_days = normalize_followup_delay(after) if after else int(config["delay_days"])
            followup_date = _followup_date_from_delay(delay_days=delay_days, today=today)
            application_id = add_application(conn, offer_id, status="sent")
            application = schedule_application_followup(conn, application_id, followup_date)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    console.print(f"Relance planifiée pour l'offre #{offer_id} le {application['next_action_at']}")


@application_app.command("list")
def application_list() -> None:
    """Liste les candidatures."""
    with connect() as conn:
        init_db(conn)
        applications = list_applications(conn)

    table = Table("ID", "Offre", "Entreprise", "Status", "Date")
    for application in applications:
        table.add_row(
            str(application["id"]),
            application["title"],
            application["company"],
            application["status"],
            application["applied_at"],
        )
    console.print(table)


@app.command()
def report() -> None:
    """Affiche un résumé de la recherche."""
    with connect() as conn:
        init_db(conn)
        summary = application_summary(conn)
        top_offers = list_offers(conn, min_score=70)[:5]

    console.print("Recherche emploi — résumé\n")
    console.print(f"Offres enregistrées : {summary['offers']}")
    console.print(f"Intéressantes       : {summary['interesting']}")
    console.print(f"Candidatures        : {summary['applied']}")
    console.print(f"À relancer          : {summary['followup']}")
    console.print(f"Refusées            : {summary['rejected']}")
    console.print(f"Offres France Travail : {summary['ft_offers']}")
    console.print(f"FT actives             : {summary['active_ft_offers']}")
    console.print(f"Brouillons             : {summary['draft_applications']}")
    console.print(f"Candidatures envoyées  : {summary['sent_applications']}")
    if top_offers:
        console.print("\nTop offres :")
        for offer in top_offers:
            console.print(f"- #{offer['id']} {offer['title']} — score {offer['score']}")


@app.command("next")
def next_actions() -> None:
    """Affiche les prochaines actions utiles."""
    with connect() as conn:
        init_db(conn)
        actions = list_next_actions(conn)

    console.print("Prochaines actions\n")
    if not actions:
        console.print("Aucune action prioritaire.")
        return
    table = Table("Action", "Offre", "Entreprise", "Score", "Guide", width=200)
    for action in actions:
        guidance_parts = [str(action.get("guidance", ""))]
        if action.get("draft_path"):
            guidance_parts.append(str(action["draft_path"]))
        guide = "\n".join(part for part in guidance_parts if part)
        console.print(f"- {action['action']} #{action['offer_id']} — {guide}")
        table.add_row(
            str(action["action"]),
            f"#{action['offer_id']} {action['title']}",
            str(action["company"]),
            str(action["score"]),
            guide,
        )
    console.print(table)


@app.command()
def brief(
    json_output: bool = typer.Option(False, "--json", help="Afficher uniquement un JSON parseable"),
    today: str | None = typer.Option(None, "--today", help="Date ISO YYYY-MM-DD pour tests/rejeu"),
) -> None:
    """Affiche le brief quotidien Julien: offres, actions, relances, blockers et stats."""
    _ensure_option_enabled("brief.enabled", json_output=json_output)
    with connect() as conn:
        init_db(conn)
        payload = build_brief(conn, today=today)

    if json_output:
        console.print_json(data=payload)
        return

    console.print(f"Brief Julien — {payload['date']}")
    _print_brief_offers("Meilleures offres", payload["best_offers"])
    _print_brief_actions("Actions prioritaires", payload["actions"])
    _print_brief_applications("Relances dues", payload["due_followups"])
    _print_brief_applications("Candidatures envoyées sans contact récent", payload["stale_sent"])
    console.print("\nBlockers")
    if payload["blockers"]:
        for blocker in payload["blockers"]:
            console.print(f"- {blocker}")
    else:
        console.print("- Aucun blocker détecté.")
    stats = payload["weekly_stats"]
    console.print(f"\nStats 7 jours — depuis {stats['since']}")
    console.print(
        f"- offres créées: {stats['offers_created']} — candidatures: {stats['applications_created']} — "
        f"brouillons: {stats['drafts']} — envoyées: {stats['sent']} — relances dues: {stats['followups_due']}"
    )


def _print_brief_offers(title: str, offers: list[dict[str, object]]) -> None:
    console.print(f"\n{title}")
    if not offers:
        console.print("- Rien à afficher.")
        return
    for offer in offers:
        console.print(f"- #{offer['id']} {offer['title']} — {offer['company']} — score {offer['score']}")


def _print_brief_actions(title: str, actions: list[dict[str, object]]) -> None:
    console.print(f"\n{title}")
    if not actions:
        console.print("- Aucune action prioritaire.")
        return
    for action in actions:
        console.print(f"- {action['action']} #{action['offer_id']} {action['title']} — {action.get('guidance', '')}")


def _print_brief_applications(title: str, applications: list[dict[str, object]]) -> None:
    console.print(f"\n{title}")
    if not applications:
        console.print("- Rien à relancer.")
        return
    for application in applications:
        due = application.get("due_date", "")
        console.print(
            f"- #{application['offer_id']} {application['title']} — {application['company']} — échéance/contact {due}"
        )


if __name__ == "__main__":
    app()
