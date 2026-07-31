"""The delivery contract: an address, a channel, and some words.

⚠️ A backend knows those three things and nothing else — none of our tables,
   and above all nothing about how old anybody is. The moment one of them
   learns that under-18s are told through a parent instead, changing provider
   means rewriting that rule — and it is a rule about this foundation, which no
   notification platform has ever heard of. core/tests.py greps this package
   for the names it must not contain, so do not spell them out even in a
   comment: the guard scans itself and everything around it.

Deciding who should be told, and at what address, is business logic and lives
in events/services.py::resolve_recipients(). That half is a permanent asset;
this half is replaceable, which is the entire point of splitting them.
See goal.md D22.
"""

from dataclasses import dataclass
from typing import Protocol, Sequence

from django.conf import settings
from django.utils.module_loading import import_string

EMAIL = "email"
SMS = "sms"


@dataclass(frozen=True)
class Message:
    """One thing to send to one address."""

    to: str          # an email address, a phone number, or a provider's id
    channel: str     # EMAIL or SMS
    subject: str
    body: str


@dataclass(frozen=True)
class DeliveryResult:
    """What the backend made of one message.

    ⚠️ "Accepted" is not "delivered". A provider can answer the first and only
       guesses at the second; neither answers "this person has no address at
       all", which is ours to work out and is why unreachable is computed on
       our side rather than read off a receipt (D22 ②).
    """

    message: Message
    accepted: bool
    detail: str = ""
    provider_ref: str = ""


class NotificationBackend(Protocol):
    def send(self, messages: Sequence[Message]) -> list[DeliveryResult]: ...


def get_backend() -> NotificationBackend:
    """The configured backend. settings.NOTIFICATION_BACKEND names the class."""
    return import_string(settings.NOTIFICATION_BACKEND)()
