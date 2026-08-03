#### Phase B · 活动闭环 —— 当前优先级

> 2026-07-29 重新划定范围。 本阶段原名"人与活动 MVP"，B0–B5（`core` / `contact` /
> `org`）**已完成**；B6 起的部分按[零、当前优先级](goal.md#零当前优先级2026-07-29-定)整个重画了。
>
> 完成的定义现在是：[零](goal.md#零当前优先级2026-07-29-定)里 R1–R8 + P1–P6 全部跑通。
> （原文写的是 `P1–P5`，**漏掉了当日追加的 P6**，而 P6 有自己的验收项和 `EventNotification` 一张表；
> `02-roadmap.md` B 段写的一直是 `P1–P6`，两份文档对"做完了没有"的定义曾经是矛盾的。）
> 原来的验收标准（"能演示一遍基金会的日常"）太模糊，答不出"做完了没有"。
>
> **移出本阶段的**：`VolunteerProfile` / `BackgroundCheck`（原 B7）→ 推迟清单。
> 理由和排除标准见[零的「排除了什么」](goal.md#排除了什么以及它们去哪了)。

已完成部分（B0–B5）：`Ministry` / `EmploymentType` / `Position` / `Assignment` 四张表 +
`core` 的时间口径与 `.active()` + `contact` 三处收口。
**`Position` + `Assignment` 这套结构是 R8 的唯一支撑**，别因为"当前优先级是活动"就以为它跑题了。

剩下要建的表如下。新模型落在哪个 app 见 D17。

| 模型 | app | 字段要点 |
|------|-----|---------|
| ✅ `Ministry` | `org` | **不是纯字典表** —— 基金会的服务单元（食物银行、报税志愿、ESL…）。字段：`code`（唯一·不可改，见 D5）/ `name` / `description` / `is_active` / 成立日期（可空）。行政职能（财务、行政）也是这张表里的行，不另建 `Department` —— 一个组织没必要拆两套单元。**不挂 simple-history**（已确认：改动频率极低，不值得一张历史表）。<br>**这张表不能推迟**，理由见下面「Ministry 视图」 |
| ✅ `Position` | `org` | 编制表 —— 组织架构的骨架，与人无关（见 D11 第二次修订）。`code`（唯一·不可改，见 D5）/ `name`（职务名，给人看）/ `kind`（`TextChoices`：employee·volunteer·board）/ `ministry`(FK，**可空** —— 理事席位没有)/ `reports_to`(自引用 FK → `Position`，可空)/ `is_leader`（布尔，**给代码查**）/ `is_active` / `description`。**挂 simple-history**（组织架构变更必须留痕）。**一个 `Position` 可以有多个在职 `Assignment`** —— 它是编制类型不是座位，所以这张表是几十行量级 |
| ✅ `Assignment` | `org` | 任职表 —— 谁在什么时候占了哪个编制。 `contact`(FK) / `position`(FK → `Position`) / `employment_type`(FK，**可空**) / **`status`（`TextChoices`：active·on_leave·suspended，默认 active）** / `start_date` / `end_date`。**没有 `kind` / `title` / `ministry` / `is_leader` / `reports_to`** —— 全部搬去 `Position` 了。**不加 `is_active`**，但**有 `status`** —— 状态和任期是正交的两个维度，见下面「`Assignment.status`」。**挂 simple-history** |
| ✅ `EmploymentType` | `org` | 字典表：`code`（唯一·不可改）/ `name` / `is_active`。**取值基金会还没定**（全职 / 兼职 / 合同 / 实习只是我们猜的），所以做成字典表而不是 `TextChoices` —— 以后加一行就行，不改代码不写迁移。符合 D5 的判定规则：目前没有任何代码按它分支 |
| `EventType` | `events` | 字典表：`code`（唯一·不可改）/ `name` / `is_active` |
| `Event` | `events` | `name` / `event_type`(FK) / **`ministry`(FK，⚠️ 非空)** / `start_time` / `end_time` / `location` / `owner`(FK → `Contact`) / `status`（`TextChoices`：**draft·open**·confirmed·completed·cancelled）/ `description`。**挂 simple-history**。<br>⚠️ **三处改动（2026-07-29）**：① **`ministry` 从可空改成非空** —— R2 和 R8 都以它为轴，P2 的权限判断也以它为轴，为空就是一场无主、无人有权管的活动；② **`status` 加 `draft` 和 `open`** —— P3 要"看到**发布的** event"，可见性必须有一个明确的闸门，不能靠推断（`open` = 已发布且开放报名）；③ **删掉 `capacity`** —— 被 `EventRole.needed_count` 取代，见 D19。<br>**2026-07-29 晚补一条：可见性不能等于 `status=open`**，见下面「[可见性与生命周期](#可见性与生命周期两个谓词不是一个-status2026-07-29-晚新增)」——否则活动一 `confirmed`，已报名的人就打不开它了 |
| `EventRole` | `events` | 新表（D19）—— 这场活动开了哪些工种、各要几人。 `event`(FK，`CASCADE`) / `role`(FK → `ParticipationRole`，`PROTECT`) / **`needed_count`**（`PositiveIntegerField`，可空 = 不限人数）/ `notes`。<br>**它之于 `Participation` 就是 `Position` 之于 `Assignment`** —— 没人报名的工种照样存在，这正是 R4 / R5 需要它的原因。`needed_count` 就是被推迟过的 `Position.headcount`，但**这次不能推迟**，它是 P2 的原话。<br>约束 `UniqueConstraint(event, role)`、`needed_count IS NULL OR needed_count > 0`。<br>**挂 simple-history**（2026-07-29 晚补上这个结论 —— 原文对它没表态）：`needed_count` 是**对外发布出去的承诺**（"搬运要 5 人"），改了要能追溯，同 `Event` 挂 history 的理由。它是几行/场的小表，成本可忽略 |
| `ParticipationRole` | `events` | 字典表：`code`（唯一·不可改）/ `name` / `is_active`。装的是**一次活动之内**的工种（签到台、搬运、翻译），**≠ `Position.name`**，见下面那条一句话定义。<br>**必须 seed 一行 `code=general`**（"通用志愿者"）—— `Participation.event_role` 非空之后，"没有具体分工"要有地方落。<br>它是 schema 的不变量，所以落在**数据迁移**里，不落在 `seed_demo`（后者拒绝在 `DEBUG` 关掉时运行，只靠它的话生产库起来就缺这一行）。见 `02-roadmap.md` B6 |
| `Participation` | `events` | 报名 / 出勤 / 工时，三件事一行。 `event_role`(FK → `EventRole`，`CASCADE`) / `contact`(FK，`PROTECT`) / `status`（`TextChoices`：registered·attended·absent·cancelled）/ `registered_at` / **`checked_in_at`** / **`checked_out_at`**（都可空 datetime）/ **`hours`**（`Decimal(6,2)`，可空）/ 六个同意字段（见下一行）。<br>⚠️ **改动（2026-07-29）**：**`event` 和 `role` 两个字段没了**，合并成 `event_role` —— 理由（跨表一致性 `CheckConstraint` 管不了）见 D19。唯一约束因此简化成 `(event_role, contact)`，**不再需要 `nulls_distinct=False`**。<br>签到签退是 P4 的"是否来过"。**`hours` 仍然是唯一权威值**，签退时由 `services.check_out()` 写入它 —— 见下面「签到签退与 `hours`」。<br>**挂 simple-history**（2026-07-29 晚补上这个结论 —— 原文对它没表态，而模型表里其它表都表过态）：这张表上有**全系统唯一一个可以手工改写的权威值**（`hours`），而工时将来可能连到奖励。按"钱的记录必须能追溯是谁改的"同一条口径，**谁把 3 小时改成 8 小时必须查得出来** |
| ↳ `Participation` 的同意字段 | `events` | P3 的未成年人同意，不建 `Guardianship`。 `consent_given_by`（文本，姓名）/ `consent_relationship`(FK → `RelationshipType`，可空) / `consent_at`（可空 datetime）/ `consent_method`（`TextChoices`：口头·纸质·线上）/ **`consent_email`**（可空）/ **`consent_phone`**（`PhoneNumberField`，可空）。<br>⚠️ **后两个字段 2026-07-29 晚补，是 P6 的硬前提**：D22 说"未成年人通知家长"，并说系统里已有"两条线通向家长"—— 而 `consent_given_by` **只是一个姓名，没有任何联系方式**，那条线是断的。不补的话未成年人只能靠 `EmergencyContact.phone`（只有电话、没有邮箱），而默认投递后端是邮件 ⇒ D22 自己认定"最需要被通知的那群人"会全部落进 `unreachable`。<br>**为什么不是 `Guardianship` 表**：需求要的是"**这一次活动**家长同意了"，那是一条**事件记录**；`Guardianship` 是"谁是小明的法定监护人"，那是一段**长期关系**。两者形状不同，先做需求要的那个。`Guardianship` 继续留在推迟清单。<br>规则：未成年人没有同意记录（`consent_at` 为空）就报不了名 —— `Participation` 根本建不出来，也不能被 `check_in()` 标成 `attended`。<br>**2026-07-29 晚更正**：原文写的是"不能进 `confirmed`/`attended`"，**而 `confirmed` 不是 `Participation` 的状态**（这张表只有 registered·attended·absent·cancelled），它是 **`Event.Status`** 的一档（"人齐了，不再收报名"）。一条 P3 的核心规则被写在了另一张表的字段上，照着实现会写出一个永远不触发的判断。<br>**同一条规则还要求 `consent_email` / `consent_phone` 至少填一个** —— 否则同意收了、P6 却通知不到家长。<br>落点：两条都是跨表判断（年龄在 `Contact` 上），`CheckConstraint` 表达不了，落在 `events/services.py::sign_up()` 里，按 D14 记为提示层，不假装它是强制的。<br>2026-07-31 补：后半句（"不能被 `check_in()` 标成 `attended`"）当时**只写在文档里，代码里没有** —— 现在落在 `events/services.py::_mark_attended()`，通往 `attended` 的三条路（`check_in` / `check_out` / `record_hours`）都走它。一条规则有三个入口一个岗哨，就是有两条路绕过去。见 `02-roadmap.md`「计划外（三方核对）」。<br>2026-07-30 更正：本文档原来三处写的是 `register()`，而 `02-roadmap.md` B9 一直写的是 `sign_up()` 并给了签名 —— 同 `ministry_ids_administered_by` 那次，两份文档给同一个函数起了两个名字。统一成 `sign_up()`：`register()` 和 `accounts/services.py::register_account()` 只差一个词，而那两件事毫无关系 |
| `EventNotification` | `events` | 新表（D22）—— 活动变更通知的留痕。 `event`(FK，`CASCADE`) / `reason`（`TextChoices`：time_changed·location_changed·cancelled·other）/ `message`（正文**快照**）/ `sent_at` / `sent_by`(FK → `User`，`SET_NULL`) / `recipients`(M2M → `Participation`) / **`unreachable`**(M2M → `Participation`，`related_name="notifications_unreachable"`) / `provider_ref`（可空文本，对账用）。<br>**两个 M2M 都是快照，不能事后重算** —— 当时联系不上不等于今天联系不上，重算会把历史记录改成"当时全通知到了"。同 `hours` 是权威值那条。<br>**2026-07-29 晚从 `unreachable_count`（整数）改成 M2M** —— 只存计数的话事后答不出"是哪几个人"，而 D22 ② 要的恰恰是这个。见 D22。<br>**不挂 simple-history** —— 它本身就是一条不可变的事件记录，改它就是伪造 |
| 投递适配器 | `core/notifications/` | **不是表** —— 一个 `NotificationBackend` 协议 + 四个实现（console 开发默认 / locmem 测试 / django_email 兜底 / novu），走 `settings.NOTIFICATION_BACKEND`。<br>⚠️ **后端只认（地址, 渠道, 内容），不认 `Contact` / `Participation` / "未成年人"** —— 一旦让它知道这些，换 provider 就要把业务规则重写一遍。见 D22 |
| `MinistryRole` | `org` | 新表（D20）—— "某人在某 ministry 有 admin 权限"。 `contact`(FK，`PROTECT`) / `ministry`(FK，**`PROTECT`** —— 2026-07-29 晚从 `CASCADE` 改，见 `on_delete` 表) / `role`（`TextChoices`：`admin`，**外加一个 `coordinator` 占位** —— **本阶段没有任何代码按它分支**，等基金会答复再决定要不要用，见[六·4](goal.md#还没定的哪些阻塞哪些不阻塞)）/ `start_date` / `end_date` / `granted_by`(FK → `User`，可空，`SET_NULL`)。**挂 simple-history**（授权变更必须留痕）。<br>复用 `core` 的 `DateRangeMixin.active()` —— 授权也有起止，同 `Assignment`。<br>**放 `org` 不放 `accounts`**：它的主语是 ministry（D17：一个 app 一个业务领域），且 `accounts` 只该装"能不能登录"这一层 |
| `accounts` 的注册流程 | `accounts` | **不是新表** —— `User.contact` 已经有了，**保持可空**（superuser 没有对应真人，D12）。P1 落在 `accounts/services.py::register_account()`：一次事务里建 `User` + 建 `Contact` + 挂上。<br>**流程约束，不是字段约束** —— 理由见 D21 第 3 条（它是 D9 的一个反例，因为这条规则有合法例外） |
| ~~`VolunteerProfile`~~ / ~~`BackgroundCheck`~~ | — | 移出本阶段（2026-07-29），见推迟清单。14 条需求一条都没碰技能、可服务时段、背景审查。<br>⚠️ **但 D18 那条"背景审查必须独立成模型"的决定不撤销** —— 将来建的时候仍然是两个模型，不是一个 |
| `EmergencyContact` | `contact` | **专用表**（2026-07-28 定案，取代早先的 `Contact.emergency_contact` 自引用 FK + `is_reference_only`）。`person`(FK → `Contact`，`CASCADE`，`related_name="emergency_contacts"`) / `name`（文本，必填）/ `phone`（`PhoneNumberField`，必填）/ `relationship_type`(FK → `RelationshipType`，**非空**，`limit_choices_to={"usable_as_emergency_contact": True}`)。<br>**姓名电话存文本，不指向 `Contact`** —— 紧急联系人可能是邻居、室友，不是与基金会交互的主体，不该占一行 `Contact`（D15 第四条判据）。<br>**表天然支持一人多个紧急联系人**，不加人为的唯一限制（基金会目前只需要一个）。见 D15、下面「紧急联系人的录入」 |
| 合并重复 `Contact`（`contact/services.py::merge_contacts()`） | `contact` | 不是新表，是一个功能**：把重复的两条 Contact 合并成一条。**落在 `services.py` 不落在 model 上** —— 跨表写入按 D18 落点表归 `services.py`。范围和实现见下面「合并重复记录」。<br>⚠️ **它进 Phase B 的原始理由已经失效** —— 当时是"reference-only 记录会持续制造重复"，而 reference-only 已经不存在了。**保留在本阶段的新理由**：跨渠道录入（活动签到、志愿者自荐、员工代录）仍然会产生重复，而同名同号硬拦截只挡得住同一表单里的手滑。**如果要削减 Phase B 范围，这是第一个候选** |
| `RelationshipType` 加三个字段 | `contact` | `code`（唯一·不可改，见 D5）+ `is_symmetric`（布尔，见 D15）+ `usable_as_emergency_contact`（布尔，默认 `False` —— 它是 `EmergencyContact` 的前置）。加到**有数据**的表要三步迁移，见下面「`code` 的三步迁移」；**本机这张表实测 0 行，所以 `02-roadmap.md` B2 一步到位** |
| ~~`Guardianship`~~ | — | **移出 Phase B**（2026-07-28 决定，2026-07-29 需求原文答复"有同意流程"后**仍然不建**，见 [D15 的已答复那节](decisions/D15-relationship-carriers.md#-已答复2026-07-29有同意流程但仍然不建-guardianship)）—— 需求要的是"这一次活动家长同意了吗"，那是一条事件记录，落在上面那六个同意字段上；`Guardianship` 是"谁是他的法定监护人"，那是一段长期关系。<br>⚠️ 原文写的是"家长通知已经靠 `is_minor` + `EmergencyContact` 闭环了"，**那句话被 D22 推翻了一半**：`EmergencyContact` 只有电话没有邮箱，默认后端是邮件。现在是 `consent_email` / `consent_phone` 优先、`EmergencyContact` 回落走 SMS。见推迟清单 |
| ~~`Skill`~~ | — | **推迟**（见推迟清单）—— 没有任何东西依赖它，ministry 视图不需要它 |

##### 本阶段内部的硬性顺序

> 2026-07-29 重排。 原来的两条（`RelationshipType.code` / `Contact.__str__` 消歧）
> **已经在 B2 / B4 做完了**，留在这里作记录。当前的顺序是下面这条。

`MinistryRole` + `org/permissions.py` 必须先于任何自助页面。

理由见 [D21](decisions/D21-self-service-and-permissions.md#d21--对外账号志愿者自助页面提前权限成为它的前置条件2026-07-29)：先写页面后加权限，
等于中间有一段时间任何登录用户都能看到所有人的资料 —— 而库里有未成年人的
姓名、生日、地址和紧急联系电话。这不是排期偏好，是同一个功能的两个必需零件。

其余按依赖排：

```
EventType / ParticipationRole（字典表）
  └→ Event → EventRole → Participation        （R1–R7 的数据基础）
MinistryRole → permissions.py                  （P2 / P4 / P5 的判断）
  └→ accounts 注册流程                          （P1）
      └→ 自助页面（列表 / 报名 / 签到 / 统计）    （P3 / P4）
```

**`EventRole` 必须先于 `Participation`** —— 后者只有 `event_role` 一个业务外键，
没有它就是一张空壳。**同 `Position` 必须先于 `Assignment`，是同一个形状**（见 D19）。

具体步骤见 **`02-roadmap.md`**（B6–B13）。

##### 一人一活动多角色（2026-07-29 改挂 `EventRole`）

同一场活动里，一个人可能上午搬运、下午在签到台，两段做的事不同、时长不同。
合并成一行会把"区分它们所需的维度"永久丢掉（只剩一个总工时和一个角色，事后拆不回来）。
所以**一人一活动可以有多行 `Participation`，靠工种区分** —— 这条决定不变。

变的是唯一约束的形状：`UniqueConstraint(event_role, contact)`。

> 2026-07-28 原方案是 `UniqueConstraint(event, contact, role)` + `nulls_distinct=False`。
> D19 把 `event` 和 `role` 合并成 `event_role` 之后，这条约束**自动**变成两列且两列都非空 ——
> `nulls_distinct=False` 不再需要，因为没有 null 了。
>
> **又一次印证了 `Assignment` 那条**："约束越加越长往往是模型没拆干净的症状"。
> 这是本项目第二次靠拆表把约束缩短，第一次是拆 `Position`
> （`(contact, ministry, kind, title, start_date)` → `(contact, position, start_date)`）。

- 同一人 + 同一活动 + **同一工种** 的第二行 → 拒绝（防手滑重复录入）
- 同一人 + 同一活动 + **不同工种** → 放行（两行 `Participation` 指向两行 `EventRole`）
- **"没有具体分工"落在 `code=general` 那行 `EventRole` 上**，不再靠 `role=NULL` 表达

**不建 `Shift` 表**（考虑过，否决）。行业里的标准结构确实是三层
（Salesforce V4S 的 Job → Shift → Hours、VolunteerHub 的 Event → Opportunity → Signup），
但**多班次的情况一律拆成多个 `Event`**，用不着第三层：

- 拆成多个 Event 之后，时段差异由 Event 自己的起止时间表达，做的事差异由 `role` 表达，
  工时差异由每行的 `hours` 表达 —— 三个维度一个不少；
- 少一张表、少一层 admin 嵌套，而 MVP 阶段的活动规模撑得住。

**代价（记下来免得以后当 bug 讨论）**：同一天的"上午场 / 下午场"在统计里是**两场活动**。
真需要把它们归成一次时，按 D15 的三条件检验（最多一个父、无独立属性、只有一种类型），
`Event.parent` 自引用 FK 正是对的载体 —— 见推迟清单。

##### `.active()` 与时间口径

`Assignment` 和 `MinistryRole` 共用同一套「在职 / 生效中」的派生逻辑，
**定义只写一处**，放在 `core` 里做成 QuerySet mixin：

```python
def active(self, on=None):
    on = on or local_today()          # core.timeutils，见 D16
    return self.filter(
        (Q(start_date__isnull=True) | Q(start_date__lte=on))
        & (Q(end_date__isnull=True) | Q(end_date__gte=on))
    )
```

两个都不能少：

- `start_date` 那一半不能漏。 只写 `end_date` 的话，一个 `start_date=2027-01-01`、
  没有结束日期的任职**今天就算在职** —— 预录下季度上岗的志愿者，ministry 页面今天就把人算进去了，
  而且不报错，只是人数悄悄多了。
- `on` 必须在调用时求值。 写成 `def active(self, on=local_today())` 是经典的
  进程启动时冻结 bug，gunicorn worker 上会越跑越错。参数化顺带让"查某一天的在职名单"和
  测试边界都变成免费的。

`local_today()` 的时区口径见 **D16** —— 那条是硬性的，`timezone.now().date()` 会错一天。

> ⚠️ `.active()` 只管日期，不管状态。 `Assignment` 另有一个 `.serving()`
> （= `.active()` AND `status=active`），请假 / 停职的人在 `.active()` 里**仍然算数**。
> 两个都对，用哪个取决于问题是"他还属不属于这个团队"还是"他今天能不能当值" ——
> 见下面「`Assignment.status`」。**`MinistryRole` 只有 `.active()`**，一次授权不会被停职。

顺带：显示姓名时记得 `select_related("contact")`，否则每行一次查询（N+1）。

##### 新表的约束必须和表同期落地（延续 A7 的教训）

A7 的原话是"等表里有了真数据再加，就得先清洗存量数据"。下面这些**不是**以后再补的优化项：

| 表 | 约束 | 不加会怎样 |
|---|---|---|
| `Participation` | `UniqueConstraint(event_role, contact)` | 同一人同一活动同一工种能登记 10 次，**工时统计直接错** —— 而工时是这张表的全部价值。<br>**2026-07-29：不再需要 `nulls_distinct=False`** —— 两列都非空了（D19 把 `event` + `role` 合并成 `event_role`） |
| `Participation.hours` | `DecimalField`（**不是 `Float`**）+ `hours IS NULL OR hours >= 0` | 对钱写过"永远不用 `FloatField`"，工时同理（浮点累加会飘）；还能存出负工时。**`null=True`**：报名了还没发生 ≠ 干了 0 小时 |
| `Participation` | `status = 'attended' OR hours IS NULL OR hours = 0` | 否则能存出 `status=缺席` + `hours=5`。这和 `is_active=True` + `end_date=2020` 是**同一种病**，见下面「单一真相」 |
| `Participation` | `checked_out_at IS NULL OR checked_in_at IS NULL OR checked_out_at >= checked_in_at` | 签退早于签到。同 `end_date >= start_date`，新表重钉一遍 |
| `Participation` | `checked_in_at IS NULL OR status <> 'absent'` | 签到了又标记缺席。**单一真相** —— "是否来过"（P4）只能有一个答案 |
| `EventRole` | `UniqueConstraint(event, role)` | 同一场活动把同一个工种开两遍，`needed_count` 从此有两个答案，R4 直接翻倍 |
| `EventRole` | `needed_count IS NULL OR needed_count > 0` | 需要 0 人的工种没有意义。同原 `Event.capacity` 那条 |
| `MinistryRole` | `UniqueConstraint(contact, ministry, role, start_date)`，**带 `nulls_distinct=False`** | 同一个人在同一个 ministry 的同一角色授两遍。`start_date` 可空且留空常见 —— 同 `Assignment` 的教训 |
| `MinistryRole` | `end_date >= start_date` | 同 `Assignment`。新表漏掉就不一致了 |
| ~~**`EventNotification`**~~ | ~~`unreachable_count >= 0`~~ | **2026-07-29 晚删除** —— 字段本身没了，`unreachable` 改成 M2M（见 D22），负数这种状态不存在了。<br>⚠️ 顺带记一笔：**这条约束原本就没什么用** —— 一个 `PositiveIntegerField` 本来就挡住了负数，它是"便宜就加"进来的，不是"漏了会出事"进来的。同 `TIME_ZONE` 那条"靠顺手进来的" |
| `EventNotification` | `Index(fields=["event", "-sent_at"])` | "这场活动通知过几次、最近一次什么时候" —— 发送前的二次确认页要显示它（防重复发送的唯一缓解） |
| `Assignment` | `end_date >= start_date` | 凡是带起止日期的表都必须有这条，新表漏掉就不一致了 |
| `Assignment` | `UniqueConstraint(contact, position, start_date)`，**带 `nulls_distinct=False`** | 见下面「`Assignment` 的唯一约束」 |
| `Position` | `reports_to` 不能指向自己那一行（`CheckConstraint`） | 见下面「汇报线的环」 |
| `Position` | `UniqueConstraint(Lower("code"))`（**不是**字段上的 `unique=True`） | 同下面字典表那条。`Position` 不是字典表，但 `code` 的作用一样：代码只认 `code`，不认 `name` |
| `Event` | `end_time >= start_time` | 同上 |
| ~~`Event`~~ | ~~`capacity IS NULL OR capacity > 0`~~ | **2026-07-29 删除** —— `capacity` 字段本身没了，被 `EventRole.needed_count` 取代（D19） |
| `EmergencyContact` | `UniqueConstraint(person, Lower(Trim(name)), phone)` | 同一个人身上把同一个紧急联系人录两遍。归一化写进表达式，不靠 `save()` —— D9 归一化通则 |
| `EmergencyContact` | `relationship_type` **FK 非空** | 记了联系人就必须写清关系。 拆成专用表之后这条从 `CheckConstraint` 降级成一个 `null=False`，是拆表白捡的简化 |
| `Ministry` / `Position` / `EmploymentType` / `EventType` / `ParticipationRole` | `UniqueConstraint(Lower("code"))`（不是 `unique=True`）<br>⚠️ `EventRole` / `Participation` / `MinistryRole` **没有 `code`** —— 它们不是字典表，是业务记录，锚点是外键组合 | 见 D5：不唯一的 `code` 不是锚点，`get(code=...)` 会抛 `MultipleObjectsReturned`。**必须是 `Lower()` 版**，否则 `bulk_create` 能塞进 `Food_Pantry` + `food_pantry` 两行 —— D9 归一化通则 |
| `RelationshipType` | `UniqueConstraint(Lower(Trim("name_a_to_b")))` | 缺口 2。`Trim` 不能省，理由同上 |

按 D14：每条约束配 `violation_error_message` + `violation_error_code`，在 `CONSTRAINT_FIELD` 里登记一条映射，**不要再写一遍 `clean()`**。
守卫测试会检查有没有漏登记；另外每条都要实测在表单里是不是真会报错（见 D14 的坑）。

##### `Assignment` 的唯一约束：一人多岗现在是天然的

`UniqueConstraint(contact, position, start_date)` + `nulls_distinct=False`。

> 这一节在 2026-07-28 大幅简化了。 原来的约束是
> `(contact, ministry, kind, title, start_date)`，还专门论证过"为什么必须带 `title`" ——
> 因为张三在食物银行同时当"志愿者协调员"和"库存管理"时，不带 `title` 的话第二行会被误杀。
> **拆出 `Position` 之后这个论证整段作废**：两个职务本来就是两个 `Position`，
> `(contact, position, start_date)` 天然放行，不需要靠往约束里塞字段来补救。
> 记在这里是因为**"约束越加越长"往往是模型没拆干净的症状** —— 这次它是。

三种情形分别是：

- 同一人 + **不同 `Position`** → 放行（一人多岗，D11 的核心场景）
- 同一人 + 同一 `Position` + **不同 `start_date`** → 放行（离开又回来，两段任职）
- 同一人 + 同一 `Position` + 同一 `start_date` → 拦住（真手滑）

**`nulls_distinct=False` 不能省**：`start_date` 可空，而留空恰恰常见，
Postgres 默认 `NULL != NULL`，不加就形同虚设 —— 同 A7 的教训。

**`title` 的归一化搬到了 `Position.name`**（`save()` 里 strip + 连续空白压成一个）。
理由不变（`"库存管理"` 和 `"库存管理 "` 在数据库看来是两个值），
但现在只需要在**几十行的编制表**上做一次，而不是在每一行任职记录上做。
`Position` 的代码锚点是 `code` 不是 `name`，所以 `name` **不加**唯一约束 ——
两个 ministry 各有一个"协调员"是合法的，靠 `__str__` 带上 ministry 消歧（同 `Contact` 重名的口径）。

##### 空缺编制：这次修订的验收点

**"编制存在但没人在任"是拆出 `Position` 的首要理由**，所以它必须是一个能查、能看见的一等状态，
而不是"碰巧查不到人"的副作用。

```python
class PositionQuerySet(models.QuerySet):
    def vacant(self, on=None):
        """还设着、但当天没有任何在职任职的编制。"""
        on = on or local_today()                       # core.timeutils，见 D16
        return self.filter(is_active=True).exclude(
            id__in=Assignment.objects.active(on=on).values("position_id")
        )
```

三件事：

1. **`Position.is_active` 和「空缺」是两个概念**，同 `Contact.is_active` 和「在职」那条。
   `is_active=False` = **这个编制撤销了**（不再招人）；空缺 = 编制还在、暂时没人。
   撤销的编制不该出现在空缺列表里，所以 `vacant()` 里那个 `filter(is_active=True)` 不能省。
2. **`vacant()` 也要参数化时钟**（`on=None`），理由同 `.active()` —— 见 D16 第 2 层。
   顺带白捡"某一天有哪些编制空着"。
3. **ministry 页面必须显示空缺**（见下面「Ministry 视图」）。
   看不见的空缺等于没建这张表 —— 那正是修订前的状态。

不加 `headcount`（编制人数）字段。 一个 `Position` 可以有多个在职 `Assignment`，
所以"3 个坑填了 2 个"这种半空状态现在表达不了 —— `vacant()` 只认"一个人都没有"。
真需要时加一个整数字段即可，不改结构，见推迟清单。

> ⚠️ 但"数人"这件事 B5 已经做了，本节原来漏记，2026-07-29 晚补上。
> `PositionQuerySet` 除 `vacant()` 外还有 **`with_headcounts(on=None)`** 和 **`retired()`**：
>
> ```python
> Position.objects.with_headcounts()      # COUNT(...) FILTER (WHERE ...) 条件聚合，
>                                         # 一次查询给出每个编制的在任 / 在岗人数
> Position.objects.retired()              # is_active=False —— 第三种状态，必须看得见
> ```
>
> **`with_headcounts()` 和被推迟的 `headcount` 字段是两回事，别搞混**：
> 前者是**算出来的在任人数**（annotation，已落地）；后者是**编制该有几个人**（计划值，没建）。
> **"缺几个人"要两者相减，所以现在算不出来** —— 这才是推迟清单那一行的准确含义。
>
> 做成 annotation 不做成 property，理由和 `EventRole.with_signup_counts()` 一字不差：
> 前端要能排序、过滤、分页、直接序列化，而 property 四样都做不到、还是 N+1。
> **`understaffed()` 就是照它抄的**（见 [D19](decisions/D19-event-role.md#d19--活动的工种编制-eventrole2026-07-29) 的类比表）。
> 另外 `retired()` 的来历值得记：它是 B5 复盘那条
> **"不要用补集定义状态 —— 把所有状态列出来数一数，超过两种补集就是错的"** 的产物，
> 而这条教训 2026-07-29 晚在 `Event.status` 上又救了一次（见[可见性与生命周期](#可见性与生命周期两个谓词不是一个-status2026-07-29-晚新增)）。

##### 汇报线的环（2026-07-28 第九轮修订：第二道防线从纪律改成结构）

`CheckConstraint` 只挡得住深度 1（`reports_to` 指向自己那一行）。
A 的上级是 B、B 的上级是 A 是两次各自合法的插入，数据库用 CHECK 表达不了跨行环路。

后果不是脏数据而是挂死：任何递归走 `reports_to` 的代码（Phase C 的组织架构图、
ministry 页面）遇到环就是无限循环或 `RecursionError`。

两道防线，都要做：

1. **`Position.clean()` 向上走链**（带 `visited` 集合、限深 20）拒绝成环。
   按 D14 的标准这**只是提示层** —— `bulk_create` 和 `queryset.update()` 绕得过去，
   是一个已知的不完美，不粉饰。
2. 全项目只有一处遍历汇报链：`org/services.py` 的 `build_org_tree()`。
   它一次查询取全表、在内存里建树，`visited` 集合和深度上限都封在里面；
   调用方拿到的已经是一棵树，**不需要知道"数据可能有环"这回事**。
   配一条 grep 守卫测试盯着，见下面。

> 第 2 条原文是「所有遍历汇报链的代码一律带 `visited` 兜底，这条写进 `Position` 的 docstring」。
> 那是**纪律性保障** —— 和本文档已经判过刑的 `Contact.objects.people()`
> （"靠所有人每次都记得调用"，见 D15 第六轮）是同一种东西，
> 而 B4.2 的结论是**纪律性保障弱于结构性保障**。同一条标准这里没执行，现在补上。
>
> 顺带修掉的是同一段代码的另一个毛病：**逐级 `position.reports_to` 往上取就是 N+1**。
> `Position` 是几十行的表，一次 `list(Position.objects.select_related("ministry"))`
> 取回全表在 Python 里建树，**一次查询、零 N+1**，比任何递归查询都快 ——
> 这两个问题的解法本来就是同一个函数。

```python
# org/services.py —— D18 落点：永久资产，Phase C 的组织架构图 import 同一个函数
def build_org_tree(positions=None):
    """一次查询取全表，在内存里建树。全项目唯一一处遍历汇报链的代码。

    ⚠️ 环的兜底在这里，不在调用方 —— 见 goal.md「汇报线的环」。
    数据有环时不挂死：成环的那一支挂到根上，并记一条 warning 日志。
    ⚠️ 不要绕过它自己递归 reports_to，core/tests.py 的守卫测试会变红。
    """
```

> 好消息：环的风险比修订前小了一个量级。 环现在只可能出现在几十行的编制表里，
> 而不是几百行、每次招人都新增一行的任职表里；而且组织架构改动频率远低于人员变动。
> 防线照做，但它从"迟早会踩"降级成了"基本不会踩"。

###### 为什么不上 Postgres 的 LTREE 扩展（2026-07-28 评审建议，未采纳）

一轮外部评审建议把邻接表（Adjacency List）换成 Postgres 原生的 `LTREE` 物化路径，
理由是"关系型数据库处理层级树非常低效、应用层递归会 N+1、手动维护 `visited` 不是标准做法"。
**N+1 那一句是对的**（就是上面第 2 条修掉的东西），**其余三条在本项目不成立**：

| 反对理由 | 说明 |
|---|---|
| 量级不对 | LTREE 的 GiST 索引在 10⁵–10⁶ 节点的树上确实碾压。`Position` 是**几十行**（一个编制多人在任，见上面「`Position` 是编制类型不是座位」）。几十行取全表建树是一次查询，LTREE 也得取这几十行 —— 换不来任何东西。另：Postgres 从 8.4 起就有 `WITH RECURSIVE`，"关系型数据库处理层级树非常低效"这句本身已经过时 |
| 踩 D9 归一化通则 | LTREE 的 path 必须**在应用层维护**：改一个 `reports_to` 要重写整棵子树的 path。这完全依赖 `save()`，而 `bulk_create` 已确认是常态写入路径 —— 一条 SQL 就能插出 `a.b.c` 而 `a.b` 根本不存在的树。判定方法（D9）："不经过 `save()` 直接写这两行，数据库会不会拒？"答案是不会，**比现在更糟** |
| 丢掉外键完整性 | path 是字符串，不是外键。`reports_to` 的 `PROTECT` 直接消失 —— 而上面那张 `on_delete` 表花了整整一段论证"这里为什么连 `SET_NULL` 都不行，因为它会把子树静默变成根"。LTREE 下删一行父编制，子树的 path 指向一个不存在的祖先，**比 `SET_NULL` 还静默** |
| 或者踩 D11 的原罪 | 想同时保住外键完整性，就得 `reports_to` FK **和** path 并存 —— 那正是 D11 初版被批评的"同一件事记两个地方"（"不是两处都能记，是只有一处能记"），且两者不同步时没有任何机制会告诉你 |

外加一条：Django 没有原生 LTREE 字段，要么装小众第三方包、要么自己写 `Field` 子类，
撞上"**好维护：一个人能读完全部代码；标准 Django 写法，不自造框架**"。
D8 拒绝 `django-languages-plus` 是因为那个包**装不下**需求；这里反过来 —— **包比需求大**。

**重启条件见推迟清单** —— 而且到那时第一选择也不是 LTREE。

##### 外键的 `on_delete` 一律显式指定

Phase B 一次加十几个外键，其中一个选错是灾难级的：

| 外键 | `on_delete` | 理由 |
|---|---|---|
| `Assignment.contact` | **`PROTECT`**（2026-07-30 从 `CASCADE` 改） | 删一个人不该静默带走他的全部任职历史。<br>原方案是 `CASCADE`，理由写的是"人的档案删了，任职记录没有意义"。**那句话对调到 `MinistryRole.contact` 上同样通顺**（人删了授权也没意义），而那一格选的是 `PROTECT`、理由是"要留痕的事不能挂 `CASCADE`"—— 同一条判据这里没执行过，正是 `MinistryRole.ministry` 那次翻案用的那条。<br>另外两条：① `Assignment` **挂着 simple-history**，声称"组织架构变更必须留痕"；② 它是 **R8 的唯一支撑**。<br>**暴露面**：有 `Participation` 的人本来就被 `PROTECT` 挡着，所以漏的是**只做过员工、没做过志愿者的人** —— 那恰恰是任职历史最长的那批。<br>**代价**：真要删档案得先处理任职行，一年遇不上一次，而那正是应该被迫看见的事（同 `MinistryRole.ministry`）。停用走 `is_active`，不做软删除 |
| `Assignment.position` | `PROTECT` | ⚠️ 写成 `CASCADE` 的话，删一个编制 → **占过它的所有人的任职历史一起消失**。同 `Participation.contact` 的道理 |
| `Assignment.employment_type` | `PROTECT` | 字典表，同 `Contact.preferred_language` |
| `Position.ministry` | `PROTECT` | 删 ministry 不该静默带走编制 |
| `Position.reports_to` | `PROTECT` | ⚠️ `CASCADE` 是灾难（删一个编制带走整棵下属子树）；但这里**也不用 `SET_NULL`** —— 那会把一整棵子树**静默地**变成组织架构图的根，看不出出过事。`PROTECT` 强迫你先把下属改挂到别处，是唯一会让你注意到的选项 |
| `Event.event_type` / `Event.ministry` / `Event.owner` | `PROTECT` | `CASCADE` 会让删一个人带走整场活动 |
| `EventRole.event` | `CASCADE` | 活动删了，它开的工种没有意义（同 `Participation.event` 原来那条） |
| `EventRole.role` | `PROTECT` | 字典表，同 `Contact.preferred_language` |
| `Participation.event_role` | `CASCADE` | 工种删了，报它的记录没有意义。<br>⚠️ 这条链要看清楚：删 `Event` → 级联删 `EventRole` → 级联删 `Participation`，工时历史一起没。 和原来"删 `Event` 直接带走 `Participation`"的风险等价，不是新增的 —— 但两级级联更不显眼，所以**删活动这个动作在 admin 里不给普通 Group**（见 D21） |
| `Participation.contact` | `PROTECT` | `CASCADE` 会让删一个联系人抹掉全部工时历史 —— R6 / R7 统计的基础 |
| `Participation.consent_relationship` | `PROTECT` | 字典表 |
| `EventNotification.event` | `CASCADE` | 活动删了，"通知过这场活动的人"没有意义 |
| `EventNotification.sent_by` | `SET_NULL` | 发通知的人离职、账号删了，**这条通知记录必须还在**。同 `MinistryRole.granted_by` 的理由 —— 留痕类的字段一律不能 `CASCADE` |
| `EventNotification.recipients` / `.unreachable`（两个 M2M） | — | M2M 中间表默认随任一端删除而清理。⚠️ **这意味着删一个 `Participation` 会把它从历史通知记录里抹掉** —— 可接受，因为 `Participation.contact` 是 `PROTECT`，有活动记录的人本来就删不掉 |
| `MinistryRole.contact` | `PROTECT` | 删一个人不该静默撤掉他的授权记录 —— 授权是要留痕的事（同 `granted_by`） |
| `MinistryRole.ministry` | **`PROTECT`**（2026-07-29 晚从 `CASCADE` 改） | 和 `Position.ministry`、`MinistryRole.contact` 一致。<br>原方案是 `CASCADE`，理由写的是"食物银行不存在之后这条授权没有意义"。那条理由不成立 —— 因为**把它对调到 `contact` 上也同样通顺**（人删了授权也没意义），而 `contact` 那一格选的是 `PROTECT`，理由恰恰是"**授权是要留痕的事**"。同一张表上两个外键用互相矛盾的理由，说明其中一个是事后合理化的。<br>另外两条：① `Ministry` **有 `is_active`**，撤销走停用、几乎不删（同 `Position` 那条论证），`CASCADE` 想防的场景本来就不该发生；② 这张表**挂着 simple-history**，声称"授权变更必须留痕"，却允许删一个 ministry 静默带走一批授权行 —— 自相矛盾。<br>**`PROTECT` 的代价**：真要删 ministry 得先把它下面的授权行结束掉（填 `end_date`）——一年遇不上一次，而且那正是应该被迫看见的事 |
| `MinistryRole.granted_by` | `SET_NULL` | 授权人的账号被删，**授权本身必须还在**（`CASCADE` 会连锁撤销一批人的权限，是灾难级）。留 `NULL` 读作"授权人已注销"，比整行消失强 |
| ~~`VolunteerProfile.contact`~~ | — | 随 `VolunteerProfile` 一起移出本阶段 |

> `reports_to` 从 `SET_NULL` 改成 `PROTECT`（2026-07-28）。 修订前它挂在 `Assignment` 上，
> 而任职记录是**会被删的**（录错了就删重录），`PROTECT` 会天天挡路，所以选了 `SET_NULL`。
> 挂到 `Position` 上之后前提变了：编制**几乎不删**，撤销走 `is_active=False`。
> `PROTECT` 的摩擦一年遇不上一次，换来的是子树不会被静默重挂 —— 现在这笔账划得来。
> **同一个 `on_delete` 在不同的表上是不同的选择**，这就是例子。

**连带效果（是特性不是 bug）**：`Participation.contact` 和 `Assignment.contact`
都用 `PROTECT` 之后，**有过活动记录或任职记录的联系人就删不掉了**，只能 `is_active=False` 停用。
这与推迟清单里"不做软删除、`is_active` 已覆盖停用语义"是一致的。

> 2026-07-30 补：`Assignment.contact` 原来是 `CASCADE`，所以这条"连带效果"当时是**半条** ——
> 只做过员工、没做过志愿者的人不在保护范围里。现在三张挂在 `Contact` 上的业务表
> （`Participation` / `Assignment` / `MinistryRole`）口径统一了。

##### 索引

| 索引 | 服务什么查询 |
|---|---|
| `Assignment`：`Index(fields=["position", "status", "end_date"])` | ministry 页面的第二段："这些编制上现在谁在当值"。`.serving()` 三个条件（编制 + 状态 + 日期）一次覆盖；`.active()` 走最左两列里的 `position` 也够用 |
| `Position`：`Index(fields=["ministry", "kind", "is_active"])` | ministry 页面的第一段："这个 ministry 有哪些编制、分别是什么 kind"。**这张表只有几十行，索引基本是象征性的** —— 建它是为了 Phase C 组织架构图和以后规模变大，现在别指望它带来可测的差别 |
| `Event`：`Index(fields=["start_time"])` | **R1**「某段时间有多少场活动」、admin 的 `date_hierarchy` |
| `Event`：`Index(fields=["ministry", "start_time"])` | **R2**「食物银行这个月办了几场」 |
| `Event`：`Index(fields=["status", "start_time"])` | **P3** 志愿者的活动列表 = `open_for_signup()` + 未开始，按时间排。**这是全系统被普通用户打得最多的一个查询**，唯一一个真的需要索引的。<br>详情页那条走 `visible_to_volunteers()`（四档**显式列全**，不是「排除 `draft`」—— 同下面那节：补集写法在加第六档时会默默把它放行），是主键查 + 一个状态判断，不需要另建索引 |
| `EventRole` | `(event, role)` 的唯一约束自带索引，覆盖 R4 / R5 / R7 的 group by |
| `Participation` | `(event_role, contact)` 的唯一约束自带索引，覆盖活动侧；联系人侧（"我的报名"）走 `contact` 的 FK 自动索引 |
| `MinistryRole`：`Index(fields=["contact", "end_date"])` | **每一次权限判断都走这个查询**（"这个人管哪几个 ministry"），是全系统调用频率最高的一条 —— 每个受保护的视图每次请求至少一次 |
| 各字典表的 `code` | `UniqueConstraint(Lower("code"))` 自带的表达式唯一索引（**不是** `unique=True`，见 D9 归一化通则） |

小提醒：Django 给 FK 自动建单列索引，所以 `Position` 上 `(ministry, kind, is_active)` 建好之后
`ministry` 单列索引就冗余了（最左前缀覆盖）；`Assignment` 上 `(position, status, end_date)` 之于
`position` 同理。数据量小无所谓，知道就行。

##### 单一真相：任何带日期的表都不加 `is_active`；`Assignment` 用 `status`

> ⚠️ 顺带一条本文档吃过两次亏的教训：**别在正文里写行号**
> （原文引用过 `contact/models.py:247-249` 和 `contact/admin.py:12`，
> 两处后来都指向了别的东西）。代码搬一次行号就变成假的了。

一张表同时有 `is_active` 和 `end_date`，就能存出
`is_active=True` + `end_date=2020-01-01` 这种自相矛盾的行。
这违反 D11 自己那句"不是两处都能记，是只有一处能记"。

**通则：结束只由 `end_date` 表达**，凡是带起止日期的表一律不加 `is_active`，
全项目只有一处日期派生逻辑（`core.querysets` 的 `.active()`）。
`Contact.is_active` 是唯一的例外，且它不是这个意思 —— 那是"这条档案还用不用"，
它身上根本没有起止日期。

#### `Assignment.status`：状态和任期是正交的两个维度（2026-07-28 修订）

> 本条改过一次。 原文是"`Assignment` 不加 `is_active`，在职状态完全由日期派生"。
> **结论对了一半，理由对了，但漏掉了一个真实需求。**（基金会已确认**跟踪请假 / 停职**。）

**漏掉的是什么**：志愿者出国三个月、员工休产假 / 病假、背景审查过期待复核 ——
这些**任职关系并没有结束**，只是当前不能服务。只有日期的话，唯一能表达它的办法
就是**把 `end_date` 截断、回来再建一行**，而那会：

- **算错任期长度** —— 一段连续三年的服务变成"两年 + 三个月空档 + 半年"，
  资历、累计服务时长、周年识别全部失真；
- **篡改真实的合同 / 志愿协议日期**（员工场景下有合规含义）；
- 原始日期只剩在 simple-history 里 —— **可追溯但主表答不出**，
  和 D11 第二次修订判死刑的是同一种病。

但修法不是加回一个 `is_active`。 那会原封不动地把矛盾请回来
（`is_active=True` + `end_date=2020` 照样存得进去）。

```python
Assignment(
    start_date, end_date,                      # 任期 —— 唯一真相，不因请假而改动
    status = active | on_leave | suspended,    # 任期「之内」的当前状态（TextChoices）
)
```

`status` 里绝不能有 "ended" / "已结束" 这一项。 那就是把 `end_date` 记两处，
正是本节要杜绝的东西。结束只由日期表达。

##### 两个谓词：`.active()` 和 `.serving()`

```python
# core 的共享 mixin —— Assignment / MinistryRole 通用，纯日期
def active(self, on=None):    ...        # 在任期内 / 生效中

# Assignment 专有
def serving(self, on=None):
    return self.active(on).filter(status=Status.ACTIVE)   # 在任期内 AND 当前可服务
```

> 规矩一句话：`status` 只能和日期做 AND，永远不能单独用。

这条规矩解掉了原来那个矛盾。`status=on_leave` + `end_date=2020` 读作
"他 2020 年离任，离任时正在休假" —— **不矛盾，只是陈旧且无害**，
因为 `serving()` 已经先 AND 了日期，结束了的任职无论什么 status 都不会被算进去。

**所以不加"状态必须和日期一致"的约束** —— 那需要在 `CheckConstraint` 里引用"今天"，
而"今天"不是不可变表达式，数据库拒绝。靠 AND 的查询纪律，不靠约束。

> **病根辨析（重要）**：`is_active` 之所以危险，不是因为"有两个维度"，
> 而是因为 admin 会把它当成日期的**替代筛选项**（`list_filter = ["is_active"]`
> 能独立按它过滤，给出错误答案）。两个维度被当成二选一才是病，正交本身不是。

已知限制：只记当前状态，不记请假历史。 "谁在去年三月请过假"这个问题
`status` 答不出来（只能翻 simple-history）。真要的话按 D15 三条件检验：
一个人可以有多段请假 → 基数破 → 必须用表（`Leave(assignment, start, end, reason)`）。
**现在不做**，见推迟清单。

##### 四个 `is_active` / 状态是四个概念

同名不同义，而 ministry 页面**四个都要用到**：

| 字段 | 含义 | 反例 |
|---|---|---|
| `Contact.is_active` | **这条档案还在不在用**（也当重复记录合并后的墓碑用） | 已停用档案的人**仍可能**挂着在职任职 |
| `Position.is_active` | 这个编制还设不设 | 编制还设着、但没人在任 = **空缺**，不是 inactive |
| `Assignment` 的「在任期内」 | 由 `start_date` / `end_date` **派生**，不是字段 | 这张表**没有** `is_active` |
| `Assignment.status` | 任期之内当前能不能服务 | 请假中的人**仍然在任期内**，不能靠改日期把他弄出去 |

**规定：ministry 页面的在职人员查询必须过滤
`.serving()` + `contact__is_active=True` + `position__is_active=True`。**
漏 `position__is_active` 的症状是"撤销的编制上还挂着人"；
用 `.active()` 而不是 `.serving()` 的症状是"请假的人还在当值名单上"。

> 但**花名册**（谁属于这个 ministry）该用 `.active()` —— 请假的人还是这个团队的成员。
> **当值名单用 `.serving()`，花名册用 `.active()`**，两个都对，别混。

##### ~~`is_reference_only` 的纪律：`Contact.objects.people()`~~ —— 已作废

> 整节删除（2026-07-28）。 `is_reference_only` 和 `Contact.objects.people()` 都不再存在 ——
> 紧急联系人改用 `EmergencyContact` 专用表，姓名电话存文本，**`Contact` 里根本不会出现幽灵记录**。
>
> 这一节原本用了 D16 那套三层套路（显式方法 + 文档 + grep 守卫）来兜住
> "每个列表、搜索、导出、人数统计都必须记得排除 reference-only"这条纪律。
> **保留这段记录，是因为它示范了一种应该警惕的解法**：
> 当你发现自己要靠"所有人每次都记得调用某个方法"来维持正确性时，
> **先回头问问那个东西是不是根本不该在那张表里** —— 结构性保障不需要任何人记得什么。
> 判据已经归纳进 D15「载体的第四条判据」。
>
> **连带作废的**：`Contact.objects.people()`、它的 grep 守卫测试、
> admin 列表默认预选"仅真实联系人"筛选器、以及"reference-only 记录不该有 `Assignment`
> 或 `Participation`"那条数据质量提示。一条都不要写。

##### `Contact` 重名的处理（必须赶在建那些 autocomplete 之前）

本阶段要新增好几个指向 Contact 的 autocomplete（`reports_to` 经 Assignment、
`Participation.contact`、`Event.owner`）。而 `Contact.__str__` 现在对两个都叫"王强"的人
返回**完全一样的字符串** —— 下拉框里两个一模一样的选项，选错了不会报错，
是**静默的数据错误**（报名、任职、紧急联系人全挂到错的人身上）。

不要用唯一约束禁止重名。 重名是合法现实，这个领域**没有可靠的自然键**：
email 不能设 unique（一家人共用一个邮箱很常见）、电话同理。
这正是 CiviCRM 要做模糊查重、而不是加唯一约束的原因。

三件事，按性价比排：

1. **改 `Contact.__str__` 带上区分信息** —— `王强 (wang@example.com)`，
   没有 email 就退到电话，都没有就退到 `#42`。**这一条修好了所有下拉框**，
   是本组里唯一必做的。代价：邮箱/电话会出现在下拉和日志里（小基金会可接受，但要知道）。
   机构侧同理，不要只改个人那一支。
2. **分级查重：同名提示、同名同号硬拦截**（2026-07-28 修订）。

   > 原方案是"一律 `messages.warning`，只提示不阻止"。 保留了"重名合法"这个正确判断，
   > 但有个真缺陷：**`messages.warning` 是保存之后才出现的，那时重复记录已经进库了**，
   > 操作员还得回头去删。

   | 信号 | 频率 | 处理 |
   |---|---|---|
   | 仅**同名**（姓名归一化后比较：去空格、忽略大小写） | 高 | `messages.warning`，**不阻断** |
   | **同名 AND 同号**（`Contact.find_exact_duplicates()`） | 低 | ✅ **硬拦截 + `force_save` 复选框** |

   硬拦截只能绑在同名同号上，绝不能绑在同名上。 在这个基金会的服务人群里，
   王强 / 李明 / 陈伟同名是常态 —— 每天弹 20 次硬拦截，操作员会训练出"看到框就打勾"的
   条件反射，**拦截失效，还多了两次点击**。这就是文档在紧急联系人那一节写过的
   "阻塞保存会让人学会绕过系统"，同一个道理。而同名同号几乎必然是真重复，一年触发不了几次。

   **`force_save` 的形状**（在 `ContactForm` 上加一个不落库的虚拟字段）：
   第一次提交命中 → `ValidationError` 打断保存 + 浮现"确认不是重复人员，强制保存"复选框 →
   操作员显式勾选并再次提交才入库。既拦得住，又不否认重名的合法性。

   ⚠️ 实现细节：widget 的显隐要在 `__init__` 里按 `data` 决定，
   **不要在 `clean()` 里改 `self.fields[...].widget` 之后抛异常** ——
   第二次提交若还有别的校验错误，复选框会退回隐藏态，用户会以为自己没勾。

   判定函数仍然只有 `find_exact_duplicates()` 一个（见下面第 6 条），
   拦截逻辑本身按 D18 放在 model / services 层，`ContactForm` 只调用。
3. **合并两条重复记录** —— **本阶段必做**（2026-07-28 从推迟改过来），见下面「合并重复记录」。
   ⚠️ 改口的**原始**理由（`is_reference_only` 会持续制造重复）已经失效，
   新理由见那一节的开头说明。

##### 紧急联系人的录入

> 本节 2026-07-28 大幅简化。 原方案（自引用 FK + `is_reference_only`）需要
> 自动建记录、命中预选、去重判定、安全阀、残留风险五大段。
> **改用专用表 + 文本之后，这些整体消失了** —— 没有身份要认，就没有认错的可能。
> 这是选文本方案顺带得到的最大简化，也是它唯一比 FK 版简单的地方。

`EmergencyContact` 是 `ContactAdmin` 上的一个 inline，操作员填三样东西：
**姓名、电话、关系**。没有查重、没有关联、没有预选、不跳页。

三条要求：

1. 关系必填，靠 FK 非空

`relationship_type` 是非空外键 —— 拆成专用表之后，原来那条
`emergency_contact IS NULL OR emergency_contact_relationship IS NOT NULL` 的
`CheckConstraint` 降级成一个 `null=False`。这是拆表白捡的简化。

词表复用 `RelationshipType` + `usable_as_emergency_contact` 过滤（见 D15），
**不新建词表** —— 这一条不受本次修订影响。

2. 方向约定必须写进 `help_text`

`relationship_type` 一律读作**「紧急联系人 是 本人 的 ___」**，即 `name_a_to_b`。
小明那一行填 `王秀英` + `parent of` = "王秀英是小明的母亲"。
**不写死一定会录反**。

3. 电话必填，用 `PhoneNumberField`

存 E.164（D7）。**一个没有电话的紧急联系人是没有意义的**，所以 `blank=False`。
姓名同理。

> 不做同名同号查重。 那套判定（`find_exact_duplicates()`）留给 `Contact` 本身
> （见上面「`Contact` 重名的处理」），**紧急联系人这一支不需要它** ——
> 文本行之间没有"是不是同一个人"这个问题要回答，只有"同一个人身上录了两遍"，
> 那由 `UniqueConstraint(person, Lower(Trim(name)), phone)` 挡住。

⚠️ **要接受的后果**：王秀英有三个孩子做志愿者，就是三行独立的文本，
改号码要改三处，系统不知道这是同一个人；她自己来做志愿者时还会有第四份。
**这是 D15 里主动接受的代价，不是遗漏** —— 别在实施时"顺手"加个 FK 把它优化掉，
那会把幽灵记录请回 `Contact`。

##### 合并重复记录（本阶段做，但理由换过一次）

> ⚠️ **它进 Phase B 的原始理由已经失效**（2026-07-28）。当时写的是
> "`is_reference_only` 会持续制造重复，没有合并就只能眼看着攒" ——
> **而 reference-only 已经不存在了**。
>
> **保留在本阶段的新理由**：跨渠道录入（活动签到、志愿者自荐、员工代录）仍然会产生重复，
> 而「同名同号硬拦截」只挡得住同一个表单里的手滑，挡不住两个人在不同时间各录一次。
> **但这个理由比原来弱** —— 如果要削减 Phase B 范围，**这是第一个候选**。
> 削掉的话它回到推迟清单，触发条件是"真的攒出了一批重复"。

范围：最小可用，不做花哨的逐字段合并界面。

- **通用地改指所有外键** —— 遍历 `Contact._meta.related_objects` 把指向"被合并方"的行
  全部改指到"保留方"。**不要手写一张外键清单** —— 那样 Phase C 的 `Contribution`
  必定被漏掉，而漏掉的症状是捐款记录跟着废弃记录一起消失。
- **遇到冲突就拒绝，交人工** —— 两种：① 一对一冲突（两条都挂了 `User` 或 `VolunteerProfile`）；
  ② 唯一约束冲突（两条在同一活动同一角色都有 `Participation`）。
  拒绝并说清是哪一条挡住了，比自作主张删一边安全得多。
- **留痕** —— `Contact` 已挂 simple-history；另外在保留方的 `notes` 里记
  "已合并 #42（2026-08-01）"，让人肉眼也能看出来。
- **必须显式触发 + 二次确认**，绝不自动合并。

##### 界面：一个朴素的 Django 视图，不做成 admin 动作（2026-07-28 修订）

> 原方案是"admin 动作 + 二次确认 + admin 首页放一个『疑似重复待处理：N 条』计数"。
> 那两样都要动 admin 模板和 `AdminSite`，**正好命中 D18 新增的形状触发** ——
> 是全项目最会随 Django 升级坏、且前端上来一定全丢的那一格。

**改成**：`contact/views.py` 里一个 `/contacts/merge/` 页面
（`keep` 和 `drop` 两个 id 作为参数），staff-only，GET 显示差异对比 + 确认按钮，POST 调
`contact/services.py` 的 `merge_contacts(keep, drop)`。**逻辑一个字不进视图**
（跨表写入按 D18 落点表归 `services.py`），视图是薄壳。

**为什么这样反而更便宜：**

| | admin 动作版 | 朴素视图版 |
|---|---|---|
| 二次确认页 | 要 `extends "admin/base_site.html"`，吃 admin 的模板结构 | 一个自己的模板，没有依赖 |
| "待处理 N 条"计数 | 要覆盖 `admin/index.html` 或自定义 `AdminSite` | 就在这个页面顶部列出来，不用碰首页 |
| Django 升级 | 模板继承点变了会坏 | 不受影响 |
| 前端上来 | 全部重写 | 只换模板，视图和逻辑照旧 |
| 削减 Phase B 范围时 | 和 admin 缠在一起，不好砍 | 一个文件，直接不写 |

连带的好处：这是本项目第一个自己写的页面，正好在模型已经稳定、
风险最低的一件事上把"视图 + 模板 + 权限"这条路先跑通 ——
比 Phase C 直接上 Ministry 视图安全（见 Phase C 的优先级顺序）。

**仍然保留在 admin 里的**：「疑似重复（同名同号）」这个 `list_filter` ——
那是纯呈现，按 D18 落点表本来就该在 admin。它给出候选，链接跳到上面那个页面。

> 仍然推迟的是**逐字段合并的交互界面**（"保留哪个邮箱、哪个地址"）。
> MVP 阶段规则简单：保留方的字段优先，被合并方只有在保留方为空时才补进来。

##### Ministry 视图：为什么 `Ministry` 表不能推迟

基金会有多个 ministry（食物银行、报税志愿、ESL…），明确想要的前端效果是
**看到各个 ministry、以及每个 ministry 的 leaders 和在职人员**。

**`Ministry` + `Position` + `Assignment` 正好就是这个结构**，不需要新概念：

```
Ministry: Food Pantry
  ├─ Leaders     ← Position(ministry=食物银行, is_leader=True) 上在职的 Assignment
  ├─ Employees   ← Position(ministry=食物银行, kind=employee)  上在职的 Assignment
  ├─ Volunteers  ← Position(ministry=食物银行, kind=volunteer) 上在职的 Assignment
  └─ 空缺        ← Position(ministry=食物银行, is_active=True) 上没有在职 Assignment 的
```

**第四组「空缺」不是可选装饰**：拆出 `Position` 的首要理由就是让空缺可表达，
页面上看不见它，这张表就白建了（见上面「空缺编制」）。

**用词口径（2026-07-28 已确认）：基金会只有 employee 和 volunteer 两种说法，
没有 "worker" 这个概念** —— 界面上就分 Leaders / Employees / Volunteers 三组，
文案里不要出现 "worker"，也不要造一个把两者合起来的中间词。
（`Position.kind` 仍是 employee·volunteer·board 三种，理事席位走 `kind=board` 见 D11；
理事席位不属于任何 ministry，所以不出现在 ministry 页面上。）

四个已有决策刚好各自到位：

- **一人服务多个 ministry** —— 两行 `Assignment` 指向两个 `Position`。正是 D11 从 1:1 改成 1:N 解决的场景
- **"在职"已定义** —— `Assignment` 不带 `is_active`，靠日期派生的 `.active()`
- **"空缺"已定义** —— `Position.objects.vacant()`，同一套时间口径
- **leader 用 `Position.is_leader` 布尔标**，不要用 `name.contains("leader")` 查 ——
  `name` 是给人看的自由文本，`is_leader` 是给代码查的。同 D5 那条 code vs 显示名的道理。
  **一个 ministry 可以有多个 leader 编制，不加约束**（联合负责人很常见，2026-07-28 确认）

⚠️ Ministry 绝不做成 `contact_type=organization` 的 Contact 行。
这个念头很自然（CiviCRM 风格），但在本设计里是错的：`Contact` 装的是人和**外部**组织
（D4），ministry 是**内部**组织单元（D11 那一侧）。混进去会同时踩两个已知的坑 ——
联系人列表被非人记录污染，以及"外部组织归属"和内部结构的边界糊掉。

**为什么不能像 `Skill` 那样推迟**：推迟就意味着先用自由文本记 ministry 名，
以后收编成外键时要去重 "Food Pantry" / "food pantry" / "Pantry" ——
**正是 D15 论证过的那个痛的迁移方向（字段 → 表）**。现在建表几乎免费。

> 同一条理由这次也适用于 `Position`，而且更强。 如果 Phase B 先做 `Assignment.title`
> 自由文本、以后再收编成 `Position`，要去重的就是几百行任职记录里的职务名 ——
> 比 ministry 那次更痛。而现在 `org` app 一行代码都还没写、开发库业务表全是 0 行，
> 拆表的成本**严格等于零**。按 Phase A 反复用的那条标准（"现在改成本≈0，以后改很痛"），
> 这就是必须现在做的典型。

**专业系统的做法是收敛的**，这个结构不是自创：Salesforce NPSP 是
`Program` + `ProgramEngagement`（带角色和起止）、ERPNext 是 `Department` + `Designation`
+ Employee（**编制和任职本来就是分开的**）、教会管理系统是 `Team` + `TeamMembership`（带 leader 角色）。
**共同点都是"一等的单元实体 + 一张带角色和起止日期的成员关系表"** —— 我们的
`Ministry` + `Position` + `Assignment` 就是它，而且 ERPNext 那一支恰好印证了把编制单列的做法。

**查询长什么样**（Phase B 建完之后）：

```python
# 当值名单：.serving() —— 请假 / 停职的人不该出现在"现在谁在管"里
on_duty = Assignment.objects.serving().filter(
    position__ministry__code="food_pantry",
    position__is_active=True,
    contact__is_active=True,
).select_related("contact", "position")          # 两个都要，否则每行两次查询

leaders    = on_duty.filter(position__is_leader=True)
employees  = on_duty.filter(position__kind="employee")
volunteers = on_duty.filter(position__kind="volunteer")

# 花名册：.active() —— 请假的人仍然是这个团队的成员，只是标注状态
roster = Assignment.objects.active().filter(position__ministry__code="food_pantry")

vacancies = Position.objects.filter(ministry__code="food_pantry").vacant()

ministry.positions.all()           # 这个 ministry 有哪些编制（不需要 join 任职）
contact.assignments.all()          # 从人那头看服务哪几个 ministry（D11 的收益）
```

> **注意 `select_related` 现在要带两个** —— 显示一行需要 `contact.name` 和 `position.name`，
> 漏掉任何一个都是 N+1。这是拆表换来的一点额外注意力，值得。

界面本身属于 Phase C（D2：前端推迟），但**数据结构必须现在就位**。
见 Phase C 里把 ministry 页面列为首选。

##### 未成年人要能查出来

`is_minor` 从 `Contact.birth_date` **派生**，**绝不要存 `age` 字段** ——
会过期，而且没有任何机制提醒你它过期了。

真实需求：**"这次活动有哪些未成年参与者、出事或活动前该拨谁的电话"**。
`Guardianship` 移出 Phase B 之后，这条需求不依赖任何未建的表：
`is_minor` 负责**把人筛出来**，联系方式来自
**`Participation` 的 `consent_email` / `consent_phone`，`EmergencyContact` 是回落**。

> ⚠️ 2026-07-29 晚更正：原文写的是"由 `is_minor` + `EmergencyContact` **完整闭环**"。
> **那半句当时是假的** —— `EmergencyContact` 只有 `phone`、没有 email，
> 而 P6 的默认投递后端是邮件，于是"最需要被通知的那群人"会全部落进 `unreachable`
> （见 [D22 ①](decisions/D22-event-notifications.md#d22--活动变更通知收件人解析是业务逻辑投递是可替换的适配器2026-07-29)）。
> **"出事时拨电话"确实闭环了（现场有人、拨号就行），"活动前发通知"没有** ——
> 两个场景当时被当成了一个。补 `consent_email` / `consent_phone` 才补上后者。

三件事，一件都不能少：

1. **`is_minor` 做成三态**（`True` / `False` / **`None` = 生日未知**）。
   `birth_date` 是可空的（`contact/models.py:100`），把未知折叠成 `False` 会让
   **没填生日的未成年人从家长通知名单里静默消失** —— 这正是这个功能最不能出的错。
2. **admin 里要有"生日为空的参与者"这个可见入口**，让"未知"看得见而不是被吞掉。
3. **`list_filter = ["is_minor"]` 不能用** —— property 无法进 ORM 过滤。
   必须写 `SimpleListFilter`，但**它自己不许算阈值**（2026-07-28 收口）：

   ```python
   # contact/models.py —— 派生判定的唯一一份
   class ContactQuerySet(models.QuerySet):
       def minors(self, on=None): ...           # birth_date > on - 18 年
       def adults(self, on=None): ...
       def birth_date_unknown(self): ...        # birth_date IS NULL
   ```

   `SimpleListFilter` 三个选项，每个只调上面一个方法。
   **为什么必须抽出来**：D18 的落点表把 `is_minor` 点名划给 QuerySet 方法，
   而"18 岁阈值 + 闰年 + D16 时区口径"这三样只该写一遍 ——
   Phase C 要"给所有未成年参与者的家长发通知"时直接 `.minors()`，不重算。
   写在筛选器里的话，那段逻辑会跟着 admin 一起被删掉，然后在前端重写一遍。

   算阈值用 `dateutil.relativedelta` 或 `date(y-18, m, d)` 加 try/except 兜 2/29，
   别自己数天数。"今天"走 `local_today()`，且**按 D16 第 2 层做成 `on=None` 参数**
   （同 `.active()` / `.vacant()`）—— 顺带白捡"某一天谁还是未成年"。

> **为什么不能改用 ORM annotation 代替 `SimpleListFilter`**（2026-07-28 评审提过，已核实否决，
> 记在这里免得下一个读者再提一遍）：
>
> 1. `list_filter` 不接受 annotation。 它通过 `get_fields_from_path` 在**模型**上解析字段名，
>    annotation 不是模型字段，会在 system check 阶段直接报 `admin.E116`。**这与 Django 版本无关**，
>    所以"新版支持、老版写个极简 Filter"的说法不成立 —— `SimpleListFilter` 一个字都省不掉。
> 2. "annotation 就不用处理闰年了"是假的。 阈值 `local_today() - relativedelta(years=18)`
>    仍然在 Python 里算，用的仍然是 `relativedelta` —— 和上面第 3 条要求的是同一件事。
> 3. 性能收益在几千行数据下不存在（同"不要提前加归一化姓名冗余列"的判断）。
>
> **可选的加法**：annotation 确实能让 `list_display` 里的这一列**可排序**（配 `admin_order_field`），
> 这是个真收益，想做可以加，但它**不替代**筛选器。
> ⚠️ 若要加，阈值必须在 `get_queryset()` 里按请求求值 —— 写成模块级常量就是 D16 那个
> "进程启动时冻结时钟"的 bug 换了个地方。

##### `EventRole` 和 `Position` 不是一回事

一句话定义，防止以后混淆（2026-07-29 更新主语，`Participation.role` 已并入 `EventRole`）：

- **`EventRole`** = "**这一次活动**里开的一个工种"（临时、一次性：签到台、搬运、翻译）
- **`Position`** = "组织里的一个编制"（长期：项目协调员），`Assignment` 是谁在什么时候占着它

不写下来，以后一定有人想把活动工种塞进 `Position`。
**判据还是 D10 那句口诀**：换个人来做还成立的 → 编制；只属于这一场活动的 → `EventRole`。

> ⚠️ 它们形状相同不代表是同一个东西。 D19 论证的是
> `EventRole : Participation = Position : Assignment` 这个**结构类比**（都是"格子 + 占格子的人"），
> **不是**说可以合并成一张表。合并的话，一场活动的临时工种会污染组织架构图，
> 而 `Position.reports_to` 对活动工种毫无意义。
> 抄结构，不抄行 —— 同 D4 那句"抄结构不抄代码"的道理，只是换了一层。

##### R8 长什么样：跨三张表的那条查询

这是 14 条需求里最难的一条，也是 `org` 那套表存在的理由。写在这里免得实施时重新推：

```python
# "某场活动里，开设它的 ministry 下面的 employee 谁参与了、分别负责什么"
on = event.start_time.date()          # ⚠️ 活动当天，不是今天 —— 见下

staff = Participation.objects.filter(
    event_role__event=event,
    contact__assignments__in=Assignment.objects.active(on=on).filter(
        position__kind=Position.Kind.EMPLOYEE,
        position__ministry=event.ministry,
        position__is_active=True,
    ),
).select_related("contact", "event_role__role").distinct()
```

三个坑，一个都不能踩：

1. 时间口径是活动当天，不是今天。 用 `.active()` 的默认值（今天）去查一场去年的活动，
   会漏掉所有之后离职的人，**而且不报错**。`.active(on=...)` 那个参数就是为这种查询准备的
   （见 [D16 第 2 层](decisions/D16-time-and-dates.md#d16--时间与日期的唯一口径2026-07-28) 和 `.active()` 那一节）。
2. 用 `.active()` 不是 `.serving()`。 问的是"他当时是不是这个 ministry 的员工"，
   不是"他今天能不能当值" —— 请假中的人参加了活动，照样要算进去。
   见「[两个谓词](#两个谓词active-和-serving)」。
3. `.distinct()` 不能省。 一个人在同一个 ministry 可能有两个 employee 编制（一人多岗，
   D11 的核心场景），join 之后他的 `Participation` 会出现两遍，**人数直接多一个**。

##### `EventRole.needed_count` 是参考值，不是硬上限（2026-07-29 从 `Event.capacity` 改写）

> **原来这一节讲的是 `Event.capacity`（整场活动一个数）。字段已被 `EventRole.needed_count`
> （每个工种一个数）取代**，理由见 [D19](decisions/D19-event-role.md#d19--活动的工种编制-eventrole2026-07-29)：
> P2 要求"说明需要多少 volunteers"，而"搬运要 5 个、翻译要 2 个"整场一个数说不出来。
> **下面这条"只提醒不阻止"的口径原样保留**，只是主语换了。

超过需求人数只**提醒**，**不做约束、不阻止**。
口径同 `Contact` 重名：现实里超员报名是常事，系统的职责是提醒而不是拦路。
数据库层只保证 `needed_count IS NULL OR needed_count > 0`。

**判断和提醒要分开**，而且这次判断本身就是 R5 要的那个数：

```python
# events/models.py —— 一次查询算完一场活动所有工种的缺口
class EventRoleQuerySet(models.QuerySet):
    def with_signup_counts(self):
        """加两列真实 SQL 列：registered_count / attended_count。

        annotation 不是 property —— 一次查询算完任意多行，能排序能筛选能直接
        序列化进 API。同 PositionQuerySet.with_headcounts()（B5 已落地），
        一模一样的理由：COUNT(...) FILTER (WHERE ...) 条件聚合，一次查询给出每行的人数。
        """

    def understaffed(self):
        """报名人数 < needed_count 的工种。needed_count 为空的一律不算缺人。

        这是 P2「征集 volunteers」最想看的那个列表，也是 D19 要拆出这张表的
        全部意义 —— 零报名的工种必须出现在这里，而它在 Participation 里没有行。
        """
```

**`count > needed_count` 这个比较绝不能写在 `ModelAdmin` 里** —— 它是业务判断，
写在 admin 里的话自助页面的活动详情要重算一遍。
> **`messages.warning` 本身留在 admin 是对的**，那是界面。
> 自助页面上同一个数字渲染成"还差 2 人"，背后调的是同一个 annotation，一个字不用改 ——
> 这正是"界面归界面、数据归数据"的样子。

##### 可见性与生命周期：两个谓词，不是一个 `status`（2026-07-29 晚新增）

> 本节是一次自查的结果，改的是 `Event.status` 的用法，不是它的取值。
> 起因：全文（含 `02-roadmap.md`）把志愿者侧的查询一律写成 `filter(status=OPEN)`。

`Event.status` 五档 `draft·open·confirmed·completed·cancelled` 同时在回答两个问题：

1. **生命周期** —— 这场活动办到哪一步了（草稿 → 招人 → 人齐 → 办完 / 取消）；
2. **可见性** —— 志愿者能不能看到它。

把可见性等同于 `status=open`，直接的后果是：**活动一被标 `confirmed`（"人齐了，不再收报名"），
已经报名的志愿者就打不开这场活动的页面了。** 而 P6 的整个场景就是"活动改时间 → 通知报名者 →
通知里那句'来不了请点这里取消'" —— **那个链接会 404**，而且最可能发生在人已经招满的活动上。

> 这是本项目第四次遇到同一个形状：两个维度挤进一个字段。
> 前三次是 [`is_active` 挨着 `end_date`](#单一真相任何带日期的表都不加-is_activeassignment-用-status)、
> [`Assignment.status`](#assignmentstatus状态和任期是正交的两个维度2026-07-28-修订)、
> 以及拒绝 [`Participation.needs_reconfirmation`](decisions/D22-event-notifications.md#报名有效性改了时间报名照旧)。
> **这一次没有被认出来**，因为 `status` 看上去只是"一个状态字段"。

**修法不是加字段，是加谓词** —— 同 `.active()` / `.serving()` 那一套，判定只写一处：

```python
# events/models.py
class Event(...):
    # ⚠️ 两个集合都【显式列全】，不许写 exclude(DRAFT)。见下面那条注。
    VISIBLE_TO_VOLUNTEERS = {Status.OPEN, Status.CONFIRMED, Status.COMPLETED, Status.CANCELLED}
    OPEN_FOR_SIGNUP       = {Status.OPEN}

class EventQuerySet(models.QuerySet):
    def visible_to_volunteers(self):
        """志愿者能看到的活动：发布过的，含已招满 / 已办完 / 已取消。"""
        return self.filter(status__in=Event.VISIBLE_TO_VOLUNTEERS)

    def open_for_signup(self):
        """还能报名的活动。"""
        return self.filter(status__in=Event.OPEN_FOR_SIGNUP)
```

> ⚠️ 不要写成 `exclude(status=DRAFT)`，哪怕现在两者等价。
> `02-roadmap.md` 的 [B5 复盘「用补集定义状态，等于赌只有两种状态」](02-roadmap.md)
> 已经为这件事付过一次代价（`Position` 的三态被补集捞错），
> 那条的判定方法是：把所有状态列出来数一数 —— 超过两种，补集就是错的。
> `Event.status` 有五档，**以后加第六档（比如 `postponed`）时，补集写法会默默把它变成可见的**。
> 本节初稿正是写成了 `exclude(DRAFT)`，同日按那条教训改成显式列举 ——
> 记在这里，因为这是同一个坑第二次差点被踩。

> 规矩一句话：可见集和可报名集各自列全；"能不能报名"和"能不能看见"分开问。
> 取消的活动**要留在可见集里** —— 报过名的人正需要看到"这场取消了"。

- **活动列表页**（P3）用 `open_for_signup()` + 未开始 —— 列表里只该出现还能报的；
- **活动详情页 / 我的报名 / 通知里的链接**用 `visible_to_volunteers()` ——
  否则 `confirmed`、`completed`、`cancelled` 三档全部变成 404；
- **`draft` 只有本 ministry 有权限的人看得到**，这一条不变（D21 第 2 条）。

**为什么不加一个 `is_published` 布尔**：那就又多一个能和 `status` 互相矛盾的维度
（`is_published=False` + `status=open` 存得进去），而这正是上面那三次付过的代价。
一个字段 + 两个谓词，没有第二处真相。

##### 签到签退与 `hours`：谁是权威（2026-07-29 新增）

P4 要两样东西：**"是否来过"**（`checked_in_at`）和**"做了多久"**（`hours`）。
它们看上去可以互相推导，而这正是危险的地方。

> 规矩一句话：`hours` 是唯一权威值。签到签退是采集手段，不是第二个口径。

```python
# events/services.py —— 唯一一处写 hours 的地方
def check_out(participation, *, at=None):
    """签退：记 checked_out_at，并把时长写进 hours。

    ⚠️ hours 是权威值，这里是「写入」不是「派生」—— 之后有人手工改了 hours，
       以它为准，不要再从时间戳重算覆盖回去。
    """
```

**为什么 `hours` 不做成派生 property**（想过，否决）：

- 有人**忘记签退** —— 那样他的工时会永远是 0 或 null，而他确实干了 4 小时；
- 有人是**纸质签到表事后补录** —— 根本没有时间戳，只有一个"他干了 3 小时"；
- 有人**中途离开又回来** —— 一对时间戳表达不了，但工时是清楚的。

**为什么不让两者各算各的**：那就是 `is_active` + `end_date` 那个病 ——
两个字段回答同一个问题，可以互相矛盾，而且没有任何机制会告诉你。
所以**签退时写入一次，之后 `hours` 说了算**，时间戳只回答"他来了没有、几点来的"。

> 顺带：`status=attended` 的判定也走签到 —— `check_in()` 把 status 改成 `attended`。
> 约束 `checked_in_at IS NULL OR status <> 'absent'` 挡住"签到了又标缺席"这种自相矛盾。

##### 背景审查：独立成表，存完成日不存到期日（表已推迟，形状不变）

> 2026-07-29 起 `BackgroundCheck` **已移出本阶段**（见上面的模型表和推迟清单）——
> 14 条需求一条都没碰它。**本节保留，因为推迟的是建表、不是推翻拆表**：
> 建的那一天照这里写。动词读作"将来建的时候"，不是"这一步要做"。

**独立成 `BackgroundCheck` 模型**（2026-07-28 决定，见 D18 和上面的模型表）——
理由是 Django 没有字段级权限，留在 `VolunteerProfile` 里就锁不住它
（一个 Group 不授 `volunteer.view_backgroundcheck` 才锁得住，而那要求它是独立的 model）。

`completed_on` + 有效期长度放 settings（`BACKGROUND_CHECK_VALID_DAYS`），
"是否过期"做成 property + admin 筛选器。理由和不存 `age` 完全一样：
政策改了（比如从 2 年缩到 1 年）不用洗数据。

> **注意它挂 `Contact` 而不是 `VolunteerProfile`** —— 背景审查是对**人**做的
> （D10 的角色层：换岗不用重查），而且将来员工、理事也可能需要，
> 挂在志愿者档案上会把它锁死在一种身份里。

**有效期具体多长基金会还没答复**，先用 730 天（2 年，美国非营利常见值）当占位，
`base.py` 里写清楚这是未确认的默认值。这不阻塞建模。

**敏感度**：背景审查结果是本系统里仅次于薪酬的敏感数据。D11 把薪酬排除在 MVP 之外的
理由（字段级权限、谁能看谁的）对它同样成立。
[Phase C 的权限复核](progress.md#phase-c--上线与真实运营)里要和未成年人信息并列处理
（**2026-07-29 C / D 对调后是 C**，原文写的是 Phase D）。

##### `RelationshipType` 词表的收口（见 D15）

代码里查关系类型一律用 `RelationshipType.code`，**不用显示名** ——
显示名在 admin 里随时能改，`filter(name_a_to_b="parent of")` 会在改名之后静默失效。

这张词表有两个使用方（`EmergencyContact.relationship_type`、
`Participation.consent_relationship`），所以"同一个意思攒出好几行"是真实风险。
两条唯一约束把它堵死：

| # | 缺口 | 现在会发生什么 | 补法 | 层级 |
|---|---|---|---|---|
| 1 | 建了反向类型行 | 已经有 `name_a_to_b="parent of"` / `name_b_to_a="child of"` 了，还能再建一个 `name_a_to_b="child of"` 的类型 —— 三个字段全都不同，唯一约束不触发。结果是同一层关系被两行词表瓜分 | `RelationshipType.clean()`：新类型的 `name_a_to_b` 撞上任何已有类型的 `name_b_to_a`（忽略大小写和首尾空格）就报错，并指出撞的是哪一行 | 提示层（类型表十几行，`clean()` 足够；这是唯一正确的拦截时机 —— 建类型那一次） |
| 2 | `name_a_to_b` 没有唯一约束 | 能建两个一模一样的 "parent of"，admin 下拉出现两个同名选项，选哪个都对但数据分裂成两半 | `UniqueConstraint(Lower(Trim("name_a_to_b")))` —— 大小写不敏感（普通唯一约束挡不住 "Parent of" vs "parent of"，那和缺口 1 的 `clean()` 口径也对不上），**`Trim` 也不能省**：只靠 `save()` strip 的话 `" parent of"` 会被 `bulk_create` 塞进来，见 D9 归一化通则 | 强制层 |

**根因说明（比补法更重要）**：缺口 1 的根本原因是**那个反向类型行本来就不该存在** ——
"child of" 已经是 "parent of" 的 `name_b_to_a` 了。防线加在建类型那一刻，
而不是之后每一处用到它的地方。

`is_symmetric` 是显式字段，不靠推断。 过去只能靠"`name_b_to_a` 为空"隐式推断对称性，
但录入的人完全可能把 "spouse of" 同时填进正反两栏，推断就失效了。
显式布尔让"这个标签反着读还是它自己"有个明确依据。

**方向约定必须写死**（`EmergencyContact.relationship_type` 的 `help_text` 里）：
不写死一定会录反 —— "王秀英 + 母亲"到底是"王秀英是小明的母亲"还是反过来，
读的人和录的人可以有两种理解，而两种都不报错。

##### `code` 的三步迁移（给已有的 `RelationshipType` 加字段）

唯一且非空的 `code` 不能一步加到**有数据**的表上。必须三个迁移：

1. 加可空的 `code` 字段；
2. 数据迁移回填（从 `name_a_to_b` slugify，撞车的手工处理）；
3. 改成 `null=False`，并加上 `UniqueConstraint(Lower("code"))`
   （**不是**字段上的 `unique=True` —— 见 D9 归一化通则）。

> ⚠️ **本机 `RelationshipType` 实测 0 行**（`02-roadmap.md` B0），所以这一次一步到位即可，
> B2 里写了简化和它的适用条件。**上面这三步规则不删** —— 它对以后任何"给有数据的表加唯一字段"
> 仍然成立，只是这一次前置条件不满足。

**不要图省事用 `default=""` 一步到位** —— 那样所有行的 code 都是空字符串，
唯一约束当场炸。新建的字典表（`Ministry` / `EmploymentType` / `EventType` /
`ParticipationRole`）不受影响，建表时就带上。

**"不可改"怎么落地**（D5 只写了要求，没写机制）：
`editable=False` 只挡 ModelForm，脚本照改。做法是 admin 的 `get_readonly_fields`
在 change（非 add）页把 `code` 设为只读，加上 `clean()` 里比对数据库中的旧值。

##### 演示数据：`seed_demo` management command

本阶段要反复验证一人多岗、跨 kind 汇报线、一人一活动多工种、以及**三种权限角色**
这些场景，手点 admin 太慢。`python manage.py seed_demo` 一条命令造出一组互相关联的假数据。

> 2026-07-29 补：它现在还要造出账号和授权。 验收要扮演三个角色
> （总管 / ministry admin / 普通志愿者），每次手建三个账号 + 授权太慢，而且容易建错
> 一个"其实什么权限都没有"的账号，然后误以为越权检查生效了。
> **必造：一个 `foundation_admin`、两个不同 ministry 的 admin（用来试越权）、
> 两个普通志愿者（其中一个未成年）。**
>
> 2026-07-29 晚补齐：上面这一行造不出验收要走的几个分支。 验收清单还要求
> "0 人报名的工种""纸质补录（没有签到时间戳）的人""联系不上的人""生日未知的人"，
> 而这些**手建同样慢、同样容易建错**，正是这条命令存在的理由。补上：
>
> | 还必须造 | 为了验收哪一条 |
> |---|---|
> | 一个**没有 email 也没有电话**的报名者 | P6 的「联系不上（N 人）」分组 —— 造不出来，就会误以为这段代码是对的 |
> | 一个**生日为空**的报名者 | `is_minor` 三态的保守侧：按未成年处理、通知家长 |
> | 一个未成年报名者，**带 `consent_email` / `consent_phone`** | P6 ①「通知家长」真的有地址可用 |
> | 一个 `hours` 手工填、**没有签到时间戳**的 `Participation` | 纸质补录照样算数（`hours` 是权威值） |
> | 一个 **0 人报名的 `EventRole`** | D19 的验收点（R4 答"5"不是"3"） |
> | 一场 **`status=draft`** 和一场 **`status=confirmed`** 的活动 | P3 的可见性闸门 + 「[可见性与生命周期](#可见性与生命周期两个谓词不是一个-status2026-07-29-晚新增)」那条（`confirmed` 的活动报过名的人**仍然打得开**） |

**三条安全要求，一条都不能省**：

1. **幂等** —— 全部用 `get_or_create`，跑三次不会得到三套张三（否则重名提示天天弹）；
2. **拒绝在非开发环境运行** —— 命令开头 `if not settings.DEBUG: raise CommandError(...)`，
   再加一个 `--force` 才能绕过。Phase C 上线后一次误运行就是往生产库灌假联系人，
   而按本设计它们和真人长得一模一样，事后极难清干净；
3. **只造假数据** —— 不要把任何真实的人写进代码库，名字也用明显虚构的。

##### 必须写的测试

Phase A 的 A10 用了"每条钉住什么"的清单，本阶段沿用。下面这些一条都不能少：

> **这张表钉的是"要保证什么"，不是函数名。** 2026-07-30 做过一次机器核对：
> 两份文档点名的 149 个 `test_*` 里，**14 个在代码里叫别的名字**
> （`..._inactive_ministry_...` → `..._retired_ministry_...`、
> `test_active_uses_the_foundation_timezone...` → `test_local_today_uses_...` 之类），
> 逐条查过，行为都覆盖了；**只有 1 个是真缺口** ——
> `test_signing_up_over_needed_count_is_allowed_but_flagged` 当时只测了 `allowed`，
> 没测 `but_flagged`，已补。
>
> 记这一笔是因为**名字对不上会让下一次核对重新查一遍这 14 条**。
> 权威是各 app 的 `tests.py`，不是这张表里的字面量。

| 测试 | 钉住什么 |
|------|---------|
| `.active()` 边界：`end_date == 今天`算在职、`== 昨天`不算 | 派生逻辑的下界 |
| `.active()` 边界：`start_date` 在未来**不算**在职 | 上界 —— 原定义漏掉的那一半 |
| `.active(on=某日)` 能改变结果 | 时钟可注入，且没有被冻结在导入时 |
| 太平洋时间晚 8 点（UTC 已次日）判定不跨天 | D16 的时区口径 |
| 全项目没有 `date.today()` / `timezone.now().date()`（grep 守卫，放 `core/tests.py`） | D16 —— 同迁移守卫，用测试当 lint |
| 全项目没有「直接问某个存下来的时刻要日期」（`*_time` / `*_at` 后面跟取日期，grep 守卫） | D16 的第二句（2026-07-30 补）。存进库的是 UTC，太平洋时间傍晚的活动答的是次日 —— R8 就是这么错的，而且不报错。<br>这条守卫上线当场又抓到两处，都在同一天写的、**专门用来验 R8 时间口径**的测试里 |
| 各 app 的 `forms.py` / `services.py` / `models.py` 都不出现 `django.contrib.admin`（grep 守卫，放 `core/tests.py`） | D18 分层 —— 让"这些代码前端上来能原样复用"从承诺变成机器检查的事实。第五次「测试当 lint」 |
| 每条业务约束都有 `violation_error_code` 且在 `CONSTRAINT_FIELD` 里有映射（遍历所有 model 的 `Meta.constraints`） | D14 —— 把"改一处必须改另一处"的注释纪律换成机器检查 |
| 每条约束在 admin 表单里提交违规数据时报的是表单错误、不是 `IntegrityError` | D14 的坑：`CheckConstraint.validate()` 遇 `FieldError` 会静默跳过，表达式约束尤其要逐条实测 |
| `Assignment` 唯一约束在 `start_date` 为空时生效 | `nulls_distinct=False` —— A7 的教训，新表重钉一遍 |
| 同一人在同 ministry 的**两个不同 `Position`** 上能各有一行 | 唯一约束没有误伤"一人多岗"（D11 的核心场景） |
| 同一人同一 `Position` 的**两段任职**（不同 `start_date`）能存两行 | 离开又回来是合法的 |
| 一人多岗各有不同上级，能分别查出 | D11 第一次修订要解决的歧义 |
| **换人不动下属**：给某 `Position` 换一个在任者，其下属编制的 `reports_to` 一个字节没变 | D11 第二次修订的全部意义就在这一条，其余测试都可以没有，这条不能没有 |
| **请假不动日期**：`status=on_leave` 后 `.serving()` 排除他、`.active()` **仍然包含他**，且 `start_date`/`end_date` 一个字节没变 | 状态与任期正交 —— 这条钉住"永远不用截断 `end_date` 表达请假" |
| `status=on_leave` + `end_date` 在过去时，`.serving()` 和 `.active()` **都**排除他 | `status` 只收窄不覆盖；陈旧状态是惰性的，不会把已离任的人放回来 |
| `Assignment.Status` 里**没有** "ended" 之类的取值 | 结束只由日期表达，不许记两处 |
| **空缺**：`Position` 的在职 `Assignment` 全部结束后进入 `vacant()`，且仍带着 kind / ministry / 下属 | 空缺是一等状态，不是"碰巧查不到人" |
| `is_active=False` 的 `Position` **不出现**在 `vacant()` 里 | 撤销 ≠ 空缺 |
| `vacant(on=某日)` 能改变结果 | 时钟可注入，同 `.active()` |
| 跨 kind 汇报线：执行总监(employee `Position`) `reports_to` 理事长(board `Position`) | D11 的理事会安排 |
| `Position.reports_to` 指向自己那一行被数据库拒绝 | `CheckConstraint` |
| A→B→A 成环时 `Position.clean()` 拒绝 | 提示层防线 |
| `bulk_create` 直插一个 A→B→A 的环之后，`build_org_tree()` 不挂死、不 `RecursionError` | 第二道防线 —— 提示层绕得过去，所以遍历必须自己扛得住脏数据 |
| `build_org_tree()` 建一棵三层树只用 1 次查询（`assertNumQueries(1)`） | 钉住"取全表在内存建树"，防止有人改回逐级 `position.reports_to`（那是 N+1） |
| 全项目除 `org/services.py` 外没有别处递归 `reports_to`（grep 守卫，放 `core/tests.py`） | 把"遍历要带 `visited`"从纪律换成结构。**第六次「测试当 lint」** |
| 删一个有下属的 `Position` 被 `PROTECT` 挡住 | 子树不会被静默重挂成根 |
| 删一个有任职记录的 `Position` 被 `PROTECT` 挡住 | 任职历史不会跟着编制消失 |
| 删一个有任职记录的 `Contact` 被 `PROTECT` 挡住 | 另一头同理（2026-07-30 从 `CASCADE` 改）—— 漏的那批恰恰是**只做过员工、没做过志愿者**、任职历史最长的人 |
| `Position.code` 唯一，且 change 页只读 | 代码锚点真的稳定（D5） |
| `Participation` 同活动同人**同工种**第二行失败；**不同工种**能存两行 | 一人一活动多工种 |
| `Participation.hours` 负数失败 | |
| `status != attended` 时 `hours` 非零失败 | 单一真相，不许自相矛盾 |
| `Event` `end_time < start_time` 失败 | |
| ⭐ 一场活动开了 5 个工种、只有 3 个有人报，R4 仍然答"5" | D19 的全部意义就在这一条。 其余活动侧的测试都可以没有，这条不能没有 —— 它就是「空缺编制」那条测试换了张表 |
| `EventRole.understaffed()` 里出现零报名的工种 | 同上的正面版：缺人的工种必须**看得见**，看不见就等于没建这张表 |
| `EventRole` 同一场活动同一工种开两遍失败 | 否则 `needed_count` 有两个答案，R4 直接翻倍 |
| `with_signup_counts()` 算 N 个工种只用 1 次查询（`assertNumQueries`） | annotation 不是 property —— 同 `with_headcounts()` 那条（B5 已有），防止有人改回每行一查 |
| R6 / R7：总工时 = 各工种工时之和，且 `hours=None` 的行不被当成 0 | `null=True` 的意义：报名了还没发生 ≠ 干了 0 小时 |
| R8：活动当天在职的 employee 查得到；活动之后才入职、以及活动之前已离职的**不出现** | `.active(on=活动当天)` 那个参数的全部意义。 用默认值（今天）写这条查询会静默漏人 |
| R8：一人在同 ministry 占两个 employee 编制时，只算一个人 | `.distinct()` 不能省 —— 漏了就是人数悄悄多一个 |
| R8：请假中（`status=on_leave`）的 employee 参加了活动，仍然**算进去** | 用 `.active()` 不是 `.serving()` —— 问的是"当时是不是员工"，不是"今天能不能当值" |
| 签退写入 `hours` 之后手工改 `hours`，再签退一次不会覆盖掉手工值 | `hours` 是权威值，时间戳只是采集手段 |
| `checked_out_at < checked_in_at` 失败；签到了又标 `absent` 失败 | 「是否来过」只能有一个答案 |
| 未成年人没有同意记录时 `sign_up()` 报名失败（`Participation` 建不出来）；已建的行不能被 `check_in()` 标成 `attended` | P3。跨表判断（年龄在 `Contact` 上），按 D14 记为提示层 —— **测试要断言它是 `ValidationError` 而不是数据库拒绝**，不许假装它是强制的。<br>⚠️ 不要写成"不能进 `confirmed`"——那是 `Event.Status` 的档位，不是 `Participation` 的 |
| `MinistryRole`：A ministry 的 admin 对 B ministry 的 `can_publish_event()` 返回 `False` | D20 的全部意义。 这条不过，等于范围化权限没做成 |
| `MinistryRole` 过期（`end_date` 在昨天）后权限立即失效 | 授权复用 `.active()`，不是一个永久布尔 |
| 没有任何 `MinistryRole` 的普通账号，`can_view_registrations()` 一律 `False` | 默认拒绝，不是默认允许 |
| `can_grant_ministry_admin()` 只认全局 Group，不认 `MinistryRole` | P5 是真·全局权限 —— ministry admin **不能自己给自己发展下线** |
| 全项目除 `org/permissions.py` 外没有 `MinistryRole.objects` 的直接查询（grep 守卫） | 权限判断只写一处。**第七次「测试当 lint」** —— 漏一处权限检查的症状是静默越权，不报错 |
| ⭐ 活动从 `open` 改成 `confirmed` 之后，已报名的志愿者**仍然打得开**它的详情页；`draft` 的活动仍然 404 | [可见性与生命周期](#可见性与生命周期两个谓词不是一个-status2026-07-29-晚新增) —— 把可见性写成 `status=open` 的话，P6 通知里那句"来不了请点这里取消"的链接会 404，而且专门发生在人已招满的活动上 |
| 活动列表页（`open_for_signup()`）里不**出现 `confirmed` / `completed` 的活动** | 同上的另一半：能不能看见 ≠ 能不能报名。两个谓词各管一件事 |
| **`user.contact is None` 时所有 `can_*()` 返回 `False` 且**不**抛异常；superuser 也不例外** | [D20 的边界](decisions/D20-ministry-role.md#判断只写一处) —— `MinistryRole` 挂 `Contact`、入口是 `user`，而 `User.contact` 必须可空（D12），所以这不是异常而是一种正常状态。**特批 superuser 等于在 `permissions.py` 里开一个绕过 ministry 范围的后门** |
| 志愿者账号（`is_staff=False`）访问 `/admin/` 得到 403 | D21 第 1 条。不是"没给链接"，是真的进不去。<br>2026-07-30 补：**Django 自己给的是 302 跳 admin 登录页**，而那一页会叫一个已经登录的人"输入 staff 账号的密码" —— 既是假话又是死循环。这条要成立得靠 `core/middleware.py::StaffOnlyAdminMiddleware`；**匿名访客照旧跳登录页**（他们可能就是还没登录的 staff） |
| 志愿者账号访问别人的报名记录得到 404/403；活动列表里看不到 `status=draft` 的活动 | D21 第 2 条 —— **查询层的隔离，不是模板层的**。测试直接打 URL，不看页面 |
| `register_account()` 建 account 的同时建了 `Contact` 并挂上；中途失败时两个都不留下 | P1 + 事务性。半个账号比没有账号更难查 |
| `User.contact` 仍然允许为空（建一个 superuser 不报错） | D21 第 3 条 —— P1 是流程约束不是字段约束，别顺手改成 `null=False` |
| ⭐ 未成年报名者的通知收件人是家长，不是他本人 | D22 ① —— 15 岁的志愿者可能没有自己的手机。这条不过，P6 对最需要被通知的那群人就是失效的 |
| 生日未知的报名者也按未成年处理（通知家长） | 保守侧，同 B4.5 的三态口径。折叠成"成年"会让人静默漏掉 |
| 一个没有 email 也没有电话的报名者出现在 `unreachable` 里，且不计入 `recipients`；发完之后**从库里查得出他是谁**（不只是"有 1 个人") | D22 ② —— "已通知 27 人"掩盖 3 个联系不上的人，是本文档反复判过刑的那种静默失败。后半句是 `unreachable` 从计数改成 M2M 的验收点 |
| 事后给那个人补了电话之后，历史那条 `EventNotification` 的 `unreachable` 里仍然**有他** | 它是快照不是派生值。同 `hours` 是权威值 |
| 未成年报名者只填了 `consent_phone`（没有 email）时，收件人是那个号码、渠道是 sms；两个联系方式都空则落进 `unreachable` | D22 ① 的可投递性 —— 光有家长**姓名**（`consent_given_by`）解析不出地址。**这条不过，"通知家长"就只是一句话** |
| `sign_up()` 拒绝"未成年 + 有同意记录但没有任何家长联系方式"的报名 | 同上的入口侧防线：不让一条注定通知不到的报名进库 |
| `resolve_recipients()` 的测试不发生任何网络调用（用 locmem / console 后端） | 业务规则的测试不许依赖 provider —— 换 provider 时这批测试一条都不该变红 |
| `NotificationBackend` 的实现里搜不到 `Contact` / `Participation` / `is_minor`（grep 守卫） | D22 的分层 —— 后端只认（地址, 渠道, 内容）。**第八次「测试当 lint」** |
| `views.py` 里搜不到任何日期计算、`Sum` / `Count`（grep 守卫） | **第九次「测试当 lint」**（2026-07-29 晚补）—— 视图是薄壳，统计在 QuerySet 方法里；否则 R4–R8 会跟着模板一起被重写一遍。<br>⚠️ 这一条原来**只写在验收清单里**，没有对应的测试 —— 而验收是"我记得点一遍"，守卫测试才是机器检查 |
| 所有 `admin.py` 里搜不到 `save_model` / `save_related` / `get_queryset` / `get_formset` 四个钩子的重写（grep 守卫） | D18 落点表的强制版。**第十次「测试当 lint」**（2026-07-29 晚补）。同上：原来只在验收清单里 |
| 全仓每个 `.md` 的每一处链接都指得到（文件存在 + 锚点存在），新文档自动纳入 | **第十一次「测试当 lint」**（2026-07-30 补，`core.tests.MarkdownLinkGuardTests`）。<br>这套文档是这个项目的记忆，而它靠几百处交叉引用连在一起 —— **一条坏链的失效方式正是本文档反复判刑的那种：不报错，读者只是落到空处**。而 2026-07-30 的拆分把每一个目标都搬了位置，"人肉核一遍"从此不成立。<br>⚠️ 锚点跟着**标题文字**走，所以**改标题就会打断所有指向它的链接** —— 这条测试就是为了让那件事当场变红。写它的当天抓到 4 处真坏链，其中一处是几分钟前刚写的 |
| 每份 `.md` 里没有整格加粗 / 整行加粗 / 变更说明上的 `⚠️`，且 `⭐` 每份不超过 4 个 | **第十二次「测试当 lint」**（2026-07-30 补，`core.tests.EmphasisGuardTests`）。<br>盯的是[强调配额](goal.md#强调的用法三个符号各只有一个意思2026-07-30)里**定义上就不合规**的那四类，让那次清理不会悄悄回潮。"哪一处才承重"要逐段读，机器判不了，所以守卫只管这四类 |
| 通知正文里不出现未成年人姓名 | D22 代价 2 的缓解措施，PII 出境面收窄到"一个邮箱 + 一段活动公告" |
| `EventNotification.message` 在活动之后再改时**不跟着变** | 快照 —— 这条记录说过的话不能被后来的编辑改写 |
| 删掉发通知的那个 `User` 之后，`EventNotification` 还在（`sent_by` 变 `NULL`） | 留痕类字段一律不 `CASCADE` |
| 两个都叫"王强"的联系人 `__str__` 不同 | 所有 autocomplete 的正确性 |
| **同名同号**第二条被表单**拒绝保存**；勾上 `force_save` 后能存 | 硬拦截真的拦得住，且没有否认重名合法 |
| **仅同名**（号码不同）**不**拦截，只出警告 | 拦截没有绑错信号 —— 绑到同名上就会被点习惯 |
| ~~`BackgroundCheck` 是独立模型~~ | 随 `BackgroundCheck` 一起移出本阶段（2026-07-29）。建表时这条测试要一起写 |
| `RelationshipType` 建 "Parent of" 与已有 "parent of" 冲突 | 缺口 2 大小写不敏感 |
| `bulk_create` 直插 `" parent of"`（带前导空格）与已有 "parent of" 冲突 | 缺口 2 的 `Trim()` —— 只靠 `save()` strip 是漏的 |
| `RelationshipType.code` 唯一，且 change 页只读 | D5 的锚点真的稳定 |
| `Ministry.code` / `Position.code` / 其余字典表 `code` 唯一 | 同上 |
| `bulk_create` 直插 `Food_Pantry` 与已有 `food_pantry` 冲突 | `Lower("code")` —— 只靠 `save()` 转小写是漏的（D9 归一化通则） |
| `is_minor` 对 `birth_date=None` 返回"未知"而不是 `False` | 未成年人不会静默消失 |
| `is_minor` 边界：18 岁生日当天 | |
| `.minors()` / `.adults()` / `.birth_date_unknown()` 三者**互不重叠、并集是全表** | 阈值只算一处；`birth_date` 为空的人不会两边都不落 |
| `.minors(on=某日)` 能改变结果 | 时钟可注入，同 `.active()` / `.vacant()` |
| `EventRole.understaffed()` 在 `needed_count` 为空时**不**把该行算成缺人 | 不限人数 ≠ 缺人。判断在 QuerySet，admin 只负责显示成黄条 |
| `EmergencyContact` 不填 `relationship_type` 被拒 | 「关系必填」= FK 非空，拆表白捡的简化 |
| 同一个人身上录两条同名同号的紧急联系人被拒 | `UniqueConstraint(person, Lower(Trim(name)), phone)` |
| 一个人可以有**两个不同**的紧急联系人 | 表天然支持多个，没有人为的基数限制 |
| 删掉一条 `Contact`，他名下的 `EmergencyContact` 跟着删（`CASCADE`） | 紧急联系人是附属数据，没有独立生命周期 |
| `Contact` 里没有 `is_reference_only` 字段，也没有 `emergency_contact` 外键 | 幽灵记录不该存在 —— 钉住这次修订的结果 |
| 同名同号命中 / 同名不同号**不**命中 / 同号不同名**不**命中 | `Contact` 查重规则的三条边界，一条都不能松 |
| 合并：遍历 `Contact._meta.related_objects`，**断言每一项要么被搬走了、要么在显式的跳过名单里**（名单只有 `Historical*`） | 漏一张表的症状是记录静默消失。**这个写法比"测试里新造一张表"更强**：以后任何人给 `Contact` 加了新外键却没决定合并时怎么处理，这条测试当场变红（见 `02-roadmap.md` B4 测试注） |
| 合并：一对一冲突（两条都有 `User`）时**拒绝**并说明原因 | 不自作主张删一边 |
| 合并：唯一约束冲突（同活动同角色都有 `Participation`）时**拒绝** | 同上 |

> ⚠️ **关于那个"第 N 次「测试当 lint」"的编号**：它是**手工维护的计数器**，
> 而本文档一贯的立场是"凡是要靠人每次都记得的东西都不可靠"（[D14](decisions/D14-constraint-is-the-only-rule.md#d14--约束是唯一的规则字段级提示靠映射表不靠重写一遍) / [第九轮](revisions.md#第九轮--汇报线遍历从每处都带-visited改成只有一处遍历)）——
> 所以它只是叙事，不是索引，别拿它当"一共有几条守卫"的依据。
> 权威是 `core/tests.py` 里那一组 grep 守卫本身（全部放在同一个文件里，就是为了能一眼数完）。
> 2026-07-29 晚补的两条（第九、第十）**正是靠人肉发现漏登记的** —— 这个计数器自己就是个例子；
> 2026-07-30 的第十二条（强调守卫）同样是事后才补进本表的。
>
> 2026-07-30 修版：这段注原来夹在上面这张表的**中间**，把表格从那一行截断了
> （后面三十多行测试掉出表格渲染成散文）。**表格里不要插引用块** —— 同"正文不写代码行号"，
> 是一条格式上的硬规矩。

##### 验收（2026-07-29 重写：改成按 14 条需求逐条验收）

> 原验收清单（7 条"能演示一遍日常"）已被替换。 它的问题是**答不出"做完了没有"** ——
> "能演示一遍"没有边界。现在的标准是[零](goal.md#零当前优先级2026-07-29-定)那 14 条，
> 一条一条勾。B0–B5 覆盖的部分（编制 / 任职 / 紧急联系人 / 合并）已经验收过，不重复。

扮演三个角色，在本机浏览器里各走一遍。数据来自 `seed_demo`，不进任何真实的人。

① 扮演基金会总管（全局 Group `foundation_admin`）

- [ ] **P5**：指定张三为「食物银行」的 admin —— 建一行 `MinistryRole`，
      `granted_by` 自动记成当前登录的自己
- [ ] 撤销时填 `end_date`，**不删行** —— 授权历史留着（同"结束只由日期表达"）
- [ ] **R1–R3**（在 admin 侧看）：按时间段筛活动，每场显示 ministry 和时长。
      ⚠️ 2026-07-30 从下面 ③ 挪上来的：**这一条原来挂在"扮演普通志愿者"名下，
      而同一组的上一条要求他打开 `/admin/` 得到 403** —— 一个进不去 admin 的角色
      验不了"在 admin 侧看"。这类自相矛盾的验收项**跑起来一定会被跳过**，
      跳过之后没人知道 R1–R3 到底验没验

② 扮演食物银行的 admin（张三）

- [ ] **P2**：发布一场活动，ministry 只能选食物银行（选不了报税志愿）；
      开三个工种，分别写"搬运 5 人 / 签到 2 人 / 翻译 1 人"；
      从 `draft` 改成 `open` 之后志愿者才看得见
- [ ] **越权检查**：把 URL 里的活动 id 换成报税志愿那场，**得到 403** ——
      这一条不过，D20 就是白做的
- [ ] **P4 上半**：看到每个工种"需要 N 人 / 已报 M 人"，翻译那个工种 0 人报名
      **照样显示在列表里**（这就是 D19 的验收点）
- [ ] **P4 下半**：活动当天给到场的人签到，签退时工时自动填上；
      给一个纸质补录的人**手工填工时**（他没有签到时间戳），照样算数
- [ ] **R4–R7**：活动结束后看统计 —— 工种数是 **3**（不是 2，虽然翻译没人来）、
      各工种人数、总工时、按工种分的工时
- [ ] **R8**：同一页面上列出"本 ministry 的 employee 参与情况" ——
      谁参加了、分别在哪个工种。**把其中一个人的任职 `end_date` 改到活动之前，
      他应该从这个名单里消失**（时间口径是活动当天，不是今天）
- [ ] **P6**：把活动时间从周六改到周日 → 点「通知报名者」→ 预览页上
      **成年人显示自己的邮箱、未成年人显示家长的联系方式**、
      **一个没填邮箱也没填电话的人出现在「联系不上（1 人）」那一组里**；
      确认发送后回到活动页，能看到"5 分钟前通知过 N 人"。
      **那个「联系不上」的分组不出现，这一条就算没过** —— 它是 P6 里唯一会静默失败的地方
- [ ] **P6 越权**：拿另一个 ministry 的 admin 账号打同一个通知 URL，**得到 403**

③ 扮演普通志愿者（李四，`is_staff=False`）

- [ ] **P1**：从注册页新建账号 → 数据库里同时多了一个 `User` 和一个 `Contact`，已挂好
- [ ] **访问 `/admin/` 得到 403** —— 不是跳登录页，是真的进不去
- [ ] **P3**：活动列表里**只**看得到 `status=open` 的活动；点进去按工种报名
- [ ] **P3 未成年人**：用一个有生日的未成年账号报名 → 被要求填家长同意
      （姓名 / 关系 / 方式 / **邮箱或电话至少一个**），不填**报不成**。
      **邮箱和电话都留空也必须报不成** —— 否则同意收了、P6 却通知不到家长
      （2026-07-29 晚补：原文只列了前三样）
- [ ] **越权检查**：直接打别人报名记录的 URL，**得到 404 或 403**
- [ ] 生日未知的账号报名，**同样**被要求填家长同意（三态的保守侧）

④ 分层验收（不用点浏览器，grep 一遍就行）

- [ ] 所有 `admin.py` 里搜不到 `save_model` / `save_related` / `get_queryset` /
      `get_formset` 这四个钩子的重写
- [ ] `forms.py` / `services.py` / `models.py` / `views.py` 里搜不到 `django.contrib.admin`
- [ ] **`views.py` / `admin.py` 里搜不到 `MinistryRole.objects`** —— 权限只问 `permissions.py`
- [ ] **`views.py` 里搜不到任何日期计算或 `Sum` / `Count`** —— 视图是薄壳，
      统计在 QuerySet 方法里（否则 R4–R8 会跟着模板一起被重写一遍）

> **这四条一起，就是 D18 那句判据的可执行版本：把 `admin.py` 和 `templates/` 都删掉，
> 剩下的必须是全部业务逻辑。**
>
> 2026-07-29 晚：这四条已经全部进了[「必须写的测试」](#必须写的测试)的 grep 守卫清单。
> 原来只有第二条（`forms.py` 不许 import admin）有测试，其余三条只存在于这份验收清单里 ——
> 而验收是"我记得点一遍"，守卫测试才是机器检查。这一节现在是那几条测试的人肉复核，不是唯一防线。

> 交付给基金会真用属于 Phase C，不是本阶段。
> 本阶段的验收只到"我自己扮三个角色跑通"，用演示数据。
> Phase C 那两件事是**交付的前置条件，不是可选项**：
> 1. **备份 + 真的演练一次恢复** —— 真实数据进来之前必须就位；
> 2. **权限复核** —— 权限本身已经在本阶段建好（D20 / D21），Phase C 要做的是拿真账号
>    再验一遍越权，以及确认没人用 superuser 登录。
>
> 这两条叠加起来风险是乘法关系（能删 × 删了找不回）。
