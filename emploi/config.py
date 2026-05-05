"""Emploi CLI configuration — loads local accounts/settings from ~/.config/emploi/*.json.

This module is the single source of truth for personal configuration
(account profile names, etc.) that MUST NOT be committed to GitHub.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# ── paths ──────────────────────────────────────────────────────────────

_XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
_EMPLOI_CONFIG_DIR = _XDG_CONFIG_HOME / "emploi"
_ACCOUNTS_FILE = _EMPLOI_CONFIG_DIR / "accounts.json"
_CONFIG_FILE = _EMPLOI_CONFIG_DIR / "config.json"


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        print(f"⚠ Warning: invalid JSON in {path}: {exc}", file=sys.stderr)
        return None


# ── accounts (profiles) ───────────────────────────────────────────────

def _load_accounts() -> dict[str, str]:
    """Load profile mapping from accounts.json (or fallback config.json)."""
    data = _load_json(_ACCOUNTS_FILE) or _load_json(_CONFIG_FILE) or {}
    return dict(data.get("profiles", data.get("accounts", {})))


def get_profile(key: str) -> str:
    """Resolve a logical account name to a Managed Browser profile string.

    >>> get_profile("candidature")
    'emploi-candidature'
    """
    accounts = _load_accounts()
    return accounts.get(key) or accounts.get(key, key)


def get_default_profile() -> str:
    """Return the default profile to use (usually 'emploi-candidature')."""
    accounts = _load_accounts()
    data = _load_json(_ACCOUNTS_FILE) or _load_json(_CONFIG_FILE) or {}
    default_key = data.get("default", "candidature")
    return accounts.get(default_key, default_key)


def list_accounts() -> list[dict[str, str]]:
    """Return all configured accounts as [{key, profile}, ...]."""
    accounts = _load_accounts()
    data = _load_json(_ACCOUNTS_FILE) or _load_json(_CONFIG_FILE) or {}
    default_key = data.get("default", "candidature")
    return [
        {"key": k, "profile": v, "default": "✓" if k == default_key else ""}
        for k, v in accounts.items()
    ]
