# Security and privacy

solar-kiosk is built for one place: **your home network**. Like a printer or a smart TV, it trusts the
people and devices on that network to look at it, and it keeps its **settings** behind a password. This page
explains what it stores, how the logins are protected, the risks that remain, and why we think they're
reasonable for a home app. It also covers how to reduce them further if you want to.

## The short version

- Anyone on your home network can **view** the solar page. Only someone with the **settings password** can
  change settings or logins.
- Your Enphase and FranklinWH logins are stored **on the Pi only**, in a file that only the app can read.
  They're sent only to Enphase and FranklinWH, over encrypted connections.
- The settings password itself is **never stored**, only a scrambled form of it (a hash).
- The page uses plain `http://`, not `https://`, so things typed into it are **not encrypted** on your home
  network. That's normal for home devices, and it's the main reason this app must **never be opened to the
  internet**.
- The app only **reads** your equipment. It never changes battery, inverter or grid settings.

## What it stores, and where

| File (on the Pi) | What's in it | Who can read it |
|---|---|---|
| `~/.config/solar-kiosk/secrets.toml` | Enphase email and password (or token); FranklinWH email and password | Only the app's user (file mode 600) |
| `~/.config/solar-kiosk/auth.json` | The settings password **as a scrypt hash** (never the password), and the current sign-in key | Only the app's user (600) |
| `~/.local/state/solar-kiosk/enphase-token` | The access token the Enphase gateway issued, cached so the app doesn't sign in to Enphase on every start | Only the app's user (600) |
| `~/.config/solar-kiosk/config.toml` | Gateway address, serial numbers, units: no passwords | Anyone with an account on the Pi |

The app writes these files itself; nobody has to edit them. It refuses to start if the logins file becomes
readable by other users. Files are replaced whole, so a power cut can't leave one half-written.

## Who can do what

| Who | Can |
|---|---|
| Anyone on your home network | View the solar page: live power, today's totals, weather. See the settings page's password prompt. |
| Someone with the settings password | Change the logins and other settings. They still can't see saved passwords: the page only says one is saved. |
| Someone at the Pi's own screen | Reset the settings password (see below). |
| Someone with the Pi's SD card, or a login on the Pi | Everything, including reading the Enphase and FranklinWH logins. |

## How sign-in works

**The settings password**

- The first person to open Settings after installing creates it. It needs at least 8 characters.
- It's stored as a scrypt hash with a random salt, never as the password itself.
- Each browser that enters it gets a cookie holding a random sign-in key, so it doesn't ask again. The cookie
  can't be read by page scripts (`HttpOnly`) and isn't sent with requests from other websites
  (`SameSite=Strict`).
- Changing the password creates a new sign-in key, which **signs out every other computer and phone**.
- After 5 wrong passwords, it waits 15 minutes before accepting more tries.
- **Forgot it?** Reset it from the Pi's own screen (the reset button appears only there), or run
  `solar-kiosk reset-password` on the Pi. The next person to open Settings then creates a new one.

**Your Enphase login**

- On current gateways (firmware 7 or later), the app uses your Enphase login once, over HTTPS, to get an
  access token from Enphase. After that it talks only to the gateway on your network and reuses the token
  until it expires.
- Accounts that use two-step sign-in can paste a token instead of a password.

**Your FranklinWH login**

- The battery is read through FranklinWH's cloud: the same service, and the same login, as the FranklinWH
  app. It's sent over HTTPS.
- FranklinWH's service sends a scrambled form of the password (an MD5 hash). That hash works as well as the
  password itself, so it gets the same care.
- This service is **unofficial and undocumented**, so FranklinWH could change it without notice.

**Protecting your accounts from lock-outs**

- The settings page's Connect buttons allow 3 failed logins per service in 15 minutes.
- While running, the app waits 5 minutes (Enphase) or 10 minutes (FranklinWH) after a failed login before
  trying again.
- Both limits stop a wrong password from getting your account locked.

## Known risks, and why we accept them

These are deliberate trade-offs for a device that sits in a home and has to keep running with nobody
looking after it.

