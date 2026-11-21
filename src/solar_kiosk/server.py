"""The web server: the kiosk page (for computers and the kiosk's own screen) and the settings page.

Anyone on the home network can view the kiosk page. Settings need the settings password, which the first
visitor creates (see auth.py). Until a device is set up, the app runs the demo.
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import logging
import socket
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any

import segno
from aiohttp import web

from . import __version__, checks
from . import config as cfg
from .auth import Auth, AuthError
from .enphase_token import read_token, reminder, unusable
from .models import Snapshot
from .poller import Poller
from .runner import build_demo, build_live

log = logging.getLogger(__name__)

COOKIE = "solar_kiosk_session"
PAGES = resources.files("solar_kiosk") / "web"


def lan_address() -> str:
    """This machine's address on the home network."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # sends nothing; just picks the outgoing interface
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _json_default(o: Any) -> Any:
    return o.isoformat() if isinstance(o, datetime) else str(o)


def to_json(data: Any) -> str:
    if dataclasses.is_dataclass(data):
        data = dataclasses.asdict(data)
    return json.dumps(data, default=_json_default)


def is_set_up(c: cfg.Config | None) -> bool:
    return c is not None and (c.enphase is not None or c.franklin is not None)


def enphase_use(body: dict, saved: cfg.EnphaseConfig) -> str:
    """Which Enphase sign-in the settings page chose: "token" or "password". Older pages don't say, so then
    it's whatever they sent, or what's saved."""
    use = body.get("use")
    if use in ("token", "password"):
        return use
    if body.get("token"):
        return "token"
    if body.get("username") or body.get("password"):
        return "password"
    return "token" if saved.uses_token or not saved.username else "password"


def is_local(request: web.Request) -> bool:
    """A request from the Pi itself: its own screen (physical presence) or a terminal on it."""
    return request.remote in ("127.0.0.1", "::1")


BuildLive = Callable[..., Awaitable[tuple[Poller, list]]]


