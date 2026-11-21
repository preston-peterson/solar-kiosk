"""Find the devices on the home network, so nobody has to type an address or a serial number.

  Enphase gateway   announces itself over mDNS as _enphase-envoy._tcp, with its serial number
  Tempest           broadcasts on UDP 50222 every few seconds
  FranklinWH aGate  listed on the FranklinWH account (needs the login)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

ENPHASE_SERVICE = "_enphase-envoy._tcp.local."


@dataclass(frozen=True)
class FoundGateway:
    host: str
    serial: str


@dataclass(frozen=True)
class FoundStation:
    serial: str
    hub: str
    address: str  # the hub's IP


def gateway_from_info(info: Any) -> FoundGateway | None:
    """A FoundGateway from a resolved zeroconf ServiceInfo, preferring an IPv4 address."""
    addresses = info.parsed_addresses() if info else []
    if not addresses:
        return None
    host = next((a for a in addresses if ":" not in a), addresses[0])
    serial = (info.properties or {}).get(b"serialnum") or b""
    return FoundGateway(host, serial.decode(errors="replace"))


async def find_enphase(timeout: float = 4.0) -> list[FoundGateway]:
    """Enphase gateways announcing themselves on the local network."""
    from zeroconf import ServiceStateChange
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

    names: set[str] = set()

    def on_change(zeroconf, service_type, name, state_change) -> None:
        if state_change is ServiceStateChange.Added:
            names.add(name)

    azc = AsyncZeroconf()
    browser = AsyncServiceBrowser(azc.zeroconf, ENPHASE_SERVICE, handlers=[on_change])
    try:
        await asyncio.sleep(timeout)
        found = []
        for name in sorted(names):
            info = AsyncServiceInfo(ENPHASE_SERVICE, name)
            if await info.async_request(azc.zeroconf, 3000):
                gw = gateway_from_info(info)
                if gw and gw not in found:
                    found.append(gw)
        return found
    finally:
        await browser.async_cancel()
        await azc.async_close()


def pick_gateway(found: list[FoundGateway], serial: str = "") -> FoundGateway | None:
    """The gateway with this serial; or, with no serial given, the only one found."""
    if serial:
        return next((g for g in found if g.serial == serial), None)
    return found[0] if len(found) == 1 else None


class _Collector(asyncio.DatagramProtocol):
    def __init__(self, on_station: Callable[[FoundStation], None]) -> None:
        self.on_station = on_station

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            msg = json.loads(data)
            serial, hub = msg.get("serial_number"), msg.get("hub_sn") or ""
        except (ValueError, AttributeError):
            return
        if isinstance(serial, str) and serial and msg.get("type") != "hub_status":
            self.on_station(FoundStation(serial, hub, addr[0]))


async def find_tempest(timeout: float = 15.0, *, host: str = "0.0.0.0", port: int = 50222,
                       settle: float = 4.0) -> list[FoundStation]:
    """Weather stations broadcasting on this network. Returns `settle` seconds after the first one is
    heard (to catch any others), or after `timeout` with an empty list."""
    found: dict[str, FoundStation] = {}
    first = asyncio.Event()

    def on_station(station: FoundStation) -> None:
        found.setdefault(station.serial, station)
        first.set()

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Collector(on_station), local_addr=(host, port), reuse_port=True
    )
    try:
        try:
            await asyncio.wait_for(first.wait(), timeout)
            await asyncio.sleep(settle)
        except asyncio.TimeoutError:
            pass
        return sorted(found.values(), key=lambda s: s.serial)
    finally:
        transport.close()


async def franklin_gateways(email: str, password: str, client_factory: Callable[..., Any] | None = None) -> list[str]:
    """The aGate serial numbers on a FranklinWH account. Raises the library's login errors."""
    from .sources.franklin import _default_client

    client = (client_factory or _default_client)(email, password, "")
    await client.refresh_token()
    return gateway_ids(await client.get_home_gateway_list())


def gateway_ids(result: Any) -> list[str]:
    return [str(g["id"]) for g in result or [] if isinstance(g, dict) and g.get("id")]
