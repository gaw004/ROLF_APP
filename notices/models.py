"""Notices: things people need to know, that are not occasions.

"The car park is closed from the 1st to the 15th." "We no longer take cheques."
Neither of those happens at a time, and neither is something to sign up for.

⚠️ **This table exists because Event could not hold them, and the attempt to
   make it hold them was the wrong shape.** 06-roadmap.md's decision 2 answered
   "how do we tell a notice from a half-built event" with a boolean on Event,
   `takes_signups`. It was never written, and it was a fake requirement twice
   over — see D41. The short version: for a notice that *does* happen at a time,
   decision 3 already covers it (open an `attending` role with no limit and let
   whoever wants to be remembered sign up); and for a notice that does not,
   Event cannot hold it at all, because `start_time` and `end_time` are both
   NOT NULL and inventing values for them puts the row in R1's event count, on
   the calendar, and in everybody's .ics.

   The foundation had already said so, and the roadmap implemented the opposite:
   「那种完全不需要 sign up 也许叫做公告？**又是另一回事**。」
"""

from django.db import models
from django.db.models import F, Q
from simple_history.models import HistoricalRecords

from contact.models import Contact
from core.constraints import ConstraintErrorFieldMixin
from core.limits import LONG_TEXT
from core.models import TimeStampedModel
from core.timeutils import local_now
from org.audience import Audience, AudienceQuerySetMixin
from org.models import Ministry


class NoticeQuerySet(AudienceQuerySetMixin, models.QuerySet):
    """Published-and-in-its-window, and published-and-past. Nothing in between.

    ⚠️ Both take `now` the way EventQuerySet.open_for_signup() does, and for the
       same mechanical reason: `ViewsAreThinGuardTests` forbids `local_now(` in
       any views.py, so the clock has to be read here.

    ⚠️ Neither of these asks who is looking. `for_audience()` is a **separate**
       call every read path has to make — see NoticesAreNarrowedGuardTests,
       which is the only thing standing between this table and a notice for one
       ministry's staff appearing on everybody's home page.
    """

    def showing(self, now=None):
        """On the board right now."""
        now = now or local_now()
        return self.filter(
            status=Notice.Status.PUBLISHED,
            starts_showing__lte=now,
            stops_showing__gt=now,
        )

    def past(self, now=None):
        """Published, and its time on the board is over.

        ⚠️ This is what makes a required `stops_showing` honest. A notice coming
           down is it leaving the board, never it being lost: the row stays, the
           history stays, and this queryset is where somebody goes to ask "what
           did that say about the cheques again".
        """
        now = now or local_now()
        return self.filter(
            status=Notice.Status.PUBLISHED, stops_showing__lte=now)


class Notice(Audience, ConstraintErrorFieldMixin, TimeStampedModel):
    """One thing worth telling people, for as long as it is worth telling them.

    ⚠️ It has no occasion, so it has none of what an occasion brings: no roles,
       no signups, no attendance, no hours. It is not in R1's count of events,
       not on the calendar, and not in anybody's .ics. If something on this row
       ever starts wanting one of those, the thing being described is an Event
       and it should be one.
    """

    #: Its own key rather than reusing "event", because the messages differ and
    #: a wrong-but-plausible sentence is worse than a missing one. See
    #: EMPTY_AUDIENCE_MESSAGE in org/audience.py.
    AUDIENCE_ON = "notice"

    #: ⭐ None, meaning "judge who is on the books **today**". This table has no
    #:    day of its own, and that is not a gap to be filled with a stand-in —
    #:    see Audience.AUDIENCE_DAY for the case that decides it. It is also the
    #:    single line in this file that says most plainly why a notice is not an
    #:    event: an event is judged on its own day, a notice on the day somebody
    #:    is reading it.
    AUDIENCE_DAY = None

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        # ⚠️ There is no EXPIRED, deliberately. Whether a notice is still up is
        #    answered by the clock against stops_showing, and a stored copy of a
        #    clock's answer is a cache nobody updates. Event.status paid for
        #    this exact lesson once already — see the note on COMPLETED in
        #    events/models.py, where "is it over" had to stop being a status the
        #    day the clock started answering it.
        #
        # ⚠️ Nor is there WITHDRAWN. "Take it down early" is `take_down()`,
        #    which moves stops_showing to now — one mechanism for coming off the
        #    board, whether it comes off on schedule or ahead of it.

    title = models.CharField(max_length=200)
    # Capped for the same reason Event.description is: every page that draws a
    # notice draws its body, so an unbounded column is one pasted document away
    # from a home page that will not load on a phone.
    body = models.TextField(max_length=LONG_TEXT)
    # Who answers for it. Not nullable: a notice nobody owns is one nobody can
    # be asked about, and it is what can_manage_notice() judges against.
    ministry = models.ForeignKey(
        Ministry, on_delete=models.PROTECT, related_name="notices")
    owner = models.ForeignKey(
        Contact, on_delete=models.PROTECT, related_name="notices_owned")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT)

    starts_showing = models.DateTimeField(
        verbose_name="Goes up",
        help_text="When people start seeing it. Now, unless you are writing "
                  "ahead of time.",
    )
    # ⚠️ Required, and this is the one decision on this model somebody will want
    #    to undo. Every product with this feature bounds it — Viva caps the
    #    expiry at two weeks, a Chatter announcement will not post without a
    #    date — and they all bound it for one reason: a board that never clears
    #    itself stops being read, and then the notice that mattered is on it
    #    with nine that do not.
    #
    #    ⚠️ "We no longer take cheques" is permanent and still gets a date. What
    #       is permanent is the *fact*, and a board is for **changes**, not for
    #       standing state. The fact stays reachable in `past()` and in the
    #       history; what expires is its claim on the front page. If the
    #       foundation ever needs somewhere for standing state to live, that is
    #       a policy page, and it is a different thing from this table.
    stops_showing = models.DateTimeField(
        verbose_name="Comes down",
        help_text="It stops showing after this, and moves to the past notices "
                  "list. Nothing is deleted.",
    )

    history = HistoricalRecords()

    objects = models.Manager.from_queryset(NoticeQuerySet)()

    class Meta:
        ordering = ["-starts_showing", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(stops_showing__gt=F("starts_showing")),
                name="notice_comes_down_after_it_goes_up",
                violation_error_message=(
                    "A notice has to come down after it goes up."),
                violation_error_code="notice_window_backwards",
            ),
        ]
        indexes = [
            # The one query every page makes: showing() filters on all three.
            models.Index(fields=["status", "stops_showing", "starts_showing"]),
        ]

    def __str__(self):
        return self.title

    @property
    def is_showing(self):
        """The row's own answer to what `showing()` asks of the table.

        ⚠️ Written to match that queryset condition for condition, the way
           Event.is_open_for_signup matches open_for_signup(). Two spellings of
           one predicate is a bug waiting for the day they disagree.
        """
        now = local_now()
        return (
            self.status == Notice.Status.PUBLISHED
            and self.starts_showing <= now < self.stops_showing
        )
