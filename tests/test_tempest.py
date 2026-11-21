import asyncio
import json
import socket
from datetime import timedelta

from helpers import NOON, ManualClock

from solar_kiosk.sources.tempest import TempestState, listen

SERIAL = "ST-00000001"


def obs(t, *, sun=850, rain=0.0, strikes=0, strike_km=0, avg=2.0, direction=300):
    ts = int(t.timestamp())
    return {"serial_number": SERIAL, "type": "obs_st", "hub_sn": "HB-00000001",
            "obs": [[ts, 1.1, avg, 3.6, direction, 3, 1013.2, 26.5, 52.0, 102000, 9.4, sun, rain, 1 if rain else 0,
                     strike_km, strikes, 2.6, 1]], "firmware_revision": 176}


def rapid(t, speed=4.0, direction=90):
    return {"serial_number": SERIAL, "type": "rapid_wind", "hub_sn": "HB-00000001",
            "ob": [int(t.timestamp()), speed, direction]}


def strike(t, km=13):
    return {"serial_number": SERIAL, "type": "evt_strike", "hub_sn": "HB-00000001", "evt": [int(t.timestamp()), km, 3848]}


def state(clock):
    return TempestState(clock=clock)


def test_observation_fields():
    clock = ManualClock()
    s = state(clock)
    s.handle(obs(NOON))
    w = s.weather()
    assert (w.temp_c, w.humidity_pct, w.pressure_mb, w.uv, w.sun_wm2, w.illuminance_lux) == (26.5, 52, 1013.2, 9.4, 850, 102000)
    assert (w.wind_ms, w.wind_gust_ms, w.wind_dir_deg) == (2.0, 3.6, 300)
    assert w.time == NOON and not w.raining and w.last_strike_km is None


def test_rapid_wind_used_while_fresh():
    clock = ManualClock()
    s = state(clock)
    s.handle(obs(NOON))
    s.handle(rapid(NOON + timedelta(seconds=3)))
    clock.tick(seconds=5)
    assert (s.weather().wind_ms, s.weather().wind_dir_deg) == (4.0, 90)
    clock.tick(seconds=30)
    assert s.weather().wind_ms == 2.0


def test_rain_adds_up_and_resets_at_midnight():
    clock = ManualClock(NOON.replace(hour=23, minute=58))
    s = state(clock)
    for minute in (57, 58):
        s.handle(obs(NOON.replace(hour=23, minute=minute), rain=0.2))
    assert s.weather().rain_today_mm == 0.4 and s.weather().raining
    next_day = NOON.replace(hour=0, minute=1) + timedelta(days=1)
    clock.t = next_day
    s.handle(obs(next_day, rain=0.1))
    assert s.weather().rain_today_mm == 0.1


def test_repeated_observation_is_counted_once():
    clock = ManualClock()
    s = state(clock)
    s.handle(obs(NOON, rain=0.2))
    s.handle(obs(NOON, rain=0.2))
    assert s.weather().rain_today_mm == 0.2


def test_lightning_strikes_expire_after_an_hour():
    clock = ManualClock()
    s = state(clock)
    s.handle(obs(NOON))
    s.handle(strike(NOON - timedelta(minutes=10), km=20))
    s.handle(strike(NOON - timedelta(minutes=2), km=13))
    w = s.weather()
    assert (w.last_strike_km, w.strikes_last_hour) == (13, 2)
    clock.tick(minutes=4)
    s.handle(obs(clock.t))
    clock.tick(minutes=59)
    s.handle(obs(clock.t))
    w = s.weather()
    assert (w.last_strike_km, w.strikes_last_hour) == (None, 0)


def test_strike_counted_in_observation_without_the_event():
    clock = ManualClock()
    s = state(clock)
    s.handle(obs(NOON, strikes=3, strike_km=8))
    assert (s.weather().last_strike_km, s.weather().strikes_last_hour) == (8, 3)


def test_stale_weather_is_dropped():
    clock = ManualClock()
    s = state(clock)
    assert s.weather() is None
    s.handle(obs(NOON))
    clock.tick(minutes=6)
    assert s.weather() is None


def test_other_station_ignored_when_serial_is_set():
    clock = ManualClock()
    s = TempestState(serial="ST-00000002", clock=clock)
    s.handle(obs(NOON))
    assert s.weather() is None


def test_malformed_messages_are_ignored():
    clock = ManualClock()
    s = state(clock)
    for bad in (None, [], {"type": "obs_st"}, {"type": "obs_st", "obs": [["x"]]}, {"type": "rapid_wind", "ob": []},
                {"type": "evt_strike"}, {"type": "hub_status"}):
        s.handle(bad)
    assert s.weather() is None


def test_listens_on_udp():
    async def go():
        clock = ManualClock()
        s = state(clock)
        transport = await listen(s, host="127.0.0.1", port=0)
        port = transport.get_extra_info("sockname")[1]
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(b"not json", ("127.0.0.1", port))
                sock.sendto(json.dumps(obs(NOON)).encode(), ("127.0.0.1", port))
            for _ in range(100):
                if s.weather():
                    break
                await asyncio.sleep(0.01)
        finally:
            transport.close()
        return s.weather()

    assert asyncio.run(go()).sun_wm2 == 850
