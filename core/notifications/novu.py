"""A thin shell over Novu's HTTP API — the unified notification platform.

⚠️ Not wired up to a real account in this phase, on purpose. There is no domain
   and no sender identity on a laptop, so a live integration could be neither
   sent nor verified; connecting it belongs to Phase C. What exists here is the
   HTTP call and a mocked test, so that the shape is settled and the seam is
   proven to be in the right place.

Credentials come from the environment, like SECRET_KEY. Nothing about this file
knows what a minor is — see base.py.
"""

import json
import urllib.error
import urllib.request

from django.conf import settings

from .base import DeliveryResult, Message

API_URL = "https://api.novu.co/v1/events/trigger"


class NovuBackend:
    def __init__(self, api_key=None, workflow=None):
        self.api_key = api_key or getattr(settings, "NOVU_API_KEY", "")
        self.workflow = workflow or getattr(settings, "NOVU_WORKFLOW", "event-change")

    def send(self, messages: list[Message]) -> list[DeliveryResult]:
        results = []
        for message in messages:
            payload = json.dumps({
                "name": self.workflow,
                # The subscriber is identified by the address alone. Sending an
                # internal id would tie the provider's records to ours, and
                # D22's cost 2 is about keeping the exported surface to an
                # address plus an announcement.
                "to": {"subscriberId": message.to, self._field(message): message.to},
                "payload": {"subject": message.subject, "body": message.body},
            }).encode()
            request = urllib.request.Request(
                API_URL, data=payload,
                headers={
                    "Authorization": f"ApiKey {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    body = json.loads(response.read() or b"{}")
                results.append(DeliveryResult(
                    message=message, accepted=True,
                    provider_ref=str(body.get("data", {}).get("transactionId", "")),
                ))
            except (urllib.error.URLError, TimeoutError, ValueError) as error:
                # Reported, never raised: one address failing must not stop the
                # rest of the batch, and the caller records what happened.
                results.append(DeliveryResult(
                    message=message, accepted=False, detail=str(error)))
        return results

    @staticmethod
    def _field(message):
        return "email" if message.channel == "email" else "phone"
