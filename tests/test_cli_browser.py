import json
import subprocess

from typer.testing import CliRunner

from emploi.cli import app


runner = CliRunner()


def test_browser_status_prints_json(monkeypatch):
    def fake_run(args, **kwargs):
        assert args == [
            'managed-browser',
            'status',
            '--site',
            'france-travail',
            '--profile',
            'emploi',
            '--json',
        ]
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({'ok': True, 'state': 'ready'}), stderr='')

    monkeypatch.setattr(subprocess, 'run', fake_run)

    result = runner.invoke(app, ['browser', 'status'])

    assert result.exit_code == 0
    assert 'ready' in result.stdout
    assert 'france-travail' in result.stdout
    assert 'emploi' in result.stdout


def test_browser_open_accepts_url_and_profile_options(monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen['args'] = args
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({'ok': True, 'url': 'https://example.test'}), stderr='')

    monkeypatch.setattr(subprocess, 'run', fake_run)

    result = runner.invoke(
        app,
        [
            'browser',
            'open',
            'https://example.test',
            '--site',
            'custom-site',
            '--profile',
            'custom-profile',
        ],
    )

    assert result.exit_code == 0
    assert seen['args'] == [
        'managed-browser',
        'open',
        '--site',
        'custom-site',
        '--profile',
        'custom-profile',
        '--url',
        'https://example.test',
        '--json',
    ]
    assert 'https://example.test' in result.stdout


def test_browser_snapshot_and_checkpoint_commands(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({'ok': True, 'id': len(calls)}), stderr='')

    monkeypatch.setattr(subprocess, 'run', fake_run)

    snapshot = runner.invoke(app, ['browser', 'snapshot', '--label', 'jobs'])
    checkpoint = runner.invoke(app, ['browser', 'checkpoint', 'after-login'])

    assert snapshot.exit_code == 0
    assert checkpoint.exit_code == 0
    assert calls[0] == [
        'managed-browser',
        'snapshot',
        '--site',
        'france-travail',
        '--profile',
        'emploi',
        '--label',
        'jobs',
        '--json',
    ]
    assert calls[1] == [
        'managed-browser',
        'checkpoint',
        '--site',
        'france-travail',
        '--profile',
        'emploi',
        '--name',
        'after-login',
        '--json',
    ]


def test_browser_unavailable_shows_clear_error_without_traceback(monkeypatch):
    def fake_run(args, **kwargs):
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(subprocess, 'run', fake_run)

    result = runner.invoke(app, ['browser', 'status'])

    assert result.exit_code != 0
    assert 'Managed Browser command not found' in result.stdout
    assert 'Traceback' not in result.stdout
    assert isinstance(result.exception, SystemExit)
