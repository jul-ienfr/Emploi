"""Unit tests for emploi.france_travail.api_client."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

from emploi.france_travail.api_client import FranceTravailAPIClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TOKEN_RESPONSE = json.dumps(
    {
        "access_token": "test-token-abc123",
        "token_type": "Bearer",
        "expires_in": 1200,
    }
).encode()

SEARCH_RESPONSE = json.dumps(
    {
        "resultats": [
            {"id": "OFR1", "intitule": "Dev Python"},
            {"id": "OFR2", "intitule": "Data Engineer"},
        ],
        "count": 2,
    }
).encode()

DETAIL_RESPONSE = json.dumps(
    {
        "id": "OFR1",
        "intitule": "Dev Python",
        "description": "Poste senior Python",
    }
).encode()


def _make_response(data: bytes, status: int = 200) -> urllib.request.Request:
    """Build a mock ``urllib.request.urlopen`` return value."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = data
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _make_http_error(code: int, msg: str = "Error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://test",
        code=code,
        msg=msg,
        hdrs=None,
        fp=None,
    )


# ---------------------------------------------------------------------------
# Token fetch & caching
# ---------------------------------------------------------------------------


class TestTokenFetch:
    @patch("emploi.france_travail.api_client.urllib.request.urlopen")
    def test_token_fetched(self, mock_urlopen):
        mock_urlopen.return_value = _make_response(TOKEN_RESPONSE)
        client = FranceTravailAPIClient("id", "secret")
        token = client._get_token()
        assert token == "test-token-abc123"
        mock_urlopen.assert_called_once()

    @patch("emploi.france_travail.api_client.urllib.request.urlopen")
    def test_token_cached(self, mock_urlopen):
        mock_urlopen.return_value = _make_response(TOKEN_RESPONSE)
        client = FranceTravailAPIClient("id", "secret")
        t1 = client._get_token()
        t2 = client._get_token()
        assert t1 == t2
        # Only one HTTP call (token cached).
        assert mock_urlopen.call_count == 1

    @patch("emploi.france_travail.api_client.urllib.request.urlopen")
    def test_token_refreshed_after_expiry(self, mock_urlopen):
        mock_urlopen.return_value = _make_response(TOKEN_RESPONSE)
        client = FranceTravailAPIClient("id", "secret")
        client._get_token()
        # Simulate expiry in the past.
        client._token_expires_at = time.time() - 10
        mock_urlopen.return_value = _make_response(TOKEN_RESPONSE)
        client._get_token()
        assert mock_urlopen.call_count == 2


# ---------------------------------------------------------------------------
# Search offers
# ---------------------------------------------------------------------------


class TestSearchOffers:
    @patch("emploi.france_travail.api_client.urllib.request.urlopen")
    def test_search_basic(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _make_response(TOKEN_RESPONSE),
            _make_response(SEARCH_RESPONSE),
        ]
        client = FranceTravailAPIClient("id", "secret")
        results = client.search_offers("Python")
        assert len(results) == 2
        assert results[0]["id"] == "OFR1"

    @patch("emploi.france_travail.api_client.urllib.request.urlopen")
    def test_search_with_all_params(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _make_response(TOKEN_RESPONSE),
            _make_response(SEARCH_RESPONSE),
        ]
        client = FranceTravailAPIClient("id", "secret")
        results = client.search_offers(
            "Python",
            location="75001",
            contract_type="CDI",
            radius=10,
            page=2,
            limit=5,
        )
        assert len(results) == 2
        # Verify the search URL contains the expected params.
        search_call = mock_urlopen.call_args_list[1]
        url = search_call[0][0].full_url
        assert "motsCles=Python" in url
        assert "lieu=75001" in url
        assert "typeContrat=CDI" in url
        assert "rayon=10" in url
        # page=2, limit=5 -> range=10-14
        assert "range=10-14" in url


# ---------------------------------------------------------------------------
# Offer detail
# ---------------------------------------------------------------------------


class TestOfferDetail:
    @patch("emploi.france_travail.api_client.urllib.request.urlopen")
    def test_get_detail(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _make_response(TOKEN_RESPONSE),
            _make_response(DETAIL_RESPONSE),
        ]
        client = FranceTravailAPIClient("id", "secret")
        detail = client.get_offer_detail("OFR1")
        assert detail["id"] == "OFR1"
        assert detail["intitule"] == "Dev Python"


# ---------------------------------------------------------------------------
# Retry on transient errors
# ---------------------------------------------------------------------------


class TestRetryOnTransientErrors:
    @patch("emploi.france_travail.api_client.urllib.request.urlopen")
    def test_search_retries_on_urlerror(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _make_response(TOKEN_RESPONSE),
            urllib.error.URLError("connection reset"),
            _make_response(SEARCH_RESPONSE),
        ]
        client = FranceTravailAPIClient("id", "secret")
        results = client.search_offers("test")
        assert len(results) == 2
        assert mock_urlopen.call_count == 3

    @patch("emploi.france_travail.api_client.urllib.request.urlopen")
    def test_detail_retries_on_500(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _make_response(TOKEN_RESPONSE),
            _make_http_error(500, "Internal Server Error"),
            _make_response(DETAIL_RESPONSE),
        ]
        client = FranceTravailAPIClient("id", "secret")
        detail = client.get_offer_detail("OFR1")
        assert detail["id"] == "OFR1"


# ---------------------------------------------------------------------------
# Token refresh on 401
# ---------------------------------------------------------------------------


class TestTokenRefreshOn401:
    @patch("emploi.france_travail.api_client.urllib.request.urlopen")
    def test_search_refreshes_token_on_401(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _make_response(TOKEN_RESPONSE),  # initial token
            _make_http_error(401, "Unauthorized"),
            _make_response(TOKEN_RESPONSE),  # refresh token
            _make_response(SEARCH_RESPONSE),  # retry search
        ]
        client = FranceTravailAPIClient("id", "secret")
        results = client.search_offers("test")
        assert len(results) == 2
        assert mock_urlopen.call_count == 4

    @patch("emploi.france_travail.api_client.urllib.request.urlopen")
    def test_detail_refreshes_token_on_401(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _make_response(TOKEN_RESPONSE),
            _make_http_error(401, "Unauthorized"),
            _make_response(TOKEN_RESPONSE),
            _make_response(DETAIL_RESPONSE),
        ]
        client = FranceTravailAPIClient("id", "secret")
        detail = client.get_offer_detail("OFR1")
        assert detail["id"] == "OFR1"
