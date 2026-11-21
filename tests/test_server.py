"""The web server and the setup page's checks, with fakes standing in for the devices."""

import asyncio
from types import SimpleNamespace as NS

import pytest
from aiohttp.test_utils import TestClient, TestServer
from franklinwh.client import InvalidCredentialsException
from helpers import NOON

from solar_kiosk import checks, server
from solar_kiosk import config as cfg
from solar_kiosk.discover import FoundGateway, FoundStation


async def fake_build(c, config_path=None, interval=None, on_change=None):
    """A live poller that reads nothing real: the demo, standing in for the devices."""
    from solar_kiosk.runner import build_demo

    return build_demo("sunny", interval=60), []


def with_client(tmp_path, test, *, setup=False):
    async def go():
        path = tmp_path / "conf" / "config.toml"
        if setup:
            cfg.save(cfg.Config(franklin=cfg.FranklinConfig("you@example.com", "pw", "EXAMPLE-AGATE"),
                                state_dir=tmp_path / "state"), path)
        app = server.App(path, build=fake_build, address=lambda: "192.0.2.5")
        await app.start()
        client = TestClient(TestServer(server.make_web_app(app)))
        await client.start_server()
        try:
            for _ in range(50):  # the first reading
                if app.snapshot:
                    break
                await asyncio.sleep(0.02)
            return await test(client, app)
        finally:
            await client.close()
            await app.stop()

    return asyncio.run(go())


PASSWORD = "example-settings-pw"


async def paired(client, app):
    """Sign this test browser in, creating the settings password on the first visit."""
    path = "/api/auth/login" if app.auth.has_password else "/api/auth/create"
    r = await (await client.post(path, json={"password": PASSWORD, "confirm": PASSWORD})).json()
    assert r == {"ok": True}


def test_demo_until_set_up(tmp_path):
    async def test(client, app):
        d = await (await client.get("/api/now")).json()
        assert d["mode"] == "demo" and d["snapshot"]["solar"]["watts"] > 0
        assert d["setup_address"] == "192.0.2.5:8080"
        assert 'xmlns="http://www.w3.org/2000/svg"' in await (await client.get("/qr.svg")).text()
        assert "Solar" in await (await client.get("/")).text()
        assert app.setup_url() == "http://192.0.2.5:8080/setup"  # the QR is only a link, not a key
        health = await (await client.get("/api/health")).json()
        assert health["ok"] and health["mode"] == "demo" and health["version"]

    with_client(tmp_path, test)


def test_first_visitor_creates_the_password(tmp_path):
    async def test(client, app):
        assert await (await client.get("/api/setup/state")).json() == {"locked": "create", "local": True}
        assert (await client.post("/api/setup/save", json={})).status == 403
        assert (await client.post("/api/setup/franklin", json={})).status == 403
        short = await (await client.post("/api/auth/create", json={"password": "short", "confirm": "short"})).json()
        assert not short["ok"] and "8 characters" in short["error"]
        mismatch = await (await client.post("/api/auth/create", json={"password": PASSWORD, "confirm": "x"})).json()
        assert "match" in mismatch["error"]
        await paired(client, app)
        assert "units" in await (await client.get("/api/setup/state")).json()
        again = await (await client.post("/api/auth/create", json={"password": PASSWORD, "confirm": PASSWORD})).json()
        assert not again["ok"]  # only the first visitor gets to create it
        stored = (app.config_path.with_name("auth.json")).read_text()
        assert PASSWORD not in stored and (app.config_path.with_name("auth.json").stat().st_mode & 0o777) == 0o600

    with_client(tmp_path, test)


def test_other_browsers_need_the_password(tmp_path):
    async def test(client, app):
        await paired(client, app)
        client.session.cookie_jar.clear()  # now a different browser
        assert (await (await client.get("/api/setup/state")).json())["locked"] == "enter"
        wrong = await (await client.post("/api/auth/login", json={"password": "nope"})).json()
        assert not wrong["ok"]
        await paired(client, app)
        assert "units" in await (await client.get("/api/setup/state")).json()

    with_client(tmp_path, test)


