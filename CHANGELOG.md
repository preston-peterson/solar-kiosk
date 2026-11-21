# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- HTML mockups of the kiosk screen (`docs/mockups/kiosk-mockups.html`): four layouts, six scenarios, light and dark.
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
- Security and privacy notes: `docs/security.md`.
- GitHub Actions: the tests run on Python 3.11 and 3.13 for every push. Pushing a `vX.Y.Z` tag runs the tests,
  then publishes a release with the wheel, the source archive and `SHA256SUMS`.
