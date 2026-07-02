from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from emploi import config as emploi_config
from emploi.applications import create_application_draft
from emploi.cli import (
    _ensure_option_enabled,
    _followup_date_from_delay,
    _parse_today,
    _resolve_nextcloud_document_profile,
    _schedule_followup_for_offer,
    application_app,
)
from emploi.db import (
    add_application,
    connect,
    get_auto_followup_config,
    get_followup_sync_config,
    init_db,
    list_applications,
    list_next_actions,
    normalize_followup_delay,
    schedule_application_followup,
    set_auto_followup_config,
    set_followup_sync_config,
    update_offer_status,
)
from emploi.nextcloud_deck import create_offer_card
from emploi.nextcloud_files import export_application_to_nextcloud
from emploi.nextcloud_tasks import create_followup_task, sync_due_followup_tasks

console = Console(soft_wrap=True)


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


@application_app.command("export")
def application_export(
    offer_id: int,
    to_nextcloud: bool = typer.Option(
        False, "--to-nextcloud", help="Exporter le dossier candidature vers Nextcloud Files/WebDAV"
    ),
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
    force_card: bool = typer.Option(
        False, "--force-card", help="Créer une nouvelle carte même si un événement existe déjà"
    ),
    mark_sent: bool = typer.Option(
        False, "--mark-sent", help="Enregistrer une candidature envoyée locale avant relance"
    ),
    schedule_followup: bool | None = typer.Option(
        None,
        "--schedule-followup/--no-schedule-followup",
        help="Planifier une relance selon la config ou désactiver pour ce run",
    ),
    followup_after: str = typer.Option(
        "", "--followup-after", help="Délai de relance pour ce run, ex: 7d; vide = config"
    ),
    sync_followup_task: bool | None = typer.Option(
        None,
        "--sync-followup-task/--no-sync-followup-task",
        help="Créer la tâche Nextcloud de relance selon config ou choix du run",
    ),
    tasks_endpoint_name: str = typer.Option("", "--tasks-endpoint", help="Endpoint nextcloud-tasks; vide = défaut"),
    force_followup_task: bool = typer.Option(
        False, "--force-followup-task", help="Recréer la tâche de relance même si un événement existe"
    ),
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
            should_schedule_followup = (
                bool(auto_followup["enabled"]) if schedule_followup is None else schedule_followup
            )
            followup_date = ""
            followup_task_result = None
            followup_requires_sent = False
            application_id = None
            should_sync_followup_task = False
            tasks_endpoint = None
            existing_sent = None
            if should_schedule_followup:
                delay_days = (
                    normalize_followup_delay(followup_after) if followup_after else int(auto_followup["delay_days"])
                )  # type: ignore[call-overload]
                followup_date = _followup_date_from_delay(delay_days=delay_days, today=today)
                if not dry_run:
                    existing_sent = conn.execute(
                        "SELECT id FROM applications WHERE offer_id = ? AND status IN ('sent', 'followup') ORDER BY id DESC LIMIT 1",
                        (offer_id,),
                    ).fetchone()
                sync_config = get_followup_sync_config(conn)
                should_sync_followup_task = (
                    bool(sync_config["enabled"]) if sync_followup_task is None else sync_followup_task
                )
                if should_sync_followup_task and (dry_run or existing_sent is not None or mark_sent):
                    tasks_endpoint = (
                        emploi_config.get_nextcloud_tasks_endpoint(tasks_endpoint_name)
                        if tasks_endpoint_name
                        else emploi_config.get_default_nextcloud_tasks_endpoint()
                    )
                    if tasks_endpoint is None:
                        raise ValueError(
                            "Aucun endpoint Nextcloud Tasks configuré. Utilise `emploi nextcloud-tasks set ...`."
                        )
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
                application_id = (
                    int(existing_sent["id"])
                    if existing_sent is not None
                    else add_application(conn, offer_id, status="sent")
                )
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
                        followup_task_result = None  # dry-run: no actual task created
                    else:
                        followup_task_result = create_followup_task(  # type: ignore[assignment]
                            conn,
                            application_id=application_id,  # type: ignore[arg-type]
                            endpoint=tasks_endpoint,  # type: ignore[arg-type]
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
            console.print(f"Tâche Nextcloud : {followup_task_result.summary}")  # type: ignore[attr-defined]
            if followup_task_result.reused_existing:  # type: ignore[attr-defined]
                console.print("Tâche déjà enregistrée : aucune nouvelle tâche créée.")
            elif followup_task_result.href:  # type: ignore[attr-defined]
                console.print(f"Tâche href : {followup_task_result.href}")  # type: ignore[attr-defined]
    elif followup_requires_sent:
        console.print("Relance : non planifiée (aucune candidature envoyée locale; utilise --mark-sent)")
    elif schedule_followup is False:
        console.print("Relance : ignorée pour ce run")
    else:
        console.print("Relance : non planifiée (auto désactivé)")
    if dry_run:
        console.print("Dry-run : aucun upload, aucun événement, aucune carte créée.")


@application_app.command("status")
def application_status(application_id: int, status: str) -> None:
    """Change le statut d'une candidature dans le pipeline."""
    from emploi.cli import _application_status_update

    _application_status_update(application_id, status)


@application_app.command("update")
def application_update(application_id: int, status: str) -> None:
    """Alias sûr pour changer le statut d'une candidature."""
    from emploi.cli import _application_status_update

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
    endpoint = (
        emploi_config.get_nextcloud_tasks_endpoint(endpoint_name)
        if endpoint_name
        else emploi_config.get_default_nextcloud_tasks_endpoint()
    )
    if endpoint is None:
        raise typer.BadParameter("Aucun endpoint Nextcloud Tasks configuré. Utilise `emploi nextcloud-tasks set ...`.")
    try:
        with connect() as conn:
            init_db(conn)
            if application_id:
                results = [
                    create_followup_task(
                        conn, application_id=application_id, endpoint=endpoint, dry_run=dry_run, force=force
                    )
                ]
            else:
                results = sync_due_followup_tasks(
                    conn, endpoint=endpoint, today=_parse_today(today).isoformat(), dry_run=dry_run, force=force
                )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    if not results:
        console.print("Aucune relance à synchroniser.")
        return
    for result in results:
        verb = "préparée" if dry_run else ("déjà enregistrée" if result.reused_existing else "créée")
        console.print(
            f"Tâche Nextcloud {verb} : candidature #{result.application_id} — {result.summary} — échéance {result.due_date}"
        )
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
def application_due(
    today: str | None = typer.Option(None, "--today", help="Date ISO YYYY-MM-DD pour tests/rejeu"),
) -> None:
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
        table.add_row(str(row["offer_id"]), row["title"], row["company"], row.get("due_date", ""))  # type: ignore[arg-type]
    console.print(table)


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
