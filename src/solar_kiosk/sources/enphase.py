"""Enphase IQ Gateway (Envoy) over the local network, through pyenphase (MIT).

On firmware 7 and later the gateway needs a token. Either the owner makes one on Enphase's token site
and pastes it into the settings (the recommended way: the app never sees their password, and the token
lasts a year), or pyenphase fetches one from the Enphase cloud with the Enlighten login. A fetched token
is cached on disk (mode 600), and the cloud is only contacted again when the gateway rejects it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..backoff import LoginBackoff, LoginBackoffActive
from ..discover import FoundGateway, pick_gateway
from ..models import EnphaseReading

log = logging.getLogger(__name__)

LOGIN_RETRY_S = 5 * 60
REDISCOVER_AFTER = 3  # failed reads in a row before looking for the gateway at a new address


def reading_from_envoy(data: Any, *, solar_from_inverters: bool, now: datetime) -> EnphaseReading:
    """Convert pyenphase's EnvoyData into an EnphaseReading."""
    prod = data.system_production
    if prod is None:
        raise ValueError("No solar production data in the gateway's response")
    total = data.system_consumption
    net = data.system_net_consumption
    return EnphaseReading(
        time=now,
        solar_w=max(0.0, float(prod.watts_now)),  # the CT reads a few watts negative at night
        consumption_w=float(total.watts_now) if total is not None else None,
        grid_w=float(net.watts_now) if net is not None else None,
        solar_lifetime_wh=float(prod.watt_hours_lifetime),
        solar_from_inverters=solar_from_inverters,
        panels_w={serial: float(inv.last_report_watts) for serial, inv in (data.inverters or {}).items()},
    )


def _default_envoy(host: str) -> Any:
    from pyenphase import Envoy

    return Envoy(host)


class EnphaseGateway:
    min_interval_s = 15

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        token_path: Path | None,
        *,
        serial: str = "",
        token: str = "",
        discover: Callable[[], Awaitable[list[FoundGateway]]] | None = None,
        on_found: Callable[[FoundGateway], Any] | None = None,
        envoy_factory: Callable[[str], Any] = _default_envoy,
        backoff: LoginBackoff | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self.host = host  # empty: find it with `discover`
        self.serial = serial
        self._discover = discover  # None turns auto-discovery off
        self._on_found = on_found  # told when the address or serial changes, to save it
        self._failures = 0
        self._lost = False
        self._username = username
        self._password = password
        self._token_path = token_path
        self._token = token  # from the secrets file: used first, for accounts with MFA
        self._factory = envoy_factory
        self._backoff = backoff or LoginBackoff("Enphase", LOGIN_RETRY_S)
        self._clock = clock
        self._envoy: Any = None

    @property
    def description(self) -> str:
        return f"Enphase gateway at {self.host}"

    async def read(self) -> EnphaseReading:
        from pyenphase.exceptions import EnvoyAuthenticationError, EnvoyAuthenticationRequired

        try:
            if self._envoy is None:
                await self._connect()
            data = await self._envoy.update()
        except EnvoyAuthenticationRequired:
            await self.close()  # the token expired or was revoked: log in again next time
            raise
        except (LoginBackoffActive, EnvoyAuthenticationError, LookupError):
            raise
        except Exception:
            self._failures += 1
            if self._failures >= REDISCOVER_AFTER and self._discover is not None:
                await self.close()
                self._lost = True  # maybe the router gave it a new address
            raise
        self._failures = 0
        return reading_from_envoy(
            data,
            solar_from_inverters=self._envoy.production_meter_type is None,
            now=self._clock(),
        )

    async def _connect(self) -> None:
        from pyenphase.exceptions import EnvoyAuthenticationError, EnvoyAuthenticationRequired

        self._backoff.check()
        if not self.host or self._lost:
            await self._find()
        envoy = self._factory(self.host)
        await envoy.setup()
        auth_errors = (EnvoyAuthenticationError, EnvoyAuthenticationRequired)
        try:
            for token in dict.fromkeys(t for t in (self._token, self._load_token()) if t):
                try:
                    await envoy.authenticate(token=token)
                    break
                except auth_errors:
                    log.info("An Enphase token was rejected")
            else:
                if not self._username:
                    raise EnvoyAuthenticationError("the gateway didn't accept the token; make a new one in Settings")
                await envoy.authenticate(username=self._username, password=self._password)
        except auth_errors as exc:
            self._backoff.failed(getattr(exc, "status", "") or str(exc) or type(exc).__name__)
            await envoy.close()
            raise
        self._backoff.succeeded()
        self._save_token(getattr(envoy.auth, "token", None))
        self._envoy = envoy
        if not self.serial and getattr(envoy, "serial_number", None):
            self._update(FoundGateway(self.host, envoy.serial_number))  # remembered for finding it later

    async def _find(self) -> None:
        if self._discover is None:
            raise LookupError("No Enphase gateway address set")
        found = await self._discover()
        gw = pick_gateway(found, self.serial)
        if gw is None:
            if not found:
                raise LookupError("Can't find the Enphase gateway on the network")
            if self.serial:
                raise LookupError(f"Enphase gateway {self.serial} isn't on the network")
            raise LookupError(f"Found {len(found)} Enphase gateways; pick one in setup")
        self._lost, self._failures = False, 0
        self._update(gw)

    def _update(self, gw: FoundGateway) -> None:
        if (gw.host, gw.serial) != (self.host, self.serial):
            log.info("Enphase gateway %s is at %s", gw.serial or "(serial unknown)", gw.host)
            self.host, self.serial = gw.host, gw.serial
            if self._on_found:
                self._on_found(gw)

    def _load_token(self) -> str | None:
        if self._token_path is None or not self._token_path.exists():
            return None
        try:
            return self._token_path.read_text().strip() or None
        except OSError:
            return None

    def _save_token(self, token: str | None) -> None:
        if not token or self._token_path is None:
            return
        try:
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(token)
        except OSError as exc:
            log.warning("Could not cache the Enphase token: %s", exc)

    async def close(self) -> None:
        envoy, self._envoy = self._envoy, None
        if envoy is not None:
            try:
                await envoy.close()
            except Exception:  # closing is best-effort
                pass
