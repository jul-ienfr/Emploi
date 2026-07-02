from __future__ import annotations

import typer
from rich.console import Console

from emploi.auto_apply import run_auto_apply_for_enabled_profiles, run_auto_apply_for_saved_search
from emploi.cli import _ensure_option_enabled, auto_apply_app
from emploi.db import connect, init_db

console = Console(soft_wrap=True)


@auto_apply_app.command("run")
def auto_apply_run(
    profile_name: str | None = typer.Option(None, "--profile", help="Nom ou ID du profil à exécuter"),
    all_profiles: bool = typer.Option(
        False, "--all-enabled", help="Exécuter tous les profils actifs avec auto-apply actif"
    ),
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
                results = [run_auto_apply_for_saved_search(conn, profile_name, drafts_dir=drafts_dir, today=today)]  # type: ignore[arg-type]
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
