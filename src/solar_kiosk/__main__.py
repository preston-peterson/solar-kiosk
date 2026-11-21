"""solar-kiosk command line.

  solar-kiosk serve    the screen and its phone setup page (what the installed service runs)
  solar-kiosk watch    print one line per reading, for checking things from a terminal
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from . import __version__
from . import config as cfg
from . import display
from .runner import build_demo, build_live


async def watch(args: argparse.Namespace) -> int:
    closers: list = []
    units = "us"
    if args.demo:
        poller = build_demo(args.demo, args.speed, args.interval or 15)
    else:
        path = args.config or cfg.default_config_path()
        try:
            c = cfg.load(path)
        except cfg.ConfigError as exc:
            print(f"Settings problem: {exc}", file=sys.stderr)
            return 2
        units = c.units
        try:
            poller, closers = await build_live(c, path, args.interval)
        except OSError as exc:
            print(f"Could not listen for the Tempest on UDP {c.tempest.port}: {exc}", file=sys.stderr)
            return 1

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await poller.run(lambda snap: print(display.line(snap, units), flush=True), stop=stop,
                         max_ticks=1 if args.once else None)
    finally:
        await poller.close()
        for closer in closers:
            closer.close()
    return 0


async def serve(args: argparse.Namespace) -> int:
    from .server import serve as run_server

    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    try:
        await run_server(args.config or cfg.default_config_path(), host=args.host, port=args.port, demo=args.demo)
    except asyncio.CancelledError:
        pass
    except OSError as exc:
        print(f"Could not start the web server on port {args.port}: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    from .sources.demo import SCENARIOS

    p = argparse.ArgumentParser(prog="solar-kiosk", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true", help="log more detail")
    p.add_argument("--version", action="version", version=f"solar-kiosk {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="run the screen and its setup page")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--host", default="0.0.0.0", help="address to listen on (default: all)")
    s.add_argument("--demo", default="sunny", choices=sorted(SCENARIOS), help="demo shown until set up")
    s.add_argument("--config", type=Path, help=f"settings file (default {cfg.default_config_path()})")

    w = sub.add_parser("watch", help="print one line per reading")
    w.add_argument("--demo", nargs="?", const="sunny", choices=sorted(SCENARIOS),
                   help="use simulated data (default scenario: sunny)")
    w.add_argument("--speed", type=float, default=1.0, help="demo only: simulated seconds per real second")
    w.add_argument("--interval", type=float, help="seconds between readings (default 15)")
    w.add_argument("--once", action="store_true", help="read once, print, and exit")
    w.add_argument("--config", type=Path, help=f"settings file (default {cfg.default_config_path()})")

    r = sub.add_parser("reset-password", help="forget the settings password (the next visitor sets a new one)")
    r.add_argument("--config", type=Path, help=f"settings file (default {cfg.default_config_path()})")

    a = sub.add_parser("check-agate", help="read a FranklinWH aGate over Modbus and show what solar-kiosk sees "
                                           "(read-only)")
    a.add_argument("address", help="the aGate's address on the home network, e.g. 192.0.2.20")
    a.add_argument("--port", type=int, default=502)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    quiet_libraries()
    if args.command == "reset-password":
        from .auth import Auth

        Auth((args.config or cfg.default_config_path()).with_name("auth.json")).reset()
        print("The settings password is gone. The next person to open Settings creates a new one.")
        return 0
    if args.command == "check-agate":
        from .agate_check import check

        return asyncio.run(check(args.address, port=args.port))
    return asyncio.run(serve(args) if args.command == "serve" else watch(args))


def quiet_libraries() -> None:
    """Never log the device libraries in detail, even with --verbose: franklinwh logs its login token
    in request headers at DEBUG, and the web server's access log would show every request."""
    for name, level in (("franklinwh", logging.INFO), ("pyenphase", logging.INFO), ("httpx", logging.WARNING),
                        ("httpcore", logging.WARNING), ("aiohttp.access", logging.WARNING)):
        logging.getLogger(name).setLevel(level)


if __name__ == "__main__":
    sys.exit(main())
