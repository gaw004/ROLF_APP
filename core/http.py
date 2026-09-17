"""从一次请求里安全地取一个主键（2026-09-17）。

🔴 **这个模块存在的唯一理由是一个 500。** `get_object_or_404(…, pk=value)` 和
   `filter(pk=value)` 接得住「查不到」，**接不住「这个值根本不是一个主键」**——
   一个非数字的值（或者一个缺席的键，`pk=None`）在字段层就抛 `ValueError`，
   而那是一个没人接管的 500，不是 404。

⚠️ 这一句 2026-09-16 之前在 `events/views.py` 里被手抄了三份，各带一份同样的
   注释，而同一个仓库里还有四处**根本没抄**（撤销一条 ministry 授权、结束一段
   任职、两处签到）。三份手抄外加四处遗漏，正是这个仓库反复在收的那个形状。

⚠️ 放 `core/`：它是底座，所有 app 都 import 得了它，而它不 import 任何 app。
"""

from django.http import Http404


def posted_pk(request, name):
    """`request.POST[name]` 当成主键读，读不出来就 404。

    ⚠️ **404 而不是 400**：这些值全部来自页面上一个隐藏字段或一颗按钮，
       一个读不出来的值和一行不存在的行，对按下它的人是同一件事 ——
       而那一页的契约写着 404。

    ⚠️ 缺席也算：`request.POST.get(name)` 为 `None` 时 `pk=None` 同样是
       `ValueError`。所以这里用 `.get()` 再判，不用 `[]`。
    """
    asked = request.POST.get(name) or ""
    if not asked.isdigit():
        raise Http404(f"{name!r} is not a primary key.")
    return int(asked)
