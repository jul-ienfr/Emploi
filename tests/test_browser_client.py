import json
import subprocess

import pytest

from emploi.browser.client import ManagedBrowserClient
from emploi.browser.errors import ManagedBrowserCommandError, ManagedBrowserUnavailableError


class FakeRunner:
    def __init__(self, stdout='{}', returncode=0, stderr=''):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, self.returncode, stdout=self.stdout, stderr=self.stderr)


def test_status_builds_default_command_and_parses_json(monkeypatch):
    monkeypatch.delenv("EMPLOI_MANAGED_BROWSER_COMMAND", raising=False)
    runner = FakeRunner(stdout=json.dumps({'ok': True, 'profile': 'emploi', 'site': 'france-travail'}))
    client = ManagedBrowserClient(runner=runner)

    result = client.status()

    assert result.ok is True
    assert result.payload['profile'] == 'emploi'
    assert runner.calls[0][0] == [
        'managed-browser',
        'profile',
        'status',
        '--profile',
        'emploi',
        '--site',
        'france-travail',
        '--json',
    ]
    assert runner.calls[0][1]['capture_output'] is True
    assert runner.calls[0][1]['text'] is True
    assert runner.calls[0][1]['check'] is False


def test_open_uses_navigate_command_with_url_and_custom_context(monkeypatch):
    monkeypatch.delenv("EMPLOI_MANAGED_BROWSER_COMMAND", raising=False)
    runner = FakeRunner(stdout=json.dumps({'ok': True, 'url': 'https://candidat.francetravail.fr'}))
    client = ManagedBrowserClient(command='mb', runner=runner)

    result = client.open('https://candidat.francetravail.fr', site='custom-site', profile='custom-profile')

    assert result.ok is True
    assert runner.calls[0][0] == [
        'mb',
        'navigate',
        '--profile',
        'custom-profile',
        '--site',
        'custom-site',
        '--url',
        'https://candidat.francetravail.fr',
        '--json',
    ]



def test_lifecycle_open_uses_lifecycle_open_command(monkeypatch):
    monkeypatch.delenv("EMPLOI_MANAGED_BROWSER_COMMAND", raising=False)
    runner = FakeRunner(stdout=json.dumps({'ok': True, 'url': 'https://candidat.francetravail.fr'}))
    client = ManagedBrowserClient(command='mb', runner=runner)

    result = client.lifecycle_open('https://candidat.francetravail.fr', site='custom-site', profile='custom-profile')

    assert result.ok is True
    assert runner.calls[0][0] == [
        'mb',
        'lifecycle',
        'open',
        '--profile',
        'custom-profile',
        '--site',
        'custom-site',
        '--url',
        'https://candidat.francetravail.fr',
        '--json',
    ]


def test_console_eval_uses_console_eval_command(monkeypatch):
    monkeypatch.delenv("EMPLOI_MANAGED_BROWSER_COMMAND", raising=False)
    runner = FakeRunner(stdout=json.dumps({'ok': True, 'value': []}))
    client = ManagedBrowserClient(command='mb', runner=runner)

    result = client.console_eval('document.title', site='custom-site', profile='custom-profile')

    assert result.ok is True
    assert runner.calls[0][0] == [
        'mb',
        'console',
        'eval',
        '--profile',
        'custom-profile',
        '--site',
        'custom-site',
        '--expression',
        'document.title',
        '--json',
    ]


def test_snapshot_and_checkpoint_command_construction(monkeypatch):
    monkeypatch.delenv("EMPLOI_MANAGED_BROWSER_COMMAND", raising=False)
    runner = FakeRunner(stdout=json.dumps({'ok': True, 'path': '/tmp/snapshot.json'}))
    client = ManagedBrowserClient(command='mb', runner=runner)

    snapshot = client.snapshot(label='search-results')
    checkpoint = client.checkpoint('after-login')

    assert snapshot.payload['path'] == '/tmp/snapshot.json'
    assert runner.calls[0][0] == [
        'mb',
        'snapshot',
        '--profile',
        'emploi',
        '--site',
        'france-travail',
        '--json',
    ]
    assert runner.calls[1][0] == [
        'mb',
        'storage',
        'checkpoint',
        '--profile',
        'emploi',
        '--site',
        'france-travail',
        '--reason',
        'after-login',
        '--json',
    ]


def test_command_from_environment(monkeypatch):
    runner = FakeRunner(stdout=json.dumps({'ok': True}))
    monkeypatch.setenv('EMPLOI_MANAGED_BROWSER_COMMAND', 'custom-managed-browser')

    ManagedBrowserClient(runner=runner).status()

    assert runner.calls[0][0][0] == 'custom-managed-browser'



def test_command_from_environment_accepts_shell_like_node_script(monkeypatch):
    runner = FakeRunner(stdout=json.dumps({'ok': True}))
    monkeypatch.setenv('EMPLOI_MANAGED_BROWSER_COMMAND', 'node /opt/camofox/scripts/managed-browser.js')

    ManagedBrowserClient(runner=runner).status()

    assert runner.calls[0][0][:4] == ['node', '/opt/camofox/scripts/managed-browser.js', 'profile', 'status']

def test_unavailable_command_raises_clear_error(monkeypatch):
    monkeypatch.delenv("EMPLOI_MANAGED_BROWSER_COMMAND", raising=False)
    def missing_runner(args, **kwargs):
        raise FileNotFoundError(args[0])

    client = ManagedBrowserClient(runner=missing_runner)

    with pytest.raises(ManagedBrowserUnavailableError) as excinfo:
        client.status()

    assert 'managed-browser' in str(excinfo.value)


def test_nonzero_exit_raises_command_error(monkeypatch):
    monkeypatch.delenv("EMPLOI_MANAGED_BROWSER_COMMAND", raising=False)
    runner = FakeRunner(stdout='', stderr='boom', returncode=2)
    client = ManagedBrowserClient(runner=runner)

    with pytest.raises(ManagedBrowserCommandError) as excinfo:
        client.status()

    assert 'boom' in str(excinfo.value)
    assert excinfo.value.returncode == 2


def test_invalid_json_raises_command_error(monkeypatch):
    monkeypatch.delenv("EMPLOI_MANAGED_BROWSER_COMMAND", raising=False)
    runner = FakeRunner(stdout='not json')
    client = ManagedBrowserClient(runner=runner)

    with pytest.raises(ManagedBrowserCommandError) as excinfo:
        client.status()

    assert 'Invalid JSON' in str(excinfo.value)
