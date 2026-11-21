# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-21

First release. The installer, the settings page and the device readers are complete. The solar page itself is a
simple placeholder while its final layout is chosen.

### Added

- `install.sh`: a one-line install on Raspberry Pi OS (64-bit):
  - The one-line command fetches `install.sh` from the latest release. The installer installs only published
    releases, checked against `SHA256SUMS`, with hash-locked, ready-built Python packages and a uv download
    checked against a pinned hash.
  - It runs as a dedicated system user in a hardened systemd service (`systemd-analyze security`: 1.1, OK), and
    keeps settings in `/var/lib/solar-kiosk`.
  - Nightly updates (around 3 am) with a health check and automatic rollback.
  - An optional full-screen browser on the Pi's own display.
  - Uninstall; `--purge` removes the settings too.
- Readers for the Enphase IQ Gateway (local, via pyenphase), the FranklinWH aGate (FranklinWH cloud, via
  franklinwh) and the WeatherFlow Tempest (local UDP broadcast).
- Rules for combining the readings (which source to trust for solar, home use and grid), today's solar from the
  gateway's lifetime counter, and login back-off so a wrong password can't lock an account.
- Automatic discovery: the Enphase gateway over mDNS (and again if its address changes), the aGate from the
  FranklinWH account, and the Tempest from its broadcast.
- A demo mode with six scenarios, used until the app is set up.
- `solar-kiosk serve`: the solar page and a settings page with a Connect test for each device and plain-English
  results.
- A settings password, created by the first visitor and stored only as a scrypt hash. It can be reset from the
  Pi's own screen or with `solar-kiosk reset-password`.
- `solar-kiosk watch`: readings printed as text.
- `/api/health`, which reports the running version.
- Security and privacy notes: `docs/security.md`.
- HTML mockups for review: the kiosk screen (`docs/mockups/kiosk-mockups.html`: four layouts, six scenarios,
  light and dark) and six logo ideas (`docs/mockups/logo-mockups.html`).
- GitHub Actions: the tests run on Python 3.11 and 3.13 for every push. Pushing a `vX.Y.Z` tag runs the tests,
  then publishes a release with the wheel, the source archive, the locked requirements, `install.sh` and
  `SHA256SUMS`.

[Unreleased]: https://github.com/preston-peterson/solar-kiosk/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/preston-peterson/solar-kiosk/releases/tag/v0.1.0
