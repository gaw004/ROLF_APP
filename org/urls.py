from django.urls import path

from . import views

app_name = "org"

urlpatterns = [
    # C0.2.4 — P5 had no entrance: the grant page needs a pk and nothing linked to it.
    path("ministries/", views.ministry_list, name="ministry_list"),
    path("ministries/<int:pk>/admins/", views.ministry_admin_page, name="ministry_admins"),
    # D1.9 的员工名册。⚠️ `positions/new/` 排在 `positions/<int:pk>/` **前面**是
    # 无所谓的（`<int:pk>` 只吃数字，"new" 撞不上它），但照 events/urls.py 那条
    # 已经写下来的规矩摆：词形的段一律在前，免得下一个人加一个 `<str:…>` 的路由
    # 时以为顺序无关。
    path("staff/", views.staff_roster, name="staff_roster"),
    # ⚠️ 词形的段全部排在 `<int:pk>` 前面 —— 同 events/urls.py 那条已经写下来的
    #    规矩。`positions` 和 `foundation-wide` 都撞不上一个数字 pk，但顺序摆对
    #    了，下一个人加一个 `<str:…>` 的路由时就不会以为顺序无关。
    path("staff/positions/new/", views.position_create, name="position_create"),
    path("staff/positions/<int:pk>/", views.position_detail, name="position_detail"),
    # 基金会级岗位那一页（`Position.ministry` 为空）—— 它没有 pk 可挂，所以是一个
    # 词。只有 foundation tier 到得了，而那不是这里判的：ministry admin 的
    # queryset 里根本没有那些行。
    path("staff/foundation-wide/", views.ministry_roster_page,
         name="foundation_wide_roster"),
    path("staff/<int:pk>/", views.ministry_roster_page, name="ministry_roster"),
]
