"""The settings password.

The first person to open Settings creates it (like Home Assistant's first-run setup). It is stored only as
a scrypt hash, in a file readable only by the app's user. A browser that has typed it once gets a cookie
holding the current session key. Changing or resetting the password makes a new key, which signs out
every other browser.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path

from .config import _write

MIN_LENGTH = 8
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}  # about 16 MB and well under a second on a Pi 4


class AuthError(Exception):
    pass


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)


class Auth:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict | None = None
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if {"salt", "hash", "session"} <= data.keys():
                    self._data = data
            except (OSError, ValueError):
                self._data = None  # unreadable: treated as no password, so it can be set again

    @property
    def has_password(self) -> bool:
        return self._data is not None

    @property
    def session_key(self) -> str:
        if self._data is None:
            raise AuthError("No settings password yet.")
        return self._data["session"]

    def session_ok(self, cookie: str) -> bool:
        return self._data is not None and bool(cookie) and hmac.compare_digest(cookie, self._data["session"])

    def check(self, password: str) -> bool:
        if self._data is None:
            return False
        got = _hash(password, bytes.fromhex(self._data["salt"]))
        return hmac.compare_digest(got.hex(), self._data["hash"])

    def create(self, password: str) -> str:
        """Set the first password. Returns the session key for this browser's cookie."""
        if self._data is not None:
            raise AuthError("A settings password already exists.")
        return self._set(password)

    def change(self, old: str, new: str) -> str:
        if not self.check(old):
            raise AuthError("The current password isn't right.")
        return self._set(new)

    def reset(self) -> None:
        """Forget the password (and sign everyone out). The next visitor to Settings creates a new one."""
        self._data = None
        self.path.unlink(missing_ok=True)

    def _set(self, password: str) -> str:
        if len(password) < MIN_LENGTH:
            raise AuthError(f"Use at least {MIN_LENGTH} characters.")
        salt = secrets.token_bytes(16)
        self._data = {"salt": salt.hex(), "hash": _hash(password, salt).hex(),
                      "session": secrets.token_urlsafe(32)}
        _write(self.path, json.dumps(self._data), 0o600)
        return self._data["session"]
