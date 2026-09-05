"""One form. Plain django.forms, no admin import (D18).

⭐ `AudienceFormMixin` here is the one out of `org.forms`, and that is the first
   real payoff of splitting it on 2026-08-31. This form gets the "Everyone"
   convenience tick, the two rules an audience obeys on its own, and the
   re-seating in `order_fields()` — and it does **not** get "a role may not be
   wider than its event", which is not a sentence about anything on this table.
   Before the split those were one class, and a notice could only have inherited
   the half that applies by inheriting the half that does not.
"""

import datetime

from django import forms

from core.timeutils import local_now
from org.audience import Audience
from org.forms import AudienceFormMixin
from org.models import Ministry
from org.permissions import in_foundation_tier, ministry_ids_administered_by

from .models import Notice

#: How long a notice stays up unless somebody says otherwise.
#:
#: ⚠️ A **prefill, not a policy**. Decision 4 is that the admin sets the date;
#:    what this buys is that they set it by editing a sensible number rather
#:    than by inventing one on an empty box. Every product with this feature
#:    bounds it somehow — Viva caps expiry at two weeks outright — and the
#:    reason is the same one written on Notice.stops_showing: a board that never
#:    clears itself stops being read.
DEFAULT_RUN_DAYS = 30


class NoticeForm(AudienceFormMixin, forms.ModelForm):
    """Write a notice, say who needs to know, and say when it comes down."""

    class Meta:
        model = Notice
        fields = [
            "title", "body", "ministry",
            # Who needs to know. Before the dates, because it is a publishing
            # decision rather than a detail — the same order EventForm uses.
            *Audience.AUDIENCE_FIELDS,
            "starts_showing", "stops_showing",
            "status",
        ]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 4}),
            "starts_showing": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "stops_showing": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "visible_to_ministries": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, user, **kwargs):
        # `user` explicit rather than dug out of a request — what lets the tests
        # build this directly, the same convention every form in this project
        # follows.
        super().__init__(*args, **kwargs)
        administered = ministry_ids_administered_by(user)
        # 🔴 **The dropdown is the other half of `can_publish_notice`**
        #    (2026-09-03). That function now lets the foundation tier publish in
        #    any ministry's name, and D41 promised the change would be "一个函数"
        #    — it was not, and this is the half that was missed. A foundation
        #    admin holding no MinistryRole has an empty `administered`, so the
        #    old line handed them a dropdown with nothing in it: permission
        #    granted, and no ministry to exercise it on. The form does not fail,
        #    it simply refuses every submission with "this field is required" —
        #    which reads as a broken page, not as a missing permission.
        #
        # ⚠️ `is_active=True` rather than everything, matching what
        #    ministry_ids_administered_by() already filters for the other branch:
        #    a retired ministry is not somewhere new notices go up.
        self.fields["ministry"].queryset = (
            Ministry.objects.filter(is_active=True).order_by("name")
            if in_foundation_tier(user)
            else Ministry.objects.filter(id__in=administered))
        # ⚠️ The same set the dropdown above is built from, not a second lookup.
        #    Two answers to "which ministries are theirs" drift apart on exactly
        #    the account where it matters.
        self.fields["visible_to_ministries"].queryset = Ministry.objects.filter(
            is_active=True).order_by("name")

        if self.instance.pk is not None:
            return
        # Decision 14, and it goes the same way as an event's: the narrowest
        # useful start. ⚠️ Only when adding — re-ticking their own ministry on
        # an edit would quietly widen a notice somebody had deliberately
        # narrowed, and nothing about that is visible.
        #
        # ⚠️ A foundation admin who runs no ministry gets **nothing** pre-ticked,
        #    and that is right rather than a gap left by the line above: the
        #    prefill's whole claim is "your own ministry is the narrowest useful
        #    start", and they have no own ministry for it to name. Ticking one
        #    for them would be the form picking an audience nobody asked for.
        self.initial.setdefault("visible_to_ministries", list(administered))
        # Decision 4's landing point. ⚠️ `setdefault`, so a bound form redisplayed
        # after an error keeps what the person typed rather than snapping back.
        #
        # ⚠️ Seconds and microseconds cut off, and this is a real one rather than
        #    tidiness: `datetime-local` renders whatever it is given, so the
        #    first version of this box opened reading "08/31/2026, 02:51:29 PM".
        #    Nobody schedules a notice to the second, and a field showing one
        #    reads as a machine's value rather than a suggestion — which is
        #    exactly backwards, because the whole point of the prefill is that
        #    it is a starting point somebody is meant to change. Seen in the
        #    browser; no test would have said a word.
        now = local_now().replace(second=0, microsecond=0)
        self.initial.setdefault("starts_showing", now)
        self.initial.setdefault(
            "stops_showing", now + datetime.timedelta(days=DEFAULT_RUN_DAYS))

    def clean(self):
        cleaned = super().clean()
        # ⚠️ The return value is discarded on purpose, and this is where a notice
        #    differs from an event. There, the Spec is carried on to
        #    refuse_narrowing_below_the_roles(); here there is nothing below to
        #    compare it against, so running the two rules is the whole job.
        self.clean_audience()
        return cleaned
