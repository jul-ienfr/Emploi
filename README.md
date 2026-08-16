# Emploi

CLI Python personnel pour chercher, scorer et suivre les offres d'emploi.

## Installation dev

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pip install ruff mypy pre-commit  # optionnel
pre-commit install                # active les hooks ruff avant chaque commit
```

Commandes utiles :

```bash
make test          # lance les tests
make lint          # vérifie le code avec ruff
make format        # formate + auto-fix
make check         # lint + test
```

## Architecture V1 — France Travail via Managed Browser

`emploi` conserve les données, le scoring, les brouillons et le reporting en local dans SQLite. L'intégration France Travail passe en priorité par un **Managed Browser** externe : la CLI `emploi` orchestre des commandes navigateur (`status`, `open`, `snapshot`, `checkpoint`), extrait les offres depuis les snapshots retournés, puis les stocke localement avec leurs métadonnées France Travail.

Cette séparation évite de coupler le cœur local à un scraper direct : le navigateur managé garde la session utilisateur, ouvre les pages France Travail et renvoie des payloads JSON/HTML/texte exploitables par `emploi`.

## Structure du code

```
emploi/
├── cli/                    # CLI Typer, découpée par groupe de commandes
│   ├── __init__.py         # App, helpers partagés, callback main
│   ├── offer.py            # offer add/list/show/score/status/reject/archive
│   ├── application.py      # application draft/list/status/export/pipeline/followup
│   ├── browser.py          # browser status/open/snapshot/checkpoint/smoke
│   ├── ft.py               # ft search/refresh/apply/smoke
│   ├── hellowork.py        # hellowork apply/search
│   ├── search_profile.py   # search-profile add/list/enable/disable/run/watch
│   ├── auto_apply.py       # auto-apply run
│   ├── import_.py          # import offers
│   ├── option.py           # option list/get/enable/disable/toggle
│   ├── document_profile.py # document-profile set/default/list/status
│   ├── kanban.py           # kanban set/show/list + card add-offer
│   ├── nextcloud.py        # nextcloud-files + nextcloud-tasks set/show/list
│   ├── doctor.py           # doctor
│   └── report.py           # report/brief/next/apply
├── france_travail/         # Intégration France Travail (browser-mediated)
├── browser/                # Client HTTP Managed Browser
├── db.py                   # SQLite data layer
├── config.py               # Configuration (~/.config/emploi/*.json)
├── config_registry.py      # Registry générique pour endpoints JSON
├── retry.py                # Retry avec backoff exponentiel
├── logging.py              # Logging structuré (RotatingFileHandler)
├── utils.py                # Utilitaires partagés (_pass_show, _safe_slug, etc.)
├── scoring.py              # Moteur de scoring des offres
├── applications.py         # Création de brouillons de candidature
├── hellowork.py            # Flux apply HelloWork
├── hellowork_search.py     # Recherche HelloWork (HTTP scraping)
├── auto_apply.py           # Sélection/candidature automatique bornée
├── daemon.py               # Boucle de veille automatique
├── brief.py                # Brief quotidien
├── doctor.py               # Diagnostic de santé
├── importers.py            # Import multi-sources (JSON/CSV)
├── nextcloud_deck.py       # Client Nextcloud Deck
├── nextcloud_files.py      # Client Nextcloud WebDAV
└── nextcloud_tasks.py      # Client Nextcloud CalDAV
```

## Configuration

Base SQLite par défaut :

```txt
~/.local/share/emploi/emploi.sqlite
```

Variables utiles :

```bash
# Choisir une base locale différente
export EMPLOI_DB=/tmp/emploi.sqlite

# Commande externe utilisée par ManagedBrowserClient.
# Par défaut: managed-browser. En local, le wrapper Python est préféré :
export MANAGED_BROWSER_URL="http://127.0.0.1:9377"
export EMPLOI_MANAGED_BROWSER_COMMAND="managed-browser"
```

La commande Managed Browser doit accepter le protocole du wrapper Camofox : `profile status`, `navigate` pour les ouvertures navigateur explicites, `lifecycle open` pour les flux France Travail, `snapshot`, `console eval`, `storage checkpoint`, et renvoyer du JSON sur stdout. Les flux France Travail utilisent `lifecycle open` pour réutiliser proprement la session et éviter les conflits d'onglet/HTTP 409.

## Commandes V1

Initialisation, diagnostic et offres locales :

```bash
emploi init
emploi doctor
emploi doctor --json
emploi doctor --no-browser-probe  # sans probe Managed Browser
emploi offer add --title "Technicien support" --company "Entreprise X" --location "Bonneville"
emploi offer list
emploi offer show 1
emploi offer score 1
emploi offer score --all
emploi offer status 1 interesting
emploi offer reject 1 --reason "Permis obligatoire"
emploi offer archive 1
```

Managed Browser :

```bash
emploi browser status
emploi browser open "https://candidat.francetravail.fr/offres/recherche"
emploi browser snapshot --label ft-search
emploi browser checkpoint login-ft
```

France Travail via Managed Browser :

```bash
emploi ft search "technicien support" --location "Annecy"
emploi ft refresh 1
emploi ft apply 1 --check
emploi ft apply 1 --draft
emploi ft apply 1 --open
emploi ft apply 1 --partner hellowork
```

`emploi ft apply` reste assisté : `--check` vérifie sans soumettre, `--open` ouvre l'offre France Travail, et `--partner NOM` ouvre explicitement un handoff externe détecté après choix opérateur. Même avec `--partner`, le CLI n'effectue aucun clic final ni soumission de candidature.

HelloWork via Managed Browser :

```bash
emploi hellowork apply 1
emploi hellowork apply 1 --submit --yes
emploi hellowork apply 1 --submit --yes --kanban-stack candidature-envoyee
emploi hellowork apply 1 --submit --yes --no-kanban
emploi identity set --firstname "Julien" --lastname "Frendo-Rossi" --email "j@mail.fr"
emploi hellowork apply 1 --cv /chemin/vers/CV.pdf
emploi hellowork search "chauffeur PL" --location "Cluses" --where "Bogève" --radius 20
```

`emploi hellowork search` recherche des offres HelloWork (scraping HTTP via le Managed Browser) avec filtres `--location`, `--contract`, `--where` (lieu d'origine) et `--radius` (rayon en km depuis `--where`, via `within_requested_radius`).

`emploi hellowork apply` est en dry-run par défaut : il ouvre l'offre HelloWork, charge le formulaire, vérifie les champs requis et le CV, puis s'arrête sans POST final. Depuis août 2026, HelloWork ne pré-remplit plus l'identité : le CLI pré-remplit Firstname/Lastname/Email depuis `~/.config/emploi/identity.json` (`emploi identity set`) et uploads le CV automatiquement (`--cv`, sinon le profil documents par défaut) via l'endpoint `uploadcv` (multipart, en-têtes Turbo-Frame requis) en injectant le `JweHashResume` dans le formulaire. Le POST réel n'est exécuté qu'avec `--submit --yes`; après confirmation HelloWork, le CLI enregistre `application_submitted`, passe la candidature locale en `sent`, et crée/réutilise une carte Deck dans la stack Kanban `candidature-envoyee` quand l'endpoint kanban est configuré. Les secrets et champs dynamiques sensibles comme `FunnelId` ne sont pas loggés. `--ack-dissuasion` permet de confirmer l'envoi malgré un avertissement compétences HelloWork.

Imports multi-sources sans scraping direct :

```bash
emploi import offers ./offers.json --source indeed
emploi import offers ./offers.csv --source linkedin --format csv
emploi import offers ./wttj.json --source welcome-to-the-jungle --json
```

`emploi import offers` charge uniquement des fichiers locaux JSON/CSV. Les champs reconnus restent volontairement simples : `title`, `company`, `location`, `url`, `source`, `description`, `salary`, `remote`, `contract_type`, `notes`, `external_id`. Le JSON peut être une liste d'offres ou un objet `{ "offers": [...] }`; le CSV doit contenir une ligne d'en-têtes. L'import met à jour les doublons via `(external_source, external_id)` quand `external_id` est présent, sinon via l'URL.

Sources/adapters prévus pour les évolutions futures :

- `indeed` — import d'exports/fichiers préparés Indeed, sans scraping direct.
- `welcome-to-the-jungle` — import d'exports/fichiers préparés Welcome to the Jungle.
- `linkedin` — import d'exports/fichiers préparés LinkedIn.
- `local-site` — import depuis sites locaux/régionaux ou pages entreprises converties en JSON/CSV.
- `remote-freelance` — import depuis sources remote/freelance converties en JSON/CSV.

Options opérateur globales :

```bash
emploi option list
emploi option get france_travail.enabled
emploi option disable france_travail.enabled
emploi option enable france_travail.enabled
emploi option toggle drafts.enabled
```

Les options disponibles sont `managed_browser.enabled`, `france_travail.enabled`, `import.enabled`, `drafts.enabled`, `brief.enabled` et `scoring.enabled`. Les valeurs par défaut sont actives pour rester rétrocompatibles. Quand une option est désactivée, la commande concernée s'arrête proprement avant l'action externe/écriture sensible; les sorties `--json` restent parseables.

Profils de recherche sauvegardés :

```bash
emploi search-profile add support-annecy --query "technicien support" --where "Annecy" --radius 15 --contract CDI
emploi search-profile add test-remote --query "python remote" --disabled
emploi search-profile list
emploi search-profile list --enabled
emploi search-profile enable support-annecy
emploi search-profile disable support-annecy
emploi search-profile toggle support-annecy
emploi search-profile run support-annecy
emploi search-profile run --all
```

Chaque profil sauvegardé a un état actif/inactif. `enable`, `disable` et `toggle` permettent d'activer ou désactiver chaque option/profil existant par nom ou ID. `emploi search-profile run --all` exécute uniquement les profils actifs; `list --enabled` masque les profils désactivés.

Pour les rayons France Travail non proposés exactement, le CLI conserve le rayon demandé et utilise l'option France Travail supérieure. Exemple: `--radius 15` est envoyé à France Travail avec `rayon=20`, puis `search-profile list` affiche `20 (demandé 15)` pour rappeler que l'analyse doit filtrer à 15 km effectifs.

Candidatures et pilotage opérateur :

```bash
emploi daily                 # rituel quotidien : doctor → scan → brief → next
emploi daily --no-run        # idem sans scanner les profils
emploi apply 1
emploi application draft 1
emploi application list
emploi application followup 1 2026-05-04
emploi next
emploi brief
emploi brief --json
emploi report
```

`emploi daily` est le point d'entrée quotidien : diagnostic (`doctor`), scan des profils actifs (France Travail + HelloWork selon la source du profil), brief et prochaines actions — en une commande. `--no-run` saute le scan (brief sur les données existantes), `--no-probe-browser` saute le probe du Managed Browser, `--today YYYY-MM-DD` permet de rejouer une journée.

`emploi next` propose les prochaines actions utiles à partir des offres actives à fort score et des candidatures en brouillon/envoyées. `emploi brief` est le point quotidien recommandé : meilleures offres (toutes sources, score ≥ 70), actions prioritaires, relances dues, candidatures envoyées devenues stale, blockers (Managed Browser/profils) et stats 7 jours **par source** (France Travail, HelloWork, APEC, CH…). `emploi brief --json` ne sort que du JSON parseable. `emploi report` conserve le résumé local historique plus des compteurs browser-backed.

Recherche multi-sources :

```bash
emploi search-all "chauffeur PL" --country FR --max 20 --json
emploi search-all "chauffeur PL" --country CH --export-csv offres_ch.csv
```

`emploi search-all` interroge les sources HTTP directes (FR : apec, monster, cadremploi ; CH : okjob, jobup, jobs_ch, comparis), déduplique et enregistre localement.

Suivi Nextcloud — kanban et tâches :

```bash
emploi kanban stacks --json                       # liste live des stacks du board
emploi kanban card add "Rappel relance" --stack envoyees --dry-run
emploi kanban move CARD_ID --stack relance --dry-run
emploi application interview add 1 --date "2026-08-20 14:30" --location "Agen" --dry-run
emploi application task add "Préparer le dossier" --due 2026-09-01 --dry-run
```

`kanban stacks` lit les colonnes du board via l'API Deck ; `kanban card add` crée une carte manuelle sans offre liée ; `kanban move` déplace une carte existante vers une autre stack (ex. `candidature-envoyee` → `relance`). `application interview add` et `application task add` créent des VTODO CalDAV (entretien avec heure, tâche générique) — UID déterministes (idempotence), bornés par l'option `followups.enabled`.

## Workflow quotidien Julien

```bash
emploi doctor --json
emploi search-profile install-julien-defaults
emploi search-profile run --all
emploi brief
emploi next
emploi ft apply <offer-id> --check
emploi application draft <offer-id>
emploi ft apply <offer-id> --open
```

1. Vérifier d'abord `emploi doctor --json`; si Managed Browser est indisponible, corriger `EMPLOI_MANAGED_BROWSER_COMMAND` ou utiliser les données locales/imports sans scraping direct.
2. Installer une fois les profils Julien par défaut, puis lancer les profils actifs pour rafraîchir France Travail.
3. Lire `emploi brief` pour décider la journée: meilleures offres, relances et blockers; utiliser `emploi next` pour la liste d'actions détaillée.
4. Pour candidater, rester en mode assisté: `--check`, brouillon local, ouverture navigateur; aucune soumission automatique.

## Daemon de veille multi-sources

```bash
emploi search-profile watch --interval 30      # boucle toutes les 30 min
emploi search-profile watch --interval 30 --once   # un seul cycle
```

À chaque cycle, le daemon exécute les profils actifs (France Travail + HelloWork, moteur résolu depuis la colonne `source` du profil) puis les sources suisses (CH, 10 par source) via `sources/aggregator`. Arrêt propre sur Ctrl+C (double signal = arrêt immédiat).

Alertes (config par variables d'environnement) :

- `EMPLOI_ALERT_WEBHOOK_URL` — POST JSON sur erreur de cycle (Slack, Telegram…) ;
- `EMPLOI_ALERT_EMAIL_TO` / `EMPLOI_ALERT_EMAIL_FROM` — email via sendmail ;
- `EMPLOI_ALERT_HIGH_SCORE=0` — désactive l'alerte « nouvelle offre à fort potentiel » (score ≥ 70, active par défaut).

## Dashboard

```bash
emploi dashboard --host 0.0.0.0 --port 8050
```

Dashboard Flask (port par défaut 8050) avec 88 routes et 11 templates Jinja (offres, candidatures, stats, carte, comparaison, entreprise, profils, actions, PWA). Les routes vivent dans des blueprints (`emploi/dashboard_app/` : `pages`, `api_offers`, `api_misc` + helpers dans `common.py`) ; `dashboard.py` n'est plus qu'une factory. Auth par variables d'environnement : `EMPLOI_DASHBOARD_API_KEY` (clé API, comparaison HMAC) et `EMPLOI_DASHBOARD_AUTH` (mot de passe), avec rate limit 100 req/min (`dashboard_auth.py`).

## Skill Hermes

Le dépôt embarque une skill Hermes dédiée : `skills/emploi-cli/SKILL.md`.

Elle décrit le workflow agent pour utiliser `emploi` correctement : diagnostic `emploi doctor --json`, recherches France Travail via Managed Browser, profils sauvegardés, commandes de suivi et règle de sécurité sur les candidatures assistées.

## Notes

- `emploi ft apply` ne soumet jamais automatiquement une candidature : il vérifie, prépare un brouillon local ou ouvre l'offre dans le navigateur managé.
- Les offres France Travail importées gardent l'URL navigateur, l'état actif/inactif, le dernier snapshot brut et les événements d'audit locaux.
- Retry automatique sur erreurs transitoires (Managed Browser, Nextcloud) via `retry.py`.
- Logging structuré vers `~/.local/share/emploi/emploi.log` (flag `--verbose` pour debug).
- Linting automatique via pre-commit (ruff) et CI GitHub Actions.
