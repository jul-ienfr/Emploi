from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from emploi.cli import _ensure_option_enabled, import_app
from emploi.db import connect, init_db
from emploi.importers import import_offers_file

console = Console(soft_wrap=True)


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
