# ROLF_APP — 目标、重大决策与进度

> 这份文档是项目的**唯一权威来源**，记录三件事：我们要做什么、做过哪些重大决策（以及为什么）、
> 现在走到哪了。做了新的重大决策或完成一个阶段就回来更新这里。
>
> 相关文档：`00-getting-started.md`（方法论与心态）· `01-roadmap.md`（**当前 Phase 的实施步骤**，
> 眼下是 Phase A；它服从本文档的决策，冲突时以本文档为准）
>
> 最后更新：2026-07-27

---

## 一、终极目标

为一个非营利基金会做一个 web application，帮他们**管理志愿者**并**追踪各类资源（人、钱、活动）**。

### 五个核心诉求，以及分别靠什么落实

| 诉求 | 怎么落实 |
|------|---------|
| **便宜** | 单体 Django 应用 + 托管平台，无付费 SaaS、无微服务、无独立前端；起步阶段月成本控制在几十美元内 |
| **好维护** | 一个人能读完全部代码；标准 Django 写法，不自造框架；有测试所以敢改 |
| **可扩展** | 数据模型抄成熟系统的抽象层次（见 D4–D6），加功能是加表，不是改表 |
| **需求变了还能用** | 会变的东西做成**数据**而不是**代码**（关系类型、技能、活动类型都是字典表，在 admin 里加，不用改代码不用迁移） |
| **数据自主且安全** | 标准 Postgres 库，一个 `pg_dump` 就能整体带走；不锁定在任何厂商的专有格式里 |

### 方法论：抄结构，不抄代码

参考 CiviCRM、ERPNext/Frappe 的**数据模型和设计智慧**，业务逻辑和界面全部自己写。

- ✅ 值得学（概念层）：字段设计、状态流转、审计日志、Contact 这类核心抽象
- ✅ 自己写：具体业务流程、界面、报表 —— 这些正是"这个基金会和别人不一样"的地方
- ❌ 别碰：插件系统、多租户、国际化框架、复杂权限引擎 —— 通用产品的负担，一个组织不需要

### 交付策略

先搭最小可用版本（MVP），**在真实使用中摸清基金会到底需要什么**，再逐步扩展。
真实使用暴露的需求比现在猜的准得多，所以每个阶段结束都必须是"能给基金会的人看、能真实录一条数据"的状态，永远不憋大版本。

---

## 二、技术选型

| 层 | 选择 | 备注 |
|----|------|------|
| 后端 | Django 5.2 | 自带 ORM / Admin / 认证 / 权限 |
| 数据库 | PostgreSQL | 数据高度关系化，不碰 NoSQL |
| 界面（起步） | Django Admin | 不写前端 |
| 界面（以后） | Django 模板 + HTMX | 等后端和数据模型稳定了再做 |
| 部署 | 托管平台（Render / Fly.io）+ 独立托管 Postgres | 数据库独立于应用，随时能迁走 |

---

## 三、重大决策记录

每条记录：**结论 → 为什么 → 代价 / 何时重新考虑**。

### D1 · Django + PostgreSQL + Admin 起步
Django Admin 本身就能当半个 MVP，几乎不写前端就能增删改查。Postgres 是关系型里最稳的选择，
生态和资料最友好。
**代价**：Admin 界面不好看，不能给外部用户用 —— 接受，前期用户只有内部管理员。

### D2 · 前端推迟到后端与数据模型完善之后
一开始就上 React/Vue 会让工作量翻倍，而且模型还在变的时候写界面等于白写。
**何时重新考虑**：需要做志愿者自助登录、或需要给外部用户看的页面时。

### D3 · 数据永远是一个标准 `pg_dump` 能带走的 Postgres 库
这是"数据掌握在自己手里"的具体定义。应用可以换、平台可以换，数据不被绑架。
**推论**：不用平台专有的数据服务，不用需要特殊导出流程的存储。

