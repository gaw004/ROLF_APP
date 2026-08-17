"""Django's own email backend. The fallback that depends on nothing external.

⚠️ Email only. A message asked for over SMS is reported as not accepted rather
   than quietly dropped — with this backend configured, an SMS-only recipient
   is a real gap, and D22 requires that gaps be visible rather than silent.

⚠️ **Nothing in here raises.** One address failing must not cost the caller the
   answers for the other ninety-nine — it records them, and a batch that dies
   halfway leaves it with no way to tell who got a message and who did not.
   That was already how NovuBackend behaved; this one used to be the exception,
   with fail_silently=False on every send. Reporting a failure as
   accepted=False is not "failing silently": the caller writes it down (see
   events/services.py::notify_event_change) and the notification record shows
   it. Swallowing means nobody is told; this means the *right* thing is told.

⚠️ One SMTP connection for the whole batch, not one per message. A hundred
   signups used to mean a hundred connect / authenticate / quit cycles, which
   providers rate-limit in their own right — the failure would arrive as
   refused connections partway down a list that had been fine a moment earlier.
"""

from django.conf import settings
from django.core.mail import EmailMessage, get_connection

from .base import EMAIL, DeliveryResult, Message


class DjangoEmailBackend:
    def send(self, messages: list[Message]) -> list[DeliveryResult]:
        connection = get_connection(fail_silently=False)
        # OSError covers what SMTP actually goes wrong with: smtplib.SMTPException
        # is a subclass of it, and so are the socket and TLS errors underneath.
        try:
            connection.open()
            opening_failed = ""
        except OSError as error:
            # Nothing was sent, so every message in the batch is a failure with
            # the same cause. Reported rather than raised, for the reason above.
            opening_failed = str(error)

        try:
            return [self._one(connection, message, opening_failed)
                    for message in messages]
        finally:
            connection.close()

    def _one(self, connection, message: Message, opening_failed: str) -> DeliveryResult:
        if message.channel != EMAIL:
            return DeliveryResult(
                message=message, accepted=False,
                detail="This backend can only send email.",
            )
        if opening_failed:
            return DeliveryResult(
                message=message, accepted=False, detail=opening_failed)

        mail = EmailMessage(
            subject=message.subject,
            body=message.body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=[message.to],
            connection=connection,
        )
        try:
            sent = mail.send(fail_silently=False)
        except OSError as error:
            # ⚠️ Where a daily quota lands. The provider answers one message
            #    with a refusal, and the ones after it usually get the same —
            #    which is exactly why this returns instead of raising: the
            #    caller ends up with a list of who did and did not make it,
            #    rather than an exception where a record should have been.
            return DeliveryResult(message=message, accepted=False, detail=str(error))
        return DeliveryResult(
            message=message, accepted=bool(sent),
            detail="" if sent else "The mail server did not accept it.",
        )
