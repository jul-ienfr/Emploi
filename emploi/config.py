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

def _emploi_config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "emploi"


def _accounts_file() -> Path:
    return _emploi_config_dir() / "accounts.json"


def _config_file() -> Path:
    return _emploi_config_dir() / "config.json"


def _document_profiles_file() -> Path:
    return _emploi_config_dir() / "document_profiles.json"


def _kanban_endpoints_file() -> Path:
    return _emploi_config_dir() / "kanban_endpoints.json"


def _nextcloud_files_endpoints_file() -> Path:
    return _emploi_config_dir() / "nextcloud_files.json"


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
    data = _load_json(_accounts_file()) or _load_json(_config_file()) or {}
    return dict(data.get("profiles", data.get("accounts", {})))


def get_profile(key: str) -> str:
    """Resolve a logical account name to a Managed Browser profile string.

    >>> get_profile("candidature")
    'emploi-candidature'
    """
    accounts = _load_accounts()
    return accounts.get(key) or accounts.get(key, key)


def get_default_profile() -> str:
    """Return the default Managed Browser profile.

    With configured accounts this resolves the configured default key, usually
    ``candidature`` -> ``emploi-candidature``. Without any personal config, keep
    the historical test/dev fallback profile ``emploi``.
    """
    accounts = _load_accounts()
    if not accounts:
        return "emploi"
    data = _load_json(_accounts_file()) or _load_json(_config_file()) or {}
    default_key = data.get("default", "candidature")
    return accounts.get(default_key, default_key)


def list_accounts() -> list[dict[str, str]]:
    """Return all configured accounts as [{key, profile}, ...]."""
    accounts = _load_accounts()
    data = _load_json(_accounts_file()) or _load_json(_config_file()) or {}
    default_key = data.get("default", "candidature")
    return [
        {"key": k, "profile": v, "default": "✓" if k == default_key else ""}
        for k, v in accounts.items()
    ]


# ── document profiles (CV + cover letters) ─────────────────────────────

def _empty_document_profiles_payload() -> dict[str, Any]:
    return {"default": "", "profiles": {}}


def _load_document_profiles_payload() -> dict[str, Any]:
    data = _load_json(_document_profiles_file()) or _empty_document_profiles_payload()
    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
    default = str(data.get("default", "") or "")
    return {"default": default, "profiles": profiles}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _expand_profile_path(path_value: str) -> str:
    if not path_value:
        return ""
    return str(Path(path_value).expanduser())


def _normalize_document_profile(name: str, raw: dict[str, Any], *, default_name: str = "") -> dict[str, Any]:
    cv_path = _expand_profile_path(str(raw.get("cv_path", "") or raw.get("cv", "") or ""))
    cover_letter_path = _expand_profile_path(
        str(raw.get("cover_letter_path", "") or raw.get("letter_path", "") or raw.get("letter", "") or "")
    )
    normalized = {
        "name": name,
        "cv_path": cv_path,
        "cover_letter_path": cover_letter_path,
        "notes": str(raw.get("notes", "") or ""),
        "default": "✓" if name == default_name else "",
        "cv_exists": bool(cv_path and Path(cv_path).exists()),
        "cover_letter_exists": bool(cover_letter_path and Path(cover_letter_path).exists()),
    }
    return normalized


def list_document_profiles() -> list[dict[str, Any]]:
    """Return configured job-document profiles, each with CV/cover-letter paths and existence flags."""
    data = _load_document_profiles_payload()
    default_name = str(data.get("default", "") or "")
    profiles = data.get("profiles", {})
    return [
        _normalize_document_profile(name, raw, default_name=default_name)
        for name, raw in sorted(profiles.items())
        if isinstance(raw, dict)
    ]


def get_document_profile(name: str) -> dict[str, Any] | None:
    data = _load_document_profiles_payload()
    raw = data.get("profiles", {}).get(name)
    if not isinstance(raw, dict):
        return None
    return _normalize_document_profile(name, raw, default_name=str(data.get("default", "") or ""))


