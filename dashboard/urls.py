"""⚠️ 挂在 `me/` 下面，而 `accounts.urls` 里的 `me/profile/` 挂得更早。

两者不冲突，但顺序是有意的：`config/urls.py` 里 accounts 在前，所以
`me/profile/` 先被它接走；本 app 只接 `me/` 这一条精确路径。
反过来写也能跑（`path("me/", include(...))` 不会吞掉 `me/profile/`），
但读的人得自己验一遍 —— 而这一条注释比那次验证便宜。
"""

from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.me, name="me"),
]
