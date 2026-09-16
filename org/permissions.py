"""The only place in the project that answers "may they do this, here?".

Every judgement about ministry-scoped authority lives here, and views may only
call it: `if not can_manage_event(request.user, event): raise PermissionDenied`.
core/tests.py greps for MinistryRole.objects appearing anywhere else.

The reason is not tidiness. Scattered permission checks mean one of them
eventually forgets .active(), or forgets ministry__is_active — and a missed
permission check is silent. Nothing raises; somebody simply sees a page they
should not. Compare the org-tree guard, where the failure at least hangs.

⚠️ user.contact is None is a normal state, not an error. MinistryRole hangs off
   Contact while the entry point is a User, and D12/D21 require User.contact to
   stay nullable because a superuser is a technical account matching no real
   person. So:

     - every can_*() returns False for such an account, and raises nothing —
       raising would turn every protected view into a 500 for them;
     - superusers get no exemption. Exempting them would open a hole straight
       through the ministry scoping, which is the entire point of D20. A
       superuser has the admin, which can already do anything.

   The cost, stated rather than hidden: logging in as a superuser and clicking
   these pages gives 403 after 403 and looks broken. The message says so, so
   that the next person edits their account rather than the check.
"""

from django.contrib.auth.models import Group, Permission

from .models import MinistryRole

# The global tier. P5 — "who may appoint a ministry's admin" — has no "of some
# ministry" in it, so it is a plain Django Group and not a MinistryRole.
FOUNDATION_ADMIN_GROUP = "foundation_admin"

# What a 403 on these pages should say. Written once, because the explanation
# is the mitigation: without it the next person removes the check instead of
# fixing the account.
SCOPED_DENIAL = (
    "These pages are granted per ministry. A superuser has no ministry scope by "
    "design — use an account with a MinistryRole (seed_demo creates some)."
)