**1. The page uses plain HTTP on your home network.**

- **Risk:** the settings password, the device logins (when typed into Settings) and the sign-in cookie cross
  your home network unencrypted. Someone already on your network, with the right tools, could capture them.
- **Why it's acceptable:** the traffic never leaves your home. Your Wi-Fi encrypts it over the air, and your
  router keeps the internet out. Proper HTTPS on a home device needs a certificate that browsers would warn
  about, and asking non-technical people to click past security warnings teaches a worse habit.
- **To reduce it:**
  - type the logins on the kiosk's own touchscreen, where they never cross the network
  - keep visitors on a guest Wi-Fi
  - change the settings password if you think someone untrusted has been on your network

**2. The device logins are stored unencrypted on the Pi.**

- **Risk:** anyone who gets the SD card, or a login on the Pi, can read your Enphase and FranklinWH logins.
- **Why it's acceptable:** the app has to start by itself after a power cut. Encrypting the logins would mean
  keeping the key on the same card, which protects nothing. File permissions keep them away from other users
  on the Pi. This is how most home automation software stores logins.
- **To reduce it:**
  - keep the Pi inside your home
  - use a password for Enphase and FranklinWH that you don't use anywhere else
  - if the Pi or its card is lost or stolen, change both passwords

**3. Anyone on your network can view the solar page.**

- **Risk:** energy use hints at when people are home or away, and the page shows it to everyone on your
  network.
- **Why it's acceptable:** viewing is the point: family members' computers and the kiosk screen shouldn't
  need a password just to look. Everyone on your network is someone you let on.
- **To reduce it:** keep guests on a guest network that can't reach your other devices.

**4. The first visitor creates the settings password.**

- **Risk:** between installing and setting up, someone else on your network could open Settings first and
  create the password.
- **Why it's acceptable:** that window is short and inside your home. If it happens, you'll notice: Settings
  asks for a password you don't know.
- **To reduce it:** open Settings right after installing. If someone else got there first, reset the
  password from the Pi's own screen.

**5. Anyone at the Pi's own screen can reset the settings password.**

- **Why it's by design:** standing at the device is proof of being in the home, just like the reset button on
  a router. It's how someone who forgot the password gets back in without a technician.

**6. A browser stays signed in for a long time.**

- **Risk:** a lost or stolen laptop or phone stays signed in to Settings.
- **To reduce it:** change the settings password. That signs out every other browser.

**7. The connection to the Enphase gateway is encrypted, but the gateway's identity isn't checked.**

- **Risk:** the gateway uses a certificate it signed itself, which can't be verified. In principle, a device
  on your network pretending to be the gateway could receive the access token.
- **Why it's acceptable:** that would need someone already inside your home network, actively attacking it.
  The token only reads data from your own gateway.

**8. The battery readings depend on FranklinWH's cloud.**

- **Risk:** if FranklinWH's service is down or changes, the battery readings stop. The solar, home and grid
  readings from the Enphase gateway keep working.

## Never put it on the internet

solar-kiosk is not built to face the internet: no HTTPS, no protection against password guessing at internet
scale. **Don't forward its port (8080) on your router.** To see it away from home, use a VPN that puts your
phone back on your home network (for example WireGuard or Tailscale).

## What it never does

- **Never changes your equipment.** It only reads; it never changes battery mode, reserve, circuits or grid
  settings.
- **Never sends your data anywhere** except to Enphase and FranklinWH, to read your own systems, and to
  GitHub, to check for updates. There's no tracking or telemetry.
- **Never writes passwords or tokens to its logs,** even with verbose logging on: the device libraries' detailed
  logging is always switched off.
- **Never shows saved passwords** in the settings page or sends them back to a browser.

## The managed kiosk (planned)

The ready-made Pi image will also:

- ship with no default password
- keep remote login (SSH) off unless you turn it on
- install operating-system security updates automatically
- update solar-kiosk every night, rolling back by itself if an update doesn't start properly

## Reporting a security problem

Please use GitHub's private vulnerability reporting on this repository (the **Security** tab, then
**Report a vulnerability**), rather than a public issue.
