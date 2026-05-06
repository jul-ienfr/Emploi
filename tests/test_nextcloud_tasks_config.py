import importlib


def reload_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    import emploi.config as config

    return importlib.reload(config)


def test_nextcloud_tasks_endpoint_round_trip(monkeypatch, tmp_path):
    config = reload_config(monkeypatch, tmp_path)

    saved = config.set_nextcloud_tasks_endpoint(
        "emploi",
        base_url="https://nextcloud.test",
        calendar="emploi-relances",
        username_pass="nextcloud/username",
        password_pass="nextcloud/password",
        make_default=True,
    )

    assert saved["name"] == "emploi"
    assert saved["base_url"] == "https://nextcloud.test"
    assert saved["calendar"] == "emploi-relances"
    assert saved["calendar_home_url"] == "https://nextcloud.test/remote.php/dav/calendars/{username}/emploi-relances"
    assert saved["username_pass"] == "nextcloud/username"
    assert saved["password_pass"] == "nextcloud/password"
    assert saved["default"] == "✓"

    loaded = config.get_default_nextcloud_tasks_endpoint()
    assert loaded == saved
    assert config.list_nextcloud_tasks_endpoints() == [saved]


def test_nextcloud_tasks_config_requires_base_url(monkeypatch, tmp_path):
    config = reload_config(monkeypatch, tmp_path)

    try:
        config.set_nextcloud_tasks_endpoint("emploi", base_url="", calendar="tasks")
    except ValueError as error:
        assert "URL Nextcloud" in str(error)
    else:
        raise AssertionError("expected ValueError")
