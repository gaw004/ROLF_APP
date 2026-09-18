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
    AUDIENCE_HEADING,
    Audience,
    refuse_empty_audience,
    refuse_redundant_audience,
)
from org.models import Assignment, Ministry, Position
from org.permissions import can_define_position_terms, ministry_ids_administered_by
from org.services import refuse_a_second_live_tenure, refuse_retiring_a_held_post


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
            # ⚠️ "volunteers" would be the wrong word here and it was the
            #    word until 2026-09-08. participants.md section 5 keeps that
            #    term for group A only — somebody giving their own time — and
            #    this box is about everybody outside the foundation, the people
            #    it serves included. Reading it as "tick this to recruit
            #    volunteers" is exactly the narrowing that section exists to
            #    stop.
            help_text="People outside the foundation and staff alike — the "
                      "same as ticking both boxes below.",
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

    @property
    def audience_heading(self):
        """"Who can see this event" / "Who may sign up for this role"."""
        return AUDIENCE_HEADING[self.AUDIENCE_ON]

    @property
    def audience_heading_before(self):
        """The field the heading sits above — whichever tick comes first.

        ⚠️ Derived, not a constant, because order_fields() above re-seats the
           "Everyone" tick to the top of the group and that tick is absent on a
           read-only form. A hard-coded name would put the heading in the middle
           of its own group on one of those two paths, and it is the kind of
           wrong that only shows up on the screen.
        """
        if self.EVERYONE_FIELD in self.fields:
            return self.EVERYONE_FIELD
        return self.AUDIENCE_GROUP_FIELD if self.AUDIENCE_GROUP_FIELD in self.fields else ""

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


