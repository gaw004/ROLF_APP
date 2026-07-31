"""Prints instead of sending. The development default.

Enough to walk the whole flow on a laptop with no domain, no provider account
and no credentials — which is the state Phase B is verified in.
"""

from .base import DeliveryResult, Message


class ConsoleBackend:
    def send(self, messages: list[Message]) -> list[DeliveryResult]:
        results = []
        for message in messages:
            print(f"[{message.channel}] -> {message.to}: {message.subject}\n{message.body}\n")
            results.append(DeliveryResult(message=message, accepted=True, detail="printed"))
        return results
