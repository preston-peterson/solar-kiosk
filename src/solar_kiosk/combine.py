"""Rules for merging the Enphase and FranklinWH readings into one set of figures.

The behavior follows FrankEnPoller (GPL-3.0, github.com/GasBandit/FrankEnPoller), re-implemented here.
"""

from __future__ import annotations

from datetime import datetime

from .models import EnphaseReading, FranklinReading, Value

# Below this the aGate's solar reading counts as idle; above this the inverters count as producing.
# Both at once means the PV isn't wired through the aGate, so its solar reading is ignored.
AGATE_SOLAR_IDLE_W = 10
INVERTER_SOLAR_ACTIVE_W = 100


def solar(enphase: EnphaseReading | None, franklin: FranklinReading | None) -> Value:
    """Solar power now.

    Prefers the Enphase production CT (real time). Without one, the aGate's reading (every 30 s)
    beats the microinverter reports (every few minutes), unless the aGate reads about zero while
    the inverters are producing.
    """
    if enphase is not None and not enphase.solar_from_inverters:
        return Value(enphase.solar_w)
    if franklin is not None and franklin.solar_w is not None:
        pv_not_through_agate = (
            franklin.solar_w < AGATE_SOLAR_IDLE_W
            and enphase is not None
            and enphase.solar_w > INVERTER_SOLAR_ACTIVE_W
        )
        if not pv_not_through_agate:
            return Value(max(0.0, franklin.solar_w), "measured by aGate")
    if enphase is not None:
        return Value(enphase.solar_w, "inverter report, updates every few min")
    return Value(None)


def home(enphase: EnphaseReading | None, franklin: FranklinReading | None) -> Value:
    """What the house itself is using.

    Prefers the aGate's own measurement; otherwise corrects the Enphase consumption figure
    for battery charge and discharge.
    """
    if franklin is not None and franklin.home_w is not None:
        return Value(franklin.home_w, "measured by aGate")
    if enphase is not None and enphase.consumption_w is not None:
        if franklin is not None and franklin.battery_w is not None:
            return Value(enphase.consumption_w + franklin.battery_w, "Enphase load + battery")
        return Value(enphase.consumption_w, "Enphase load (includes battery charging)")
    return Value(None)


def grid(enphase: EnphaseReading | None, franklin: FranklinReading | None) -> Value:
    """Grid power, + import / − export. Prefers the Enphase consumption CTs, else the aGate."""
    if enphase is not None and enphase.grid_w is not None:
        return Value(enphase.grid_w)
    if franklin is not None and franklin.grid_w is not None:
        return Value(franklin.grid_w, "measured by aGate")
    return Value(None)


def solar_today(
    counter_kwh: float | None,
    counter_since: datetime | None,
    franklin: FranklinReading | None,
) -> tuple[float | None, datetime | None]:
    """Today's solar energy, best source first.

    1. The Enphase lifetime counter minus a pre-dawn baseline (exact, needs no cloud).
    2. The aGate's own daily solar total, when the counter only covers part of today.
    3. The counter's partial total, shown as "since <time>".
    """
    if counter_kwh is not None and counter_since is None:
        return counter_kwh, None
    if franklin is not None and franklin.solar_today_kwh is not None:
        return franklin.solar_today_kwh, None
    return counter_kwh, counter_since