def _contact_of(user):
    """The Contact behind a login, or None. Never raises."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "contact", None)


def ministry_ids_administered_by(user, on=None) -> set[int]:
    """Which ministries this person administers on `on`. The floor everything stands on.

    Named for what it returns — ids, not objects. Callers wanting objects do
    Ministry.objects.filter(id__in=...) themselves.

    ⚠️ Three filters, none of them optional:
       in_force(on)        — an expired **or just-revoked** grant must stop
                             conferring anything;
       ministry__is_active — authority over a retired ministry is not authority;
       a Contact           — see the module docstring.

    🔴 **`in_force()` 而不是 `active()`（2026-09-15，用户拍板）。**
       `active()` 的 `end_date` 是右闭的 —— 撤销把它填成今天，于是被撤销的人
       **今天剩下的时间里照旧管着这个 ministry**，明天才失效。按钮说「撤销」，
       发生的是「明天起撤销」，而页面上没有任何地方说这件事。
       ⚠️ 这是一个**既有的**行为，不是这一轮引入的 —— 它没有任何测试钉着，
          是 D47 落地时撞上的。整段理由在 `core.querysets.not_revoked_by()`。
       ⚠️ 报表和记录那一侧**照旧走 `active()`**：那一行诚实地写着「有效期到
          今天」，因为那是事实。变的只是权限判断。
    """
    contact = _contact_of(user)
    if contact is None:
        return set()
    return set(
        MinistryRole.objects.in_force(on=on)
        .filter(
            contact=contact,
            role=MinistryRole.Role.ADMIN,
            ministry__is_active=True,
        )
        .values_list("ministry_id", flat=True)
    )


def administers(user, ministry, on=None) -> bool:
    """Does this person administer this one ministry?"""
    if ministry is None:
        return False
    ministry_id = getattr(ministry, "pk", ministry)
    return ministry_id in ministry_ids_administered_by(user, on=on)


#: 🔴 **`administers_one_of()` 和 `holds_grant_on()` 2026-09-16 删掉了（D48）。**
#:
#:    两个都是「同一条规则的集合版」，给管理列表逐行判「这一行能不能改」用的，
#:    唯一的调用方是 `events.views.event_manage_list`。D48 把 foundation tier 的
#:    写权限放开之后，那一页上**每一行都改得动** —— 那个逐行判断只剩一个答案，
#:    连同这两个函数一起清掉了（phase-d 的判据 2：它没有读者）。
#:
#:    ⚠️ 它们解决的问题**没有消失**：一页 50 行要判权限时，逐行 `administers()`
#:       就是 50 次查询。哪天再需要，形状照旧是「调用方先取一次 id 集合，
#:       这里只做集合判断」，而且要**紧挨着** `administers()` / `event_ids_granted_to()`
#:       放 —— 同一条规则的两份实现分开放，是它们走散的开始。
#:    ⚠️ 还有一条当时写下的理由值得留着：把 `event.ministry_id in administered`
#:       直接内联进 `views.py`，等于把这个函数的函数体写在 grep 守卫看不见的
#:       地方（`PermissionGuardTests` 找的是 `MinistryRole.objects`，一个集合
#:       判断它一个字都认不出来）。所以那一天真要回来，是回来**一个函数**，
#:       不是回来一行内联。


def _any_management_tier(user) -> bool:
    """「这个账号是不是某种管理员」—— 一扇门该问的那个宽问题，实现只有一处。

    ⭐ **抽出来是因为它被抄到了第三遍**（2026-09-15，员工名册）。
       `can_reach_notice_manage()` 和 `can_reach_gallery_manage()` 从各自落地起
       就是同一行判断的两份拷贝（连 `or` 两边的顺序都不一样，而那个不同毫无意义）。
       `in_foundation_tier()` 自己的注释早就把理由写好了：「giving each of those
       its own copy of `groups.filter(...)` is how two checks end up disagreeing
       about who is in the tier」。

    ⚠️ **三个 `can_reach_*` 各自保留自己的名字，不许合并成一个。** 它们今天答案
       相同，是三件事碰巧同时成立，不是一条规则 —— 同 `can_publish_notice()` 和
       `can_manage_notice()` 那一对为什么没合并。哪天照片墙收窄了，改的是那一个
       函数的函数体，另外两个一个字不动。

    ⚠️ 私有（前缀下划线）：它不是一个可以拿去守门的问题。门要问的是三个具名函数
       里的一个 —— 一个视图直接问这个原语，等于又把「这一页归谁」写回了视图里。
    """
    return bool(ministry_ids_administered_by(user)) or in_foundation_tier(user)


def can_publish_event(user, ministry) -> bool:
    """P2: publish an event for this ministry, and say how many each role needs.

    ⭐ **foundation tier 也算，替任何一个 ministry**（2026-09-16，用户拍板，D48）。
       在此之前它一个活动都发不了 —— 它持不了 `MinistryRole`，而这是唯一的判据。
    """
    return administers(user, ministry) or in_foundation_tier(user)


def can_reach_publish_page(user) -> bool:
    """能不能打开发布页 —— 替**任意一个** ministry 发得了就算（2026-09-16）。

    ⚠️ 自成一问，同 `can_reach_staff_roster()`：「你还没被授权管任何 ministry」
       和「这一页不是给你的」不能长一个样（D27）。上面那个函数问的是一个具体
       的 ministry，而开页面的那一刻还没有具体的 ministry 可问。

    ⚠️ 它同时决定管理列表上那颗 `Publish a new event` 画不画。少了那一处，
       权限放开了而**没有任何东西指向它** —— `phase-d.md` 第四节点名三次、
       `core/context_processors.py` 开头列了五个的同一种缺口。
    """
    return bool(ministry_ids_administered_by(user)) or in_foundation_tier(user)


def can_manage_series(user, series) -> bool:
    """Edit a repeat rule, open jobs on it, generate and stop its occasions.

    The same question `can_manage_event()` asks, of the row one level up: a
    series belongs to a ministry, and running that ministry is what entitles
    somebody to schedule its evenings.

    ⭐ **foundation tier 也算**（2026-09-16，D48）。在此之前这里只认
       `MinistryRole`，而那条路和发布是同一个洞：放开发布之后，一个 foundation
       admin 发得出一条规则、发完**立刻进不去它自己的详情页** —— 开不了工种、
       排不了场次，也就是说那条规则发出来就是死的。

    ⚠️ **下面那段讲 `view_eventseries` 的话仍然成立，而它说的是另一扇门。**
       Django admin 那一侧照旧只读（那是 `FOUNDATION_ADMIN_PERMISSIONS` 的事）；
       站点这一侧的写权限由这个函数判。两处不冲突，写在一起是因为它们读起来像
       一回事。

    ⚠️ Not the `view_eventseries` grant in FOUNDATION_ADMIN_PERMISSIONS. That
       one is the **admin's** door and is deliberately read-only (D20: building
       a batch is an act on one ministry's events, so it belongs to the
       ministry tier). This is that tier's door, and L5.4's note beside those
       two lines predicted it.
    """
    return administers(user, series.ministry) or in_foundation_tier(user)


def event_ids_granted_to(user, on=None) -> set[int]:
    """Which single events this person was handed, on `on`（D47，2026-09-15）。

    ⚠️ 三个 filter，一个都不能少 —— 逐条对着
       `ministry_ids_administered_by()` 那三条写的，因为它们防的是同一批事：
         active(on)          一条过期的授权必须不再授予任何东西；
         event__ministry__is_active   一个已停用的 ministry 的活动，权限不再成立；
         一个 Contact        账号没有 Contact 是正常状态，见本模块开头。

    ⚠️ 名字说的是它返回什么 —— id，不是对象。要对象的调用方自己
       `Event.objects.filter(id__in=...)`，同 `ministry_ids_administered_by()`。
    """
    contact = _contact_of(user)
    if contact is None:
        return set()
    # 延迟 import：`events.models` import `org.models`，模块级会成环。
    # ⚠️ 这不是把 D17 的依赖方向反过来 —— `org/permissions.py` 是**判断层**，
    #    它按定义要认识每一张带权限的表（它已经认识 `MinistryRole`）。
    #    真正不许反向的是业务逻辑，而那条线在 `org/services.py` 上（见 D39 的
    #    落点改口）。
    from events.models import Event, EventGrant

    return set(
        # ⚠️ `in_force()`，不是 `active()`：撤销当场生效，而不是明天
        #    （`core.querysets._ended_on_or_before()`）。
        EventGrant.objects.in_force(on=on)
        .filter(contact=contact, event__ministry__is_active=True)
        # ⭐ **管到这场活动收尾为止**（2026-09-15，用户拍板）。授权的表单上没有
        #    截止日期那一格，因为一条单场授权的自然寿命就是这场活动本身 ——
        #    而 `Event.Status.COMPLETED` 的标签**正好就是 "Wrapped up"**，
        #    它的含义写在 `Event.Status` 上：出勤记了、工时记了、跟进做完了。
        #
        #    ⚠️ 这不只是省一个表单格子，它是一条真的安全性质：**授权会自己到期**，
        #       于是不会攒下一批永远看得见未成年人紧急联系电话的人。
        #
        #    ⚠️ 排掉的只有 `COMPLETED` 一档，**不含 `CANCELLED`**：一场取消了的
        #       活动正是最需要有人去通知报名者的时候，而那是这条授权的本职。
        #
        #    ⚠️ 收尾之后那一行**不删也不改** —— 「去年三月谁能看这场活动的报名」
        #       仍然答得出来。到期的是权限，不是记录。
        .exclude(event__status=Event.Status.COMPLETED)
        .values_list("event_id", flat=True)
    )


#: 🔴 **`administers_one_of()` 和 `holds_grant_on()` 2026-09-16 删掉了（D48）。**
#:
#:    两个都是「同一条规则的集合版」，给管理列表逐行判「这一行能不能改」用的，
#:    唯一的调用方是 `events.views.event_manage_list`。D48 把 foundation tier 的
#:    写权限放开之后，那一页上**每一行都改得动** —— 那个逐行判断只剩一个答案，
#:    连同这两个函数一起清掉了（phase-d 的判据 2：它没有读者）。
#:
#:    ⚠️ 它们解决的问题**没有消失**：一页 50 行要判权限时，逐行 `administers()`
#:       就是 50 次查询。哪天再需要，形状照旧是「调用方先取一次 id 集合，
#:       这里只做集合判断」，而且要**紧挨着** `administers()` / `event_ids_granted_to()`
#:       放 —— 同一条规则的两份实现分开放，是它们走散的开始。
#:    ⚠️ 还有一条当时写下的理由值得留着：把 `event.ministry_id in administered`
#:       直接内联进 `views.py`，等于把这个函数的函数体写在 grep 守卫看不见的
#:       地方（`PermissionGuardTests` 找的是 `MinistryRole.objects`，一个集合
#:       判断它一个字都认不出来）。所以那一天真要回来，是回来**一个函数**，
#:       不是回来一行内联。


def can_manage_event(user, event) -> bool:
    """Edit it, open roles on it, check people in, notify the people signed up.

    The write side. Sending a notification belongs here rather than with the
    read side: it puts a message in front of everybody who signed up, which is
    not something "may look at the list" should carry.

    ⭐ **两条路，而第二条 2026-09-15 才有**（D47）：这个 ministry 的 admin，
       或者**被指名管理这一场**的人。两者对这一场的权限**完全一致** ——
       用户定的，而「一致」正是它几乎免费的原因：这个函数是全项目唯一的写判断
       （编辑 / 开工种 / 签到 / 出勤 / 记工时 / 群发通知都走它），改这一处，
       那些页面一个字不用动。
       ⚠️ 如果当初那些检查散在各个 view 里，这个功能就是二十处修改 ——
          **而漏掉的那一处是静默的**。这是 D20「判断只有一处」买到的东西。

    ⚠️ **被授权人拿不到的一样**：再把这一场授权给第三个人
       （`can_grant_event_admin`）。⚠️ 2026-09-16 前这里写的是「三样」，
       另外两样是发布新活动和管理系列 —— 那两条现在对 foundation tier 开了，
       但对**被授权人**仍然关着，所以这句话的主语要跟着收窄。

    🔴 **第三条路 2026-09-16 加：foundation tier，对任何一场**（D48，用户拍板）。
       ⚠️ 这**推翻了 2026-08-05 定下、9-03 重申的「它读得了每一场、改不了任何
          一场」** —— 不是绕过它，是明说换掉。触发它的是同一天放开的发布权：
          一个 foundation admin 发得出活动、发完立刻 403，开不了工种、发不了
          通知，于是发出来的是一个谁也报不了名的壳。
       ⚠️ 代价如实记：`can_view_event_records()` 和这个函数从此**对每一类人
          答案相同**（见那个函数自己的注释），而「只读身份」那一档在这个系统里
          不再有人属于。为它写的六处分支和一个测试类跟着这次改动一起清掉了 ——
          留着它们就是留一套描述着一个不存在的区别的代码。
    """
    if event is None:
        return False
    return (administers(user, event.ministry_id)
            or in_foundation_tier(user)
            or event.pk in event_ids_granted_to(user))


def can_grant_event_admin(user, event) -> bool:
    """把**这一场**活动交给别人管。D47。

    🔴 **只读 `MinistryRole`，不看 `EventGrant`** —— 被授权人转授不了。
       同 `can_grant_ministry_admin()` 不看 `MinistryRole` 的理由：一个能自我
       繁殖的权限，没有人数得清最后有多少人能看未成年人的紧急联系人。

    ⚠️ **foundation tier 也不行**，而这是有意的：这一场活动交给谁办，是**这个
       ministry 的事**。D20 的判据（句子里有没有「某个 ministry 的」）在这里
       指向 ministry 那一档 —— 而 foundation tier 本来就读得到这场活动的一切，
       它缺的不是知情权。
       ⚠️ 收**回**授权是另一回事，见 `can_revoke_event_grant()`。
    """
    return event is not None and administers(user, event.ministry_id)


def can_revoke_event_grant(user, event) -> bool:
    """收回**这一场**活动的授权。**比授出去宽。**

    ⭐ 形状和理由都照 `can_publish_notice()` / `can_manage_notice()` 那一对
       （用户 2026-09-15 定的）：**发布窄、收回宽**。
       那一对的原话是「taking down a wrong or harmful notice cannot wait for the
       admin who wrote it to answer the phone」—— 一条不该再有的权限同样等不了。

    ⚠️ 和 `can_grant_event_admin()` **分成两个函数**，即使它们只差一个 `or`：
       两者今天不同，而将来可能各自再动（授权哪天放宽给 foundation tier 的话，
       收回必须仍然更宽）。同那一对当初没有合并的理由。
    """
    return can_grant_event_admin(user, event) or in_foundation_tier(user)


def can_publish_notice(user, ministry) -> bool:
    """Put a notice on the board in this ministry's name.

    Two ways in, and unlike an event's records they are **not** two different
    authorities — both may write:

      · the ministry's own admin, in their own ministry's name;
      · the foundation tier, in any ministry's name.

    ⚠️ The foundation half arrived 2026-09-03, on the foundation's word, and it
       is the restart condition D41's last table wrote down verbatim — "给
       ministry admin 之外的人发布权 / 基金会说了算". That entry also promised
       "改动只在 org.permissions.can_publish_notice 一个函数里", and that turned
       out to be **half true**: the check is one line here, but a check is not a
       door. `NoticeForm` builds its ministry dropdown from
       ministry_ids_administered_by(), so a foundation admin holding no
       MinistryRole would have passed this function and still had an empty
       dropdown — permitted to publish, with nothing to publish for. The other
       half of the change is there, and it is written on that form.

    ⚠️ What did **not** change is the audience axis. A notice is louder than an
       event — it lands on the home page of everybody it is ticked for — and
       there was a real argument (2026-08-31) for reserving the wider ticks to
       this tier. It was not taken then and is not taken now: the same three
       ticks meaning something stricter on a second table is how two checks come
       to disagree about one question. Every audience is still recorded with a
       name against it.
    """
    return administers(user, ministry) or in_foundation_tier(user)


def can_manage_notice(user, notice) -> bool:
    """Edit it, publish it, take it down.

    ⚠️ **Deliberately still its own function, even though it and
       can_publish_notice now admit exactly the same two tiers** (2026-09-03).
       They coincide today for two unrelated reasons: this one has always
       included the foundation tier because taking down a wrong or harmful
       notice cannot wait for the admin who wrote it to answer the phone, and
       that one includes it as of today because the foundation asked to be able
       to write one. Folding either into the other would tie the two together,
       and the next move is likelier to separate them again — if publishing is
       ever narrowed back, removing must stay wide.
    """
    if notice is None:
        return False
    return administers(user, notice.ministry_id) or in_foundation_tier(user)


def can_reach_notice_manage(user) -> bool:
    """May this account open the notice manage page at all, with nothing on it yet?

    ⚠️ Its own question rather than "do they own any notices", because
       "you have not written one yet" and "this page is not for you" must not
       look the same (D27). A ministry admin on their first day gets an empty
       page and a button, not a 403.

    ⚠️ 2026-09-15：函数体换成了 `_any_management_tier()`，**这个问题本身一个字
       没改**。理由是它当时和 `can_reach_gallery_manage()` 是同一行判断的两份
       拷贝，而员工名册会是第三份 —— 见那个原语自己的注释。
    """
    return _any_management_tier(user)


def in_foundation_tier(user) -> bool:
    """Is this account in the foundation-wide group?

    The primitive the global tier is built on. Named for what it reads rather
    than for one thing it permits, because it now answers two questions —
    "may they appoint ministry admins" and "may they read any event's records"
    — and giving each of those its own copy of `groups.filter(...)` is how two
    checks end up disagreeing about who is in the tier.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    # 🔴 **`pk` 那一半不是多余的**（2026-09-16 撞上的）。Django 的
    #    `AbstractBaseUser.is_authenticated` 是一个**硬编码的 True**，所以上面
    #    那一句拦不住一个**没存过**的 `User()` —— 而 `user.groups` 对一个没有
    #    主键的实例直接抛 `ValueError`，也就是一个 500，而不是一句「不是」。
    #    ⚠️ 它的兄弟 `ministry_ids_administered_by()` 早就兜住了同一种输入
    #       （`_contact_of()` 拿不到 Contact 就返回空集）。两个并排的谓词对同一个
    #       输入一个答 False、一个 500，是这个模块最不该有的那种不一致。
    #    ⚠️ 答 False 而不是抛：一个没存过的账号**不是**基金会那一层的人，
    #       这是这个问句唯一诚实的答案，也是安全的那个方向。
    if user.pk is None:
        return False
    return user.groups.filter(name=FOUNDATION_ADMIN_GROUP).exists()