### D4 · Contact：人和组织统一成一张表
用 `contact_type` 字段区分 individual / organization。这是 CiviCRM 的核心洞察。
**为什么**：捐款人可能是公司，活动主办方可能是机构。拆成两张表的话，
所有"跟某个联系人有关"的功能（捐款、关系、通信记录）都要写两遍。
**代价**：有些字段只对一种类型有意义 —— 已用 `Contact.clean()` 校验 + `save()` 清空
+ admin 里 JS 隐藏无关字段来处理。

### D5 · 会变的分类做成字典表，不做 Python 枚举
`RelationshipType` 是数据库表。基金会以后想加"紧急联系人"、"推荐人"这类关系，
在 admin 里加一行就行 —— 不用改代码、不用写迁移、不用重新部署。
**这是"需求变了还能用"最直接的体现**，后续 Skill、Department、活动类型、捐款类型、
付款方式一律照此办理。

> 注意"理事会成员"**不是**关系类型 —— 理事走 `kind=board` 的 `Assignment`（见 D11）。
> 判断方法：**这个人在基金会担任的职务 → `Assignment`；这个人和另一个人/组织之间的联系 → `Relationship`。**

### D6 · 一张通用 Relationship 表表达人与人 / 人与外部组织的关系
`Relationship(contact_a, contact_b, relationship_type, start_date, end_date)`
一张表覆盖：外部组织归属、亲子（parent of）、未成年人监护链接、紧急联系人 ——
不用为每种新关系建新表。
**代价**：查询比专用表稍绕。

> **适用范围（2026-07-27 收窄）**：**基金会内部的岗位与汇报线不走这张表**，走 `Assignment`（见 D11）。
> 原因是 Relationship 说不清"这条汇报线属于这个人的哪个身份"。
> `manages` / `managed by` 关系类型因此不再用于组织架构。

### D7 · 标准化字段用成熟库，做完整的下拉
电话用 `django-phonenumber-field`（存 E.164 国际格式，含区号）、
国家用 `django-countries`（完整 ISO 3166）、美国州用 `django-localflavor`（完整 50 州）。
**为什么**：这些数据的正确性和完整性是解决过的问题，自己列一遍必然出错、必然缺项。

### D8 · Language 自建表（ISO 639-3），不用 `django-languages-plus`
`languages-plus` 的表键在 2 字母 ISO 639-1 码上，**排除了 Mandarin (cmn)、Cantonese (yue)、
Hmong 等**，而这些正是基金会最常服务的语言。所以自建 `Language` 表，
由数据迁移从 `pycountry` 灌入约 7900 行 ISO 639-3，并加 `pin_rank` 字段让常用语言排在下拉最前面。
**代价**：多一张自己维护的表 —— 但换来的是能正确记录服务对象的语言，这是刚需。

### D9 · 业务规则写在 model 层，不只写在 form 里
`Contact.clean()` 和 `Contact.save()` 承载"名字必须匹配 contact_type"的规则。
**为什么**：从 admin 存、从脚本存、从以后的 API 存，行为都一致。写在 form 里只能管住一条路径。

### D10 · 人只有一份档案；「角色」和「岗位」是两个不同层次
❌ 不建独立的 `Volunteer` / `Employee` 模型（里面又有姓名电话地址）。
✅ `Contact` 是所有人的唯一档案：

```
Contact ←1:N→ Assignment              (任职：职务、部门、上级、起止 —— 见 D11)
Contact ←1:1→ VolunteerProfile        (技能、可服务时段、背景审查状态)
Contact ←1:N→ Contribution            (捐款)
Contact ←N:M→ Event via Participation (参与活动 + 工时)
```

**为什么一份档案**：一个人既捐款又做志愿者时，独立表方案会产生两份档案、两个地址，
改一个另一个不同步 —— 这是小系统最常见的死法。员工尤其如此：基金会的员工经常自己也捐款、
也带活动，甚至是先做志愿者后来入职的 —— 这种情况下"同一个人"必须是同一条记录。

**三层判断标准**（这是 2026-07-27 修订的重点，原来只有两层，见 D11 的修订说明）：