class App:
    def __init__(self, config_path: Path, *, port: int = 8080, demo: str = "sunny",
                 build: BuildLive = build_live, address: Callable[[], str] = lan_address) -> None:
        self.config_path = config_path
        self.port = port
        self.demo = demo
        self._build = build
        self._address = address
        self.config: cfg.Config | None = None
        self.config_error: str | None = None
        self.mode = "demo"
        self.snapshot: Snapshot | None = None
        self.limiter = checks.TryLimiter()
        self.auth = Auth(config_path.with_name("auth.json"))
        self.password_tries = checks.TryLimiter(max_failures=5)
        self._poller: Poller | None = None
        self._closers: list = []
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @property
    def state_dir(self) -> Path:
        return self.config.state_dir if self.config else cfg.default_state_dir()

    def setup_url(self) -> str:
        return f"http://{self._address()}:{self.port}/setup"

    async def start(self) -> None:
        if self.config_path.exists():
            try:
                self.config = cfg.load(self.config_path)
            except cfg.ConfigError as exc:
                self.config_error = str(exc)
                log.warning("Settings problem: %s", exc)
        await self.use(self.config)

    async def use(self, c: cfg.Config | None) -> None:
        """Run on these settings (or the demo, if they don't name any device yet)."""
        await self.stop()
        self.config, self.snapshot = c, None
        if is_set_up(c):
            def changed(new: cfg.Config) -> None:
                self.config = new

            self._poller, self._closers = await self._build(c, self.config_path, on_change=changed)
            self.mode = "live"
        else:
            self._poller, self.mode = build_demo(self.demo, interval=5), "demo"
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._poller.run(self._got, stop=self._stop))

    def _got(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot

    async def stop(self) -> None:
        if self._task:
            self._stop.set()
            try:
                await asyncio.wait_for(self._task, 30)
            except (asyncio.TimeoutError, Exception) as exc:
                log.warning("Poller didn't stop cleanly: %r", exc)
                self._task.cancel()
            self._task = None
        if self._poller:
            await self._poller.close()
            self._poller = None
        for closer in self._closers:
            closer.close()
        self._closers = []

    def attention(self) -> list[str]:
        """Problems to show on the TV, in plain words."""
        out = []
        if self.config_error:
            out.append(f"Settings problem: {self.config_error}")
        token_ran_out = False
        if self.config and self.config.enphase and self.config.enphase.uses_token:
            now = datetime.now(timezone.utc)
            try:
                info = read_token(self.config.enphase.token)
                note, token_ran_out = reminder(info, now), info.expires is not None and info.expires <= now
            except ValueError:
                note = "The saved Enphase token can't be read. Open Settings to paste a new one."
            if note:
                out.append(note)
        s = self.snapshot
        if self.mode == "live" and s is not None:
            if s.enphase_error and self.config and self.config.enphase and not token_ran_out:
                out.append(f"Can't read the Enphase gateway: {s.enphase_error}")
            if s.franklin_error and self.config and self.config.franklin and s.franklin_updated is None:
                out.append(f"Can't read the aGate: {s.franklin_error}")
        return out

    def settings_view(self) -> dict:
        """The current settings for the setup page, without any passwords."""
        c = self.config or cfg.Config()
        e, f = c.enphase, c.franklin
        token_expires = ""
        if e and e.uses_token:
            try:
                expires = read_token(e.token).expires
                token_expires = expires.isoformat() if expires else ""
            except ValueError:
                pass
        return {
            "mode": self.mode,
            "enphase": {"on": e is not None, "use": "password" if e and not e.uses_token else "token",
                        "username": e.username if e else "", "host": e.host if e else "",
                        "serial": e.serial if e else "", "has_password": bool(e and e.password),
                        "has_token": bool(e and e.uses_token), "token_expires": token_expires,
                        "off": e is None and c.enphase_missing == "Off in the settings"},
            "franklin": {"on": f is not None, "email": f.email if f else "", "gateway_id": f.gateway_id if f else "",
                         "has_password": bool(f and f.password),
                         "off": f is None and c.franklin_missing == "Off in the settings"},
            "tempest": {"enabled": c.tempest.enabled, "serial": c.tempest.serial},
            "units": c.units,
        }

    def settings_from(self, body: dict) -> cfg.Config:
        """New settings from the setup page. A blank password keeps the saved one."""
        old = self.config or cfg.Config()
        enphase, e_missing = None, "Off in the settings"
        if (e := body.get("enphase")) is not None:
            prev = old.enphase or cfg.EnphaseConfig()
            host, serial = str(e.get("host", prev.host)).strip(), str(e.get("serial", prev.serial)).strip()
            if enphase_use(e, prev) == "token":  # keeps only the token: a saved email and password are deleted
                token = str(e.get("token") or prev.token).strip()
                if not token:
                    raise ValueError("Paste the Enphase token, or turn Enphase off.")
                if why := unusable(read_token(token), datetime.now(timezone.utc)):  # read_token: ValueError too
                    raise ValueError(why)
                enphase = cfg.EnphaseConfig(host=host, serial=serial, token=token, poll_s=prev.poll_s)
            else:  # keeps only the email and password: the app makes and renews its own token
                enphase = cfg.EnphaseConfig(host=host, serial=serial, username=str(e.get("username") or prev.username).strip(),
                                            password=str(e.get("password") or prev.password), poll_s=prev.poll_s)
                if not (enphase.username and enphase.password):
                    raise ValueError("Enter the Enphase email and password, or turn Enphase off.")
        franklin, f_missing = None, "Off in the settings"
        if (f := body.get("franklin")) is not None:
            prev_f = old.franklin
            franklin = cfg.FranklinConfig(
                email=str(f.get("email") or (prev_f.email if prev_f else "")).strip(),
                password=str(f.get("password") or (prev_f.password if prev_f else "")),
                gateway_id=str(f.get("gateway_id", prev_f.gateway_id if prev_f else "")).strip(),
                poll_s=prev_f.poll_s if prev_f else 30)
            if not (franklin.email and franklin.password):
                raise ValueError("Enter the FranklinWH email and password, or turn FranklinWH off.")
        t = body.get("tempest") or {}
        tempest = cfg.TempestConfig(enabled=bool(t.get("enabled", True)), port=old.tempest.port,
                                    serial=str(t.get("serial", old.tempest.serial)))
        units = body.get("units", old.units)
        if units not in ("us", "metric"):
            raise ValueError("Units must be US or metric.")
        # "Off in the settings" only for a device the page turned off, or Settings would show it off next time
        if enphase is not None:
            e_missing = cfg.Config.enphase_missing
        if franklin is not None:
            f_missing = cfg.Config.franklin_missing
        return cfg.Config(enphase, franklin, tempest, units, old.state_dir, e_missing, f_missing)


def make_web_app(app: App) -> web.Application:
    routes = web.RouteTableDef()

    def page(name: str) -> web.Response:
        return web.Response(text=(PAGES / name).read_text(), content_type="text/html")

    def allowed(request: web.Request) -> bool:
        return app.auth.session_ok(request.cookies.get(COOKIE, ""))

    def reply(data: Any, status: int = 200) -> web.Response:
        return web.Response(text=to_json(data), status=status, content_type="application/json")

    def guarded(handler):
        async def wrapper(request: web.Request) -> web.Response:
            if not allowed(request):
                return reply({"error": "Enter the settings password first."}, 403)
            return await handler(request)
        return wrapper

    @routes.get("/")
    async def kiosk(request):
        return page("kiosk.html")

    @routes.get("/setup")
    async def setup(request):
        return page("setup.html")

    def signed_in(data: Any, session: str) -> web.Response:
        resp = reply(data)
        resp.set_cookie(COOKIE, session, max_age=10 * 365 * 24 * 3600, httponly=True, samesite="Strict")
        return resp

    async def json_body(request: web.Request) -> dict:
        try:
            body = await request.json()
            return body if isinstance(body, dict) else {}
        except ValueError:
            return {}

    @routes.post("/api/auth/create")
    async def create_password(request):
        b = await json_body(request)
        if b.get("password") != b.get("confirm"):
            return reply({"ok": False, "error": "The two passwords don't match."})
        try:
            return signed_in({"ok": True}, app.auth.create(str(b.get("password", ""))))
        except AuthError as exc:
            return reply({"ok": False, "error": str(exc)})

    @routes.post("/api/auth/login")
    async def login(request):
        if app.password_tries.blocked("password", "solar-kiosk") is not None:
            return reply({"ok": False, "error": "Too many wrong passwords. Wait 15 minutes and try again."})
        b = await json_body(request)
        password = str(b.get("password", ""))
        if await asyncio.to_thread(app.auth.check, password):  # scrypt takes a moment; don't stall the readings
            return signed_in({"ok": True}, app.auth.session_key)
        app.password_tries.record("password", checks.Result(False, "", login_failed=True))
        return reply({"ok": False, "error": "That isn't the settings password."})

    @routes.post("/api/auth/change")
    @guarded
    async def change_password(request):
        b = await json_body(request)
        if b.get("new") != b.get("confirm"):
            return reply({"ok": False, "error": "The two new passwords don't match."})
        try:
            return signed_in({"ok": True}, app.auth.change(str(b.get("old", "")), str(b.get("new", ""))))
        except AuthError as exc:
            return reply({"ok": False, "error": str(exc)})

    @routes.post("/api/auth/logout")
    async def logout(request):
        resp = reply({"ok": True})
        resp.del_cookie(COOKIE)
        return resp

    @routes.post("/api/auth/reset")
    async def reset_password(request):
        if not is_local(request):
            return reply({"ok": False, "error": "This only works on the solar screen itself."}, 403)
        app.auth.reset()
        resp = reply({"ok": True})
        resp.del_cookie(COOKIE)
        return resp

    @routes.get("/qr.svg")
    async def qr(request):
        buf = io.BytesIO()  # a standalone SVG (with its namespace), so it works as an <img>
        segno.make(app.setup_url(), error="m").save(buf, kind="svg", scale=8, border=2, dark="#000", light="#fff")
        return web.Response(body=buf.getvalue(), content_type="image/svg+xml")

    @routes.get("/api/health")
    async def health(request):  # the installer checks this after starting a new version
        return reply({"ok": True, "version": __version__, "mode": app.mode})

    @routes.get("/api/now")
    async def now(request):
        return reply({"mode": app.mode, "demo": app.demo if app.mode == "demo" else None,
                      "attention": app.attention(), "setup_address": f"{app._address()}:{app.port}",
                      "units": app.config.units if app.config else "us",
                      "snapshot": dataclasses.asdict(app.snapshot) if app.snapshot else None})

    @routes.get("/api/setup/state")
    async def state(request):
        if allowed(request):
            return reply(app.settings_view())
        return reply({"locked": "enter" if app.auth.has_password else "create", "local": is_local(request)})

    async def run_check(service: str, name: str, check: Awaitable[checks.Result]) -> web.Response:
        if (blocked := app.limiter.blocked(service, name)) is not None:
            check.close()
            return reply(blocked)
        result = await check
        app.limiter.record(service, result)
        return reply(result)

    @routes.post("/api/setup/enphase")
    @guarded
    async def try_enphase(request):
        b = await request.json()
        saved = (app.config.enphase if app.config else None) or cfg.EnphaseConfig()
        where = {"state_dir": app.state_dir, "host": str(b.get("host", "")).strip(),
                 "serial": str(b.get("serial", "")).strip()}
        if enphase_use(b, saved) == "token":
            token = str(b.get("token") or saved.token).strip()
            if not token:
                return reply(checks.Result(False, "Paste the token first", "Follow steps 2 and 3 above to make one."))
            return await run_check("enphase", "Enphase", checks.check_enphase("", "", token=token, **where))
        return await run_check("enphase", "Enphase", checks.check_enphase(
            str(b.get("username") or saved.username).strip(), str(b.get("password") or saved.password), **where))

    @routes.post("/api/setup/enphase/find")
    @guarded
    async def find_enphase_gateway(request):  # step 1 of the walkthrough: no login involved
        saved = (app.config.enphase if app.config else None) or cfg.EnphaseConfig()
        return reply(await checks.find_enphase_gateway(serial=saved.serial))

    @routes.post("/api/setup/franklin")
    @guarded
    async def try_franklin(request):
        b = await request.json()
        saved = app.config.franklin if app.config else None
        return await run_check("franklin", "FranklinWH", checks.check_franklin(
            str(b.get("email") or (saved.email if saved else "")).strip(),
            str(b.get("password") or (saved.password if saved else ""))))

    @routes.post("/api/setup/tempest")
    @guarded
    async def try_tempest(request):
        port = app.config.tempest.port if app.config else 50222
        return reply(await checks.check_tempest(port))

    @routes.post("/api/setup/save")
    @guarded
    async def save(request):
        try:
            new = app.settings_from(await request.json())
        except (ValueError, AttributeError, TypeError) as exc:
            return reply({"ok": False, "error": str(exc)}, 400)
        cfg.save(new, app.config_path)
        app.config_error = None
        await app.use(new)  # stops the old reader first, so it can't write the cache below again
        if new.enphase and new.enphase.uses_token:  # a token cached from an earlier email-and-password login
            (app.state_dir / "enphase-token").unlink(missing_ok=True)
        return reply({"ok": True, "mode": app.mode})

    web_app = web.Application()
    web_app.add_routes(routes)
    return web_app


async def serve(config_path: Path, *, host: str = "0.0.0.0", port: int = 8080, demo: str = "sunny") -> None:
    app = App(config_path, port=port, demo=demo)
    await app.start()
    runner = web.AppRunner(make_web_app(app))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    print(f"solar-kiosk is running. Open http://{lan_address()}:{port}/ on any computer on this network.", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await app.stop()
        await runner.cleanup()
