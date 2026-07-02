import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from emploi.browser.client import ManagedBrowserClient
from emploi.browser.errors import ManagedBrowserCommandError, ManagedBrowserUnavailableError

# ---------------------------------------------------------------------------
# Helpers — lightweight HTTP server that records requests
# ---------------------------------------------------------------------------


class FakeBrowserServer(BaseHTTPRequestHandler):
    """Minimal server that serves ``GET /managed/profiles/{p}/status`` and
    ``POST /managed/cli/*`` and records all requests."""

    requests: list[dict] = []

    def log_message(self, format, *args):
        pass  # silence logs

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # GET  /managed/profiles/{profile}/status
    def do_GET(self):
        FakeBrowserServer.requests.append({"method": "GET", "path": self.path})
        # Simple status response
        self._send_json(200, {"ok": True, "profile": "emploi-candidature", "site": "france-travail"})

    # POST /managed/cli/open | snapshot | act | checkpoint
    def do_POST(self):
        body = self._read_body()
        FakeBrowserServer.requests.append({"method": "POST", "path": self.path, "body": body})
        self._send_json(200, {"ok": True, **body})


@pytest.fixture(scope="module")
def browser_server():
    """Start a fake Managed Browser HTTP server on a random port."""
    server = HTTPServer(("127.0.0.1", 0), FakeBrowserServer)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("EMPLOI_MANAGED_BROWSER_URL", raising=False)
    monkeypatch.delenv("EMPLOI_MANAGED_BROWSER_TIMEOUT", raising=False)
    FakeBrowserServer.requests.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_status_calls_correct_endpoint(browser_server):
    client = ManagedBrowserClient(base_url=browser_server)
    result = client.status()
    assert result.ok is True
    assert result.payload["profile"] == "emploi-candidature"
    req = FakeBrowserServer.requests[-1]
    assert req["method"] == "GET"
    assert "/managed/profiles/emploi-candidature/status" in req["path"]


def test_status_passes_site_as_query_param(browser_server):
    client = ManagedBrowserClient(base_url=browser_server)
    result = client.status(site="custom-site")
    assert result.ok is True
    req = FakeBrowserServer.requests[-1]
    assert "site=custom-site" in req["path"]


def test_open_posts_correct_body(browser_server):
    client = ManagedBrowserClient(base_url=browser_server)
    result = client.open("https://candidat.francetravail.fr", site="s", profile="p")
    assert result.ok is True
    req = FakeBrowserServer.requests[-1]
    assert req["method"] == "POST"
    assert req["path"] == "/managed/cli/open"
    assert req["body"]["url"] == "https://candidat.francetravail.fr"
    assert req["body"]["site"] == "s"
    assert req["body"]["profile"] == "p"


def test_lifecycle_open_sends_warmup(browser_server):
    client = ManagedBrowserClient(base_url=browser_server)
    result = client.lifecycle_open("https://x.com", site="s", profile="p")
    assert result.ok is True
    req = FakeBrowserServer.requests[-1]
    assert req["body"]["warmup"] is True


def test_console_eval_posts_act_with_evaluate(browser_server):
    client = ManagedBrowserClient(base_url=browser_server)
    result = client.console_eval("document.title", site="s", profile="p")
    assert result.ok is True
    req = FakeBrowserServer.requests[-1]
    assert req["path"] == "/managed/cli/act"
    assert req["body"]["action"] == "evaluate"
    assert req["body"]["params"]["expression"] == "document.title"


def test_snapshot_posts_correct_body(browser_server):
    client = ManagedBrowserClient(base_url=browser_server)
    result = client.snapshot(label="my-label", site="s", profile="p")
    assert result.ok is True
    req = FakeBrowserServer.requests[-1]
    assert req["path"] == "/managed/cli/snapshot"
    assert req["body"]["label"] == "my-label"


def test_checkpoint_posts_correct_body(browser_server):
    client = ManagedBrowserClient(base_url=browser_server)
    result = client.checkpoint("after-login", site="s", profile="p")
    assert result.ok is True
    req = FakeBrowserServer.requests[-1]
    assert req["path"] == "/managed/cli/checkpoint"
    assert req["body"]["reason"] == "after-login"


def test_default_base_url_from_env(monkeypatch, browser_server):
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_URL", browser_server)
    client = ManagedBrowserClient()
    result = client.status()
    assert result.ok is True


def test_timeout_from_env(monkeypatch, browser_server):
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_TIMEOUT", "12.5")
    client = ManagedBrowserClient(base_url=browser_server)
    assert client.timeout == 12.5


def test_explicit_timeout_overrides_env(monkeypatch, browser_server):
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_TIMEOUT", "12.5")
    client = ManagedBrowserClient(base_url=browser_server, timeout=4)
    assert client.timeout == 4.0


def test_invalid_timeout_raises(monkeypatch):
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_TIMEOUT", "slow")
    with pytest.raises(ManagedBrowserCommandError, match="EMPLOI_MANAGED_BROWSER_TIMEOUT"):
        ManagedBrowserClient(base_url="http://localhost:1")


def test_zero_timeout_raises(monkeypatch):
    with pytest.raises(ManagedBrowserCommandError, match="positive"):
        ManagedBrowserClient(base_url="http://localhost:1", timeout=0)


def test_nan_timeout_raises(monkeypatch):
    monkeypatch.setenv("EMPLOI_MANAGED_BROWSER_TIMEOUT", "nan")
    with pytest.raises(ManagedBrowserCommandError, match="finite positive"):
        ManagedBrowserClient(base_url="http://localhost:1")


def test_unreachable_server_raises_unavailable():
    client = ManagedBrowserClient(base_url="http://127.0.0.1:19999", timeout=1)
    with pytest.raises(ManagedBrowserUnavailableError, match="unreachable"):
        client.status()


def test_http_500_raises_command_error(monkeypatch):
    """Server returning a non-200 should raise ManagedBrowserCommandError."""
    with patch("emploi.browser.client.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = HTTPError(
            "http://x", 500, "Server Error", {}, b'{"detail":"Internal error"}'
        )
        client = ManagedBrowserClient(base_url="http://x:1")
        with pytest.raises(ManagedBrowserCommandError, match="HTTP 500"):
            client.status()


def test_invalid_json_response_raises_command_error(monkeypatch):
    fake_response = type("FakeResp", (), {
        "read": lambda self: b"not json",
        "__enter__": lambda self: self,
        "__exit__": lambda self, *a: None,
    })()
    with patch("emploi.browser.client.urlopen", return_value=fake_response):
        client = ManagedBrowserClient(base_url="http://x:1")
        with pytest.raises(ManagedBrowserCommandError, match="Invalid JSON"):
            client.status()


def test_non_dict_json_response_raises_command_error(monkeypatch):
    fake_response = type("FakeResp", (), {
        "read": lambda self: b'"just a string"',
        "__enter__": lambda self: self,
        "__exit__": lambda self, *a: None,
    })()
    with patch("emploi.browser.client.urlopen", return_value=fake_response):
        client = ManagedBrowserClient(base_url="http://x:1")
        with pytest.raises(ManagedBrowserCommandError, match="expected object"):
            client.status()
