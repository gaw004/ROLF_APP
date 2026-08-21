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
from events.services import (
    check_in,
    check_out,
    mark_absent,
    record_hours,
    set_served_as,
)
from org.models import Assignment, EmploymentType, Ministry, MinistryRole, Position
from org.permissions import foundation_admin_group

HOUR = datetime.timedelta(hours=1)
DAY = datetime.timedelta(days=1)
PASSWORD = "demo-password-not-a-secret"

#: The demo logins, keyed by the part each one plays in the acceptance walk,
#: with the address it logs in with and the sentence printed for it.
#:
#: ⚠️ One dictionary, read three times: the accounts are created from it, the
#:    credentials printed at the end are generated from it, and
#:    events/tests.py's walk logs in through it. The printed list used to be
#:    maintained by hand next to the code that made the accounts — so the day
#:    the login name changed, the command cheerfully printed seven credentials
#:    that no longer existed, and the walk stopped at step one.
#:
#: ⚠️ Roles, not names, are the keys. The acceptance checklist says "play the
#:    pantry admin"; it does not care what address that person has.
DEMO_ACCOUNTS = {
    "foundation_admin": (
        "boss@example.invalid",
        "the foundation-wide group; appoints ministry admins (P5)"),
    "pantry_admin": (
        "zhangsan@example.invalid", "administers Food Pantry"),
    "tax_admin": (
        "chensi@example.invalid",
        "administers Tax Help (use this one to try over-reach)"),
    "participant_adult": (
        "lisi@example.invalid", "an ordinary volunteer"),
    "participant_minor": (
        "xiaoming@example.invalid",
        "under 18; signing up needs a guardian's consent"),
    "participant_unknown": (
        "wang@example.invalid", "no date of birth; treated as a minor"),
    "participant_minor2": (
        "zhaoxiaoyu@example.invalid",
        "under 18, reachable only through her emergency contact"),
    # ⚠️ An account, not just a Contact, and the reason is an acceptance line
    #    that cannot otherwise be walked: D38 section 4 says the person whose
    #    weekend it was must be able to **see** what their identity says and
    #    who set it. Log in as this one after an admin corrects her on the
    #    signups page. Every other person in this demo who has an identity has
    #    no way to look at it.
    "staff_unpaid": (
        "ada@example.invalid",
        "unpaid staff — on the books, not paid; sees her own served-as"),
}


