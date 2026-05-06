# France Travail partner handoff hardening

Session pattern captured from the `ft apply --partner` work.

## Goal

Keep France Travail applications assisted-only while making external partner handoffs actionable and safe:

- `ft apply --check` may reveal partner choices such as Meteojob / HelloWork.
- `ft apply OFFER_ID --partner NAME` may open one explicitly selected partner URL.
- Neither command may click a partner-side final application control or submit anything.

## Verified behavior to preserve

1. Open the France Travail offer detail page with `lifecycle_open`, not raw `open`/`navigate`.
2. If the first snapshot has an apply signal but no partner URLs, perform at most one non-destructive expansion click on the FT apply-options control.
3. Re-snapshot and use DOM/console extraction for `a[href]` links because accessibility snapshots can expose partner labels while omitting hrefs.
4. Store `partner_handoff` as structured objects:
   ```json
   [{"name": "Meteojob", "url": "..."}, {"name": "HelloWork", "url": "..."}]
   ```
5. Operator output can stay name-only, but event payloads should preserve URLs.
6. `--partner NAME` must open only the selected partner URL through `lifecycle_open` and record `partner_opened` only after successful external open.

## Failure guardrails

Regression coverage should include:

- requested partner absent;
- partner present but missing URL;
- CLI runtime error rendered as clean `Error: ...`, not `typer.BadParameter` / `Invalid value`;
- no traceback;
- only the FT detail page may be opened in negative paths;
- no external partner URL opened;
- no `partner_opened` event recorded;
- no fallback to raw `open` / `navigate`.

Implementation pattern for CLI runtime errors inside `emploi ft apply`:

```python
except ValueError as error:
    console.print(f"[red]Error:[/red] {error}")
    raise typer.Exit(1) from error
```

Use `typer.BadParameter` for argument parsing mistakes, not runtime state like missing partner handoff data.

## Validation commands used

```bash
python3 -m pytest tests/test_france_travail_flows.py tests/test_cli_france_travail.py -q
python3 -m pytest tests/test_repo_skill.py -q
python3 -m compileall emploi
git diff --check
```

For live #22 checks, use the Python Managed Browser wrapper:

```bash
MANAGED_BROWSER_URL=http://127.0.0.1:9377 \
EMPLOI_MANAGED_BROWSER_COMMAND=managed-browser \
python3 -m emploi.cli ft apply 22 --check
```
