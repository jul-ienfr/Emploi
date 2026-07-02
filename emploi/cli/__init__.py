from __future__ import annotations

from datetime import date, timedelta

import typer
from rich.console import Console

from emploi import __version__
from emploi import config as emploi_config
from emploi.db import (
    FEATURE_OPTIONS,
    add_application,
    connect,
    db_path,
    get_auto_followup_config,
    get_boolean_option,
    init_db,
    normalize_followup_delay,
    schedule_application_followup,
    set_boolean_option,
    set_saved_search_enabled,
    update_application_status,
    validate_option_key,
)
from emploi.db import (
    get_saved_search as get_saved_search,
)
from emploi.db import (
    list_saved_searches as list_saved_searches,
)

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


# ---------------------------------------------------------------------------
# Shared helpers used by multiple command modules
# ---------------------------------------------------------------------------

def _print_browser_result(result) -> None:
    console.print(f"Managed Browser {result.command} — site={result.site} profile={result.profile}")
    console.print_json(data=result.payload)


def _print_json_or_text(payload: dict, *, json_output: bool, text: str) -> None:
    if json_output:
        console.print_json(data=payload)
    else:
        console.print(text)


def _handle_browser_error(error) -> None:
    console.print(f"[red]{error}[/red]")
    raise typer.Exit(1)


def _option_disabled_payload(key: str) -> dict[str, str]:
    return {"status": "disabled", "option": key, "message": f"Option désactivée: {key}"}


def _option_is_enabled_without_creating_db(key: str, conn=None) -> bool:
    normalized = validate_option_key(key)
    if conn is not None:
        return get_boolean_option(conn, normalized)
    path = db_path()
    if not path.exists():
        return FEATURE_OPTIONS[normalized]
    with connect(path) as conn_tmp:
        init_db(conn_tmp)
        return get_boolean_option(conn_tmp, normalized)


def _ensure_option_enabled(key: str, *, json_output: bool = False, conn=None) -> None:
    try:
        enabled = _option_is_enabled_without_creating_db(key, conn=conn)
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


# ---------------------------------------------------------------------------
# Helpers shared across command modules (application, search_profile, etc.)
# ---------------------------------------------------------------------------

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


def _application_status_update(application_id: int, status: str) -> None:
    try:
        with connect() as conn:
            init_db(conn)
            application = update_application_status(conn, application_id, status)
    except ValueError as error:
        console.print(str(error))
        raise typer.Exit(1) from error
    console.print(f"Candidature #{application_id} → {application['status']}")


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


def _set_search_profile_enabled(name_or_id: str, enabled: bool) -> None:
    try:
        with connect() as conn:
            init_db(conn)
            saved = set_saved_search_enabled(conn, name_or_id, enabled)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    status = "activé" if saved["enabled"] else "désactivé"
    console.print(f"Profil de recherche {status} #{saved['id']} — {saved['name']}")


# All available search engines — add new sources here
SEARCH_ENGINES: list[str] = ["france-travail", "hellowork"]


def _resolve_sources(source: str) -> list[str]:
    """Resolve a profile source value to a list of engines to run.

    'all' → every engine in SEARCH_ENGINES
    'france-travail' / 'hellowork' → just that one
    """
    if source == "all":
        return list(SEARCH_ENGINES)
    if source in SEARCH_ENGINES:
        return [source]
    return []


def _set_option_enabled(key: str, enabled: bool) -> None:
    try:
        with connect() as conn:
            init_db(conn)
            option = set_boolean_option(conn, key, enabled)
    except ValueError as error:
        console.print(str(error))
        raise typer.Exit(1) from error
    _print_option_state(option)


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


# ---------------------------------------------------------------------------
# Main callback + init command
# ---------------------------------------------------------------------------

@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(False, "--version", help="Afficher la version"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Activer les logs de debug"),
) -> None:
    if version:
        console.print(__version__)
        raise typer.Exit()
    if verbose:
        import os
        os.environ["EMPLOI_LOG_LEVEL"] = "DEBUG"
        # Force reconfiguration of logging
        import emploi.logging as _log_mod
        _log_mod._configured = False
        _log_mod._ensure_configured()


@app.command()
def init() -> None:
    """Initialise la base SQLite locale."""
    path = db_path()
    with connect(path) as conn:
        init_db(conn)
    console.print(f"Base initialisée : {path}")


# ---------------------------------------------------------------------------
# Import command modules to register commands on the sub-apps
# ---------------------------------------------------------------------------
import emploi.cli.application  # noqa: E402, F401
import emploi.cli.auto_apply  # noqa: E402, F401
import emploi.cli.browser  # noqa: E402, F401
import emploi.cli.doctor  # noqa: E402, F401
import emploi.cli.document_profile  # noqa: E402, F401
import emploi.cli.ft  # noqa: E402, F401
import emploi.cli.hellowork  # noqa: E402, F401
import emploi.cli.import_  # noqa: E402, F401
import emploi.cli.kanban  # noqa: E402, F401
import emploi.cli.nextcloud  # noqa: E402, F401
import emploi.cli.offer  # noqa: E402, F401
import emploi.cli.option  # noqa: E402, F401
import emploi.cli.report  # noqa: E402, F401
import emploi.cli.search_profile  # noqa: E402, F401

if __name__ == "__main__":
    app()
