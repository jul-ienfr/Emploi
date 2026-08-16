from __future__ import annotations

import typer
from rich.console import Console

from emploi import config as emploi_config
from emploi.cli import identity_app

console = Console(soft_wrap=True)


@identity_app.command("set")
def identity_set(
    firstname: str = typer.Option("", "--firstname", "-f", help="Prénom (vide = conservé)"),
    lastname: str = typer.Option("", "--lastname", "-l", help="Nom de famille (vide = conservé)"),
    email: str = typer.Option("", "--email", "-e", help="Email (vide = conservé)"),
) -> None:
    """Enregistre l'identité locale pour le pré-remplissage des candidatures.

    Stockée dans ~/.config/emploi/identity.json (jamais committée).
    Utilisée par `emploi hellowork apply` pour remplir Firstname/Lastname/Email.
    """
    try:
        identity = emploi_config.set_identity(firstname=firstname, lastname=lastname, email=email)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    console.print("Identité enregistrée :")
    console.print(f"  Prénom : {identity['firstname']}")
    console.print(f"  Nom    : {identity['lastname']}")
    console.print(f"  Email  : {identity['email']}")


@identity_app.command("show")
def identity_show(
    json_output: bool = typer.Option(False, "--json", help="Afficher en JSON"),
) -> None:
    """Affiche l'identité locale configurée."""
    identity = emploi_config.get_identity()
    if json_output:
        console.print_json(data=identity)
        return
    if not any(identity.values()):
        console.print(
            "Aucune identité configurée. Utilise `emploi identity set --firstname ... --lastname ... --email ...`."
        )
        return
    console.print("Identité configurée :")
    console.print(f"  Prénom : {identity['firstname']}")
    console.print(f"  Nom    : {identity['lastname']}")
    console.print(f"  Email  : {identity['email']}")
