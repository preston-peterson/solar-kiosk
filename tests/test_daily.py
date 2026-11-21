from datetime import timedelta

from helpers import NOON

from solar_kiosk.backoff import LoginBackoff, LoginBackoffActive
from solar_kiosk.daily import DailySolar

DAWN = NOON.replace(hour=5)


def test_baseline_before_sunrise_covers_the_whole_day(tmp_path):
    d = DailySolar(tmp_path / "s.json")
    assert d.update(1_000_000, 0, DAWN).kwh == 0
    day = d.update(1_018_200, 6100, NOON)
    assert (day.kwh, day.since, day.peak_w, day.peak_time) == (18.2, None, 6100, NOON)


def test_baseline_after_sunrise_is_partial(tmp_path):
    day = DailySolar(tmp_path / "s.json").update(1_000_000, 2500, NOON)
    assert day.since == NOON


def test_restart_keeps_the_morning_baseline_and_peak(tmp_path):
    path = tmp_path / "s.json"
    first = DailySolar(path)
    first.update(1_000_000, 0, DAWN)
    first.update(1_010_000, 6100, NOON)
    day = DailySolar(path).update(1_020_000, 300, NOON.replace(hour=18))
    assert (day.kwh, day.since, day.peak_w, day.peak_time) == (20.0, None, 6100, NOON)


def test_new_day_starts_over(tmp_path):
    d = DailySolar(tmp_path / "s.json")
    d.update(1_000_000, 0, DAWN)
    d.update(1_030_000, 6000, NOON)
    day = d.update(1_040_000, 0, DAWN + timedelta(days=1))
    assert (day.kwh, day.peak_w) == (0, 0)


def test_counter_going_backwards_reads_zero(tmp_path):
    d = DailySolar(tmp_path / "s.json")
    d.update(1_000_000, 0, DAWN)
    assert d.update(5_000, 100, NOON).kwh == 0  # e.g. a replaced gateway


def test_unreadable_state_is_ignored(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    assert DailySolar(path).update(1_000_000, 0, DAWN).kwh == 0


def test_works_without_a_state_file():
    d = DailySolar(None)
    d.update(1_000_000, 0, DAWN)
    assert d.update(1_001_500, 900, NOON).kwh == 1.5


def test_login_backoff():
    now = [0.0]
    b = LoginBackoff("Example", 300, clock=lambda: now[0])
    b.check()
    b.failed("bad password")
    now[0] = 120
    try:
        b.check()
        raise AssertionError("expected LoginBackoffActive")
    except LoginBackoffActive as exc:
        assert "bad password" in str(exc) and "3 min" in str(exc)
    now[0] = 301
    b.check()
    b.failed("again")
    b.succeeded()
    b.check()