class PositionForm(forms.ModelForm):
    """建 / 改一个岗位。**这个账号填得了哪几格，由权限决定。**

    🔴 **两档的差别是「删字段」，不是 `disabled`**，而这是这张表单唯一一处真正
       承重的设计。`disabled=True` 会让 Django 忽略提交上来的值、改用 initial ——
       对**改**是安全的，对**建**不是：新建时 initial 是字段默认值，而
       `Position.compensation` 的默认值是 `unpaid`，于是伪造一个
       `compensation=paid` 的 POST 不会被拒绝，只是被悄悄换成 `unpaid`——
       看起来像挡住了。删掉字段之后，那个值连进 `cleaned_data` 的机会都没有。

       ⚠️ 同 `events.forms.SignUpForm` 对 `served_as` 的做法（问不到那个人的时候
          整个删掉那一格），理由一字不差。

    ⚠️ `code` **这张表单上永远没有**（2026-09-15 用户拍板）：它建了就改不了，
       而让一个非技术的人敲一个永久性的 slug，敲错之后没有任何一条路能改。
       🔴 **2026-09-16 改口**：这里原来写着「由 `create_position()` 从名字生成，
          理由写在 `_free_position_code()` 上」——**两句都是假的**。
          D46 当天把自动生成整个删掉了（用户的原话：「我不能接受名字改对后
          code 依然错误」），`create_position()` 现在明写着「code 一个字不碰」，
          而 `_free_position_code()` 这个函数从来没有存在过。
          ⚠️ 真实情况：`code` 默认是空的，只有在**真有东西要指着这个岗位**时
             才由 foundation tier 手填 —— D46（可空 + 需要时手填）。

    ⚠️ `is_active` 只在**改**的时候出现：一个刚建出来的岗位当然是存在的，
       而一颗建的时候就能勾掉的「这个岗位已撤销」是一个没有意义的状态。
    """

    #: 🔴 **只有 foundation tier 填得了的那一格。** 名单只在这里写一次 ——
    #: 视图、模板和测试都读它。
    #:
    #: ⭐ **2026-09-15 当天从三格收到一格**，用户改的主意：薪酬档和汇报线**还给
    #:    ministry admin 填**，foundation tier 的角色从「替他填两格」变成
    #:    「核验他填的对不对」。这更好，而且治掉了原方案一个真问题 —— 那两格原来
    #:    留着默认值 `unpaid` / 空，**没有任何人声明过它们**；现在是他声明、
    #:    另一个人签字，每一格都有人负责。核验流程见
    #:    `Position.needs_foundation_review`。
    #:
    #: ⚠️ `kind` **留在这里**，而它不是漏收的那一格：理事席位是基金会的事，
    #:    不是某个部门的（D32：「board seats are the rare, deliberate ones」）。
    #:    对 ministry admin 它恒为 `STAFF` —— 而那不是一个没人声明过的猜测，
    #:    是一句真话：他建的就是本部门的员工岗。
    #:    ⚠️ 顺带挡住一个不显眼的后果：`kind=board` 的人**不算在编**
    #:       （`org.audience.on_the_books_q()` 只认 `STAFF`），选错一格，
    #:       那个人会静默地看不见发给员工的活动。
    FOUNDATION_ONLY_FIELDS = ("kind",)

    class Meta:
        model = Position
        fields = [
            "ministry", "name", "kind", "compensation", "reports_to",
            "is_leader", "description", "is_active",
        ]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        editing = self.instance.pk is not None
        foundation = can_define_position_terms(user)

        if not editing:
            del self.fields["is_active"]

        if not foundation:
            # ⚠️ 算一次，下面两处共用 —— `ministry_ids_administered_by()` 是这个
            #    项目跑得最频繁的一条查询（每一次权限判断都要问它），而这张表单
            #    上要问两遍：ministry 的下拉、以及汇报线的下拉。
            administered = ministry_ids_administered_by(user)
            for name in self.FOUNDATION_ONLY_FIELDS:
                del self.fields[name]
            # ⚠️ 薪酬档和汇报线**不在这里删**（2026-09-15 起）—— 他填得了，
            #    而 foundation tier 事后核验。两格都带上一句说明，因为他是
            #    第一次被要求回答它们。
            self.fields["compensation"].help_text = (
                "Whether this post is paid. The foundation checks this afterwards — "
                "there are no amounts anywhere in this system.")
            self.fields["reports_to"].queryset = Position.objects.filter(
                is_active=True, ministry_id__in=administered
            ).select_related("ministry")
            if editing:
                # ⚠️ 自己不能向自己汇报。约束 `position_reports_to_is_not_self`
                #    拦得住它，下拉里不列只是让人不必撞那堵墙（D14 的分工：
                #    约束强制，界面负责好用）。**环**（A→B→A）仍然由
                #    `Position.clean()` → `creates_a_reporting_cycle()` 判。
                self.fields["reports_to"].queryset = (
                    self.fields["reports_to"].queryset.exclude(pk=self.instance.pk))
            self.fields["reports_to"].help_text = (
                "Who this post answers to. Only posts in your own ministries are "
                "listed — leave it empty if it answers to somebody outside them.")
            # 🔴 **必填，而这一格是一道真的门。** `Position.ministry` 可空，
            #    空的意思是「基金会级岗位」—— 而
            #    `org.permissions.can_manage_staff_roster(user, None)` 说那只有
            #    foundation tier 做得了。不把它设成必填的话，ministry admin 留空
            #    提交就建出了一个他没有权限建的东西，而表单不会有任何意见。
            self.fields["ministry"].required = True
            self.fields["ministry"].empty_label = None
            # 🔴 **模型的 help_text 对他是一句假话**（2026-09-15，在浏览器里看到的）。
            #    那句话写着「Leave empty for foundation-wide posts such as Executive
            #    Director」—— 而上一行刚把这一格设成必填，他留空会被拒绝。
            #    ⚠️ 这一格是 `Position.ministry` 的 help_text，对 foundation tier
            #       完全成立，所以不能去改模型上那句；要改的是**这一档看到的那句**。
            #    ⚠️ curl 抓不到这种东西：HTML 一个字不差，错的是那句话和这张表单的
            #       关系。同 revisions.md 六十五记的那三个 bug。
            self.fields["ministry"].help_text = "Which of your ministries this post belongs to."
            self.fields["ministry"].queryset = Ministry.objects.filter(
                id__in=administered).order_by("name")
        else:
            self.fields["ministry"].queryset = Ministry.objects.filter(
                is_active=True).order_by("name")
            self.fields["ministry"].empty_label = "Foundation-wide (no ministry)"
            # ⚠️ 自己不能向自己汇报。约束（`position_reports_to_is_not_self`）拦得住
            #    这一条，而下拉里根本不列它是让人不必撞那堵墙 —— D14 的分工：
            #    约束强制，界面只负责好用。**环**（A→B→A）仍然由
            #    `Position.clean()` → `creates_a_reporting_cycle()` 判。
            reports_to = Position.objects.filter(is_active=True)
            if editing:
                reports_to = reports_to.exclude(pk=self.instance.pk)
            self.fields["reports_to"].queryset = reports_to.select_related("ministry")

    def clean(self):
        """撤销一个还有人在任的岗位 —— 让 `is_active` 那一格变红。

        ⚠️ 规则本身在 `org.services.refuse_retiring_a_held_post()`，这里只是把它
           请过来。两处一个函数，不是两份判断 —— 那个函数的注释写着为什么它不能
           是一条数据库约束（跨两张表，CheckConstraint 看不见）。
        """
        cleaned = super().clean()
        # ⚠️ `self.instance.pk` 才问：新建的岗位不可能有人在任。
        if self.instance.pk and cleaned.get("is_active") is False:
            refuse_retiring_a_held_post(self.instance)
        return cleaned


