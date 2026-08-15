# Phase D · 员工与排班 —— 实施步骤

> **决策全文**在 [`decisions/D32-worker-axes-schedule-and-assignment.md`](decisions/D32-worker-axes-schedule-and-assignment.md)。
> 这一份只讲照着做的顺序，不重复「为什么」—— 每一步后面标的是 D32 里对应的那一节。
>
> ⚠️ **Phase 编号动过**：原来的 Phase D 是资金追踪，现在**顺延成 Phase E**。
> 理由和 2026-07-29 那次 C / D 对调一样 —— 排序跟着"哪件事先做"走，不跟着"当初写在哪"走。
> 改动记在 [`goal.md`](goal.md) 的进度表和 [`progress.md`](progress.md) 里，没有一处是悄悄改的。

## 动手前先读这三条

1. **[D32 的唯一不变量](decisions/D32-worker-axes-schedule-and-assignment.md)**：
   一个人在基金会里只有一条在编路径（`Position` + `Assignment`）。
   本轮任何一步如果开始出现"给这类人单独建一张表"的念头，先回去读那一节；
2. **两处会静默改变结果**：`kind` 的取值迁移、R8 的名单。它们各自单独一步，各配一条测试；
3. **[D18 的落点规矩](decisions/D18-admin-boundary.md)** 照旧：
   逻辑进 `services.py`，权限只在 `org/permissions.py`，统计在 queryset，视图是薄壳。
   本轮新增四条守卫，见[守卫测试](#本轮新增的守卫测试)。

## 交付节奏：三批，每批结束都是可演示的

按 [`goal.md` 的交付策略](goal.md#交付策略)（「每个阶段结束都必须是可演示的状态，永远不憋大版本」）。

| 批次 | 装什么 | 为什么是这个顺序 |
|---|---|---|
| **D1** 结构与页面 | 拆轴迁移 + 补齐的 HRIS 字段 + 四个页面 | 几乎没有新结构，一交付基金会就能看到东西，**而他们的反馈会直接改善 D2 的班表设计** |
| **D2** 班表与请假 | `WorkPattern` + `Shift` + 生成 cron + `Leave` + 例会出勤 + 唯一口径 + 班表页 | 本轮最重的一批，也是唯一有 cron 的一批 |
| **D3** 指派与接受/拒绝 | `Participation` 两档 + 两条写入路径 + 统计口径 + D27 报表加块 | 修好 R8。放最后是因为它依赖 D1 的 `kind=staff` 口径和 D2.8 那个口径函数 |

---

# D1 · 结构与页面

## D1.1 拆轴：`kind` 收窄 + `compensation`

**一次迁移，三件事**，必须在同一个 migration 里：

```python
Position.Kind:  STAFF = "staff" · BOARD = "board"        # EMPLOYEE / VOLUNTEER 去掉
Position.compensation = paid | unpaid | stipend          # 新字段，TextChoices（D5 判定规则：有 branch）
```

数据迁移（`RunPython`，带反向函数）：

| 旧 | 新 |
|---|---|
| `kind=employee` | `kind=staff`, `compensation=paid` |
| `kind=volunteer` | `kind=staff`, `compensation=unpaid` |
| `kind=board` | `kind=board`, `compensation=unpaid` |

⚠️ **反向函数写不全，要写清楚它写不全**：`staff` + `unpaid` 回不去
`volunteer` 还是回不去 `employee`—— 信息在正向是增加的。反向函数里
`compensation` 一律映射回 `volunteer`，并在函数的 docstring 里注明这是有损的。

跟着改的三处，**一个都不能漏**（漏了不报错）：

- `events/services.py` 的 `ministry_staff_participation()` —— 见 D1.2；
- `events/management/commands/seed_demo.py` 里两处 `Kind.EMPLOYEE`；
- `org/tests.py` / `events/tests.py` 里的 `Kind.EMPLOYEE`（`grep -rn "Kind.EMPLOYEE"`）。

**测试**：迁移前后各建一批行，断言映射表逐行成立；
断言 `Position.Kind` 只剩两档（防止以后有人偷偷加回第三档）。

## D1.2 R8 换口径（单独一步，因为它改的是答案）

`events/services.py:ministry_staff_participation()` 里
`position__kind=Position.Kind.EMPLOYEE` → `Position.Kind.STAFF`。

**这一步之后 R8 会返回不同的名单，而且两次都不报错**（D32 第三节）。
所以配一条固定新口径的测试：同一个 ministry 下建 `paid` / `unpaid` / `stipend`
三个在编人员，都参加同一场活动，断言三个人**都在** R8 的结果里。

⚠️ 保留原来那两条坑的测试：`.active(on=活动当天)` 不是 `.active()`，
`.active()` 不是 `.serving()`。它们和本步无关，但改这个函数时最容易碰坏。

## D1.3 补齐的 HRIS 字段（D32 第九节）

一次 migration，落点已经按 D10 四层判据分好：

```python
Position    + flsa_status   = exempt | non_exempt | not_applicable
            + headcount     PositiveIntegerField(null=True)      # 编制人数，≠ 在任人数
            + location      CharField(blank=True)

Assignment  + fte             DecimalField(3, 2, null=True)      # 0.50 = 半职
            + work_arrangement = onsite | hybrid | remote
            + hours_tracking  = scheduled | agreed | not_tracked   # self_logged 推迟
            + end_reason      FK → EndReason (null=True, PROTECT)
            + is_rehirable    BooleanField(null=True)            # null = 未评估

Contact     + service_start_date  DateField(null=True)           # 组织级入职日
            + employee_number     CharField(blank=True)

EndReason(code, name, is_active)                                 # 新字典表，D5 通则
```

三件同期的事：

1. **删掉 `Assignment.clean()` 里那条 `employment_type` 规则**（D32 第九节）。
   连同它的测试一起删，并在 `clean()` 的 docstring 里写明为什么删；
2. `EndReason` 按 [D5 通则](decisions/D05-lookup-tables-not-enums.md)带
   `Lower("code")` 唯一约束 + `ImmutableCodeMixin`，并给一条种子迁移
   （⚠️ [C0.2.1 的教训](03-roadmap.md#c021--给字典表加种子迁移打红了-40-个测试)：
   种子迁移会打红一批既有测试，一并修）；
3. `employee_number` 加 `Lower()` 唯一约束，**但允许多行为空** ——
   `UniqueConstraint(Lower("employee_number"), condition=~Q(employee_number=""))`。
   ⚠️ 不能靠 `nulls_distinct`，因为它是 `CharField(blank=True)` 存空串不是 NULL。

⚠️ `hours_tracking` **默认 `scheduled`，并且要显示在 D1.6 的员工名册上**（D32 第九节）。
一个只有 admin 看得见、且从不出现在页面上的字段，没有人会去维护它 ——
而它填错了不报错，只是报表那一格用错的口径读出一个像模像样的数。
`self_logged` 这一档**本轮不进 `choices`**，它连同 `HoursLog` 一起在推迟清单里。

`headcount` 落地后 `PositionQuerySet.vacant()` 的定义要跟着变：
从"一个人都没有"改成"在任人数 < `headcount`"，`headcount` 为空时退回旧定义。
**配一条三格测试**：空缺 / 部分填满 / 满编。

## D1.4 证照：`CredentialType` + `Credential`

```python
CredentialType(code, name, is_active, default_valid_days=null)
Credential(contact → PROTECT, credential_type → PROTECT,
           issued_on, expires_on=null, reference="", notes="")
```

⚠️ **不吃掉 `BackgroundCheck`**（D32 第九节）。背景审查仍然是推迟清单里那张独立表，
[D18](decisions/D18-admin-boundary.md) 的拆表决定不撤销。
在 `Credential` 的 docstring 里写死这句话，因为这正是以后最容易被顺手合并的地方。

queryset：`expiring_within(days)` / `expired(on=None)`，
时间口径走 [D16](decisions/D16-time-and-dates.md)。

## D1.5 `org/services.py` 的名册与统计

新增（全部是 queryset 或纯函数，视图一行逻辑都不写）：

```python
ministry_roster(ministry, *, on=None)     # 分组：Leaders / Staff(paid) / Staff(unpaid) / Board / 空缺
staff_directory(ministries, *, on=None)   # 收筛过的 queryset，不收 id —— D27 那条规矩
ministry_headcounts(ministries, *, on=None)
```

⚠️ **`staff_directory()` 收的是已经按权限收窄过的 ministry queryset，不是 id 列表。**
理由和 [D27 的唯一不变量](decisions/D27-ministry-report.md)一字不差：
收 id 的写法需要在函数里再判一次权限，而那一处判断迟早和页面那一处走散。

## D1.6 四个页面

| 页面 | URL | 要点 |
|---|---|---|
| Ministry 详情 | `/org/ministries/<pk>/` | 五组：Leaders / Staff (paid) / Staff (unpaid) / Board / **空缺**。空缺那一组是 D11 花两次修订买来的，必须画出来 |
| 组织架构图 | `/org/chart/` | ⚠️ **直接 `from org.services import build_org_tree`**，不要自己递归 `reports_to` —— `core/tests.py` 有守卫盯着 |
| 员工名册 | `/org/staff/` | 按 ministry / compensation / 在职状态筛选；分页 |
| 岗位详情 | `/org/positions/<pk>/` | 职责、`headcount` vs 在任、汇报线上下各一层、在任人员 |

**`build_org_tree()` 到今天为止没有任何调用者**（只有测试打它）。
组织架构图是它的第一个真实调用者 —— 这也是 C4 里欠着的那一步
（[`03-roadmap.md` C4](03-roadmap.md#c4--运营功能试点期间并行) 第 1、2 条）。

**权限**（D32 第五节末尾定的）：

- 组织架构图：**全体登录用户可见**（汇报线不是秘密）；
- `compensation` / `employment_type` / `fte` / 起止日期 / `end_reason` / 证照：
  **只有 ministry admin（限自己的 ministry）和 foundation tier**；
- `MinistryRole` **不加新档** —— [`goal.md` 待定表第 4 条](goal.md#还没定的哪些阻塞哪些不阻塞)
  建议先只做 `admin` 一档，照做。

⚠️ 权限收窄写在 `org/permissions.py`，模板里只问一个布尔。
模板里出现第二处判断，就是 D20 那条「权限判断变成两份」的开始。

## D1.7 个人档案页扩展

`accounts` 的「我的资料」页加三块：我的岗位（含汇报线）、我的证照（含快过期提示）、
我的任职历史。**只读**，编辑仍然走 admin 侧。

---

# D2 · 班表与请假

## D2.1 `WorkPattern`

```python
WorkPattern(assignment → CASCADE, weekday 0..6,
            start_time, end_time, start_date, end_date=null)
```

约束：`end_time > start_time`；`end_date >= start_date`；
`UniqueConstraint(assignment, weekday, start_time, start_date, nulls_distinct=False)`
（⚠️ `nulls_distinct=False` 是 A7 的老教训，`start_date` 可空）。

**跨午夜的班次先不支持**，`end_time > start_time` 直接挡掉。
真需要时的做法写进 D32 的推迟表 —— 不要在这一步顺手做。

## D2.2 `Shift`

```python
Shift(assignment → PROTECT, date, start_time, end_time,
      status = scheduled | worked | on_leave | absent | cancelled,
      actual_start=null, actual_end=null,          # 只在偏离时填 —— 例外记录法
      source = generated | manual | edited,
      generated_from → WorkPattern (null, SET_NULL),
      notes="")
```

`PROTECT` 而不是 `CASCADE`：班次行是工时记录，删任职不能带走它
（同 `Assignment.contact` 的理由）。

约束：`UniqueConstraint(assignment, date, start_time)`；`end_time > start_time`；
索引 `(assignment, date)` 和 `(date, status)`。

## D2.3 生成器 —— 纯函数先行

照 [C6.1 的做法](03-roadmap.md#c61-eventstokenspy--纯函数先写这一层)：无 DB、无 request。

```python
occurrences(pattern, since, until) -> list[date]     # 纯函数，先写这一层
generate_shifts(assignment, until)                   # 落库，幂等
```

⚠️ **生成器的规则只有一句**（D32 第四节）：

> 只覆盖 `date > today` 且 `source == generated` 的行。

**这条要有自己的测试，而且是本轮最重要的一条**：
造一批过去的班次（含一行 `source=edited`），改模板重新生成，
断言过去的行**一个字节都没动**，且未来那行 `source=edited` 的也没动。

⚠️ 「今天」只有一种写法，走 [D16](decisions/D16-time-and-dates.md)。
用 `datetime.date.today()` 会在生产的 UTC 下**静默错一天**，
而错的那一天正好是边界那一天。

## D2.4 生成 cron

`org/management/commands/generate_shifts.py` + `render.yaml` 第三个 cron。

⚠️ **`render.yaml` 开头那段警告现在适用于三个 cron**：
「两个 cron，不是一个」漏掉的表现是**没有表现**。
本步同时把那段注释里的"两个"改成"三个"，并把 R2 那七项环境变量
逐字复制进新 cron —— 那七项在现有两个 cron 里就是逐字重复的，
理由写在文件里，不要试图去重。

滚动窗口 26 周，`schedule` 用 UTC（⚠️ Render 的 cron 一律 UTC，
现有两个 cron 的注释里已经踩过一次）。

## D2.5 `Leave`

```python
Leave(assignment → PROTECT, start_date, end_date, reason="")
+ end_date >= start_date 约束 + simple-history
```

`services.apply_leave(leave)`：把区间内 `status=scheduled` 的班次翻成 `on_leave`。
**只翻 `scheduled` 的** —— 已经 `worked` 的不动（那是既成事实），
已经 `absent` 的不动（那是另一个人记下的判断）。

`Assignment.status` 的 `on_leave` 档**保留为当前值缓存**，不删
（推迟清单里那条早就写好了处置方式）。

## D2.6 「某天谁在班」只有一处实现

```python
on_duty(ministry, date)      # org/services.py，唯一的一处
```

⚠️ 这是本轮的第二条守卫（见下）。班表页、ministry 详情页、
以后的报表都必须问它，谁也不许自己拼 `Shift.objects.filter(...)` ——
因为口径里含着 `status` 该数哪几档，而那件事一旦有两份就会走散。

## D2.7 例会出勤：批量翻状态（D32 第四节，推翻了同日早些时候的「不记」）

例会记在 `WorkPattern` 里（每周二 19:00–21:00），稠密生成之后每周都真的落出一行 `Shift`。
所以"记出勤"不新建任何东西，是把已有的行翻一下：

```
本周二例会 · Food Pantry · 排班 8 人
    [ 全部到齐 ]      或者勾掉缺席的那几个
```

`services.confirm_shifts(shifts, *, absent_pks=())`：一次翻一批，
`scheduled → worked`，勾掉的翻 `absent`。**已经 `worked` / `on_leave` 的不动。**

⚠️ **不要给例会套 invite / accept 流程**（那是 D3 给一次性活动做的）。
一年 50 场，让人每次点"接受"，两周之内就没人点了 ——
指派进标准岗位本身就是那个承诺。

⚠️ 没人翻的行会永远停在 `scheduled`。**它既不是实到也不是缺席，是"没人看过"** ——
D2.8 的口径必须把它单独算成「未确认」，不能算实到，也不能算 0。
**这一条消不掉**（消掉它就等于要求打卡，而第七节选了不打卡），
所以做的是三件让它变小的事：那一屏**默认全勾**只需取消缺席的；
ministry admin 页面显示「上周有 N 小时未确认」；
🔴 **不做「超过两周自动确认」** —— 那是把假定值伪装成观测值，比现在更糟。

## D2.8 「实际投入」的唯一口径（本轮唯一一处，配守卫）

```python
staff_hours(assignments, start, end)   # org/services.py，返回 实到 / 未确认 / 请假 三个数
```

⚠️ **它绝不和 `Participation.hours` 相加。** 一个带薪员工在他排班的时段里去办了一场活动，
`Shift` 有一行、`Participation` 也有一行 —— 相加就是重复计算，
而它不报错、看起来还很像一个更完整的数字（D32 第八节）。

`hours_tracking` 决定这个函数怎么答：

| 模式 | 返回什么 |
|---|---|
| `scheduled` | 读 `Shift`，三个数 |
| `agreed` | 用 `fte` 折算成约定值，**带上"约定值，非观测值"的标记** |
| `not_tracked` | 返回"不计工时"这个状态本身，不返回数字 |

**这一格永远显示模式的标签，永远不显示裸的 `0`** —— 它是 D27
「没有和没算不能长得一样」推广一格，而落成结构之后就不靠谁记得了。
配一条测试：三种模式各断言一次，且断言 `not_tracked` 那一档**不返回 0**。

## D2.9 班表页面

| 页面 | 要点 |
|---|---|
| 周视图 `/org/schedule/` | 按 ministry 分组，一列一天。⚠️ **必须带时间窗**，不能有"列出全部班次"的入口（D32 代价第 4 条） |
| 班表编辑 | 改模板（影响未来）和改单个班次（只影响那一天）是**两个不同的按钮**，页面上要说清楚哪个是哪个 |
| 请假录入 | 一个表单，保存后当场显示"翻了 N 个班次" |

---

# D3 · 指派与接受/拒绝

## D3.1 `Participation` 加两档 + `source`

```python
Status  + INVITED = "invited"     + DECLINED = "declined"
source  = self_signup | assigned                            # 新字段
```

⚠️ **既有行的 `source` 一律回填成 `self_signup`** ——
默认值不能靠 `default=`，那样迁移之后旧行是对的、
而任何一条 `bulk_create` 写进来的新行会拿到一个没人想过的值。

## D3.2 两条写入路径

| 服务 | 落地状态 | 入口 |
|---|---|---|
| `invite(contact, event_role, invited_by)` | `INVITED` | 活动管理页「指派」，只列本 ministry 在编人员 |
| `register_on_behalf(contact, event_role, consent=None)` | `REGISTERED` | 活动管理页「代录」 |
| `respond_to_invite(participation, accepted)` | `REGISTERED` / `DECLINED` | 志愿者侧「我的邀请」 |

⚠️ **两条路都必须经过 `sign_up()` 的那两道门**（紧急联系人、未成年同意）。
那个函数的注释里已经写着"an admin entering somebody from a paper list reaches
this function too" —— 本步要让这句话第一次变成真的，不是绕开它。

通知走 `core/notifications/` 的投递适配器（[D22](decisions/D22-event-notifications.md)），
不新建投递代码。

## D3.3 统计口径（D32 第六节）

**`INVITED` / `DECLINED` 不算报名。** 要改的地方：

- `EventRoleQuerySet.with_signup_counts()` —— 只数 `REGISTERED` + `ATTENDED`；
- `events/services.py:ministry_report()` 的满员率、R5、缺勤率分母；
- 报表面板**并排加一个数**：「待答复 M 人」。

### 同一步里的第二件事：R6 / R7 只数无薪的（D32 第八节）

指派路径一落地，ministry admin 就能把带薪员工放进活动，
他们的工时会混进两个自称是"志愿者工时"的数字。**这是本步自己制造出来的问题。**

排除规则三条，写在 `services.py` 里：

- 排除在**办这场活动的那个 ministry** 里持有 `paid` / `stipend` 编制的人（活动当天口径，同 R8）；
- **跨 ministry 不排除** —— 财务部的带薪员工去食物银行帮忙是真的志愿服务；
- `stipend` 跟 `paid` 一起排除。

⚠️ 跨 ministry 那一种**出提示给人看，不拦截** —— 代码判断不了"是不是同一类工作"。
同 D32 第七节末尾那条 FLSA 提示，一个成因、一处实现。

## D3.4 D27 报表加「在编人员投入」

读 D2.8 那个函数，三个数：**实到** · **未确认** · **请假**，
和现有的「志愿者工时」**并排，不相加**，各自带注脚。

它解决的是一件具体的事：无薪在编成员的投入主要在例会和日常岗位上，
**那些时间一行都不在 `Participation` 里** —— 于是他们在「志愿者工时」上接近于零，
而他们很可能是投入最多的一批。拿那个数去跟董事会或捐赠人说人力价值，
会系统性地低估这批人。

⚠️ 「未确认」那个数**必须画出来**，同 D27 里「未成年无同意记录为 0 也要画」。

⚠️ 缺勤率的分母那一节（[D27](decisions/D27-ministry-report.md)）问的是
「这场活动还有没有报名停在 `registered`」。`INVITED` 的行**不能**算进那个判断 ——
"还没答复"和"没人处理过考勤"是两件事，混在一起会让分母虚高。

**这一条要有测试**，因为它错了不报错：造一场活动，2 人已确认、3 人待答复，
断言满员率的分子是 2 不是 5。

## D3.5 「等 N 人答复」要画出来

同 D27 里「未成年无同意记录为 0 也画」的规矩：藏起来的话，
「没人答应」和「还没问」在页面上长得一模一样。

---

# 本轮新增的守卫测试

现有 12 条之外加 4 条，都放 `core/tests.py`（和既有的汇报链守卫同一处）：

| 守卫 | 盯什么 | 不守会怎样 |
|---|---|---|
| 只有一处生成班次 | 除 `org/services.py` 外没有别处 `Shift.objects.create` / `bulk_create` | 第二个生成器和第一个的覆盖规则不一样，而两边都不报错 |
| 只有一处回答"某天谁在班" | 除 `org/services.py:on_duty()` 外没有别处按 `Shift.status` 过滤 | `status` 该数哪几档有两份口径，页面和报表对不上 |
| 生成器不碰过去 | grep + 行为测试双管：生成函数里必须出现 `date__gt` 那个条件 | ⚠️ 静默改写考勤史，这是本轮唯一那条真正会出事的 |
| 两个工时账本不相加 | 全仓没有任何一处把 `Shift` 的小时数和 `Participation.hours` 加在一起 | 重复计算，且加出来的数看起来更完整、更像对的 |

⚠️ 按项目惯例，每条守卫都要做**双向验证**：故意写一处违规的代码，确认它真的打红。

---

# 验收

按三批分别验收，每批走完标一次。

## D1

- [ ] 迁移前后各建一批 `Position`，映射表逐行成立；反向迁移跑得通（且 docstring 注明有损）
- [ ] R8：`paid` / `unpaid` / `stipend` 三个在编人员都在名单里
- [ ] `vacant()` 三格：空缺 / 部分填满 / 满编
- [ ] 组织架构图画得出来，且 `build_org_tree()` 只跑一次查询
- [ ] 拿 ministry admin 的真账号看**别的** ministry 的员工名册 → 403
- [ ] 普通志愿者账号看组织架构图 → 看得到；看 `compensation` → 看不到

## D2

- [ ] 改模板重新生成：过去的行零改动，未来 `source=edited` 的行零改动
- [ ] 两周假 = 1 行 `Leave` + N 个班次翻成 `on_leave`，且已 `worked` 的不翻
- [ ] cron 在 Render 上真的跑过一次，班表窗口真的往前推了
- [ ] ⚠️ 停掉 cron 一周，确认班表**在窗口末尾断掉**而不是报错 —— 确认这个失败模式长什么样，因为将来它一定会发生
- [ ] 周视图没有任何"列出全部"的入口
- [ ] 例会那一屏：8 人排班，勾掉 2 人，一次点击翻完；已 `worked` 的那行没被翻回去
- [ ] `hours_tracking=not_tracked` 的人，报表那一格显示「按产出考核」而**不是** `0`
- [ ] `hours_tracking=agreed` 的人，数字旁边带着「约定值，非观测值」

## D3

- [ ] 指派 → 对方收到通知 → 接受 → 状态变 `REGISTERED`
- [ ] 指派 → 拒绝 → `DECLINED`，且不进满员率
- [ ] 被邀请没答复的人当天扫码签到 → 直接 `ATTENDED`
- [ ] 2 人确认 + 3 人待答复的活动，满员率分子是 2
- [ ] 给一个没有紧急联系人的未成年人发指派 → 被 `sign_up()` 那两道门挡住
- [ ] 把本 ministry 的带薪员工指派进活动 → 他的工时**不进** R6 / R7
- [ ] 把**别的** ministry 的带薪员工指派进来 → 工时**照常进** R6 / R7，另外出一条 FLSA 提示
- [ ] D27 报表上「志愿者工时」和「在编人员投入」是两块，页面上没有任何地方把它们加起来

## 全轮

- [ ] `python manage.py check` / `makemigrations --check` / `ruff` 干净
- [ ] 测试数只增不减（[口径见 `phase-c.md`](phase-c.md#测试数基线只增不减的新口径)）
- [ ] 16 条 grep 守卫全部做过双向验证
- [ ] `python manage.py test core.tests.MarkdownLinkGuardTests` 绿 —— 本轮改了六份文档

---

# 计划外记录

> 实施时才发现的坑写在这里。**这一节是这个项目最贵的资产之一**，
> 每个 roadmap 都留着它，不要因为"这次很顺"就不写。

（待填）