def get_default_document_profile() -> dict[str, Any] | None:
    data = _load_document_profiles_payload()
    default_name = str(data.get("default", "") or "")
    if default_name:
        found = get_document_profile(default_name)
        if found is not None:
            return found
    profiles = list_document_profiles()
    return profiles[0] if profiles else None


def set_document_profile(
    name: str,
    *,
    cv_path: str = "",
    cover_letter_path: str = "",
    notes: str = "",
    make_default: bool = False,
) -> dict[str, Any]:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Nom de profil documents obligatoire")
    data = _load_document_profiles_payload()
    profiles = dict(data.get("profiles", {}))
    existing = profiles.get(normalized_name, {}) if isinstance(profiles.get(normalized_name), dict) else {}
    cv = _expand_profile_path(cv_path) if cv_path else str(existing.get("cv_path", "") or "")
    letter = _expand_profile_path(cover_letter_path) if cover_letter_path else str(existing.get("cover_letter_path", "") or "")
    profile = {
        "cv_path": cv,
        "cover_letter_path": letter,
        "notes": notes if notes else str(existing.get("notes", "") or ""),
    }
    profiles[normalized_name] = profile
    default = normalized_name if make_default or not data.get("default") else str(data.get("default", "") or "")
    payload = {"default": default, "profiles": profiles}
    _write_json(_document_profiles_file(), payload)
    return _normalize_document_profile(normalized_name, profile, default_name=default)


def set_default_document_profile(name: str) -> dict[str, Any]:
    data = _load_document_profiles_payload()
    if name not in data.get("profiles", {}):
        raise ValueError(f"Profil documents introuvable: {name}")
    data["default"] = name
    _write_json(_document_profiles_file(), data)
    profile = get_document_profile(name)
    assert profile is not None
    return profile


# ── external kanban endpoints (Nextcloud Deck, etc.) ─────────────────────

def _empty_kanban_endpoints_payload() -> dict[str, Any]:
    return {"default": "", "endpoints": {}}


def _load_kanban_endpoints_payload() -> dict[str, Any]:
    data = _load_json(_kanban_endpoints_file()) or _empty_kanban_endpoints_payload()
    endpoints = data.get("endpoints", {})
    if not isinstance(endpoints, dict):
        endpoints = {}
    default = str(data.get("default", "") or "")
    return {"default": default, "endpoints": endpoints}


def _normalize_stack_aliases(raw_stacks: Any) -> dict[str, int]:
    if not isinstance(raw_stacks, dict):
        return {}
    stacks: dict[str, int] = {}
    for alias, stack_id in raw_stacks.items():
        key = str(alias or "").strip()
        if not key:
            continue
        try:
            stacks[key] = int(stack_id)
        except (TypeError, ValueError):
            continue
    return stacks


def resolve_kanban_stack(endpoint: dict[str, Any], stack: str | int) -> int:
    """Resolve a Deck stack from a numeric ID or configured endpoint alias."""
    if isinstance(stack, int):
        return stack
    value = str(stack or "").strip()
    if not value:
        raise ValueError("Stack kanban obligatoire")
    if value.isdigit():
        return int(value)
    stacks = _normalize_stack_aliases(endpoint.get("stacks"))
    if value in stacks:
        return stacks[value]
    raise ValueError(f"Stack kanban inconnue: {value}")


def parse_kanban_stack_options(values: list[str] | tuple[str, ...] | None) -> dict[str, int]:
    stacks: dict[str, int] = {}
    for value in values or []:
        item = str(value or "").strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError("Format stack attendu: alias=ID")
        alias, raw_stack_id = item.split("=", 1)
        alias = alias.strip()
        if not alias:
            raise ValueError("Alias stack obligatoire")
        try:
            stacks[alias] = int(raw_stack_id.strip())
        except ValueError as error:
            raise ValueError(f"ID stack invalide pour {alias}: {raw_stack_id}") from error
    return stacks