def test_wrong_passwords_are_limited(tmp_path):
    async def test(client, app):
        await paired(client, app)
        client.session.cookie_jar.clear()
        for _ in range(5):
            await client.post("/api/auth/login", json={"password": "nope"})
        r = await (await client.post("/api/auth/login", json={"password": PASSWORD})).json()
        assert not r["ok"] and "Too many" in r["error"]

    with_client(tmp_path, test)


def test_changing_the_password_signs_out_everyone_else(tmp_path):
    async def test(client, app):
        await paired(client, app)
        old_cookie = app.auth.session_key
        r = await client.post("/api/auth/change", json={"old": PASSWORD, "new": "another-password-1",
                                                        "confirm": "another-password-1"})
        assert (await r.json())["ok"]
        assert "units" in await (await client.get("/api/setup/state")).json()  # this browser stays in
        client.session.cookie_jar.clear()
        other = await client.get("/api/setup/state", headers={"Cookie": f"{server.COOKIE}={old_cookie}"})
        assert (await other.json())["locked"] == "enter"

    with_client(tmp_path, test)


def test_sign_out(tmp_path):
    async def test(client, app):
        await paired(client, app)
        await client.post("/api/auth/logout")
        assert (await (await client.get("/api/setup/state")).json())["locked"] == "enter"

    with_client(tmp_path, test)


def test_reset_only_from_the_pi_itself(tmp_path, monkeypatch):
    async def test(client, app):
        await paired(client, app)
        monkeypatch.setattr(server, "is_local", lambda request: False)
        assert (await client.post("/api/auth/reset")).status == 403
        assert app.auth.has_password
        monkeypatch.setattr(server, "is_local", lambda request: True)
        assert (await (await client.post("/api/auth/reset")).json())["ok"]
        assert (await (await client.get("/api/setup/state")).json())["locked"] == "create"

    with_client(tmp_path, test)


def test_password_survives_a_restart(tmp_path):
    async def first(client, app):
        await paired(client, app)

    async def second(client, app):
        assert app.auth.has_password and app.auth.check(PASSWORD)

    with_client(tmp_path, first)
    with_client(tmp_path, second)


def test_save_switches_from_demo_to_live(tmp_path):
    async def test(client, app):
        await paired(client, app)
        body = {"enphase": None, "franklin": {"email": "you@example.com", "password": "pw"},
                "tempest": {"enabled": False}, "units": "metric"}
        r = await (await client.post("/api/setup/save", json=body)).json()
        assert r == {"ok": True, "mode": "live"}
        saved = cfg.load(app.config_path)
        assert saved.franklin.email == "you@example.com" and saved.enphase is None and saved.units == "metric"
        state = await (await client.get("/api/setup/state")).json()
        assert state["franklin"]["has_password"] and "pw" not in str(state)
        assert state["enphase"]["off"] and not state["franklin"]["off"]  # reopening Settings shows it on
        assert app.config.franklin_missing != "Off in the settings"

    with_client(tmp_path, test)


def test_blank_password_keeps_the_saved_one(tmp_path):
    async def test(client, app):
        await paired(client, app)
        body = {"franklin": {"email": "you@example.com", "password": ""}, "enphase": None}
        assert (await (await client.post("/api/setup/save", json=body)).json())["ok"]
        assert cfg.load(app.config_path).franklin.password == "pw"

    with_client(tmp_path, test, setup=True)


def test_save_without_a_password_is_refused(tmp_path):
    async def test(client, app):
        await paired(client, app)
        r = await client.post("/api/setup/save", json={"enphase": {"username": "you@example.com"}, "franklin": None})
        assert r.status == 400 and "Enphase" in (await r.json())["error"]

    with_client(tmp_path, test)


def test_live_problems_shown_on_the_screen(tmp_path):
    async def test(client, app):
        assert app.mode == "live"
        app.snapshot = NS(enphase_error=None, franklin_error="Couldn't log in", franklin_updated=None)
        assert app.attention() == ["Can't read the aGate: Couldn't log in"]

    with_client(tmp_path, test, setup=True)


# --- the checks themselves -----------------------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


