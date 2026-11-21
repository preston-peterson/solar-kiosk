"""FranklinWH aGate over the home network, by Modbus TCP: no login, and nothing goes through the cloud.

Read-only by construction: this module only ever sends Modbus function 3 (read holding registers). There is
no code here that writes, so it can't change the battery's mode, reserve or anything else.

Modbus TCP is off on the aGate until the installer or FranklinWH support turns it on. It has no password:
anything on the home network that can reach port 502 can read it.

Where the values are:
- the SunSpec 2 models, laid out as in the SunSpec Alliance's model definitions (offsets count from the
  model's ID register, as in its JSON files):
  - 1, common: maker, model, firmware, serial number
  - 713, storage capacity: battery charge (SoC)
  - 714, DC measurement: battery power (DCW), + while discharging
  - 701, AC measurement: AC power (W) and grid connection (ConnSt). The aGate's sign for W isn't confirmed
    yet; `solar-kiosk check-agate` tests it against the other readings.
- FranklinWH's own registers, as measured by the community project franklinwh-modbus (MIT):
  - 15502: solar power, W
  - 15506: home load, W, in about 100 W steps
  - 15510-15511: lifetime solar energy, Wh
  - 16000: home load, W, in about 1 W steps
The aGate serves the SunSpec map from register 0 (newer firmware also mirrors it at 40000). The unit ID is 2,
or 1 on some aGates.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass, field
from typing import Any

from ..models import FranklinReading

PORT = 502
UNITS = (2, 1)  # tried in this order
MAX_REGISTERS = 125  # the most one Modbus read may ask for
SUNS = (0x5375, 0x6E53)  # "SunS", the start of a SunSpec map
BASES = (0, 40000)
GRID_SIGN = 1  # +1 if the aGate's model 701 W is + while importing; to be confirmed on a real aGate


class ModbusError(Exception):
    """The aGate answered, but with a Modbus error (for example 2: no such register)."""


class ModbusClient:
    """A Modbus TCP client that can only read holding registers."""

    def __init__(self, host: str, port: int = PORT, unit: int = UNITS[0], timeout: float = 5.0) -> None:
        self.host, self.port, self.unit, self.timeout = host, port, unit, timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._tid = 0

    async def __aenter__(self) -> ModbusClient:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), self.timeout)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
            self._reader = self._writer = None

    async def read(self, address: int, count: int) -> list[int]:
        """`count` registers from `address` (a 0-based PDU address), in reads of at most 125."""
        out: list[int] = []
        while count > 0:
            n = min(count, MAX_REGISTERS)
            out += await asyncio.wait_for(self._read_once(address, n), self.timeout)
            address, count = address + n, count - n
        return out

    async def _read_once(self, address: int, count: int) -> list[int]:
        assert self._reader is not None and self._writer is not None, "use `async with`"
        self._tid = (self._tid + 1) % 0x10000
        self._writer.write(struct.pack(">HHHBBHH", self._tid, 0, 6, self.unit, 3, address, count))
        await self._writer.drain()
        tid, proto, length, _unit = struct.unpack(">HHHB", await self._reader.readexactly(7))
        pdu = await self._reader.readexactly(length - 1)
        if proto != 0 or tid != self._tid:
            raise ModbusError("the reply didn't match the request")
        if pdu[0] == 0x83:
            raise ModbusError(f"Modbus exception {pdu[1]} reading {count} registers at {address}")
        if pdu[0] != 3 or pdu[1] != 2 * count:
            raise ModbusError("unexpected reply")
        return list(struct.unpack(f">{count}H", pdu[2:2 + 2 * count]))


# --- SunSpec values ----------------------------------------------------------------------------------------


def s16(v: int) -> int | None:
    return None if v == 0x8000 else v - 0x10000 if v & 0x8000 else v


def u16(v: int) -> int | None:
    return None if v == 0xFFFF else v


def u32(hi: int, lo: int) -> int | None:
    v = hi << 16 | lo
    return None if v == 0xFFFFFFFF else v


def scaled(v: int | None, sf: int | None) -> float | None:
    """A value times 10^sf; None if either isn't reported. (Divides for a negative sf: 2401 / 10 is 240.1,
    while 2401 * 0.1 is 240.10000000000002.)"""
    if v is None or sf is None:
        return None
    return float(v * 10 ** sf) if sf >= 0 else v / 10 ** -sf


def text(regs: list[int]) -> str:
    return b"".join(struct.pack(">H", r) for r in regs).split(b"\0", 1)[0].decode("ascii", "replace").strip()


async def find_models(client: ModbusClient) -> tuple[int, dict[int, int]]:
    """Where the SunSpec map starts, and where each model's ID register is."""
    for base in BASES:
        try:
            if tuple(await client.read(base, 2)) != SUNS:
                continue
        except ModbusError:
            continue
        models: dict[int, int] = {}
        address = base + 2
        for _ in range(200):  # a map ends with model 0xFFFF; this stops a broken one from looping forever
            model, length = await client.read(address, 2)
            if model == 0xFFFF:
                break
            models.setdefault(model, address)
            address += 2 + length
        return base, models
    raise LookupError("This device doesn't serve a SunSpec map")


