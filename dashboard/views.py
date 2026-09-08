"""`/me/` —— 一个人打开这个站，最先该看到的那一屏。

🔴 **这个 app 一张表都没有，而那是它的形状而不是它的欠缺。** 仪表盘是一个
   聚合器：它读 events、notices、org，被谁都不读，所以它排在依赖链末端
   （D17 那条「谁也不许反向 import」）。放进 `core` 就是让依赖链的根去
   import 它自己的三个下游。没有 `models.py` 的 app 长不出表，于是
   「仪表盘不拥有数据」是结构事实。全部理由见 D42。

⚠️ 这一页不推翻 D25。D25 否掉的是「`/` 做成按角色分流的调度页」，而它自己
   写着「那个调度器**有用**，但它不是首页」，并留下判据：「这一页是不是要
   发给一个还没有账号的人看的？是 → 公开。不是 → 要登录。」`/me/` 要登录，
   所以它不是 `/`。`/` 一个字没改，登录后也不跳转。

⚠️ 装配全在 services.py。这里只有一个 `@login_required` 和一次 render ——
   `ViewsAreThinGuardTests` 拦着这个文件里的日期运算和聚合，而它拦的正是
   「一页五处各问一次现在」那种会被抄成好几份的东西。
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .services import dashboard_for


@login_required
def me(request):
    return render(request, "dashboard/me.html", dashboard_for(request.user))
