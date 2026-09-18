# D51 · 日期区间右开：`end_date` 是第一个不算数的日子（2026-09-17）

结论：全项目所有带起止日期的表，`end_date` 一律读作**第一个不算数的日子** ——
判定是 `start_date <= on < end_date`。于是同一条规则只剩**两种写法**
（问一批行、问一行），而不是四种。

> 本条**改写了 [D47](D47-event-level-grant.md) 的一段论证**（授权表敢读右开的依据，
> 已在那条里就地写了修订说明），并给 [D16](D16-time-and-dates.md) 补了一条交叉链接：
> D16 管「哪一个今天」，本条管「边界那一天算谁的」。

## ⭐ 唯一的不变量：一条规则，两种求值，零个开关

「有效期到 X 日，X 日那天算不算数」这个问题，全项目只有一个答案：**不算**。

两种事实的差别不再由**读法**表达，而由**写进去的是哪一天**表达：

| 说的是 | 写进 `end_date` 的是 | 谁写的 |
|---|---|---|
| 「他做到 3 月 15 日」 | 3 月 16 日 —— 次日 | `org.services.end_assignment(last_day=...)` |
| 「今天撤销他的权限」 | 今天 —— 当天 | `revoke_ministry_role()` / `revoke_event_grant()` |

两句话读的是同一条谓词，而它们各自都对。

⚠️ **`start_date` 因此不可为空**（三张表都是）。一个区间没有起点就无从判「在不在
里面」，而 SQL:2011 对 `PERIOD` 起止列的要求正是这个。表单留空由服务层填成今天。

## 一、为什么不是「把语义轴参数化」

2026-09-17 之前，同一条规则有**四种写法**，是一个 2×2：

|  | 问一批行（SQL） | 问一行（Python） |
|---|---|---|
| 右闭 / 事实 | `active()` | `is_currently_active` |
| 右开 / 授权 | `in_force()` | `is_in_force` |

第一版方案是把语义轴做成一个参数（一个开关，两份实现）。**用户当场提了更强的一版**：
把语义轴**消掉** —— 事实那一侧改成「做到 15 号就写 16 号停」，授权那一侧「哪天撤销
就写当天」，于是 2×2 塌成 2×1。

采纳它的理由是一句话：**开关是会被拨错的，不存在的开关不会。**

### 三条现场证据

1. 🔴 **`end_assignment()` 的注释当时是假的。** 它写着「结束之后这个人**当场**不再算
   在编人员」，而右闭下今天他还在 `[start, end]` 里。写注释的人脑子里是右开、代码
   是右闭，**没有任何测试钉这一格**。这是同一病灶的第三个症状，而且从来没有人发现过。
2. 🔴 **右闭那一侧的理由查无实据。** 当时写着「15 号当天不算在职的话工时会少算一天」——
   而全项目**没有任何地方拿 `end_date` 做算术**（搜过 `org/ events/ dashboard/ core/`，
   没有 `end - start`，没有天数差）。工时来自 `Participation.hours` 和 `Shift`。
   那条理由保护的是一个**不存在的计算**。
3. 🔴 **覆盖率最低的一格恰好是代价最高的一格。** `active()` 的四个边界钉得很死，
   而 `in_force()` 加进来时**一条边界测试都没有** —— 它和 `active()` 的全部差别
   就是撤销当天那一行。

### 它已经咬过一次，而且是静默的

`in_force()` 是 2026-09-15 加的，行级的 `is_in_force` 09-16 才补。中间那一天：
**撤销当天那一行写着「In effect: Yes」，正下方的横幅写着「Revoking takes effect at
once」**，而他一点权限都没有了。两边都不报错，因为两边**各自都是对的**，只是不是
同一条规则。

## 二、外部依据

存储侧，右开是标准，而不是一种口味：

- **SQL:2011** 的 application-time period tables 规定 `PERIOD` 是 closed-open，
  且起止列不可为空；
- **PostgreSQL** 的 `daterange` 默认边界就是 `[)`；PG 18 的 `WITHOUT OVERLAPS`
  建在同一套语义上；
- **RFC 5545（iCalendar）** 明文规定 `DTEND` 是 "non-inclusive"：6 月 28 日到
  7 月 8 日的活动，`DTEND` 写 **7 月 9 日**；
- ⭐ **而本仓库已经在两处这么做了**：[`events/ics.py`](../../../events/ics.py) 发的
  `DTEND`，以及 [`notices/models.py`](../../../notices/models.py) 的
  `starts_showing <= now < stops_showing`（约束也是严格 `>`）。
  **公告那一处是一次新鲜决策里自己选的半开** —— 四种写法的那三张表是历史包袱，
  不是深思熟虑的分歧。

⚠️ 而**人看到的**惯例相反，所以显示层要分开：**Workday** 里 termination 的
effective date 就是 last day of employment；失业金申请和 COBRA 也以「最后一天」
为锚。**Google Calendar** 严格存 exclusive、**显示 inclusive**，因为
"most users don't appreciate the difference"。展示口径见 D51 的第四节。

## 三、为什么「恢复」分支是错的修法（授权表的唯一约束）

两张授权表的约束是 `UNIQUE(contact, <东西>, start_date) nulls_distinct=False`，
而 `start_date` 对这张表是**偶然量**（表单默认留空）。于是它同时：

- **过松**：两行起始日期不同、都没结束 → 同一人**两条同时生效**。撤销页一次撤一行，
  点了「撤销」权限纹丝不动，而页面上没有任何东西解释为什么；
