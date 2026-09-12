"""登录之后 `/` 下半页的那一屏 —— 一个人打开这个站最先该看到的东西。

⚠️ 这份 docstring 2026-09-12 改过一次口：它原来开头写着「`/me/` —— ……」，
   而 D44 之后 `/me/` 只是一条 302，这一屏住在 `/` 上（`front()` 在下面）。

🔴 **这个 app 一张表都没有，而那是它的形状而不是它的欠缺。** 仪表盘是一个
   聚合器：它读 events、notices、org，被谁都不读，所以它排在依赖链末端
   （D17 那条「谁也不许反向 import」）。放进 `core` 就是让依赖链的根去
   import 它自己的三个下游。没有 `models.py` 的 app 长不出表，于是
   「仪表盘不拥有数据」是结构事实。全部理由见 D42。

⚠️ 这一页不推翻 D25。D25 否掉的是「`/` 做成按角色分流的调度页」，而它自己
   写着「那个调度器**有用**，但它不是首页」，并留下判据：「这一页是不是要
   发给一个还没有账号的人看的？是 → 公开。不是 → 要登录。」仪表盘要登录，
   所以它不是 `/` 的**全部** —— 它是 `/` 往下滚之后那一段，而未登录的人
   连渲染都不渲染。`/` 仍然 200、仍然不跳转（D44 第一节逐条对过）。

⚠️ 装配全在 services.py。这里只有一个 `@login_required` 和一次 render ——
   `ViewsAreThinGuardTests` 拦着这个文件里的日期运算和聚合，而它拦的正是
   「一页五处各问一次现在」那种会被抄成好几份的东西。
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from core.models import HomePage

from .services import dashboard_for


@login_required
def me(request):
    """`/me/` —— 现在是一条回 `/` 的跳转，而不是第二张仪表盘（2026-09-11 改）。

    🔴 **「连在一起」的意思是同一页的内容，不是两页之间有个动画。** 这一页原来
       自己渲染一份仪表盘，于是登录之后落在这里的人**往上滚不到 hero** ——
       他看到的是一张没有上半身的页面，而首页那张照片明明就在同一个站里。
       用户 2026-09-11 的原话：「我现在在 home page 完全不能 scroll up 看到
       hero image」。一页只能有一个地址。

    ⚠️ 这条路**留着**而不是删掉：它进过菜单、进过六处登录后跳转，也可能已经在
       谁的书签里。删掉是 404，跳转是「你要的东西在那边」。

    ⚠️ `@login_required` 照旧留着，而且不是摆设：少了它，一个没登录的人点到旧链接
       会被送到公开首页，什么提示都没有 —— 而他要的是自己那一屏。留着它，
       他先去登录，登完再到 `/`。
    """
    return redirect("home")


def front(request):
    """`/` —— 公开的门面页，而登录的人往下滚就是上面那一页（D44）。

    ⚠️ **没有 `@login_required`，而且这是承重的**：这是一个链接发给陌生人也要
       打得开的页面（D25 的判据）。未登录的人拿到的上下文只有 `page` 一项，
       模板里那一整段仪表盘连渲染都不会渲染。

    🔴 **它住在这个 app，而不是 `core`，方向是被 D17 逼出来的。** 这一页要读
       events / notices / org 三家的模型，而 `core` 是依赖链的根 —— D42 第一节
       为此新开了这个 app。所以搬的是 `/` 这一头：末端 import 根，顺向、合法、
       grep 查得到。考虑过让本 app 出一个模板标签、把视图留在 `core`，没有采纳：
       `{% load %}` 在渲染时照样 import，依赖没有消失，只是查不到了 —— 而它失败
       时炸的是全站最公开的那一页，连匿名访客一起炸。完整的账在 D44 第二节。

    ⚠️ 模板仍然是 `core/home.html`。那一页的 hero、经文、顶栏、侧边菜单都还是
       `core` 的东西，本 app 只是把自己那一屏接在它下面。

    ⚠️ `for_request()` 而不是 `load()` 或 `current()`，两条理由都还在
       `core/models.py` 上：`load()` 是 get_or_create，把一次写放在全站最忙的
       读路径上；`current()` 会和 `site_appearance` 那个上下文处理器各查一次。
    """
    # 🔴 **这一页的分叉在这里命名一次，模板不再各判各的**（2026-09-12）。
    #    `deck` 说的是「这一页在 hero 下面还有第二屏」—— 那才是模板真正要问的
    #    问题，「有没有人登录」只是它今天的答案。在此之前
    #    `user.is_authenticated` 在 home.html 里被重新判断了**五次**
    #    （`<head>`、body 的 class、顶栏 fixed/absolute、顶栏 themed、deck 的
    #    include），而其中只有两处有测试盯着。漏掉 body 那一处，整页滚不动；
    #    漏掉 `<head>` 那一处，就是一个能变深色、却没有主题引导脚本的页面 ——
    #    两种都渲染得好好的，也都不会报错。
    #
    # ⚠️ 顶栏和 `<head>` 的 `themed` 也读它，而那不是偷懒：D44 第五节写的正是
    #    「下面那一屏必须跟随深色模式」—— 主题跟不跟随，跟的就是有没有那一屏。
    context = {
        "page": HomePage.for_request(request),
        "deck": request.user.is_authenticated,
    }
    if request.user.is_authenticated:
        context.update(dashboard_for(request.user))
    return render(request, "core/home.html", context)
