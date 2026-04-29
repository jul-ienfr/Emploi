# Emploi CLI Next Evolution Phase Plan

**Repo Path:** `/home/jul/Emploi`
**Concurrency:** 2
**Parallel Mode:** safe
**Validation Mode:** standard
**Max Retries Per Task:** 1
**Resume:** true
**Commit Mode:** none

## Phase 1 — Managed Browser real-run hardening
**Progress:** 100%

- [x] Add JSON-capable dry-run/smoke commands for `emploi browser` and `emploi ft` so real Managed Browser wiring can be verified without touching candidature submit.
- [x] Improve `doctor --json` Managed Browser diagnostics with actionable status fields and documented remediation.

### Phase Status
- [x] Phase 1 complete

## Phase 2 — Julien search profiles bootstrap
**Progress:** 100%

- [x] Add an idempotent command to install default Julien search profiles adapted to Bogève, remote work, Python/support/admin roles, and mobility constraints.
- [x] Add list/run UX improvements so profile output clearly shows what was created, skipped, enabled, and last run.

### Phase Status
- [x] Phase 2 complete

## Phase 3 — Scoring v2
**Progress:** 100%

- [x] Extend scoring with explicit criteria for remote work, Bogève/location constraints, contract type, salary signal, realistic match, and candidature effort.
- [x] Store/display richer score reasons while keeping existing offers rescorable and migrations idempotent.

### Phase Status
- [x] Phase 3 complete

## Phase 4 — Assisted application generator
**Progress:** 100%

- [x] Add a safe `emploi application draft` surface that generates a short tailored French draft/checklist from a stored offer without submitting anything.
- [x] Persist draft applications and draft file paths/events so `emploi next` can guide final manual action.

### Phase Status
- [x] Phase 4 complete

## Phase 5 — Pipeline and follow-ups
**Progress:** 100%

- [x] Add application status update and follow-up scheduling commands covering analyzed, interesting, draft, sent, followup, response, rejected, interview.
- [x] Make `emploi next` include due follow-ups and stale sent applications before new offers.

### Phase Status
- [x] Phase 5 complete

## Phase 6 — Multi-source import foundation
**Progress:** 100%

- [x] Add a generic import command for JSON/CSV offers so non-France-Travail sources can be loaded without direct scraping.
- [x] Add source fields and documentation for Indeed, Welcome to the Jungle, LinkedIn, local sites, and remote/freelance sources as future adapters.

### Phase Status
- [x] Phase 6 complete

## Phase 7 — Julien brief/report mode
**Progress:** 100%

- [x] Add `emploi brief` with readable and JSON modes summarizing best offers, actions, follow-ups, blockers, and weekly stats.
- [x] Update README and the in-repo Hermes skill with the new daily workflow.

### Phase Status
- [x] Phase 7 complete

## Global Status
**Overall Progress:** 100%
- [x] Plan complete
