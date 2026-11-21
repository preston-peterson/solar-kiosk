import shutil
from pathlib import Path

import pytest

from solar_kiosk import config as cfg
from solar_kiosk.__main__ import main

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def conf(tmp_path):
    """The repo's example config and secrets, copied as a user would."""
    shutil.copy(REPO / "config.example.toml", tmp_path / "config.toml")
    shutil.copy(REPO / "secrets-example.toml", tmp_path / "secrets.toml")
    (tmp_path / "secrets.toml").chmod(0o600)
    return tmp_path / "config.toml"


def test_example_files_load(conf):
    c = cfg.load(conf)
    assert (c.enphase.host, c.enphase.username, c.enphase.poll_s) == ("192.0.2.10", "you@example.com", 15)
    assert (c.franklin.gateway_id, c.franklin.email, c.franklin.poll_s) == ("", "you@example.com", 30)
    assert (c.tempest.enabled, c.tempest.port, c.units) == (True, 50222, "us")


def test_secrets_readable_by_others_are_refused(conf):
    (conf.parent / "secrets.toml").chmod(0o644)
    with pytest.raises(cfg.ConfigError, match="chmod 600"):
        cfg.load(conf)


def test_missing_franklin_login(conf):
    (conf.parent / "secrets.toml").write_text('[enphase]\nusername = "u"\npassword = "p"\n')
    with pytest.raises(cfg.ConfigError, match="franklin"):
        cfg.load(conf)


def test_franklin_off_and_metric(conf):
    text = conf.read_text().replace('source = "cloud"', 'source = "off"').replace('units = "us"', 'units = "metric"')
    conf.write_text(text)
    c = cfg.load(conf)
    assert c.franklin is None and c.franklin_missing == "Off in the settings" and c.units == "metric"


@pytest.mark.parametrize("bad", ['units = "imperial"', 'source = "modbus"'])
def test_bad_values(conf, bad):
    key = bad.split(" = ")[0]
    conf.write_text("\n".join(bad if line.startswith(key + " ") else line for line in conf.read_text().splitlines()))
    with pytest.raises(cfg.ConfigError):
        cfg.load(conf)


def test_missing_config_file(tmp_path):
    with pytest.raises(cfg.ConfigError, match="not found"):
        cfg.load(tmp_path / "nope.toml")


@pytest.mark.parametrize("scenario", ["sunny", "outage", "offline"])
def test_demo_prints_a_reading(capsys, scenario):
    assert main(["watch", "--demo", scenario, "--once"]) == 0
    out = capsys.readouterr().out
    assert "Solar" in out and "Solar today" in out
    if scenario == "outage":
        assert "Grid DOWN" in out and "lightning" in out
    if scenario == "offline":
        assert "aGate: FranklinWH cloud not responding" in out


def test_config_problem_exits_2(tmp_path, capsys):
    assert main(["watch", "--config", str(tmp_path / "nope.toml"), "--once"]) == 2
    assert "Settings problem" in capsys.readouterr().err


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "solar-kiosk" / "config.toml"
    c = cfg.Config(
        enphase=cfg.EnphaseConfig(host="192.0.2.10", serial="000000000001", username="you@example.com", password='p"w\\x'),
        franklin=cfg.FranklinConfig(email="you@example.com", password="pw", gateway_id="EXAMPLE-AGATE"),
        tempest=cfg.TempestConfig(serial="ST-00000001"), units="metric", state_dir=tmp_path / "state",
    )
    cfg.save(c, path)
    assert cfg.load(path) == c
    assert (cfg.secrets_path(path).stat().st_mode & 0o777) == 0o600
    assert "you@example.com" not in path.read_text()  # logins only in the secrets file


def test_logins_only_means_find_the_gateway(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("")
    cfg.secrets_path(path).write_text('[enphase]\nusername = "u"\npassword = "p"\n')
    cfg.secrets_path(path).chmod(0o600)
    c = cfg.load(path)
    assert c.enphase.host == "" and c.franklin is None and c.franklin_missing == "Not set up yet"


def test_turned_off_stays_off(tmp_path):
    path = tmp_path / "config.toml"
    c = cfg.Config(enphase_missing="Off in the settings", state_dir=tmp_path)
    cfg.save(c, path)
    loaded = cfg.load(path)
    assert loaded.enphase is None and loaded.enphase_missing == "Off in the settings"


def test_verbose_never_logs_library_details(capsys):
    import logging

    assert main(["-v", "watch", "--demo", "--once"]) == 0
    assert not logging.getLogger("franklinwh").isEnabledFor(logging.DEBUG)  # it logs its login token at DEBUG
    assert not logging.getLogger("aiohttp.access").isEnabledFor(logging.INFO)


def test_reset_password_command(tmp_path, capsys):
    from solar_kiosk.auth import Auth

    auth = Auth(tmp_path / "auth.json")
    auth.create("example-settings-pw")
    assert main(["reset-password", "--config", str(tmp_path / "config.toml")]) == 0
    assert not (tmp_path / "auth.json").exists() and "creates a new one" in capsys.readouterr().out
