"""What the shared navigation is allowed to know about the person reading it.

Every management page in this project was reachable only by typing its URL:
event_create and org:ministry_admins appeared in no template at all, and
event_roles — which links onward to registrations, attendance, the report and
the notice page — was reached only by the one redirect after creating an event.
The pages and their permission checks were all correct; nothing pointed at them.

⚠️ Everything here asks org.permissions and nothing else. Reading MinistryRole
   directly would put a second copy of the scoping rule in the navigation, and
   the copy that drifts is always the one nobody remembers is there — that is
   D20's whole argument, and there is a grep guard enforcing it.

Cheap by construction: ministry_ids_administered_by() is one query returning a
set of ids, and can_grant_ministry_admin() is one group lookup. Both run per
request because base.html draws the nav on every page.
"""

from django.contrib.auth.models import AnonymousUser
from django.urls import reverse

from core.models import HomePage
from org.permissions import (
    can_grant_ministry_admin,
    in_foundation_tier,
    ministry_ids_administered_by,
)
from org.services import positions_awaiting_review_count


def manage_list_name(user, *, foundation=None):
    """管理那一页叫什么 —— **全站唯一一处定义这对词的地方**（2026-09-03）。

    `events/manage/` 的标题、顶栏那一排里它自己那一格、以及 `_event_nav.html`
    的面包屑都读它。面包屑此前读的是**逐场活动**的 `can_manage`（`event_access`
    算的），而列表页读的是页面级那个 —— 一张列表之后这两个不再同步：两顶帽子的
    人在自己的活动上会看到「← Events I Manage」，而那一页的标题写着「All Events」。
    同一个页面两个名字，正是 events.tests 那条「one page should not have two
    names」钉的。

    ⭐ **判据是「这一页列的是不是全部」，不是「你能不能改」**：两顶帽子的人两者
       都为真，而页面上列的确实是全部。

    ⚠️ 匿名也答得了（`in_foundation_tier` 对匿名返回 False）—— 管理页对他是 302
       到登录，但活动详情页那条面包屑**匿名可达**，少了这个值它会渲染成一个空
       字符串的链接，而模板不报错。

    🔴 两个调用方，而它们画的是同一页上的两个东西：
       · 这个模块的 `navigation()` → `manage_list_name`，模板拿去画 `<h1>`；
       · `events.views._sibling_tabs()` → 顶栏那一排里管理页那一格的字。
       写成两份的表现是「这一排写 Events I Manage、而版心标题写 All Events」——
       一个页面两个名字，而 `ManageListHeadTests` 正是为这件事钉着两处必须同词。
       那条守卫留着，这个函数让它守的东西**从结构上就不可能分家**。

    ⚠️ `events` import `core` 是允许的方向（D17）；反过来不行。

    ⚠️ `foundation` 让**已经知道答案的调用方**把它传进来（同
       `services.default_served_as()` 的 `on_the_books`）。`in_foundation_tier()`
       是一次没有缓存的 `groups.filter().exists()`，而 `navigation()` 上面两行
       刚算过它 —— 不给这个口子的话，这个抽取会给**每一个登录后的页面渲染**
       （连同每一个 HTMX 片段）多加一次查询。实测过：普通页面从 2 次
       `auth_group` 涨到 3 次。
    """
    if foundation is None:
        foundation = in_foundation_tier(user)
    return "All Events" if foundation else "Events I Manage"


def _link(label, url_name, icon, query="", badge=None):
    """一个菜单项。

    ⚠️ `badge` 是那一格右端的**计数**（2026-09-15，员工名册那一条）。
       `None` 和 `0` 都不画 —— 一颗写着 0 的徽章说的是「没有事在等你」，
       而那件事的正确说法是**什么都不显示**。同 `_needs_you_band.html` 里
       「空态照画、但不写 0 item」那一条。

    🔴 **`icon` 是必填的位置参数，而那是故意的。** 钉住之后这个菜单收成一条只有
       图标的窄栏（2026-09-14），于是「有没有图标」不再是装饰问题 —— 漏一个就是
       窄栏上一个**看不出是什么的空格**，而页面不报错。做成必填之后，加一条新
       菜单项却忘了配图标的那一刻是一个 `TypeError`，不是一次走查。

    ⚠️ 名字而不是一段 SVG：这里是**数据**，画在 `_menu_icons.html` 上。
       把标记塞进 context processor 的话，改一个图形要动 Python。
    """
    item = {"label": label, "url": reverse(url_name) + query, "icon": icon}
    if badge:
        item["badge"] = badge
    return item