def can_grant_ministry_admin(user) -> bool:
    """P5: appoint somebody as a ministry's admin.

    Reads the global Group and does not look at MinistryRole at all — a
    ministry admin must not be able to recruit their own downline. That is what
    makes this tier "higher", and it is the one thing about P5 worth testing.
    """
    return in_foundation_tier(user)


def can_view_event_records(user, event) -> bool:
    """Read one event's signups, attendance and report.

    🔴 **2026-09-16（D48）起，这个函数和 `can_manage_event()` 对每一类人答案
       相同**，而它仍然存在、仍然被四处调用。说清为什么，因为「两个名字一个
       答案」读起来像是漏删了一个：

         · 它们是**两个问题**（「看得见吗」／「改得动吗」），而这个仓库已经
           付过一次「两个问题共用一个判断」的钱；
         · 成员集合**分开过、而且是往两个方向分的**：8-05 到 9-16 之间
           foundation tier 只在这一边；D47 的被授权人至今只在**另一**边
           （他管得了这一场，却持不了 `MinistryRole`，走不到这个函数的前两项）。
           —— 所以今天相等是一个巧合，不是一条规律。
       ⚠️ 这里**不许**改写成 `return can_manage_event(user, event)`：那会把
          「今天相等」固化成「永远相等」，而下一次分家将无声无息。

    ⚠️ 原来这里写着「**Read only**」和「the foundation tier, who may only
       look」。**那两句 2026-09-16 起是假的**，已经删掉 —— 一条描述着一个不存在
       的保护的注释，是这个仓库反复判刑的那一种。

    ⚠️ Nothing that writes may be gated on this. The attendance page in
       particular is a write page — check-in, check-out, hours — so it asks
       this for GET and `can_manage_event` for POST. Hiding the buttons is
       interface; refusing the POST is the permission, and only the second one
       is a boundary.

    ⚠️ This widens who can see a minor's emergency contact number, because the
       attendance page shows it (that is the number somebody dials when an
       ankle gets twisted). Deliberate, decided 2026-08-05, and written into
       phase-c.md's "who can see a minor's data" table — which is what C3.7
       checks against, so it cannot be widened quietly.
    """
    if event is None:
        return False
    return administers(user, event.ministry_id) or in_foundation_tier(user)


