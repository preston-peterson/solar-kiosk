"""Plain-text rendering of a snapshot, for the command line."""

from __future__ import annotations

from .models import Snapshot

COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def kw(w: float | None) -> str:
    return "—" if w is None else f"{abs(w) / 1000:.2f} kW"


def temp(c: float | None, units: str) -> str:
    if c is None:
        return "—"
    return f"{c * 9 / 5 + 32:.0f}°F" if units == "us" else f"{c:.1f}°C"


def speed(ms: float | None, units: str) -> str:
    if ms is None:
        return "—"
    return f"{ms * 2.23694:.0f} mph" if units == "us" else f"{ms * 3.6:.0f} km/h"


def distance(km: float, units: str) -> str:
    return f"{km * 0.621371:.0f} mi" if units == "us" else f"{km:.0f} km"


def line(s: Snapshot, units: str = "us") -> str:
    def note(v) -> str:  # flag figures from a fallback source that is less exact
        return f" ({v.source})" if v.source and not v.source.startswith("measured") else ""

    parts = [f"{s.time:%H:%M:%S}", f"Solar {kw(s.solar.watts)}{note(s.solar)}", f"Home {kw(s.home.watts)}{note(s.home)}"]
    if s.soc_pct is not None:
        b = s.battery_w or 0
        state = "charging" if b < -50 else "discharging" if b > 50 else "idle"
        parts.append(f"Battery {s.soc_pct:.0f}% {state}" + (f" {kw(b)}" if state != "idle" else ""))
    if s.off_grid:
        parts.append("Grid DOWN (on battery)")
    elif s.grid.watts is not None:
        g = s.grid.watts
        parts.append("Grid idle" if abs(g) < 50 else f"Grid {'buying' if g > 0 else 'selling'} {kw(g)}")
    if s.solar_today_kwh is not None:
        since = f" since {s.solar_today_since:%H:%M}" if s.solar_today_since else ""
        parts.append(f"Solar today {s.solar_today_kwh:.1f} kWh{since}")
    w = s.weather
    if w is not None:
        wx = f"{temp(w.temp_c, units)}, wind {speed(w.wind_ms, units)}"
        if w.wind_dir_deg is not None:
            wx += f" {COMPASS[round(w.wind_dir_deg / 45) % 8]}"
        wx += f", sun {w.sun_wm2:.0f} W/m²" if w.sun_wm2 else ", sun down"
        if w.last_strike_km is not None:
            wx += f", lightning {distance(w.last_strike_km, units)}"
        parts.append(wx)
    out = " · ".join(parts)
    for name, err in (("Enphase", s.enphase_error), ("aGate", s.franklin_error)):
        if err:
            out += f"\n    {name}: {err}"
    return out
