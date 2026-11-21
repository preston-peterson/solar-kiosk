"""Login back-off, so a wrong password can't get an account locked by repeated attempts."""

from __future__ import annotations

import time
from collections.abc import Callable


class LoginBackoffActive(Exception):
    """Raised instead of attempting a login while the back-off is running."""


class LoginBackoff:
    def __init__(self, name: str, delay_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.name = name
        self.delay_s = delay_s
        self._clock = clock
        self._retry_at: float | None = None
        self.last_error: str | None = None

    def check(self) -> None:
        """Raise LoginBackoffActive if a login failed less than delay_s ago."""
        if self._retry_at is None:
            return
        left = self._retry_at - self._clock()
        if left > 0:
            raise LoginBackoffActive(
                f"{self.name} login failed ({self.last_error}); next try in {max(1, round(left / 60))} min"
            )

    def failed(self, error: str) -> None:
        self.last_error = error
        self._retry_at = self._clock() + self.delay_s

    def succeeded(self) -> None:
        self._retry_at = None
        self.last_error = None
