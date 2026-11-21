import asyncio
import json
import socket
from types import SimpleNamespace as NS

from solar_kiosk import discover
from solar_kiosk.discover import FoundGateway, FoundStation, gateway_from_info, pick_gateway


def test_gateway_from_mdns_prefers_ipv4():
    info = NS(parsed_addresses=lambda: ["fe80::1", "192.0.2.10"], properties={b"serialnum": b"000000000001"})
    assert gateway_from_info(info) == FoundGateway("192.0.2.10", "000000000001")


def test_gateway_without_address_or_serial():
    assert gateway_from_info(NS(parsed_addresses=lambda: [], properties={})) is None
    assert gateway_from_info(NS(parsed_addresses=lambda: ["192.0.2.10"], properties=None)).serial == ""


def test_pick_gateway():
    a, b = FoundGateway("192.0.2.10", "1"), FoundGateway("192.0.2.11", "2")
    assert pick_gateway([a]) == a and pick_gateway([a, b]) is None
    assert pick_gateway([a, b], "2") == b and pick_gateway([a], "9") is None


def test_hears_a_tempest():
    async def go():
        task = asyncio.create_task(discover.find_tempest(timeout=2, host="127.0.0.1", port=port, settle=0.2))
        await asyncio.sleep(0.1)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for msg in ({"type": "hub_status", "serial_number": "HB-00000001"},
                        {"type": "rapid_wind", "serial_number": "ST-00000001", "hub_sn": "HB-00000001", "ob": [0, 1, 2]}):
                sock.sendto(json.dumps(msg).encode(), ("127.0.0.1", port))
            sock.sendto(b"junk", ("127.0.0.1", port))
        return await task

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:  # pick a free port
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert asyncio.run(go()) == [FoundStation("ST-00000001", "HB-00000001", "127.0.0.1")]


def test_no_tempest():
    assert asyncio.run(discover.find_tempest(timeout=0.2, host="127.0.0.1", port=0)) == []


def test_franklin_gateways():
    class Client:
        def __init__(self, *args):
            pass

        async def refresh_token(self):
            pass

        async def get_home_gateway_list(self):
            return [{"id": "EXAMPLE-AGATE-1"}, {"name": "no id"}, "junk"]

    assert asyncio.run(discover.franklin_gateways("you@example.com", "pw", Client)) == ["EXAMPLE-AGATE-1"]
