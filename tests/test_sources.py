"""The Enphase and FranklinWH adapters, against fakes of the libraries they wrap."""

import asyncio
import stat
from types import SimpleNamespace as NS

import pytest
from franklinwh import GridStatus
from franklinwh.client import Current, InvalidCredentialsException, Stats, Totals
from helpers import NOON
from pyenphase.exceptions import EnvoyAuthenticationError, EnvoyAuthenticationRequired

from solar_kiosk.backoff import LoginBackoffActive
from solar_kiosk.discover import FoundGateway
from solar_kiosk.sources.enphase import EnphaseGateway, reading_from_envoy
from solar_kiosk.sources.franklin import FranklinCloud, reading_from_stats


def envoy_data(solar=5000, total=1500, net=-3500, lifetime=18_234_000):
    meter = lambda w, wh=0: NS(watts_now=w, watt_hours_lifetime=wh)  # noqa: E731
    return NS(
        system_production=meter(solar, lifetime),
        system_consumption=meter(total) if total is not None else None,
        system_net_consumption=meter(net) if net is not None else None,
        inverters={"DEMO-PANEL-01": NS(last_report_watts=251), "DEMO-PANEL-02": NS(last_report_watts=249)},
    )


class TestEnphaseReading:
    def test_metered_gateway(self):
        r = reading_from_envoy(envoy_data(), solar_from_inverters=False, now=NOON)
        assert (r.solar_w, r.consumption_w, r.grid_w, r.solar_lifetime_wh) == (5000, 1500, -3500, 18_234_000)
        assert r.panels_w == {"DEMO-PANEL-01": 251, "DEMO-PANEL-02": 249} and not r.solar_from_inverters

    def test_without_consumption_cts(self):
        r = reading_from_envoy(envoy_data(total=None, net=None), solar_from_inverters=True, now=NOON)
        assert (r.consumption_w, r.grid_w, r.solar_from_inverters) == (None, None, True)

    def test_night_standby_is_not_negative(self):
        assert reading_from_envoy(envoy_data(solar=-4), solar_from_inverters=False, now=NOON).solar_w == 0

    def test_no_production_data(self):
        with pytest.raises(ValueError):
            reading_from_envoy(NS(system_production=None), solar_from_inverters=False, now=NOON)


class FakeEnvoy:
    """Accepts only the token "good-token", and issues it for the login "user"/"pw"."""

    instances: list["FakeEnvoy"] = []
    unreachable: dict[str, Exception] = {}

    def __init__(self, host):
        self.calls, self.auth, self.closed = [], None, False
        self.host = host
        self.production_meter_type = "production"
        self.serial_number = "000000000001"
        self.update_error = FakeEnvoy.unreachable.get(host)
        FakeEnvoy.instances.append(self)

    async def setup(self):
        pass

    async def authenticate(self, username=None, password=None, token=None):
        self.calls.append(("token", token) if token else ("login", username))
        if token == "good-token" or (username, password) == ("user", "pw"):
            self.auth = NS(token="good-token")
            return
        raise EnvoyAuthenticationError("Unable to authenticate")

    async def update(self):
        if self.update_error:
            raise self.update_error
        return envoy_data()

    async def close(self):
        self.closed = True


def gateway(tmp_path, username="user", password="pw", token="", host="192.0.2.10", serial="", found=None):
    FakeEnvoy.instances.clear()
    FakeEnvoy.unreachable = {}

    async def discover():
        return found or []

    return EnphaseGateway(host, username, password, tmp_path / "token", serial=serial, token=token,
                          discover=discover, on_found=saved.append, envoy_factory=FakeEnvoy, clock=lambda: NOON)


saved: list = []


class TestEnphaseGateway:
    def test_login_caches_the_token_privately(self, tmp_path):
        r = asyncio.run(gateway(tmp_path).read())
        assert r.solar_w == 5000
        path = tmp_path / "token"
        assert path.read_text() == "good-token" and stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_cached_token_skips_the_cloud_login(self, tmp_path):
        (tmp_path / "token").write_text("good-token")
        asyncio.run(gateway(tmp_path).read())
        assert FakeEnvoy.instances[0].calls == [("token", "good-token")]

    def test_rejected_token_falls_back_to_login(self, tmp_path):
        (tmp_path / "token").write_text("expired")
        asyncio.run(gateway(tmp_path).read())
        assert FakeEnvoy.instances[0].calls == [("token", "expired"), ("login", "user")]

    def test_configured_token_without_login(self, tmp_path):
        asyncio.run(gateway(tmp_path, username="", password="", token="good-token").read())
        assert FakeEnvoy.instances[0].calls == [("token", "good-token")]

    def test_bad_login_backs_off(self, tmp_path):
        gw = gateway(tmp_path, password="wrong")
        with pytest.raises(EnvoyAuthenticationError):
            asyncio.run(gw.read())
        with pytest.raises(LoginBackoffActive, match="Unable to authenticate"):
            asyncio.run(gw.read())
        assert len(FakeEnvoy.instances) == 1 and FakeEnvoy.instances[0].closed

    def test_expired_session_logs_in_again(self, tmp_path):
        gw = gateway(tmp_path)
        asyncio.run(gw.read())
        FakeEnvoy.instances[0].update_error = EnvoyAuthenticationRequired("401")
        with pytest.raises(EnvoyAuthenticationRequired):
            asyncio.run(gw.read())
        asyncio.run(gw.read())
        assert len(FakeEnvoy.instances) == 2


