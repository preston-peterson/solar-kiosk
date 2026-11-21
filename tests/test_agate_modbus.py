"""The aGate over Modbus TCP, against a fake aGate: a real Modbus TCP server with an invented register map."""

import asyncio
import struct

import pytest

from solar_kiosk import agate_check
from solar_kiosk.sources import agate_modbus as am

SERIAL = "EXAMPLE00001234"


def text(s: str, size: int) -> list[int]:
    b = s.encode().ljust(2 * size, b"\0")
    return list(struct.unpack(f">{size}H", b))


def agate_map(*, base=0, soc_raw=620, battery=-1000, ac=-2100, solar=4300, home=1200, home_coarse=1200,
              lifetime=15_882_870, hires=True, extension=True) -> dict[int, int]:
    """Registers laid out like an aGate's (SunSpec models 1, 701, 713, 714, 502, then FranklinWH's own)."""
    regs: dict[int, int] = {}

    def put(address, values):
        for i, v in enumerate(values):
            regs[address + i] = v & 0xFFFF

    put(base, am.SUNS)
    address = base + 2

    def model(mid, size, **points):
        nonlocal address
        block = [0] * size
        block[0], block[1] = mid, size - 2
        for offset, value in points.items():
            block[int(offset[1:])] = value
        put(address, block)
        address += size

    common = [0] * 68
    common[2:18], common[18:34] = text("FranklinWH", 16), text("aGate X-20", 16)
    common[42:50], common[50:66] = text("V12R02B85D00", 8), text(SERIAL, 16)
    common[0], common[1] = 1, 66
    put(address, common)
    address += 68
    model(701, 155, o4=3, o5=1, o10=ac, o16=2401, o114=-1, o116=0)
    model(713, 9, o4=soc_raw, o8=-1)
    model(714, 20, o6=battery, o17=0)
    model(502, 30, o23=solar, o4=0)
    put(address, [0xFFFF, 0])
    if extension:
        put(15500, [1, 0, solar, solar, 0, 0, home_coarse, 2, 8, 15, lifetime >> 16, lifetime & 0xFFFF, 0, 0])
        if hires:
            put(16000, [home])
    return regs


