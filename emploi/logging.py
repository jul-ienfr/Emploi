"""Logging configuration for Emploi CLI.

Provides a ``get_logger(name)`` factory and optional file-based logging
to ``~/.local/share/emploi/emploi.log``.

Environment variables:
    EMPLOI_LOG_LEVEL  – root log level (default: WARNING).
    EMPLOI_LOG_FILE   – path to log file (default: ~/.local/share/emploi/emploi.log).
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

_configured = False
_DEFAULT_LOG_DIR = Path.home() / ".local" / "share" / "emploi"
_DEFAULT_LOG_FILE = _DEFAULT_LOG_DIR / "emploi.log"


def _ensure_configured() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    level_name = os.environ.get("EMPLOI_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)

    root = logging.getLogger("emploi")
    root.setLevel(level)

    # Console handler – only if EMPLOI_LOG_LEVEL is DEBUG or lower
    if level <= logging.DEBUG:
        console = logging.StreamHandler()
        console.setLevel(logging.DEBUG)
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"))
        root.addHandler(console)

    # File handler – always attempt
    log_file_raw = os.environ.get("EMPLOI_LOG_FILE", "")
    log_file = Path(log_file_raw) if log_file_raw else _DEFAULT_LOG_FILE
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(file_handler)
    except OSError:
        # If we can't write the log file, just skip it silently.
        pass


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``emploi`` namespace."""
    _ensure_configured()
    return logging.getLogger(f"emploi.{name}")
