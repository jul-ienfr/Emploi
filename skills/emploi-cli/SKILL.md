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
- `emploi hellowork apply` est le flow réutilisable pour HelloWork : dry-run par défaut, soumission réelle uniquement avec `--submit --yes`, puis trace locale `application_submitted` et carte Deck `candidature-envoyee` si Kanban configuré.

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
emploi doctor --no-browser-probe  # skip le probe Managed Browser si non disponible
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
emploi ft apply 1 --partner hellowork
```

Règle : `emploi ft apply` ne soumet jamais automatiquement. Utiliser `--check` pour vérifier, `--draft` pour préparer, `--open` pour ouvrir l'offre France Travail dans le navigateur managé, ou `--partner NOM` pour ouvrir explicitement un partenaire externe détecté (ex. Meteojob/HelloWork) après handoff structuré. `--partner` ouvre seulement l'URL partenaire choisie via Managed Browser; il ne clique pas les liens de candidature finale et ne soumet rien. Si le partenaire demandé est absent ou sans URL exploitable, le CLI doit afficher une erreur propre `Error: ...`, sans `Invalid value`, sans traceback, sans ouverture externe et sans événement `partner_opened`. Voir `references/france-travail-partner-handoff-hardening.md`.

### HelloWork

```bash
emploi hellowork apply 1
emploi hellowork apply 1 --submit --yes
emploi hellowork apply 1 --submit --yes --ack-dissuasion
emploi hellowork apply 1 --submit --yes --kanban-stack candidature-envoyee
emploi hellowork apply 1 --submit --yes --no-kanban
```

Règle : `emploi hellowork apply` lance un dry-run par défaut. Il ouvre l'offre HelloWork, extrait le formulaire, vérifie prénom/nom/email/CV/bouton submit et enregistre seulement `hellowork_apply_dry_run`. `--submit --yes` est requis pour envoyer réellement; après confirmation HelloWork, le CLI crée une application locale `sent`, ajoute l'événement `application_submitted`, passe l'offre en `sent`, puis crée/réutilise une carte Deck dans la stack `candidature-envoyee` via l'endpoint Kanban par défaut. `--ack-dissuasion` permet de passer outre un avertissement compétences HelloWork (FIMO, FCO, etc.). Utiliser `--no-kanban` uniquement si Julien demande de ne pas toucher au board. Ne jamais logger `FunnelId`, cookies, credentials ou payloads complets. Voir `references/hellowork-application-flow.md`.

### Options opérateur globales

```bash
emploi option list
emploi option get france_travail.enabled
emploi option disable france_travail.enabled
emploi option enable france_travail.enabled
emploi option toggle drafts.enabled
```

Les options booléennes globales permettent de couper proprement une surface du workflow avant action externe ou écriture sensible : `managed_browser.enabled`, `france_travail.enabled`, `import.enabled`, `drafts.enabled`, `brief.enabled`, `scoring.enabled`. Elles sont actives par défaut. Respecter ces toggles avant de lancer recherche FT, imports, brouillons, brief ou recalcul de score.

### Profils de recherche sauvegardés

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

Chaque profil/option de recherche peut être activé ou désactivé par nom ou ID avec `enable`, `disable` ou `toggle`. `run --all` ne lance que les profils actifs; `list --enabled` filtre les profils désactivés.

Si Julien demande un rayon qui n'existe pas exactement côté France Travail, le CLI stocke le rayon demandé et envoie l'option France Travail supérieure. Exemple : `--radius 15` devient `rayon=20` dans l'URL, et `search-profile list` affiche `20 (demandé 15)` pour que l'analyse garde le plafond réel de 15 km.

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

### Candidatures, documents Nextcloud et pilotage opérateur
```bash
emploi apply 1
emploi application draft 1
emploi application list
emploi application followup 1 2026-05-04
emploi application followup-config enable --after 10d
emploi application followup-config disable
emploi application followup schedule 1 --after 7d --force
emploi application followup-sync-config enable
emploi application followup-sync --dry-run
emploi application due
emploi document-profile set poids-lourd --cv PATH --cover-letter PATH --default
emploi nextcloud-files set emploi --base-url URL --remote-root /Emploi --username-pass nextcloud/username --password-pass nextcloud/password --default
emploi nextcloud-tasks set emploi --base-url URL --calendar tasks --username-pass nextcloud/username --password-pass nextcloud/password --default
emploi kanban set chauffeur-pl --base-url URL --board-id BOARD_ID --username-pass nextcloud/username --password-pass nextcloud/password
emploi application export 1 --to-nextcloud --dry-run --include-documents --document-profile poids-lourd
emploi kanban card add-offer 1 --endpoint chauffeur-pl --stack-id STACK_ID --dry-run
emploi application pipeline 1 --files-endpoint emploi --kanban-endpoint chauffeur-pl --stack-id STACK_ID --dry-run
emploi brief
emploi next
emploi report
```

Nextcloud est intégré via APIs directes déterministes : Deck pour le kanban, WebDAV/Files pour les dossiers candidature, CalDAV/VTODO pour les tâches de relance. Les credentials restent dans `pass`; les fichiers de config locaux stockent seulement les noms d'entrées `pass`, jamais les secrets. Utiliser `--dry-run` avant tout upload WebDAV, création de carte Deck live ou synchronisation de tâche. `application pipeline` orchestre export Files + carte Deck et réutilise l'event `nextcloud_deck_card` existant sauf `--force-card`. Les relances automatiques restent sous contrôle opérateur : désactivées par défaut, activables avec `application followup-config enable --after 10d`, désactivables avec `disable`, et surchargées par run via `--schedule-followup/--no-schedule-followup` + `--followup-after`. La synchro Tasks est séparée et désactivée par défaut : `application followup-sync-config enable/disable`, `application followup-sync --dry-run`, ou pipeline avec `--sync-followup-task/--no-sync-followup-task`.

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
   - ouvrir manuellement avec `emploi ft apply <id> --open` si Julien veut finaliser côté France Travail ;
   - si le check expose un handoff partenaire, utiliser seulement sur demande explicite `emploi ft apply <id> --partner hellowork|meteojob` pour ouvrir ce partenaire choisi ;
   - pour HelloWork, utiliser `emploi hellowork apply <id>` en dry-run puis `emploi hellowork apply <id> --submit --yes` seulement si Julien demande explicitement de postuler ;
   - après `--submit --yes`, vérifier la confirmation, la trace `application_submitted`, le statut `sent` et la carte Deck `candidature-envoyee` ;
   - ne jamais cliquer/soumettre automatiquement une candidature réelle sans validation explicite.
5. Pour modifier le CLI : suivre TDD strict, puis :
   ```bash
   python3 -m pytest tests -q
   python3 -m compileall emploi
   ```

## Variables d'environnement

```bash
export EMPLOI_DB=/chemin/vers/emploi.sqlite
export EMPLOI_MANAGED_BROWSER_COMMAND="node /home/jul/tools/camofox-browser/scripts/managed-browser.js"
```

`emploi` parle le protocole du wrapper Camofox Managed Browser : `profile status`, `flow run open_url`, `snapshot`, `storage checkpoint`. `EMPLOI_MANAGED_BROWSER_COMMAND` peut donc contenir une commande avec arguments, pas seulement un binaire unique.

## Pièges connus

- Si `emploi --version` échoue avec “Missing command”, vérifier que le callback Typer utilise `invoke_without_command=True`.
- Une installation editable peut créer `emploi.egg-info/`; ne pas le committer sauf décision explicite.
- Si Managed Browser est absent, ne pas remplacer par du scraping France Travail improvisé : signaler l'état dégradé et configurer `EMPLOI_MANAGED_BROWSER_COMMAND`.
- Ne pas utiliser une base de production pour les tests : définir `EMPLOI_DB` vers un fichier temporaire.