| 层次 | 问题 | 放哪 | 例子 |
|------|------|------|------|
| **人** | 这个人是谁？ | `Contact` | 姓名、联系方式、地址、语言、生日 |
| **角色** | 这个人对基金会是什么身份？换岗位也不变的事实 | `VolunteerProfile` 等 1:1 角色表 | 技能、背景审查状态、可服务时段 |
| **岗位** | 这个人担任什么职务？**同一个人可以有多个** | `Assignment` 1:N | 职务名、部门/项目、上级、雇佣类型、起止日期 |

**关键**：凡是"同一个人可能同时有多份"的信息，就不能放在 1:1 的角色表里。
背景审查是对**人**做的（换岗不用重查）→ 角色表。职务和上级是**每个岗位各有一份** → `Assignment`。

### D11 · 任职做成 `Assignment` 表（1:N），上级挂在岗位上
> **这条在 2026-07-27 修订过，替换了原来的方案。** 原方案是
> `EmployeeProfile` 一对一 + 汇报线走 `Relationship` 的 `manages` 类型。
> 保留修订记录是因为**为什么错**比结论更值得记住。

**原方案的两个破口：**

1. **Relationship 无法把汇报线归属到某个身份。** 张三是项目部兼职员工（上级 Alice）、
   同时是食物银行的志愿者协调员（上级 Bob），Relationship 里就是两行长得一样的
   `managed by` —— 问"张三作为员工向谁汇报"答不出来。
   注意这不是存不下，是**说不清哪行属于哪个身份**（歧义，而非表达力不足）。
2. **1:1 的 `EmployeeProfile` 压根装不下一人多岗。** 张三如果同时是项目部兼职 +
   财务部兼职，两个职务、两个部门、两个上级 —— 一对一表只有一个 title 一个 department。

**根因**：原方案把"岗位"信息塞进了"角色"表，混淆了 D10 里的两个层次。

**修订后的方案：**

```python
Assignment(
    contact      → Contact,              # 谁
    kind         = employee | volunteer | board,
    title,                               # 职务名
    department   → Department,           # 字典表（见 D5）
    reports_to   → Assignment (可空),     # 上级岗位，自引用
    employment_type (可空),               # 全职/兼职/合同/实习，仅有薪岗位
    start_date, end_date, is_active,
)
```

张三的例子 = 三行 Assignment，每行自带自己的上级。歧义消失，一人多岗天然支持，
历史靠每行的起止日期 + 审计日志保留。

**`reports_to` 指向 `Assignment` 而不是 `Contact`**（自引用外键）。
语义更准：你向的是"项目总监这个岗位"，而不是"Alice 这个人"。
好处是能回答"张三在担任项目协调员期间向谁汇报"这类带身份限定的问题。
**代价**：管理者换人时，要把下属的 `reports_to` 指到新任者的 Assignment 行
（指向 Contact 的方案同样要改，只是改的是另一个字段）—— 接受。

**汇报线只存在 `Assignment` 上**，`manages` / `managed by` 关系类型**不再用于组织架构**
（见 D6 的适用范围补充）。这一点很重要：原方案批评"加 manager FK"的理由正是
"同一件事记两个地方"，所以修订后的方案不能自己犯同样的错 —— 不是两处都能记，是只有一处能记。

**`EmployeeProfile` 因此不建。** title / 部门 / 雇佣类型 / 起止全部搬进 `Assignment` 之后，
它只剩员工编号之类的零碎，MVP 阶段不值得一张表。
**`VolunteerProfile` 保留**，因为它装的是真正的人级事实（技能、背景审查），见 D10 的三层标准。

**已知限制：一个岗位只能有一个上级。** 实线 + 虚线的矩阵式双上级存不了。
真需要的话要把 `reports_to` 改成多对多 —— 小基金会极少有矩阵汇报，现在不做（见推迟清单）。

**约束**：`reports_to` 不能指向自己那一行（`CheckConstraint`）。允许指向同一个人的
另一个 Assignment（少见但合法，不额外禁止）。

**薪酬数据暂不入库**。工资是本系统里敏感度最高的数据，一旦入库就要处理字段级权限、
加密、审计、以及"谁能看到谁的工资"这类问题，而 MVP 阶段没有任何功能需要它。
**何时重新考虑**：真的要在系统里算人力成本或做预算报表时 —— 那时要连同权限方案一起设计，
不能顺手加个字段了事。

