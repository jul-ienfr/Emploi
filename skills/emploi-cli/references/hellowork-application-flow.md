# HelloWork application flow contract

This reference freezes the expected reusable CLI behavior for HelloWork partner applications.

## Commands

```bash
emploi hellowork apply OFFER_ID
emploi hellowork apply OFFER_ID --submit
emploi hellowork apply OFFER_ID --submit --kanban-stack candidature-envoyee
emploi hellowork apply OFFER_ID --submit --no-kanban
```

## Safety contract

- Default mode is dry-run: open the HelloWork URL, fetch the application form from browser context, validate required fields/CV, and stop before final POST.
- Real application submission requires explicit `--submit`.
- Never log or commit secrets, cookies, `FunnelId`, email values, local CV paths, or full POST payloads.
- Do not fallback from a failed form extraction to blind clicking.
- Use the existing `france-travail` / candidature Managed Browser context unless the operator passes another site/profile.

## Runtime behavior

1. Resolve the HelloWork URL from the offer URL, `partner_opened` event, or explicit `--url`.
2. Open with Managed Browser `lifecycle_open`.
3. Extract external offer ID from `/emplois/<id>.html`.
4. Fetch `/fr-fr/offres/getinitialformframeview?offerId=<id>&ts=...` from the page context.
5. Validate:
   - form exists;
   - `FunnelId` exists but remains redacted;
   - `Firstname` exists;
   - `LastName` exists (`LastName`, not `Lastname`);
   - `Email` exists but remains redacted;
   - CV frame/content is present;
   - submit control is detected either by `button[data-cy="submitButton"]`, `type=submit`, or visible `Postuler` in the Turbo-frame HTML.
6. On dry-run, record `hellowork_apply_dry_run` only.
7. On `--submit`, fill the motivation message from the local draft if available, submit the form, handle the dissuasion warning step when shown, and require confirmation text such as candidature envoyée.
8. After confirmation:
   - create/update local application status `sent`;
   - record `application_submitted` with sanitized payload;
   - set offer status `sent`;
   - create/reuse a Deck card in stack alias `candidature-envoyee` unless `--no-kanban`.

## Regression coverage

Keep tests for:

- dry-run does not submit and does not create a sent application;
- submit records `application_submitted`, local `sent`, and Deck card creation/reuse;
- CLI uses configured `candidature-envoyee` stack alias;
- docs mention `emploi hellowork apply`, `--submit`, `application_submitted`, `candidature-envoyee`, and no `FunnelId` logging.
