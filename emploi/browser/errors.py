from __future__ import annotations


class ManagedBrowserError(RuntimeError):
    """Base error for Managed Browser adapter failures."""


class ManagedBrowserUnavailableError(ManagedBrowserError):
    """Raised when the external Managed Browser command is unavailable."""


class ManagedBrowserCommandError(ManagedBrowserError):
    """Raised when the external command fails or returns invalid output."""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode
