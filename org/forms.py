"""Forms for the org pages. Permanent asset: plain django.forms, no admin (D18).

Here rather than in events/forms.py, where this started. The subject of P5 is a
ministry, so by D17 it belongs to this app — and the import direction settles
it: events depends on org (Event -> Ministry), so org reaching back into events
inverts the one dependency INSTALLED_APPS spells out. Same lesson as the
cross-app admin assembly in B5: the arrow points one way, and a form is not an
exception to it.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.db import models

from contact.models import Contact
from org.audience import (
    Audience,
    refuse_empty_audience,
    refuse_redundant_audience,
)


class GrantForm(forms.Form):
    """P5: appoint somebody as a ministry's admin.

    A plain Form: granted_by comes from the session, not from the page, and a
    field somebody could type into would be a field somebody could lie in.
    """

    contact = forms.ModelChoiceField(queryset=None, label="Who")
    start_date = forms.DateField(
        required=False, label="Starting on", widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contact"].queryset = Contact.objects.filter(
            is_active=True, contact_type=Contact.ContactType.INDIVIDUAL)


class AudienceFormMixin:
    """The two rules an audience obeys on its own, for any form that edits one.

    🔴 This is where they live, and not in `Model.clean()`, because a
       ManyToMany is written after save() while full_clean() runs before it: on
       a new object the field cannot be read at all, and on an existing one it
       reads the row already in the database rather than what is being
       submitted. A form is the first layer that holds the new value. The full
       working is in org/audience.py, above refuse_empty_audience().

    ⚠️ So the admin needs `form = ` pointing at a subclass of this, or the admin
       has no check whatsoever. Not drawing a control keeps nobody out; this
       does.

    ⚠️ **The two rules here are the ones an audience obeys alone**, and that is
       the whole of what this class holds (split 2026-08-31). "A role cannot be
       wider than its event" is a rule about a *pair* of rows, it is worded in
       event vocabulary, and it lives with the tables it is about — see
       `events.forms.EventAudienceFormMixin`, which subclasses this and adds
       exactly that half. A form for a table with no parent and no children
       (Notice) mixes in this one and is finished.
    """

    #: Which side of the pair this form edits — read off the model rather than
    #: declared here (2026-08-27). It was a constant on each form while
    #: refuse_bad_audience() answered the same question with isinstance(), so a
    #: third audience-bearing table could have been classified differently by
    #: the two. The model is the thing that knows; see Audience.AUDIENCE_ON.
    @property
    def AUDIENCE_ON(self):
        return self._meta.model.AUDIENCE_ON

    #: ⭐ The convenience tick, and it is **not a stored value** — ticking it
    #: sets the two boxes below it and nothing else reaches the database.
    #: Decision 11: "everyone" means exactly "outsiders + all staff", and giving
    #: that state a spelling of its own would be one state with two
    #: representations, which is what refuse_redundant_audience() exists to stop
    #: happening a row lower down.
    #:
    #: ⚠️ It exists because the widest-looking box is not the widest setting.
    #:    Somebody publishing an open day ticks "People with no current post" —
    #:    it reads as the outside world, so it reads as everybody — and has just
    #:    hidden the event from every member of staff. Nothing raises, and the
    #:    only person who could notice is the one who cannot see it.
    #:
    #: ⚠️ Plain server-side expansion, so it works with no JavaScript at all
    #:    (D24). The greying-out in the template is the enhancement; this is not.
    EVERYONE_FIELD = "audience_is_everyone"

    #: Where a message about the audience **as a whole** goes. The three ticks
    #: render as one group, so the first of them puts the sentence at the top of
    #: that group — which is where somebody hunting for "which box" starts.
    #:
    #: ⚠️ Only for faults that are about the set. A message about one tick goes
    #:    on that tick, and since 2026-08-27 refuse_wider_than_event() says
    #:    which one it means rather than leaving every refusal here. Reaching
    #:    for this constant when the rule already named a field is how all
    #:    three ended up on one box the first time.
    AUDIENCE_GROUP_FIELD = Audience.AUDIENCE_FIELDS[0]

    def __init__(self, *args, **kwargs):
        """Adds the "Everyone" tick, and ticks it back on for an audience that is one.

        ⚠️ Added here rather than declared on the class: this is a plain mixin,
           not a form, so Django's metaclass never looks at it for fields —
           declaring one would simply not appear, silently.

        ⚠️ The tick is **derived** when the form is drawn, not stored. An event
           saved as outsiders + all-staff comes back showing "Everyone", which
           is what the person chose; showing them two separate ticks instead
           would make the convenience a one-way trip and teach them not to use
           it. Decision 11 puts this derivation in Python for exactly that
           reason — a template doing it would be a second place it could be got
           wrong.
        """
        super().__init__(*args, **kwargs)
        # ⚠️ Only when the two boxes it fills in are actually on this form
        #    (2026-08-28). A ModelForm whose audience fields are all readonly
        #    has none of them — which is exactly the form the admin builds for
        #    somebody holding `view_event` and nothing else — and the tick then
        #    had nothing to sit above: order_fields() below went looking for
        #    `visible_to_outsiders` in an empty list and raised ValueError, so
        #    **opening a change page in read-only mode was a 500**. Reproduced
        #    on 2026-08-28, for Event and EventRole alike.
        #
        #    Returning early is also the right answer on its own terms, not just
        #    a way to dodge the crash: a convenience tick over two boxes nobody
        #    can edit is a control that does nothing, and audience() reads it
        #    with `.get()`, so its absence changes no answer.
        self.offer_the_ministries()
        pair = ("visible_to_outsiders", "visible_to_all_staff")
        if not all(name in self.fields for name in pair):
            return
        self.fields[self.EVERYONE_FIELD] = forms.BooleanField(
            required=False,
            label="Everyone",
            help_text="Outside volunteers and staff alike — the same as "
                      "ticking both boxes below.",
        )
        instance = getattr(self, "instance", None)
        if instance is not None and instance.pk is not None:
            covers_both = (
                instance.visible_to_outsiders and instance.visible_to_all_staff)
            self.initial.setdefault(self.EVERYONE_FIELD, covers_both)
            # 🔴 And the pair it stands for comes back **unticked**, which is
            #    what makes this control work in both directions.
            #
            #    Until 2026-09-08 all three came back ticked, and the tick was
            #    therefore one-way: audience() reads it as
            #    `everyone or visible_to_outsiders`, so unticking "Everyone"
            #    left the two below still ticked, stored the same audience it
            #    already had, and reported success. The one thing somebody opens
            #    this form to do — take an event back off the public listing —
            #    did nothing at all and said nothing at all.
            #
            #    Unticked, the screen reads the way app.css already describes
            #    the greying: they are not broken, they are *already covered*.
            #    Untick "Everyone" and they come back live and empty, so the
            #    save is refused by refuse_empty_audience() with "Say who this
            #    is for" — which is the honest answer to what was just asked.
            #
            # ⚠️ Nothing is lost by blanking them: audience() expands the tick
            #    back into both values on the way in (decision 11 — the tick is
            #    a convenience on the form, the database stores the two).
            #
            # ⚠️ **The pair only.** The same move on visible_to_ministries would
            #    be data loss, not presentation: those rows are what somebody
            #    chose, and "Everybody on the books" covering them does not make
            #    them recoverable from a boolean. That box keeps its state and
            #    says so on the page instead.
            # ⚠️ Assigned, not setdefault(). BaseModelForm has already filled
            #    self.initial from model_to_dict(instance), so both keys are
            #    there and holding True — setdefault would look right and do
            #    nothing at all.
            if covers_both:
                for name in pair:
                    self.initial[name] = False
        self.order_fields(None)

    def offer_the_ministries(self):
        """Which ministries this form may tick: the live ones, plus this row's own.

        🔴 The second half is what stops a retired ministry locking an event out
           of the site. `limit_choices_to={"is_active": True}` on the field, and
           this queryset behind it, together meant the tick simply **stopped
           being rendered** when a ministry was retired — so a browser could not
           submit it, the audience came back narrower than it was stored, and
           the save was refused by the containment rule with "narrow that role
           first". There is no page for narrowing a role: events/urls.py has
           create and delete and nothing between them. So the event could not be
           saved from the site again, for any edit at all, including fixing a
           typo. Reproduced 2026-09-05, fixed 2026-09-08.

           ⚠️ Retiring a ministry is not an obscure act — org/permissions.py
              recommends it in as many words ("we are not running this any more"
              is is_active=False) and withholds delete_ministry on that basis.

        ⚠️ Already-ticked rows are offered, never re-ticked: `initial` is
           untouched, so this only makes the value expressible. Somebody can
           take it off, or leave it — what they cannot do is be trapped by a box
           they are not allowed to see.

        ⚠️ One implementation, three forms. EventForm, EventRoleForm and
           NoticeForm each wrote this line with its own copy of the comment
           explaining it; the third was added by copying the second. It belongs
           on the mixin they already share.

        ⚠️ Not symmetrical with `ministry_ids_administered_by()`, which *does*
           filter on is_active — retiring a ministry takes away its admin's
           authority immediately, while `on_the_books_q()` does not, so its
           staff keep seeing what they were already ticked into. Two different
           questions ("who may act" against "who may look"), and the asymmetry
           is deliberate rather than an oversight.
        """
        field = self.fields.get("visible_to_ministries")
        if field is None:
            return
        # ⚠️ Built from the model's own manager, not from `field.queryset` —
        #    that one already carries the field's `limit_choices_to`
        #    (is_active=True), so filtering it again could only ever narrow.
        #    Widening past limit_choices_to is the point here, and it is safe
        #    because a submitted value is validated against *this* queryset.
        ministries = field.queryset.model.objects
        live = models.Q(is_active=True)
        instance = getattr(self, "instance", None)
        if instance is not None and instance.pk is not None:
            live |= models.Q(pk__in=instance.visible_to_ministries.values("pk"))
        field.queryset = ministries.filter(live).distinct().order_by("name")

    def order_fields(self, field_order):
        """Order as asked, then always re-seat the tick above the pair it fills in.

        ⚠️ An override rather than a method subclasses must remember to call
           (2026-08-27). Django's `order_fields()` sends anything it was not
           handed to the **end**, so a form that lists its fields explicitly —
           EventRoleForm does — threw the tick down under "notes", three fields
           below the two it explains. That was fixed by having that form call a
           `place_everyone_tick()` afterwards, which is a convention every
           future audience form would have to know about and nothing would
           enforce. Here the re-seating happens inside the very call that
           disturbs it, so it cannot be skipped.

        ⚠️ `order_fields(None)` is how __init__ asks for just the re-seating:
           Django returns early on None, and the half below still runs.
        """
        super().order_fields(field_order)
        if self.EVERYONE_FIELD not in self.fields:
            return
        names = [name for name in self.fields if name != self.EVERYONE_FIELD]
        names.insert(names.index("visible_to_outsiders"), self.EVERYONE_FIELD)
        super().order_fields(names)

    def audience(self):
        """The submitted audience as an Audience.Spec.

        ⚠️ Built from `cleaned_data`, never from `self.instance` — on an edit
           the instance still holds the audience already in the database, and
           validating that is the trap L2.1 records in full.

        ⚠️ "Everyone" is expanded here, so every rule below and every caller
           sees the two values that actually get stored. Expanding it later —
           in save(), say — would let the rules validate one audience while the
           database received another.
        """
        ministries = self.cleaned_data.get("visible_to_ministries") or []
        everyone = bool(self.cleaned_data.get(self.EVERYONE_FIELD))
        return Audience.Spec(
            outsiders=everyone or bool(self.cleaned_data.get("visible_to_outsiders")),
            all_staff=everyone or bool(self.cleaned_data.get("visible_to_all_staff")),
            ministries=frozenset(m.pk for m in ministries),
        )

    def clean_audience(self):
        """Run the two rules that an audience obeys on its own.

        ⚠️ Errors land on a **field**, never on the form as a whole. "Say who
           this is for" at the top of a long publish form leaves somebody
           hunting for which box it means — and landing on the *wrong* field is
           that same failure with an extra step, which is why the two rules
           below go to different places and why refuse_wider_than_event() names
           its own.

        Returns the Spec when it is usable, or None when the audience is empty
        — ⚠️ so the caller can stop rather than go on to compare an empty
        audience against things. An event ticked for nobody is wider than
        nothing, so every one of its roles would also be reported, and the one
        real fault would be buried under a list of consequences.
        """
        spec = self.audience()
        # 🔴 Written back, and this line is what makes "Everyone" real. The Spec
        #    is only what the rules see; what reaches the database is whatever
        #    ModelForm finds in cleaned_data. Without these two the form would
        #    validate an audience of "everyone" and then save one of "nobody" —
        #    a published event visible to no one, past the very rule below that
        #    exists to prevent it.
        self.cleaned_data["visible_to_outsiders"] = spec.outsiders
        self.cleaned_data["visible_to_all_staff"] = spec.all_staff
        empty = False
        try:
            refuse_empty_audience(
                outsiders=spec.outsiders, all_staff=spec.all_staff,
                ministries=spec.ministries, on=self.AUDIENCE_ON)
        except ValidationError as error:
            # Nobody ticked: every box is wrong, so there is no single guilty
            # one — see AUDIENCE_GROUP_FIELD.
            self.add_error(self.AUDIENCE_GROUP_FIELD, error)
            empty = True
        try:
            refuse_redundant_audience(
                all_staff=spec.all_staff, ministries=spec.ministries,
                # ⚠️ Named after the box they ticked. Somebody who ticked
                #    "Everyone" never saw the phrase "Everybody on the books"
                #    and cannot act on a sentence about it.
                covered_by=("everyone"
                            if self.cleaned_data.get(self.EVERYONE_FIELD)
                            else "all_staff"))
        except ValidationError as error:
            self.add_error("visible_to_ministries", error)
        if not empty:
            # ⚠️ On the instance too, which is not a duplicate of the two
            #    cleaned_data lines above: it is how an **inline role reaches
            #    its unsaved parent's** audience. Django's admin validates the
            #    parent form first and then builds the formsets with
            #    `instance=form.instance` — the very object this writes to — so
            #    on the Event add page a role can be compared against the event
            #    it is about to belong to. Nothing reads it once the event is
            #    saved; see refuse_wider_than_its_event().
            #
            # ⚠️ **Only when the audience is not empty**, for the same reason
            #    this method returns None then. An empty audience is wider than
            #    nothing, so publishing it to the inlines would report every
            #    role on the page as too wide and bury the one real fault under
            #    a list of its consequences. Left over from the first draft of
            #    this on 2026-08-28, where it sat above the check.
            #
            # ⚠️ Written unconditionally even though only the event/role pair
            #    reads it (2026-08-31). On a table with no children it is a
            #    write nobody reads, which is the cheaper of the two options —
            #    the alternative is a hook method here for one subclass to
            #    override, and a hook is a thing the next person has to learn
            #    about in order to not get it wrong.
            self.instance.submitted_audience = spec
        return None if empty else spec