class FakeAGate:
    """Answers Modbus function 3 for one unit ID. Anything else gets a Modbus exception, like a real device."""

    def __init__(self, regs: dict[int, int], unit: int = 2) -> None:
        self.regs, self.unit, self.requests = regs, unit, []

    async def __aenter__(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    async def handle(self, reader, writer):
        try:
            while True:
                tid, _proto, length, unit = struct.unpack(">HHHB", await reader.readexactly(7))
                pdu = await reader.readexactly(length - 1)
                fc = pdu[0]
                self.requests.append((unit, fc, *struct.unpack(">HH", pdu[1:5])))
                address, count = struct.unpack(">HH", pdu[1:5])
                if unit != self.unit:
                    reply = bytes([fc | 0x80, 0x0B])
                elif fc != 3:
                    reply = bytes([fc | 0x80, 0x01])
                elif any(address + i not in self.regs for i in range(count)):
                    reply = bytes([0x83, 0x02])
                else:
                    reply = bytes([3, 2 * count]) + struct.pack(f">{count}H", *(self.regs[address + i] for i in range(count)))
                writer.write(struct.pack(">HHHB", tid, 0, len(reply) + 1, unit) + reply)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


def run(coro):
    return asyncio.run(coro)


def read(regs, unit=2, **kw):
    async def go():
        async with FakeAGate(regs, unit) as fake:
            d, layout = await am.read_agate("127.0.0.1", port=fake.port, timeout=2, **kw)
            return d, layout, fake.requests
    return run(go())


def test_reads_everything():
    d, (unit, base, models), _ = read(agate_map())
    assert (unit, base, sorted(models)) == (2, 0, [1, 502, 701, 713, 714])
    assert (d.maker, d.model, d.firmware, d.serial) == ("FranklinWH", "aGate X-20", "V12R02B85D00", SERIAL)
    assert (d.soc_pct, d.battery_w, d.ac_w, d.solar_w, d.home_w) == (62.0, -1000, -2100, 4300, 1200)
    assert (d.connected, d.inverter_state, d.grid_v, d.solar_lifetime_wh) == (1, 3, 240.1, 15_882_870)
    assert d.solar_module_w == 4300 and d.home_coarse_w == 1200


def test_only_ever_reads():
    """The read-only promise: every request is function 3, and long reads are split to Modbus's 125 limit."""
    _, _, requests = read(agate_map())
    assert requests and all(fc == 3 for _unit, fc, _a, _n in requests)
    assert all(n <= am.MAX_REGISTERS for *_x, n in requests)


def test_tries_unit_1_when_2_doesnt_answer():
    d, layout, _ = read(agate_map(), unit=1)
    assert d.unit == 1 and layout[0] == 1


def test_finds_the_map_at_40000():
    d, _, _ = read(agate_map(base=40000))
    assert d.base == 40000 and d.soc_pct == 62.0


def test_unreported_values_are_none_not_zero():
    d, _, _ = read(agate_map(soc_raw=0xFFFF, battery=0x8000))
    assert d.soc_pct is None and d.battery_w is None


def test_without_register_16000_uses_the_coarse_home_load():
    d, _, _ = read(agate_map(hires=False, home_coarse=1300))
    assert d.home_w == 1300


def test_without_franklinwh_registers_solar_comes_from_model_502():
    d, _, _ = read(agate_map(extension=False))
    assert d.solar_w is None and d.home_w is None
    assert am.reading_from_agate(d).solar_w == 4300


def test_a_device_without_sunspec_is_refused():
    with pytest.raises(LookupError):
        read({0: 1, 1: 2, 40000: 3, 40001: 4})


def test_reading_for_the_screen():
    d, _, _ = read(agate_map())
    r = am.reading_from_agate(d)
    assert (r.soc_pct, r.battery_w, r.grid_w, r.solar_w, r.home_w) == (62.0, -1000, -2100 * am.GRID_SIGN, 4300, 1200)
    assert r.off_grid is None  # not trusted until seen during a real outage


def test_the_reader_remembers_the_layout_and_looks_again_after_a_failure():
    async def go():
        async with FakeAGate(agate_map()) as fake:
            reader = am.AGateModbus("127.0.0.1", port=fake.port)
            await reader.read()
            scans = sum(1 for _u, _f, a, n in fake.requests if (a, n) == (0, 2))
            await reader.read()
            assert sum(1 for _u, _f, a, n in fake.requests if (a, n) == (0, 2)) == scans  # no second scan
            soc = reader._layout[2][713] + 4
            saved = fake.regs.pop(soc)  # the aGate stops answering a register (say, after a firmware update)
            with pytest.raises(am.ModbusError):
                await reader.read()
            fake.regs[soc] = saved
            assert (await reader.read()).soc_pct == 62.0
            assert sum(1 for _u, _f, a, n in fake.requests if (a, n) == (0, 2)) == scans + 1  # looked again
    run(go())


# --- check-agate ---------------------------------------------------------------------------------------


async def no_wait(_s):
    pass


def check(regs):
    lines = []

    async def go():
        async with FakeAGate(regs) as fake:
            return await agate_check.check("127.0.0.1", port=fake.port, samples=2, out=lines.append, sleep=no_wait)
    code = run(go())
    return code, "\n".join(lines)


def test_check_reports_the_grid_direction_and_hides_the_serial():
    code, out = check(agate_map())  # 4300 + -1000 + -2100 = 1200 = home
    assert code == 0 and "+ while importing from the grid. solar-kiosk already assumes this" in out
    assert SERIAL not in out and "serial ending 1234" in out and "aGate X-20" in out


def test_check_spots_the_other_direction():
    code, out = check(agate_map(ac=2100))  # only 4300 - 1000 - 2100 = 1200 adds up
    assert code == 0 and "needs a change for this aGate" in out


def test_check_with_little_grid_flow_says_so():
    _, out = check(agate_map(ac=50, home=3350, home_coarse=3300))
    assert "almost nothing is flowing" in out


def test_check_explains_a_refused_connection():
    async def closed_port():
        server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        return port
    lines = []
    port = run(closed_port())
    assert run(agate_check.check("127.0.0.1", port=port, out=lines.append, sleep=no_wait)) == 1
    assert "Modbus TCP is turned on" in "\n".join(lines)
