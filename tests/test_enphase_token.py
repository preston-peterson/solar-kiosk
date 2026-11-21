"""Enphase tokens pasted by the owner: reading them, warning before they run out, and the settings that use them."""

import asyncio
import base64
import dataclasses
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from test_server import Envoy, found, paired, with_client

from solar_kiosk import checks, server
from solar_kiosk import config as cfg
from solar_kiosk.discover import FoundGateway
from solar_kiosk.enphase_token import day, read_token, reminder, unusable

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)  # noon: the same date in any time zone
DAY = 86400


def make_token(**claims) -> str:
    """A token shaped like the ones from Enphase's token site. Invented values; the signature is a stand-in."""
    def part(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'ES256', 'typ': 'JWT'})}.{part(claims)}.c2lnbmF0dXJl"


def owner_token(days: float, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + days * DAY)
    return make_token(aud="000000000001", iss="Entrez", enphaseUser="owner", exp=exp, username="you@example.com")


# --- reading a token -----------------------------------------------------------------------------------


def test_reads_expiry_and_kind():
    info = read_token(owner_token(365, NOW.timestamp()))
    assert info.kind == "owner" and info.expires == NOW + timedelta(days=365)
    assert day(info.expires) == "November 21, 2027"


def test_whitespace_around_a_pasted_token_is_fine():
    assert read_token("  " + owner_token(10) + "\n").kind == "owner"


@pytest.mark.parametrize("bad", ["", "not a token", "a.b", "a..c", "a.!!!.c", f"a.{base64.b64encode(b'[1]').decode()}.c"])
def test_anything_else_is_explained(bad):
    with pytest.raises(ValueError, match="doesn't look like an Enphase token"):
        read_token(bad)


def test_a_token_without_an_expiry_is_never_nagged_about():
    info = read_token(make_token(enphaseUser="owner"))
    assert info.expires is None and unusable(info, NOW) is None and reminder(info, NOW) is None


def test_expired_and_installer_tokens_are_refused():
    assert "ran out on November 20, 2026" in unusable(read_token(owner_token(-1, NOW.timestamp())), NOW)
    installer = read_token(make_token(enphaseUser="installer", exp=int(NOW.timestamp()) + 12 * 3600))
    assert "12 hours" in unusable(installer, NOW) and "homeowner" in unusable(installer, NOW)
    assert unusable(read_token(owner_token(200, NOW.timestamp())), NOW) is None


def test_reminders_start_a_month_before():
    assert reminder(read_token(owner_token(31, NOW.timestamp())), NOW) is None
    assert "runs out on December 21, 2026" in reminder(read_token(owner_token(30, NOW.timestamp())), NOW)
    assert "ran out" in reminder(read_token(owner_token(-2, NOW.timestamp())), NOW)


def test_uses_token_only_without_a_full_login():
    assert cfg.EnphaseConfig(token="t").uses_token
    assert cfg.EnphaseConfig(username="you@example.com", token="t").uses_token  # older MFA setups
    assert not cfg.EnphaseConfig(username="you@example.com", password="pw", token="t").uses_token
    assert not cfg.EnphaseConfig(username="you@example.com", password="pw").uses_token


# --- the Connect check with a token --------------------------------------------------------------------

GOOD = owner_token(300)


class TokenEnvoy(Envoy):
    async def authenticate(self, username=None, password=None, token=None):
        from pyenphase.exceptions import EnvoyAuthenticationError

        if token != GOOD:
            raise EnvoyAuthenticationError("bad")
        self.auth = NS(token=token)


def run(coro):
    return asyncio.run(coro)


GW = found(FoundGateway("192.0.2.10", "000000000001"))


def test_token_connects_and_says_how_long_it_lasts(tmp_path):
    r = run(checks.check_enphase("", "", token=GOOD, state_dir=tmp_path, discover=GW, envoy_factory=TokenEnvoy))
    assert r.ok and "5.2 kW" in r.detail and "The token lasts until" in r.detail and r.data["token_expires"]
    assert not (tmp_path / "enphase-token").exists()  # nothing cached: the token is in the secrets file


def test_token_rejected_by_the_gateway_is_not_counted_as_a_login(tmp_path):
    (tmp_path / "enphase-token").write_text(GOOD)  # a working token cached from an earlier login
    r = run(checks.check_enphase("", "", token=owner_token(299), state_dir=tmp_path, discover=GW,
                                 envoy_factory=TokenEnvoy))
    assert not r.ok and not r.login_failed and "didn't accept that token" in r.title
    assert "000000000001" in r.detail  # which gateway to pick on Enphase's site


def test_bad_tokens_are_explained_before_contacting_anything(tmp_path):
    def nothing():
        raise AssertionError("shouldn't look for the gateway")
    r = run(checks.check_enphase("", "", token="half a token", state_dir=tmp_path, discover=nothing))
    assert not r.ok and r.title == "That isn't a whole token"
    r = run(checks.check_enphase("", "", token=owner_token(-1), state_dir=tmp_path, discover=nothing))
    assert not r.ok and "ran out" in r.detail


