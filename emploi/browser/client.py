from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

from emploi.browser.errors import ManagedBrowserCommandError, ManagedBrowserUnavailableError
from emploi.browser.models import DEFAULT_PROFILE, DEFAULT_SITE, BrowserCommandResult

Runner = Callable[..., subprocess.CompletedProcess[str]]


class ManagedBrowserClient:
    """Thin JSON command adapter for an external Managed Browser CLI."""

    def __init__(self, command: str | None = None, runner: Runner | None = None) -> None:
        self.command = command or os.environ.get("EMPLOI_MANAGED_BROWSER_COMMAND", "managed-browser")
        self.runner = runner or subprocess.run

    def status(self, *, site: str = DEFAULT_SITE, profile: str = DEFAULT_PROFILE) -> BrowserCommandResult:
        return self._run("status", site=site, profile=profile)

    def open(
        self,
        url: str,
        *,
        site: str = DEFAULT_SITE,
        profile: str = DEFAULT_PROFILE,
    ) -> BrowserCommandResult:
        return self._run("open", site=site, profile=profile, options=["--url", url])

    def snapshot(
        self,
        *,
        label: str | None = None,
        site: str = DEFAULT_SITE,
        profile: str = DEFAULT_PROFILE,
    ) -> BrowserCommandResult:
        options: list[str] = []
        if label:
            options.extend(["--label", label])
        return self._run("snapshot", site=site, profile=profile, options=options)

    def checkpoint(
        self,
        name: str,
        *,
        site: str = DEFAULT_SITE,
        profile: str = DEFAULT_PROFILE,
    ) -> BrowserCommandResult:
        return self._run("checkpoint", site=site, profile=profile, options=["--name", name])

    def _run(
        self,
        subcommand: str,
        *,
        site: str,
        profile: str,
        options: Sequence[str] = (),
    ) -> BrowserCommandResult:
        args = [
            self.command,
            subcommand,
            "--site",
            site,
            "--profile",
            profile,
            *options,
            "--json",
        ]
        try:
            completed = self.runner(args, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise ManagedBrowserUnavailableError(
                f"Managed Browser command not found: {self.command}. "
                "Set EMPLOI_MANAGED_BROWSER_COMMAND or install the command."
            ) from exc

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode != 0:
            detail = stderr.strip() or stdout.strip() or f"exit code {completed.returncode}"
            raise ManagedBrowserCommandError(
                f"Managed Browser command failed: {detail}", returncode=completed.returncode
            )

        try:
            payload: Any = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ManagedBrowserCommandError(f"Invalid JSON from Managed Browser command: {exc}") from exc
        if not isinstance(payload, dict):
            raise ManagedBrowserCommandError("Invalid JSON from Managed Browser command: expected object")
        return BrowserCommandResult(command=subcommand, site=site, profile=profile, payload=payload)
