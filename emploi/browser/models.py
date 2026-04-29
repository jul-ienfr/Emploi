from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_PROFILE = "emploi"
DEFAULT_SITE = "france-travail"


@dataclass(frozen=True)
class BrowserCommandResult:
    """Parsed JSON response from the external Managed Browser command."""

    command: str
    site: str
    profile: str
    payload: dict[str, Any]

    @property
    def ok(self) -> bool:
        return bool(self.payload.get("ok", False))