def _normalize_kanban_endpoint(name: str, raw: dict[str, Any], *, default_name: str = "") -> dict[str, Any]:
    base_url = str(raw.get("base_url", "") or "").rstrip("/")
    api_base_path = str(raw.get("api_base_path", "/index.php/apps/deck/api/v1.0") or "/index.php/apps/deck/api/v1.0")
    if not api_base_path.startswith("/"):
        api_base_path = "/" + api_base_path
    board_id = int(raw.get("board_id", 0) or 0)
    board_url = str(raw.get("board_url", "") or "")
    if not board_url and base_url and board_id:
        board_url = f"{base_url}/apps/deck/board/{board_id}"
    api_board_url = f"{base_url}{api_base_path}/boards/{board_id}" if base_url and board_id else ""
    return {
        "name": name,
        "title": str(raw.get("title", "") or ""),
        "base_url": base_url,
        "board_id": board_id,
        "board_url": board_url,
        "api_base_path": api_base_path,
        "api_board_url": api_board_url,
        "api_stacks_url": f"{api_board_url}/stacks" if api_board_url else "",
        "username_pass": str(raw.get("username_pass", "") or ""),
        "password_pass": str(raw.get("password_pass", "") or ""),
        "stacks": _normalize_stack_aliases(raw.get("stacks")),
        "default": "✓" if name == default_name else "",
    }

def list_kanban_endpoints() -> list[dict[str, Any]]:
    data = _load_kanban_endpoints_payload()
    default_name = str(data.get("default", "") or "")
    endpoints = data.get("endpoints", {})
    return [
        _normalize_kanban_endpoint(name, raw, default_name=default_name)
        for name, raw in sorted(endpoints.items())
        if isinstance(raw, dict)
    ]


def get_kanban_endpoint(name: str) -> dict[str, Any] | None:
    data = _load_kanban_endpoints_payload()
    raw = data.get("endpoints", {}).get(name)
    if not isinstance(raw, dict):
        return None
    return _normalize_kanban_endpoint(name, raw, default_name=str(data.get("default", "") or ""))


def get_default_kanban_endpoint() -> dict[str, Any] | None:
    data = _load_kanban_endpoints_payload()
    default_name = str(data.get("default", "") or "")
    if default_name:
        found = get_kanban_endpoint(default_name)
        if found is not None:
            return found
    endpoints = list_kanban_endpoints()
    return endpoints[0] if endpoints else None


def set_kanban_endpoint(
    name: str,
    *,
    base_url: str,
    board_id: int,
    board_url: str = "",
    username_pass: str = "",
    password_pass: str = "",
    title: str = "",
    api_base_path: str = "/index.php/apps/deck/api/v1.0",
    make_default: bool = False,
    stacks: dict[str, int] | None = None,
) -> dict[str, Any]:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Nom d'endpoint kanban obligatoire")
    if not base_url.strip():
        raise ValueError("URL Nextcloud obligatoire")
    if int(board_id) <= 0:
        raise ValueError("board_id doit être positif")
    data = _load_kanban_endpoints_payload()
    endpoints = dict(data.get("endpoints", {}))
    endpoint = {
        "title": title,
        "base_url": base_url.rstrip("/"),
        "board_id": int(board_id),
        "board_url": board_url,
        "api_base_path": api_base_path,
        "username_pass": username_pass,
        "password_pass": password_pass,
        "stacks": _normalize_stack_aliases(stacks),
    }
    endpoints[normalized_name] = endpoint
    default = normalized_name if make_default or not data.get("default") else str(data.get("default", "") or "")
    _write_json(_kanban_endpoints_file(), {"default": default, "endpoints": endpoints})
    return _normalize_kanban_endpoint(normalized_name, endpoint, default_name=default)


# ── Nextcloud Files/WebDAV endpoints ─────────────────────────────────────

def _empty_nextcloud_files_endpoints_payload() -> dict[str, Any]:
    return {"default": "", "endpoints": {}}


