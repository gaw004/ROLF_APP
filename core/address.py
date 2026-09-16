"""地址那几格的**一份**出处：州的名单、邮编的格式、以及去空白。

🔴 **全站有两处存地址，而它们的规矩不一样 —— 这个模块只解决其中一半，
   而另一半为什么没动，写在下面。**

     · `events.Event` / `events.EventSeries`：**没有** `address_country` 列，
       而那是有意的（见 `events/models.py` 那段 🔴）—— 活动是基金会在本地办的
       实体场所，这些字段的唯一去处是「在地图里打开这个地方」，而本地地址的
       地图查询不需要国家。所以它们的州**一定是**美国五十州之一，
       校验得起来，这个模块服务的就是它们。
     · `contact.Contact`：**有** `address_country`（`CountryField`，默认 US）。
       于是它的州是一条**有条件**的规则（US 时是五十州之一，否则是自由文本），
       而那条规则要落在 `Contact.clean()` 上、并且会碰到库里已有的行。
       那是一件独立的事，2026-09-16 这一批**没有做**。

   ⚠️ 如实写在这里而不是留给人去发现：今天 Contact 那一侧的州仍然是自由文本，
      只有管理后台和个人资料页上那段 `address_state_toggle.js` 在**界面上**
      换成下拉 —— 而那是一段 JS，关掉脚本它就是个文本框（D24 说功能不挂在
      脚本上，所以活动这一侧走的是真的 `<select>`，不是那条路）。
      **一条只说一半的注释比没有更糟**，所以这一段把另一半也说了。

⚠️ 放 `core/`：它是底座（同 `core/timeutils.py` / `core/limits.py`），
   所有 app 都 import 得了它，而它不 import 任何 app。
"""

from django import forms
from localflavor.us.forms import USZipCodeField
from localflavor.us.us_states import STATE_CHOICES

#: 五十州（加特区和属地），**从 localflavor 取，不抄**。
#:
#: ⚠️ 抄一份的代价是：localflavor 哪天加一个属地，抄件不会跟着变，而没有人
#:    在看抄件。`contact.forms.us_state_choices_json()` 从 2026-09-16 起也读
#:    这一份 —— 在那之前它自己 import 一遍 `STATE_CHOICES`，两处各读各的。
#:
#: ⚠️ 空那一项的字是 **"State"**，不是「--- 请选择 ---」：一条式的筛选/表单
#:    版式里标签常常是 `sr-only`，而框里那个词是看得见的人唯一读得到的说明。
#:    同 `events.forms.EventPeriodForm` 里 ministry / role 两格的改口。
US_STATE_CHOICES = [("", "State"), *STATE_CHOICES]

#: 「这几格前后的空白要去掉」——`location` 也在里面，虽然它不是地址的一部分。
#:
#: 🔴 **这一条最不起眼也最值钱。** `"NY "` 和 `"NY"` 在地图查询上、在将来任何
#:    一次去重或分组上都是两个值，而**屏幕上一模一样**。手机的自动补全和从别处
#:    粘贴过来的地址都会带着它。
#:    ⚠️ `events.forms.EventPeriodForm.narrow()` 里那句 `.strip()` 记着同一个坑
#:       的另一半：搜索框里一个尾随空格会让 `icontains` 一条都匹不上，而那一格
#:       看起来填得好好的。
TRIMMED_FIELDS = (
    "location",
    "address_street", "address_city", "address_state", "address_postal_code",
)


def state_field(**kwargs):
    """美国州的下拉 —— 一个**真的** `<select>`，不是一段脚本换出来的。

    🔴 **没有 JavaScript 也完整可用**，这是它比 `contact/static/contact/
       address_state_toggle.js` 那条路强的地方，也是活动这一侧不走那条路的
       理由（D24：功能不挂在脚本上）。

    ⚠️ 存下去的仍然是两个字母，列宽 `max_length=100` 一个字不用改 ——
       这不是一次迁移。

    ⚠️ `required=False` 是默认：那四格在模型上全是 `blank=True`，而模型注释
       写明了理由「地址可以晚一点补，必填会让『先建草稿、回头再补细节』这条路
       走不通」。**不许顺手改成必填。**
    """
    kwargs.setdefault("required", False)
    kwargs.setdefault("label", "State")
    return forms.ChoiceField(choices=US_STATE_CHOICES, **kwargs)


def postal_code_field(**kwargs):
    """美国邮编：`12345` 或 `12345-6789`。

    ⚠️ 走 localflavor 的 `USZipCodeField`，不自己写正则 —— 格式是它的事，
       而写在这里就是第二份对「什么是邮编」的定义。

    ⚠️ 同上，`required=False`。
    """
    kwargs.setdefault("required", False)
    kwargs.setdefault("label", "ZIP code")
    return USZipCodeField(**kwargs)


def strip_text(cleaned, names=TRIMMED_FIELDS):
    """把 `names` 里那几格的首尾空白去掉，就地改 `cleaned` 并交回它。

    ⚠️ 只碰字符串：这个字典里还有日期、queryset 和上传的文件。

    ⚠️ 名字不在 `cleaned` 里就跳过 —— 三张发布表单画的字段不完全一样
       （一门课没有 `end_time`），而「这张表单有没有这一格」不是这个函数该
       知道的事。
    """
    for name in names:
        value = cleaned.get(name)
        if isinstance(value, str):
            cleaned[name] = value.strip()
    return cleaned