**理事会成员用 `kind=board` 的 Assignment**（已确认）。理事确实有职务（理事长 / 财务负责人 /
秘书 / 普通理事）和任期，正好是 `title` + `start_date` / `end_date`，和岗位是同一个形状。
好处是"这个人在基金会的所有身份"永远只查一张表 —— 理事同时捐款、或先做志愿者后进理事会，
都是同一个 Contact 下多加一行。

由此产生的两个字段影响：

- **`department` 必须可空** —— 理事不属于任何部门。（`employment_type` 本来就可空，理事无薪。）
- **汇报方向可以反过来**：执行总监的 `reports_to` 指向**理事长的 Assignment 行**。
  这恰好印证了 `reports_to` 指向 `Assignment` 而不是 `Contact` 的选择 ——
  执行总监向的是"理事长这个职位"，理事长换人时组织结构本身没变。

### D12 · 登录账号（User）挂在 Contact 上，与任职状态解耦
`accounts.User` 有一个可空的 OneToOne 指向 `Contact`；**不指向 `Assignment`**。
**为什么**：三个概念必须分清 ——
- `Contact` = 这个人是谁
- `Assignment` = 这个人在基金会担任什么岗位
- `User` = 这个人能不能登录、能看什么

它们不是一一对应的：不是每个员工都需要登录账号（比如只领工资不用系统的兼职），
不是每个登录账号都是员工（以后志愿者自助登录、外部审计员只读账号），
还有一个纯技术账号（superuser）根本不对应任何真人 —— 所以 `contact` 必须可空。
一个人有多个 Assignment 但只该有一个登录账号，这也是 User 必须挂在 `Contact` 而非
`Assignment` 上的原因。
**影响**：这条决定了 Phase A 里自定义 User model 怎么建，所以要在动手前定下来。
权限一律用 Django Group（管理员 / 项目协调员 / 只读），不看"是不是员工"来判断权限。

### D13 · 单个 email / 电话 / 地址，暂不拆表
CiviCRM 把它们拆成独立的一对多表。我们现在用单字段。
**为什么**：单字段够用，且以后要拆随时能加表迁移过去，不是不可逆决策。
**何时重新考虑**：真的出现"志愿者有工作和私人两个邮箱要分别使用"这类需求时。

---

## 四、当前进度

### ✅ 已完成 —— 数据核心设计，这是目前最有价值的部分

> 这部分已经按 D4–D9 验证过，**除非某条 D 记录被修订，否则不要动**。
> 尤其是：一张 Contact 表管人和机构、字典表而非枚举、自建 Language、业务规则在 model 层。
> 另外**有测试** —— 这是后面敢重构的唯一底气，新增模型时一并补测试是硬要求。

`contact` app 里已建好并有测试覆盖：

- **`Contact`** —— 人和组织统一表（见 D4）。含姓名（法定名/偏好名/机构名）、
  联系方式、人口统计（性别、生日、偏好语言、偏好联系方式）、结构化地址、
  `is_active` 状态、备注。
- **`RelationshipType`** —— 关系类型字典表（见 D5），带正反双向标签（如 `parent of` / `child of`）。
  注意：`manages` / `managed by` 已不用于组织架构，汇报线走 `Assignment`（见 D6 适用范围、D11）。
- **`Relationship`** —— 连接两个 Contact，带类型和起止日期（见 D6）。
- **`Language`** —— 自建 ISO 639-3 表（见 D8），数据迁移已灌入约 7900 行，
  English / Mandarin / Cantonese 已 pin 到最前。
- **`TimeStampedModel`** —— 抽象基类，给所有表加 created_at / updated_at。
- **Admin** —— 完整配置：搜索、筛选、autocomplete、Relationship inline，
  以及两段 JS（按 contact_type 隐藏无关名字字段、按国家切换州的下拉/文本框）。
- **测试** —— 覆盖名字与类型的匹配规则、地址州的美国/非美国两种情况、Language 的排序与筛选。

### 🚧 进行中 —— Phase A 地基加固

