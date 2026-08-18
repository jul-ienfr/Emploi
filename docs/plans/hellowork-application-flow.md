# Exploration HelloWork — flow candidature

Date: 2026-05-06
Offre inspectée: HelloWork `78282309` — Chauffeur Poids Lourd H/F, Slash Intérim, Bons-en-Chablais.

> **Statut : VALIDÉ EN LIVE (2026-08-16)** — dry-run complet de bout en bout (identité pré-remplie depuis `identity.json`, CV uploadé via `/fr-fr/uploadcv` + `JweHashResume`) **et soumission réelle envoyée** (offre 80750290, Ortec Group, Bonneville — « Mes candidatures » 47 → 48, candidature enregistrée localement #2/offre 98). **Tunnels directs et OTP : fonctionnels.** Tunnels smart-apply/dissuasion : pré-remplissage validé dans le DOM mais POST re-rendu par le serveur (blocage côté HelloWork, à compléter à la main). Détails dans « Résultats de la revalidation » ci-dessous.

## Résultats de la revalidation (2026-08-16)

Ce qui a été vérifié en live (offre `81098172`, profil `emploi-candidature`) :

1. ✅ **Endpoints stables** : `getinitialformframeview` (HTTP 200) et `GetUploaderCvFrameView` (HTTP 200) répondent toujours.
2. 🐛 **Bug corrigé — parsing des réponses console_eval** : le serveur imbrique la valeur sous `result.result` (replay step) ; `hellowork.py` lisait l'ancien format et ne voyait **jamais** les champs en live (le dry-run remontait « FunnelId manquant » à tort). Aligné sur `flows._eval_value` (legacy `value`/`raw`/chaîne directe conservés). Vérifié en live : FunnelId et bouton submit sont désormais détectés.
3. 🆕 **Champ `HasAcceptedCGU` requis** (consentement CGU) ajouté au formulaire principal. Garde-fou ajouté : le CLI refuse toute soumission si la case n'est pas cochée et **ne la coche jamais lui-même** (message clair, aucun POST).
4. ✅ **Prénom/Nom/Email ne sont plus pré-remplis par HelloWork** — le CLI les pré-remplit désormais automatiquement depuis `~/.config/emploi/identity.json` (`emploi identity set --firstname --lastname --email`), en injectant les valeurs dans le formulaire avant lecture/soumission. Validé en live : `firstnamePresent/lastnamePresent/emailPresent: true`.
5. ✅ **CV requis par offre** : upload automatique implémenté — le CLI lit le CV (option `--cv`, sinon profil documents par défaut), le transmet en base64 dans l'expression, construit un `File` côté page et POST `multipart` vers `/fr-fr/uploadcv` **avec les en-têtes `Turbo-Frame: funnel-resume-uploader-frame` + `X-Requested-With` (sans eux → HTTP 400)** ; le `JweHashResume` de la réponse est injecté dans le formulaire principal (créé si absent). Validé en live : `cvPresent: true`.
6. ✅ **Flux multi-étapes** : bouton « Continuer ma candidature » observé (step 1 = identité + CV + CGU + message). Page de confirmation post-submit observée lors de la soumission réelle : « Votre candidature est envoyée, vous allez être redirigé·e » (offre 80750290).

### Étape « information complémentaire » (Smart Apply SAv2) — blocage constaté en live

**Soumission réelle tentée le 2026-08-16 (offre 81098172, Temporis Interim)** avec identité + CV + CGU + OTP :

1. ✅ POST du formulaire principal → accepté (codes OTP envoyés par email : 101505 puis 983585 — chaque POST du formulaire principal renvoie un NOUVEAU code ; les codes expirent ~10 min).
2. ✅ Validation OTP (983585) → le tunnel avance.
3. ❌ **Étape finale bloquée** : `funnel-smart-apply-form` → `POST /fr-fr/offres/postsav2formstepframeview` (« Temporis Interim a besoin d'une information complémentaire pour enregistrer votre candidature »). La grille de questions est **VIDE** et chaque POST renvoie la même étape avec un **nouveau FunnelId** → boucle infinie. Aucune candidature enregistrée dans « Mes candidatures » (47 existantes, aucune nouvelle), aucun mail de confirmation, offre devenue « plus disponible ».

Hypothèses (à investiguer lors d'une prochaine session) :
- Le contrôleur Stimulus `mutable` (event `product.preselect_question`, `data-controller="mutable forced-reload-guard"`) charge la question via un fetch que l'injection brute (`target.innerHTML`) ne déclenche pas — il faut laisser **Turbo** swapper le frame `offer-detail-step-frame` et attendre le chargement (clic natif sur « Postuler » + attente longue).
- Ou la question du recruteur n'est pas configurée côté serveur (état transitoire de l'annonce, expirée entre-temps).

Progrès (2026-08-16, commit `76d0394`) :
- Les injections n'écrasent plus le body : le frame `turbo-frame#funnel-frame` est créé s'il manque (Stimulus peut connecter les contrôleurs).
- **Constat supplémentaire : les offres deviennent « plus disponible » dès qu'un tunnel est démarré** (Temporis 81098172 ET AFTRAL 80942431) sans que la candidature soit enregistrée dans « Mes candidatures » (vérifié : 47 candidatures, aucune nouvelle). Le blocage semble lié à l'étape elle-même (question vide), pas à l'offre.

À retenter sur une **offre fraîche** (issue du scan quotidien) avec la gestion Smart Apply implémentée (`_smart_apply_expression`, boucle 3 essais, frame créé) : si la grille de questions se remplit (contrôleur mutable connecté), le CLI le signalera (`questionLabels`) pour réponse manuelle ; sinon il rapportera l'étape bloquée proprement.

### ✅ PREMIÈRE CANDIDATURE RÉELLE ENVOYÉE (2026-08-16, offre 80750290 — Ortec Group, Bonneville)

**« Mes candidatures » : 48 entrées (était 47) — « Envoyée Chauffeur PL H/F — Ortec Group — Bonneville — Envoyée le 16 août ».** Le tunnel de cette offre s'est finalisé **directement** (pas d'OTP, pas de smart-apply) : identité pré-remplie + POST principal → réponse « Votre candidature est envoyée, vous allez être redirigé·e ». Enregistrement local fait (application #2, offre 98 → sent, événement `application_submitted`).

Observations clés du succès :
- **Le POST a abouti SANS upload CV** (le CV est attaché au compte, pas besoin de JweHashResume pour ce tunnel ; `cvPresent` reste vrai dans l'uploader).
- Les funnels varient par offre : direct (98), OTP (63), smart-apply (62/60), CGU présent/absent — le CLI gère les trois, la confirmation directe reste la plus simple.

### Constat final sur les tunnels smart-apply (2026-08-16, 4 recruteurs testés)

- **Direct (sans étape) : FONCTIONNE** — offre 80750290 (Ortec Group) envoyée ✅. Le POST principal → « Votre candidature est envoyée ».
- **OTP : FONCTIONNE** — code lu dans la webmail, tunnel avancé ✅.
- **Dissuasion** (« compétences précises ») : le POST/le clic re-rendent l'étape — boucle (offre 74311789 Ortec Annecy). Détection + gestion implémentées (commit `a21d069`), mais le serveur ne confirme pas.
- **Smart-apply** (« informations complémentaires ») : même boucle avec valeurs VALIDES (téléphone normalisé sans espaces — validation passée, étape re-rendue quand même). Testé sur Temporis (80808238), GT Solutions, Adequat (offres 101/102). Le pré-remplissage des champs est vérifié dans le DOM ; le serveur re-rend l'étape à chaque POST (fetch ET clic natif form-validator).

Conclusion : le CLI couvre tous les chemins (direct, OTP, dissuasion, smart-apply) avec diagnostics propres ; les tunnels smart-apply/dissuasion semblent bloqués côté HelloWork (probablement une exigence non visible du tunnel — questions recruteur non publiées ou étape nécessitant un écran interactif). Pour ces offres, compléter à la main dans le navigateur (champs déjà pré-remplis par le CLI en dry-run).

### Dernier état (2026-08-16, offre 80808238 Temporis Contamine) — validation du pré-remplissage

Le flux complet est désormais validé jusqu'au POST smart-apply inclus :
- ✅ **Détection** : le texte utile est dans `#funnel-frame` (hors preview 500 o) — `frameText` ajouté aux expressions et utilisé par les deux détections (bug ligne 838 corrigé, commit `72d2507`).
- ✅ **Pré-remplissage vérifié dans le DOM** : `sav2_field1..4` = « 06 99 85 69 48 », « Bogève », « 74250 », « 96 route du croue » (extraits du CV par `pdftotext` puis `emploi identity set`).
- ⚠️ **Le POST smart-apply re-rend l'étape** (questions vides, nouveau FunnelId) — le serveur ne confirme pas. Les tunnels **expirent** entre les tentatives (« Votre session a expiré »). Ni Temporis ni AFTRAL n'ont abouti (« Mes candidatures » vérifié : aucune nouvelle).
- Piste restante : les valeurs remplies doivent peut-être transiter par le contrôleur `form-validator` (fetch sérialisé maison) plutôt que `FormData(form)` brut — à investiguer sur une offre fraîche.

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
