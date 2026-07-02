"""Dashboard authentication middleware."""

from __future__ import annotations

import hmac
import os
import time
from functools import wraps

from emploi.logging import get_logger

logger = get_logger("dashboard.auth")

_rate_limits: dict[str, list[float]] = {}
_RATE_LIMIT = 100  # requests per minute


def _get_api_key() -> str | None:
    return os.environ.get("EMPLOI_DASHBOARD_API_KEY", "").strip() or None


def _get_auth_password() -> str | None:
    return os.environ.get("EMPLOI_DASHBOARD_AUTH", "").strip() or None


def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    if ip not in _rate_limits:
        _rate_limits[ip] = []
    _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < 60]
    if len(_rate_limits[ip]) >= _RATE_LIMIT:
        return False
    _rate_limits[ip].append(now)
    return True


def check_auth(f):
    """Decorator that checks API key or basic auth."""

    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import jsonify, request

        api_key = _get_api_key()
        auth_password = _get_auth_password()

        # No auth configured = open access
        if not api_key and not auth_password:
            if not _check_rate_limit(request.remote_addr):
                return jsonify({"error": "Rate limit exceeded"}), 429
            return f(*args, **kwargs)

        # Check API key
        if api_key:
            provided_key = request.headers.get("X-API-Key") or request.args.get("api_key", "")
            if hmac.compare_digest(provided_key, api_key):
                return f(*args, **kwargs)

        # Check basic auth
        if auth_password:
            auth = request.authorization
            if auth and hmac.compare_digest(auth.password, auth_password):
                return f(*args, **kwargs)

        logger.warning("Unauthorized access attempt from %s", request.remote_addr)
        return jsonify({"error": "Unauthorized"}), 401

    return decorated


def setup_auth(app):
    """Register auth middleware on a Flask app."""
    api_key = _get_api_key()
    auth_password = _get_auth_password()

    if not api_key and not auth_password:
        logger.info("No auth configured — dashboard is open")
        return

    @app.before_request
    def _check_auth():
        from flask import jsonify, request

        # Skip auth for health check
        if request.path == "/health":
            return

        # Skip auth for static files
        if request.path.startswith("/static/"):
            return

        # Rate limit all requests
        if not _check_rate_limit(request.remote_addr):
            return jsonify({"error": "Rate limit exceeded"}), 429

        # Check API key
        if api_key:
            provided_key = request.headers.get("X-API-Key") or request.args.get("api_key", "")
            if hmac.compare_digest(provided_key, api_key):
                return

        # Check basic auth
        if auth_password:
            auth = request.authorization
            if auth and hmac.compare_digest(auth.password, auth_password):
                return

        logger.warning("Unauthorized access attempt from %s to %s", request.remote_addr, request.path)
        return jsonify({"error": "Unauthorized"}), 401
