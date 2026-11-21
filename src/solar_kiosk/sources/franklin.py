"""FranklinWH aGate through the FranklinWH cloud, using the franklinwh library (MIT).

The cloud API is unofficial and undocumented. This adapter is kept thin so a hand-written client,
or a local Modbus (SunSpec) reader like upstream FrankEnPoller's, can replace it. Read-only:
nothing here changes an aGate setting.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..backoff import LoginBackoff
from ..models import FranklinReading

log = logging.getLogger(__name__)

LOGIN_RETRY_S = 10 * 60  # FranklinWH locks accounts after repeated bad logins


def reading_from_stats(stats: Any) -> FranklinReading:
    """Convert franklinwh's Stats (power in kW) into a FranklinReading (W).

    battery_use (p_fhp) is + discharging / − charging; grid_use (p_uti) is + import / − export.
    """
    from franklinwh import GridStatus

    cur, tot = stats.current, stats.totals
    return FranklinReading(
        soc_pct=float(cur.battery_soc),
        battery_w=float(cur.battery_use) * 1000,
        home_w=float(cur.home_load) * 1000,
        grid_w=float(cur.grid_use) * 1000,
        solar_w=float(cur.solar_production) * 1000,
        home_today_kwh=float(tot.home_use),
        solar_today_kwh=float(tot.solar),
        off_grid=cur.grid_status != GridStatus.NORMAL,
    )


def _default_client(email: str, password: str, gateway_id: str) -> Any:
    from franklinwh import Client, TokenFetcher

    return Client(TokenFetcher(email, password), gateway_id)


class FranklinCloud:
    min_interval_s = 30  # the cloud updates about every 30 s; polling faster gains nothing

    def __init__(
        self,
        email: str,
        password: str,
        gateway_id: str = "",
        *,
        client_factory: Callable[[str, str, str], Any] = _default_client,
        backoff: LoginBackoff | None = None,
    ) -> None:
        self.gateway_id = gateway_id  # empty: use the first aGate on the account
        self._email = email
        self._password = password
        self._factory = client_factory
        self._backoff = backoff or LoginBackoff("FranklinWH", LOGIN_RETRY_S)
        self._client: Any = None

    @property
    def description(self) -> str:
        return "FranklinWH cloud"

    async def read(self) -> FranklinReading:
        from franklinwh.client import AccountLockedException, InvalidCredentialsException

        if self._client is None:
            self._backoff.check()
            client = self._factory(self._email, self._password, self.gateway_id)
            try:
                await client.refresh_token()
            except (InvalidCredentialsException, AccountLockedException) as exc:
                self._backoff.failed(str(exc) or type(exc).__name__)
                raise
            self._backoff.succeeded()
            if not self.gateway_id:
                await self._look_up_gateway(client)
            self._client = client
        try:
            return reading_from_stats(await self._client.get_stats())
        except (InvalidCredentialsException, AccountLockedException) as exc:
            # The library logs in again by itself when a token expires; a failure there lands here.
            self._backoff.failed(str(exc) or type(exc).__name__)
            self._client = None
            raise

    async def _look_up_gateway(self, client: Any) -> None:
        from ..discover import gateway_ids

        ids = gateway_ids(await client.get_home_gateway_list())
        if not ids:
            raise LookupError("No aGate found on this FranklinWH account")
        if len(ids) > 1:
            log.info("The FranklinWH account has %d aGates; using %s", len(ids), ids[0])
        self.gateway_id = client.gateway = ids[0]

    async def close(self) -> None:
        self._client = None
