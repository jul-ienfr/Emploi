from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from emploi import __version__
from emploi.db import (
    add_application,
    add_offer,
    application_summary,
    connect,
    db_path,
    get_offer,
    init_db,
    list_applications,
    list_offers,
    rescore_offer,
    update_offer_status,
)

app = typer.Typer(help="CLI personnel pour chercher, scorer et suivre les offres d'emploi.")
offer_app = typer.Typer(help="Gestion des offres")
application_app = typer.Typer(help="Gestion des candidatures")
app.add_typer(offer_app, name="offer")
app.add_typer(application_app, name="application")
console = Console()


@app.callback()
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


@app.command()
def apply(offer_id: int, notes: str = typer.Option("", "--notes")) -> None:
    """Crée une candidature pour une offre."""
    with connect() as conn:
        init_db(conn)
        application_id = add_application(conn, offer_id, notes=notes)
    console.print(f"Candidature créée #{application_id} pour l'offre #{offer_id}")


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
    if top_offers:
        console.print("\nTop offres :")
        for offer in top_offers:
            console.print(f"- #{offer['id']} {offer['title']} — score {offer['score']}")


if __name__ == "__main__":
    app()
