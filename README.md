# Emploi

CLI Python personnel pour chercher, scorer et suivre les offres d'emploi.

## Installation dev

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Architecture V1 — France Travail via Managed Browser

`emploi` conserve les données, le scoring, les brouillons et le reporting en local dans SQLite. L'intégration France Travail passe en priorité par un **Managed Browser** externe : la CLI `emploi` orchestre des commandes navigateur (`status`, `open`, `snapshot`, `checkpoint`), extrait les offres depuis les snapshots retournés, puis les stocke localement avec leurs métadonnées France Travail.

Cette séparation évite de coupler le cœur local à un scraper direct : le navigateur managé garde la session utilisateur, ouvre les pages France Travail et renvoie des payloads JSON/HTML/texte exploitables par `emploi`.

## Configuration

Base SQLite par défaut :

```txt
~/.local/share/emploi/emploi.sqlite
```

Variables utiles :

```bash
# Choisir une base locale différente
export EMPLOI_DB=/tmp/emploi.sqlite

# Commande externe utilisée par ManagedBrowserClient
# Par défaut: managed-browser
export EMPLOI_MANAGED_BROWSER_COMMAND=managed-browser
```

La commande Managed Browser doit accepter les sous-commandes utilisées par `emploi`, par exemple `status`, `open`, `snapshot`, `checkpoint`, et renvoyer du JSON sur stdout.

## Commandes V1

Initialisation, diagnostic et offres locales :

```bash
emploi init
emploi doctor
emploi doctor --json
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
```

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

Profils de recherche sauvegardés :

```bash
emploi search-profile add support-annecy --query "technicien support" --where "Annecy" --radius 20 --contract CDI
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

Candidatures et pilotage opérateur :

```bash
emploi apply 1
emploi application draft 1
emploi application list
emploi application followup 1 2026-05-04
emploi next
emploi brief
emploi brief --json
emploi report
```

`emploi next` propose les prochaines actions utiles à partir des offres France Travail actives à fort score et des candidatures en brouillon/envoyées. `emploi brief` est le point quotidien recommandé : meilleures offres, actions prioritaires, relances dues, candidatures envoyées devenues stale, blockers (Managed Browser/profils) et stats 7 jours. `emploi brief --json` ne sort que du JSON parseable. `emploi report` conserve le résumé local historique plus des compteurs France Travail/browser-backed (offres FT, offres FT actives, brouillons, candidatures envoyées).

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

## Skill Hermes

Le dépôt embarque une skill Hermes dédiée : `skills/emploi-cli/SKILL.md`.

Elle décrit le workflow agent pour utiliser `emploi` correctement : diagnostic `emploi doctor --json`, recherches France Travail via Managed Browser, profils sauvegardés, commandes de suivi et règle de sécurité sur les candidatures assistées.

## Notes

- `emploi ft apply` ne soumet jamais automatiquement une candidature : il vérifie, prépare un brouillon local ou ouvre l'offre dans le navigateur managé.
- Les offres France Travail importées gardent l'URL navigateur, l'état actif/inactif, le dernier snapshot brut et les événements d'audit locaux.
