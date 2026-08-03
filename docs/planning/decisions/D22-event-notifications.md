# D22 · 活动变更通知：收件人解析是业务逻辑，投递是可替换的适配器（2026-07-29）

> 本文件是 `../goal.md` 拆出来的一条决策记录（2026-07-30 拆分，内容一字未改）。
> **`goal.md` 仍是唯一入口**：决策一览表和「去哪找」都在那里，
> 代码注释里写的 `goal.md D22` 指的就是本文件。

**结论：新建 `EventNotification` 留痕；「该通知谁、用什么地址」全部留在自己的
`services.py`；「把消息发出去」抽成一个只认（地址, 渠道, 内容）的后端接口，
默认实现是 Django 自带的邮件后端，可换成统一通知平台（Novu / Courier / SuprSend）。**

> 需求方 2026-07-29 选定：接入统一通知基础设施。
> 本决策把这个选择做成**可逆**的 —— 换 provider 不动模型、不动业务规则、不动测试。

## 「快速找到」是免费的，新东西是另外三件

```python
Participation.objects.filter(event_role__event=event).exclude(
    status=Participation.Status.CANCELLED
).select_related("contact", "event_role__role")
```

P6 的前半句到此为止。**真正要新建的是下面三件，一件都不能省：**

① 通知名单 ≠ 报名名单 —— 未成年人要通知家长

一个 15 岁的志愿者可能根本没有自己的手机。通向家长的两条线：
`Participation` 的同意字段（P3 收的）和 `EmergencyContact`（B4.2 建的）。

> 2026-07-29 晚更正：原文说的"两条线"，第一条当时是断的。
> 原文写的是"`Participation` 的 `consent_given_by`"，而那个字段**只是一个姓名文本** ——
> 拿它解析不出任何投递地址。`02-roadmap.md` B11 更是直接写着
> "找 `consent_given_by` **对应的联系方式**"，那个东西不存在，照着写会卡住。
> **修法：给同意字段补 `consent_email` / `consent_phone`**（见上面模型表），
> 未成年人报名时至少填一个。
>
> **顺带暴露的一个真代价，要如实记**：`EmergencyContact` 只有 `phone`，没有 email。
> 所以"回落到紧急联系人"这条线**只能走 SMS** —— 而 MVP 的默认后端
> （`ConsoleBackend` / `DjangoEmailBackend`）只有邮件。
> **结论：要么同意时收到邮箱，要么 provider 必须真的开通短信**，
> 否则未成年人这一支在默认配置下等于没有通知能力。这是一个**会出现在
> `unreachable` 分组里的、看得见的**限制，不是静默失败 —— 可以接受，但不能不知道。

