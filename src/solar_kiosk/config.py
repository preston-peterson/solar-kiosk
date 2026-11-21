"""Settings: a config file plus a separate secrets file (mode 600), both outside the repo.

The app writes these itself (from the setup page, and when it finds a device), so nobody has to edit
them. Default locations follow the XDG conventions:
  ~/.config/solar-kiosk/config.toml     see config.example.toml
  ~/.config/solar-kiosk/secrets.toml    see secrets-example.toml
  ~/.local/state/solar-kiosk/           saved state (solar baseline, cached Enphase token)
"""

from __future__ import annotations

import json
import os
import stat
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class EnphaseConfig:
    host: str = ""  # empty: found automatically on the network
    serial: str = ""  # filled in once found; used to find the gateway again if its address changes
    username: str = ""  # either an email and password (the app renews the gateway's token itself) ...
    password: str = ""
    token: str = ""  # ... or a token the owner made on Enphase's token site, so the app never sees a password
    poll_s: float = 15

    @property
    def uses_token(self) -> bool:
        """True when the owner pasted a token and no login is saved: nothing renews it but them."""
        return bool(self.token) and not (self.username and self.password)


@dataclass(frozen=True)
class FranklinConfig:
    email: str
    password: str
    gateway_id: str = ""  # empty: the first aGate on the account
    poll_s: float = 30


@dataclass(frozen=True)
class TempestConfig:
    enabled: bool = True
    port: int = 50222
    serial: str = ""


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


def default_config_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "solar-kiosk" / "config.toml"


def default_state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "solar-kiosk"


@dataclass(frozen=True)
class Config:
    enphase: EnphaseConfig | None = None
    franklin: FranklinConfig | None = None
    tempest: TempestConfig = field(default_factory=TempestConfig)
    units: str = "us"
    state_dir: Path = field(default_factory=default_state_dir)
    enphase_missing: str = "Not set up yet"
    franklin_missing: str = "Not set up yet"

    def with_enphase(self, **changes) -> Config:
        return replace(self, enphase=replace(self.enphase or EnphaseConfig(), **changes))


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"{path} not found") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None


def _read_secrets(path: Path) -> dict:
    if not path.exists():
        return {}
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(f"{path} can be read by other users; run: chmod 600 {path}")
    return _read_toml(path)


def secrets_path(path: Path) -> Path:
    return path.with_name("secrets.toml")


def load(path: Path | None = None) -> Config:
    path = (path or default_config_path()).expanduser()
    raw = _read_toml(path)
    sec_path = secrets_path(path)
    secrets = _read_secrets(sec_path)

    units = raw.get("display", {}).get("units", "us")
    if units not in ("us", "metric"):
        raise ConfigError(f'display.units must be "us" or "metric", not {units!r}')
    state_dir = Path(raw.get("paths", {}).get("state_dir") or default_state_dir()).expanduser()

    enphase, enphase_missing = None, "Not set up yet"
    e_raw, e_sec = raw.get("enphase", {}), secrets.get("enphase", {})
    if e_raw.get("enabled", True) is False:
        enphase_missing = "Off in the settings"
    elif e_raw.get("host") or e_sec:
        enphase = EnphaseConfig(
            host=str(e_raw.get("host", "")),
            serial=str(e_raw.get("serial", "")),
            username=str(e_sec.get("username", "")),
            password=str(e_sec.get("password", "")),
            token=str(e_sec.get("token", "")),
            poll_s=float(e_raw.get("poll_seconds", 15)),
        )
        if not (enphase.token or enphase.username):
            raise ConfigError(f"Add an [enphase] username and password (or a token) to {sec_path}")

    franklin, franklin_missing = None, "Not set up yet"
    f_raw, f_sec = raw.get("franklin", {}), secrets.get("franklin", {})
    source = f_raw.get("source", "cloud" if (f_raw or f_sec) else "off")
    if source == "cloud":
        if not (f_sec.get("email") and f_sec.get("password")):
            raise ConfigError(f"Add a [franklin] email and password to {sec_path}")
        franklin = FranklinConfig(
            email=str(f_sec["email"]),
            password=str(f_sec["password"]),
            gateway_id=str(f_raw.get("gateway_id", "")),
            poll_s=float(f_raw.get("poll_seconds", 30)),
        )
    elif source == "off":
        franklin_missing = "Off in the settings" if f_raw else franklin_missing
    else:
        raise ConfigError(f'franklin.source must be "cloud" or "off", not {source!r}')

    t_raw = raw.get("tempest", {})
    tempest = TempestConfig(
        enabled=bool(t_raw.get("enabled", True)),
        port=int(t_raw.get("port", 50222)),
        serial=str(t_raw.get("serial", "")),
    )
    return Config(enphase, franklin, tempest, units, state_dir, enphase_missing, franklin_missing)


def _toml(sections: dict[str, dict]) -> str:
    """A small TOML writer for flat sections of str, bool, int and float values."""
    out = []
    for name, values in sections.items():
        out.append(f"[{name}]")
        for key, v in values.items():
            if isinstance(v, bool):
                text = "true" if v else "false"
            elif isinstance(v, (int, float)):
                text = repr(v)
            else:
                text = json.dumps(str(v), ensure_ascii=False)  # a valid TOML basic string
            out.append(f"{key} = {text}")
        out.append("")
    return "\n".join(out)


def _write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def save(c: Config, path: Path | None = None) -> None:
    """Write the config and secrets files (secrets with mode 600), replacing them whole."""
    path = (path or default_config_path()).expanduser()
    conf: dict[str, dict] = {}
    secrets: dict[str, dict] = {}
    if c.enphase:
        e = c.enphase
        conf["enphase"] = {"host": e.host, "serial": e.serial, "poll_seconds": e.poll_s}
        secrets["enphase"] = {"username": e.username, "password": e.password, "token": e.token}
    elif c.enphase_missing == "Off in the settings":
        conf["enphase"] = {"enabled": False}
    if c.franklin:
        f = c.franklin
        conf["franklin"] = {"source": "cloud", "gateway_id": f.gateway_id, "poll_seconds": f.poll_s}
        secrets["franklin"] = {"email": f.email, "password": f.password}
    else:
        conf["franklin"] = {"source": "off"}
    conf["tempest"] = {"enabled": c.tempest.enabled, "port": c.tempest.port, "serial": c.tempest.serial}
    conf["display"] = {"units": c.units}
    conf["paths"] = {"state_dir": str(c.state_dir)}
    header = "# Written by solar-kiosk. The setup page changes these for you.\n\n"
    _write(path, header + _toml(conf), 0o644)
    _write(secrets_path(path), header + _toml(secrets), 0o600)
