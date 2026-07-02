"""Generic registry for JSON-backed endpoint configuration.

Eliminates the copy-paste pattern repeated across kanban, nextcloud_files,
and nextcloud_tasks sections of ``config.py``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        print(f"⚠ Warning: invalid JSON in {path}: {exc}", file=sys.stderr)
        return None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class EndpointRegistry:
    """Manages a JSON file containing ``{"default": "", "endpoints": {}}``.

    Each endpoint type supplies:
      - *file_path_func*: callable that returns the ``Path`` (re-evaluated on each call
        so that ``XDG_CONFIG_HOME`` changes in tests take effect)
      - *normalize_func*: ``(name, raw_dict, default_name) -> normalized_dict``
      - *validate_func* (optional): ``(name, **kwargs) -> None`` called on ``set()``
    """

    def __init__(
        self,
        file_path_func: Callable[[], Path],
        normalize_func: Callable[[str, dict[str, Any], str], dict[str, Any]],
        validate_func: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._file_func = file_path_func
        self._normalize = normalize_func
        self._validate = validate_func

    # ── internal helpers ────────────────────────────────────────────────

    def _empty_payload(self) -> dict[str, Any]:
        return {"default": "", "endpoints": {}}

    def _load_payload(self) -> dict[str, Any]:
        data = _load_json(self._file_func()) or self._empty_payload()
        endpoints = data.get("endpoints", {})
        if not isinstance(endpoints, dict):
            endpoints = {}
        default = str(data.get("default", "") or "")
        return {"default": default, "endpoints": endpoints}

    # ── public API (same signatures as before) ──────────────────────────

    def list(self) -> list[dict[str, Any]]:
        data = self._load_payload()
        default_name = str(data.get("default", "") or "")
        return [
            self._normalize(name, raw, default_name=default_name)
            for name, raw in sorted(data.get("endpoints", {}).items())
            if isinstance(raw, dict)
        ]

    def get(self, name: str) -> dict[str, Any] | None:
        data = self._load_payload()
        raw = data.get("endpoints", {}).get(name)
        if not isinstance(raw, dict):
            return None
        return self._normalize(name, raw, default_name=str(data.get("default", "") or ""))

    def get_default(self) -> dict[str, Any] | None:
        data = self._load_payload()
        default_name = str(data.get("default", "") or "")
        if default_name:
            found = self.get(default_name)
            if found is not None:
                return found
        endpoints = self.list()
        return endpoints[0] if endpoints else None

    def set(self, name: str, endpoint: dict[str, Any], *, make_default: bool = False) -> dict[str, Any]:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Nom d'endpoint obligatoire")
        if self._validate is not None:
            self._validate(normalized_name, endpoint)
        data = self._load_payload()
        endpoints = dict(data.get("endpoints", {}))
        endpoints[normalized_name] = endpoint
        default = normalized_name if make_default or not data.get("default") else str(data.get("default", "") or "")
        _write_json(self._file_func(), {"default": default, "endpoints": endpoints})
        return self._normalize(normalized_name, endpoint, default_name=default)
