from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from emploi.cli import _ensure_option_enabled, offer_app
from emploi.db import (
    add_offer,
    connect,
    get_offer,
    init_db,
    list_offers,
    rescore_offer,
    update_offer_status,
)

console = Console(soft_wrap=True)


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
    all_offers: bool = typer.Option(False, "--all", help="Inclure aussi les offres inactives/archivées"),
) -> None:
    """Liste les offres."""
    with connect() as conn:
        init_db(conn)
        offers = list_offers(conn, status=status, min_score=min_score, include_inactive=all_offers)

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
    _ensure_option_enabled("scoring.enabled")
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
