"""Readings from each source, and the combined snapshot the screen shows.

Power is in watts throughout, with these signs:
  grid     + importing from the grid, − exporting to it
  battery  + discharging (powering the home), − charging
Solar and home use are never negative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class EnphaseReading:
    """One poll of the Enphase IQ Gateway.

    solar_w comes from the production CT when one is installed (real time); otherwise from the
    microinverter reports, which update only every few minutes (solar_from_inverters=True).
    consumption_w is the total-consumption CT. A FranklinWH aGate sits downstream of it, so battery
    charging adds to it and discharging subtracts from it. It is None without consumption CTs.
    grid_w is the net-consumption CT (+ import / − export), None without consumption CTs.
    """

    time: datetime
    solar_w: float
    consumption_w: float | None
    grid_w: float | None
    solar_lifetime_wh: float
    solar_from_inverters: bool = False
    panels_w: dict[str, float] = field(default_factory=dict)  # microinverter serial → last report


@dataclass(frozen=True)
class FranklinReading:
    """One poll of the FranklinWH aGate. A None field means this source doesn't provide it."""

    soc_pct: float | None
    battery_w: float | None
    home_w: float | None
    grid_w: float | None
    solar_w: float | None = None  # 0 when the PV isn't wired through the aGate
    home_today_kwh: float | None = None  # the aGate's own daily totals, reset at local midnight
    solar_today_kwh: float | None = None
    off_grid: bool | None = None


@dataclass(frozen=True)
class WeatherReading:
    """The latest WeatherFlow Tempest observation, in metric units."""

    time: datetime
    temp_c: float | None
    humidity_pct: float | None
    pressure_mb: float | None
    wind_ms: float | None  # from the 3-second rapid wind when it's fresh, else the 1-minute average
    wind_gust_ms: float | None
    wind_dir_deg: float | None
    uv: float | None
    illuminance_lux: float | None
    sun_wm2: float | None  # solar radiation on a flat surface
    rain_today_mm: float
    raining: bool
    last_strike_km: float | None = None
    last_strike_time: datetime | None = None
    strikes_last_hour: int = 0


@dataclass(frozen=True)
class Value:
    """A combined figure and where it came from ("" = the preferred source)."""

    watts: float | None
    source: str = ""


@dataclass(frozen=True)
class Snapshot:
    """Everything known at one poll: each source's reading or error, plus the combined figures."""

    time: datetime
    solar: Value
    home: Value
    grid: Value
    battery_w: float | None
    soc_pct: float | None
    off_grid: bool | None
    solar_today_kwh: float | None
    solar_today_since: datetime | None  # set when today's total covers only part of the day
    solar_peak_w: float | None
    solar_peak_time: datetime | None
    enphase: EnphaseReading | None
    enphase_error: str | None
    franklin: FranklinReading | None
    franklin_error: str | None
    franklin_updated: datetime | None
    weather: WeatherReading | None