def _menu_for(user, administered, foundation):
    """The site menu, in order, as data. Rendered by _site_menu.html.

    ⚠️ **Built here rather than as branches in the template**, and the reason is
       the entrance animation. Each entry's transition-delay is computed from a
       `--i` the template supplies, and those numbers were written by hand —
       which works exactly as long as the list is flat. With three conditional
       sections it stops working: whichever section is hidden leaves a hole in
       the numbering, one entry waits an extra beat for nothing, and the
       staggered entrance the whole effect exists for goes lumpy. Nothing errors,
       and it is invisible until you watch the right account open the menu.
       Generated here, `forloop.counter0` is the number and there is no hole to
       leave.

    ⚠️ The two admin sections are **labelled by tier**, because one person can
       hold both and the two mean different things: a ministry admin publishes
       and edits their own ministry's events, while the foundation tier appoints
       ministry admins and may read every ministry's records. Somebody who is
       both needs to see which hat each page belongs to.

    🔴 **两个管理页 2026-09-03 从这个菜单里撤走了**（`Events I Manage`、
       `Notices I Publish`、`All Events` 三格）。入口改成每一页标题行右端那颗
       ⋮，加上仪表盘「Needs you」那张卡的出口。

       ⚠️ **这是一次知情的收敛，不是又一次「没有东西指向它」。** 这个模块开头
          列的那五个缺口正是后者，而本次会话刚补过第八次（foundation tier 的
          `Notices I Publish` 从来就不在这个菜单里）。区别在于：那几次是
          **谁都没注意到**，这一次是用户看着代价拍的板。

       ⚠️ `?scope=all` 一并消失。它当初存在，是因为管理页有两种模式而两顶帽子
          的人默认落在窄的那一种；现在那一页一张列表列全部、权限逐行判，
          没有第二种模式可切了（events/views._scoped_events）。

       ⚠️ 重启条件：有人反映找不到管理页。那时该加回来的**不一定**是菜单 ——
          先问是不是那颗 ⋮ 太安静（触屏上它没有任何文字说明，这是已知代价）。

    Headings are entries too, rather than a nested structure: a flat list is what
    lets one loop number every item, which is the whole point (above).
    """
    # ⚠️ "Past Events" left this menu on 2026-08-17 along with the page itself.
    #    Events now starts at today rather than at "not started yet", so the
    #    one entry covers what the two used to; any period at all is on the
    #    management list for the tier that has it.
    #
    # ⚠️ 那句话原来还有半句 ——「志愿者自己结束了的活动**在 My Signups 上**」——
    #    2026-09-14 起不成立了：它们搬去了 `/me/participations/past/`（决定 48）。
    #    这里**不加第七条菜单项**，去那一页的路是 My Signups 顶栏上并排的那一格，
    #    外加它底下那条「9 past signups →」。一个东西一个入口，而这个模块开头
    #    数着的那五个缺口讲的是**没有**入口，不是只有一个。
    if not user.is_authenticated:
        return [
            _link("Events", "events:event_list", "events"),
            _link("Programs", "events:program_list", "programs"),
            _link("Log In", "accounts:login", "login"),
            _link("Register", "accounts:register", "register"),
        ]

    menu = [
        # ⚠️ 第一条，因为它是登录之后的落脚点 —— 别的每一条都答一个他带着来的
        #    问题，只有这一条告诉他「有什么在等你」。
        _link("Home", "home", "home"),
        _link("Events", "events:event_list", "events"),
        # ⭐ 紧跟着 Events，因为它就是 Events 的一半（2026-09-14，决定 45）：
        #    两张列表页互斥，一门课**只**在这一格后面。没有这一条的话，
        #    Programs 那一页只有顶栏那一排进得去，而顶栏要先到得了 /events/ ——
        #    正是这个模块开头列的那五个缺口的形状。
        _link("Programs", "events:program_list", "programs"),
        # ⚠️ Second, above My Signups, and the order is the argument. A notice is
        #    the one thing on this menu somebody might not know they need to
        #    read — everything else answers a question they arrived with. It is
        #    not first because Events is what most people came for.
        _link("Notices", "notices:notice_list", "notices"),
        _link("My Signups", "events:my_participations", "signups"),
        _link("My Profile", "accounts:profile", "profile"),
    ]

    # ⭐ 待核验岗位的计数，**只给 foundation tier**（2026-09-15，用户要的
    #    「一定要让 foundation admin 积极做 review」的两半之一，另一半是仪表盘
    #    那张卡）。ministry admin 看不到这个数：那批待办他做不了。
    #
    # ⚠️ **一次 `COUNT`，落在每一个页面渲染上**（连同每一个 HTMX 片段），
    #    而且只有 foundation tier 付。这是这颗徽章的全部代价，如实写在这里 ——
    #    这个模块开头那段正是在讲「每个请求都跑的东西要说得出自己多贵」。
    #
    # ⚠️ 算在这里而不是在下面两个分支里各算一次：两顶帽子的人走上面那个分支、
    #    只有 foundation tier 的走下面那个，各算一遍就是两处要保持一致的东西。
    awaiting_review = positions_awaiting_review_count() if foundation else None

    if administered:
        menu += [
            {"heading": "Ministry Admin"},
            # ⭐ 员工名册（2026-09-15）。**这一条是这一页唯一的可见入口**，而它
            #    进菜单而不是走标题行那颗 ⋮，理由和 2026-09-03 把三个管理页撤出
            #    菜单的理由是同一条、方向相反：那三页各自都有一个**读页面**可以
            #    挂 ⋮（读公告、读活动），而员工名册没有 —— 组织架构图和 ministry
            #    详情页都还没建（Phase D 的 D1.9 其余几页）。
            #    ⚠️ 那两页真建出来之后，这一条该不该改挂 ⋮，是那时候的问题。
            #
            # ⚠️ 叫 "Staff Roster" 而不是 "Staff"：这个菜单底下已经有一个叫
            #    "Staff" 的小标题（`is_staff`，通往 Django admin 的那一组），
            #    两个都叫 Staff 正是 `ManageListHeadTests` 那条「一个页面两个
            #    名字」守的反面。页面的 <h1> 和 <title> 用的是同一个词。
            _link("Staff Roster", "org:staff_roster", "roster",
                  badge=awaiting_review),
            # ⚠️ The **manage** page, not the wall. The wall's entrance is the
            #    feather (the drifting ones, and the still one in the top bar),
            #    and putting a second door to it in the menu would give away the
            #    one thing that page is built around — you find it by noticing
            #    something. This entry is here for the opposite reason: without
            #    it, the upload page is reachable only by typing its URL, which
            #    is precisely the shape of the five gaps this module exists to
            #    close.
            _link("Memories Photos", "gallery:manage", "photos"),
        ]

    if foundation:
        menu += [{"heading": "Foundation Admin"}]
        # ⚠️ Only when they are not already a ministry admin, so that somebody
        #    holding both tiers gets one entry rather than two identical ones:
        #    it is the same URL either way, and the page itself widens for the
        #    tier. Two entries pointing at one page reads as a bug.
        if not administered:
            menu.append(_link("Memories Photos", "gallery:manage", "photos"))
            # ⚠️ 同上一格：两顶帽子的人只给一条，因为两档进的是同一个 URL，
            #    而那一页自己会按 tier 变宽（foundation tier 多一块待办面板、
            #    多看得见基金会级岗位）。两条指向同一页读起来像 bug。
            menu.append(_link("Staff Roster", "org:staff_roster", "roster",
                               badge=awaiting_review))
        if can_grant_ministry_admin(user):
            menu.append(_link("Ministry Admins", "org:ministry_list", "ministries"))

    if user.is_staff:
        # ⚠️ Its own section, and not folded into either tier above. `is_staff`
        #    is a different axis: it says "may open the Django admin", which is
        #    neither of the two ministry-scoped tiers and is held by neither by
        #    default. Filing it under "Foundation admin" would state something
        #    untrue about who has it.
        # ⚠️ `new_tab` — the only entry in this menu that gets it, and the only
        #    one that leaves this interface. Somebody opens the Django admin to
        #    look something up or fix a row **while** they are in the middle of
        #    whatever brought them here; replacing the tab throws that away and
        #    the way back is the browser's Back button through a page that may
        #    have been a POST. Every other entry is a page of this site, and
        #    opening those in new tabs would just accumulate them.
        menu += [{"heading": "Staff"},
                 {"label": "Admin Site", "url": "/admin/", "new_tab": True,
                  # ⚠️ 这一条是手写的（它不走 `reverse()`），所以 `_link()` 那个
                  #    必填参数管不到它 —— `SiteMenuIconsGuardTests` 兜住的正是它。
                  "icon": "admin"}]

    return menu