def event_access(user, event) -> tuple[bool, bool]:
    """(may manage it, may read its records) — both answers, one look at the grants.

    ⚠️ Composed here rather than in the caller, and that is the whole reason it
       exists. The two questions share a term (`administers`), and a page that
       needs both was asking each separately — reading the grant table twice
       per render. Spelling `administers(...) or in_foundation_tier(...)` out at
       the call site would fix the query and break the rule this module is for:
       the two ways into an event's records are one policy, judged in one place.

    ⚠️ Order matters for the second element: managing implies reading, so the
       foundation-tier check is only reached by somebody who does not manage
       this ministry. That is the containment can_view_event_records() states,
       not a shortcut on top of it.

    🔴 **2026-09-16（D48）起两个元素永远相等**，而这个函数保持原样，理由逐字
       同 `can_view_event_records()` 那段：它们是两个问题，而成员集合分开过、
       还是往两个方向分的。调用方照旧解成两个名字 —— 把它们合并成一个返回值，
       等于把「今天相等」写成「永远相等」，而下一次分家时每一个调用方都得重新
       想一遍自己当初问的是哪一个。
    """
    manages = can_manage_event(user, event)
    return manages, manages or (event is not None and in_foundation_tier(user))


def can_upload_gallery_photo(user, ministry) -> bool:
    """Put a photo on the Memories wall, attributed to `ministry`.

    `ministry` None means foundation-wide, and **only the foundation tier may
    pass it**. That is D20's test applied literally: "a photo that speaks for
    the whole foundation" contains no "of some ministry", so it belongs to the
    global tier. A ministry admin publishes their ministry's memories; they do
    not publish the foundation's.
    """
    if ministry is None:
        return in_foundation_tier(user)
    return administers(user, ministry) or in_foundation_tier(user)


