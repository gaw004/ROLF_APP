# D37 · 补齐的 HRIS 字段与证照表（2026-08-14）

结论：按 [D10](D10-person-role-position-assignment.md) 的四层判据分配落点，
**但先只做能说出读者的那几个**。证照独立成 `CredentialType` + `Credential`，
⚠️ **它不吃掉 `BackgroundCheck`**。

> 2026-08-14 从 [D32](D32-worker-axes-schedule-and-assignment.md) 第九节拆出来独立成条。
> ⚠️ 拆分时砍掉了四个字段（第二节），理由是本项目自己的自查问题。

## ⭐ 唯一的不变量：这一批字段必须能说出「哪条查询会读它」

[`../goal.md` 零](../goal.md) 末尾那句自查问题是这个项目最贵的一条纪律：

> 「这张表 / 这条约束，会出现在哪条需求的查询里？」答不上来就先别做。

D32 原文第九节开头写的却是「这一批不是需求里点名的，是每个 HRIS 都有、
迟早会撞上的东西，**用户要求一并排进来**」——
那是一次**明知故犯**，而且没有在文档里承认它是例外。

拆条时把这条纪律重新套一遍，砍掉四个（第二节）。剩下的每一个都写了读者。

## 一、做的：字段与落点

⚠️ **「谁读它」必须是本轮真的存在的那一条查询或那一列**，
不是「将来有用」。所以下表第四列写的是**本轮的落点**，
一个字段如果这一列填不出来，它就该待在第二节那张推迟表里。

| 字段 / 表 | 落点 | 判据 | 本轮谁读它（哪条查询 / 哪一列） |
|---|---|---|---|
| `compensation` | `Position` | 换个人来做都成立 → 编制 | R8 名单的「身份」列、志愿小时数口径、FLSA 提示（[D32](D32-worker-axes-schedule-and-assignment.md)） |
| `flsa_status` | `Position` | 同上 | ⭐ **员工名册上的矛盾提示**：`non_exempt` + `hours_tracking=not_tracked` 是法律上自相矛盾的组合（[D33 第七节](D33-work-schedule.md)）。这是它唯一的读者，也是它值得建的理由 |
| `headcount` | `Position` | 同上 | `understaffed()`（见第四节）；**基金会问过「每个职位多少人」，那句话里含着"应该有几人"** |
| `fte` | `Assignment` | 离不开具体这个人这一段任期 | 员工名册的一列 + 岗位详情（⚠️ 不参与工时折算，见 [D36 第四节](D36-two-hour-ledgers.md)） |
| `hours_tracking` | `Assignment` | 同上 | [D36](D36-two-hour-ledgers.md) 的口径函数 **+ 员工名册的一列**（第四节末尾那条：藏进 admin 的字段没有人维护） |
| `agreed_hours_per_week` | `Assignment` | 同上 | 同上，`agreed` 那一档；名册上「约定值未填写」的标记 |
| `service_start_date` | `Contact` | 换岗位也不变的人级事实 | 员工名册的**默认排序**和「服务年数」那一列 |
| `employee_number` | `Contact` | 同上 | 员工名册的一列（对外对账时人拿着它去核）+ 名册搜索框 |
| `CredentialType` + `Credential` | 新表 | 一个人多张证 → 基数条件破了，[D15](D15-relationship-carriers.md) 判据 → 必须是表 | 「谁的证下个月过期」（员工名册上的提示 + 我的资料）。⚠️ 但它过不了另一条判据，见第三节 |

⚠️ **`EmploymentType`（既有的那张字典表）本轮要重新问一遍它的读者。**
`compensation`（拿不拿钱）+ `fte`（多少比例）+ `hours_tracking`（怎么记时间）
落地之后，它还答什么？本条把 `Assignment.clean()` 那条规则删掉之后
（见下），它更是**没有任何代码 branch、也没有任何页面显示它**。
处置：把它放到岗位详情和员工名册上显示（合同形态是个真问题：全职 / 兼职 / 合同工 / 实习），
**或者**在下一轮把它一起推迟掉。不允许的是继续留着不显示 ——
那正是第二节砍掉四个字段用的同一条判据。