**具体怎么做见 `01-roadmap.md`**（那份文档现在只讲 Phase A 的实施步骤）。
这些事的共同点是"**现在改成本≈0，以后改很痛**"，所以排在所有新功能之前。
验收标准是**功能上什么都没变、测试全绿**。

| 事项 | 为什么不能拖 | 状态 |
|------|------------|------|
| 自定义 User model（`AUTH_USER_MODEL`，按 D12 带可空 Contact 外键） | Django 项目一旦有真实用户数据，换 `AUTH_USER_MODEL` 极其痛苦（要手写数据迁移、重建外键）。现在库里只有测试数据，成本≈0 | ⬜ 未开始 · **最紧急** |
| 从 SQLite 切到 Postgres | 两者在约束、JSONField、大小写敏感、并发上行为不同。等写了几个月业务逻辑才切，等于所有东西重测一遍。本机 `postgresql@18` 已装 | ⬜ 未开始 |
| `SECRET_KEY` / `DEBUG` / `ALLOWED_HOSTS` / `STATIC_ROOT` 进环境变量 | 现 key 已进 git 历史，**已泄露的 key 不能再用**，上线前必须换新的；配置越早拆干净，上线时越不手忙脚乱 | ⬜ 未开始 |
| 建 `core` app，`TimeStampedModel` 从 `contact` 迁出 | 下一个 app（volunteer / event）要用它就得 `from contact.models import ...`，依赖方向反了，以后想单独理解或替换 `contact` 会被缠住 | ⬜ 未开始 |
| 移除装了没用的 `countries_plus` / `languages_plus` | 它们各自在库里建了几千行的表。既然已按 D8 自建 `Language`，删掉减少依赖和迁移噪音 | ⬜ 未开始 |
| `Relationship` 加数据库约束（禁自我关系、禁重复） | 现在可以存"Alice 是 Alice 的母亲"，也可以把同一段关系重复存 10 遍。约束加在数据库层，脏数据永远进不来 | ⬜ 未开始 |
| 审计日志（`django-simple-history`） | "谁在什么时候改了这条记录"在基金会场景下是刚需，且是我们自己定的"值得抄"的一条。先挂 `Contact`，`Assignment` / `Contribution` 之后必挂 | ⬜ 未开始 |
| 写 `README.md`（删空的 `READ.md`） | 半年后的你（或下一个接手的人）需要知道怎么把这个项目跑起来 | ⬜ 未开始 |

### ⬜ 未开始 —— 后续阶段

> 这里记"要建什么、为什么"，不记具体步骤。每个 Phase 开工前，把当时的实施细节写进
> `01-roadmap.md`（那份文档一次只服务一个 Phase）。

#### Phase B · 人与活动 MVP —— 做完就让基金会开始真用

`Assignment` 是这一阶段的核心表，员工 / 志愿者 / 理事的岗位共用它（`kind` 区分），
**不建 `EmployeeProfile`**（见 D11）。

| 模型 | 字段要点 |
|------|---------|
| `Department` | 字典表（见 D5），admin 里能加 |
| `Assignment` | `contact` / `kind`(employee·volunteer·board) / `title` / `department`(**可空**) / `reports_to`(自引用 FK，可空) / `employment_type`(**可空**) / `start_date` / `end_date` / `is_active`。**挂 simple-history** —— 岗位历史靠起止日期 + 审计日志保留 |
| `Skill` | 字典表（见 D5） |
| `VolunteerProfile` | OneToOne → `Contact`。`skills`(M2M → Skill) / `background_check_status` + 审查日期 / `availability_notes`。**不含** title / 上级 / 任职起始日（那些是岗位，归 `Assignment`）；**不含**紧急联系人（走 `Relationship`，见 D6） |
| `Event` | 名称 / 类型（字典表） / 起止时间 / 地点 / 负责人(FK → Contact) / 状态 / 容量 |
| `Participation` | FK Event + FK Contact + 角色 + 状态（报名·出席·缺席） + **hours**。这张中间表是整个系统的价值所在 —— 工时统计、志愿者活跃度、活动回顾全靠它 |

实现要点：

