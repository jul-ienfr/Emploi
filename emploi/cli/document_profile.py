from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from emploi import config as emploi_config
from emploi.cli import document_profile_app

console = Console(soft_wrap=True)


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
