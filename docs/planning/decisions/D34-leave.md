# D34 · 请假独立成表，理由不用自由文本（2026-08-14）

结论：`Leave(assignment, leave_type, start_date, end_date, note)` 独立成表，
**请假是权威、班次状态是派生的**。理由走 `LeaveType` 字典表，
⚠️ **不用自由文本** —— 那一列会装进健康和家庭状况。

> 2026-08-14 从 [D32](D32-worker-axes-schedule-and-assignment.md) 第五节拆出来独立成条。
> 第三节（`LeaveType` 取代自由文本）和第四节（权限）是拆分时新增的。

## ⭐ 唯一的不变量：一次请假是一行，班次状态跟着它走

**不许用 N 行班次表达一次请假。** 专业系统在这件事上是收敛的 ——
没有一个这么做：

| 系统 | 做法 |
|---|---|
| Workday | Absence / Time Off 是独立域，和排班分开 |
| BambooHR | Time Off Request + 政策 + 余额 |
| ERPNext | `Leave Application` + `Leave Type` + `Leave Allocation`，**由请假去标记考勤记录** |
| Deputy / When I Work | 请假申请**阻止**排班 |

四条理由：

1. 一次请假是一个**可审批的单元** —— 有起止、类型、状态。摊成 10 行班次之后，"这一件事"就不存在了；
2. 假期**跨非工作日** —— 两周假包含周末，而周末在班次表里没有行；
3. **余额 / 累计**需要一个独立实体挂（本轮不做，但形状要留得住）；
4. 请假对**人**成立，而有些人根本没有班次（exempt 的牧职人员、只来开会的无薪成员）。

⚠️ 选了稠密的 [`Shift`](D33-work-schedule.md) 之后这条更硬：
两周假手工去翻 10 行班次的状态，**没有人会真的执行**。
同 [D27](D27-ministry-report.md) 里「让 admin 点四十次签到不会被真的执行」，
后果也一样 —— 数据看起来在，但跟着"哪个 admin 比较负责"走。

## 一、落地边界

对齐 ERPNext 那条「请假是权威、班次状态派生」：

```
现在做：  Leave(assignment → PROTECT, leave_type → PROTECT,
                start_date, end_date, note="")
          + end_date >= start_date 约束 + simple-history
          保存时把区间内 status=scheduled 的 Shift 翻成 on_leave

推迟：    余额 / 累计 / 审批工作流（形状照 ERPNext：LeaveAllocation）
```

`services.apply_leave(leave)` **只翻 `scheduled` 的**：

- 已经 `worked` 的不动 —— 那是既成事实；
- 已经 `absent` 的不动 —— 那是另一个人记下的判断。

⚠️ **反过来那一半在 [D33 第四节 ①](D33-work-schedule.md)**：生成器碰到已有的 `Leave`，
直接生成 `status=on_leave` 的行，而不是生成 `scheduled` 再等人来翻。
**两条要一起读**，任一条单独成立都不够 ——
一条管"假录在班次之后"，一条管"假录在班次之前"，
而它们必须得出**同一种形状的行**，否则「请假」那个数会跟着录入时机变。

### ⚠️ `Assignment.status` 的 `on_leave` 档退休

D32 原来打算把它「保留为当前值缓存」。**那是错的，这里改掉**：
本条落地之后没有任何代码会写它 —— `apply_leave()` 只翻 `Shift`。
于是「他这两周在不在」有两个答案：`Leave` 表（权威、有人维护）
和 `Assignment.status`（手工、没人维护），
而 `AssignmentQuerySet.serving()` 读的正是后者 —— **值班名单会说他在岗**。

一个没有人写的缓存，就是这个项目已经判过两次的那个病。处置：

```python
# choices 去掉 ON_LEAVE，迁移把现存的 on_leave 行映射回 active
AssignmentQuerySet.serving(on)   # = active(on) AND status=ACTIVE
                                 #   AND 没有覆盖 on 的 Leave
```

`SUSPENDED` 留着 —— 停职不是请假，它没有 `Leave` 行，
而且它本来就是"人手工设置、人手工解除"的东西。

## 二、销假 / 改期：翻回去只翻自己翻过的

删除或缩短一次 `Leave` 时，把区间内 `status=on_leave` 的班次翻回 `scheduled`。
⚠️ **只翻 `on_leave` 的** —— 期间被人手工改成 `worked` 的不动
（有人请了假又来上班，那是真事）。

同 [D33](D33-work-schedule.md) ⭐ 那条的精神：系统造的状态系统可以收，人造的状态只有人能收。

## 三、理由用 `LeaveType` 字典表，不用自由文本（拆分时改）

D32 原文写的是 `Leave(assignment, start_date, end_date, reason)`，`reason` 是自由文本。
**改掉**，三条理由，每一条单独都够：

1. ⚠️ **那一列会装进最敏感的个人信息** —— 病假写病名、丧假写谁去世了、
   照顾家人写家里出了什么事。而 [D18](D18-admin-boundary.md) 的判据说得很清楚：
   Django 没有字段级权限，敏感字段只能靠边界隔离。一个所有 ministry admin
   都能翻的自由文本框，是这个系统里权限最松的敏感数据；
2. **自由文本答不出任何问题。** 「今年病假一共多少天」在自由文本上是个正则问题；
   在字典表上是一个 `group by`；