def can_delete_gallery_photo(user, photo) -> bool:
    """Take a photo back off the wall.

    ⚠️ Deliberately wider than uploading: the foundation tier may delete
       anything, including a ministry's own. Somebody has to be able to take
       down a photo of a child whose family has asked for it to go, at an hour
       when that ministry's admin is not reachable — and that request does not
       wait for a duty roster.

    ⚠️ And deliberately **not** a read check. There is no "may they see it"
       function here because the wall is one page behind @login_required with
       no per-photo visibility; adding a per-photo read rule would be inventing
       a boundary the interface does not have.
    """
    if photo is None:
        return False
    if in_foundation_tier(user):
        return True
    return photo.ministry_id is not None and administers(user, photo.ministry_id)


def can_reach_gallery_manage(user) -> bool:
    """Open the page photos are put up and taken down from at all.

    ⚠️ Coarser than the two above on purpose, and it does not replace either.
       This answers "is this person any kind of photo admin" — the question the
       *page* asks — while `can_upload_gallery_photo` and
       `can_delete_gallery_photo` answer it per photo, which is what the page's
       contents and its buttons are decided by. A gate this wide is the right
       shape for a door and the wrong shape for anything behind it.

    ⚠️ It exists because the batch removal added a **second** URL that has to
       ask it (2026-08-14). One copy of `in_foundation_tier(...) or
       ministry_ids_administered_by(...)` in a view was fine; two copies is a
       permission rule living in views.py, and the second copy is the one that
       gets forgotten when the rule changes.

    ⚠️ 2026-09-15：函数体换成了 `_any_management_tier()`。上面那段话说的是
       「一条规则不许有两份拷贝」，而这个函数自己曾经就是第二份 —— 现在三个
       问题各留各的名字，实现只有一处。
    """
    return _any_management_tier(user)


