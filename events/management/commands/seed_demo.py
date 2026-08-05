"""One command that builds a demo foundation to click through.

The acceptance walk plays three roles and checks about thirty things, several
of which only show up on an awkward branch: a role nobody signed up for, a
person with no email and no phone, somebody whose birth date is unknown, hours
entered from paper with no timestamps. Building those by hand is slow, and —
worse — easy to get subtly wrong, which produces a walk that appears to pass
while testing nothing. Every branch the checklist asks about is created here,
deliberately.

Three safety rules, none of them optional:

1. Idempotent. Everything goes through get_or_create, so running it three times
   does not leave three 张三 — which would set the duplicate warnings off on
   every page from then on.
2. Refuses to run outside development. One mistaken run against production
   fills the contact table with invented people who, by this system's own
   design, look exactly like real ones and are near-impossible to pick out
   afterwards. --force exists for the deliberate case.
3. Invented data only. No real person's name goes into this repository.
"""

import datetime
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.services import register_account
from contact.models import Contact, EmergencyContact, RelationshipType
from core.timeutils import local_now, local_today
from events.models import Event, EventRole, EventType, Participation, ParticipationRole
from events.services import check_in, check_out, record_hours
from org.models import Assignment, EmploymentType, Ministry, MinistryRole, Position
from org.permissions import foundation_admin_group

HOUR = datetime.timedelta(hours=1)
DAY = datetime.timedelta(days=1)
PASSWORD = "demo-password-not-a-secret"


