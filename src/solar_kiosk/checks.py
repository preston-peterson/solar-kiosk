"""The setup page's "Connect" checks: try a device with the details given, and explain the result in
plain words, with what to do about it."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backoff import LoginBackoff
from .discover import FoundGateway, find_enphase, find_tempest, pick_gateway
from .enphase_token import day, read_token, unusable

log = logging.getLogger(__name__)


@dataclass
class Result:
    ok: bool
    title: str
    detail: str = ""
    data: dict = field(default_factory=dict)
    login_failed: bool = False


def kw(w: float | None) -> str:
    return "—" if w is None else f"{abs(w) / 1000:.1f} kW"


async def find_enphase_gateway(*, serial: str = "",
                               discover: Callable[[], Awaitable[list[FoundGateway]]] = find_enphase) -> Result:
    """Look for the gateway on the home network. No login: the first step of setting up Enphase."""
    found = await discover()
    gw = pick_gateway(found, serial)
    if gw is None:
        if not found:
            return Result(False, "Couldn't find an Enphase gateway",
                          "The solar app has to be on the same home network as the gateway (the same Wi-Fi or "
                          "router). Check the gateway's lights are on, then try again. You can also type its "
                          "address under More options.", {"not_found": True})
        choices = [{"host": g.host, "serial": g.serial} for g in found]
        return Result(False, f"Found {len(found)} Enphase gateways", "Pick yours below.", {"choices": choices})
    return Result(True, "Found your Enphase gateway",
                  f"Its serial number is {gw.serial}." if gw.serial else "", {"host": gw.host, "serial": gw.serial})


async def check_enphase(username: str, password: str, *, state_dir: Path, host: str = "", serial: str = "",
                        token: str = "", discover: Callable[[], Awaitable[list[FoundGateway]]] = find_enphase,
                        envoy_factory: Callable[[str], Any] | None = None,
                        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> Result:
    """Try the gateway with a pasted token (when no email and password are given) or with the Enphase login."""
    from pyenphase.exceptions import EnvoyAuthenticationError, EnvoyAuthenticationRequired

    from .sources.enphase import EnphaseGateway

    use_token = bool(token) and not (username and password)
    info = None
    if use_token:
        try:
            info = read_token(token)
        except ValueError as exc:
            return Result(False, "That isn't a whole token", str(exc))
        if why := unusable(info, now()):
            return Result(False, "That token can't be used", why)
    if not host:
        where = await find_enphase_gateway(serial=serial, discover=discover)
        if not where.ok:
            return where
        host, serial = where.data["host"], where.data["serial"]

    kwargs = {"envoy_factory": envoy_factory} if envoy_factory else {}
    if use_token:  # test only the pasted token, not one cached from an earlier login
        gateway = EnphaseGateway(host, "", "", None, serial=serial, token=token,
                                 backoff=LoginBackoff("Enphase", 0), **kwargs)
    else:
        gateway = EnphaseGateway(host, username, password, state_dir / "enphase-token", serial=serial,
                                 backoff=LoginBackoff("Enphase", 0), **kwargs)
    try:
        reading = await asyncio.wait_for(gateway.read(), 45)
    except (EnvoyAuthenticationError, EnvoyAuthenticationRequired):
        if use_token:  # nothing was sent to Enphase's servers, so no account can be locked: not counted
            which = f" (serial {gateway.serial})" if gateway.serial else ""
            return Result(False, "The gateway didn't accept that token",
                          f"Check you picked this gateway{which} on Enphase's token site and copied the whole "
                          "token, then make a new one and paste it again.")
        return Result(False, "Enphase didn't accept that email and password",
                      "Use the email and password for the Enphase app. If that account asks for a code from your "
                      "phone when you sign in (two-step sign-in), use a token instead.", login_failed=True)
    except Exception as exc:  # unreachable, timed out, unexpected reply
        log.info("Enphase check failed: %r", exc)
        return Result(False, f"Found the gateway at {host}, but it didn't answer",
                      "Wait a minute and try again. If it keeps happening, restart the gateway.")
    finally:
        await gateway.close()
    detail, data = f"Solar right now: {kw(reading.solar_w)}.", {"host": gateway.host, "serial": gateway.serial}
    if info is not None and info.expires is not None:
        detail += f" The token lasts until {day(info.expires)}."
        data["token_expires"] = info.expires.isoformat()
    return Result(True, "Connected to the Enphase gateway", detail, data)


async def check_franklin(email: str, password: str, *, gateway_id: str = "",
                         client_factory: Callable[..., Any] | None = None) -> Result:
    from franklinwh.client import AccountLockedException, InvalidCredentialsException

    from .sources.franklin import FranklinCloud

    kwargs = {"client_factory": client_factory} if client_factory else {}
    cloud = FranklinCloud(email, password, gateway_id, backoff=LoginBackoff("FranklinWH", 0), **kwargs)
    try:
        reading = await asyncio.wait_for(cloud.read(), 45)
    except InvalidCredentialsException:
        return Result(False, "FranklinWH didn't accept that email and password",
                      "Use the email and password for the FranklinWH app.", login_failed=True)
    except AccountLockedException:
        return Result(False, "FranklinWH has locked this account after too many tries",
                      "Wait a while, or reset the password in the FranklinWH app, then try again.", login_failed=True)
    except LookupError:
        return Result(False, "That FranklinWH account has no aGate",
                      "Check it's the same login the FranklinWH app uses for this house.")
    except Exception as exc:
        log.info("FranklinWH check failed: %r", exc)
        return Result(False, "Couldn't reach FranklinWH",
                      "The aGate is read through FranklinWH's servers, so the solar app needs the internet. "
                      "Check the connection and try again.")
    finally:
        await cloud.close()
    soc = f"Battery {reading.soc_pct:.0f}%." if reading.soc_pct is not None else ""
    return Result(True, "Connected to the aGate", soc, {"gateway_id": cloud.gateway_id})


async def check_tempest(port: int = 50222, timeout: float = 15.0,
                        find: Callable[..., Awaitable[list]] = find_tempest) -> Result:
    try:
        found = await find(timeout=timeout, port=port)
    except OSError as exc:
        return Result(False, "Couldn't listen for the Tempest", str(exc))
    if not found:
        return Result(False, "Didn't hear a Tempest",
                      "The Tempest hub has to be on the same home network as the solar app. If you don't have "
                      "one, skip this; everything else still works.")
    station = found[0]
    return Result(True, "Found the Tempest weather station", f"Station {station.serial}.",
                  {"serial": station.serial, "choices": [s.serial for s in found]})


class TryLimiter:
    """At most `max_failures` failed logins per service in `window_s`, so testing can't lock an account."""

    def __init__(self, max_failures: int = 3, window_s: float = 15 * 60,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max_failures, self.window_s, self._clock = max_failures, window_s, clock
        self._failures: dict[str, list[float]] = {}

    def blocked(self, service: str, name: str) -> Result | None:
        now = self._clock()
        recent = [t for t in self._failures.get(service, []) if now - t < self.window_s]
        self._failures[service] = recent
        if len(recent) < self.max_failures:
            return None
        wait = max(1, round((recent[0] + self.window_s - now) / 60))
        return Result(False, "Too many tries for now",
                      f"Wait {wait} min before trying again, so {name} doesn't lock the account. "
                      "Meanwhile, check the email and password in the app.")

    def record(self, service: str, result: Result) -> None:
        if result.login_failed:
            self._failures.setdefault(service, []).append(self._clock())
