from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    # B9 — the volunteer's own pages
    # ⚠️ This one now lists **today's events and everything after**, whatever
    #    their status — not just what is open (2026-08-17). `events/past/` was
    #    removed in the same change; nothing replaced the route, so an old link
    #    to it 404s rather than landing on a page that answers a different
    #    question while looking like the one they bookmarked.
    path("events/", views.event_list, name="event_list"),
    # 日程翻页换掉的那一块（2026-08-18）。
    #
    # ⚠️ 它和下面 `new` / `manage` 那几条**不是**同一种情况，别照抄它们的注释：
    #    `<int:pk>` 只吃数字，所以 "schedule" 撞不上它，顺序在这里无所谓。
    #    写在这里只是因为它属于 event_list 那一页。
    path("events/schedule/", views.event_schedule, name="event_schedule"),
    # L5.8b — 课程那一份（2026-09-14，决定 44/45）。⚠️ 它和上面那两条是**同一个
    #    视图**喂了另一种形状，不是第二套页面；分开的只有地址，而地址分开正是
    #    「我们替人预先筛好」这句话的落点。
    #
    # ⚠️ 不在 `events/` 下面：这两页是并排的兄弟（顶栏上那一排也是这么画的），
    #    挂成 `events/programs/` 读起来是「活动的一个子集的一个子页」，而它不是。
    path("programs/", views.program_list, name="program_list"),
    path("programs/schedule/", views.program_schedule, name="program_schedule"),
    path("events/<int:pk>/", views.event_detail, name="event_detail"),
    # 日程上点一张卡时换进面板的那一块（2026-08-18）。⚠️ 同一份模板、同一份
    # 上下文、同一道权限，只是外面少了一层页面 —— 见 views.event_detail_panel。
    path("events/<int:pk>/panel/", views.event_detail_panel, name="event_detail_panel"),
    path("events/<int:pk>/signup/", views.event_signup, name="event_signup"),
    # D47 —— 把这一场活动交给别人管。⚠️ 词形的段在 `<int:pk>` 之后是安全的
    # （`<int:pk>` 只吃数字），而它挂在这里是因为它属于「这一场活动的某一面」，
    # 同 registrations / attendance / report / notify 那一排。
    path("events/<int:pk>/admins/", views.event_admins, name="event_admins"),
    path("me/participations/", views.my_participations, name="my_participations"),
    # L5.8b（2026-09-14，决定 48）。⚠️ 上面那条**保持原样**不改名：它已经进过
    #    六处登录后跳转、站点菜单和别人的书签，而改地址换来的只是对称。
    path("me/participations/past/", views.past_participations,
         name="past_participations"),
    # D28 — the two halves of a scan. `confirm` comes first for the same reason
    # `new` does below: the token pattern matches any string, so the other order
    # would read the word "confirm" as a token and refuse it as expired.
    path("events/checkin/confirm/", views.checkin_confirm, name="checkin_confirm"),
    path("events/checkin/<str:token>/", views.checkin_scan, name="checkin_scan"),
    path(
        "me/participations/<int:pk>/cancel/",
        views.participation_cancel,
        name="participation_cancel",
    ),
    # B10 — the ministry admin's pages. `new` before `<int:pk>` is not an
    # accident: the other order would try to read "new" as a primary key.
    # C0.2.4 — the entrance to everything below. `manage` before `<int:pk>`.
    path("events/manage/", views.event_manage_list, name="event_manage_list"),
    # D27 — the full report, same filters, nothing truncated. `manage/report`
    # before `manage/<int:pk>` would matter if the latter existed; it does not,
    # and this comment is here so that adding it does not break this route.
    path("events/manage/report/", views.ministry_report_page, name="ministry_report"),
    path("events/new/", views.event_create, name="event_create"),
    # L5.8a — the publisher's door onto a repeat rule. ⚠️ Every word-shaped
    # segment goes before `<int:pk>`, the same rule `new` and `manage` follow
    # above: the other order reads "preview" as a primary key and 404s.
    #
    # ⚠️ `publish/when/` is not under `series/` on purpose — it serves the
    #    publish page whichever of the three shapes is selected, including the
    #    two that are not series at all.
    path("events/publish/when/", views.publish_when, name="publish_when"),
    path("events/series/preview/", views.series_preview, name="series_preview"),
    # ⚠️ 兄弟路由，D49。两条各自一个视图，因为两边的规则**存不存**不一样
    #    （系列存进一列、课用一次就扔），而那是判这条规则时唯一的差别。
    path("events/programs/preview/", views.program_preview,
         name="program_preview"),
    path("events/series/roles/<int:pk>/delete/",
         views.series_role_delete, name="series_role_delete"),
    path("events/series/<int:pk>/", views.series_detail, name="series_detail"),
    path("events/series/<int:pk>/roles/", views.series_roles, name="series_roles"),
    path("events/series/<int:pk>/generate/",
         views.series_generate, name="series_generate"),
    # ⚠️ 两趟（GET 看确认屏、POST 执行），所以它是一条路由而不是系列页上的一个
    #    POST：那一屏要说清楚会发生什么，而一屏是要有地址的。
    #    ⚠️ 这里原来还有一条 `/undo/`，2026-09-11（L5.8g）合并掉了。
    path("events/series/<int:pk>/stop/", views.series_stop, name="series_stop"),
    # C0.2.2 — the only way to move an event, and the only way to mark one
    # completed. Its absence is what left services.reschedule() unreachable.
    path("events/<int:pk>/edit/", views.event_update, name="event_update"),
    path("events/<int:pk>/roles/", views.event_roles, name="event_roles"),
    path("events/roles/<int:pk>/delete/", views.role_delete, name="role_delete"),
    # D38 — the only way to correct somebody's identity, and it has to exist:
    # the two columns are readonly in the admin, so without this route nothing
    # anywhere can change one. That is the failure this project has shipped
    # three times — a service with no door — so the route and the service land
    # together.
    path(
        "events/<int:pk>/registrations/",
        views.event_registrations,
        name="event_registrations",
    ),
    # ⚠️ 只对课有意义，而视图对一场单场活动答 404 —— 这个地址在那里
    #    **不存在**，不是「存在但不给你」。D49。
    path("events/<int:pk>/meetings/", views.event_meetings,
         name="event_meetings"),
    path("events/<int:pk>/attendance/", views.event_attendance, name="event_attendance"),
    # D28 — the iPad page and the endpoint that feeds it. ⚠️ The token endpoint
    # is gated on can_manage_event; without that check the whole rotating-code
    # scheme is decoration, because any volunteer could fetch a live code.
    path("events/<int:pk>/checkin-qr/", views.checkin_display, name="checkin_display"),
    # ⚠️ A run's screen is one meeting's, not the term's — see
    #    views.session_checkin_display for why "which meeting" must not be
    #    guessed from the clock.
    path("events/sessions/<int:pk>/checkin-qr/",
         views.session_checkin_display, name="session_checkin_display"),
    path("events/sessions/<int:pk>/checkin-qr/token/",
         views.session_checkin_token, name="session_checkin_token"),
    path(
        "events/<int:pk>/checkin-qr/token/",
        views.checkin_token,
        name="checkin_token",
    ),
    # 「加进我的日历」（2026-09-14）。⚠️ 走 `.ics` 结尾而不是 `/calendar/`：
    #    有的客户端（尤其是手机上从短信里点开的那种）按后缀认文件类型，
    #    而 `Content-Type` 对头都不一定读得到。
    path("events/<int:pk>/calendar.ics", views.event_calendar,
         name="event_calendar"),
    # ⚠️ 单独一讲（用户 2026-09-14）。整期那一条在上面 —— 一门课两种下法都要有，
    #    而它们是两个地址，因为它们答的是两个问题。
    path("events/sessions/<int:pk>/calendar.ics", views.session_calendar,
         name="session_calendar"),

    # 订阅源（2026-09-14）。
    #
    # ⚠️ `<slug:token>` 而不是 `<str:token>`：slug 的字符集 `[-a-zA-Z0-9_]+`
    #    **正好**是 `secrets.token_urlsafe()` 的产物，而 `str` 是 `[^/]+` ——
    #    后者会把 `.ics` 也吞进去再靠回溯吐出来。能用，但它默许了一个带点的
    #    令牌，而我们从不签发那种。
    #
    # ⚠️ 不在 `events/` 前缀下面：它不是某一场活动的东西，是**这个人的**。
    path("calendar/<slug:token>.ics", views.calendar_feed,
         name="calendar_feed"),
    # 发一把钥匙 / 换一把钥匙。⚠️ 两条都在 `me/` 下面，因为它们是**这个人的**
    #    设置，不是某一场活动的东西 —— 同 `me/participations/`。
    path("me/calendar/create/", views.calendar_feed_create,
         name="calendar_feed_create"),
    path("me/calendar/reset/", views.calendar_feed_reset,
         name="calendar_feed_reset"),
    path("events/<int:pk>/report/", views.event_report, name="event_report"),
    # B11 — P6. Same permission as attendance: sending is a write.
    path("events/<int:pk>/notify/", views.event_notify, name="event_notify"),
]
