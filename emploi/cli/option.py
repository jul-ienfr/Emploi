from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from emploi.cli import _print_option_state, _set_option_enabled, option_app
from emploi.db import connect, get_option, init_db, list_options, toggle_boolean_option

console = Console(soft_wrap=True)


@option_app.command("list")
def option_list() -> None:
    """Liste les options opérateur et leur état."""
    with connect() as conn:
        init_db(conn)
        options = list_options(conn)
    table = Table("Clé", "Active", "Valeur", "Source", "Défaut", "MAJ")
    for option in options:
        table.add_row(
            str(option["key"]),
            "oui" if option["enabled"] else "non",
            str(option["value"]),
            str(option["source"]),
            "oui" if option["default"] else "non",
            str(option["updated_at"]),
        )
    console.print(table)


@option_app.command("get")
def option_get(key: str) -> None:
    """Affiche une option opérateur."""
    try:
        with connect() as conn:
            init_db(conn)
            option = get_option(conn, key)
    except ValueError as error:
        console.print(str(error))
        raise typer.Exit(1) from error
    _print_option_state(option)


@option_app.command("enable")
def option_enable(key: str) -> None:
    """Active une option opérateur."""
    _set_option_enabled(key, True)


@option_app.command("disable")
def option_disable(key: str) -> None:
    """Désactive une option opérateur."""
    _set_option_enabled(key, False)


@option_app.command("toggle")
def option_toggle(key: str) -> None:
    """Inverse une option opérateur."""
    try:
        with connect() as conn:
            init_db(conn)
            option = toggle_boolean_option(conn, key)
    except ValueError as error:
        console.print(str(error))
        raise typer.Exit(1) from error
    _print_option_state(option)