def demo_login(role):
    """The address the given demo role logs in with."""
    return DEMO_ACCOUNTS[role][0]


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
        # ⚠️ Log in with the **address**, not a handle (2026-08-06), and printed
        #    from the same dictionary the accounts were made from — see
        #    DEMO_ACCOUNTS for why that matters.
        width = max(len(address) for address, _ in DEMO_ACCOUNTS.values())
        for address, what in DEMO_ACCOUNTS.values():
            self.stdout.write(f"  {address:<{width}} — {what}")
        self.stdout.write(
            "  Sam Noreach has no login at all — no email, no phone. That is the "
            "person who lands in “cannot be reached” on the notice page.")

    # --- the pieces ------------------------------------------------------

    def dictionaries(self):
        # Not created here: contact/0004_seed_relationship_types owns this row,
        # because EmergencyContact.relationship_type is a required FK and a
        # production database has to come up with somewhere for it to point.
        self.parent_of = RelationshipType.objects.get(code="parent")
        self.full_time, _ = EmploymentType.objects.get_or_create(
            code="full_time", defaults={"name": "Full time"})
        self.part_time, _ = EmploymentType.objects.get_or_create(
            code="part_time", defaults={"name": "Part time"})
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
                "name": "Food Pantry lead", "kind": Position.Kind.STAFF,
                "compensation": Position.Compensation.PAID,
                "ministry": self.pantry, "is_leader": True,
            },
        )
        self.pantry_staff, _ = Position.objects.get_or_create(
            code="pantry_staff",
            defaults={
                "name": "Food Pantry officer", "kind": Position.Kind.STAFF,
                "compensation": Position.Compensation.PAID,
                "ministry": self.pantry, "reports_to": self.pantry_lead,
            },
        )
        # A post nobody holds. Vacancy is a first-class state, and a demo with
        # no vacancy in it cannot show that.
        #
        # ⚠️ Unpaid, and a vacancy still says so. Being able to describe an
        #    empty box — is it budgeted or not — is the reason compensation
        #    hangs off Position and not off Assignment (D11 / D32 section 2).
        Position.objects.get_or_create(
            code="pantry_driver",
            defaults={
                "name": "Driver", "kind": Position.Kind.STAFF,
                "compensation": Position.Compensation.UNPAID,
                "ministry": self.pantry, "reports_to": self.pantry_lead,
            },
        )
        # ⚠️ All three compensation values have to exist in the demo data, held
        #    by people who took part, or a whole acceptance line cannot be
        #    walked in the browser: R8 has to show paid, unpaid and stipend
        #    staff side by side (05-roadmap D1.2). Driver above is unpaid but
        #    deliberately vacant, so the unpaid *person* needs a post of their
        #    own — this is the "ministry whose members are all volunteers but
        #    work like employees" that started D32.
        self.pantry_helper, _ = Position.objects.get_or_create(
            code="pantry_helper",
            defaults={
                "name": "Food Pantry helper", "kind": Position.Kind.STAFF,
                "compensation": Position.Compensation.UNPAID,
                "ministry": self.pantry, "reports_to": self.pantry_lead,
            },
        )
        self.pantry_intern, _ = Position.objects.get_or_create(
            code="pantry_intern",
            defaults={
                "name": "Food Pantry intern", "kind": Position.Kind.STAFF,
                "compensation": Position.Compensation.STIPEND,
                "ministry": self.pantry, "reports_to": self.pantry_lead,
            },
        )

    def account(self, email, last_name, first_name="", **contact_fields):
        """One demo login, keyed by its address — which is its login name (D 2026-08-06).

        ⚠️ Every account therefore **has** to have an address, which is why the
           "cannot be reached" demo person below is a Contact and not an account
           any more. That is not a workaround: somebody with no email and no
           phone did not sign themselves up, so they are exactly the sort of
           person a ministry admin writes down on the day.
        """
        user = get_user_model().objects.filter(email__iexact=email).first()
        if user:
            return user
        return register_account(
            email=email, password=PASSWORD,
            legal_last_name=last_name, legal_first_name=first_name,
            **contact_fields,
        )

    def accounts(self):
        self.boss = self.account(
            demo_login("foundation_admin"), "Boss", "Terry",
            birth_date=datetime.date(1975, 4, 4))
        # Staff, because the acceptance walk reads R1–R3 off the admin
        # changelist. The scoped pages still refuse them — a global group is
        # not a ministry scope, and permissions.py grants no exemptions.
        self.boss.is_staff = True
        self.boss.save(update_fields=["is_staff"])
        self.boss.groups.add(foundation_admin_group())

        self.pantry_admin = self.account(
            demo_login("pantry_admin"), "Zhang", "San",
            birth_date=datetime.date(1982, 6, 1))
        self.tax_admin = self.account(
            demo_login("tax_admin"), "Chen", "Si",
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
            demo_login("participant_adult"), "Li", "Si",
            phone="+14085550101", birth_date=datetime.date(1990, 2, 2))
        self.minor = self.account(
            demo_login("participant_minor"), "Xiao", "Ming",
            birth_date=local_today() - datetime.timedelta(days=365 * 15))
        # Unknown birth date: the cautious branch of the three-state. Signing
        # up asks for consent, and notifications go to the guardian.
        self.unknown = self.account(
            demo_login("participant_unknown"), "Wang", "Unknown",
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
        #
        # ⚠️ A Contact with **no login**, since 2026-08-06 — it used to be an
        #    account called volunteer_silent. An account is now identified by its
        #    email address, so an account with no address cannot exist, and this
        #    person is truer as a Contact anyway: no email and no phone describes
        #    somebody a ministry admin wrote down on the day, not somebody who
        #    filled in a registration form. P6's third group is reached through
        #    Participation → Contact and never through User, so the branch this
        #    person exists to demonstrate is untouched.
        self.silent = Contact.objects.get_or_create(
            legal_last_name="Noreach", legal_first_name="Sam",
            defaults={"contact_type": Contact.ContactType.INDIVIDUAL,
                      "birth_date": datetime.date(1988, 3, 3)},
        )[0]
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
            demo_login("participant_minor2"), "Zhao", "Xiaoyu",
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

        # The two people the axis split was made for: on the books, in post,
        # and not paid the way an employee is. Before D32 neither of them could
        # be filed at all without calling them something they are not.
        self.unpaid_staff = self.account(
            demo_login("staff_unpaid"), "Okafor", "Ada",
            birth_date=datetime.date(1979, 6, 12),
        ).contact
        Assignment.objects.get_or_create(
            contact=self.unpaid_staff, position=self.pantry_helper,
            start_date=local_today() - datetime.timedelta(days=300),
            defaults={"employment_type": self.part_time},
        )
        self.intern = Contact.objects.get_or_create(
            legal_last_name="Silva", legal_first_name="Rafa",
            defaults={"contact_type": Contact.ContactType.INDIVIDUAL,
                      "birth_date": datetime.date(2003, 4, 2)},
        )[0]
        Assignment.objects.get_or_create(
            contact=self.intern, position=self.pantry_intern,
            start_date=local_today() - datetime.timedelta(days=120),
            defaults={"employment_type": self.part_time},
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
        # 🔴 三个角色，现在**各演一种容量**（2026-08-19，`stop_at_needed_count`
        #    落地那天补的）：
        #      lifting      要 2 人、已经 2 人、卡上限   → 满了，报不进
        #      welcome      要 4 人、已经 3 人           → 还有位置
        #      interpreting 要 1 人、不卡上限            → 「多多益善」那一档
        #    没有第三种的话，演示数据里根本走不到满员那条路，而那正是这次要看的。
        lifting = self.role(self.open_event, self.lifting, 2)
        welcome = self.role(self.open_event, self.welcome, 4)
        self.role(self.open_event, self.interpreting, 1,   # nobody signs up
                  stop_at_needed_count=False)

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

        # 3. Full: absent from the list, but whoever signed up
        #    can still open it. That is the difference between "can see" and
        #    "can join", and P6's cancel link depends on it.
        confirmed, made = Event.objects.get_or_create(
            name="English corner (full)",
            defaults={
                "event_type": self.distribution, "ministry": self.pantry,
                "start_time": now + 3 * DAY, "end_time": now + 3 * DAY + 2 * HOUR,
                "owner": self.pantry_admin.contact, "status": Event.Status.FULL,
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
            # Wanted 4: the paper sign-in plus the two unpaid staff below.
            # A finished event showing more signups than it asked for is a
            # different demo story than the one this data is telling.
            past_welcome = self.role(past, self.welcome, 4)
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

            # ⚠️ Unpaid and stipend staff on the same event as the paid ones.
            #    R8 used to answer with the paid two only, and the point of
            #    D1.2 is that the other two now appear beside them — which
            #    cannot be seen on a page unless the demo data has them.
            #
            # ⚠️ And they answered the identity question differently, which is
            #    the whole of D38 on one screen: Ada came on her own time, Rafa
            #    was put on the rota. Same event, same job, two ledgers.
            #    Without both values in the demo, the column on the report page
            #    reads as decoration.
            #
            # ⚠️ The paid two above are deliberately left without an identity,
            #    so the third state — "not recorded", which is what every row
            #    older than D38 looks like — is on the screen as well. A cell
            #    that only ever appears in production is a cell nobody designs.
            for person, served_as in [
                (self.unpaid_staff, Participation.ServedAs.VOLUNTEER),
                (self.intern, Participation.ServedAs.WORK),
            ]:
                row = Participation.objects.create(
                    contact=person, event_role=past_welcome,
                    registered_at=past.start_time)
                set_served_as(
                    row, served_as,
                    declared_by=Participation.DeclaredBy.SELF)
                check_in(row, at=past.start_time)
                check_out(row, at=past.start_time + 3 * HOUR)

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

        self.filler_events(now)

    #: Enough events that the two public lists actually scroll (2026-08-05).
    #:
    #: ⚠️ Created **after** the five above, so their ids stay 1–5. The C0.3
    #:    acceptance checklist names them by id and by status; renumbering them
    #:    would quietly invalidate that whole document.
    #:
    #: ⚠️ These carry no signups, no roles and no hours on purpose. Every one of
    #:    the five above exists to demonstrate one specific rule (a draft nobody
    #:    can see, a role with zero turnout, an employee who has since left);
    #:    padding them with lookalikes makes the list longer and the fixtures
    #:    harder to reason about. These are scenery.
    FILLER_UPCOMING = [
        ("Weekday pantry shift", "pantry", 1, "Church ground floor"),
        ("Clothing drive sorting", "pantry", 2, "Fellowship hall"),
        ("Community breakfast", "pantry", 4, "Kitchen"),
        ("Neighbourhood clean-up", "pantry", 6, ""),
        ("Backpack packing", "pantry", 8, "Fellowship hall"),
        ("Winter coat collection", "pantry", 9, ""),
        ("Tax filing drop-in", "tax", 10, "Room 2B"),
        ("Benefits advice clinic", "tax", 12, "Room 2B"),
        ("Senior lunch service", "pantry", 14, "Kitchen"),
        ("Reading buddies", "pantry", 16, "Library corner"),
        ("Tax help for students", "tax", 18, ""),
        ("Garden working party", "pantry", 21, "Back garden"),
    ]

    FILLER_PAST = [
        ("Autumn food drive", "pantry", 8, "Church ground floor"),
        ("Free tax clinic", "tax", 14, "Room 2B"),
        ("School supplies handout", "pantry", 21, "Fellowship hall"),
        ("Thanksgiving meal prep", "pantry", 35, "Kitchen"),
        ("Winter shelter shift", "pantry", 47, ""),
        ("Spring cleaning day", "pantry", 61, "Back garden"),
        ("Easter hamper packing", "pantry", 88, "Fellowship hall"),
        ("Tax season overflow", "tax", 104, "Room 2B"),
        ("New year store-room sort", "pantry", 132, "Church ground floor"),
    ]

    #: A pool of past volunteers, so the report has something to be a report of
    #: (2026-08-05). Twelve is chosen to be more than the ten "Most hours"
    #: shows — a leaderboard that lists everybody is not a leaderboard.
    FILLER_VOLUNTEERS = [
        ("Alice", "Adams"), ("Ben", "Baker"), ("Cai", "Chen"),
        ("Dolo", "Diallo"), ("Elena", "Esposito"), ("Frank", "Fisher"),
        ("Gloria", "Gomez"), ("Hana", "Haddad"), ("Idris", "Ibrahim"),
        ("Jonas", "Jensen"), ("Kiran", "Kaur"), ("Luz", "Lopez"),
    ]

    def filler_events(self, now):
        """Scenery: enough rows that the lists are worth scrolling."""
        for name, ministry, days, place in self.FILLER_UPCOMING:
            owner = self.pantry_admin if ministry == "pantry" else self.tax_admin
            Event.objects.get_or_create(
                name=name,
                defaults={
                    "event_type": self.distribution,
                    "ministry": self.pantry if ministry == "pantry" else self.tax,
                    "start_time": now + days * DAY,
                    "end_time": now + days * DAY + 3 * HOUR,
                    "location": place, "owner": owner.contact,
                    "status": Event.Status.OPEN,
                },
            )
        past = []
        for name, ministry, days, place in self.FILLER_PAST:
            owner = self.pantry_admin if ministry == "pantry" else self.tax_admin
            event, _ = Event.objects.get_or_create(
                name=name,
                defaults={
                    "event_type": self.distribution,
                    "ministry": self.pantry if ministry == "pantry" else self.tax,
                    "start_time": now - days * DAY,
                    "end_time": now - days * DAY + 3 * HOUR,
                    "location": place, "owner": owner.contact,
                    "status": Event.Status.COMPLETED,
                },
            )
            past.append(event)
        self.filler_turnout(past)

    def filler_turnout(self, events):
        """Roles, signups and hours on the past scenery, so the report reports.

        Without this the panel is honest and useless: nine signups across
        twenty-three events makes every chart fall back to its "fewer than
        three bars" text, and nobody can tell a working page from a broken one.

        ⚠️ Deterministic, never random. The same index arithmetic the scroll
           effect uses, and for the same reason: a demo that looks different
           after every reseed cannot be walked through with somebody, and a
           screenshot of it cannot be compared with anything.

        ⚠️ Spread across two ministries, several months, and a mix of outcomes
           on purpose — full roles and short ones, hours recorded and hours
           missing, one no-show, and people who came back. A demo where every
           figure is 100% exercises none of the branches that matter.
        """
        volunteers = [
            Contact.objects.get_or_create(
                legal_last_name=surname,
                legal_first_name=given,
                defaults={
                    "contact_type": Contact.ContactType.INDIVIDUAL,
                    "birth_date": datetime.date(1970 + index, 3, 12),
                    "email": f"{surname.lower()}@example.invalid",
                },
            )[0]
            for index, (given, surname) in enumerate(self.FILLER_VOLUNTEERS)
        ]

        for position, event in enumerate(events):
            lifting = self.role(event, self.lifting, 4)
            welcome = self.role(event, self.welcome, 2)
            # A rotating window over the pool: consecutive events share most of
            # their people, so "came more than once" is a real majority and the
            # leaderboard has an order rather than a twelve-way tie.
            turnout = 4 + position % 3
            for offset in range(turnout):
                who = volunteers[(position * 2 + offset) % len(volunteers)]
                row = self.signup(who, lifting if offset % 3 else welcome)
                if row.status != Participation.Status.REGISTERED:
                    continue
                if position == 1 and offset == 0:
                    # One recorded absence, so the status is not a dead choice
                    # in the demo and the attendance page shows all three states.
                    mark_absent(row)
                elif offset == turnout - 1 and position % 2 == 0:
                    # Somebody nobody checked out. This is the whole reason the
                    # hours total ships with "from N records" next to it.
                    continue
                else:
                    record_hours(row, Decimal(2 + (offset + position) % 3))

    # --- helpers ---------------------------------------------------------

    def role(self, event, participation_role, needed_count, **fields):
        return EventRole.objects.get_or_create(
            event=event, role=participation_role,
            defaults={"needed_count": needed_count, **fields},
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
