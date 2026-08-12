"""The short-lived proof that somebody was standing in front of the screen.

A pure module: no database, no request, no template. Everything here is a
function of its arguments plus SECRET_KEY, which is what lets the tests pin
"exactly 90 seconds" and "exactly 91 seconds" without mocking a clock. See D28.

⭐ Read D28's first section before changing anything here. This token is **not**
   an anti-fraud mechanism and no amount of cryptography can make it one: the
   server cannot tell "this phone is at the venue" from "this phone was sent the
   link by somebody at the venue". What rotation buys is that the code cannot be
   photographed in the morning and used that evening — the attacker now needs a
   live accomplice on site. Every trade-off below follows from that, and a
   change made in the belief that this proves attendance will make the wrong
   one.

⚠️ The clock is injected everywhere (`at=None`), and that is the whole reason
   this is not `django.core.signing.TimestampSigner`. That class does exactly
   the right thing semantically, but `sign()` reaches for `time.time()` itself,
   so its boundary cases can only be tested through a mock — and every other
   time-dependent write path in this project takes `at=` (check_in, upcoming,
   in_period). D16's spelling of "now" has one home; opening a mocking precedent
   for a new module costs more than assembling a payload does.

   ⚠️ Nothing here invents a cryptographic construction. The HMAC is Django's
      salted_hmac, the comparison is Django's constant_time_compare. What is
      written by hand is the canonical string the three fields are packed into.
"""

import base64
import datetime

from django.core.exceptions import ValidationError
from django.utils.crypto import constant_time_compare, salted_hmac

from core.timeutils import local_now

#: Namespace for the HMAC key. ⚠️ Never sign with a bare SECRET_KEY: the salt is
#  what keeps these tokens in a key domain of their own, separate from sessions,
#  password-reset links and signing.dumps(). Without it a weakness in the
#  construction here would be a weakness in all of them.
KEY_SALT = "events.checkin"

#: How long a token stays valid, in seconds. Flat, not a bucket — see the
#  module's counterpart note in D28「为什么不是时间分桶的 TOTP」. Paired with the
#  20-second refresh on the display, so whoever scans the code on screen has at
#  least 70 seconds left. ⚠️ Changing either number changes that difference,
#  which is the figure a volunteer actually experiences.
MAX_AGE_SECONDS = 90

#: When the display may hand out tokens at all, relative to the event.
#
# ⚠️ This exists for a tab left open on an office screen. Without it that page
#    is a permanently valid clock-in machine that looks entirely normal — the
#    failure shape this project keeps convicting: nothing raises, and it is
#    never right.
WINDOW_BEFORE = datetime.timedelta(hours=2)
WINDOW_AFTER = datetime.timedelta(hours=4)

#: Which way round the scan counts. The mode travels inside the signature, so a
#  volunteer cannot turn a check-in code into a check-out one by editing a URL.
CHECK_IN = "in"
CHECK_OUT = "out"
MODES = frozenset({CHECK_IN, CHECK_OUT})

#: Length of the signature kept in the token. 20 hex characters is 80 bits —
#  far past guessing inside a 90-second window, and short enough to keep the URL
#  sparse, which is what makes the QR readable across a hall.
SIGNATURE_CHARS = 20

_SEPARATOR = "."


class InvalidCheckInToken(ValidationError):
    """The token does not verify, or it verified and is too old.

    One class for both, deliberately: the page says the same thing either way
    ("scan the code on the screen again"), and telling a caller which of the two
    it was is information they have no use for and an attacker does.
    """


def _payload(event_id, mode, issued_at_epoch):
    """The canonical string that gets signed. One spelling, used by both sides."""
    return f"{event_id}:{mode}:{issued_at_epoch}"


def _signature(payload):
    return salted_hmac(KEY_SALT, payload).hexdigest()[:SIGNATURE_CHARS]


