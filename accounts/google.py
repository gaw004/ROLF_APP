"""Reading the name and address out of a Google sign-in, to fill the form in.

⚠️ **This is prefill, and it is not a way to log in.** Google tells us an address
   and a name; the person still chooses a password and the account that gets made
   is an ordinary password account. Nothing about holding a Google account grants
   anything here, and there is no branch anywhere that trusts this token for
   anything except three text boxes.

   That is what makes the security story short: the worst a forged token could do
   is put somebody else's name in a box that its holder is about to overwrite.
   Verifying it anyway (below) costs one call and means the boxes are not a place
   to inject arbitrary strings.

⚠️ It does not check `email_verified`, deliberately. Google returns it, and acting
   on it would mean silently refusing to fill the box for some accounts — while
   anyone can type any address into that box by hand, and this project does not
   confirm addresses at registration yet either (phase-c.md's known gaps). A
   check that changes nothing about what is possible, in exchange for a blank box
   nobody can explain, is not a check worth having.
"""

import urllib.request

from django.conf import settings

#: How long to wait for Google's signing certificates. Short on purpose: this sits
#: on the registration page, and a person staring at a spinner will press the
#: button again.
CERTS_TIMEOUT_SECONDS = 5


class _Response:
    """The two attributes google-auth reads off a fetch: `status` and `data`."""

    def __init__(self, status, data):
        self.status = status
        self.data = data


def _urllib_transport(url, method="GET", body=None, headers=None, timeout=None, **kwargs):
    """A google-auth transport built on the standard library.

    ⚠️ Written rather than installed, and the reason is dependency weight, not
       taste. google-auth's own transport is `google.auth.transport.requests`,
       which needs `requests` — and that pulls in urllib3, certifi, idna and
       charset-normalizer. This project already talks HTTP to a provider
       (core/notifications/novu.py) using `urllib.request` for exactly the same
       reason, so this follows the pattern that is already here rather than
       starting a second one.

    ⚠️ **The certificate check is not hand-rolled.** `urlopen` verifies TLS
       against the system trust store by default; the JWT signature check stays
       entirely inside google-auth. All this function does is fetch bytes.

    The signature is google-auth's transport protocol, which is why it takes
    arguments this call site never passes.
    """
    request = urllib.request.Request(
        url, data=body, headers=headers or {}, method=method)
    with urllib.request.urlopen(
            request, timeout=timeout or CERTS_TIMEOUT_SECONDS) as response:
        return _Response(response.status, response.read())


def is_configured():
    """Is there a client id at all? If not, the button must not be drawn.

    ⚠️ Not drawn rather than drawn-and-broken. A "Continue with Google" button on
       a deployment with no client id is a control that fails on click, which is
       the same lesson C0.5 paid for with three dead links.
    """
    return bool(getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", ""))


def verify_id_token(credential):
    """Google's own verification of the credential. Patched wholesale in tests.

    Kept as one thin function with no logic of its own so that tests can replace
    it: the real thing fetches Google's signing certificates over the network,
    and a unit test that needs the internet is a test that goes red on a train.

    ⚠️ `audience` is passed, and that is the check that matters. Without it any
       valid Google token — including one issued to somebody else's application —
       would verify here.
    """
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(
        credential, _urllib_transport, settings.GOOGLE_OAUTH_CLIENT_ID)


def identity_from(credential):
    """`{"email", "legal_first_name", "legal_last_name"}` from a credential, or None.

    Returns None for anything that does not verify, rather than raising: the
    caller's job is to render the registration page either way, and a bad token
    means "we could not fill this in for you", not an error page. A person who
    hit a stale or forged token can still type the three boxes.

    ⚠️ The keys are RegistrationForm's field names on purpose. The alternative —
       Google's own `given_name` / `family_name` — would need a translation table
       somewhere, and that table is exactly the sort of thing that gets updated
       on one side only.
    """
    if not is_configured() or not credential:
        return None
    try:
        claims = verify_id_token(credential)
    except Exception:
        # ⚠️ Deliberately broad. google-auth raises ValueError for a bad token,
        #    but the transport underneath can raise anything from a socket error
        #    to a JSON error, and every one of them means the same thing here:
        #    carry on with an empty form. A 500 on the registration page because
        #    Google was slow would be a worse outcome than an unfilled box.
        return None
    return {
        "email": (claims.get("email") or "").strip().lower(),
        "legal_first_name": claims.get("given_name") or "",
        "legal_last_name": claims.get("family_name") or "",
    }
