# ROLF_APP — 目标、重大决策与进度

> 这份文档是项目的**唯一入口和唯一索引**：要做什么、现在的优先级是什么、
> 别的东西各自在哪一份文件里。做了新的重大决策或完成一个阶段就回来更新这里。
>
> 最后更新：2026-07-30

> ## 📁 文件地图（2026-07-30 拆分）
>
> 本文档原来是一个 3859 行的单文件，**已按主题拆开**。内容一字未删，只是搬了地方。
>
> | 文件 | 装什么 | 什么时候看 |
> |---|---|---|
> | **`goal.md`**（本文件） | 目标 · 技术选型 · **当前优先级** · 导航 · 进度一览 · 下一步 | 每次开工前 |
> | [`decisions/`](decisions/README.md) | D1–D24，一条决策一个文件 | 想知道"某件事当初为什么这么定" |
> | [`phase-c.md`](phase-c.md) | **Phase C** 的要点：判据 · **落点规矩**（样式写哪、JS 写哪、什么该翻）· 验收口径 · 已知缺口 | 当前开工期间天天翻 |
> | [`03-roadmap.md`](03-roadmap.md) | **Phase C** 的**实施步骤** C0 · C0.5 · C3 · C4 · C5（照着一步步做） | 当前动手写代码时 |
> | [`04-roadmap.md`](04-roadmap.md) | **Phase C 的前端分册**：C1（构建链）· C2（设计系统 + 20 个模板） | 做前端那一段时 |
> | [`phase-d.md`](phase-d.md) | **Phase D** 的要点：**需求原文（第二批）** · 判据 · ⭐ **页面与入口总表** · 权限 · 验收口径 · 已知缺口 | 做员工那一段时天天翻 |
> | [`05-roadmap.md`](05-roadmap.md) | **Phase D · 员工与排班** 的实施步骤 D1 / D2a / D2b / D3 四批 | 做员工那一段动手写代码时 |
> | [`design-system.md`](design-system.md) | 色板 · 字号 · 间距 · 组件 · HTMX 与 Alpine 的用法 | C2 逐页重写时天天翻 |
> | [`phase-b.md`](phase-b.md) | Phase B 的模型表 + 全部实现要点 + 测试清单 + 验收 | 查 Phase B 建的表和它们的判据 |
> | [`02-roadmap.md`](02-roadmap.md) | Phase B 的**实施步骤** B0–B13 | 查历史 |
> | [`progress.md`](progress.md) | 已完成的部分（数据核心 / Phase A）+ Phase C / D 的计划 | 想知道"走到哪了" |
> | [`deferred.md`](deferred.md) | 明确推迟的事 + 各自的重启条件 | 手痒想做某件事时 |
> | [`revisions.md`](revisions.md) | 全部修订记录（2026-07-29 / 07-28 各轮） | 想知道"这条为什么改口" |
> | [`01-roadmap.md`](01-roadmap.md) | Phase A 的实施手册，已完成 | 查历史 |
> | [`diagrams/`](diagrams/README.md) | ERD · DFD · app 边界图（浏览器打开，零依赖） | 想一眼看清"表怎么连、数据怎么走" |
>
> ### 约定（不要破坏）
>
> 1. 决策编号是稳定引用。 代码注释、roadmap、本文档里写的 `goal.md D9`、`D14`、`D18`
>    指的是 [`decisions/`](decisions/README.md) 里的那一条 —— **编号永不改**，文件名可以改。
>    正文搬走了，但**"见 goal.md X"这种引用仍然成立**，因为本文档一定能把你导到 X。
> 2. **每个 Phase 一份 `0N-roadmap.md`**，编号递增，**旧的不覆盖、不删除** ——
>    里面的「计划外」记录（踩过的坑）是这个项目最贵的资产之一。
>    **2026-08-03 放宽了一格**：Phase C 有两份手册（`03` 是主册，
>    `04` 是它的前端分册），因为前端目标改成 Tailwind + HTMX + Alpine 之后
>    C1 / C2 装不下了。**所以 Phase D 的手册用 `05-roadmap.md`**，不是 `04`。
>    约定的意图（旧的不覆盖、编号是稳定引用）不变，只是「一个 Phase 一份」这条形式松了。
> 3. 正文不写代码行号。 代码搬一次行号就变成假的，而假引用比没引用更糟
>    （已经踩过两次，见 [`revisions.md`](revisions.md)）。写文件名 + 符号名。
> 4. **链接由测试看着**（`core.tests.MarkdownLinkGuardTests`，第十一次「测试当 lint」）：
>    全仓每个 `.md` 的每一处链接，文件和锚点都必须指得到，新文档自动纳入。
>    ⚠️ **锚点跟着标题文字走 —— 改一个标题，指向它的链接全断**，所以改标题后跑一次
>    `python manage.py test core.tests.MarkdownLinkGuardTests`。
> 5. **冲突时以本文档的[零、当前优先级](#零当前优先级2026-07-29-定)为准**，其余文件都服从它。
> 6. **强调是配额，不是语气**（2026-07-30 定，见下）。

> ## 强调的用法：三个符号，各只有一个意思（2026-07-30）
>
> 起因：一次自查发现 **35% 的行带粗体、46 个 `⭐`、206 个 `⚠️`** ——
> 而同一个符号被用在三四种意思上（`⭐` 既标"唯一验收点"也标 `# ⭐ P3`；
> ⚠️ 既标"会静默失败的坑"也标"2026-07-29 晚更正"这种变更说明）。
> **强调到处都是，等于没有强调** —— 读者没法一眼看出哪条真的要命。
>
> | 符号 | 唯一含义 | 配额 |
> |---|---|---|
> | `⭐` | **唯一验收点**：这一条不过，对应的那整块设计就是白做的 | 一个决策 / 一个步骤最多一个，全仓十几个 |
> | `⚠️` | **会静默失败的坑**：不报错，只是悄悄给出错的结果或丢数据 | 只给真的不报错的东西。**"注意""改过""重要"都不算** |
> | 粗体 | 一句话里**承重的那半句** | 不整格加粗、不整句加粗、不整段加粗 |
>
> - **变更说明不用 ⚠️**，直接写「2026-07-29 更正：…」——它是历史，不是陷阱；
> - `✅` / `❌` / `~~删除线~~` 表示的是**状态**（做完了 / 否决了），那是数据不是强调，不受配额约束；
> - **表格里整列加粗 = 这一列没有重点**。要突出某一行，靠内容或 `⭐`，不靠给整格描粗。
>
> > **判定方法**（同本项目其它判据，是一个能当场做的动作）：
> > **把这一页的粗体全部去掉，再问哪几句必须重新加粗？**
> > 答不出三句以内，说明这一页本来就没有重点，加粗只是语气。

> # ⭐ 先读这个：当前唯一优先级（2026-07-29 定）
>
> **基金会给出了一套完整的、具体的需求 —— 见[零、当前优先级](#零当前优先级2026-07-29-定)。
> 它现在是本项目唯一的优先级。**
>
> 判定规则，从今天起对每一件事都适用：
>
> > **这件事是不是[零](#零当前优先级2026-07-29-定)里那 14 条需求的前置条件？
> > 不是 → 它不进本阶段，不管它以前排在哪。**
>
> **2026-08-14：这条规则第一次换锚了**（Phase D 不满足它）。
> 退休判据、新的锚、以及为什么手续要补，见
> [判定规则的第一次换锚](#判定规则的第一次换锚2026-08-14)。
> Phase C 收尾**仍然优先**。
>
> 已经因此改动的决策：[D2](decisions/D02-frontend-deferred.md)（前端不再整体推迟）、
> [Phase B 的模型表](phase-b.md)（`Event` 一族重画）、
> [Phase C / D 的排序](progress.md)（资金追踪整体后移，权限提前）。
> 新增 [D19](decisions/D19-event-role.md) / [D20](decisions/D20-ministry-role.md) /
> [D21](decisions/D21-self-service-and-permissions.md) / [D22](decisions/D22-event-notifications.md) 四条决策。
> **已经写好的代码一行不删** —— 见[零](#零当前优先级2026-07-29-定)末尾「已实现但暂时用不上的」。

---

## 目录

> **怎么用这套文档**：
>
> - **新接手的人**：本文件的[一、终极目标](#一终极目标) → [零、当前优先级](#零当前优先级2026-07-29-定) →
>   [`decisions/`](decisions/README.md) 里的 [D4](decisions/D04-contact-one-table.md) /
>   [D10](decisions/D10-person-role-position-assignment.md) /
>   [D11](decisions/D11-position-and-assignment.md) /
>   [D15](decisions/D15-relationship-carriers.md) 这四条 —— 它们决定了"一条信息该放进哪张表"。
> - **已经在写代码的人**：日常查的是 [`phase-c.md`](phase-c.md)（判据 + 落点规矩）和
>   [`03-roadmap.md`](03-roadmap.md) / [`04-roadmap.md`](04-roadmap.md)（实施步骤）。
>   ⚠️ 本行原来指向 phase-b.md / 02-roadmap.md，那是 Phase B 开工时写的，
>   Phase C 开工后一直没改 —— 而本文档开头那张表早就把它们标成了「查历史」，
>   于是同一份文件里两处打架（2026-08-09 对齐）。

### 常见问题 → 去哪找

| 我想知道… | 去看 |
|---|---|
| 现在到底该做什么？ | [零、当前优先级](#零当前优先级2026-07-29-定) —— 14 条需求 + 逐条覆盖对账 |
| 这件事该不该做？ | 判据：**它是不是那 14 条的前置条件**。不是就不做，见[零](#零当前优先级2026-07-29-定) |
| 下一步具体做什么？ | [六、下一步](#六下一步)，然后照 [`03-roadmap.md`](03-roadmap.md)（前端那一段在 [`04-roadmap.md`](04-roadmap.md)）|
| 「食物银行的 admin」在数据库里怎么表达？ | [D20](decisions/D20-ministry-role.md) —— Django Group 顶不上，为什么 |
| 「这场活动开了几个工种」为什么不能数报名？ | [D19](decisions/D19-event-role.md) —— 和 D11 的「空缺编制」是同一个病 |
| 活动改时间了，通知谁？ | [D22](decisions/D22-event-notifications.md) —— **未成年人通知家长**；联系不上的人必须自己算 |
| 志愿者能看到哪些活动？ | [phase-b.md「可见性与生命周期」](phase-b.md#可见性与生命周期两个谓词不是一个-status2026-07-29-晚新增) —— **`draft` 是唯一的不可见档**，别把可见性写成 `status=open` |
| 新加一个分类字段，做成 `TextChoices` 还是字典表？ | [D5 判定规则](decisions/D05-lookup-tables-not-enums.md#判定规则什么时候用字典表什么时候用-textchoices2026-07-28-补) |
| 这条信息该放 `Contact` / 角色表 / `Position` / `Assignment`？ | [D10 四层判断标准](decisions/D10-person-role-position-assignment.md) |
| 「像员工一样有固定岗位、但不拿钱」的人怎么存？ | [D32](decisions/D32-worker-axes-schedule-and-assignment.md) —— 不是缺一个类型，是一个字段扛了三个轴 |
| 员工的工作时间 / 排班在哪？ | [D33](decisions/D33-work-schedule.md) —— `WorkPattern` + `Shift`；⚠️ 和推迟清单里被否决的那个「活动内班次 `Shift`」不是一回事 |
| 请假记在哪？ | [D34](decisions/D34-leave.md) —— 独立成表，理由走 `LeaveType` 字典表**不用自由文本** |
| 一个人「总共投入了多少小时」怎么算？ | [D36](decisions/D36-two-hour-ledgers.md) —— ⚠️ **两个账本会重叠，「总投入」加起来就是错的**；量不出来的人显示模式标签，不显示 `0`。⭐ 但**志愿服务小时数有定义、可以印**，见 [D38 第七节](decisions/D38-served-as-volunteer-or-work.md) |
| 一次批量建了 30 条模板，建错了怎么收？ | [D40](decisions/D40-undo-a-pattern-batch.md) —— 整批撤销，⚠️ 但「撤销」是**停止并收回未来**，过去的班次一行都不动 |
| 一个人被排了两件同时的事，系统会说话吗？ | [D39](decisions/D39-scheduling-conflicts.md) —— 四类冲突走一个函数，⚠️ **一类都不拦截**；而其中一半的检测 D36 / D38 早就写好了，只是挂错了条件、也出现在事后的报表上 |
| 员工要不要打卡？ | [D33 第七节](decisions/D33-work-schedule.md) —— 取决于 exempt / non-exempt，不取决于机构偏好；而一行班次就是法律认的例外记录表 |
| ⭐ 一个员工参加活动，怎么分清是**献爱心**还是**工作安排**？ | [D38](decisions/D38-served-as-volunteer-or-work.md) —— 一个字段 `served_as`，**当事人自己声明**、admin 可更正、改动本人看得见。⚠️ 不许从「谁点的按钮」或「那天有没有排班」推 |
| 员工在哪里看自己的排班和工时？ | [`phase-d.md` 的 My Schedule](phase-d.md#my-schedule-的形状) —— 一页、两条泳道、三个数、永不求和，外加一个 `.ics` 订阅 |
| 一种新关系该用字段、自引用 FK 还是专用表？ | [D15 四条判据 + 选择规则](decisions/D15-relationship-carriers.md) |
| 新模型放哪个 app？ | [D17](decisions/D17-app-layout.md) |
| 这段代码写在 admin 里行不行？ | [D18 的落点规矩](decisions/D18-admin-boundary.md#逻辑落点的硬规矩成本为零现在就要守) —— 判据：换个界面要不要跟着搬 |
| 这段代码该写进哪个文件？升级 / 换前端之后还用得上吗？ | [D18 代码落点与文件分层](decisions/D18-admin-boundary.md#代码落点与文件分层什么会随升级坏什么换界面还用得上2026-07-28-补) —— 判据：**把 `admin.py` 删掉还剩什么** |
| 业务规则写成数据库约束还是 `clean()`？ | [D9](decisions/D09-rules-in-db-constraints.md) + [D14](decisions/D14-constraint-is-the-only-rule.md) |
| 代码里要取"今天"？ | [D16](decisions/D16-time-and-dates.md) |
| 某个决定当初为什么改口？ | [D9](decisions/D09-rules-in-db-constraints.md) / [D11](decisions/D11-position-and-assignment.md) / [D15](decisions/D15-relationship-carriers.md) 各自的修订说明 + [`revisions.md`](revisions.md) |
| 一个编制没人在任（空缺）怎么表示？ | [D11 第二次修订](decisions/D11-position-and-assignment.md#第二次修订为什么-reports_to-不能指向-assignment) + [空缺编制](phase-b.md#空缺编制这次修订的验收点) |
| Phase B 要建哪些表、必须带哪些约束？ | [`phase-b.md`](phase-b.md) 的模型表 + [约束清单](phase-b.md#新表的约束必须和表同期落地延续-a7-的教训) |
| 某件事为什么现在不做？ | [`deferred.md`](deferred.md) |
| 还有什么没拍板？ | [六 · 还没定的](#还没定的哪些阻塞哪些不阻塞) |

### 决策一览 D1–D40

**完整索引在 [`decisions/README.md`](decisions/README.md)**（每条一句话结论 + 它回答的问题）。
一条决策一个文件，编号是稳定引用。

### 实现要点索引

**当前（Phase C）在 [`phase-c.md`](phase-c.md) 开头** —— 判据、落点规矩
（样式写哪、JS 写哪、什么该翻）、验收口径、已知缺口。开工期间日常翻的是那一份。

**Phase B 的在 [`phase-b.md`](phase-b.md) 开头**，已完成但没过期：
`.active()` 与时间口径、约束清单、`on_delete` 表、R8 那条查询、可见性、
签到与 `hours`、必须写的测试、验收清单，全部在那里 —— 查表的形状和它为什么
带那些约束，仍然去那一份。

---

## 零、当前优先级（2026-07-29 定）

> 这一节压过本文档除[一、终极目标](#一终极目标)之外的一切。
> 和它冲突的决策已经在原地改掉或删掉了，**改动都留了修订说明，没有一条是悄悄改的**。

### 需求原文（不要转述，转述会丢东西）

> 在特定时间有多少 events 开展，每个 event 分别来自哪个 ministry，每个 event 创办了多久时长，
> 每个 event 有多少工种，每个工种 volunteers 有多少人，volunteers 一共做了多少工时，
> 分别在各个工种做了多少工时。在某 event 里，开设这个 event 的 ministry 下面的 employee
> 有谁参与了这个 event，分别负责什么。
>
> 我还需要 accounts，每个新建的 account 都会 create 一个 contact。在每个 ministry 下面有
> admin 权限的人可以发布 event（征集 volunteers，event 会说明需要多少 volunteers），
> 每个普通 account 可以看到发布的 event，选择注册参加 event（如果是 minor，可能涉及
> guardian consent 之类的）。每个 ministry 的权限的人都可以看到有多少人报名 event。
> 在 event 活动期间，也要有每个 volunteer 是否来过的记录。event 结束后，跟 event 同个
> ministry 的权限的人可以统计出有多少 volunteers 来，每个人做了多久。
>
> Account 除了每个 ministry 下面有 admin 权限的人，还有更高一级的权限可以指定谁可以成为
> 每个 ministry 下面有 admin 权限的人。

### 拆成 14 条可验收的需求

> 条数以下面两张表为准：报表侧 R1–R8 共 8 条，流程侧 P1–P6 共 6 条，合计 14。
> ⚠️ 2026-07-29 全文一度写成"12 条"（P6 追加前也应是 13 条，不是 12）——
> **那是个纯算术错误，已全部改正**。这个数字在正文里被复制了十几处，
> 改需求条数时全文搜 `14 条` 一并改；能写 `R1–R8 + P1–P6` 的地方优先写它，不要复制数字。

报表侧（活动办完之后要答得出的问题）

| # | 需求 |
|---|---|
| R1 | 某个时间段里有多少场 event |
| R2 | 每场 event 属于哪个 ministry |
| R3 | 每场 event 持续多久 |
| R4 | 每场 event 开了多少个工种 |
| R5 | 每个工种有多少 volunteer |
| R6 | 一场 event 的总工时 |
| R7 | 按工种分的工时 |
| R8 | 某场 event 里，**开设它的那个 ministry 下面的 employee** 谁参与了、分别负责什么 |

流程侧（活动办起来的过程）

| # | 需求 |
|---|---|
| P1 | 有 account 体系；**每建一个 account 就建一个 `Contact`** |
| P2 | 某个 ministry 下有 admin 权限的人可以发布 event，发布时说明**每个工种要几个 volunteer** |
| P3 | 普通 account 能看到已发布的 event，能报名；**未成年人涉及 guardian consent** |
| P4 | 该 ministry 有权限的人能看到报名人数；活动期间有**是否来过**的记录；结束后能统计**来了几个人、每人做了多久** |
| P5 | **更高一级的权限**可以指定谁成为某个 ministry 的 admin |
| P6 | 活动改时间时，该 ministry 有权限的人能快速找到所有已报名的 volunteer 并给他们发通知（2026-07-29 追加） |

> **P6 是需求方后来追加的一条**（原话：「如果一个 event 突然改时间了，开设这个 event 的
> ministry 下面有权限的人可以快速找到所有已经报名这个 event 的 volunteers，给他们发通知」）。
> **"快速找到"这半句现有结构已经免费提供**（`Participation` 一个 filter），
> 真正的新东西是另外三件，见 [D22](decisions/D22-event-notifications.md#d22--活动变更通知收件人解析是业务逻辑投递是可替换的适配器2026-07-29)。

### 逐条对账：现在的表答不答得出来

`core` / `contact` / `org` 三个 app 已建成（B0–B5），`events` / `accounts` 的业务部分还没写。

| # | 靠什么回答 | 状态 |
|---|---|---|
| R1 | `Event.start_time` + `Index(start_time)` + `EventQuerySet.in_period()` | ✅ 时间段筛选 + 条数，落在**两处**：志愿者的 `/events/`（今天起，往前看）和管理侧的 All Events（任意窗口，含全部历史）。2026-08-17 **改口**：原本还有一个「所有人可见的往期活动页」，那一页删了 —— 往回看的那一半现在**只有 foundation tier 有**（走 All Events）。志愿者自己参加过的仍在 My Signups。改动的经过见 [`revisions.md`](revisions.md) |
| R2 | `Event.ministry` | ⚠️ **原设计可空 → 改非空**，见下面模型表 |
| R3 | `end_time - start_time`，派生不存 | ✅ |
| R4 | `EventRole` 一行一个工种 | ❌ **原设计没有这张表**，见 [D19](decisions/D19-event-role.md#d19--活动的工种编制-eventrole2026-07-29) |
| R5 | `Count(Participation)` group by `event_role` | ⚠️ 随 R4 |
| R6 | `Sum(Participation.hours)` | ✅ |
| R7 | `Sum(hours)` group by `event_role` | ⚠️ 随 R4 |
| R8 | `Participation` × `Assignment.active(on=活动日)` × `Position(kind=employee, ministry=活动的 ministry)` | ✅ **三张表 join 就答得出，这正是 B5 那套结构存在的理由** |
| P1 | `User.contact` 已有；缺注册流程 | ⚠️ 字段不用改，缺 `accounts/services.py` |
| P2 | **`MinistryRole`** + `Event.status` 的发布档 + `EventRole.needed_count` | ❌ **最大的结构缺口**，见 [D20](decisions/D20-ministry-role.md#d20--范围化权限-ministryrole不走-django-group2026-07-29) |
| P3 | 自助页面 + `Participation` 的同意字段 | ❌ 页面从零；同意记录见下面模型表 |
| P4 | `Participation` 的 `status` + 签到签退 + `hours` | ⚠️ 形状要补，见模型表 |
| P5 | `MinistryRole` + 一个全局 Group | ❌ 随 P2 |
| P6 | 找人：`Participation` 一个 filter（**免费**）<br>发通知：`EventNotification` + 收件人解析 + 投递适配器 | ⚠️ **一半免费，一半是新的** —— 见 [D22](decisions/D22-event-notifications.md#d22--活动变更通知收件人解析是业务逻辑投递是可替换的适配器2026-07-29) |

**一句话总结差距：报表侧结构缺一张表（`EventRole`），流程侧缺两张表
（`MinistryRole`、`EventNotification`）、一层权限服务、一层投递适配器、
和一整套面向志愿者的自建页面。**

### 排除了什么（以及它们去哪了）

按上面那条判定规则（"是不是这 14 条的前置条件"）被移出当前阶段的：

| 原本排在 Phase B / C 的 | 处置 | 为什么 |
|---|---|---|
| `VolunteerProfile`（原 B7） | → [推迟清单](deferred.md#五明确推迟的事) | 14 条需求一条都没提到技能和可服务时段 |
| `BackgroundCheck`（原 B7） | → [推迟清单](deferred.md#五明确推迟的事) | 同上。**但拆表的决定不撤销**（[D18](decisions/D18-admin-boundary.md#d18--admin-的边界以及业务逻辑的落点2026-07-28)），建的时候仍然是独立模型 |
| `Skill` | 继续推迟 | 本来就在推迟清单 |
| Phase C 资金追踪（`Contribution`） | → 整体后移，改叫 Phase D | 钱和这 14 条完全无关 |
| **原 Phase D**（对调后是现在的 Phase C）**的权限方案** | → **提前**，进当前阶段 | 志愿者一旦能登录，它就从"上线前置"变成"这个功能自己的前置"，见 [D21](decisions/D21-self-service-and-permissions.md#d21--对外账号志愿者自助页面提前权限成为它的前置条件2026-07-29) |

### 已实现但暂时用不上的：留着，不删

`Contact` 的分级查重与 `merge_contacts()`（B4.3、B4.4）对上面 14 条需求的
贡献接近零。**但一行都不删**，两个理由：

1. 它是**正确的**、有测试的、且不挡任何人的路 —— 删掉是纯粹的净损失；
2. `merge_contacts()` 会遍历 `Contact._meta.related_objects`，**新加的 `Participation` /
   `MinistryRole` 自动被它覆盖**，还有一条测试盯着"有没有漏表"。开放注册一上线，
   跨渠道重复只会更多，它反而变得更有用。

`EmergencyContact`（B4.2）和 `is_minor` 三态（B4.5）则**直接落在 P3 上** ——
同一批工作里这两块本来就是命中的。

> **值得记下来的判断**：偏离的判据不是"做了组织架构"——
> R8 只有 `Ministry` + `Position` + `Assignment` 才答得出来，B5 是必要投资。
> 偏离的是**为一条查询都不会碰的数据做了一整套完整性工程**。
> 下次的自查问题：「这张表 / 这条约束，会出现在哪条需求的查询里？」答不上来就先别做。

---

## 判定规则的第一次换锚（2026-08-14）

上面那条判定规则（「**是不是那 14 条需求的前置条件？不是就不做**」）
自 2026-07-29 起管着每一件事。**Phase D 是第一件不满足它的工作** ——
除了 R8 换口径之外，员工与排班和那 14 条一条都不沾。

这里把手续补上 —— **规则没有退休条件，比破例本身更糟**。
一次没有解释的例外，会让下一个读文档的人发现规则和进度表互相矛盾，
**然后两个都不信**。

### 旧规则退休的判据，以及它现在到哪了

> R1–R8 + P1–P6 **全部跑通**，并经过一次真实试点。

现在的位置：功能侧十四条已经全部落地，差的是
[C0.3 那一遍浏览器验收](03-roadmap.md#c03-三角色浏览器验收)和 C5 试点。
所以旧规则**接近功成，但还没功成** —— 由此得出本次换锚的两条纪律：

1. **Phase C 的收尾优先级不变**：C0.3 · C3 部署 · C5 试点仍然排在
   Phase D 的 [D2b / D3](05-roadmap.md#交付节奏四批中间留一次反馈) 之前；
2. **Phase D 的 D1 / D2a 可以并行** —— 它们几乎全是结构和页面，不碰上线路径，
   而基金会用过之后的反馈会直接改善后面两批。

### 新的锚：基金会的第二批需求

**原文一字不改地记在 [`phase-d.md` 第一节](phase-d.md#一需求原文第二批不要转述)**，
和 [零](#零当前优先级2026-07-29-定) 里第一批的待遇一样 —— 转述会丢东西。

Phase D 期间的判据换成两条，**两条都要过**：

> 1. 它是不是第二批原文里某一句的前置条件？
> 2. 这张表 / 这个字段 / 这个页面，**哪条查询或哪个人会读它**？

第 2 条不是新的 —— 它就是上面那句自查问题。
⚠️ **它在 2026-08-14 第一次被真的执行了一遍，当场砍掉四个字段和一整套排除规则**，
记录在 [`phase-d.md` 第六节](phase-d.md#六自查这一轮砍掉和补上的东西)。
在此之前它被写下来过、引用过，**但没有被执行过**。

---

## 一、终极目标

为一个非营利基金会做一个 web application，帮他们**管理志愿者**并**追踪各类资源（人、钱、活动）**。

### 五个核心诉求，以及分别靠什么落实

| 诉求 | 怎么落实 |
|------|---------|
| 便宜 | 单体 Django 应用 + 托管平台，无付费 SaaS、无微服务、无独立前端；起步阶段月成本控制在几十美元内 |
| 好维护 | 一个人能读完全部代码；标准 Django 写法，不自造框架；有测试所以敢改 |
| 可扩展 | 数据模型抄成熟系统的抽象层次（见 D4 / D5 / D10），加功能是加表，不是改表 |
| 需求变了还能用 | 会变的东西做成**数据**而不是**代码**（关系类型、技能、活动类型都是字典表，在 admin 里加，不用改代码不用迁移） |
| 数据自主且安全 | 标准 Postgres 库，一个 `pg_dump` 就能整体带走；不锁定在任何厂商的专有格式里 |

### 方法论：抄结构，不抄代码

参考 CiviCRM、ERPNext/Frappe 的**数据模型和设计智慧**，业务逻辑和界面全部自己写。

- ✅ 值得学（概念层）：字段设计、状态流转、审计日志、Contact 这类核心抽象
- ✅ 自己写：具体业务流程、界面、报表 —— 这些正是"这个基金会和别人不一样"的地方
- ❌ 别碰：插件系统、多租户、国际化框架、复杂权限引擎 —— 通用产品的负担，一个组织不需要

### 交付策略

先搭最小可用版本（MVP），**在真实使用中摸清基金会到底需要什么**，再逐步扩展。
真实使用暴露的需求比现在猜的准得多，所以每个阶段结束都必须是**可演示**的状态
（能完整跑通一条真实业务流程，哪怕跑在本机、用演示数据），永远不憋大版本。
**能否交给基金会真用是另一条线，前置条件在 Phase C**（2026-07-29 C / D 对调后；原文写的是 Phase D）。

---

## 二、技术选型

| 层 | 选择 | 备注 |
|----|------|------|
| 后端 | Django 5.2 | 自带 ORM / Admin / 认证 / 权限 |
| 数据库 | PostgreSQL | 数据高度关系化，不碰 NoSQL |
| 界面（起步） | Django Admin | 不写前端 |
| 界面（对外，Phase B 起） | Django 模板 + Tailwind + HTMX + Alpine | **2026-07-29 提前**：志愿者不能用 Admin，自助页面进当前阶段（[D21](decisions/D21-self-service-and-permissions.md#d21--对外账号志愿者自助页面提前权限成为它的前置条件2026-07-29)）。原备注"等后端和数据模型稳定了再做"随 [D2](decisions/D02-frontend-deferred.md#d2--前端推迟到后端与数据模型完善之后) 一起部分作废；**不上 React / Vue 这一半仍然成立**。<br>**2026-08-03 补齐**：本行原来只写 HTMX，而 `phase-c.md` 那边只写 Tailwind —— 两份文档各记了一半。三层的分工见 [D24](decisions/D24-htmx-alpine-tailwind.md)，具体写法见 [`design-system.md`](design-system.md) |
| 部署 | **Render**（Web Service + 它的托管 Postgres） | 2026-08-03 定死了是 Render，数据库也用它的。原文写的是「托管平台（Render / Fly.io）+ 独立托管 Postgres」——**「独立」指的是独立于应用进程、能被一个 `pg_dump` 整体带走，不是「必须另找一家」**。判据和代价见 [D3 的 2026-08-03 补](decisions/D03-portable-postgres.md#2026-08-03-补render-的托管-postgres-过不过这一关)。<br>⚠️ **备份不放 Render** —— 库和备份在同一家，平台出事两边一起没。备份走 Cloudflare R2 |

---


---

## 三、重大决策记录 → [`decisions/`](decisions/README.md)

D1–D40 **一条一个文件**，索引见 [`decisions/README.md`](decisions/README.md)。

> **为什么搬走**：D1–D18 原来全部塞在 `<details>` 里，而本文档有几十处链接指向它们内部的小节 ——
> **点过去都是收起状态**。拆开之后每条决策是一页，链接落在正文上。
> 顺带把最容易出错的一类问题也消掉了：一个 3859 行的文件里，
> "改一处忘另一处"几乎必然发生（2026-07-29 晚那次通读自查抓到的 25 条里，大半是这个成因，
> 见 [`revisions.md`](revisions.md)）。
>
> **`goal.md D9` 这种引用照旧成立** —— 编号不变，本文档负责把你导过去。

## 四、当前进度 → [`progress.md`](progress.md) · [`phase-b.md`](phase-b.md)

| 阶段 | 状态 | 详情 |
|---|---|---|
| 数据核心设计（`Contact` / `Language` / `EmergencyContact`） | ✅ 已完成，有测试 | [`progress.md`](progress.md#-已完成--数据核心设计这是目前最有价值的部分) |
| **Phase A · 地基加固**（A1–A10） | ✅ 已完成（2026-07-27，分支 `phase-a`） | [`progress.md`](progress.md#-已完成--phase-a-地基加固) · [`01-roadmap.md`](01-roadmap.md) |
| Phase B · 活动闭环 | 🔄 五处缺口已补齐（C0.2）、**1049 个测试全绿**（2026-08-17 实测），**只差 [C0.3](03-roadmap.md#c03-三角色浏览器验收) 那一遍浏览器验收**就能标 ✅ | [`phase-b.md`](phase-b.md)（要点） · [`02-roadmap.md`](02-roadmap.md)（步骤） · [五处缺口](phase-c.md#phase-b-的五处缺口2026-07-31-发现) |
| Phase C · 上线与真实运营 | 🔄 **当前在做**。2026-08-17：**站点已上线**（Render + 自定义域名 + HTTPS），发信链路打通，前端整份完工；四条交付硬前置里做完一条（生产加固），逐步进度见 [03-roadmap 的进度表](03-roadmap.md#进度2026-08-17-收盘) | [`phase-c.md`](phase-c.md)（要点） · [`03-roadmap.md`](03-roadmap.md)（主册） · [`04-roadmap.md`](04-roadmap.md)（前端分册） · [`progress.md`](progress.md#phase-c--上线与真实运营)（原始计划） |
| Phase D · 员工与排班 | ⬜ 已定案，未开工（2026-08-14 定案 + 同日自查重排） | [`phase-d.md`](phase-d.md)（要点 · 需求原文 · 页面总表） · [D32](decisions/D32-worker-axes-schedule-and-assignment.md)–[D40](decisions/D40-undo-a-pattern-batch.md)（九条决策） · [`05-roadmap.md`](05-roadmap.md)（四批实施步骤） |
| Phase E · 资金追踪 | ⬜ 未开始 | [`progress.md`](progress.md#phase-d--资金追踪) |

> **Phase B 完成的定义**：[零](#零当前优先级2026-07-29-定)里 R1–R8 + P1–P6 全部跑通，
> 扮三个角色各走一遍（[验收清单](phase-b.md#验收2026-07-29-重写改成按-14-条需求逐条验收)）。
> ⚠️ **C / D 在 2026-07-29 对调过**：原来 C 是资金、D 是上线。
> **2026-08-14 又动了一次**：员工与排班插进来当 Phase D，资金追踪顺延成 Phase E。
> 判据和上次一样 —— 排序跟着"哪件事先做"走，不跟着"当初写在哪"走。
> `progress.md` 里那一节仍叫「Phase D · 资金追踪」，指的是现在的 Phase E。

## 五、明确推迟的事 → [`deferred.md`](deferred.md)

记下来是为了不反复纠结。每条都带**重启条件**，手痒之前先去那里看一眼有没有已经想清楚的理由。

## 七、修订记录 → [`revisions.md`](revisions.md)

2026-07-29（按基金会需求重排 + 当晚的通读自查）和 2026-07-28（九轮修订）的完整记录。
**"为什么改口"是这个项目最贵的资产之一**，所以只搬位置，一条不删。

---

## 六、下一步

**Phase A 已完成**（2026-07-27，分支 `phase-a`，A1–A10 全部验收通过）。
**Phase B 的 B0–B5 已完成**（2026-07-28～29，分支 `phase-b`：`core` 时间口径 +
`contact` 三处收口 + `org` 四张表）。

**本文档于 2026-07-29 按基金会给出的一套完整需求重新划定了优先级** ——
见[零、当前优先级](#零当前优先级2026-07-29-定)，以及新增的
[D19](decisions/D19-event-role.md#d19--活动的工种编制-eventrole2026-07-29) / [D20](decisions/D20-ministry-role.md#d20--范围化权限-ministryrole不走-django-group2026-07-29) /
[D21](decisions/D21-self-service-and-permissions.md#d21--对外账号志愿者自助页面提前权限成为它的前置条件2026-07-29) /
[D22](decisions/D22-event-notifications.md#d22--活动变更通知收件人解析是业务逻辑投递是可替换的适配器2026-07-29)。改动清单见文末
[「2026-07-29 修订记录了什么」](revisions.md#七2026-07-29-修订记录了什么)。

**B6–B13 已经做完了**（2026-07-30）：`events` 五张表 + `EventNotification`、
`MinistryRole` + `org/permissions.py`、注册流程、志愿者自助页、ministry admin 侧页面、
活动变更通知、R1–R8 的统计口径、`seed_demo`。
测试从 192 涨到 **334**（2026-07-31 删通用关系表带走了六个测试类，从 363 降下来 ——
下降的口径见 [`phase-c.md`](phase-c.md#测试数基线只增不减的新口径)；
C0.2 和它的返工之后是 **404**），
`check` / `makemigrations --check` / `ruff` 都干净，
12 条 grep 守卫做过双向验证。实测结果见 [`02-roadmap.md` 的收尾那节](02-roadmap.md#自动化部分的实测结果)。

**2026-07-31 更正：那句「B6–B13 已经做完了」说早了。** 按上面 14 条逐条重查代码，
查出**五处缺口，四处是「服务层写好了、没有页面」** ——
改活动、R1、管理侧入口、我的资料页、`RelationshipType` 种子数据。
证据和成因在 [`phase-c.md`](phase-c.md#phase-b-的五处缺口2026-07-31-发现)，
补法在 [`03-roadmap.md` 的 C0.2](03-roadmap.md#c02--补齐-14-条的功能缺口)。

**C0.2 已经做完了**（2026-08-03）：五处缺口全补上，加上浏览器带回的那一轮返工，
测试 **334 → 404**。

**2026-08-17：站点上线了，而且真人已经能收到信。** Phase C 的 C1 / C2（前端整份）、
C0.5、C3.0（域名 + 发信服务）、C3.1（首页）、**C3.2 密码重置**、**C3.3 真实发信**、
C3.4（生产加固）、C3.12 做完并验过；C3.5 部署、C3.6 备份、C3.8 错误可见性、
C6 扫码签到**代码做完，各差一次真实验证**。测试 **404 → 1049**。

⚠️ 发信服务当天从 Amazon SES 换成 **Brevo**（SES 出不了沙箱），
代码零改动 —— 见 [`revisions.md` 第三十九批](revisions.md#三十九2026-08-17发信换成-brevo以及那条没写下来的通知记录)。

**下一步不是写代码**：剩下的四件里三件是[交付硬前置](phase-c.md#判据什么必须做完才能放真人什么可以边用边加)，
而它们**都只能拿真账号、真收件人、真库做** —— 备份的真实恢复演练、权限复核、
隐私说明（内容要基金会定），加上一直悬着的 C0.3 三角色浏览器验收。

**[C0.5](03-roadmap.md#c05--上线前的三条死链) 也做完了**（2026-08-03，分支 `phase-c-frontend`）：
`LOGIN_URL` 补上、三个错误页模板落地、守卫接进 pre-commit 和 CI，测试 **404 → 409**。
⚠️ 其中一条**不在代码里**：CI 的「红灯禁止合并」是 GitHub 的分支保护规则，
要去 Settings → Branches 把 `guards` 设成 required status check，
否则这个 workflow 只是在 PR 上显示一个红叉、照样能合。

所以下一步是 [C0.3](03-roadmap.md#c03-三角色浏览器验收) —— **它等的是人，不是代码**。

照[验收清单](phase-b.md#验收2026-07-29-重写改成按-14-条需求逐条验收)扮三个角色各走一遍。
`python manage.py seed_demo` 一条命令把数据造齐（账号密码在命令的输出里），
清单上大部分勾已经有对应的自动化测试（`events.tests.AcceptanceWalkTests`），
**但浏览器那一遍仍然要走** —— 表单排版坏了、链接指向空处，断言看不出来。
⚠️ C0.2 交付后确实走过一遍浏览器（带回 10 条返工），
**但那不是这一遍** —— 清单上「撤销授权不删行」和「employee 任职结束后从 R8 名单消失」
两条一条都没碰到。

走完再把上面那张表的 Phase B 改成 ✅。

[C1](04-roadmap.md#c1--构建链) 和 [C2](04-roadmap.md#c2--设计系统与-20-个模板) **也做完了**
（2026-08-04，分支 `phase-c-frontend`）：构建链 + 20 个模板重写完毕，测试 409 → **414**。
所以前端不再挡任何事，**C0.3 现在是 Phase B 收尾唯一剩下的一步**。

⚠️ C0.3 那一遍现在走**正好**：模板刚重写过，而它带回来的返工正是落在模板上的。
早一天走，返工就少改一遍。

> **这一轮学到的**：404 个测试全绿，而四个功能没有入口 ——
> 因为**没有 URL 的功能，测试也没有 URL 可打**。
> 以后核对完工，问的不是「service 写了吗」，是「**用户从哪个链接点进去**」。
>
> **2026-08-03 补一句，同一个病的第二次发作**：`LOGIN_URL` 和 403 模板这两处，
> 代码和 URL 都在，缺的是**把它们连起来的那一个设置 / 那一个模板**。
> 所以问题要再往前问一步：不是「用户从哪个链接点进去」，
> 是「**用户点进去之后，看到的是不是我以为的那一页**」。

走之前值得重读的两条：

1. [零](#零当前优先级2026-07-29-定)那 14 条需求**原文**，不是转述。
   转述会丢东西 —— 这一轮最贵的那个发现（R4 靠 `Participation` 反推工种是错的）
   就藏在"每个 event 有多少工种"和"每个工种 volunteers 有多少人"这两句的**并列**里；
2. `02-roadmap.md` 的「计划外记录」新增的三条 —— 实施时才发现的坑，
   其中两条（UTC 那一天、空 Group）是**验收跑成测试之后当场抓出来的**。

### 还没定的（哪些阻塞、哪些不阻塞）

| # | 待定的事 | 等谁 | 阻塞吗 |
|---|---------|------|-----------|
| 1 | 未成年志愿者的同意流程具体长什么样（口头 / 纸质 / 线上签） | 基金会 | **不阻塞** —— `consent_method` 是 `TextChoices`，先放三档，改就是改代码。但 **P3 本身要做**，不能因为流程没定就跳过 |
| 2 | `EmploymentType` 的实际取值 | 基金会 | 不阻塞 —— 正因为不知道才做成字典表，admin 里加行 |
| 3 | `status` 除 `on_leave` / `suspended` 外还需要哪几种 | 基金会 | 不阻塞 —— `TextChoices`，加值就是改代码 |
| 4 | `MinistryRole` 除 admin 之外还需要哪几档（协调员？只读？） | 基金会 | **不阻塞，但要早问** —— 它是 `TextChoices`，加一档就是改代码 + 改 `permissions.py` 的判断。**先只做 `admin` 一档**，需求原文只要求了这一档 |
| 5 | 工时是志愿者自己填还是 admin 填 | 基金会 | **不阻塞** —— 两条路径都走 `services.check_out()` 那一个函数，区别只在哪个页面上有那个按钮。先做 admin 侧（需求原话是"跟 event 同个 ministry 的权限的人可以统计"） |
| ~~6~~ | ~~背景审查有效期多长~~ | — | 随 `BackgroundCheck` 一起移出本阶段，不再需要答复 |
| ~~7~~ | ~~跟不跟踪请假 / 停职~~ | ✅ **已答复（2026-07-28）：跟踪** | 已做进 `Assignment.status` |

> **Phase C 期间拍板的都在 [`phase-c.md`](phase-c.md#2026-07-31-这一轮拍板的不再是待定)** ——
> 2026-07-31 那一轮（时长口径 · R1 谁看 · 界面语言 · 生日能不能自己改 · 改活动走哪条路）
> 和 [2026-08-03 那一轮](phase-c.md#2026-08-03-这一轮拍板的)（前端三件套 · 构建方式 ·
> 四条上线硬前置 · SES / R2 / Sentry · 域名提前 · 隐私说明）。
> 那里还有一张 Phase C 自己的待定表，现在只剩四条（试点选哪个 ministry、
> 合并页开不开放、`MinistryRole` 的档位、基金会内部沟通语言）。

---


---

> 本文件是文件地图和当前优先级。正文各自在 [`decisions/`](decisions/README.md)、
> [`phase-b.md`](phase-b.md)、[`progress.md`](progress.md)、[`deferred.md`](deferred.md)、
> [`revisions.md`](revisions.md) 里。
