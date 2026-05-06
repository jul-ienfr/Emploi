# Plan — intégration Nextcloud API pour le CLI Emploi

Objectif : utiliser Nextcloud comme hub local de recherche d'emploi sans MCP, via APIs déterministes et testables.

## Décision

- Backend principal : API Nextcloud directe.
- Deck reste la source du pipeline candidature.
- WebDAV sert au stockage des preuves/documents.
- CalDAV/VTODO peut servir aux relances, rendez-vous et tâches.
- Secrets : uniquement des références `pass`, jamais de valeur en clair dans Git ni dans `~/.config/emploi/*.json`.

## Phase 1 — Deck API renforcée

État actuel : `emploi kanban set/show/list` sait enregistrer des boards Deck par profil métier.

À ajouter :

1. Client Deck injecté/testable.
2. Commandes :
   - `emploi kanban stacks [PROFILE] --json`
   - `emploi kanban card add PROFILE --stack NAME --title TITLE [--description TEXT]`
   - `emploi kanban offer push OFFER_ID --profile PROFILE [--stack "À Postuler"]`
   - `emploi kanban move CARD_ID --profile PROFILE --to STACK`
3. Mapping local offre ↔ carte Deck dans SQLite pour éviter les doublons.
4. Tests avec faux client HTTP, pas d'appel réseau réel.

## Phase 2 — Files / WebDAV

But : créer un dossier candidature propre dans Nextcloud et y déposer les documents générés.

Config locale prévue : `~/.config/emploi/nextcloud_files.json`

Champs :

```json
{
  "default": "emploi",
  "endpoints": {
    "emploi": {
      "base_url": "https://nextcloud.example.test",
      "remote_root": "/Emploi",
      "username_pass": "nextcloud/username",
      "password_pass": "nextcloud/password"
    }
  }
}
```

URLs dérivées :

- WebDAV root : `{base_url}/remote.php/dav/files/{username}/{remote_root}`

Commandes :

- `emploi nextcloud-files set NAME --base-url URL --remote-root /Emploi --username-pass ... --password-pass ... [--default]`
- `emploi nextcloud-files show [NAME] --json`
- `emploi nextcloud-files list --json`
- `emploi application export OFFER_ID --to-nextcloud`

Comportement `application export` :

1. créer `/Emploi/Candidatures/<source>-<offer_id>-<slug>/` ;
2. écrire `offre.md` avec titre, entreprise, lieu, URL, description ;
3. copier/générer CV + LM quand disponibles ;
4. enregistrer le lien dossier dans la DB ;
5. optionnel : ajouter le lien dossier à la carte Deck.

## Phase 3 — Calendar / Tasks via CalDAV

But : relances et échéances.

Commandes :

- `emploi followup schedule OFFER_ID --in 7d`
- `emploi interview add OFFER_ID "YYYY-MM-DD HH:MM" --location TEXT`
- `emploi task add TEXT --due DATE`

Stockage : références locales + UID CalDAV pour idempotence.

## Phase 4 — Contacts / Notes

À faire seulement quand le pipeline Deck+Files+Calendar est stable.

- Contacts recruteurs/entreprises via CardDAV.
- Notes Markdown dans `/Emploi/Journal.md` et `/Emploi/Entretiens/`.
- Recherche Nextcloud plus tard si indexation utile.

## Priorité recommandée

1. Deck `stacks/card add/offer push`.
2. Config Files/WebDAV + export dossier candidature.
3. Relances Calendar/Tasks.

## Critères de qualité

- TDD obligatoire pour chaque commande.
- Aucune valeur de secret en stdout JSON.
- Idempotence : réexécuter une commande ne doit pas créer de doublons quand un mapping local existe.
- `--json` parseable et silencieux.
- Pas d'appel réseau dans les tests unitaires.