class AssignmentForm(forms.ModelForm):
    """把一个人放进一个岗位。**岗位不在表单上 —— 它来自地址栏。**

    ⚠️ `position` 不是一个字段，而这不是省事：这张表单开在
       `/org/staff/positions/<pk>/assign/` 上，岗位由那个 pk 定，而视图已经把那个
       pk 按权限收窄过了（`_scoped_positions()`）。做成一个字段就等于要在表单里
       **再判一次权限** —— 而那一处判断迟早和页面那一处走散（D27 的不变量）。

    ⚠️ 没有 `status` 这一格：新入职的人当然是 `ACTIVE`（字段默认值），而休假/停职
       是**以后**发生的事，属于改而不属于建。
    ⚠️ 也没有 `end_date`：结束一段任职走 `org.services.end_assignment()`，
       它记的是「今天结束」而不是一个手填的日期。
    """

    class Meta:
        model = Assignment
        fields = ["contact", "employment_type", "start_date"]
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, position=None, **kwargs):
        super().__init__(*args, **kwargs)
        if position is not None:
            self.instance.position = position
        self.fields["contact"].queryset = Contact.objects.filter(
            is_active=True, contact_type=Contact.ContactType.INDIVIDUAL
        ).order_by("legal_last_name", "legal_first_name")
        self.fields["contact"].label = "Who"
        self.fields["start_date"].label = "Starting on"

    def clean(self):
        """同一个人在这个岗位上已经有一段没结束的任职 —— 让 `contact` 那一格变红。

        ⚠️ 规则在 `org.services.refuse_a_second_live_tenure()`，这里只是请过来 ——
           同 `PositionForm.clean()`。
        🔴 **那句「它和那条约束管的不是同一件事」2026-09-17 之后反过来了**（D51）：
           它们管的正是同一件事（区间不相交），而这个调用之所以还在，是因为
           **这一条路上数据库那句话到不了** —— 这张表单不含 `end_date`，于是
           `ExclusionConstraint.validate()` 整条跳过。理由写在那个函数上。
        """
        cleaned = super().clean()
        refuse_a_second_live_tenure(
            contact=cleaned.get("contact"), position=self.instance.position_id,
            # ⚠️ 日期一起传：这一条问的是**区间重叠**，不是「今天两条都活着」。
            #    不传的话它只拿默认的「今天起、没有终点」去比，一段补录的历史
            #    任职压在另一段上会被它放过（D51）。
            start_date=cleaned.get("start_date"))
        return cleaned
