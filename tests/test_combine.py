from helpers import NOON, enphase, franklin

from solar_kiosk import combine
from solar_kiosk.models import Value


class TestSolar:
    def test_production_ct_wins(self):
        assert combine.solar(enphase(solar=5000), franklin(solar_w=4900)) == Value(5000)

    def test_agate_beats_inverter_reports(self):
        assert combine.solar(enphase(inverters=True), franklin(solar_w=4900)) == Value(4900, "measured by aGate")

    def test_agate_reading_zero_while_inverters_produce_is_ignored(self):
        # the PV isn't wired through the aGate
        got = combine.solar(enphase(solar=3000, inverters=True), franklin(solar_w=0))
        assert got == Value(3000, "inverter report, updates every few min")

    def test_agate_zero_at_night_is_used(self):
        assert combine.solar(enphase(solar=0, inverters=True), franklin(solar_w=0)) == Value(0, "measured by aGate")

    def test_agate_only_is_clamped_at_zero(self):
        assert combine.solar(None, franklin(solar_w=-20)) == Value(0, "measured by aGate")

    def test_inverters_when_agate_has_no_solar(self):
        assert combine.solar(enphase(solar=2500, inverters=True), franklin(solar_w=None)).watts == 2500

    def test_nothing(self):
        assert combine.solar(None, None) == Value(None)


class TestHome:
    def test_agate_measurement_wins(self):
        assert combine.home(enphase(consumption=9999), franklin(home_w=1400)) == Value(1400, "measured by aGate")

    def test_enphase_load_corrected_for_discharge(self):
        # battery discharging 2 kW: the consumption CT only sees the 0.5 kW the battery didn't cover
        got = combine.home(enphase(consumption=500), franklin(home_w=None, battery_w=2000))
        assert got == Value(2500, "Enphase load + battery")

    def test_enphase_load_corrected_for_charge(self):
        got = combine.home(enphase(consumption=4000), franklin(home_w=None, battery_w=-3000))
        assert got.watts == 1000

    def test_enphase_only_includes_battery_charging(self):
        assert combine.home(enphase(consumption=4000), None) == Value(4000, "Enphase load (includes battery charging)")

    def test_nothing_without_consumption_cts_or_agate(self):
        assert combine.home(enphase(consumption=None), None) == Value(None)


class TestGrid:
    def test_enphase_ct_wins(self):
        assert combine.grid(enphase(grid=-3500), franklin(grid_w=-3400)) == Value(-3500)

    def test_agate_without_consumption_cts(self):
        assert combine.grid(enphase(grid=None), franklin(grid_w=800)) == Value(800, "measured by aGate")

    def test_nothing(self):
        assert combine.grid(None, None) == Value(None)


class TestSolarToday:
    def test_full_day_counter_wins(self):
        assert combine.solar_today(18.2, None, franklin(solar_today_kwh=17.9)) == (18.2, None)

    def test_partial_counter_uses_agate_total(self):
        assert combine.solar_today(3.1, NOON, franklin(solar_today_kwh=17.9)) == (17.9, None)

    def test_partial_counter_without_agate(self):
        assert combine.solar_today(3.1, NOON, None) == (3.1, NOON)

    def test_agate_only(self):
        assert combine.solar_today(None, None, franklin(solar_today_kwh=17.9)) == (17.9, None)
