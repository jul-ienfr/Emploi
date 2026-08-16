from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from emploi.brief import build_brief
from emploi.cli import (
    _ensure_option_enabled,
    _print_brief_actions,
    _print_brief_applications,
    _print_brief_offers,
    app,
)
from emploi.db import (
    add_application,
    application_summary,
    connect,
    get_option,
    init_db,
    list_next_actions,
    list_offers,
    list_saved_searches,
)

console = Console(soft_wrap=True)


@app.command("daily")
def daily(
    run_profiles: bool = typer.Option(
        True, "--run/--no-run", help="Exécuter les profils de recherche actifs avant le brief (défaut: oui)"
    ),
    probe_browser: bool = typer.Option(
        True, "--probe-browser/--no-probe-browser", help="Probe le Managed Browser dans le diagnostic (défaut: oui)"
    ),
    today: str | None = typer.Option(None, "--today", help="Date ISO YYYY-MM-DD pour tests/rejeu"),
) -> None:
    """Rituel quotidien en une commande : diagnostic → scan des profils → brief → prochaines actions."""
    # 1. Diagnostic
    from emploi.doctor import build_doctor_report

    doctor = build_doctor_report(probe_browser=probe_browser)
    console.print(f"[Daily] Diagnostic : {doctor['status']}")
    for action in doctor["recommended_actions"]:
        console.print(f"  ⚠ {action}")
    if doctor["status"] != "ok":
        console.print("  → Corrige les points ci-dessus, ou utilise --no-run pour le brief sur les données existantes.")

    # 2. Scan des profils actifs (même logique que search-profile run --all)
    if run_profiles:
        with connect() as conn:
            init_db(conn)
            if get_option(conn, "managed_browser.enabled")["enabled"]:
                console.print("\n[Daily] Scan des profils actifs…")
                total = created = updated = 0
                for saved in list_saved_searches(conn, enabled=True):
                    from emploi.cli import _resolve_sources

                    source = str(saved["source"]) if "source" in saved.keys() else "all"
                    for src in _resolve_sources(source):
                        try:
                            if src == "hellowork":
                                from emploi.hellowork_search import run_hellowork_saved_search

                                results = run_hellowork_saved_search(conn, int(saved["id"]))
                            else:
                                if not get_option(conn, "france_travail.enabled")["enabled"]:
                                    continue
                                from emploi.france_travail.flows import run_saved_search

                                results = run_saved_search(conn, int(saved["id"]))
                        except Exception as exc:
                            console.print(f"  ✗ {saved['name']} ({src}) : {exc}")
                            continue
                        total += len(results)
                        created += sum(1 for r in results if r.created)
                        updated += sum(1 for r in results if not r.created)
                        console.print(f"  ✓ {saved['name']} ({src}) : {len(results)} offre(s)")
                console.print(f"  Total : {total} offre(s) — créée(s): {created} — mise(s) à jour: {updated}")
            else:
                console.print("[Daily] Scan ignoré (option managed_browser.enabled désactivée)")

    # 3. Brief
    with connect() as conn:
        init_db(conn)
        if get_option(conn, "brief.enabled")["enabled"]:
            payload = build_brief(conn, today=today)
            console.print(f"\n[Daily] {payload['date']}")
            _print_brief_offers("Meilleures offres", payload["best_offers"])
            _print_brief_actions("Actions prioritaires", payload["actions"])
            _print_brief_applications("Relances dues", payload["due_followups"])
        else:
            console.print("\n[Daily] Brief ignoré (option brief.enabled désactivée)")

    # 4. Prochaines actions
    actions = list_next_actions(conn, today=today)
    if actions:
        console.print("\n[Daily] Prochaines actions")
        for action in actions[:10]:
            guide = str(action.get("guidance", "")) or ""
            console.print(f"  - {action['action']} #{action['offer_id']} — {guide}")
    else:
        console.print("\n[Daily] Aucune action prioritaire.")


@app.command()
def apply(offer_id: int, notes: str = typer.Option("", "--notes")) -> None:
    """Crée une candidature pour une offre."""
    with connect() as conn:
        init_db(conn)
        application_id = add_application(conn, offer_id, notes=notes)
    console.print(f"Candidature créée #{application_id} pour l'offre #{offer_id}")


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
    console.print(f"Offres France Travail : {summary['ft_offers']}")
    console.print(f"FT actives             : {summary['active_ft_offers']}")
    console.print(f"Brouillons             : {summary['draft_applications']}")
    console.print(f"Candidatures envoyées  : {summary['sent_applications']}")
    if top_offers:
        console.print("\nTop offres :")
        for offer in top_offers:
            console.print(f"- #{offer['id']} {offer['title']} — score {offer['score']}")


@app.command("next")
def next_actions() -> None:
    """Affiche les prochaines actions utiles."""
    with connect() as conn:
        init_db(conn)
        actions = list_next_actions(conn)

    console.print("Prochaines actions\n")
    if not actions:
        console.print("Aucune action prioritaire.")
        return
    table = Table("Action", "Offre", "Entreprise", "Score", "Guide", width=200)
    for action in actions:
        guidance_parts = [str(action.get("guidance", ""))]
        if action.get("draft_path"):
            guidance_parts.append(str(action["draft_path"]))
        guide = "\n".join(part for part in guidance_parts if part)
        console.print(f"- {action['action']} #{action['offer_id']} — {guide}")
        table.add_row(
            str(action["action"]),
            f"#{action['offer_id']} {action['title']}",
            str(action["company"]),
            str(action["score"]),
            guide,
        )
    console.print(table)


@app.command()
def brief(
    json_output: bool = typer.Option(False, "--json", help="Afficher uniquement un JSON parseable"),
    today: str | None = typer.Option(None, "--today", help="Date ISO YYYY-MM-DD pour tests/rejeu"),
) -> None:
    """Affiche le brief quotidien Julien: offres, actions, relances, blockers et stats."""
    _ensure_option_enabled("brief.enabled", json_output=json_output)
    with connect() as conn:
        init_db(conn)
        payload = build_brief(conn, today=today)

    if json_output:
        console.print_json(data=payload)
        return

    console.print(f"Brief Julien — {payload['date']}")
    _print_brief_offers("Meilleures offres", payload["best_offers"])
    _print_brief_actions("Actions prioritaires", payload["actions"])
    _print_brief_applications("Relances dues", payload["due_followups"])
    _print_brief_applications("Candidatures envoyées sans contact récent", payload["stale_sent"])
    console.print("\nBlockers")
    if payload["blockers"]:
        for blocker in payload["blockers"]:
            console.print(f"- {blocker}")
    else:
        console.print("- Aucun blocker détecté.")
    stats = payload["weekly_stats"]
    console.print(f"\nStats 7 jours — depuis {stats['since']}")
    console.print(
        f"- offres créées: {stats['offers_created']} — candidatures: {stats['applications_created']} — "
        f"brouillons: {stats['drafts']} — envoyées: {stats['sent']} — relances dues: {stats['followups_due']}"
    )
    by_source = stats.get("by_source") or {}
    if by_source:
        detail = " — ".join(f"{source}: {count}" for source, count in by_source.items())
        console.print(f"- par source: {detail}")