def issue(event_id, mode, *, at=None):
    """Mint a token for this event and direction, valid MAX_AGE_SECONDS from now.

    ⚠️ The instant of issue travels **inside** the payload rather than being
       derived from a time bucket. A bucket makes the remaining life depend on
       where in the bucket the scan landed — one volunteer gets 89 seconds and
       the next gets a tenth of a second — which is precisely the symptom the
       original design added a tolerance to paper over. Carrying the instant
       makes every token live exactly as long as every other one.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown check-in mode: {mode!r}")
    issued_at = int((at or local_now()).timestamp())
    payload = _payload(event_id, mode, issued_at)
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{encoded}{_SEPARATOR}{_signature(payload)}"


def issue_with_expiry(event_id, mode, *, at=None):
    """(token, expires_at_epoch) — what the display endpoint hands the browser.

    ⚠️ An **absolute** expiry, not a "valid for N seconds" countdown. The iPad's
       timers are throttled the moment the screen sleeps or the tab goes to the
       background, so a page counting down from its own clock wakes up believing
       a long-dead code is fresh — and a dead QR looks exactly like a live one.
       Given the instant it dies, the page can always tell, however long it was
       asleep. See D28「setInterval 单独用，当天一定翻车」.
    """
    now = at or local_now()
    # Built from the same truncated second verify() will measure against, so the
    # bar on screen empties at the moment the code actually stops working rather
    # than up to a second after it.
    return issue(event_id, mode, at=now), int(now.timestamp()) + MAX_AGE_SECONDS


def verify(token, *, at=None):
    """Return (event_id, mode) for a token that is genuine and still fresh.

    Raises InvalidCheckInToken otherwise.

    ⚠️ The signature is checked **before** the age. Reading a claimed timestamp
       out of an unverified payload and acting on it is how a forged token gets
       to choose its own expiry.
    """
    if not isinstance(token, str) or _SEPARATOR not in token:
        raise InvalidCheckInToken(_EXPIRED_MESSAGE)
    encoded, _, signature = token.rpartition(_SEPARATOR)
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = base64.urlsafe_b64decode(encoded + padding).decode()
    except (ValueError, UnicodeDecodeError) as error:
        raise InvalidCheckInToken(_EXPIRED_MESSAGE) from error

    if not constant_time_compare(signature, _signature(payload)):
        raise InvalidCheckInToken(_EXPIRED_MESSAGE)

    # Only now is the payload something we wrote, so only now may it be parsed.
    event_id, _, rest = payload.partition(":")
    mode, _, issued_at = rest.partition(":")
    try:
        event_id, issued_at = int(event_id), int(issued_at)
    except ValueError as error:  # pragma: no cover - unreachable while we sign it
        raise InvalidCheckInToken(_EXPIRED_MESSAGE) from error

    # ⚠️ Both sides truncated to whole seconds, because the payload carries a
    #    whole second. Comparing a float "now" against a floored "issued" makes
    #    the real lifetime anything from 89.0 to 90.0 depending on the fraction
    #    of a second the code happened to be minted in — a smaller version of
    #    exactly the wobble that got time buckets rejected. One resolution, used
    #    on both ends, and the lifetime is 90 for everybody.
    age = int((at or local_now()).timestamp()) - issued_at
    # ⚠️ Both ends. A token stamped in the future is not a clock-skew case to be
    #    tolerated — this process signed it, so a future stamp means the payload
    #    is not what we think it is.
    if age < 0 or age > MAX_AGE_SECONDS:
        raise InvalidCheckInToken(_EXPIRED_MESSAGE)
    return event_id, mode


_EXPIRED_MESSAGE = (
    "That check-in code has expired. Scan the code on the screen again."
)


def window_is_open(event, *, at=None):
    """May this event hand out check-in codes at this moment?

    Two hours before the start until four hours after the end, and never for a
    draft or a cancelled event — an event nobody has published, or one that is
    off, must not have a route that manufactures attendance records for it.
    """
    if event.status in {event.Status.DRAFT, event.Status.CANCELLED}:
        return False
    now = at or local_now()
    return event.start_time - WINDOW_BEFORE <= now <= event.end_time + WINDOW_AFTER


def window_message(event, *, at=None):
    """What to tell an admin whose screen is outside the window, or None.

    Here rather than in the template because it is a statement about the same
    two constants as window_is_open(): a template saying "opens 2 hours before"
    is a second copy of a rule, free to drift from the one that is enforced.
    """
    if window_is_open(event, at=at):
        return None
    if event.status == event.Status.DRAFT:
        return "Publish this event before opening check-in."
    if event.status == event.Status.CANCELLED:
        return "This event is cancelled, so check-in is closed."
    if (at or local_now()) < event.start_time - WINDOW_BEFORE:
        return "Check-in opens two hours before the event starts."
    return "Check-in for this event has closed."