def _load_nextcloud_files_endpoints_payload() -> dict[str, Any]:
    data = _load_json(_nextcloud_files_endpoints_file()) or _empty_nextcloud_files_endpoints_payload()
    endpoints = data.get("endpoints", {})
    if not isinstance(endpoints, dict):
        endpoints = {}
    default = str(data.get("default", "") or "")
    return {"default": default, "endpoints": endpoints}


def _normalize_remote_root(remote_root: str) -> str:
    root = str(remote_root or "").strip() or "/Emploi"
    if not root.startswith("/"):
        root = "/" + root
    return root.rstrip("/") or "/"


def _normalize_nextcloud_files_endpoint(name: str, raw: dict[str, Any], *, default_name: str = "") -> dict[str, Any]:
    base_url = str(raw.get("base_url", "") or "").rstrip("/")
    remote_root = _normalize_remote_root(str(raw.get("remote_root", "") or "/Emploi"))
    webdav_base_path = str(raw.get("webdav_base_path", "/remote.php/dav/files") or "/remote.php/dav/files")
    if not webdav_base_path.startswith("/"):
        webdav_base_path = "/" + webdav_base_path
    webdav_root_url = f"{base_url}{webdav_base_path}/{{username}}{remote_root}" if base_url else ""
    return {
        "name": name,
        "base_url": base_url,
        "remote_root": remote_root,
        "webdav_base_path": webdav_base_path,
        "webdav_root_url": webdav_root_url,
        "username_pass": str(raw.get("username_pass", "") or ""),
        "password_pass": str(raw.get("password_pass", "") or ""),
        "default": "✓" if name == default_name else "",
    }


def list_nextcloud_files_endpoints() -> list[dict[str, Any]]:
    data = _load_nextcloud_files_endpoints_payload()
    default_name = str(data.get("default", "") or "")
    endpoints = data.get("endpoints", {})
    return [
        _normalize_nextcloud_files_endpoint(name, raw, default_name=default_name)
        for name, raw in sorted(endpoints.items())
        if isinstance(raw, dict)
    ]


def get_nextcloud_files_endpoint(name: str) -> dict[str, Any] | None:
    data = _load_nextcloud_files_endpoints_payload()
    raw = data.get("endpoints", {}).get(name)
    if not isinstance(raw, dict):
        return None
    return _normalize_nextcloud_files_endpoint(name, raw, default_name=str(data.get("default", "") or ""))


def get_default_nextcloud_files_endpoint() -> dict[str, Any] | None:
    data = _load_nextcloud_files_endpoints_payload()
    default_name = str(data.get("default", "") or "")
    if default_name:
        found = get_nextcloud_files_endpoint(default_name)
        if found is not None:
            return found
    endpoints = list_nextcloud_files_endpoints()
    return endpoints[0] if endpoints else None


def set_nextcloud_files_endpoint(
    name: str,
    *,
    base_url: str,
    remote_root: str = "/Emploi",
    username_pass: str = "",
    password_pass: str = "",
    webdav_base_path: str = "/remote.php/dav/files",
    make_default: bool = False,
) -> dict[str, Any]:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Nom d'endpoint Nextcloud Files obligatoire")
    if not base_url.strip():
        raise ValueError("URL Nextcloud obligatoire")
    endpoint = {
        "base_url": base_url.rstrip("/"),
        "remote_root": _normalize_remote_root(remote_root),
        "webdav_base_path": webdav_base_path,
        "username_pass": username_pass,
        "password_pass": password_pass,
    }
    data = _load_nextcloud_files_endpoints_payload()
    endpoints = dict(data.get("endpoints", {}))
    endpoints[normalized_name] = endpoint
    default = normalized_name if make_default or not data.get("default") else str(data.get("default", "") or "")
    _write_json(_nextcloud_files_endpoints_file(), {"default": default, "endpoints": endpoints})
    return _normalize_nextcloud_files_endpoint(normalized_name, endpoint, default_name=default)
