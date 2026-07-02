from __future__ import annotations

import typer
from rich.console import Console

from emploi.cli import app
from emploi.doctor import build_doctor_report

console = Console(soft_wrap=True)


@app.command()
def doctor(
    json_output: bool = typer.Option(False, "--json", help="Afficher un diagnostic JSON parseable"),
    probe_browser: bool = typer.Option(True, "--probe-browser/--no-browser-probe", help="Exécuter le probe Managed Browser"),
) -> None:
    """Diagnostique l'état local du CLI, de SQLite et du Managed Browser."""
    report = build_doctor_report(probe_browser=probe_browser)
    if json_output:
        console.print_json(data=report)
        return

    console.print(f"Diagnostic Emploi — {report['status']}")
    console.print(f"Version       : {report['version']}")
    database = report["database"]
    console.print(f"Base SQLite   : {database['status']} — {database['path']}")
    if database["status"] == "ok":
        console.print(f"Offres        : {database['offers']}")
        console.print(f"Candidatures  : {database['applications']}")
    accounts = report.get("accounts", {})
    if accounts.get("status") == "ok":
        accts = accounts.get("accounts", [])
        default_p = accounts.get("default_profile", "?")
        console.print(f"Comptes FT    : {accounts['count']} — défaut: {default_p}")
        for a in accts:
            mark = " (défaut)" if a.get("default") else ""
            console.print(f"  - {a['key']} → {a['profile']}{mark}")
    elif accounts.get("status") == "missing":
        console.print(f"Comptes FT    : aucun configuré — {accounts.get('error', '')}")
    browser = report["managed_browser"]
    console.print(f"Managed Browser : {browser['status']} — {browser['command']}")
    if browser.get("error"):
        console.print(f"Erreur        : {browser['error']}")
    if report["recommended_actions"]:
        console.print("Actions recommandées :")
        for action in report["recommended_actions"]:
            console.print(f"- {action}")
