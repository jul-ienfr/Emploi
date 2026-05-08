from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

from emploi.browser.errors import ManagedBrowserCommandError, ManagedBrowserUnavailableError
from emploi.browser.models import DEFAULT_PROFILE, DEFAULT_SITE, BrowserCommandResult

Runner = Callable[..., subprocess.CompletedProcess[str]]


class ManagedBrowserClient:
    """Thin JSON command adapter for an external Managed Browser CLI."""

    def __init__(
        self,
        command: str | None = None,
        runner: Runner | None = None,
        timeout: float | int | None = None,
    ) -> None:
        self.command = command or os.environ.get("EMPLOI_MANAGED_BROWSER_COMMAND", "managed-browser")
        self.command_parts = shlex.split(self.command)
        self.runner = runner or subprocess.run
        self.timeout = self._parse_timeout(timeout)

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

    def lifecycle_open(
        self,
        url: str,
        *,
        site: str = DEFAULT_SITE,
        profile: str = DEFAULT_PROFILE,
    ) -> BrowserCommandResult:
        return self._run("lifecycle_open", site=site, profile=profile, options=["--url", url])

    def console_eval(
        self,
        expression: str,
        *,
        site: str = DEFAULT_SITE,
        profile: str = DEFAULT_PROFILE,
    ) -> BrowserCommandResult:
        return self._run("console_eval", site=site, profile=profile, options=["--expression", expression])

    def snapshot(
        self,
        *,
        label: str | None = None,
        site: str = DEFAULT_SITE,
        profile: str = DEFAULT_PROFILE,
    ) -> BrowserCommandResult:
        options = ["--label", label] if label else []
        return self._run("snapshot", site=site, profile=profile, options=options)

    def checkpoint(
        self,
        name: str,
        *,
        site: str = DEFAULT_SITE,
        profile: str = DEFAULT_PROFILE,
    ) -> BrowserCommandResult:
        return self._run("checkpoint", site=site, profile=profile, options=["--reason", name])

    def _parse_timeout(self, timeout: float | int | None) -> float:
        raw_timeout: object = timeout if timeout is not None else os.environ.get("EMPLOI_MANAGED_BROWSER_TIMEOUT", "60")
        try:
            parsed = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise ManagedBrowserCommandError(
                "Invalid EMPLOI_MANAGED_BROWSER_TIMEOUT: expected a number of seconds"
            ) from exc
        if not math.isfinite(parsed) or parsed <= 0:
            raise ManagedBrowserCommandError(
                "Invalid EMPLOI_MANAGED_BROWSER_TIMEOUT: expected a finite positive number of seconds"
            )
        return parsed

    def _run(
        self,
        subcommand: str,
        *,
        site: str,
        profile: str,
        options: Sequence[str] = (),
    ) -> BrowserCommandResult:
        args = [
            *self.command_parts,
            *self._managed_browser_subcommand(subcommand),
            "--profile",
            profile,
            "--site",
            site,
            *options,
            "--json",
        ]
        try:
            completed = self.runner(args, capture_output=True, text=True, check=False, timeout=self.timeout)
        except FileNotFoundError as exc:
            command = self.command_parts[0] if self.command_parts else self.command
            raise ManagedBrowserUnavailableError(
                f"Managed Browser command not found: {command}. "
                "Set EMPLOI_MANAGED_BROWSER_COMMAND or install the command."
            ) from exc
        except PermissionError as exc:
            command = self.command_parts[0] if self.command_parts else self.command
            raise ManagedBrowserUnavailableError(
                f"Managed Browser command not executable: {command}. "
                "Check file permissions."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            context = self._error_context(subcommand, site, profile, None, exc.stdout, exc.stderr)
            raise ManagedBrowserCommandError(
                f"Managed Browser command timed out after {self.timeout:g}s; {context}"
            ) from exc

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode != 0:
            context = self._error_context(subcommand, site, profile, completed.returncode, stdout, stderr)
            raise ManagedBrowserCommandError(
                f"Managed Browser command failed; {context}", returncode=completed.returncode
            )

        try:
            payload: Any = json.loads(stdout)
        except json.JSONDecodeError as exc:
            context = self._error_context(subcommand, site, profile, completed.returncode, stdout, stderr)
            raise ManagedBrowserCommandError(f"Invalid JSON from Managed Browser command: {exc}; {context}") from exc
        if not isinstance(payload, dict):
            context = self._error_context(subcommand, site, profile, completed.returncode, stdout, stderr)
            raise ManagedBrowserCommandError(
                f"Invalid JSON from Managed Browser command: expected object; {context}"
            )
        return BrowserCommandResult(command=subcommand, site=site, profile=profile, payload=payload)

    def _error_context(
        self,
        subcommand: str,
        site: str,
        profile: str,
        returncode: int | None,
        stdout: str | bytes | None,
        stderr: str | bytes | None,
    ) -> str:
        parts = [f"subcommand={subcommand}", f"site={site}", f"profile={profile}", f"returncode={returncode}"]
        if stdout:
            parts.append(f"stdout={self._bounded_output(stdout)}")
        if stderr:
            parts.append(f"stderr={self._bounded_output(stderr)}")
        return "; ".join(parts)

    def _bounded_output(self, value: str | bytes, limit: int = 500) -> str:
        if isinstance(value, bytes):
            value = value.decode(errors="replace")
        value = value.strip()
        if len(value) > limit:
            value = f"{value[:limit]}..."
        return repr(value)

    def _managed_browser_subcommand(self, subcommand: str) -> list[str]:
        if subcommand == "status":
            return ["profile", "status"]
        if subcommand == "open":
            return ["navigate"]
        if subcommand == "lifecycle_open":
            return ["lifecycle", "open"]
        if subcommand == "console_eval":
            return ["console", "eval"]
        if subcommand == "checkpoint":
            return ["storage", "checkpoint"]
        return [subcommand]