def can_reach_staff_roster(user) -> bool:
    """May this account open `/org/staff/` at all, with nothing on it yet?

    ⚠️ 自成一问，同 `can_reach_notice_manage()`：「这个 ministry 还没建过岗位」
       和「这一页不归你」不能长一个样（D27）。一个刚上任的 ministry admin 该看到
       一个空名册和一颗「新建岗位」，不是 403。
    """
    return _any_management_tier(user)


def can_manage_staff_roster(user, ministry) -> bool:
    """在这一个 ministry 里建岗位、把人放进去、结束一段任职。

    ⭐ `ministry` 为 `None` 指的是**基金会级的岗位**（`Position.ministry` 可空，
       "Executive Director" 之类），而**只有 foundation tier 过得去** ——
       和 `can_upload_gallery_photo()` 里 `ministry is None` 那一支一个形状、
       一个理由：一个不属于任何 ministry 的东西，按 D20 的判据（句子里有没有
       「某个 ministry 的」）就是全局那一档的事。ministry admin 管自己的部门，
       他不给基金会设岗。

    ⚠️ foundation tier 在**每一个** ministry 里都过得去，同
       `can_publish_notice()`：他要能替一个还没有 admin 的新 ministry 把第一批
       人录进去，否则一个新 ministry 永远没有第一个员工。
    """
    if ministry is None:
        return in_foundation_tier(user)
    return administers(user, ministry) or in_foundation_tier(user)


def can_define_position_terms(user) -> bool:
    """填得了一个岗位的**薪酬档和汇报线**吗（`compensation` / `reports_to`）。

    ⭐ 这是本轮唯一一条比 `can_manage_staff_roster()` 窄的判断，而它窄在两件
       ministry admin 不该说了算的事上：这个岗位拿不拿钱、它挂在组织架构的哪里。
       其余的（名字、说明、是不是组长、谁在这个岗位上）他最清楚，收给上面一档
       会让他做不了自己的事 —— `phase-d.md` 第五节判过这个分法。

    🔴 **模板和表单都只问这一个布尔，别在别处重判。** 表单按它**删掉字段**
       （不是 `disabled`）—— 见 `org.forms.PositionForm`：`disabled` 是展示，
       删字段才挡得住伪造的 POST。

    ⚠️ 它答 False 的时候，建出来的岗位带上 `Position.needs_foundation_review`，
       否则那两列会以默认值的样子躺在那里，看起来像有人填过。落值在
       `org.services.create_position()`。
    """
    return in_foundation_tier(user)


