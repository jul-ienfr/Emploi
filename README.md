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

Initialisation et offres locales :

```bash
emploi init
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

Profils de recherche sauvegardés :

```bash
emploi search-profile add support-annecy --query "technicien support" --where "Annecy" --radius 20 --contract CDI
emploi search-profile list
emploi search-profile run support-annecy
emploi search-profile run --all
```

Candidatures et pilotage opérateur :

```bash
emploi apply 1
emploi application list
emploi next
emploi report
```

`emploi next` propose les prochaines actions utiles à partir des offres France Travail actives à fort score et des candidatures en brouillon/envoyées. `emploi report` inclut le résumé local historique plus des compteurs France Travail/browser-backed (offres FT, offres FT actives, brouillons, candidatures envoyées).

## Notes

- `emploi ft apply` ne soumet jamais automatiquement une candidature : il vérifie, prépare un brouillon local ou ouvre l'offre dans le navigateur managé.
- Les offres France Travail importées gardent l'URL navigateur, l'état actif/inactif, le dernier snapshot brut et les événements d'audit locaux.
