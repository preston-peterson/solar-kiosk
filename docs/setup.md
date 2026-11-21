# Setting up solar-kiosk, step by step

This guide walks through connecting your solar panels, battery and weather station after
[installing](../README.md#install-on-a-raspberry-pi). Set aside about 15 minutes. The settings page shows the same
steps as you go, and tests each device as you connect it.

You'll need:

- a computer, tablet or phone on the same home network (the same Wi-Fi or router) as the solar app
- for **Enphase**: the email and password you use in the Enphase app
- for **FranklinWH**: the email and password you use in the FranklinWH app
- for **Tempest**: nothing; it's found automatically

## 1. Open the settings page

1. On a computer or phone on your home network, open the address the installer printed. It looks like
   `http://raspberrypi.local:8080`. The solar page shows a demo until you finish setting up.
2. Click **Open settings** (on a phone, you can scan the QR code instead).
3. **Create a settings password** of at least 8 characters, and type it twice. It keeps other people on your
   network out of the settings. Each computer or phone asks for it once, then remembers it.

The settings page lists your three devices. Untick **I have this** for any you don't have.

## 2. Solar panels (Enphase)

There are two ways to connect. We recommend the first.

- **Token from Enphase (recommended):** you sign in on Enphase's own website and make a token: a long code that
  lets the solar app read your gateway. Your Enphase password never reaches the solar app. A token lasts a year.
- **Email and password:** simpler, but the solar app keeps your Enphase password so it can renew the token
  itself.

### With a token (recommended)

Choose **Token from Enphase**, then:

1. **Find your gateway.** Click **Find my gateway**. The solar app looks for it on your home network and shows
   its serial number. (It doesn't sign in to anything.)
   - You can also find the serial number in the Enphase app: tap **Menu** (bottom right), then **System**,
     **Devices**, **Gateway**. It's the number after **SN:**.
2. **Make a token on Enphase's website.** This is easiest on a computer.
   1. Open [entrez.enphaseenergy.com](https://entrez.enphaseenergy.com).
   2. Click **Login**, then sign in with the email and password you use in the Enphase app.
   3. From the lists, choose your system's name, then your gateway's serial number (from step 1).
   4. Click **Create access token**.
   5. In the window that opens, click **Copy and close**. The token is now copied.
3. **Paste the token** into the box on the settings page.
4. **Click Connect.** If it works, you'll see "Connected to the Enphase gateway", how much your panels are
   making right now, and the date the token runs out.

**Once a year:** about a month before the token runs out, the solar page shows a reminder. Open the settings
page and repeat steps 2 to 4. It takes two minutes.

### With your email and password

Choose **Email and password**, then:

1. Type the email and password you use in the Enphase app.
2. Click **Connect**. It finds your gateway and checks the login, which takes up to a minute.

This doesn't work if your Enphase account asks for a code from your phone when you sign in. Use a token instead.

### If something goes wrong

| The settings page says | What to do |
|---|---|
| Couldn't find an Enphase gateway | The solar app has to be on the same home network as the gateway. Check the gateway's lights are on. You can still type the serial number from the Enphase app in step 2, and the gateway's address under **More options**. |
| That isn't a whole token | Make the token again and use **Copy and close**, then paste without changing anything. It's one long line with two dots in it. |
| That token was made with an installer's account | If you installed the system yourself, Enphase treats your account as an installer's, and its tokens only last 12 hours. Use **Email and password** instead, or ask Enphase Support to change your account to a homeowner account. |
| The gateway didn't accept that token | Check you chose this gateway's serial number on Enphase's website, then make a new token and paste it again. |
| That token ran out | Make a new token (steps 2 to 4). |
| Enphase didn't accept that email and password | Check them in the Enphase app. After 3 wrong tries, the settings page waits 15 minutes, so your Enphase account doesn't get locked. |

## 3. Battery (FranklinWH)

1. Type the email and password you use in the FranklinWH app.
2. Click **Connect**. It signs in to FranklinWH and shows your battery's charge.

The battery is read through FranklinWH's servers, so the solar app needs the internet. A way to read the aGate
directly over your home network, without a login, is being added.

| The settings page says | What to do |
|---|---|
| FranklinWH didn't accept that email and password | Check them in the FranklinWH app. After 3 wrong tries, the settings page waits 15 minutes. |
| FranklinWH has locked this account | Wait a while, or reset the password in the FranklinWH app, then try again. |
| That FranklinWH account has no aGate | Use the same login the FranklinWH app uses for this house. |
| Couldn't reach FranklinWH | Check the internet connection, then try again. |

## 4. Weather (Tempest)

1. Check the Tempest hub is plugged in and already working in the Tempest app.
2. Click **Look for it**. The solar app listens for the hub for up to 15 seconds. There's nothing to type.

If it says "Didn't hear a Tempest", check the hub is on the same home network as the solar app. If you don't
have a Tempest, untick **I have this**.

## 5. Save

Choose your units (°F and mph, or °C and km/h), then click **Save and start** at the bottom. The solar page
switches from the demo to your own system within a minute.

## Later

- **Changing something:** open the settings page again. Saved passwords and tokens aren't shown; leave a box
  blank to keep what's saved.
- **Forgot the settings password:** on the solar screen itself, open the settings page and click **Forgot it?
  Reset**. Or run `solar-kiosk reset-password` on the Pi. The next person to open the settings page creates a
  new one.
- **What's stored, and how it's protected:** see [security.md](security.md).
