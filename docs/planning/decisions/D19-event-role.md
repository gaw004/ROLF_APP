# D19 · 活动的工种编制 `EventRole`（2026-07-29）

> 本文件是 `../goal.md` 拆出来的一条决策记录（2026-07-30 拆分，内容一字未改）。
> **`goal.md` 仍是唯一入口**：决策一览表和「去哪找」都在那里，
> 代码注释里写的 `goal.md D19` 指的就是本文件。

**结论：新建 `EventRole(event, role, needed_count)`，`Participation` 改为指向它，
不再直接指向 `Event` 和 `ParticipationRole`。**

```python
EventRole(                              # 这场活动开了哪些工种、各要几人
    event         → Event,              # CASCADE
    role          → ParticipationRole,  # PROTECT，字典表
    needed_count  = PositiveIntegerField(null=True, blank=True),
    notes,
)
Participation(                          # 谁报了哪场活动的哪个工种
    event_role    → EventRole,          # CASCADE —— 没有 event 字段了
    contact       → Contact,            # PROTECT
    status, hours, 签到签退, 同意记录…
)
```

## 为什么不能靠 `Participation` 反推工种（这一条是本决策的全部理由）

原设计里没有这张表，R4「每场 event 有多少工种」只能写成
`COUNT(DISTINCT participation.role)`。这等于说：一个工种如果没人报名，它就不存在。

一场开了 5 个工种、只招到 3 个工种的人的活动，系统会报告「这场活动有 3 个工种」，
**而且不报错**。同理 R5 会漏掉所有零报名的工种 —— 而"哪个工种没人报"恰恰是
P2（征集志愿者）最想看的那个数。

> **这和 [D11 第二次修订](D11-position-and-assignment.md#第二次修订为什么-reports_to-不能指向-assignment)判死刑的是同一个病，
> 一字不改**：*「张三走了、李四还没到位，这个编制客观存在，但系统里没有任何一行代表它。」*
> 那里的解法是把编制（`Position`）从任职（`Assignment`）里拆出来。这里是同一个动作：
> 把「活动开了什么工种」从「谁报了名」里拆出来。

`EventRole` 之于 `Participation`，就是 `Position` 之于 `Assignment`。
这个类比是严格的，不是修辞：

| 编制侧 | 活动侧 | 共同点 |
|---|---|---|
| `Position` 与人无关，空着也存在 | `EventRole` 与报名无关，没人报也存在 | 缺人是一等状态，不是"碰巧查不到" |
| `Position.objects.vacant()` | `EventRole.objects.understaffed()` | 同一套查询形状，[`with_headcounts()`](../phase-b.md#空缺编制这次修订的验收点) 直接照搬 |
| `Assignment(contact, position)` | `Participation(contact, event_role)` | 唯一约束都以 `(格子, 人)` 为轴 —— `Assignment` 还多一列 `start_date`（离开又回来是两段任职），活动侧没有这回事 |
| ~~`Position.headcount`~~ 已推迟 | `EventRole.needed_count` 必须有 | 见下 |

`needed_count` 就是被推迟过的 `Position.headcount`，但这次不能推迟 ——
P2 的原话是"event 会说明需要多少 volunteers"，它是需求本身，不是优化。
（同一个字段在两张表上的优先级不同，因为需求不同；这不矛盾。）

## 为什么 `Participation` 不保留 `event` 字段

保留的话 `participation.event` 和 `participation.event_role.event` 可以指向两场不同的活动，
**而这是跨表条件，`CheckConstraint` 表达不了**（同 [`Assignment.employment_type`](D11-position-and-assignment.md#d11--编制-position-与任职-assignment-分开汇报线挂在编制上) 那条）。
按 D11 那句"不是两处都能记，是只有一处能记"，删掉。查询走 `event_role__event`。

**唯一约束因此从 `(event, contact, role)` 简化成 `(event_role, contact)`** ——
`event_role` 已经蕴含了 event 和 role 两个维度，而且**不再需要 `nulls_distinct=False`**
（两列都非空）。同 `Assignment` 那条"约束越加越长往往是模型没拆干净"，这次又验证了一遍。

## 代价（三条，如实说）

1. **录入多一步** —— 发活动时必须先开工种，才能登记人。这正是 P2 要求的顺序
   （发布时说明每个工种要几人），所以在当前需求下它不是摩擦，是流程。
2. **"没有具体分工"的场景要一行 `code=general` 的工种承载** ——
   `Participation.role` 原本可空，现在不可空了。可接受：多一行字典表数据，
   换掉一个 `nulls_distinct=False`。
3. **从人那头查"参加过哪些活动"多一跳 join**（`participations__event_role__event`）。
   数据量级下无所谓，`select_related("event_role__event", "event_role__role")` 一次带回。

## 2026-08-29 增补：`EventRole` 长出「谁报得上」（L2）

参与者那一轮（[`../participants.md`](../participants.md) 第六节）给这张表加了三列：

```python
EventRole(
    …
    visible_to_outsiders   = BooleanField(default=False),   # 没有在职任职的人
    visible_to_all_staff   = BooleanField(default=False),   # 当天有在职任职的人
    visible_to_ministries  = ManyToManyField(Ministry),     # 指名的几个部门
)
```

`Event` 上有同名的三列（那是 L3「谁看得见这场活动」），两套加起来正是需求 8：
**一次发布，同时招内外** —— 对外的角色所有人看得见，内部的角色只有在编的人看得见，
而它们在同一场活动上。

三件要写下来的事：

1. **在角色这一层，看得见 = 报得上。** 需求 8 原文是 internal roles
   「只会显示给 internal 的人」，所以不是给他的角色**根本不出现**在页面和报名
   下拉框里，不是列出来附一句「你报不上」。判据只有一份实现
   （`AudienceQuerySetMixin.for_audience()`），`Event` 和 `EventRole` 共用；
2. **角色的范围不许超出活动的范围**（`refuse_wider_than_event()`）。
   ⚠️ 它进不了 `CheckConstraint`：字段在两张表上，还多一张多对多。
   照 D14 如实说，`bulk_create` 走得过去；
3. **「外部人员」不是最宽的一档** —— 它只包含没有在职任职的人。最宽的是
   「外部人员 + 全体在编」两个都勾。

### L1（这一次是来给还是来受）为什么**不**落在这张表上

同样是「这个位置是什么」，`nature` 落在字典表 `ParticipationRole` 上而不是这里，
判据是 D10 那一条（「换个人来做这条信息还成立的，属于编制」）：
「ESL 座位」不管哪一场课都是来接受服务的，「搬运」不管哪一场发放日都是来提供的 ——
它属于**工种本身**，不属于某一场活动对它的一次开设。

好处很具体：它不可能在两场活动之间被设成不一致。
而受众相反 —— 同一个工种在这场活动只给在编的人、在另一场对外开放，是完全正常的，
所以受众落在这张表上。**同一个问题问的是不同的东西，答案就落在不同的表上。**

## 这不是被否决过的 `Shift`

`Shift` 的维度是**时间**（上午场 / 下午场），已按"多班次拆成多个 `Event`"否决，
[那条决定不变](../deferred.md#五明确推迟的事)。`EventRole` 的维度是**工种**，两者正交。
行业里 Salesforce V4S 的 Job → Shift → Hours 三层，我们取的是 Job（工种）这一层、
跳过 Shift（班次）这一层 —— 不是少做了一层，是选了需求指向的那一层。
