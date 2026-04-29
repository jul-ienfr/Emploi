# Managed Browser France Travail Production Plan

> **For Hermes:** Use phase-plan-executor skill to implement this plan phase-by-phase.

**Goal:** Make `emploi` production-ready as a Python CLI orchestrator for France Travail through a Managed Browser adapter.

**Architecture:** Keep SQLite/scoring/reporting local. Add a browser adapter boundary that can call an external Managed Browser CLI/API later, and build France Travail flows on top of that boundary. Avoid hardcoded scraping as the primary integration path.

**Tech Stack:** Python, Typer, SQLite, pytest, JSON command adapter.

**Concurrency:** 1
**Parallel Mode:** serial
**Validation Mode:** standard
**Max Retries Per Task:** 1
**Resume:** true
**Commit Mode:** per-phase

---

## Phase 1 — Managed Browser adapter foundation
**Progress:** 100%

- [x] Add browser command models and errors in `emploi/browser/models.py` and `emploi/browser/errors.py`
- [x] Add `ManagedBrowserClient` in `emploi/browser/client.py` with injectable subprocess runner and JSON parsing
- [x] Add CLI commands `emploi browser status/open/snapshot/checkpoint` in `emploi/cli.py`
- [x] Add tests for browser client command construction and CLI output in `tests/test_browser_client.py` and `tests/test_cli_browser.py`

### Phase Status
- [x] Phase 1 complete

## Phase 2 — DB schema for browser-backed France Travail
**Progress:** 100%

- [x] Add idempotent migrations in `emploi/migrations.py`
- [x] Extend `offers` with external/browser/active/snapshot fields without breaking existing DBs
- [x] Add `browser_sessions` and `offer_events` tables plus DB helpers
- [x] Add tests for migrations and event/session helpers in `tests/test_migrations.py`

### Phase Status
- [x] Phase 2 complete

## Phase 3 — France Travail browser flows
**Progress:** 100%

- [x] Add `emploi/france_travail/extractors.py` to parse offers from Managed Browser snapshots/HTML/text
- [x] Add `emploi/france_travail/flows.py` for search, refresh and apply-check flow orchestration using `ManagedBrowserClient`
- [x] Add CLI commands `emploi ft search`, `emploi ft refresh`, and `emploi ft apply --check/--draft/--open`
- [x] Add tests for extractors, flows with fake browser client, and FT CLI commands

### Phase Status
- [x] Phase 3 complete

## Phase 4 — Saved searches and operator reporting
**Progress:** 100%

- [x] Add saved search storage and helpers in DB/migrations
- [x] Add CLI commands `emploi search-profile add/list/run`
- [x] Add `emploi next` and enrich `emploi report` for active FT offers and application actions
- [x] Update README with Managed Browser architecture, setup env vars, and V1 command examples
- [x] Run full validation: `python3 -m pytest tests -q`, `python3 -m compileall emploi`, CLI smoke commands

### Phase Status
- [x] Phase 4 complete

## Global Status
**Overall Progress:** 100%
- [x] Plan complete
