# CLAUDE.md — Projet Emploi

CLI Python personnel d'automatisation de recherche d'emploi (France Travail, HelloWork, sources FR/CH, scoring, candidatures assistées, Nextcloud, dashboard).

## Commandes de base

```bash
make check        # ruff + pytest (validation avant commit)
make lint         # ruff check
make format       # ruff --fix + format
make test         # pytest
make cov          # pytest --cov (seuil 70 %)
```

Tout commit passe par pre-commit (ruff + ruff-format). Ne jamais commiter si `make check` échoue.

## Architecture (vue rapide)

```
emploi/
├── cli/                    # CLI Typer, un module par groupe de commandes
├── france_travail/         # FT via Managed Browser (flows, extractors, distance, api_client)
├── sources/                # scrapers HTTP (apec, monster, cadremploi, okjob, jobup, jobs_ch, comparis) + aggregator
├── browser/                # client HTTP du Managed Browser externe (Camoufox, http://127.0.0.1:9377)
├── db.py                   # SQLite (offers, applications, events, saved_searches…)
├── migrations.py           # migrations idempotentes
├── scoring.py              # score 50 ± règles déterministes
├── applications.py         # brouillons de candidature
├── auto_apply.py           # auto-apply borné (quota/stratégie) — NE SOUMET JAMAIS
├── hellowork.py            # flux apply HelloWork (dry-run par défaut)
├── hellowork_search.py     # recherche HelloWork
├── daemon.py               # boucle de veille multi-sources (FT + HelloWork + CH)
├── monitoring.py           # alertes webhook/email
├── doctor.py               # rapport de santé (ok/degraded)
├── brief.py                # brief quotidien
├── dashboard.py            # dashboard Flask (monolithe, 88 routes) + _dashboard_ui/
├── nextcloud_deck.py       # kanban Deck
├── nextcloud_files.py      # WebDAV (dossiers candidature)
└── nextcloud_tasks.py      # CalDAV VTODO (relances)
scripts/ft_diagnostic.py    # outil manuel live (pas un test pytest)
skills/emploi-cli/          # skill Hermès
docs/plans/                 # plans d'évolution (statuts à tenir à jour)
```

## Règles de sécurité candidature (IMPORTANT)

- **Aucune soumission automatique** : `ft apply` ne soumet jamais (--check/--draft/--open/--partner), `hellowork apply` est dry-run par défaut et ne POST qu'avec `--submit --yes`.
- `auto-apply run` ne crée que des brouillons locaux.
- Les secrets ne vont jamais dans le code : endpoints config via références `*_pass` vers le gestionnaire `pass`; auth dashboard par env (`EMPLOI_DASHBOARD_API_KEY`, `EMPLOI_DASHBOARD_AUTH`).
- Ne pas logger FunnelId/email (le code les masque déjà — préserver ce comportement).

## Chemins

- Base SQLite canonique : `~/.local/share/emploi/emploi.sqlite` (une seule base ! `doctor` signale les bases résiduelles).
- Config : `~/.config/emploi/*.json` (accounts, document_profiles, kanban_endpoints, nextcloud_files, nextcloud_tasks).
- Brouillons : `~/.local/share/emploi/drafts/` ; logs : `~/.local/share/emploi/emploi.log`.
- Managed Browser externe : `EMPLOI_MANAGED_BROWSER_URL` (défaut http://127.0.0.1:9377), sessions France Travail/HelloWork loggées.
- En test : `EMPLOI_DB` pointe vers une base jetable — ne jamais écrire dans la vraie base depuis un test.

## Workflow quotidien (1 commande)

```bash
emploi daily            # doctor → search-profile run --all → brief → next
```

Puis candidature assistée : `emploi ft apply <id> --check`, `emploi application draft <id>`, `emploi hellowork apply <id> --submit --yes` (dry-run sinon).

## Rituel de fin de session

1. `make check` vert ;
2. `git status` propre (ou commits logiques) ;
3. `docs/plans/` : cocher les statuts des phases faites ;
4. README + skill `skills/emploi-cli/` synchronisés si une commande a changé.
