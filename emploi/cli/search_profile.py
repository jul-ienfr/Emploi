from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from emploi.browser.errors import ManagedBrowserError
from emploi.browser.models import DEFAULT_PROFILE, DEFAULT_SITE
from emploi.cli import (
    _ensure_option_enabled,
    _format_auto_apply,
    _format_search_radius,
    _handle_browser_error,
    _resolve_sources,
    _set_search_profile_enabled,
    search_profile_app,
)
from emploi.daemon import watch_loop
from emploi.db import (
    add_saved_search,
    configure_saved_search_auto_apply,
    connect,
    get_saved_search,
    init_db,
    install_default_julien_search_profiles,
    list_saved_searches,
    set_saved_search_enabled,
)
from emploi.france_travail.flows import run_saved_search
from emploi.hellowork_search import run_hellowork_saved_search

console = Console(soft_wrap=True)


@search_profile_app.command("add")
def search_profile_add(
    name: str,
    query: str = typer.Option(..., "--query", "-q"),
    where_text: str = typer.Option("", "--where", "--location", "-w"),
    radius: int = typer.Option(0, "--radius"),
    contract: str = typer.Option("", "--contract"),
    disabled: bool = typer.Option(False, "--disabled"),
    source: str = typer.Option("all", "--source", help="Source: all (tous les sites), france-travail, ou hellowork"),
) -> None:
    """Ajoute un profil de recherche."""
    if source not in ("all", "france-travail", "hellowork"):
        raise typer.BadParameter("--source doit être 'all', 'france-travail' ou 'hellowork'")
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
            source=source,
        )
    console.print(f"Profil de recherche ajouté #{search_id} — {name} (source: {source})")


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
        "Source",
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
    console.print("Colonnes: Source — Actif — Dernier run — Auto-apply — Notes")
    for saved in searches:
        auto_apply = _format_auto_apply(saved)
        console.print(
            f"Profil {saved['name']} | Source: {saved['source']} | Actif: {'oui' if saved['enabled'] else 'non'} | "
            f"Dernier run: {saved['last_run_at'] or 'jamais'} | Auto-apply: {auto_apply} | Notes: {saved['notes']}"
        )
        table.add_row(
            str(saved["id"]),
            saved["name"],
            saved["source"],
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
    """Exécute un profil de recherche sauvegardé."""
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
                    source = str(saved["source"]) if "source" in saved.keys() else "all"
                    sources_to_run = _resolve_sources(source)
                    profile_total = 0
                    for src in sources_to_run:
                        if src == "hellowork":
                            results = run_hellowork_saved_search(conn, int(saved["id"]), site=site, profile=profile)
                        else:
                            _ensure_option_enabled("france_travail.enabled")
                            results = run_saved_search(conn, int(saved["id"]), site=site, profile=profile)
                        profile_total += len(results)
                        total += len(results)
                        created += sum(1 for result in results if result.created)
                        updated += sum(1 for result in results if not result.created)
                    refreshed = conn.execute("SELECT * FROM saved_searches WHERE id = ?", (saved["id"],)).fetchone()
                    rows.append((saved, profile_total, refreshed["last_run_at"] if refreshed else ""))
                console.print(
                    f"{total} offre(s) traitée(s) via {len(profiles)} profil(s) actif(s) — "
                    f"créée(s): {created} — mise(s) à jour: {updated}"
                )
                table = Table("Profil", "Source", "Actif", "Offres", "Dernier run")
                for saved, count, last_run in rows:
                    s = str(saved["source"]) if "source" in saved.keys() else "france-travail"
                    table.add_row(saved["name"], s, "oui", str(count), last_run or "jamais")
                console.print(table)
                return
            if name_or_id is None:
                raise typer.BadParameter("Indique un nom/ID ou --all")
            saved = get_saved_search(conn, name_or_id)
            if saved is None:
                raise typer.BadParameter(f"Profil de recherche introuvable: {name_or_id}")
            source = str(saved["source"]) if "source" in saved.keys() else "all"
            sources_to_run = _resolve_sources(source)
            if not sources_to_run:
                raise typer.BadParameter(f"Source inconnue: {source}")
            if len(sources_to_run) > 1:
                if not typer.confirm(f"Lancer '{saved['name']}' sur {', '.join(sources_to_run)} ?"):
                    console.print("Annulé.")
                    return
            elif sources_to_run[0] == "hellowork":
                if not typer.confirm(f"Lancer le profil HelloWork '{saved['name']}' ?"):
                    console.print("Annulé.")
                    return
            all_results = []
            for src in sources_to_run:
                if src == "hellowork":
                    results = run_hellowork_saved_search(conn, name_or_id, site=site, profile=profile)
                else:
                    _ensure_option_enabled("france_travail.enabled")
                    results = run_saved_search(conn, name_or_id, site=site, profile=profile)
                all_results.extend(results)
                console.print(f"{len(results)} offre(s) {src} traitée(s)")
            results = all_results
    except ManagedBrowserError as error:
        _handle_browser_error(error)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    created = sum(1 for result in results if result.created)
    updated = len(results) - created
    console.print(f"créée(s): {created} — mise(s) à jour: {updated}")
    table = Table("ID", "Action", "Score", "Titre", "URL")
    for result in results:
        table.add_row(str(result.offer_id), "créée" if result.created else "mise à jour", str(result.score), result.title, result.browser_url)
    console.print(table)


@search_profile_app.command("watch")
def search_profile_watch(
    interval: int = typer.Option(30, "--interval", "-i", help="Intervalle en minutes entre chaque cycle"),
    once: bool = typer.Option(False, "--once", help="Exécute un seul cycle puis s'arrête"),
    site: str = typer.Option(DEFAULT_SITE, "--site"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Exécute les profils actifs en boucle, toutes les N minutes.

    Utilisez --once pour un seul cycle (utile pour vérifier le fonctionnement).
    """
    watch_loop(interval_minutes=interval, once=once, site=site, profile=profile)
