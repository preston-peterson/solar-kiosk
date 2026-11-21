"""Builds a Poller from the settings (real devices) or from a demo scenario."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import config as cfg
from .daily import DailySolar
from .discover import FoundGateway, find_enphase
from .poller import Poller
from .sources import tempest
from .sources.tempest import TempestState


def build_demo(scenario: str = "sunny", speed: float = 1.0, interval: float = 15) -> Poller:
    from .sources.demo import DemoEnphase, DemoFranklin, DemoSystem

    system = DemoSystem(scenario, speed=speed)
    weather = TempestState(clock=system.clock)
    daily = DailySolar(None)
    daily.update(system.midnight_lifetime_wh, 0.0, system.midnight)  # as if running since midnight

    def broadcast() -> None:
        for msg in system.tempest_messages():
            weather.handle(msg)

    return Poller(
        enphase=DemoEnphase(system),
        franklin=DemoFranklin(system),
        tempest=weather,
        daily=daily,
        interval_s=interval,
        clock=system.clock,
        before_tick=broadcast,
    )


async def build_live(c: cfg.Config, config_path: Path | None = None, interval: float | None = None,
                     on_change: Callable[[cfg.Config], Any] | None = None) -> tuple[Poller, list]:
    """A Poller for the real devices, plus things to close when it stops (the Tempest listener).

    When the Enphase gateway is found at a new address, the settings are saved (if config_path is
    given) and on_change is told.
    """
    from .sources.enphase import EnphaseGateway
    from .sources.franklin import FranklinCloud

    enphase = franklin = weather = None
    closers = []
    current = c

    def remember(gw: FoundGateway) -> None:
        nonlocal current
        current = current.with_enphase(host=gw.host, serial=gw.serial)
        if config_path is not None:
            cfg.save(current, config_path)
        if on_change:
            on_change(current)

    if c.enphase:
        # with a pasted token there's nothing to cache: it's already in the secrets file
        cache = None if c.enphase.uses_token else c.state_dir / "enphase-token"
        enphase = EnphaseGateway(c.enphase.host, c.enphase.username, c.enphase.password,
                                 cache, serial=c.enphase.serial, token=c.enphase.token,
                                 discover=find_enphase, on_found=remember)
    if c.franklin:
        franklin = FranklinCloud(c.franklin.email, c.franklin.password, c.franklin.gateway_id)
    if c.tempest.enabled:
        weather = TempestState(c.tempest.serial)
        closers.append(await tempest.listen(weather, port=c.tempest.port))
    poller = Poller(
        enphase=enphase,
        franklin=franklin,
        tempest=weather,
        daily=DailySolar(c.state_dir / "solar-baseline.json"),
        interval_s=interval or (c.enphase.poll_s if c.enphase else 15),
        franklin_interval_s=c.franklin.poll_s if c.franklin else 30,
        enphase_missing=c.enphase_missing,
        franklin_missing=c.franklin_missing,
    )
    return poller, closers