### `service_start_date` 为什么是存的不是派生的

第一版打算派生（`MIN(Assignment.start_date)`，零新字段）。**不成立**：
一旦有人离开又回来，`MIN` 给出的是第一次入职的日期，
**会把中间断掉的几年算进工龄**。HR 管这个叫 adjusted service date，
它在各家系统里都是**存的**字段，原因正是它派生不出来。

> ⚠️ D32 原文把这条的理由挂在 `is_rehirable` 上。那个字段现在推迟了（第二节），
> **但这条理由不跟着走** —— 离开又回来这件事本身就会发生，跟跟不跟踪"可否再雇"无关。
> 拆条时更正。

### `fte` 顺带解决一件事

`Assignment.clean()` 里那条「`employment_type` 只对 `kind=employee` 有意义」**删掉**。
无薪的人一样分「每周来两天」和「每周来五天」，那个信息交给 `fte`，
而 `employment_type` 回到它字面的意思（合同形态）。

⚠️ 连同它的测试一起删，并在 `clean()` 的 docstring 里写明为什么删 ——
否则下一个人会以为是漏掉的。

### 为什么不建 `StaffProfile` 1:1 表

`service_start_date` + `employee_number` 是 D10 说的"角色层"事实（换岗位也不变），
而 D10 把那一层指给了 1:1 角色表。**仍然放 `Contact` 上**，两条理由：
[D18](D18-admin-boundary.md) 的敏感度判据不触发（这两个都不敏感），
而 D11 的「只剩员工编号之类的零碎，不值得一张表」在字段数为 2 时仍然成立。

**重新考虑的条件**：出现第三个员工级字段，**或者**出现任何一个敏感的
（那时按 D18 必须独立成 model，不是可选）。
⚠️ 第二节推迟的 `is_rehirable` 正好是敏感的那一类 —— **它出栏之日就是这张表建立之时**。

## 二、砍掉的四个，以及它们为什么没通过自查

| 字段 | 谁读它？ | 处置 |
|---|---|---|
| `Position.location` | 答不上来。一个 ministry 一个地点，而 ministry 已经有了 | → 推迟。真需要时是一个 `CharField` |
| `Assignment.work_arrangement`（onsite / hybrid / remote） | 答不上来。小基金会的现场服务机构，remote 这一档现在没有对应的人 | → 推迟 |
| `Assignment.end_reason` → `EndReason` 字典表 | 答不上来。而它要付一张字典表 + 一次种子迁移（⚠️ [C0.2.1 的教训](../03-roadmap.md)：种子迁移会打红一批既有测试） | → 推迟 |
| `Assignment.is_rehirable` | 答不上来，**而且它是本轮最敏感的字段** —— 「此人不可再雇」是个会引起纠纷的判断，而 `MinistryRole` 现在只有 admin 一档，做了就等于所有 ministry admin 都看得见 | → 推迟，⚠️ 出栏时必须同时建 `StaffProfile` 或等价的权限边界 |

⚠️ **推迟的不是"这些字段没用"**，是"现在没有读者，而每个字段都要付权限、
表单、名册显示和一次迁移"。重启条件写进 [`../deferred.md`](../deferred.md)。

## 三、`Credential`：证照与有效期

```python
CredentialType(code, name, is_active, default_valid_days=null)
Credential(contact → PROTECT, credential_type → PROTECT,
           issued_on, expires_on=null, reference="", notes="")
```

queryset：`expiring_within(days)` / `expired(on=None)`，
时间口径走 [D16](D16-time-and-dates.md)。

装的是普通证照：CPR、食品安全、按立、驾照。
`expires_on` 让「谁的证下个月过期」变成一条查询 ——
它和 D27 里那个「未成年无同意记录」同类：**答的是"有没有出事"，不是"做得怎么样"**。

