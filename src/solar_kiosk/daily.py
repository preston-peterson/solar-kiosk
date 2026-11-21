"""Today's solar energy from the Enphase lifetime counter, and today's peak solar power.

The gateway's own daily figures can't be trusted (on current firmware whToday repeats the lifetime
total), so today's energy is the lifetime counter minus its value at the start of the day. That
start-of-day baseline is saved, so a restart during the day keeps the full-day total.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

NIGHT_W = 10  # below this the panels are idle, so a baseline taken now misses nothing
PEAK_SAVE_EVERY_S = 300  # throttle peak saves: the state file may live on an SD card


@dataclass(frozen=True)
class SolarDay:
    kwh: float
    since: datetime | None  # set when kwh covers only part of today
    peak_w: float
    peak_time: datetime


class DailySolar:
    def __init__(self, state_path: Path | None) -> None:
        self._path = state_path
        self._day: date | None = None
        self._baseline_wh = 0.0
        self._since: datetime | None = None
        self._peak_w = 0.0
        self._peak_time: datetime | None = None
        self._peak_saved_at: datetime | None = None

    def update(self, lifetime_wh: float, solar_w: float, now: datetime) -> SolarDay:
        """lifetime_wh: the Enphase counter. solar_w: the best current solar figure, for the peak."""
        today = now.date()
        if self._day != today:
            self._start_day(today, lifetime_wh, solar_w, now)
        if solar_w > self._peak_w:
            self._peak_w, self._peak_time = solar_w, now
            if self._peak_saved_at is None or (now - self._peak_saved_at).total_seconds() >= PEAK_SAVE_EVERY_S:
                self._save(now)
        kwh = max(0.0, lifetime_wh - self._baseline_wh) / 1000
        return SolarDay(kwh, self._since, self._peak_w, self._peak_time or now)

    def _start_day(self, today: date, lifetime_wh: float, solar_w: float, now: datetime) -> None:
        self._day = today
        saved = self._load(today)
        if saved is not None:
            self._baseline_wh = saved["baseline_wh"]
            self._since = datetime.fromisoformat(saved["since"]) if saved.get("since") else None
            self._peak_w = saved.get("peak_w", 0.0)
            self._peak_time = datetime.fromisoformat(saved["peak_time"]) if saved.get("peak_time") else None
        else:
            self._baseline_wh = lifetime_wh
            self._since = None if solar_w < NIGHT_W else now
            self._peak_w, self._peak_time = 0.0, None
        self._save(now)
        log.info(
            "Solar day %s: baseline %.0f Wh%s",
            today,
            self._baseline_wh,
            f" (partial, since {self._since:%H:%M})" if self._since else "",
        )

    def _load(self, today: date) -> dict | None:
        if self._path is None or not self._path.exists():
            return None
        try:
            saved = json.loads(self._path.read_text())
            return saved if saved.get("date") == today.isoformat() else None
        except (OSError, ValueError, AttributeError) as exc:
            log.warning("Ignoring unreadable solar baseline %s: %s", self._path, exc)
            return None

    def _save(self, now: datetime) -> None:
        self._peak_saved_at = now
        if self._path is None:
            return
        state = {
            "date": self._day.isoformat() if self._day else None,
            "baseline_wh": self._baseline_wh,
            "since": self._since.isoformat() if self._since else None,
            "peak_w": self._peak_w,
            "peak_time": self._peak_time.isoformat() if self._peak_time else None,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state))
            os.replace(tmp, self._path)
        except OSError as exc:
            log.error("Could not save solar baseline: %s", exc)
