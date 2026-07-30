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
| `Assignment(contact, position)` | `Participation(contact, event_role)` | 唯一约束都是 `(格子, 人)` |
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

## 这不是被否决过的 `Shift`

`Shift` 的维度是**时间**（上午场 / 下午场），已按"多班次拆成多个 `Event`"否决，
[那条决定不变](../deferred.md#五明确推迟的事)。`EventRole` 的维度是**工种**，两者正交。
行业里 Salesforce V4S 的 Job → Shift → Hours 三层，我们取的是 Job（工种）这一层、
跳过 Shift（班次）这一层 —— 不是少做了一层，是选了需求指向的那一层。
