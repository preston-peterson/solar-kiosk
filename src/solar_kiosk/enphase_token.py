"""What an Enphase gateway token says about itself: when it runs out, and which kind of account made it.

The token is a JWT made on Enphase's token site (entrez.enphaseenergy.com). Only the gateway checks its
signature; this module just reads the claims, to warn people before it runs out and to explain mistakes in
plain words. Nothing here relies on the claims for security.

Enphase's tech brief TEB-00060 (June 2025): a token lasts one year when made by the system owner's account,
and 12 hours when made by an installer's account (which includes people who installed their own system).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

RENEW_WARNING = timedelta(days=30)  # how long before it runs out the solar page starts reminding

NOT_A_TOKEN = ("That doesn't look like an Enphase token. On Enphase's token site, use Copy and close, then "
               "paste here without changing anything. It's one long line with two dots in it.")


@dataclass(frozen=True)
class TokenInfo:
    expires: datetime | None  # None: the token doesn't say
    kind: str  # "owner", "installer", or "" when the token doesn't say


def read_token(token: str) -> TokenInfo:
    """Read a token's claims. Raises ValueError (with words for the settings page) if it isn't a token."""
    parts = token.strip().split(".")
    if len(parts) != 3 or not parts[1]:
        raise ValueError(NOT_A_TOKEN)
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError:  # bad base64, bad UTF-8 and bad JSON are all ValueErrors
        raise ValueError(NOT_A_TOKEN) from None
    if not isinstance(claims, dict):
        raise ValueError(NOT_A_TOKEN)
    exp = claims.get("exp")
    expires = datetime.fromtimestamp(exp, timezone.utc) if isinstance(exp, (int, float)) else None
    return TokenInfo(expires, str(claims.get("enphaseUser", "")))


def day(when: datetime) -> str:
    """A date people read easily, in the Pi's own time zone: 'November 21, 2027'."""
    local = when.astimezone()
    return f"{local:%B} {local.day}, {local.year}"


def unusable(info: TokenInfo, now: datetime) -> str | None:
    """Why this token can't be saved, in plain words; None if it's fine."""
    if info.expires is not None and info.expires <= now:
        return f"That token ran out on {day(info.expires)}. Make a new one on Enphase's token site."
    if info.kind == "installer":
        return ("That token was made with an installer's account, so it only lasts 12 hours. Make it again "
                "signed in with the homeowner's account (the one used in the Enphase app). If you installed the "
                "system yourself, Enphase Support can change your account to a homeowner account, or you can use "
                "your Enphase email and password here instead, which renews it automatically.")
    return None


def reminder(info: TokenInfo, now: datetime) -> str | None:
    """A note for the solar page when the token has run out or soon will; None otherwise."""
    if info.expires is None:
        return None
    if info.expires <= now:
        return f"The Enphase token ran out on {day(info.expires)}. Open Settings to make a new one."
    if info.expires - now <= RENEW_WARNING:
        return f"The Enphase token runs out on {day(info.expires)}. Open Settings to make a new one."
    return None
