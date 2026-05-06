# Exploration HelloWork — flow candidature

Date: 2026-05-06
Offre inspectée: HelloWork `78282309` — Chauffeur Poids Lourd H/F, Slash Intérim, Bons-en-Chablais.

## Objectif

Comprendre le tunnel HelloWork pour préparer une automatisation assistée depuis le projet `Emploi`, sans soumission automatique non maîtrisée.

## Ce qui a été observé

### Ouverture

- L’URL HelloWork issue du handoff France Travail s’ouvre via Managed Browser avec le profil `emploi-candidature` rattaché au site `france-travail`.
- Tenter `--site hellowork --profile emploi-candidature` échoue avec `site_mismatch`; le profil appartient à `france-travail`.
- L’URL inspectée est du type:
  `https://www.hellowork.com/fr-fr/emplois/78282309.html?...#postuler`

### État connecté

La page affiche un état connecté HelloWork avec le profil utilisateur visible. Les sorties doivent toujours masquer email et données sensibles.

### Étape initiale

La page offre affiche:

- bouton/lien `Postuler` dans l’onglet de l’offre ;
- section `Envoyez votre candidature dès maintenant !` ;
- forms annexes OneTap / alerte / bookmark qui ne sont pas le formulaire de candidature principal.

Les resources observées incluent notamment:

- `/fr-fr/compte/accountdata`
- `/fr-fr/candidat/onetapturbocustom`
- `/fr-fr/candidat/customonetapframeview`
- `/fr-fr/offres/getinitialformframeview?offerId=78282309&ts=...`
- `/fr-fr/GetUploaderCvFrameView?formId=offer-detail-main-step-form&isRequired=true&turboFrameId=funnel-resume-uploader-frame`

## Formulaire principal

L’endpoint déterministe utile est:

```http
GET /fr-fr/offres/getinitialformframeview?offerId=78282309&ts=<timestamp>
Headers:
  Turbo-Frame: offer-detail-main-step-frame
  X-Requested-With: XMLHttpRequest
Credentials: include
```

Il renvoie un HTML `turbo-frame` contenant le formulaire principal:

- `id="offer-detail-main-step-form"`
- `method="post"`
- `action="/fr-fr/offres/postcandidateinformationfromstepframeview"`
- variante produit observée: `FORM_DO_CLASSIQUE_ATS_CLIENT`

Champs observés dans le formulaire principal:

| Champ | Type | Requis | Note |
|---|---:|---:|---|
| `FunnelId` | hidden | non | token/tunnel dynamique, ne jamais logger en clair |
| `Firstname` | text | oui | pré-rempli |
| `LastName` | text | oui | pré-rempli |
| `Email` | email | oui | pré-rempli, masquer dans logs |
| `MotivationLetter` | textarea | non | lettre/message optionnel |
| `cover-letter-collapse-funnel` | checkbox UI | non | contrôle d’affichage, pas utile dans payload final observé |
| `emailReadonly` | checkbox UI | non | contrôle UI sans nom exploitable |

Payload construit côté client avant soumission observé, sans envoi:

```text
FunnelId=[FUNNEL]
Firstname=Julien
LastName=Frendo-Rossi
Email=[EMAIL]
MotivationLetter=<texte optionnel>
```

Le bouton final visible est:

- `button[data-cy="submitButton"]`
- `type="submit"`
- texte `Postuler`
- `form="offer-detail-main-step-form"`

## CV / upload

L’endpoint CV/uploader a été repéré:

```http
GET /fr-fr/GetUploaderCvFrameView?formId=offer-detail-main-step-form&isRequired=true&turboFrameId=funnel-resume-uploader-frame
```

À explorer proprement sur une session stable: champs d’upload, CV déjà disponible, requirement réel du fichier.

## Blocages / fragilité

- Les tabs Managed Browser se ferment parfois (`lifecycle.close.mode=after_task`), il faut rouvrir avant chaque inspection longue.
- Le clic direct Playwright sur `Postuler` peut échouer avec une strict mode violation; préférer endpoints Turbo/fetch ou sélecteurs DOM déterministes.
- `#postuler` seul ne suffit pas toujours à injecter le formulaire dans le DOM; l’endpoint `getinitialformframeview` est plus fiable.
- Ne pas traiter `formCustomOneTap`, `formOneTap`, bookmark ou `alert-form` comme le tunnel de candidature principal.
- La soumission effective via `POST /fr-fr/offres/postcandidateinformationfromstepframeview` n’a pas été exécutée.

## Automatisation sûre proposée

1. Ouvrir l’offre HelloWork en Managed Browser/profil existant.
2. Charger/inspecter le form via `getinitialformframeview` en mode credentials include.
3. Extraire les champs requis et vérifier que le formulaire correspond au contrat connu.
4. Préremplir seulement les champs non sensibles ou déjà connus: prénom, nom, email masqué côté logs, message optionnel généré depuis le brouillon.
5. Vérifier la présence d’un CV requis / CV déjà disponible.
6. Afficher un résumé avant action finale.
7. Garder le dernier `POST` derrière un flag explicite et visible, par exemple `--submit`, ou mieux demander validation interactive.

## Garde-fou impératif

Aucune candidature HelloWork ne doit être envoyée automatiquement tant que:

- le comportement du CV/uploader n’est pas confirmé;
- la page de confirmation post-submit n’est pas connue;
- la détection anti-doublon candidature envoyée n’est pas implémentée;
- le CLI n’a pas un mode dry-run/test couvrant le payload sans secrets.
