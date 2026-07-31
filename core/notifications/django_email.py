"""Django's own email backend. The fallback that depends on nothing external.

⚠️ Email only. A message asked for over SMS is reported as not accepted rather
   than quietly dropped — with this backend configured, an SMS-only recipient
   is a real gap, and D22 requires that gaps be visible rather than silent.
"""

from django.conf import settings
from django.core.mail import send_mail

from .base import EMAIL, DeliveryResult, Message


class DjangoEmailBackend:
    def send(self, messages: list[Message]) -> list[DeliveryResult]:
        results = []
        for message in messages:
            if message.channel != EMAIL:
                results.append(DeliveryResult(
                    message=message, accepted=False,
                    detail="This backend can only send email.",
                ))
                continue
            sent = send_mail(
                subject=message.subject,
                message=message.body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[message.to],
                fail_silently=False,
            )
            results.append(DeliveryResult(message=message, accepted=bool(sent)))
        return results
