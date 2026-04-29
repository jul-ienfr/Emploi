---
name: emploi-cli
description: Utiliser le CLI personnel `emploi` pour chercher, scorer et suivre les offres d'emploi de Julien, avec France Travail via Managed Browser et candidatures assistées.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [emploi, france-travail, cli, sqlite, managed-browser, candidatures]
---

# Emploi CLI — Skill Hermes

Utilise cette skill quand Julien demande de travailler sur sa recherche d'emploi avec le dépôt/CLI `emploi` : recherche d'offres, scoring, suivi de candidatures, profils France Travail, rapports opérateur ou diagnostic local.

## Principes importants

- Le dépôt est généralement dans `/home/jul/Emploi`.
- La commande principale est `emploi` après installation editable, ou `python3 -m emploi.cli` depuis le dépôt.
- Les données locales sont dans SQLite, par défaut `~/.local/share/emploi/emploi.sqlite`.
- La variable `EMPLOI_DB` permet de viser une autre base.
- France Travail passe par Managed Browser, pas par scraping direct côté agent.
- `emploi ft apply` ne soumet jamais automatiquement une candidature : il vérifie, prépare un brouillon local ou ouvre l'offre. Toute soumission réelle doit rester validée humainement par Julien.

## Préflight recommandé

Depuis le dépôt :

```bash
cd /home/jul/Emploi
python3 -m emploi.cli doctor --json
```

Si le script console est installé :

```bash
emploi doctor --json
```

Le JSON doit être parseable. Interprétation rapide :

- `status: ok` : cœur local + Managed Browser OK.
- `status: degraded` avec `managed_browser.status: missing` : le local SQLite marche, mais l'orchestrateur navigateur n'est pas disponible/configuré.
- `database.status != ok` : régler d'abord le chemin `EMPLOI_DB` ou les permissions SQLite.

## Commandes utiles

### Initialiser / diagnostiquer

```bash
emploi init
emploi doctor
emploi doctor --json
```

### Ajouter et consulter des offres locales

```bash
emploi offer add --title "Technicien support" --company "Entreprise X" --location "Bonneville"
emploi offer list
emploi offer show 1
emploi offer score 1
emploi offer score --all
emploi offer status 1 interesting
emploi offer reject 1 --reason "Permis obligatoire"
emploi offer archive 1
```

### Managed Browser

```bash
emploi browser status
emploi browser open "https://candidat.francetravail.fr/offres/recherche"
emploi browser snapshot --label ft-search
emploi browser checkpoint login-ft
```

### France Travail

```bash
emploi ft search "technicien support" --location "Annecy"
emploi ft refresh 1
emploi ft apply 1 --check
emploi ft apply 1 --draft
emploi ft apply 1 --open
```

Règle : `emploi ft apply` ne soumet jamais automatiquement. Utiliser `--check` pour vérifier, `--draft` pour préparer, `--open` pour ouvrir dans le navigateur managé.

### Profils de recherche sauvegardés

```bash
emploi search-profile add support-annecy --query "technicien support" --where "Annecy" --radius 20 --contract CDI
emploi search-profile list
emploi search-profile run support-annecy
emploi search-profile run --all
```

Pour une routine de recherche, commencer par :

```bash
emploi doctor --json
emploi search-profile install-julien-defaults
emploi search-profile list
emploi search-profile run --all
emploi brief
emploi brief --json
emploi next
emploi report
```

`emploi brief` est le briefing quotidien Julien : meilleures offres, actions prioritaires, relances dues, candidatures envoyées sans contact récent, blockers et stats 7 jours. Utiliser `emploi brief --json` quand un agent doit parser le résultat : la sortie doit rester du JSON pur, sans bruit Rich.

### Candidatures et pilotage opérateur

```bash
emploi apply 1
emploi application draft 1
emploi application list
emploi application followup 1 2026-05-04
emploi brief
emploi next
emploi report
```

## Workflow agent recommandé

1. Vérifier le dépôt et l'état Git :
   ```bash
   cd /home/jul/Emploi
   git status -sb
   ```
2. Lancer le diagnostic :
   ```bash
   python3 -m emploi.cli doctor --json
   ```
3. Si Julien demande une recherche France Travail :
   - vérifier que Managed Browser est disponible ;
   - installer les profils par défaut si besoin avec `emploi search-profile install-julien-defaults` ;
   - exécuter `emploi search-profile run --all` ou `emploi ft search ...` ;
   - finir par `emploi brief`, puis `emploi next` si des actions détaillées sont nécessaires.
4. Pour une candidature assistée :
   - utiliser d'abord `emploi ft apply <id> --check` ;
   - puis éventuellement `emploi application draft <id>` ou `emploi ft apply <id> --draft` ;
   - ouvrir manuellement avec `emploi ft apply <id> --open` si Julien veut finaliser ;
   - ne jamais cliquer/soumettre automatiquement une candidature réelle sans validation explicite.
5. Pour modifier le CLI : suivre TDD strict, puis :
   ```bash
   python3 -m pytest tests -q
   python3 -m compileall emploi
   ```

## Variables d'environnement

```bash
export EMPLOI_DB=/chemin/vers/emploi.sqlite
export EMPLOI_MANAGED_BROWSER_COMMAND=managed-browser
```

## Pièges connus

- Si `emploi --version` échoue avec “Missing command”, vérifier que le callback Typer utilise `invoke_without_command=True`.
- Une installation editable peut créer `emploi.egg-info/`; ne pas le committer sauf décision explicite.
- Si Managed Browser est absent, ne pas remplacer par du scraping France Travail improvisé : signaler l'état dégradé et configurer `EMPLOI_MANAGED_BROWSER_COMMAND`.
- Ne pas utiliser une base de production pour les tests : définir `EMPLOI_DB` vers un fichier temporaire.
