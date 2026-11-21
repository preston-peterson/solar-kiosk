"""A simulated house: solar, home use, battery and grid, plus matching Tempest weather.

Runs the whole app with no hardware. All values are invented. The scenarios match the mockup's.
The simulation steps forward to the clock's time on every read, so it can run in real time or
sped up (--speed), and it starts from local midnight so today's totals are already filled in.
"""

from __future__ import annotations

import math
import random
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from ..models import EnphaseReading, FranklinReading

ARRAY_W = 7200  # invented 7.2 kW array
DERATE = 0.85  # inverter, wiring and temperature losses at full sun
PANELS = 20
BATTERY_WH = 13600
BATTERY_MAX_W = 5000
RESERVE_PCT = 20
CHARGE_EFFICIENCY = 0.95
SUNRISE_H, SUNSET_H = 5.5, 20.5  # a June day
STEP_S = 60


@dataclass(frozen=True)
class Scenario:
    start: time  # the simulated time the demo opens at
    sky: float  # 1 = clear sky
    vari: float  # how much clouds come and go
    midnight_soc: float
    soil: float = 1.0  # share of the expected output the array really makes (dirt, shade)
    off_grid: bool = False
    franklin_offline: bool = False
    rain_mm_h: float = 0.0
    strike_km: float | None = None
    temp_lo_c: float = 17.0
    temp_hi_c: float = 29.0


SCENARIOS = {
    "sunny": Scenario(time(12, 30), sky=1.0, vari=0.03, midnight_soc=55),
    "evening": Scenario(time(20, 15), sky=0.9, vari=0.05, midnight_soc=60),
    "outage": Scenario(time(15, 4), sky=0.7, vari=0.12, midnight_soc=72, off_grid=True,
                       rain_mm_h=1.2, strike_km=13, temp_hi_c=26),
    "offline": Scenario(time(11, 10), sky=0.95, vari=0.05, midnight_soc=60, franklin_offline=True),
    "cloudy": Scenario(time(13, 45), sky=0.35, vari=0.15, midnight_soc=40, rain_mm_h=0.6, temp_hi_c=21),
    "check": Scenario(time(12, 30), sky=1.0, vari=0.04, midnight_soc=30, soil=0.755, temp_hi_c=27),
}


class DemoClock:
    """Simulated local time: starts at the scenario's time today and runs `speed` times faster."""

    def __init__(self, start: datetime, speed: float = 1.0) -> None:
        self.start, self.speed = start, speed
        self._t0 = _time.monotonic()

    def __call__(self) -> datetime:
        return self.start + timedelta(seconds=(_time.monotonic() - self._t0) * self.speed)