class Command(BaseCommand):
    help = "Create a demo foundation: ministries, posts, accounts, events, signups."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true",
            help="Run even when DEBUG is off. Think first — this writes invented "
                 "people that are indistinguishable from real ones.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "seed_demo writes invented people and refuses to run with DEBUG off. "
                "Pass --force if that is genuinely what you want."
            )

        self.dictionaries()
        self.ministries_and_posts()
        self.accounts()
        self.events()
        if not options["verbosity"]:
            return
        self.stdout.write(self.style.SUCCESS("\nDemo data ready. Accounts (password "
                                             f"'{PASSWORD}'):"))
        for line in [
            "  foundation_admin  — the foundation-wide group; appoints ministry admins (P5)",
            "  pantry_admin      — administers Food Pantry",
            "  tax_admin         — administers Tax Help (use this one to try over-reach)",
            "  volunteer_adult   — an ordinary volunteer",
            "  volunteer_minor   — under 18; signing up needs a guardian's consent",
            "  volunteer_unknown — no date of birth; treated as a minor",
            "  volunteer_silent  — no email and no phone; lands in “cannot be reached”",
        ]:
            self.stdout.write(line)

    # --- the pieces ------------------------------------------------------

    def dictionaries(self):
        # Not created here: contact/0004_seed_relationship_types owns this row,
        # because EmergencyContact.relationship_type is a required FK and a
        # production database has to come up with somewhere for it to point.
        self.parent_of = RelationshipType.objects.get(code="parent")
        self.full_time, _ = EmploymentType.objects.get_or_create(
            code="full_time", defaults={"name": "Full time"})
        EmploymentType.objects.get_or_create(code="part_time", defaults={"name": "Part time"})
        self.distribution, _ = EventType.objects.get_or_create(
            code="distribution", defaults={"name": "Distribution"})
        EventType.objects.get_or_create(code="class", defaults={"name": "Class"})
        # The catch-all role has to exist: event_role is not nullable, so "no
        # particular job" needs somewhere to land.
        self.general = ParticipationRole.seed_general()
        self.lifting, _ = ParticipationRole.objects.get_or_create(
            code="lifting", defaults={"name": "Lifting"})
        self.welcome, _ = ParticipationRole.objects.get_or_create(
            code="welcome", defaults={"name": "Welcome desk"})
        self.interpreting, _ = ParticipationRole.objects.get_or_create(
            code="interpreting", defaults={"name": "Interpreting"})

    def ministries_and_posts(self):
        # ⚠️ One with a website and one without, deliberately: the ministry's
        #    name on an event page is a link only where there is somewhere to
        #    link to, and demo data that exercises one branch verifies half a
        #    feature.
        self.pantry, _ = Ministry.objects.get_or_create(
            code="food_pantry",
            defaults={"name": "Food Pantry",
                      "website": "https://example.invalid/food-pantry"})
        self.tax, _ = Ministry.objects.get_or_create(
            code="tax_help", defaults={"name": "Tax Help"})

        self.pantry_lead, _ = Position.objects.get_or_create(
            code="pantry_lead",
            defaults={
                "name": "Food Pantry lead", "kind": Position.Kind.EMPLOYEE,
                "ministry": self.pantry, "is_leader": True,
            },
        )
        self.pantry_staff, _ = Position.objects.get_or_create(
            code="pantry_staff",
            defaults={
                "name": "Food Pantry officer", "kind": Position.Kind.EMPLOYEE,
                "ministry": self.pantry, "reports_to": self.pantry_lead,
            },
        )
        # A post nobody holds. Vacancy is a first-class state, and a demo with
        # no vacancy in it cannot show that.
        Position.objects.get_or_create(
            code="pantry_driver",
            defaults={
                "name": "Driver", "kind": Position.Kind.VOLUNTEER,
                "ministry": self.pantry, "reports_to": self.pantry_lead,
            },
        )

    def account(self, username, last_name, first_name="", **contact_fields):
        user = get_user_model().objects.filter(username=username).first()
        if user:
            return user
        return register_account(
            username=username, password=PASSWORD,
            legal_last_name=last_name, legal_first_name=first_name,
            **contact_fields,
        )

    def accounts(self):
        self.boss = self.account(
            "foundation_admin", "Boss", "Terry", email="boss@example.invalid",
            birth_date=datetime.date(1975, 4, 4))
        # Staff, because the acceptance walk reads R1–R3 off the admin
        # changelist. The scoped pages still refuse them — a global group is
        # not a ministry scope, and permissions.py grants no exemptions.
        self.boss.is_staff = True
        self.boss.save(update_fields=["is_staff"])
        self.boss.groups.add(foundation_admin_group())

        self.pantry_admin = self.account(
            "pantry_admin", "Zhang", "San", email="zhangsan@example.invalid",
            birth_date=datetime.date(1982, 6, 1))
        self.tax_admin = self.account(
            "tax_admin", "Chen", "Si", email="chensi@example.invalid",
            birth_date=datetime.date(1979, 9, 9))
        # Two ministries, two admins. One of each cannot demonstrate scoping:
        # "she can see her own" passes just as well with no scoping at all.
        MinistryRole.objects.get_or_create(
            contact=self.pantry_admin.contact, ministry=self.pantry,
            role=MinistryRole.Role.ADMIN, start_date=None,
            defaults={"granted_by": self.boss},
        )
        MinistryRole.objects.get_or_create(
            contact=self.tax_admin.contact, ministry=self.tax,
            role=MinistryRole.Role.ADMIN, start_date=None,
            defaults={"granted_by": self.boss},
        )

        self.adult = self.account(
            "volunteer_adult", "Li", "Si", email="lisi@example.invalid",
            phone="+14085550101", birth_date=datetime.date(1990, 2, 2))
        self.minor = self.account(
            "volunteer_minor", "Xiao", "Ming", email="xiaoming@example.invalid",
            birth_date=local_today() - datetime.timedelta(days=365 * 15))
        # Unknown birth date: the cautious branch of the three-state. Signing
        # up asks for consent, and notifications go to the guardian.
        self.unknown = self.account(
            "volunteer_unknown", "Wang", "Unknown", email="wang@example.invalid",
            birth_date=None)
        # ⚠️ Both of these need somebody to call: sign_up() refuses a minor —
        #    and an unknown birth date counts as one — with no emergency
        #    contact on file. Demo data has to satisfy the rules it demonstrates,
        #    or the acceptance walk fails on its own fixtures.
        for person, kin, kin_email in [
            (self.minor, "Ming's mother", "ming.mother@example.invalid"),
            (self.unknown, "Wang's guardian", "wang.guardian@example.invalid"),
        ]:
            EmergencyContact.objects.get_or_create(
                person=person.contact, name=kin, phone="+14085550199",
                defaults={"relationship_type": self.parent_of, "email": kin_email},
            )
        # Neither an email nor a phone. Without this person the "cannot be
        # reached" group on the notification page is always empty, which looks
        # like a pass and verifies nothing.
        self.silent = self.account("volunteer_silent", "Noreach", "Sam",
                                   birth_date=datetime.date(1988, 3, 3))
        # A second minor whose only route to a guardian is the emergency
        # contact, with no consent address of her own — so she exercises the
        # fallback branch of resolve_recipients().
        #
        # ⚠️ 2026-08-05 更正：这里原来写的是「phone-only，用来跑 SMS 兜底」。
        #    EmergencyContact.email 当天变成必填，所以这一行现在走的是 email。
        #    SMS 那条分支仍然存在（email 为空时），但只有
        #    contact.tests.EmergencyContactReachabilityTests 覆盖得到它 ——
        #    演示数据不再制造一个模型自己会拒绝的行。
        self.minor_emergency = self.account(
            "volunteer_minor2", "Zhao", "Xiaoyu",
            birth_date=local_today() - datetime.timedelta(days=365 * 16))
        EmergencyContact.objects.get_or_create(
            person=self.minor_emergency.contact, name="Zhao's mother", phone="+14085550188",
            defaults={"relationship_type": self.parent_of,
                      "email": "zhao.mother@example.invalid"},
        )

        # An employee who was in post on the day of the past event and has
        # since left — R8's clock is the day of the event, and without somebody
        # like this that is untestable by hand.
        self.leaver = Contact.objects.get_or_create(
            legal_last_name="Sun", legal_first_name="Former",
            defaults={"contact_type": Contact.ContactType.INDIVIDUAL,
                      "birth_date": datetime.date(1985, 1, 1)},
        )[0]
        Assignment.objects.get_or_create(
            contact=self.pantry_admin.contact, position=self.pantry_lead,
            start_date=local_today() - datetime.timedelta(days=400),
            defaults={"employment_type": self.full_time},
        )
        Assignment.objects.get_or_create(
            contact=self.leaver, position=self.pantry_staff,
            start_date=local_today() - datetime.timedelta(days=400),
            defaults={
                "employment_type": self.full_time,
                "end_date": local_today() - datetime.timedelta(days=10),
            },
        )

    def events(self):
        now = local_now()

        # 1. Open, taking signups — three roles, one of them filled, one half
        #    filled, and one nobody has taken. That third one is R4's whole
        #    point: the event has three roles, not two.
        self.open_event, created = Event.objects.get_or_create(
            name="Saturday distribution",
            defaults={
                "event_type": self.distribution, "ministry": self.pantry,
                "start_time": now + 7 * DAY, "end_time": now + 7 * DAY + 3 * HOUR,
                "location": "Church ground floor", "owner": self.pantry_admin.contact,
                "status": Event.Status.OPEN,
            },
        )
        lifting = self.role(self.open_event, self.lifting, 2)
        welcome = self.role(self.open_event, self.welcome, 4)
        self.role(self.open_event, self.interpreting, 1)   # nobody signs up

        if created:
            self.signup(self.adult, lifting)
            self.signup(self.silent, lifting)              # unreachable
            self.signup(self.minor, welcome, consent_email="parent@example.invalid")
            self.signup(self.unknown, welcome, consent_phone="+14085550177")
            self.signup(self.minor_emergency, welcome)     # falls back to SMS

        # 2. A draft: volunteers must not see it at all.
        Event.objects.get_or_create(
            name="Christmas distribution (not published yet)",
            defaults={
                "event_type": self.distribution, "ministry": self.pantry,
                "start_time": now + 30 * DAY, "end_time": now + 30 * DAY + 2 * HOUR,
                "owner": self.pantry_admin.contact, "status": Event.Status.DRAFT,
            },
        )

        # 3. Confirmed and full: absent from the list, but whoever signed up
        #    can still open it. That is the difference between "can see" and
        #    "can join", and P6's cancel link depends on it.
        confirmed, made = Event.objects.get_or_create(
            name="English corner (full)",
            defaults={
                "event_type": self.distribution, "ministry": self.pantry,
                "start_time": now + 3 * DAY, "end_time": now + 3 * DAY + 2 * HOUR,
                "owner": self.pantry_admin.contact, "status": Event.Status.CONFIRMED,
            },
        )
        if made:
            self.signup(self.adult, self.role(confirmed, self.general, 1))

        # 4. A finished event with hours on it — R6 / R7 — including one entry
        #    from a paper sheet with no timestamps at all.
        past, made = Event.objects.get_or_create(
            name="Last month's distribution",
            defaults={
                "event_type": self.distribution, "ministry": self.pantry,
                "start_time": now - 30 * DAY, "end_time": now - 30 * DAY + 3 * HOUR,
                "owner": self.pantry_admin.contact, "status": Event.Status.COMPLETED,
            },
        )
        if made:
            past_lifting = self.role(past, self.lifting, 3)
            past_welcome = self.role(past, self.welcome, 2)
            self.role(past, self.interpreting, 1)          # zero turnout, still a role

            attended = self.signup(self.adult, past_lifting)
            check_in(attended, at=past.start_time)
            check_out(attended, at=past.start_time + 3 * HOUR)

            # The employee who has since left. R8 asked on the day of the event
            # finds them; asked with today's date it would not, silently.
            leaver_row = Participation.objects.create(
                contact=self.leaver, event_role=past_lifting,
                registered_at=past.start_time)
            check_in(leaver_row, at=past.start_time)
            check_out(leaver_row, at=past.start_time + 2 * HOUR)

            # Paper sign-in: hours by hand, no timestamps. Still counts.
            paper = self.signup(self.pantry_admin, past_welcome)
            record_hours(paper, Decimal("4.00"))

        # 5. The tax ministry's own event, so over-reach can be tried against
        #    something that really exists.
        Event.objects.get_or_create(
            name="Tax clinic",
            defaults={
                "event_type": self.distribution, "ministry": self.tax,
                "start_time": now + 5 * DAY, "end_time": now + 5 * DAY + 2 * HOUR,
                "owner": self.tax_admin.contact, "status": Event.Status.OPEN,
            },
        )

    # --- helpers ---------------------------------------------------------

    def role(self, event, participation_role, needed_count):
        return EventRole.objects.get_or_create(
            event=event, role=participation_role,
            defaults={"needed_count": needed_count},
        )[0]

    def signup(self, who, event_role, **consent):
        contact = who.contact if hasattr(who, "contact") else who
        fields = {"registered_at": local_now()}
        if contact.is_minor in (True, None) and consent:
            fields.update(
                consent_given_by="Guardian (demo data)",
                consent_relationship=self.parent_of,
                consent_at=local_now(),
                consent_method=Participation.ConsentMethod.VERBAL,
                **consent,
            )
        return Participation.objects.get_or_create(
            contact=contact, event_role=event_role, defaults=fields)[0]