class Envoy:
    def __init__(self, host):
        self.host, self.auth, self.serial_number = host, None, "000000000001"
        self.production_meter_type = "production"

    async def setup(self):
        pass

    async def authenticate(self, username=None, password=None, token=None):
        from pyenphase.exceptions import EnvoyAuthenticationError

        if password != "pw":
            raise EnvoyAuthenticationError("bad")
        self.auth = NS(token="t")

    async def update(self):
        meter = NS(watts_now=5200, watt_hours_lifetime=1)
        return NS(system_production=meter, system_consumption=None, system_net_consumption=None, inverters={})

    async def close(self):
        pass


def found(*gws):
    async def discover():
        return list(gws)
    return discover


def test_enphase_check_finds_and_connects(tmp_path):
    r = run(checks.check_enphase("you@example.com", "pw", state_dir=tmp_path,
                                 discover=found(FoundGateway("192.0.2.10", "000000000001")), envoy_factory=Envoy))
    assert r.ok and r.data == {"host": "192.0.2.10", "serial": "000000000001"} and "5.2 kW" in r.detail


def test_enphase_check_wrong_password(tmp_path):
    r = run(checks.check_enphase("you@example.com", "nope", state_dir=tmp_path,
                                 discover=found(FoundGateway("192.0.2.10", "1")), envoy_factory=Envoy))
    assert not r.ok and r.login_failed and "didn't accept" in r.title


def test_enphase_check_nothing_found_or_several(tmp_path):
    r = run(checks.check_enphase("u", "pw", state_dir=tmp_path, discover=found()))
    assert not r.ok and r.data == {"not_found": True} and "same home network" in r.detail
    r = run(checks.check_enphase("u", "pw", state_dir=tmp_path,
                                 discover=found(FoundGateway("192.0.2.10", "1"), FoundGateway("192.0.2.11", "2"))))
    assert not r.ok and len(r.data["choices"]) == 2


def test_franklin_check(tmp_path):
    from franklinwh import GridStatus
    from franklinwh.client import Current, Stats, Totals

    def client(fail=None):
        class Client:
            def __init__(self, email, password, gateway):
                self.gateway = gateway

            async def refresh_token(self):
                if fail:
                    raise fail

            async def get_home_gateway_list(self):
                return [{"id": "EXAMPLE-AGATE"}]

            async def get_stats(self):
                cur = Current(1, 0, False, 0, 0, 1, 64, 0, 0, 0, GridStatus.NORMAL)
                return Stats(cur, Totals(*[0] * 11))

        return Client

    r = run(checks.check_franklin("you@example.com", "pw", client_factory=client()))
    assert r.ok and r.data == {"gateway_id": "EXAMPLE-AGATE"} and "64%" in r.detail
    r = run(checks.check_franklin("you@example.com", "x", client_factory=client(InvalidCredentialsException("no"))))
    assert not r.ok and r.login_failed
    r = run(checks.check_franklin("you@example.com", "pw", client_factory=client(OSError("offline"))))
    assert not r.ok and "internet" in r.detail


def test_tempest_check():
    async def one(**kw):
        return [FoundStation("ST-00000001", "HB-00000001", "192.0.2.30")]

    async def none(**kw):
        return []

    assert run(checks.check_tempest(find=one)).data["serial"] == "ST-00000001"
    assert "skip this" in run(checks.check_tempest(find=none)).detail


def test_try_limiter_protects_the_account():
    now = [0.0]
    lim = checks.TryLimiter(max_failures=3, window_s=900, clock=lambda: now[0])
    bad = checks.Result(False, "no", login_failed=True)
    for _ in range(3):
        assert lim.blocked("franklin", "FranklinWH") is None
        lim.record("franklin", bad)
    blocked = lim.blocked("franklin", "FranklinWH")
    assert blocked and "15 min" in blocked.detail and "lock" in blocked.detail
    now[0] = 901
    assert lim.blocked("franklin", "FranklinWH") is None


@pytest.mark.parametrize("command", [["serve", "--help"], ["watch", "--help"]])
def test_cli_help(command, capsys):
    with pytest.raises(SystemExit):
        from solar_kiosk.__main__ import main
        main(command)
    assert "usage" in capsys.readouterr().out
