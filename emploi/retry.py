"""Generic retry helper with jittered exponential backoff."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

_DEFAULT_RETRYABLE = (ConnectionError, OSError)


def with_retry(
    func: F | None = None,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: tuple[type[BaseException], ...] = _DEFAULT_RETRYABLE,
) -> F | Callable[[F], F]:
    """Decorator that retries *func* on transient exceptions with exponential backoff.

    Usage::

        @with_retry
        def fetch(): ...

        @with_retry(max_retries=5, retryable_exceptions=(URLError,))
        def fetch(): ...
    """

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: BaseException | None = None
            for attempt in range(1 + max_retries):
                try:
                    return fn(*args, **kwargs)
                except retryable_exceptions as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        break
                    delay = min(base_delay * (2**attempt) + random.uniform(0, 0.5), max_delay)
                    logger.warning(
                        "Retry %d/%d for %s after %.1fs: %s",
                        attempt + 1,
                        max_retries,
                        fn.__qualname__,
                        delay,
                        exc,
                    )
                    time.sleep(delay)
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    if func is not None:
        return decorator(func)
    return decorator
