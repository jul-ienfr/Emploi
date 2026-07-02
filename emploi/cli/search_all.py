"""search-all command — aggregate results from all job sources (FR + CH)."""

from __future__ import annotations

import csv

import typer

from emploi.cli import app, console
from emploi.logging import get_logger

logger = get_logger("cli.search_all")


@app.command()
def search_all(
    query: str = typer.Argument(..., help="Mots-clés de recherche"),
    location: str = typer.Option("", "--location", "-l", help="Lieu (ex: Lausanne, Paris)"),
    country: str = typer.Option(
        "",
        "--country",
        "-c",
        help="Filtrer par pays: FR, CH, ou vide pour les deux",
    ),
    max_per_source: int = typer.Option(20, "--max", help="Résultats max par source"),
    json_output: bool = typer.Option(False, "--json", help="Sortie JSON"),
    export_csv: str = typer.Option("", "--export-csv", help="Exporter en CSV vers ce fichier"),
) -> None:
    """Recherche multi-sources sur tous les portails d'emploi (FR + CH).

    Agrège les résultats de toutes les sources configurées, déduplique
    et affiche un classement unifié.

    Exemples:

        emploi search-all "python développeur"

        emploi search-all "support informatique" --location Lausanne --country CH

        emploi search-all "devops" --export-csv results.csv
    """
    from emploi.sources.aggregator import search_all as _search_all

    countries = [c.strip().upper() for c in country.split(",") if c.strip()] if country else None

    offers = _search_all(
        query,
        location=location,
        countries=countries,
        max_per_source=max_per_source,
    )

    if not offers:
        console.print("[yellow]Aucune offre trouvée.[/yellow]")
        raise typer.Exit(0)

    if export_csv:
        _export_to_csv(offers, export_csv)
        console.print(f"[green]✓ {len(offers)} offres exportées vers {export_csv}[/green]")

    if json_output:
        console.print_json(data=[o.to_dict() for o in offers])
    else:
        _print_table(offers)


def _print_table(offers: list) -> None:
    from rich.table import Table

    table = Table(title=f"{len(offers)} offres trouvées")
    table.add_column("Source", style="dim", width=8)
    table.add_column("Titre", style="bold")
    table.add_column("Entreprise")
    table.add_column("Lieu")
    table.add_column("Contrat", width=6)
    table.add_column("Salaire", width=10)

    for offer in offers:
        table.add_row(
            offer.source,
            offer.title[:50],
            offer.company[:25],
            offer.location[:20],
            offer.contract_type[:6],
            offer.salary[:10] if offer.salary else "",
        )

    console.print(table)


def _export_to_csv(offers: list, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["source", "title", "company", "location", "url", "description", "contract_type", "salary"]
        )
        writer.writeheader()
        for offer in offers:
            writer.writerow(offer.to_dict())
