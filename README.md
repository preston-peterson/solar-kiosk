# solar-kiosk

A full-screen solar and battery display for a Raspberry Pi and an HDMI screen. It reads an
**Enphase IQ Gateway** (local network), a **FranklinWH aGate** (FranklinWH cloud) and a
**WeatherFlow Tempest** weather station (local UDP broadcast), and combines them into one picture of
what the house is doing.

Inspired by [FrankEnPoller](https://github.com/GasBandit/FrankEnPoller), a Windows tray app. A few of its rules
for combining the two systems are re-implemented here; no code is shared.

**Who it's for:** households with Enphase solar and a FranklinWH battery (a Tempest weather station is
optional) who want one clear, always-on picture of their energy on a computer or a small wall screen,
without being technical.

**What it isn't:** a control app (it never changes battery or inverter settings), a cloud service, or
something to expose to the internet. It runs on a Raspberry Pi on your home network.

**Status:** early. It runs, finds the devices, and has a phone setup page. The final screen design is
still being picked from the mockups: [`docs/mockups/kiosk-mockups.html`](docs/mockups/kiosk-mockups.html).

## Install on a Raspberry Pi

On a Raspberry Pi 4 or 5 running **Raspberry Pi OS (64-bit)**, Lite or Desktop, open a terminal and run:

```sh
curl -fsSL https://github.com/preston-peterson/solar-kiosk/releases/latest/download/install.sh | bash
```

It installs the latest release and starts it in the background, then prints the address to open from any
computer or phone on your network.

- **Full screen:** if the Pi has a desktop and a screen, it also opens the solar page full screen at login.
- **Updates:** it updates itself every night around 3 am, and goes back to the previous version if an update
  doesn't start properly.
- **Options:** `... | bash -s -- --help`, for example `--no-kiosk`, `--no-auto-update` or `--port 8081`.
- **Remove:** `sudo /opt/solar-kiosk/install.sh --uninstall`. This keeps the settings; add `--purge` to delete
  them too.
- **Forgot the settings password?** Run `solar-kiosk reset-password` on the Pi.

## Try it without any hardware

```sh
uv sync
uv run solar-kiosk serve           # then open http://localhost:8080 (a demo until set up)
uv run solar-kiosk watch --demo outage --speed 60 --interval 1   # readings as text, sped up
```

Demo scenarios: `sunny`, `evening`, `outage`, `offline` (aGate unreachable), `cloudy`, `check` (the array
making less than the sunlight allows).

## Setting it up

`solar-kiosk serve` runs the solar page on port 8080. Open it from any computer or phone on the home
network. Until it's set up, it shows a demo and an **Open settings** button (and a QR code for phones).

**Step-by-step guide: [docs/setup.md](docs/setup.md).** In short:

1. The first person to open Settings creates a **settings password**. Each computer or phone asks for it once.
2. **Enphase:** make a token on Enphase's website and paste it in, so the app never needs your Enphase
   password. (Your Enphase email and password also work.) The gateway is found automatically.
3. **FranklinWH:** the email and password from the FranklinWH app.
4. **Tempest:** found automatically.
5. Click **Save and start**.

Forgot the settings password? Reset it from the Pi's own screen, or run `solar-kiosk reset-password` on the Pi.

**Security:** solar-kiosk is made for a home network: anyone on it can view the page, and only the settings
password opens Settings. It uses plain HTTP, so **never open its port to the internet**. What's stored, the
risks, and why we accept them for a home app: [docs/security.md](docs/security.md).

The Pi must be on the same home network as the Enphase gateway and the Tempest hub.

## Tests

```sh
uv run pytest
```

## Libraries

- [pyenphase](https://github.com/pyenphase/pyenphase) (MIT): the Enphase gateway, as used by Home
  Assistant.
- [franklinwh](https://github.com/richo/franklinwh-python) (MIT): the FranklinWH cloud API, which is
  unofficial and may change without notice.

## License

MIT. See [LICENSE](LICENSE).
