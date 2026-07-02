"""Tests for the monitoring module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from emploi.monitoring import report_cycle_result, send_alert


def test_send_alert_no_configured_channels():
    """send_alert should not raise when no channels are configured."""
    with patch.dict("os.environ", {}, clear=True):
        send_alert("Test title", "Test details")


def test_send_alert_webhook(monkeypatch):
    """send_alert should POST to webhook URL when configured."""
    monkeypatch.setenv("EMPLOI_ALERT_WEBHOOK_URL", "https://hooks.test/alert")

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda s, *a: False
        mock_urlopen.return_value = mock_resp

        send_alert("Test alert", "Test body")

        assert mock_urlopen.called
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert req.full_url == "https://hooks.test/alert"
        body = json.loads(req.data)
        assert body["title"] == "Test alert"
        assert body["details"] == "Test body"
        assert body["source"] == "emploi-daemon"


def test_report_cycle_result_no_errors():
    """Successful cycle should not send alert."""
    with patch("emploi.monitoring.send_alert") as mock_alert:
        report_cycle_result(total_offers=10, created=3, updated=7, errors=[], duration_seconds=15.5)
        mock_alert.assert_not_called()


def test_report_cycle_result_with_errors():
    """Cycle with errors should send alert."""
    with patch("emploi.monitoring.send_alert") as mock_alert:
        report_cycle_result(
            total_offers=5,
            created=2,
            updated=3,
            errors=["profile1: timeout", "profile2: HTTP 500"],
            duration_seconds=30.0,
        )
        mock_alert.assert_called_once()
        call_kwargs = mock_alert.call_args[1]
        assert "2 erreur" in call_kwargs["title"]
        assert "profile1: timeout" in call_kwargs["details"]


def test_report_cycle_result_includes_duration():
    """Alert details should include duration."""
    with patch("emploi.monitoring.send_alert") as mock_alert:
        report_cycle_result(
            total_offers=0,
            created=0,
            updated=0,
            errors=["fatal crash"],
            duration_seconds=123.4,
        )
        details = mock_alert.call_args[1]["details"]
        assert "123.4s" in details
