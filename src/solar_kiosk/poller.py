"""The polling loop: reads each source on its own schedule, combines them, hands out snapshots."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Protocol, TypeVar

from . import combine
from .daily import DailySolar
from .models import EnphaseReading, FranklinReading, Snapshot
from .sources.tempest import TempestState

log = logging.getLogger(__name__)

T = TypeVar("T")

FRANKLIN_RETRY = timedelta(seconds=60)  # while the aGate is failing, so timeouts don't pile up
READ_TIMEOUT_S = 20


class Source(Protocol[T]):
    min_interval_s: float
    description: str

    async def read(self) -> T: ...

    async def close(self) -> None: ...


async def _attempt(read: Callable[[], Awaitable[T]]) -> tuple[T | None, str | None]:
    try:
        return await asyncio.wait_for(read(), READ_TIMEOUT_S), None
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        return None, f"no answer in {READ_TIMEOUT_S} s"
    except Exception as exc:
        return None, str(exc) or getattr(exc, "status", "") or type(exc).__name__


class Poller:
    def __init__(
        self,
        *,
        enphase: Source[EnphaseReading] | None,
        franklin: Source[FranklinReading] | None,
        tempest: TempestState | None,
        daily: DailySolar,
        interval_s: float = 15,
        franklin_interval_s: float = 30,
        enphase_missing: str = "Not configured",
        franklin_missing: str = "Not configured",
        clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        before_tick: Callable[[], Any] | None = None,
    ) -> None:
        self.enphase, self.franklin, self.tempest, self.daily = enphase, franklin, tempest, daily
        self.interval_s = interval_s
        min_franklin = getattr(franklin, "min_interval_s", 0)
        self.franklin_interval = timedelta(seconds=max(franklin_interval_s, min_franklin, interval_s))
        self._enphase_missing = enphase_missing
        self._clock = clock
        self._before_tick = before_tick  # the demo uses this to broadcast its weather
        self._franklin_result: tuple[FranklinReading | None, str | None] = (
            (None, "Waiting for the first reading") if franklin else (None, franklin_missing)
        )
        self._franklin_updated: datetime | None = None
        self._next_franklin: datetime | None = None
        self._errors: dict[str, str | None] = {}

    async def tick(self) -> Snapshot:
        if self._before_tick:
            self._before_tick()
        started = self._clock()
        poll_franklin = self.franklin is not None and (self._next_franklin is None or started >= self._next_franklin)

        async def missing() -> tuple[None, str]:
            return None, self._enphase_missing

        async def cached() -> tuple[FranklinReading | None, str | None]:
            return self._franklin_result

        (e, e_err), f_result = await asyncio.gather(
            _attempt(self.enphase.read) if self.enphase else missing(),
            _attempt(self.franklin.read) if poll_franklin else cached(),
        )
        self._log_change("Enphase", e_err)
        if poll_franklin:
            self._franklin_result = f_result
            self._log_change("aGate", f_result[1])
            if f_result[0] is not None:
                self._franklin_updated = started
            self._next_franklin = started + (self.franklin_interval if f_result[1] is None else FRANKLIN_RETRY)
        f, f_err = self._franklin_result

        now = self._clock()
        solar = combine.solar(e, f)
        counter_kwh = counter_since = peak_w = peak_time = None
        if e is not None:
            day = self.daily.update(e.solar_lifetime_wh, solar.watts if solar.watts is not None else e.solar_w, now)
            counter_kwh, counter_since, peak_w, peak_time = day.kwh, day.since, day.peak_w, day.peak_time
        today_kwh, today_since = combine.solar_today(counter_kwh, counter_since, f)
        return Snapshot(
            time=now,
            solar=solar,
            home=combine.home(e, f),
            grid=combine.grid(e, f),
            battery_w=f.battery_w if f else None,
            soc_pct=f.soc_pct if f else None,
            off_grid=f.off_grid if f else None,
            solar_today_kwh=today_kwh,
            solar_today_since=today_since,
            solar_peak_w=peak_w,
            solar_peak_time=peak_time,
            enphase=e,
            enphase_error=e_err,
            franklin=f,
            franklin_error=f_err,
            franklin_updated=self._franklin_updated,
            weather=self.tempest.weather() if self.tempest else None,
        )

    def _log_change(self, name: str, error: str | None) -> None:
        if self._errors.get(name, "") != error:
            self._errors[name] = error
            if error:
                log.warning("%s: %s", name, error)
            else:
                log.info("%s: reading OK", name)

    async def run(self, on_snapshot: Callable[[Snapshot], Any], *, stop: asyncio.Event | None = None,
                  max_ticks: int | None = None) -> None:
        loop = asyncio.get_running_loop()
        stop = stop or asyncio.Event()
        ticks = 0
        while not stop.is_set():
            began = loop.time()
            on_snapshot(await self.tick())
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            try:
                await asyncio.wait_for(stop.wait(), max(0.0, self.interval_s - (loop.time() - began)))
            except asyncio.TimeoutError:
                pass

    async def close(self) -> None:
        for source in (self.enphase, self.franklin):
            if source is not None:
                await source.close()