#: What the global tier may do, as app_label.codename. Global permissions are
#: the right shape here precisely because none of these sentences contains "of
#: some ministry" — which is the test for whether something belongs in a Group
#: or in MinistryRole (D20).
FOUNDATION_ADMIN_PERMISSIONS = [
    # P5 itself: appointing and revoking a ministry's admins.
    "org.add_ministryrole",
    "org.change_ministryrole",
    "org.delete_ministryrole",
    "org.view_ministryrole",
    # R1–R3 are read off the event changelist, foundation-wide: how many events
    # ran in a period, whose they were, how long each took.
    "events.view_event",
    "events.view_eventrole",
    "events.view_participation",
    # L5.2's two tables, on the same footing and for the same reason: a run's
    # meetings and its register are part of "what did this ministry run", which
    # is what R1–R3 are read off.
    #
    # 🔴 Registering a model in admin.py is **not** what makes it reachable.
    #    Django hides a model from the admin index entirely when you hold no
    #    permission on it, so a table that is registered but not named here is
    #    invisible to every account except a superuser — and it looks exactly
    #    like a page that was never built. That is how add_ministry went
    #    missing, and it is written a few lines above; L5.2 walked into the same
    #    hole on 2026-09-08 and this is the second half of the fix.
    #
    # ⚠️ View only, deliberately — no add/change, matching the three lines
    #    above. Scheduling a course's meetings is an act on **one ministry's**
    #    event, and by D20's test ("does the sentence contain 'of some
    #    ministry'?") that belongs to the ministry tier, not to a
    #    foundation-wide grant. Its door is the Programmes pages (06-roadmap
    #    L5.8), scoped by MinistryRole, and until those exist the only writer is
    #    a superuser — the same footing event creation is on.
    "events.view_session",
    "events.view_sessionattendance",
    # L5.4's two, on the same footing and for the same reason as the pair above.
    # A repeat rule and the roles it opens are part of "what did this ministry
    # run", which is what R1–R3 are read off — and a batch of twelve events with
    # no visible rule behind them is twelve events nobody can explain.
    #
    # ⚠️ View only, deliberately. Building a batch is an act on **one ministry's**
    #    events, so by D20's test it belongs to the ministry tier rather than to
    #    a foundation-wide grant.
    #
    # ⚠️ Its door **is now built** (L5.8a, 2026-09-10): `/events/series/<pk>/`,
    #    gated on `can_manage_series()`. So the sentence that stood here until
    #    that day — "until those exist the only writer is a superuser" — is no
    #    longer true, and the line below is no longer the reason a ministry
    #    admin cannot build one. It is view-only here because the writing
    #    happens on the site rather than in the admin, which is the opposite
    #    reason and reads the same from a distance.
    #
    # ⚠️ 2026-09-11（L5.8f）之前这里写着「撤销一批和改规则仍然只有超级用户
    #    做得了」。**那句话过期了** —— 两者现在都在系列页上：撤销走
    #    `/events/series/<pk>/stop/`（一张确认屏 + 一次 POST），改规则就在那张
    #    表单上改、保存时拦一屏确认（底下是 `services.split_series()`）。
    #    两条都按 `can_manage_series()` 收给 ministry admin。
    #
    # ⚠️ 2026-09-11（L5.8g）「撤销」和「即日停止」合并成了一颗键，所以上面写的
    #    是 `/stop/` 而不是 `/undo/` —— 后者不存在了。
    #
    # ⚠️ admin 上那三个 action 一个没删：它们仍是超级用户的路，而且能一次处理
    #    多条系列（站点那一侧一次只管一条）。两套门，两拨读者。
    #
    # 🔴 And they are here at all because registering a model in admin.py is not
    #    what makes it reachable: Django hides a model from the admin index
    #    entirely when you hold no permission on it, so a registered-but-ungranted
    #    table is invisible to every account except a superuser and looks exactly
    #    like a page nobody built. That is how add_ministry went missing, and how
    #    L5.2 lost two days on 2026-09-08.
    "events.view_eventseries",
    "events.view_eventseriesrole",
    # D47 的那张表：谁被指名管理某一场活动。
    #
    # ⚠️ **只读**，同上面那几张。而这一档和它在站点上的权限是一致的：
    #    foundation tier **收得回**一条授权（`can_revoke_event_grant()`），
    #    但**授不出去** —— 一场活动交给谁办是那个 ministry 的事。
    #    收回那条路在站点的授权页上，不在这里。
    #
    # 🔴 而它在这张名单上，是因为「在 admin.py 里注册」**不等于**够得着：
    #    没有这条权限，这张表对每一个非超级用户在 admin 首页上整个不出现，
    #    看起来和「这一页没人建」一模一样。上面 add_ministry 和 L5.2 那两段
    #    记的是同样的经过 —— 这是第四次，而这一次是守卫当场拦下的。
    "events.view_eventgrant",
    # Ministries themselves. A production database comes up with none, and
    # nothing else in the interface can create one — so without these the
    # foundation cannot get started at all. Django hides a model from the admin
    # index entirely when you hold no permission on it, which is why this looked
    # like a missing page rather than a missing permission.
    #
    # ⚠️ No delete_ministry, deliberately, and "we are not running this any
    #    more" is is_active=False — the same "an ending is a date, not a
    #    deletion" rule the rest of this project follows.
    #
    # 🔴 The reason written here until 2026-09-08 was **wrong**: it said
    #    deleting a ministry cascades into its events. It does not —
    #    `Event.ministry` is PROTECT, so a ministry that owns events cannot be
    #    deleted at all. What does cascade is the one nobody had written down:
    #    the audience many-to-many. A ministry that owns nothing but is *ticked
    #    into* other ministries' events takes those ticks with it, and an event
    #    left with an empty audience disappears for everybody — the state
    #    refuse_empty_audience() exists to prevent, arriving by a path it does
    #    not watch. Deleting still needs a superuser, so the withholding above
    #    is the protection; what changed here is that it now gives the reason
    #    that is true.
    "org.add_ministry",
    "org.change_ministry",
    "org.view_ministry",
    # The public front page: its picture, its video and the verse over them.
    # ⚠️ Foundation-wide by construction — the sentence "change the face of the
    #    foundation" contains no "of some ministry", which is D20's test for
    #    which tier a permission belongs to. A ministry admin publishes events;
    #    they do not speak for the whole foundation.
    "core.change_homepage",
    "core.view_homepage",
    # The other lookup tables: read-only for now (2026-08-04 拍板). They still
    # have to be filled in before the pilot, and that is done by a staff account
    # in the admin — this tier can see what is there without being able to
    # rename a category out from under existing rows.
    # ⚠️ `events.view_eventtype` sat here until 2026-09-08, three days after
    #    that table was deleted (commit d600bc9, "一处不留"). Nothing reported
    #    it: the loop below skipped the label it could not resolve, and the test
    #    on this list asserts a **subset**, so a name that resolves to nothing
    #    can never fail. That is the third time this file has met "the list is
    #    right, reality is not, and nothing says so" — see the note under
    #    unresolved() for what now says so.
    "events.view_participationrole",
    "org.view_position",
    "org.view_employmenttype",
]