def test_find_step_reports_the_serial():
    r = run(checks.find_enphase_gateway(discover=GW))
    assert r.ok and r.data == {"host": "192.0.2.10", "serial": "000000000001"} and "000000000001" in r.detail
    assert not run(checks.find_enphase_gateway(discover=found())).ok


# --- settings ------------------------------------------------------------------------------------------


def body(**enphase):
    return {"enphase": enphase, "franklin": None, "tempest": {"enabled": False}, "units": "us"}


def test_saving_a_token_drops_the_password_and_the_cached_token(tmp_path):
    async def test(client, app):
        await paired(client, app)
        r = await client.post("/api/setup/save", json=body(use="password", username="you@example.com", password="pw"))
        assert (await r.json())["ok"]
        (app.state_dir / "enphase-token").parent.mkdir(parents=True, exist_ok=True)
        (app.state_dir / "enphase-token").write_text("made-by-the-login")
        r = await client.post("/api/setup/save", json=body(use="token", token=GOOD, username="you@example.com"))
        assert (await r.json())["ok"]
        e = cfg.load(app.config_path).enphase
        assert (e.token, e.username, e.password) == (GOOD, "", "") and e.uses_token
        assert not (app.state_dir / "enphase-token").exists()
        state = await (await client.get("/api/setup/state")).json()
        assert state["enphase"]["use"] == "token" and state["enphase"]["has_token"]
        assert state["enphase"]["token_expires"] and GOOD not in json.dumps(state)
        # a blank token field keeps the saved one; switching back to a password drops the token
        assert (await (await client.post("/api/setup/save", json=body(use="token", token=""))).json())["ok"]
        assert cfg.load(app.config_path).enphase.token == GOOD
        r = await client.post("/api/setup/save", json=body(use="password", username="you@example.com", password="pw"))
        assert (await r.json())["ok"]
        e = cfg.load(app.config_path).enphase
        assert (e.token, e.password) == ("", "pw") and not e.uses_token

    with_client(tmp_path, test)


@pytest.mark.parametrize("token, words", [("", "Paste the Enphase token"), ("nope", "doesn't look like"),
                                          (owner_token(-1), "ran out")])
def test_unusable_tokens_are_not_saved(tmp_path, token, words):
    async def test(client, app):
        await paired(client, app)
        r = await client.post("/api/setup/save", json=body(use="token", token=token))
        assert r.status == 400 and words in (await r.json())["error"]

    with_client(tmp_path, test)


def test_connect_in_token_mode_needs_a_token(tmp_path):
    async def test(client, app):
        await paired(client, app)
        r = await (await client.post("/api/setup/enphase", json={"use": "token", "token": ""})).json()
        assert not r["ok"] and r["title"] == "Paste the token first"

    with_client(tmp_path, test)


def test_find_route(tmp_path, monkeypatch):
    async def fake(serial=""):
        return checks.Result(True, "Found your Enphase gateway", "", {"host": "192.0.2.10", "serial": "000000000001"})
    monkeypatch.setattr(checks, "find_enphase_gateway", fake)

    async def test(client, app):
        await paired(client, app)
        r = await (await client.post("/api/setup/enphase/find", json={})).json()
        assert r["ok"] and r["data"]["serial"] == "000000000001"

    with_client(tmp_path, test)


def test_the_screen_reminds_before_the_token_runs_out(tmp_path):
    async def test(client, app):
        app.config = dataclasses.replace(app.config, enphase=cfg.EnphaseConfig(token=owner_token(400)))
        assert app.attention() == []
        app.config = dataclasses.replace(app.config, enphase=cfg.EnphaseConfig(token=owner_token(10)))
        assert any("runs out on" in a for a in app.attention())
        # once it has run out, that's the one message: not also "can't read the gateway"
        app.config = dataclasses.replace(app.config, enphase=cfg.EnphaseConfig(token=owner_token(-1)))
        app.snapshot = NS(enphase_error="the gateway didn't accept the token", franklin_error=None,
                          franklin_updated=None)
        notes = app.attention()
        assert len(notes) == 1 and "ran out" in notes[0]
        # with an email and password, the app renews it itself: no reminders
        app.snapshot = NS(enphase_error=None, franklin_error=None, franklin_updated=None)
        app.config = dataclasses.replace(app.config, enphase=cfg.EnphaseConfig(
            username="you@example.com", password="pw", token=owner_token(-1)))
        assert not any("token" in a for a in app.attention())

    with_client(tmp_path, test, setup=True)


def test_enphase_use_reads_older_pages():
    saved_login = cfg.EnphaseConfig(username="you@example.com", password="pw")
    assert server.enphase_use({"use": "token"}, saved_login) == "token"
    assert server.enphase_use({"token": "t"}, saved_login) == "token"
    assert server.enphase_use({"password": "pw"}, cfg.EnphaseConfig(token="t")) == "password"
    assert server.enphase_use({}, saved_login) == "password"
    assert server.enphase_use({}, cfg.EnphaseConfig()) == "token"