class DemoSystem:
    def __init__(self, scenario: str = "sunny", *, seed: int = 1, clock: Callable[[], datetime] | None = None,
                 speed: float = 1.0) -> None:
        self.name = scenario
        self.sc = SCENARIOS[scenario]
        self._rng = random.Random(seed)
        self._phases = [self._rng.uniform(0, 2 * math.pi) for _ in range(6)]
        self._panel_share = [1 + self._rng.uniform(-0.04, 0.04) for _ in range(PANELS)]
        today = datetime.now().astimezone()
        start = datetime.combine(today.date(), self.sc.start, tzinfo=today.tzinfo)
        self.clock = clock or DemoClock(start, speed)
        now = self.clock()
        self.t = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.soc = self.sc.midnight_soc
        self.lifetime_wh = 18_234_000.0
        self.midnight, self.midnight_lifetime_wh = self.t, self.lifetime_wh  # for a full-day baseline
        self.home_today_wh = self.solar_today_wh = 0.0
        self.panels_w = {f"DEMO-PANEL-{i + 1:02d}": 0.0 for i in range(PANELS)}
        self._panels_at: datetime | None = None
        self._weather_sent: datetime | None = None
        self._strike_at: datetime | None = None
        self._flows = (0.0, 0.0, 0.0, 0.0, 0.0)
        self.advance()

    # --- the physics ---------------------------------------------------------------------------

    def clear_sun_wm2(self, t: datetime) -> float:
        x = (t.hour + t.minute / 60 + t.second / 3600 - SUNRISE_H) / (SUNSET_H - SUNRISE_H)
        return 1000 * math.sin(math.pi * x) ** 1.25 if 0 < x < 1 else 0.0

    def cloud(self, t: datetime) -> float:
        h = t.hour + t.minute / 60
        p = self._phases
        wobble = math.sin(h * 2.1 + p[0]) * 0.6 + math.sin(h * 5.3 + p[1]) * 0.3 + math.sin(h * 11.7 + p[2]) * 0.1
        return min(1.05, max(0.05, self.sc.sky + self.sc.vari * 2 * wobble))

    def sun_wm2(self, t: datetime) -> float:
        return self.clear_sun_wm2(t) * self.cloud(t)

    def expected_w(self, t: datetime) -> float:
        """What the array should make in this sunlight, before any dirt or shade."""
        return self.sun_wm2(t) / 1000 * ARRAY_W * DERATE

    def home_w(self, t: datetime) -> float:
        h = t.hour + t.minute / 60
        w = 450 + 900 * math.exp(-((h - 7.5) ** 2) / 1.5) + 1500 * math.exp(-((h - 19) ** 2) / 3)
        w += 1200 * max(0.0, self.sc.sky - 0.5) * math.exp(-((h - 15) ** 2) / 4)  # air conditioning
        slot = random.Random(f"{self.name}-{t.date().toordinal()}-{int(h * 6)}")  # str seeds are stable
        if slot.random() < 0.15:
            w += 1500  # a kettle, the dryer
        return w

    def _flows_at(self, t: datetime) -> tuple[float, float, float, float, float]:
        """(solar, home, battery, grid, expected) in W; battery + discharging, grid + import."""
        expected = self.expected_w(t)
        solar, home = expected * self.sc.soil, self.home_w(t)
        can_charge = BATTERY_MAX_W if self.soc < 100 else 0.0
        can_discharge = BATTERY_MAX_W if self.soc > (0 if self.sc.off_grid else RESERVE_PCT) else 0.0
        if self.sc.off_grid:
            battery = max(-can_charge, min(can_discharge, home - solar))
            if battery == -can_charge and solar - home > can_charge:
                solar = home + can_charge  # nowhere for the rest to go: the inverters curtail
            return solar, home, battery, 0.0, expected
        net = solar - home
        battery = -min(net, can_charge) if net > 0 else min(-net, can_discharge)
        return solar, home, battery, home - solar - battery, expected

    def advance(self) -> None:
        """Step the simulation forward to the clock's time."""
        now = self.clock()
        while self.t < now:
            dt = min(STEP_S, (now - self.t).total_seconds())
            solar, home, battery, _, _ = flows = self._flows_at(self.t)
            if self.t.date() != (self.t + timedelta(seconds=dt)).date():
                self.home_today_wh = self.solar_today_wh = 0.0
            hours = dt / 3600
            stored = -battery * hours * (CHARGE_EFFICIENCY if battery < 0 else 1)
            self.soc = min(100.0, max(0.0, self.soc + stored / BATTERY_WH * 100))
            self.lifetime_wh += solar * hours
            self.home_today_wh += home * hours
            self.solar_today_wh += solar * hours
            self.t += timedelta(seconds=dt)
            self._flows = flows
        self._report_panels(now)

    def _report_panels(self, now: datetime) -> None:
        """Microinverters report every 5 minutes, all alike unless something is wrong."""
        slot = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
        if self._panels_at != slot:
            self._panels_at = slot
            per_panel = self._flows[0] / PANELS
            for i, serial in enumerate(self.panels_w):
                self.panels_w[serial] = round(per_panel * self._panel_share[i], 1)

    # --- readings --------------------------------------------------------------------------------

    def enphase(self) -> EnphaseReading:
        self.advance()
        solar, home, battery, grid, _ = self._flows
        return EnphaseReading(
            time=self.t,
            solar_w=round(solar, 1),
            consumption_w=round(home - battery, 1),  # the aGate sits downstream of the CTs
            grid_w=round(grid, 1),
            solar_lifetime_wh=round(self.lifetime_wh),
            panels_w=dict(self.panels_w),
        )

    def franklin(self) -> FranklinReading:
        self.advance()
        if self.sc.franklin_offline:
            raise ConnectionError("FranklinWH cloud not responding (demo)")
        solar, home, battery, grid, _ = self._flows
        return FranklinReading(
            soc_pct=round(self.soc, 1),
            battery_w=round(battery, 1),
            home_w=round(home, 1),
            grid_w=round(grid, 1),
            solar_w=round(solar, 1),
            home_today_kwh=round(self.home_today_wh / 1000, 2),
            solar_today_kwh=round(self.solar_today_wh / 1000, 2),
            off_grid=self.sc.off_grid,
        )

    def tempest_messages(self) -> list[dict]:
        """Broadcast messages since the last call: one obs_st per simulated minute, plus wind."""
        self.advance()
        now = self.t
        start = self._weather_sent or now.replace(hour=0, minute=0, second=0, microsecond=0)
        out = []
        t = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
        while t <= now:
            out.append(self._obs(t))
            t += timedelta(minutes=1)
        self._weather_sent = now
        gust_rng = random.Random(int(now.timestamp()) // 3)
        out.append({"serial_number": "ST-DEMO0001", "type": "rapid_wind", "hub_sn": "HB-DEMO0001",
                    "ob": [int(now.timestamp()), round(2.2 + gust_rng.uniform(-0.8, 1.4), 2),
                           int(300 + gust_rng.uniform(-25, 25))]})
        if self.sc.strike_km and (self._strike_at is None or now - self._strike_at >= timedelta(minutes=4)):
            self._strike_at = now
            out.append({"serial_number": "ST-DEMO0001", "type": "evt_strike", "hub_sn": "HB-DEMO0001",
                        "evt": [int(now.timestamp()), self.sc.strike_km, 3848]})
        return out

    def _obs(self, t: datetime) -> dict:
        h = t.hour + t.minute / 60
        sc = self.sc
        warm = (math.cos((h - 15.5) / 24 * 2 * math.pi) + 1) / 2
        temp = sc.temp_lo_c + (sc.temp_hi_c - sc.temp_lo_c) * warm
        sun = self.sun_wm2(t)
        rng = random.Random(int(t.timestamp()))
        avg = max(0.0, 2.2 + rng.uniform(-0.6, 0.6))
        raining = sc.rain_mm_h > 0 and self.cloud(t) < 0.5
        rain = round(sc.rain_mm_h / 60, 3) if raining else 0.0
        return {
            "serial_number": "ST-DEMO0001", "type": "obs_st", "hub_sn": "HB-DEMO0001",
            "obs": [[int(t.timestamp()), round(avg * 0.6, 2), round(avg, 2), round(avg * 1.8, 2),
                     int(300 + rng.uniform(-20, 20)), 3, 1013.2, round(temp, 1), round(80 - 35 * warm, 1),
                     int(sun * 120), round(sun / 90, 1), int(sun), rain, 1 if raining else 0, 0, 0, 2.6, 1]],
            "firmware_revision": 176,
        }


class DemoEnphase:
    min_interval_s = 15
    description = "demo Enphase gateway"

    def __init__(self, system: DemoSystem) -> None:
        self.system = system

    async def read(self) -> EnphaseReading:
        return self.system.enphase()

    async def close(self) -> None:
        pass


class DemoFranklin:
    min_interval_s = 30
    description = "demo FranklinWH aGate"

    def __init__(self, system: DemoSystem) -> None:
        self.system = system

    async def read(self) -> FranklinReading:
        return self.system.franklin()

    async def close(self) -> None:
        pass