def _named_permissions():
    """Every label in FOUNDATION_ADMIN_PERMISSIONS, resolved — None where it is not.

    One resolution, two readers: the group builder below takes what resolved,
    and unresolved() reports what did not. Written as one function because the
    two used to be one loop with the failures thrown away.
    """
    for label in FOUNDATION_ADMIN_PERMISSIONS:
        app_label, codename = label.split(".")
        yield Permission.objects.filter(
            content_type__app_label=app_label, codename=codename).first()


def unresolved_permissions():
    """The labels naming a permission this database does not have.

    ⚠️ Skipping them is still right — an app that is not installed yet should
       not stop the group being built. What was wrong was skipping them
       **silently**: `events.view_eventtype` outlived its model by three days
       and the only thing that would ever have noticed was somebody reading this
       file. A typo in a codename fails exactly the same way, and grants nothing
       while looking correct in every listing.

    Read by core.management.commands.check_deployment, so a stale name is
    reported where the rest of "is this database ready" is reported.
    """
    return [label for label, found
            in zip(FOUNDATION_ADMIN_PERMISSIONS, _named_permissions())
            if found is None]


def foundation_admin_group() -> Group:
    """The global group, with its permissions. Used by seed_demo and the admin.

    ⚠️ The permissions are attached here rather than clicked on in the admin.
       A group that exists but grants nothing looks right in every listing and
       fails only when somebody tries to use it — and an empty group is
       indistinguishable from a full one until then. Membership of this group
       still confers no ministry scope whatsoever: the scoped pages ask
       MinistryRole, and a foundation admin who is not also a ministry's admin
       gets 403 from them. That is deliberate, not an oversight.
    """
    group, _ = Group.objects.get_or_create(name=FOUNDATION_ADMIN_GROUP)
    wanted = [p for p in _named_permissions() if p is not None]
    # ⚠️ Reconciled every time, not only when the group is new or empty.
    #
    #    The earlier version was `if created or not group.permissions.exists()`,
    #    and it made the list above a **lie on every database that already had
    #    this group** — adding a permission here did nothing at all, silently,
    #    and the only symptom was a page missing from the admin index. That is
    #    exactly how add_ministry went missing: the list was right, the group
    #    was stale, and nothing reported the difference.
    #
    #    Making it authoritative also means a permission ticked by hand in the
    #    admin is removed on the next call. That is intended and is what the
    #    docstring above already promised: this list is where the answer lives.
    group.permissions.set(wanted)
    return group
