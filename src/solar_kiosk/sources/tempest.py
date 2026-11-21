"""WeatherFlow Tempest, from the hub's local UDP broadcast (port 50222). No account or token needed.

Message types used (see WeatherFlow's "Tempest UDP Reference"):
  obs_st      full observation, every minute
  rapid_wind  wind speed and direction, every 3 s
  evt_strike  one lightning strike: time, distance (km), energy
  evt_precip  rain started

UDP broadcast does not cross subnets or VLANs, so this must run on the hub's network.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta

from ..models import WeatherReading

log = logging.getLogger(__name__)

PORT = 50222
STALE_AFTER = timedelta(minutes=5)  # obs_st comes every minute; allow a few misses
RAPID_WIND_FRESH = timedelta(seconds=15)
STRIKE_WINDOW = timedelta(hours=1)

# obs_st field positions
(OB_TIME, OB_LULL, OB_AVG, OB_GUST, OB_DIR, _OB_WIND_INT, OB_PRESSURE, OB_TEMP, OB_HUMIDITY,
 OB_LUX, OB_UV, OB_SUN, OB_RAIN, OB_PRECIP_TYPE, OB_STRIKE_KM, OB_STRIKE_COUNT) = range(16)


def _local(ts: float, tz) -> datetime:
    return datetime.fromtimestamp(ts, tz)


def _num(values: list, i: int) -> float | None:
    v = values[i] if i < len(values) else None
    return float(v) if isinstance(v, (int, float)) else None


class TempestState:
    """Keeps the latest weather from the broadcast messages it's given."""

    def __init__(self, serial: str = "", clock: Callable[[], datetime] = lambda: datetime.now().astimezone()) -> None:
        self.serial = serial  # only needed when more than one Tempest is on the network
        self._clock = clock
        self._obs: list | None = None
        self._obs_time: datetime | None = None
        self._rapid: tuple[datetime, float, float] | None = None
        self._rain_day = None
        self._rain_today_mm = 0.0
        self._strikes: deque[tuple[datetime, float]] = deque()

    def handle(self, msg: dict) -> None:
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if self.serial and kind in ("obs_st", "rapid_wind", "evt_strike") and msg.get("serial_number") != self.serial:
            return
        tz = self._clock().tzinfo  # "today" for the rain total follows the clock's time zone
        try:
            if kind == "obs_st":
                for ob in msg.get("obs") or []:
                    self._observation(ob, tz)
            elif kind == "rapid_wind":
                ts, speed, direction = msg["ob"][:3]
                self._rapid = (_local(ts, tz), float(speed), float(direction))
            elif kind == "evt_strike":
                ts, km = msg["evt"][:2]
                self._strikes.append((_local(ts, tz), float(km)))
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            log.debug("Ignoring malformed %s message: %s", kind, exc)

    def _observation(self, ob: list, tz) -> None:
        when = _local(ob[OB_TIME], tz)
        if self._obs_time is not None and when <= self._obs_time:
            return  # a repeat
        if self._rain_day != when.date():
            self._rain_day, self._rain_today_mm = when.date(), 0.0
        self._rain_today_mm += _num(ob, OB_RAIN) or 0.0
        self._obs, self._obs_time = ob, when

    def weather(self) -> WeatherReading | None:
        """The latest weather, or None if nothing has arrived in the last few minutes."""
        now = self._clock()
        if self._obs is None or now - self._obs_time > STALE_AFTER:
            return None
        while self._strikes and now - self._strikes[0][0] > STRIKE_WINDOW:
            self._strikes.popleft()
        ob = self._obs
        wind, direction = _num(ob, OB_AVG), _num(ob, OB_DIR)
        if self._rapid is not None and now - self._rapid[0] <= RAPID_WIND_FRESH:
            wind, direction = self._rapid[1], self._rapid[2]
        last = self._strikes[-1] if self._strikes else None
        obs_strikes = int(_num(ob, OB_STRIKE_COUNT) or 0)
        if last is None and obs_strikes:  # missed the strike event, but the observation counted it
            km = _num(ob, OB_STRIKE_KM)
            last = (self._obs_time, km) if km else None
        return WeatherReading(
            time=self._obs_time,
            temp_c=_num(ob, OB_TEMP),
            humidity_pct=_num(ob, OB_HUMIDITY),
            pressure_mb=_num(ob, OB_PRESSURE),
            wind_ms=wind,
            wind_gust_ms=_num(ob, OB_GUST),
            wind_dir_deg=direction,
            uv=_num(ob, OB_UV),
            illuminance_lux=_num(ob, OB_LUX),
            sun_wm2=_num(ob, OB_SUN),
            rain_today_mm=round(self._rain_today_mm, 2),
            raining=(_num(ob, OB_RAIN) or 0) > 0,
            last_strike_km=last[1] if last else None,
            last_strike_time=last[0] if last else None,
            strikes_last_hour=max(len(self._strikes), obs_strikes),
        )


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, state: TempestState) -> None:
        self.state = state

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            self.state.handle(json.loads(data))
        except (UnicodeDecodeError, ValueError):
            log.debug("Ignoring a non-JSON packet from %s", addr)


async def listen(state: TempestState, host: str = "0.0.0.0", port: int = PORT) -> asyncio.DatagramTransport:
    """Start feeding broadcast messages into state. Close the returned transport to stop.

    reuse_port lets this share the port with another listener on the same machine (for example
    Home Assistant's WeatherFlow integration); each one still receives every broadcast.
    """
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Protocol(state), local_addr=(host, port), reuse_port=True
    )
    return transport