def stats(grid_status=GridStatus.NORMAL):
    return Stats(
        Current(solar_production=5.2, generator_production=0, generator_enabled=False, battery_use=-1.5,
                grid_use=-2.4, home_load=1.3, battery_soc=64.0, switch_1_load=0, switch_2_load=0, v2l_use=0,
                grid_status=grid_status),
        Totals(battery_charge=6.1, battery_discharge=3.2, grid_import=1.4, grid_export=9.8, solar=18.2,
               generator=0, home_use=11.9, switch_1_use=0, switch_2_use=0, v2l_export=0, v2l_import=0),
    )


class TestFranklin:
    def test_reading_converts_kw_to_w(self):
        r = reading_from_stats(stats())
        assert (r.soc_pct, r.battery_w, r.home_w, r.grid_w, r.solar_w) == (64, -1500, 1300, -2400, 5200)
        assert (r.home_today_kwh, r.solar_today_kwh, r.off_grid) == (11.9, 18.2, False)

    @pytest.mark.parametrize("status", [GridStatus.DOWN, GridStatus.OFF])
    def test_off_grid(self, status):
        assert reading_from_stats(stats(status)).off_grid

    def test_looks_up_the_agate_when_no_serial_is_set(self):
        class Client:
            def __init__(self, email, password, gateway):
                self.gateway = gateway

            async def refresh_token(self):
                pass

            async def get_home_gateway_list(self):
                return [{"id": "EXAMPLE-AGATE-1"}, {"id": "EXAMPLE-AGATE-2"}]

            async def get_stats(self):
                assert self.gateway == "EXAMPLE-AGATE-1"
                return stats()

        cloud = FranklinCloud("you@example.com", "pw", client_factory=Client)
        asyncio.run(cloud.read())
        assert cloud.gateway_id == "EXAMPLE-AGATE-1"

    def test_account_without_an_agate(self):
        class Client:
            def __init__(self, *args):
                pass

            async def refresh_token(self):
                pass

            async def get_home_gateway_list(self):
                return []

        with pytest.raises(LookupError, match="No aGate"):
            asyncio.run(FranklinCloud("you@example.com", "pw", client_factory=Client).read())

    def test_bad_login_backs_off(self):
        made = []

        class Client:
            def __init__(self, *args):
                made.append(args)

            async def refresh_token(self):
                raise InvalidCredentialsException("wrong password")

        cloud = FranklinCloud("you@example.com", "pw", "EXAMPLE-AGATE", client_factory=Client)
        with pytest.raises(InvalidCredentialsException):
            asyncio.run(cloud.read())
        with pytest.raises(LoginBackoffActive, match="wrong password"):
            asyncio.run(cloud.read())
        assert len(made) == 1

    def test_reads_stats(self):
        class Client:
            def __init__(self, *args):
                pass

            async def refresh_token(self):
                pass

            async def get_stats(self):
                return stats()

        cloud = FranklinCloud("you@example.com", "pw", "EXAMPLE-AGATE", client_factory=Client)
        assert asyncio.run(cloud.read()).soc_pct == 64


class TestEnphaseDiscovery:
    def setup_method(self):
        saved.clear()

    def test_finds_the_gateway_when_no_address_is_set(self, tmp_path):
        gw = gateway(tmp_path, host="", found=[FoundGateway("192.0.2.20", "000000000001")])
        asyncio.run(gw.read())
        assert FakeEnvoy.instances[0].host == "192.0.2.20"
        assert saved == [FoundGateway("192.0.2.20", "000000000001")]

    def test_two_gateways_and_no_serial_needs_a_choice(self, tmp_path):
        two = [FoundGateway("192.0.2.20", "000000000001"), FoundGateway("192.0.2.21", "000000000002")]
        with pytest.raises(LookupError, match="pick one"):
            asyncio.run(gateway(tmp_path, host="", found=two).read())
        gw = gateway(tmp_path, host="", serial="000000000002", found=two)
        FakeEnvoy.instances.clear()
        asyncio.run(gw.read())
        assert FakeEnvoy.instances[0].host == "192.0.2.21"

    def test_nothing_on_the_network(self, tmp_path):
        with pytest.raises(LookupError, match="Can't find"):
            asyncio.run(gateway(tmp_path, host="").read())

    def test_learns_the_serial_from_a_typed_address(self, tmp_path):
        asyncio.run(gateway(tmp_path).read())
        assert saved == [FoundGateway("192.0.2.10", "000000000001")]

    def test_moved_gateway_is_found_again(self, tmp_path):
        gw = gateway(tmp_path, serial="000000000001", found=[FoundGateway("192.0.2.30", "000000000001")])
        asyncio.run(gw.read())
        FakeEnvoy.instances[0].update_error = OSError("unreachable")
        FakeEnvoy.unreachable = {"192.0.2.10": OSError("unreachable")}
        for _ in range(3):
            with pytest.raises(OSError):
                asyncio.run(gw.read())
        assert asyncio.run(gw.read()).solar_w == 5000
        assert gw.host == "192.0.2.30" and saved[-1] == FoundGateway("192.0.2.30", "000000000001")
