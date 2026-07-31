"""Collects messages in a list. For tests.

Business-rule tests — who should be told, who cannot be reached — run against
this one. They must not touch a network, and they must not go red because
somebody changed provider: that is the whole claim the split is making, so it
has to be testable without a provider at all.
"""

from .base import DeliveryResult, Message


class LocmemBackend:
    #: Every message any instance has sent. Tests read and clear it.
    outbox: list[Message] = []

    def send(self, messages: list[Message]) -> list[DeliveryResult]:
        type(self).outbox.extend(messages)
        return [DeliveryResult(message=m, accepted=True, detail="locmem") for m in messages]
