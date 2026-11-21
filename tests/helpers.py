"""Shared test helpers. All values are invented."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from solar_kiosk.models import EnphaseReading, FranklinReading

TZ = timezone.utc
NOON = datetime(2026, 6, 12, 12, 0, tzinfo=TZ)


class ManualClock:
    """A clock the test moves by hand."""

    def __init__(self, t: datetime = NOON) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def tick(self, **delta) -> datetime:
        self.t += timedelta(**delta)
        return self.t


def enphase(solar=5000.0, consumption=1500.0, grid=-3500.0, *, inverters=False, lifetime=1_000_000.0,
            t=NOON) -> EnphaseReading:
    return EnphaseReading(t, solar, consumption, grid, lifetime, inverters)


def franklin(**overrides) -> FranklinReading:
    values = dict(soc_pct=60.0, battery_w=-1000.0, home_w=1400.0, grid_w=-3400.0, solar_w=4900.0)
    values.update(overrides)
    return FranklinReading(**values)
