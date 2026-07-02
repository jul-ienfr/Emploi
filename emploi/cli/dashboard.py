"""dashboard command — start the web dashboard."""

from __future__ import annotations

import typer

from emploi.cli import app, console


@app.command()
def dashboard(
    host: str = typer.Option("127.0.0.1", "--host", help="Adresse de bind"),
    port: int = typer.Option(8050, "--port", "-p", help="Port du serveur"),
) -> None:
    """Lance le dashboard web pour visualiser les offres.

    Ouvre http://localhost:8050 dans le navigateur pour voir les offres,
    les filtres, les stats et les scores.

    Nécessite Flask: pip install flask
    """
    try:
        from emploi.dashboard import run_dashboard
    except ImportError:
        console.print("[red]Flask requis. Installe-le avec: pip install flask[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Dashboard: http://{host}:{port}[/green]")
    console.print("Ctrl+C pour arrêter")
    run_dashboard(host=host, port=port)