3. **ERPNext 的对照行里本来就写着 `Leave Type`** —— 抄结构不抄代码，
   这一格当初漏抄了。

形状照 [D5 通则](D05-lookup-tables-not-enums.md)：

```
LeaveType(code, name, is_active)
          种子：annual（年假）· sick（病假）· personal（事假）
                · bereavement（丧假）· unpaid（无薪假）
Leave.note   可选、短、**页面上明写"不需要填写医疗细节"**
```

⚠️ **没有 `is_sensitive` 那一列**（初版有）：说不出谁读它 ——
第四节把**整个类型**对 ministry admin 隐藏了，
一列"这一档特别敏感"因此没有任何判断会去问它。
它看起来很负责任，实际上是一个会让人以为"没标敏感的就可以给人看"的字段。

`note` 仍然留着（"换班给张三了"这种协调信息要有地方写），
但它从"理由"降级成"备注"，措辞上主动引导人**不要**往里写敏感内容 ——
这比事后做权限便宜得多。

## 四、权限：三档，写在 `org/permissions.py`（拆分时补）

D32 原文一个字没写权限。而请假是本轮引入的最敏感的一块数据：

| 谁 | 看得到 |
|---|---|
| 本人 | 自己的全部：类型、日期、备注 |
| 该 ministry 的 admin | **日期 + 「请假」这个事实**；类型和备注**看不到** |
| foundation tier | 全部 |

⚠️ 中间那一行是关键：ministry admin 要排班，所以必须知道「他这两周不在」;
但他不需要知道**为什么**。这正是 [D18](D18-admin-boundary.md)
拆 `BackgroundCheck` 时用的同一条判据的一次轻量版应用 ——
这里不必拆表，因为字段级的隐藏在同一个 model 上做得到（服务层不返回、模板拿不到）。

### ⭐ 于是**录入的人必须是本人**，否则上面那张表执行不了

这一条不是权限设计，是流程设计，但它决定上面那张表是不是一句空话：

> **录入的人必须选类型。** 如果录入口在 ministry admin 那里
> （`/org/leave/new/`），他在建这一行的时候就已经看见类型了 ——
> 之后再"看不到"，只是把他刚刚亲手输进去的东西藏起来。

所以请假的录入口是 **`/me/leave/new/`，本人提交**，
ministry admin 只在班表和名册上看到日期和「请假」这个事实。

三条附带的好处，每一条单独都不够，合起来是决定性的：

1. 它顺手解决了代价 3 的观感 —— 没有审批流的时候，
   "谁能建这一行 = 谁能批假"，而**由本人建、机构看得见**
   比"由主管替他建、主管自己也看得见"诚实得多；
2. 和 [D38](D38-served-as-volunteer-or-work.md)「说这句话的人首先是当事人自己」同一条精神，
   本轮两处自助写入用同一个形状；
3. 请假录入本来就是自助侧最典型的一件事，专业系统（BambooHR / Workday / Deputy）
   **没有一个**是让主管替员工建假条的。

⚠️ 代价：**没有账号的在编人员请不了假**（无薪成员里会有几个）。
处置是 foundation tier 可以代录一行，**而不是**把录入口开回给 ministry admin ——
foundation tier 本来就看得到全部，代录不多泄漏任何东西。

⚠️ 而这也意味着 [`../goal.md`](../goal.md) 待定表第 4 条那句
「`MinistryRole` 先只做 admin 一档」在本轮**不再够用了** ——
那个结论是在「只有活动管理」的世界里下的。处置见 [`../phase-d.md`](../phase-d.md)
的「权限第一次吃紧」那一节：不加档位，
把请假的类型和备注划成 foundation tier only —— **收窄的只有这一样**
（`compensation` / `fte` 这些，本部门的 admin 该看得见，专业系统也是这么分的）。

## 五、代价（如实记）

1. **`Leave` 挂 `assignment` 而不是 `contact`**（沿用推迟清单里已定的形状），
   所以**一个人两个岗位休同一次假 = 两行**。小基金会里罕见，
   记下来免得以后当 bug 讨论；
2. **没有余额，所以答不出"他还剩几天假"** —— 但答得出"他今年请了几天"
   （一个 `Sum`，本轮就做，零新结构）。⚠️ 别把后者当成前者用；
3. **没有审批流**，所以 `Leave` 存在即生效。谁能建这一行 = 谁能批假 ——
   而第四节把这一行定成了**本人**，所以准确的说法是
   「本人录假即生效，机构事后看得见」。这件事必须在演示时对基金会说清楚，
   ⚠️ 而它是本轮和专业系统（一律 request → approve）**唯一一处结构性的偏离**；
4. **没有半天假、没有余额**，见第六节。

## 六、推迟的，以及各自的重启条件

| 事情 | 何时重新考虑 |
|---|---|
| 余额 / 累计 / 额度 | 真的要回答"他还剩几天假"时。形状照 ERPNext：`LeaveAllocation` 挂 `contact` + `LeaveType` + 年度 |
| 审批工作流 | 出现"有人自己给自己批了两周假"的实际问题时 |
| 半天假 | 有人请半天时。现在只有整日，`start_date` / `end_date` 都是 `DateField` |
| 按 `contact` 而不是 `assignment` 挂 | 出现"一个人两个岗位、每次请假要录两遍"的真实抱怨时（代价 1） |
