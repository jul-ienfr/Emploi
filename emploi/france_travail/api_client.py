"""REST API client for France Travail (offres d'emploi)."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from emploi.retry import with_retry

logger = logging.getLogger(__name__)

BASE_URL = "https://api.francetravail.io"
TOKEN_URL = f"{BASE_URL}/connexion/oauth2/access_token"
SEARCH_URL = f"{BASE_URL}/partenaire/offresdemploi/v2/offres/search"
DETAIL_URL = f"{BASE_URL}/partenaire/offresdemploi/v2/offres"

# Token lifetime is ~20 min; refresh 2 min early.
_TOKEN_MARGIN_SECONDS = 120


class FranceTravailAPIClient:
    """Thin REST client for the France Travail Offres d'emploi API.

    Uses only stdlib (``urllib.request``) -- no third-party HTTP library needed.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        scope: str = "api_offresdemploiv2",
        base_url: str = BASE_URL,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self._base_url = base_url.rstrip("/")

        # Token cache
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        """Return a valid access_token, fetching a new one if needed."""
        now = time.time()
        if self._access_token and now < self._token_expires_at:
            return self._access_token

        token_url = f"{self._base_url}/connexion/oauth2/access_token"
        payload = urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": self.scope,
            }
        ).encode()

        req = urllib.request.Request(
            token_url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())

        self._access_token = body["access_token"]
        # expires_in is in seconds; default to 1100 (~18 min) if absent.
        expires_in = int(body.get("expires_in", 1100))
        self._token_expires_at = time.time() + expires_in - _TOKEN_MARGIN_SECONDS
        logger.debug("Acquired France Travail token (expires in %ds)", expires_in)
        return self._access_token  # type: ignore[return-value]

    def _invalidate_token(self) -> None:
        """Drop cached token so the next call fetches a fresh one."""
        self._access_token = None
        self._token_expires_at = 0.0

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        url: str,
        params: dict[str, str | int] | None = None,
        *,
        _retry_on_401: bool = True,
    ) -> dict:
        """Issue an authenticated GET and return parsed JSON."""
        if params:
            url = f"{url}?{urlencode(params)}"

        token = self._get_token()
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and _retry_on_401:
                logger.info("Got 401, refreshing token and retrying")
                self._invalidate_token()
                return self._request(url, _retry_on_401=False)
            raise

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @with_retry(max_retries=3, retryable_exceptions=(urllib.error.URLError, OSError, ConnectionError))  # type: ignore[arg-type]
    def search_offers(
        self,
        query: str | None = None,
        *,
        location: str | None = None,
        contract_type: str | None = None,
        radius: int | None = None,
        page: int = 0,
        limit: int = 20,
    ) -> list[dict]:
        """Search job offers.

        Parameters
        ----------
        query:
            Free-text keyword search (``motsCles`` param).
        location:
            Postal code or commune code (``lieu`` param).
        contract_type:
            Contract type filter (``typeContrat`` param, e.g. ``"CDI"``).
        radius:
            Search radius in km around *location*.
        page:
            Zero-based page index.
        limit:
            Number of results per page (API default 20, max 150).

        Returns
        -------
        list[dict]
            List of offer dicts from the ``resultats`` key.
        """
        params: dict[str, str | int] = {}
        if query:
            params["motsCles"] = query
        if location:
            params["lieu"] = location
        if contract_type:
            params["typeContrat"] = contract_type
        if radius is not None:
            params["rayon"] = radius

        # Pagination: API uses range-based pagination.
        start = page * limit
        params["range"] = f"{start}-{start + limit - 1}"

        data = self._request(SEARCH_URL, params)
        return data.get("resultats", [])

    @with_retry(max_retries=3, retryable_exceptions=(urllib.error.URLError, OSError, ConnectionError))  # type: ignore[arg-type]
    def get_offer_detail(self, offer_id: str | int) -> dict:
        """Fetch full details for a single offer.

        Parameters
        ----------
        offer_id:
            The offer identifier (``id`` field from search results).

        Returns
        -------
        dict
            The offer detail object.
        """
        url = f"{DETAIL_URL}/{offer_id}"
        return self._request(url)