> 这是「活动前该拨谁的电话」这条需求第二次出现。 第一次促成了
> `is_minor` 三态 + `EmergencyContact`（见[未成年人要能查出来](../phase-b.md#未成年人要能查出来)），
> 当时写的是"家长通知的完整闭环"—— **而上面那条更正正是在说它当时并不完整**：
> 筛人的那一半（`is_minor` 三态）真的用上了，投递的那一半要靠本节补的
> `consent_email` / `consent_phone` 才成立。**"出事时拨号"和"活动前发通知"
> 当时被当成了一个场景**，前者只需要电话，后者要的是一个能投递的地址。
> **生日未知的按未成年处理**，沿用 B4.5 已经定下的保守侧口径。

② 联系不上的人必须显式列出来，而且必须自己算

有些 `Contact` 是员工代录的，没有 email、只有电话，甚至两样都没有。

> ⚠️ 通知平台答得了「这封信送到了吗」，答不了「这个人根本没有地址」。
> 前者是投递问题（provider 知道），后者是**本系统的数据质量问题**（provider 连这个人存在都不知道）。
> 一个「已通知 27 人」的绿色提示掩盖 3 个联系不上的人，**和把 `is_minor` 的「未知」
> 折叠成「否」是同一种病** —— 静默消失，不报错。
>
> **所以 `unreachable` 这一组必须由 `resolve_recipients()` 自己算出来、自己存下来，
> 不能指望 provider 的回执。**

③ 「通知过了吗」现在答不出来

`Event` 已挂 simple-history，"什么时候从周六改到周日"有据可查。缺的是通知本身。
按 [D15 三条件](D15-relationship-carriers.md#d15--关系用什么载体承载四条判据--选择规则)检验：一场 event 可以有多次通知（**基数破**）、
通知有自己的属性（时间、原因、正文、发给了谁）（**属性破**）→ **必须是表，不能是
`Event` 上的一个 `notified_at` 字段。**

```python
EventNotification(
    event             → Event (CASCADE),
    reason            = time_changed | location_changed | cancelled | other,   # TextChoices
    message           = TextField,          # ⚠️ 快照 —— 之后再改活动，这条记录说过的话不变
    sent_at, sent_by  → User (SET_NULL),
    recipients        = M2M → Participation,   # 覆盖到了谁
    unreachable       = M2M → Participation,   # ⚠️ 联系不上的是谁（快照，不是计数）
    provider_ref      = CharField(blank=True), # provider 那边的批次 id，用来对账
)
```

**两个 M2M 都是快照，不事后重算**，理由和 [`hours` 是权威值](../phase-b.md#签到签退与-hours谁是权威2026-07-29-新增)同一条：
当时联系不上，不等于今天联系不上。 事后给那个人补了电话再去重算，
这条历史记录会变成"当时全都通知到了"—— **那是假的**。
（M2M 的行一旦写下就不会随 `Contact` 的字段变化而改变，这一点和存一个数字同样成立。）

> 2026-07-29 晚改：`unreachable_count`（一个整数）→ `unreachable`（M2M）。
> 原方案只存了一个计数，于是**事后答不出"上次是哪 3 个人没通知到"** ——
> 想知道就只能重算，**而重算正是本条决策自己禁止的事**。
> 本节 ② 的全部要点是"联系不上的人必须显式列出来、不能静默消失"，
> 只存一个数字等于把它降级成"看得见一次、之后再也查不到"，
> **和它自己判过刑的那种失败是同一形状**。
>
> 逐个收件人的**原因**（`Unreachable.why`：没有地址 / 未成年且没有家长联系方式）
> 只在发送前的预览页出现，**不入库** —— 真要存就得上 through 表，
> 而"是哪几个人"已经能靠这两张 M2M 答出来，够了。要升级也只是给 M2M 加 `through`，不改语义。

## 分层：哪一半绝不能外包

> **判据是 [D18](D18-admin-boundary.md#逻辑落点的硬规矩成本为零现在就要守) 那句话换个对象：
> 换一个通知服务商，这段代码要不要跟着搬？要搬，就不该在适配器里。**

```python
# events/services.py —— 业务规则，永久资产，换 provider 一个字不改
def resolve_recipients(event) -> tuple[list[Recipient], list[Unreachable]]:
    """谁该收到通知、用什么地址。

    ⚠️ 这里的规则是本基金会特有的（未成年人通知家长、生日未知按未成年处理、
       优先渠道看 Contact.preferred_communication_method），任何通知平台都不知道它们。
       （2026-07-30 更正字段名：原文写的是 preferred_contact_method，库里没有这个字段。）
    """

def notify_event_change(event, *, reason, message, sent_by) -> EventNotification:
    """编排：解析收件人 → 交给后端投递 → 落一条 EventNotification。"""


# core/notifications/ —— 适配器，可替换，唯一和外部服务打交道的地方
class NotificationBackend(Protocol):
    def send(self, messages: Sequence[Message]) -> list[DeliveryResult]: ...

@dataclass(frozen=True)
class Message:
    to: str          # 一个邮箱 / 一个电话号 / 一个 provider subscriber id
    channel: str     # email | sms
    subject: str
    body: str
```

> **后端只认（地址, 渠道, 内容）三样，不认 `Contact`、不认 `Participation`、
> 不认「未成年人」这个概念。** 这一条是整个设计的关键 ——
> 一旦让后端知道什么是未成年人，换 provider 就要把这条规则重写一遍。

```python
# settings/base.py
NOTIFICATION_BACKEND = "core.notifications.console.ConsoleBackend"     # 开发默认
# 可选：core.notifications.django_email.DjangoEmailBackend            （不依赖任何外部服务）
#      core.notifications.novu.NovuBackend                            （统一通知平台）
```

**测试一律跑 `ConsoleBackend` 或一个 `LocmemBackend`** —— 业务规则的测试
（谁该收到、谁联系不上）**不许**依赖任何网络，也不许因为换 provider 而变红。

## 要如实说的三条代价

1. 和「便宜：无付费 SaaS」这条核心诉求冲突。 Courier / SuprSend 是纯 SaaS；
   Novu 有自托管开源版，能保住这条，但要跑 Redis + MongoDB + 几个服务，
   撞上另一条核心诉求「好维护：一个人能读完全部代码」。**两条里必然要让一条**，
   记在这里免得以后当成疏忽。
2. PII 出境，和 [D3](D03-portable-postgres.md#d3--数据永远是一个标准-pg_dump-能带走的-postgres-库) 有张力。
   通知内容会带未成年人姓名、家长电话、活动地址 —— 走第三方就有一份副本在别人服务器上。
   **缓解（要做，不是可选）：通知正文里不写未成年人姓名，
   只写活动信息 + "您的孩子报名的活动"**，收件地址仍然只在自己库里。
   这样即使换到最激进的 SaaS，泄露面也只有一个邮箱地址加一段活动公告。
3. 重复发送挡不住。 网络失败重试可能发两遍。MVP 阶段**不建队列、不做幂等键** ——
   记为一个已知的不完美（同 `clean()` 挡不住 `bulk_create` 的口径），
   缓解是发送前二次确认页 + `EventNotification` 列表里能看到"5 分钟前刚发过一次"。

## 报名有效性：改了时间，报名照旧

**2026-07-29 定：`Participation` 一个字段不加**，通知正文里写"新时间来不了请点这里取消报名"，
链到 `/me/participations/`。

**为什么不加 `needs_reconfirmation` 这一档**：现有四档
（registered / attended / absent / cancelled）回答的是"**这个人怎么样了**"，
而 `needs_reconfirmation` 回答的是"**这个人和某次改动的关系**" —— 两个维度。
本项目已经为"两个维度挤进一个字段"付过两次代价
（[`is_active` 挨着 `end_date`](../phase-b.md#单一真相任何带日期的表都不加-is_activeassignment-用-status)、
[`Assignment.status`](../phase-b.md#assignmentstatus状态和任期是正交的两个维度2026-07-28-修订)），不付第三次。

**代价（如实说）**：改完时间到大家陆续取消之间，报名数是虚高的，
`understaffed()` 会短暂说"人齐了"。可接受 —— 那个数本来就是参考值不是硬上限。
真需要时的正确形状不是加一档 status，是一张 `ParticipationConfirmation` 表
（一次改动一行），见推迟清单。

---
