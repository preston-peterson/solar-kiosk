from datetime import timedelta

import pytest
from helpers import NOON, ManualClock

from solar_kiosk.sources.demo import SCENARIOS, DemoSystem
from solar_kiosk.sources.tempest import TempestState


def system(name, t=NOON):
    clock = ManualClock(t)
    return DemoSystem(name, clock=clock), clock


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_energy_balances_all_day(name):
    s, clock = system(name, NOON.replace(hour=0, minute=5))
    for _ in range(24 * 6):
        e = s.enphase()
        solar, home, battery, grid, _ = s._flows
        assert e.grid_w == pytest.approx(home - solar - battery, abs=1)
        assert e.consumption_w == pytest.approx(home - battery, abs=1)  # the aGate is behind the CTs
        assert 0 <= s.soc <= 100 and solar >= 0 and home > 0 and abs(battery) <= 5000
        if s.sc.off_grid:
            assert grid == 0
        if not s.sc.franklin_offline:
            f = s.franklin()
            assert (f.grid_w, f.off_grid) == (e.grid_w, s.sc.off_grid)
        clock.tick(minutes=10)


def test_no_solar_at_night():
    s, _ = system("sunny", NOON.replace(hour=2))
    assert s.enphase().solar_w == 0


def test_check_scenario_makes_three_quarters_of_expected():
    s, _ = system("check")
    assert s.enphase().solar_w / s.expected_w(s.t) == pytest.approx(0.755, abs=0.01)


def test_offline_scenario_has_no_agate():
    s, _ = system("offline")
    with pytest.raises(ConnectionError):
        s.franklin()


def test_same_seed_same_day():
    a, b = system("cloudy")[0], system("cloudy")[0]
    assert a.enphase() == b.enphase()


def test_today_totals_start_at_midnight():
    s, clock = system("sunny")
    assert s.franklin().solar_today_kwh > 10  # the morning is already simulated
    clock.t = NOON.replace(hour=0, minute=10) + timedelta(days=1)
    assert s.franklin().solar_today_kwh == 0


def test_weather_matches_the_simulated_sun():
    s, clock = system("outage")
    weather = TempestState(clock=clock)
    for msg in s.tempest_messages():
        weather.handle(msg)
    w = weather.weather()
    assert w.sun_wm2 == pytest.approx(s.sun_wm2(w.time), abs=1)
    assert w.last_strike_km == 13
    clock.tick(minutes=3)
    assert len([m for m in s.tempest_messages() if m["type"] == "obs_st"]) == 3


def test_panels_all_report():
    s, _ = system("sunny")
    panels = s.enphase().panels_w
    assert len(panels) == 20 and all(w > 200 for w in panels.values())
