# Contributing to solar-kiosk

solar-kiosk is a one-person hobby project. The short version of how to work with the project from outside:

- **Bug reports:** welcome, best-effort response.
- **Feature suggestions:** welcome in issues, but the bar for "I'll build this" is "I want it for myself."
- **Pull requests:** not accepted. Only collaborators can open them.
- **Forks:** encouraged. The MIT license invites you to take solar-kiosk somewhere different if it isn't going
  where you want.

The longer version is below.

## Why pull requests are disabled

solar-kiosk is built and maintained by one person, and I keep changes to myself so the codebase stays
consistent: in style, in which features earn their place, and in how carefully it treats people's logins and
home networks. Reviewing outside code well takes real time, and for a project this size I'd rather spend that
time building.

That doesn't mean ideas aren't welcome. **They are. Please file an issue.** I want to know what people run
into, what's missing and what could be better. I read every bug report and suggestion, and the things that fit
where solar-kiosk is going will land in future releases.

The MIT license also keeps the door open the other way: fork freely, change whatever, ship your own version. If
you want solar-kiosk to do something it doesn't, and the answer to "will the maintainer build this?" turns out
to be no, your fork is a real option, not a polite deflection.

## Reporting bugs

If something is broken, please open an issue. The more of this you can include, the faster I can act on it:

- **What you tried to do**: the action, not just the symptom.
- **What happened instead**: ideally with a screenshot if it's on screen. Crop or blur anything personal first.
- **Version**: `solar-kiosk --version` on the Pi.
- **How it's installed**: the one-line install, the ready-made kiosk image, or from source.
- **Which devices are involved**: Enphase IQ Gateway, FranklinWH aGate, WeatherFlow Tempest, or none (demo).
- **Relevant messages**: the error shown on the page, or the useful lines from the app's log.

**Please leave out personal details:** passwords, tokens, email addresses, IP addresses, serial numbers and
locations. Replace them with something like `192.0.2.10` or `<serial>`.

If the bug is security-shaped (a way around the settings password, logins leaking, and so on), please **don't**
open an issue. Use private reporting instead: see [SECURITY.md](SECURITY.md). solar-kiosk assumes a trusted
home network ([docs/security.md](docs/security.md)), but anything that breaks even that assumption is worth
reporting carefully.

## Suggesting features

Feature requests are fine, with a few realities to set expectations:

- **The maintenance bar is high.** I'm conservative about adding surface area, especially for equipment or
  uses I won't personally run. "I want it for myself" is a real filter.
- **The roadmap is private.** I don't commit to delivery dates. If a request lands in my own backlog, you may
  see it in a future release. If it doesn't, it doesn't.
- **A "no" isn't a judgment of the idea.** It usually means the idea is good but not where I want this
  codebase to go. That's exactly the case where forking makes sense.

## If you're forking

Go ahead. The MIT license has you covered. A few practical pointers:

- **Setup:** `uv sync`, then `uv run pytest`. The tests use fakes for every device, so no hardware is needed.
- **Demo mode:** `uv run solar-kiosk serve` shows simulated data until it's set up.
  `uv run solar-kiosk watch --demo <scenario>` prints readings. The scenarios live in
  `src/solar_kiosk/sources/demo.py`.
- **Combining the readings:** the rules for which source to trust (`src/solar_kiosk/combine.py`) follow
  [FrankEnPoller](https://github.com/GasBandit/FrankEnPoller); credit them if you build on those rules.
- **Security notes:** if you change how logins, the settings password or the network side work, update
  [docs/security.md](docs/security.md) to match.
- **Fixtures and examples:** use invented values (`192.0.2.x` addresses, `you@example.com`, made-up serials),
  never real ones.

Good luck, and have fun with it.