def navigation(request):
    """Which management entrances this account should see."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {
            "administered_ministry_ids": set(),
            "can_grant_ministry_admin": False,
            "is_ministry_admin": False,
            "can_see_all_events": False,
            # ⚠️ 匿名分支也要给这个键，理由在 `manage_list_name()` 的 docstring 里。
            "manage_list_name": manage_list_name(AnonymousUser()),
            "site_menu": _menu_for(AnonymousUser(), set(), False),
        }

    administered = ministry_ids_administered_by(user)
    foundation = in_foundation_tier(user)
    return {
        "site_menu": _menu_for(user, administered, foundation),
        # A set of ids, not a queryset: the nav only asks "any?", and handing
        # templates a queryset invites somebody to iterate it into a menu and
        # add a query to every page in the project.
        "administered_ministry_ids": administered,
        "is_ministry_admin": bool(administered),
        # ⚠️ The foundation tier reaches the same page over every ministry, and
        #    without this it had **no link to it at all** — it holds no
        #    MinistryRole, so `is_ministry_admin` is False for it and the nav
        #    drew nothing. The view was already open; nothing pointed at it.
        #    That is the exact shape of the five gaps C0.2 closed, arriving for
        #    the seventh time, and it was caught by looking at a screenshot
        #    rather than by any test.
        "can_see_all_events": bool(administered) or foundation,
        "can_grant_ministry_admin": can_grant_ministry_admin(user),
        # ⭐ 这一页叫什么，只定义在一处 —— 见 `manage_list_name()`，
        #    顶栏那一排里它自己那一格读的是同一个函数。
        # ⚠️ 把上面已经算好的 `foundation` 递进去 —— 见那个函数的最后一条。
        "manage_list_name": manage_list_name(user, foundation=foundation),
    }


def site_appearance(request):
    """The front page's picture and the brand ramp derived from it.

    Both are needed by the **shared shell**, so they are here rather than in
    each view: the top bar is on every page, and in dark mode the background is
    that picture.

    ⚠️ One **read** query per request, on a single row that almost never
       changes. `for_request()` rather than `load()` on purpose: the latter is a
       get_or_create, which puts a write on the read path of every page in the
       site. Two query-count tests caught that within a minute of it landing.

    ⚠️ `for_request()` rather than `current()` as of 2026-08-13, and it is the
       front page that was paying: `dashboard.views.front` needs the same row for the
       verse, so that one page — the busiest public URL in the site — ran this
       SELECT twice. Neither call site could see the other, which is why the
       caching is on the model rather than a note asking people to be careful.

    ⚠️ One query is cheap but not free, and it is the reason `brand_palette` is
       stored on the row rather than computed: quantising a photograph per page
       view would not be.

    ⚠️ Returns the **image** only, never the video. A video behind every page
       means every page decodes video, which on a phone is heat and battery for
       something nobody is looking at. A page whose only hero is a video falls
       back to the plain dark background.
    """
    page = HomePage.for_request(request)
    hero_image = page.hero_image if page.hero_image else None
    return {
        "site_hero_image": hero_image,
        "site_brand_palette": page.brand_palette or None,
        # ⚠️ The same string the front page uses, out of the same property.
        #    Every page crops this one photograph to a different shape, and the
        #    focus is what keeps all of those framings agreeing with each other.
        #    Formatting it separately here is how they would drift apart.
        "site_hero_focus": page.hero_focus,
        # The `<html>` class for every page that carries the shell. Computed
        # here for one reason, and it is written on the two files that used to
        # do it themselves:
        #
        # ⚠️ `has-hero` is what selects the dark glass — every rule for it is
        #    `.dark.has-hero ...`. Without the class the backdrop photograph is
        #    still painted but the 62% black over it is not, so the picture comes
        #    through at nearly full strength and the whole page goes bright and
        #    busy. **It does not error and it does not look like a missing
        #    class.** `wall.html` said exactly that in a comment while holding
        #    the second copy of the condition; it has been hit once already.
        #
        # ⚠️ **Image only, and `/` is the one page where that shows** (noted
        #    2026-09-12, deliberately not "fixed"). Since D44 the signed-in front
        #    page reads this class too, and its own canvas is happy with a video
        #    or with the built-in fallback picture — so a foundation whose hero
        #    is a video gets a front page that plainly has a picture behind it
        #    while `<html>` says it has none. The visible consequence is bounded:
        #    in dark mode the deck's cards come out solid instead of glass. Both
        #    states are legible, and the scrim is painted either way, because it
        #    lives inside `.home-canvas` rather than behind this flag.
        #
        # 🔴 Do not "fix" this by making the class follow `page.hero` instead.
        #    That would make every **inner** page claim `has-hero` on a video-only
        #    site, and those pages deliberately paint no backdrop for a video
        #    (see the note above this function) — the glass would then be
        #    sampling a plain dark background, which is the bug this class was
        #    introduced to stop. The two pages want different questions answered;
        #    one flag cannot answer both.
        "site_root_class": "h-full has-hero" if hero_image else "h-full",
    }