- **过紧**：去年就结束的那一行，再授权照样 `IntegrityError`（500）。

为绕开过紧，`grant_*_admin()` 加了「恢复」分支（清掉 `end_date`）—— 它在**当前表里
制造了一段从未存在过的连续授权**。HRIS 的规范做法是重新授权**开新行**、旧行不动
（SCD Type 2）；而对一个存着未成年人紧急联系电话的系统，「谁在什么时候能看这个」
恰好是最不该造假的一列。

真规则是**区间不相交**，落法是 Postgres 的排他约束
（`EXCLUDE ... daterange(start_date, end_date) WITH &&`）。而 `daterange` 的 `[)`
边界和本条规则**逐字相同** —— 于是「今天撤销、今天再授权」是两行不重叠的记录，
断档在当前表里如实保留，涂改液可以扔了。

⚠️ Django **不支持** PG18 的 `WITHOUT OVERLAPS`（ticket #36627，
`needsnewfeatureprocess`，未进任何版本），走 `ExclusionConstraint` —— 它从 PG 9.x
起就有，不依赖线上是不是 18，反而更稳。

⚠️ **已知的粒度限制**：日期粒度表达不了「上午 10 点发、下午 2 点撤」。那种情况
`end_date == start_date`，是一个**零长度**区间（什么都没覆盖），合法且有意义；
Postgres 里空区间不和任何东西重叠，所以这样的行可以有任意多条。
`end_date >= start_date` 那条 CheckConstraint 因此保持 `>=` 不变。

## 四、显示：存一条规则，展示两个具名口径

`end_date` **不许在模板里裸打印**（守卫见下）。两个展示器各一处实现：

| 表 | 展示器 | 显示的值 | 表头 |
|---|---|---|---|
| `Assignment` | `last_day` | `end_date − 1` | 最后一天 / Through |
| `MinistryRole` / `EventGrant` | `revoked_on` | `end_date` | 撤销于 / Revoked |

授权那一侧不需要 `−1`：「撤销于 3 月 16 日」本来就读作「16 号起没了」，
而 ISO 27001 A.5.18 的「移除访问权发生在哪一天」天然是 exclusive 边界。

⚠️ **这不是又养出两条规则。** 被消灭的是两种**查询语义**，保留的是两个**标签**，
而标签本来就该随业务领域不同 —— 这正是 Google Calendar 的做法。

## 五、代价，如实记

- **一次数据迁移**，`Assignment.end_date`（连同影子表）整体 +1 天。影子表必须一起动：
  不动的话翻历史会看到一条「结束日期从 3-15 变成 3-16」的假变更，而一份可被改写的
  审计记录在合规上等于没有。
  ⚠️ 回填 `start_date` 那一步的**反向是 noop** —— 哪几行原来是空的，改完就无从知道。
- **在 admin 里手填 `end_date` 的人要改习惯**：那一格现在是「停止日」。admin 是脚手架
  （[D18](D18-admin-boundary.md)），站点侧的表单上没有这一格。
- **`is_currently_active` 没有改名成 `is_active`**，尽管后者更短：`is_active` 已经是
  Contact / Ministry / Position / EmploymentType / ParticipationRole 上的**字段**，
  意思是「这个东西还存在」，而 `position_detail.html` 就在任职表格正上方打印
  `position.is_active`。一个名字两个意思，正是本条要消灭的那类东西。

## 六、守卫

- `core.tests.DatePredicateHalvesAgreeTests.test_every_predicate_answers_the_same_for_one_row_and_for_a_set`
  —— 同一个问题，问一批行和问一行，不许得到两个答案。**这是 09-15 那个 bug 的直接
  守卫**：加了集合谓词而忘了行级双胞胎，下一次就是红的。
- `core.tests.ActiveQuerySetTests` —— 右开的四个边界，含
  `test_active_excludes_a_row_ending_today`（它 2026-09-17 翻了面，此前被标着「别动」）。
- `in_effect_on()` 的另外三条调用路径各有自己的边界测试，因为它们是**四段不同的
  SQL**：`org.tests.VacancyTests.test_a_post_is_still_held_on_its_holders_last_day`
  （`OuterRef("pk")`）、
  `org.tests.VacancyTests.test_a_tenure_on_its_last_day_still_counts_in_the_headcount`
  （`prefix="assignments__"`）、
  `org.tests.StaffRosterTests.test_a_tenure_ending_on_the_event_day_still_sees_that_event`
  （`on=OuterRef(day)`，而它判的是**活动那一天**、不是今天）。

⚠️ **网是反向验过的**：把 `in_effect_on()` 临时改成右开跑全量，恰好 8 条红，
其中 **4 条是织网那一个 commit 刚加的** —— 也就是说没有那一步，这次改动会静默改掉
空缺、编制人数、受众可见性和在编名册四件事，而全量测试照样全绿。

## 七、没有做的事

- **`notices.Notice` 一个字没动。** 它已经是右开的，只是列叫 `starts_showing` /
  `stops_showing`、类型是 datetime 而不是 date，所以不在 `DateRangeMixin` 上。
  ⚠️ 它同样是「一条规则两处书写」（[`notices/models.py`](../../../notices/models.py)
  的查询集一处、`is_showing` 一处），而**它没有奇偶守卫**。哪天那一对走散了，
  症状会和 09-15 那次一模一样。
- **`WorkPattern` / `Leave` / `Shift`（[D33](D33-work-schedule.md) /
  [D34](D34-leave.md)）还没建**，建的时候按本条来。