@dataclass
class AGateData:
    """Everything one read of the aGate gives, in plain units. None: the aGate doesn't report it."""

    unit: int
    base: int
    models: list[int]
    maker: str = ""
    model: str = ""
    firmware: str = ""
    serial: str = ""
    soc_pct: float | None = None  # model 713 SoC
    battery_w: float | None = None  # model 714 DCW, + while discharging
    ac_w: float | None = None  # model 701 W, as the aGate reports it
    connected: int | None = None  # model 701 ConnSt: 0 disconnected, 1 connected
    inverter_state: int | None = None  # model 701 InvSt
    grid_v: float | None = None  # model 701 LNV
    solar_w: float | None = None  # register 15502
    solar_module_w: float | None = None  # model 502 OutPw, when the aGate has it
    home_w: float | None = None  # register 16000
    home_coarse_w: float | None = None  # register 15506
    solar_lifetime_wh: float | None = None  # registers 15510-15511
    raw: dict[str, Any] = field(default_factory=dict)


async def _read_models(client: ModbusClient, d: AGateData, models: dict[int, int]) -> None:
    if (a := models.get(1)) is not None:
        r = await client.read(a, 68)
        d.maker, d.model, d.firmware, d.serial = text(r[2:18]), text(r[18:34]), text(r[42:50]), text(r[50:66])
    if (a := models.get(713)) is not None:
        r = await client.read(a, 9)
        d.raw["713"] = r
        d.soc_pct = scaled(u16(r[4]), s16(r[8]))
    if (a := models.get(714)) is not None:
        r = await client.read(a, 20)
        d.raw["714"] = r
        d.battery_w = scaled(s16(r[6]), s16(r[17]))
    if (a := models.get(701)) is not None:
        r = await client.read(a, 121)
        d.raw["701"] = {"St": r[3], "InvSt": r[4], "ConnSt": r[5], "W": r[10], "LNV": r[16], "W_SF": r[116],
                        "V_SF": r[114]}
        d.ac_w = scaled(s16(r[10]), s16(r[116]))
        d.connected, d.inverter_state = u16(r[5]), u16(r[4])
        d.grid_v = scaled(u16(r[16]), s16(r[114]))
    if (a := models.get(502)) is not None:
        r = await client.read(a, 30)
        d.raw["502"] = r
        d.solar_module_w = scaled(s16(r[23]), s16(r[4]))


async def _read_extension(client: ModbusClient, d: AGateData) -> None:
    try:
        r = await client.read(15500, 14)
    except ModbusError:
        return
    d.raw["15500"] = r
    d.solar_w, d.home_coarse_w = float(r[2]), float(r[6])
    d.solar_lifetime_wh = u32(r[10], r[11])
    try:
        d.home_w = float((await client.read(16000, 1))[0])
        d.raw["16000"] = d.home_w
    except ModbusError:
        d.home_w = d.home_coarse_w


async def read_agate(host: str, *, port: int = PORT, units: tuple[int, ...] = UNITS, timeout: float = 5.0,
                     layout: tuple[int, int, dict[int, int]] | None = None) -> tuple[AGateData, tuple]:
    """Read everything once. Returns the data, and the layout (unit, base, models) to pass next time."""
    last: Exception = LookupError("no unit ID to try")
    for unit in ([layout[0]] if layout else list(units)):
        try:
            async with ModbusClient(host, port, unit, timeout) as client:
                if layout:
                    base, models = layout[1], layout[2]
                else:
                    base, models = await find_models(client)
                d = AGateData(unit=unit, base=base, models=sorted(models))
                await _read_models(client, d, models)
                await _read_extension(client, d)
                return d, (unit, base, models)
        except (ModbusError, LookupError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
            last = exc  # try the next unit ID
    raise last


def reading_from_agate(d: AGateData) -> FranklinReading:
    return FranklinReading(
        soc_pct=d.soc_pct,
        battery_w=d.battery_w,
        home_w=d.home_w,
        grid_w=None if d.ac_w is None else GRID_SIGN * d.ac_w,
        solar_w=d.solar_w if d.solar_w is not None else d.solar_module_w,
        off_grid=None,  # not trusted until confirmed during a real outage
    )


class AGateModbus:
    """Reads the aGate directly. Connects for each reading and lets go, so other Modbus users get a turn."""

    min_interval_s = 10

    def __init__(self, host: str, port: int = PORT) -> None:
        self.host, self.port = host, port
        self._layout: tuple | None = None

    @property
    def description(self) -> str:
        return f"aGate at {self.host} (Modbus)"

    async def read(self) -> FranklinReading:
        try:
            d, self._layout = await read_agate(self.host, port=self.port, layout=self._layout)
        except Exception:
            self._layout = None  # look again next time: maybe the aGate restarted or changed
            raise
        return reading_from_agate(d)

    async def close(self) -> None:
        pass
