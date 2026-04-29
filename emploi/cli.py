from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from emploi.applications import create_application_draft
from emploi.brief import build_brief
from emploi.doctor import build_doctor_report

from emploi import __version__
from emploi.browser.client import ManagedBrowserClient
from emploi.browser.errors import ManagedBrowserError
from emploi.browser.models import DEFAULT_PROFILE, DEFAULT_SITE, BrowserCommandResult
from emploi.db import (
    add_application,
    add_offer,
    add_saved_search,
    application_summary,
    connect,
    db_path,
    get_offer,
    init_db,
    install_default_julien_search_profiles,
    list_applications,
    list_next_actions,
    list_offers,
    list_saved_searches,
    rescore_offer,
    schedule_application_followup,
    update_application_status,
    update_offer_status,
)
from emploi.france_travail.extractors import extract_offers
from emploi.france_travail.flows import (
    apply_check_offer,
    build_search_url,
    draft_application,
    open_offer,
    refresh_offer,
    run_saved_search,
    search_offers,
)
from emploi.importers import import_offers_file

app = typer.Typer(help="CLI personnel pour chercher, scorer et suivre les offres d'emploi.")
offer_app = typer.Typer(help="Gestion des offres")
application_app = typer.Typer(help="Gestion des candidatures")
browser_app = typer.Typer(help="Commandes Managed Browser")
ft_app = typer.Typer(help="Flux France Travail via Managed Browser")
search_profile_app = typer.Typer(help="Profils de recherche sauvegardés")
import_app = typer.Typer(help="Imports génériques sans scraping")
app.add_typer(offer_app, name="offer")
app.add_typer(application_app, name="application")
app.add_typer(browser_app, name="browser")
app.add_typer(ft_app, name="ft")
app.add_typer(search_profile_app, name="search-profile")
app.add_typer(import_app, name="import")
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


@app.command()
def doctor(json_output: bool = typer.Option(False, "--json", help="Afficher un diagnostic JSON parseable")) -> None:
    """Diagnostique l'état local du CLI, de SQLite et du Managed Browser."""
    report = build_doctor_report()
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
    browser = report["managed_browser"]
    console.print(f"Managed Browser : {browser['status']} — {browser['command']}")
    if browser.get("error"):
        console.print(f"Erreur        : {browser['error']}")
    if report["recommended_actions"]:
        console.print("Actions recommandées :")
        for action in report["recommended_actions"]:
            console.print(f"- {action}")


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
) -> None:
    """Liste les offres."""
    with connect() as conn:
        init_db(conn)
        offers = list_offers(conn, status=status, min_score=min_score)

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
        opened = client.open(search_url, site=site, profile=profile)
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
    drafts_dir: str | None = typer.Option(None, "--drafts-dir", help="Répertoire des brouillons"),
    site: str = typer.Option(DEFAULT_SITE, "--site"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Vérifie, prépare ou ouvre une candidature France Travail; ne soumet jamais automatiquement."""
    if not any((check, draft, open_browser)):
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
    except ManagedBrowserError as error:
        _handle_browser_error(error)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


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
        "Notes",
        width=160,
    )
    console.print("Colonnes: Actif — Dernier run — Notes")
    for saved in searches:
        console.print(
            f"Profil {saved['name']} | Actif: {'oui' if saved['enabled'] else 'non'} | "
            f"Dernier run: {saved['last_run_at'] or 'jamais'} | Notes: {saved['notes']}"
        )
        table.add_row(
            str(saved["id"]),
            saved["name"],
            saved["query"],
            saved["where_text"],
            str(saved["radius"]),
            saved["contract"],
            "oui" if saved["enabled"] else "non",
            saved["last_run_at"] or "jamais",
            saved["notes"],
        )
    console.print(table)


@search_profile_app.command("run")
def search_profile_run(
    name_or_id: str | None = typer.Argument(None),
    all_profiles: bool = typer.Option(False, "--all", help="Exécuter tous les profils actifs"),
    site: str = typer.Option(DEFAULT_SITE, "--site"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Exécute un profil de recherche via France Travail."""
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
    try:
        with connect() as conn:
            init_db(conn)
            result = create_application_draft(conn, offer_id, drafts_dir=drafts_dir)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    console.print(f"Brouillon créé : {result.draft_path}")
    console.print("Aucune soumission automatique : relis puis envoie manuellement.")


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


@application_app.command("followup")
def application_followup(application_id: int, followup_date: str) -> None:
    """Planifie une relance manuelle au format YYYY-MM-DD."""
    try:
        with connect() as conn:
            init_db(conn)
            application = schedule_application_followup(conn, application_id, followup_date)
    except ValueError as error:
        console.print(str(error))
        raise typer.Exit(1) from error
    console.print(f"Candidature #{application_id} → followup le {application['next_action_at']}")


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