- `department` 和 `employment_type` 必须可空（理事两个都没有）；`kind` 三种取值一次做齐。
- `reports_to` 指向 `Assignment` 而非 `Contact`（见 D11）。admin 里必须用 autocomplete，
  且 `Assignment.__str__` 必须可读（如「张三 — 项目协调员」），否则下拉框没法用。
- Admin：`Event` 页面用 inline 直接登记参与者；`Contact` 页面用 inline 显示 TA 的
  Assignment 和参加过的活动。
- **必须写的约束和测试**：
  1. `reports_to` 不能指向自己那一行（`CheckConstraint`）；
  2. **一人多岗** —— 同一个 Contact 有两个 Assignment、各有不同上级，能存下且能分别查出。
     这正是 D11 修订要解决的场景，值得作为明确的测试用例钉住；
  3. **跨 kind 的汇报线** —— 执行总监（employee）`reports_to` 理事长（board）。
- **验收：基金会的人能在浏览器里录一个真志愿者、开一个真活动、登记出席和工时。**
  到这一步就交给他们用，不要等"全做完了"再给人看 —— 真实使用暴露的需求比现在猜的准得多。

#### Phase C · 资金追踪

- `Contribution`：FK Contact（捐款人） / `amount` + `currency` / `received_date` /
  `financial_type`（字典表：一般捐赠·指定用途·实物） / `payment_method`（字典表） /
  `status`（pending·completed·refunded） / 收据编号 / 关联 `Event`（可空 —— 某次活动筹到的钱） / 备注
- 金额一律 `DecimalField`，**永远不用 `FloatField`**。
- **必须挂 simple-history** —— 钱的记录必须能追溯是谁改的。
- 第一批自己写的页面（不再是 admin）：本月/本年捐款总额、某活动总工时、志愿者活跃排行。
  这时候上 HTMX 刚好（见 D2）。

#### Phase D · 上线与真实运营

- 部署：Render 或 Fly.io + 托管 Postgres（保持 D3：一个 `pg_dump` 就能带走）
- **备份**：定时 `pg_dump` 到对象存储，并且**真的演练一次恢复**。没验证过的备份等于没有备份。
- 权限：用 Django Group 划分角色（管理员 / 项目协调员 / 只读），按 D12 不看"是不是员工"来判断，
  也别自造权限引擎
- CSV 导出、简单报表
- 安全：`SECURE_SSL_REDIRECT`、HSTS、`SESSION_COOKIE_SECURE`，
  跑 `manage.py check --deploy` 到没有警告

---

## 五、明确推迟的事

记下来是为了不反复纠结。

| 事情 | 为什么现在不做 | 什么时候再看 |
|------|--------------|------------|
| 一个 Contact 多个 email / 电话 / 地址 | 见 D13，单字段够用且可逆 | 出现"两个邮箱分别用"的真实需求时 |
| 薪酬 / 工资数据 | 见 D11，敏感度最高且 MVP 无功能需要 | 要算人力成本或做预算报表时，连同权限方案一起设计 |
| 一个岗位多个上级（矩阵式实线/虚线汇报） | 见 D11，小基金会极少有；`reports_to` 现在是单个外键 | 真的出现双线汇报时，把 `reports_to` 改成多对多 |
| Membership / 会员制 | 基金会未必有会员概念 | 需求出现时 |
| REST API、前后端分离 | Admin + HTMX 能撑很久 | 要做志愿者自助登录的手机端时 |
| 软删除 | 现有 `is_active` 已覆盖"停用"语义，不同时上两套 | 出现"误删要恢复"的真实事故时 |
| 邮件群发 / 对外活动报名页 | 属于对外系统，和内部管理是两回事 | Phase D 之后 |

---

## 六、下一步

按 `01-roadmap.md` 走完 Phase A。核心是三件互相咬合的事 ——
**配置进环境变量 → 切 Postgres → 自定义 User**，一起做比分开做省事：
所有改结构的动作都赶在切库之前完成，然后在一个全新的空 Postgres 库上一次性建表，
就不用处理任何数据迁移。

验收标准是**功能上什么都没变、测试全绿**。
