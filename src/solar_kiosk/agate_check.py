"""`solar-kiosk check-agate ADDRESS`: read a FranklinWH aGate over Modbus and show what solar-kiosk would make of
it, so its readings can be checked against the FranklinWH app before switching over. Read-only.

The serial number is never printed, so the output can be shared as it is.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .sources.agate_modbus import PORT, AGateData, read_agate

SAMPLES, GAP_S = 3, 5.0


def watts(w: float | None) -> str:
    return "     —" if w is None else f"{w:6.0f} W"


def grid_direction(samples: list[AGateData]) -> tuple[str, float | None, float | None]:
    """Which way round model 701's AC power is: does solar + battery + AC add up to the home load, or
    solar + battery - AC? Returns a verdict and each guess's average miss, in watts."""
    usable = [d for d in samples if None not in (d.solar_w, d.battery_w, d.ac_w, d.home_w)]
    if not usable:
        return "Can't tell: some readings are missing.", None, None
    plus = sum(abs(d.solar_w + d.battery_w + d.ac_w - d.home_w) for d in usable) / len(usable)
    minus = sum(abs(d.solar_w + d.battery_w - d.ac_w - d.home_w) for d in usable) / len(usable)
    flow = sum(abs(d.ac_w) for d in usable) / len(usable)
    if flow < 150:
        return ("Can't tell yet: almost nothing is flowing to or from the grid. Try again when the house is "
                "using or sending more power."), plus, minus
    if plus < minus / 2:
        return "AC power is + while importing from the grid. solar-kiosk already assumes this.", plus, minus
    if minus < plus / 2:
        return "AC power is + while exporting to the grid. solar-kiosk needs a change for this aGate.", plus, minus
    return "Can't tell: neither direction adds up. Model 701 may not be the grid connection.", plus, minus


async def check(host: str, *, port: int = PORT, samples: int = SAMPLES, gap_s: float = GAP_S,
                out: Callable[[str], None] = print,
                sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> int:
    out(f"Reading the aGate at {host}, port {port}. Read-only: nothing on the aGate is changed.\n")
    got: list[AGateData] = []
    layout = None
    for i in range(samples):
        try:
            d, layout = await read_agate(host, port=port, layout=layout)
        except OSError as exc:
            out(f"Couldn't connect: {exc}. Check the address, and that Modbus TCP is turned on for this aGate.")
            return 1
        except Exception as exc:  # a Modbus error, a timeout, or not a SunSpec device
            out(f"Connected, but couldn't read it: {exc!r}")
            return 1
        if i == 0:
            last4 = f" (serial ending {d.serial[-4:]})" if len(d.serial) >= 4 else ""
            out(f"Found: {d.maker} {d.model}, firmware {d.firmware or 'unknown'}{last4}".replace("  ", " "))
            out(f"Modbus unit {d.unit}, SunSpec map at register {d.base}, models {', '.join(map(str, d.models))}\n")
            out("              solar      home   battery  AC (701)   charge")
        soc = "     —" if d.soc_pct is None else f"{d.soc_pct:5.1f} %"
        out(f"  reading {i + 1}  {watts(d.solar_w)}  {watts(d.home_w)}  {watts(d.battery_w)}  {watts(d.ac_w)}  {soc}")
        got.append(d)
        if i < samples - 1:
            await sleep(gap_s)
    last = got[-1]
    verdict, plus, minus = grid_direction(got)
    out("\nBattery: + means discharging (into the house), - means charging.")
    out("Does solar + battery + AC power add up to the home?")
    if plus is not None:
        out(f"  if AC is + while importing: off by {plus:.0f} W on average")
        out(f"  if AC is + while exporting: off by {minus:.0f} W on average")
    out(f"  {verdict}")
    out(f"Grid connection (701 ConnSt): {last.connected} (1 = connected, 0 = disconnected). "
        f"Inverter state (701 InvSt): {last.inverter_state}. Voltage: {last.grid_v} V.")
    if last.solar_lifetime_wh is not None:
        out(f"Solar, lifetime: {last.solar_lifetime_wh / 1000:,.1f} kWh.")
    if last.solar_module_w is not None:
        out(f"Solar from model 502: {last.solar_module_w:.0f} W (compare with the solar column).")
    if last.home_coarse_w is not None:
        out(f"Home load in 100 W steps (15506): {last.home_coarse_w:.0f} W.")
    out("\nRaw registers, for the developer:")
    for key, value in last.raw.items():
        out(f"  {key}: {value}")
    out("\nCompare the numbers above with the FranklinWH app, then send all of this to whoever helps you with "
        "solar-kiosk. It doesn't include the serial number.")
    return 0
