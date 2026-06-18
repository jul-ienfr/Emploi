from __future__ import annotations

import json
import math
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from emploi.browser.errors import ManagedBrowserCommandError, ManagedBrowserUnavailableError
from emploi.browser.models import DEFAULT_PROFILE, DEFAULT_SITE, BrowserCommandResult


class ManagedBrowserClient:
    """HTTP adapter for the Python Managed Browser server (port 9377).

    Uses ``EMPLOI_MANAGED_BROWSER_URL`` (default ``http://127.0.0.1:9377``).
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | int | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("EMPLOI_MANAGED_BROWSER_URL", "http://127.0.0.1:9377")
        ).rstrip("/")
        self.timeout = self._parse_timeout(timeout)

    # ------------------------------------------------------------------
    # Public API — same signatures as before
    # ------------------------------------------------------------------

    def status(
        self, *, site: str = DEFAULT_SITE, profile: str = DEFAULT_PROFILE
    ) -> BrowserCommandResult:
        return self._get(
            f"/managed/profiles/{profile}/status",
            params={"site": site},
            action="status",
            site=site,
            profile=profile,
        )

    def open(
        self,
        url: str,
        *,
        site: str = DEFAULT_SITE,
        profile: str = DEFAULT_PROFILE,
    ) -> BrowserCommandResult:
        return self._post_json(
            "/managed/cli/open",
            body={"profile": profile, "site": site, "url": url},
            action="open",
            site=site,
            profile=profile,
        )

    def lifecycle_open(
        self,
        url: str,
        *,
        site: str = DEFAULT_SITE,
        profile: str = DEFAULT_PROFILE,
    ) -> BrowserCommandResult:
        return self._post_json(
            "/managed/cli/open",
            body={"profile": profile, "site": site, "url": url, "warmup": True},
            action="lifecycle_open",
            site=site,
            profile=profile,
        )

    def console_eval(
        self,
        expression: str,
        *,
        site: str = DEFAULT_SITE,
        profile: str = DEFAULT_PROFILE,
    ) -> BrowserCommandResult:
        return self._post_json(
            "/managed/cli/act",
            body={
                "profile": profile,
                "site": site,
                "action": "evaluate",
                "params": {"expression": expression},
            },
            action="console_eval",
            site=site,
            profile=profile,
        )

    def snapshot(
        self,
        *,
        label: str | None = None,
        site: str = DEFAULT_SITE,
        profile: str = DEFAULT_PROFILE,
    ) -> BrowserCommandResult:
        body: dict[str, Any] = {"profile": profile, "site": site}
        if label:
            body["label"] = label
        return self._post_json(
            "/managed/cli/snapshot",
            body=body,
            action="snapshot",
            site=site,
            profile=profile,
        )

    def checkpoint(
        self,
        name: str,
        *,
        site: str = DEFAULT_SITE,
        profile: str = DEFAULT_PROFILE,
    ) -> BrowserCommandResult:
        return self._post_json(
            "/managed/cli/checkpoint",
            body={"profile": profile, "site": site, "reason": name},
            action="checkpoint",
            site=site,
            profile=profile,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_timeout(self, timeout: float | int | None) -> float:
        raw: object = (
            timeout
            if timeout is not None
            else os.environ.get("EMPLOI_MANAGED_BROWSER_TIMEOUT", "60")
        )
        try:
            parsed = float(raw)
        except (TypeError, ValueError) as exc:
            raise ManagedBrowserCommandError(
                "Invalid EMPLOI_MANAGED_BROWSER_TIMEOUT: expected a number of seconds"
            ) from exc
        if not math.isfinite(parsed) or parsed <= 0:
            raise ManagedBrowserCommandError(
                "Invalid EMPLOI_MANAGED_BROWSER_TIMEOUT: expected a finite positive number of seconds"
            )
        return parsed

    def _get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        action: str,
        site: str,
        profile: str,
    ) -> BrowserCommandResult:
        url = self.base_url + path
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items() if v)
            if qs:
                url += "?" + qs
        payload = self._fetch_json(url, method="GET", action=action, site=site, profile=profile)
        return BrowserCommandResult(command=action, site=site, profile=profile, payload=payload)

    def _post_json(
        self,
        path: str,
        *,
        body: dict[str, Any],
        action: str,
        site: str,
        profile: str,
    ) -> BrowserCommandResult:
        url = self.base_url + path
        payload = self._fetch_json(
            url, method="POST", body=body, action=action, site=site, profile=profile
        )
        return BrowserCommandResult(command=action, site=site, profile=profile, payload=payload)

    def _fetch_json(
        self,
        url: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        action: str,
        site: str,
        profile: str,
    ) -> dict[str, Any]:
        data_bytes = json.dumps(body).encode() if body else None
        req = Request(
            url,
            data=data_bytes,
            method=method,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode()
        except HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode()[:500]
            except Exception:
                pass
            raise ManagedBrowserCommandError(
                f"Managed Browser HTTP {exc.code}; "
                f"subcommand={action}; site={site}; profile={profile}; "
                f"body={error_body!r}"
            ) from exc
        except URLError as exc:
            raise ManagedBrowserUnavailableError(
                f"Managed Browser unreachable at {self.base_url}: {exc.reason}. "
                f"Check that the server is running (port {self.base_url.rsplit(':', 1)[-1]})."
            ) from exc

        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ManagedBrowserCommandError(
                f"Invalid JSON from Managed Browser: {exc}; "
                f"subcommand={action}; site={site}; profile={profile}; "
                f"raw={raw[:300]!r}"
            ) from exc
        if not isinstance(payload, dict):
            raise ManagedBrowserCommandError(
                f"Invalid JSON from Managed Browser: expected object; "
                f"subcommand={action}; site={site}; profile={profile}"
            )
        return payload
