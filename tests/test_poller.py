import asyncio

from helpers import NOON, ManualClock, enphase, franklin

from solar_kiosk import poller as poller_mod
from solar_kiosk.daily import DailySolar
from solar_kiosk.poller import Poller


class Fake:
    min_interval_s = 0
    description = "fake"

    def __init__(self, result):
        self.result, self.reads = result, 0

    async def read(self):
        self.reads += 1
        if isinstance(self.result, Exception):
            raise self.result
        if callable(self.result):
            return await self.result()
        return self.result

    async def close(self):
        pass


def make(e=None, f=None, **kw):
    clock = ManualClock()
    p = Poller(enphase=e, franklin=f, tempest=None, daily=DailySolar(None), clock=clock, **kw)
    return p, clock


def test_combines_both_sources():
    p, _ = make(Fake(enphase()), Fake(franklin(solar_today_kwh=17.9)))
    s = asyncio.run(p.tick())
    assert (s.solar.watts, s.home.watts, s.grid.watts, s.battery_w, s.soc_pct) == (5000, 1400, -3500, -1000, 60)
    # the counter only started now, so the aGate's daily total is used
    assert (s.solar_today_kwh, s.solar_today_since) == (17.9, None)
    assert s.enphase_error is None and s.franklin_error is None and s.franklin_updated == NOON


def test_agate_polled_on_its_own_schedule():
    f = Fake(franklin())
    p, clock = make(Fake(enphase()), f, interval_s=15, franklin_interval_s=30)

    async def go():
        for _ in range(5):
            await p.tick()
            clock.tick(seconds=15)

    asyncio.run(go())
    assert f.reads == 3  # at 0, 30 and 60 s


def test_failing_agate_retries_after_a_minute():
    f = Fake(ConnectionError("cloud down"))
    p, clock = make(Fake(enphase()), f, interval_s=15)

    async def go():
        snaps = []
        for _ in range(5):
            snaps.append(await p.tick())
            clock.tick(seconds=15)
        return snaps

    snaps = asyncio.run(go())
    assert f.reads == 2  # at 0 and 60 s
    assert snaps[-1].franklin is None and snaps[-1].franklin_error == "cloud down"
    assert snaps[-1].home.source == "Enphase load (includes battery charging)"


def test_missing_enphase():
    p, _ = make(None, Fake(franklin()), enphase_missing="No [enphase] host")
    s = asyncio.run(p.tick())
    assert s.enphase_error == "No [enphase] host" and s.solar.source == "measured by aGate"
    assert s.solar_today_kwh is None


def test_slow_source_times_out(monkeypatch):
    monkeypatch.setattr(poller_mod, "READ_TIMEOUT_S", 0.05)

    async def slow():
        await asyncio.sleep(5)

    p, _ = make(Fake(slow), None)
    assert asyncio.run(p.tick()).enphase_error == "no answer in 0.05 s"


def test_run_calls_back_each_poll():
    p, _ = make(Fake(enphase()), None, interval_s=0)
    seen = []
    asyncio.run(p.run(seen.append, max_ticks=3))
    assert len(seen) == 3