`CredentialType.default_valid_days` 的读者只有一个，而且必须写下来，
否则它就是个没人读的数：**录入表单按它预填 `expires_on`**（CPR 两年、
食品安全三年），人可以改。⚠️ 它是**预填不是写死** ——
同 [D38 第五节](D38-served-as-volunteer-or-work.md) 那条「预选而不是预填」的邻居问题：
这里预填是对的，因为有效期是发证机构定的客观事实，不是当事人的声明。

### ⚠️ 它过不了本阶段判据的第 1 条，这是一次有意的破例

[`../phase-d.md` 第二节](../phase-d.md#二判据这一阶段的东西该不该做)写着两条判据
**「两条都要过」**，第 1 条是「它是不是第二批需求原文里某一句的前置条件」。
证照和那三句话（像员工的志愿者 / 志愿还是工作 / 工时与排班）**没有关系**。

这一条必须承认，因为本条第二节刚刚用同一套判据砍掉了四个字段 ——
**用判据砍别人、不用判据量自己，是这份文档最容易犯的错**，
而 D32 原文（「不是需求点名的，用户要求一并排进来」）已经犯过一次。

破例的理由，以及它的位置：

- 证照到期是**"有没有出事"类的指标**，和 D27 那个未成年无同意记录同类。
  这类指标的价值不在被人查，在**不被人查的时候仍然在算**；
- 它便宜：两张表、两个 queryset、名册上一行提示，没有跨模块影响；
- ⚠️ **但它是本轮第一个该砍的东西。** 如果 D1 那一批时间紧，
  砍它比砍任何一个页面都安全 —— 因为砍掉之后，没有任何一句需求原文答不出来。

### ⚠️ 它不吃掉 `BackgroundCheck`

背景审查**仍然是独立 model**，[D18](D18-admin-boundary.md) 那条决定不撤销
（Django 没有字段级权限，敏感字段只能靠 model 边界隔离）。
把它塞进通用的 `Credential` 表，等于让所有能看证照的人都能看背景审查结果 ——
而这**正是 D18 拆表要防的那件事**。

这句话要写死在 `Credential` 的 docstring 里，
因为这正是以后最容易被顺手合并的地方。

## 四、代价

1. `headcount` 要的是**一个新方法**，不是改 `vacant()` 的定义。
   第一版打算把 `vacant()` 从"一个人都没有"改成"在任人数 < `headcount`"。
   ⚠️ **那会打破 `PositionQuerySet` 自己写下的不变量** ——
   它的 docstring 明写「vacant / occupied / retired **划分**整张表：
   一个岗位是其中之一，绝不是两个，也不会一个都不是」。
   编制 3 人、在任 1 人的岗位，改完之后**同时是 vacant 和 occupied**，
   而 `org/admin.py::StaffingFilter` 是个三选一的过滤器 ——
   同一行会在两个选项下各出现一次，且不报错。

   处置：**`vacant()` 一个字不动**，新增 `understaffed()`（在任 < `headcount`）。
   和 `EventRoleQuerySet.understaffed()` **同名同形同语义**（都是"要的比有的多"），
   `headcount` 为空时它返回空 —— 没写编制数就答不出还缺几个，这是真话。
   ministry 详情页和 D27 报表读新方法。

   ⚠️ 这样一来**本轮的静默语义变更从四处减少到三处** ——
   `kind` 的取值、R8 的名单、R6/R7 的分母。`vacant()` 不再在列，
   因为它根本没变。**能不改口径就不改口径，比改完配一条测试更便宜**；
2. **`employee_number` 的唯一约束要允许多行为空**：
   `UniqueConstraint(Lower("employee_number"), condition=~Q(employee_number=""))`。
   ⚠️ 不能靠 `nulls_distinct` —— 它是 `CharField(blank=True)`，存的是空串不是 NULL；
3. **谁生成员工编号没有定** —— 本轮手工填。真要自动生成时再说，
   ⚠️ 但别做成"最大值 +1"，那在并发下会撞。
