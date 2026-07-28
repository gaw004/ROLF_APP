# ROLF_APP — 目标、重大决策与进度

> 这份文档是项目的**唯一权威来源**，记录三件事：我们要做什么、做过哪些重大决策（以及为什么）、
> 现在走到哪了。做了新的重大决策或完成一个阶段就回来更新这里。
>
> 相关文档：`01-roadmap.md`（**当前 Phase 的实施步骤**，眼下是 Phase A；
> 它服从本文档的决策，冲突时以本文档为准）
>
> 最后更新：2026-07-28

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
真实使用暴露的需求比现在猜的准得多，所以每个阶段结束都必须是**可演示**的状态
（能完整跑通一条真实业务流程，哪怕跑在本机、用演示数据），永远不憋大版本。
**能否交给基金会真用是另一条线，前置条件在 Phase D。**

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

**代价**：有些字段只对一种类型有意义 —— 用
`contact_name_matches_type` 约束强制 + `Contact.clean()` 给字段级提示（见 D9 / D14）
+ `save()` 清空不适用的字段 + admin 里 JS 隐藏无关字段来处理。

> CiviCRM 本身有**三种**：Individual / Household / Organization。我们只取两种 ——
> "家庭"这个概念对本基金会是否有用还不知道，等真实使用暴露需求再说。
> 真要加的话，除了 `TextChoices` 还要改 D9 那条 `CheckConstraint`（两处，见 D14 的注释纪律）。

### D5 · 会变的分类做成字典表，不做 Python 枚举
`RelationshipType` 是数据库表。基金会以后想加"推荐人"、"校友"这类关系，
在 admin 里加一行就行 —— 不用改代码、不用写迁移、不用重新部署。
**这是"需求变了还能用"最直接的体现**，后续 Ministry、Skill、活动类型、捐款类型、
付款方式一律照此办理。

> 注意"理事会成员"**不是**关系类型 —— 理事走 `kind=board` 的 `Assignment`（见 D11）。
> 判断方法：**这个人在基金会担任的职务 → `Assignment`；这个人和另一个人/组织之间的联系 → `Relationship`。**

#### 判定规则：什么时候用字典表，什么时候用 `TextChoices`（2026-07-28 补）

"会变的做字典表"这句话不够判定 —— 几乎所有分类看上去都会变。真正的分界是：

> **代码要按它分支的（状态机、权限判断、统计口径）→ `TextChoices`；
> 纯粹是给人看的标签分类 → 字典表 + `code`。**

理由：字典表的全部价值是"不改代码就能加一行"。而只要代码里存在
`if status == X` 这样的分支，加一行就**必然**要改代码 —— 那字典表只是把枚举藏进了数据库，
额外多一次查询、多一个静默失效的风险，什么也没换来。
`contact_type` 走 `TextChoices` 正是这个道理（D9 的"代价"那一段已经隐含说了）。

当前分配：

| `TextChoices`（代码按它分支） | 字典表 + `code`（纯标签） |
|---|---|
| `Contact.contact_type`、`Assignment.kind`、`Event.status`、`Participation.status`、`background_check_status` | `RelationshipType`、`Ministry`、`EmploymentType`、`EventType`、`ParticipationRole`、`Skill`、Phase C 的 `financial_type` / `payment_method` |

#### 通则：每张字典表都带一个唯一且不可改的 `code`

**代码只认 `code`，永远不认显示名。**

字典表的全部价值是"显示名可以在 admin 里随时改" —— 那么代码里凡是引用显示名的地方
都会在某人改名之后**静默失效**（`filter(name="parent of")` 不报错，只是查不到东西了）。
`code` 是给代码用的稳定锚点，显示名是给人看的、可变的。

三个要求缺一不可（2026-07-28 补齐后两条）：

1. **`unique=True`** —— 不唯一的 `code` 根本不是锚点。`get(code="food_pantry")` 会抛
   `MultipleObjectsReturned`，而且是在有人手滑建了第二行之后才炸。
2. **小写归一化** —— `food_pantry` 和 `Food_Pantry` 是两个不同的值，唯一约束拦不住。
   在 `save()` 里 `self.code = self.code.strip().lower()`。
3. **真的不可改** —— `editable=False` 只挡 ModelForm，脚本照改。落地方式是
   admin 的 `get_readonly_fields` 在 change（非 add）页把 `code` 设为只读，
   加上 `clean()` 里比对数据库中的旧值。

适用范围：**一张不落**。新建字典表时就带上，成本为零；
`RelationshipType` 是已有表，Phase B 补加（三步迁移，见 Phase B）。

### D6 · 通用 Relationship 表：承载"薄"关系
`Relationship(contact_a, contact_b, relationship_type, start_date, end_date)`
**适用于只需要记录「A 和 B 有某种联系 + 起止日期」、没有专有字段的关系。**

**剩余适用范围（2026-07-27 两次收窄后）：**

1. 外部组织归属 —— "张三是 XX 公司员工"（企业配捐、企业志愿者团队）、"李四是 XX 中学学生"
2. 家庭 / 配偶 —— 家庭作为一个捐赠单元
3. 推荐人 —— 谁介绍谁来的
4. 亲属关系中不涉及法律责任的部分
5. 以后冒出来的、暂时说不清要什么字段的新关系（先记下来，够格了再升级成专用表）

**被拿走的两块（记录在此以免重复讨论）：**

- ❌ **基金会内部岗位与汇报线** → 走 `Assignment`（见 D11）。
  原因：Relationship 说不清"这条汇报线属于这个人的哪个身份"。
  `manages` / `managed by` 关系类型**不再用于组织架构**。
- ❌ **法定监护等带专有字段的关系** → 走专用表（见 D15）。
  原因：通用表放不下"同意书签署日期"这类字段。
  **该专用表（`Guardianship`）已于 2026-07-28 推迟到 Phase B 之后**，见推迟清单。
- ❌ **紧急联系人** → 2026-07-28 改成 `Contact` 上的三个字段（见 D15 的修订）。

**补强**：`RelationshipType` 要加两个字段：

- **`code`**（不可改的 slug，如 `guardian_of`）—— 代码里一律引用 `code` 而**不是**显示名。
  否则 `filter(relationship_type__name_a_to_b="guardian of")` 这种字符串匹配，
  会在有人于 admin 里改了显示名之后**静默失效**。这是通用表最大的脆弱点，一个字段修掉大半。
  唯一性、小写归一化、不可改的落地方式见 D5 的三条要求。
- **`is_symmetric`**（布尔）—— 显式标记"配偶""兄弟姐妹"这类正反同义的类型，
  不靠"`name_b_to_a` 为空"去推断。理由见 D15。

### D7 · 标准化字段用成熟库，做完整的下拉
电话用 `django-phonenumber-field`（存 E.164 国际格式，含区号）、
国家用 `django-countries`（完整 ISO 3166）、美国州用 `django-localflavor`（完整 50 州）。
**为什么**：这些数据的正确性和完整性是解决过的问题，自己列一遍必然出错、必然缺项。

### D8 · Language 自建表（ISO 639-3），不用 `django-languages-plus`
`languages-plus` 的表键在 2 字母 ISO 639-1 码上，**排除了 Mandarin (cmn)、Cantonese (yue)、
Hmong 等**，而这些正是基金会最常服务的语言。所以自建 `Language` 表，
由数据迁移从 `pycountry` 灌入约 7900 行 ISO 639-3，并加 `pin_rank` 字段让常用语言排在下拉最前面。
**代价**：多一张自己维护的表 —— 但换来的是能正确记录服务对象的语言，这是刚需。

### D9 · 业务规则落到数据库约束
> **这条在 2026-07-27 修订过。** 原文是"`Contact.clean()` 和 `Contact.save()` 承载规则，
> 所以从 admin 存、从脚本存、从以后的 API 存行为都一致"。**这个说法是错的。**

`clean()` 只有 ModelForm 和显式 `full_clean()` 会调用，**`save()` 不调用它** ——
`Contact.objects.create(contact_type="individual")` 不填姓氏一直是能存进去的。
`contact/tests.py` 里那两个校验测试必须手写 `.full_clean()` 才触发得到，这就是证据。
保留修订记录，是因为"**以为规则生效了、其实没生效**"比规则本身更值得记住 ——
这种错误不会报错，只会安静地放脏数据进来。

**修订后的原则：能用数据库约束表达的规则，就落到数据库约束。**
数据库是所有写入路径唯一绕不过去的一层 —— admin、脚本、`bulk_create`、
`queryset.update()`、以后的 API、直接连 psql 全都绕不过。而 Python 层的校验
管不住后面这几种，其中 `bulk_create` 恰恰是批量导入数据时最常用的。

具体到"名字必须匹配 contact_type"：改成 `CheckConstraint`
（个人必须有 `legal_last_name`，机构必须有 `organization_name`），
提示层怎么配合见 D14。

**`Contact.save()` 里清空不适用名字字段的行为不变** —— 那是数据整理，不是校验，两回事。

**代价**：约束把 `contact_type` 的两种取值写进了数据库。以后要加第三种
（CiviCRM 其实有 Individual / Household / Organization 三种，见 D4）就多一处要改。
可接受 —— `contact_type` 本来就是 `TextChoices` 枚举、本来就是代码而非字典表，
加一种本来就要改代码，只是从改一处变成改两处。

### D10 · 人只有一份档案；「角色」和「岗位」是两个不同层次
❌ 不建独立的 `Volunteer` / `Employee` 模型（里面又有姓名电话地址）。
✅ `Contact` 是所有人的唯一档案：

```
Contact ←1:N→ Assignment              (任职：职务、ministry、上级、起止 —— 见 D11)
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
| **岗位** | 这个人担任什么职务？**同一个人可以有多个** | `Assignment` 1:N | 职务名、ministry、上级、雇佣类型、起止日期 |

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
    ministry     → Ministry,             # 服务单元：食物银行/报税志愿…（可空）
    reports_to   → Assignment (可空),     # 上级岗位，自引用
    employment_type (可空),               # 全职/兼职/合同/实习，仅有薪岗位
    is_leader,                           # 布尔，给代码查；title 是给人看的
    start_date, end_date,                # 注意没有 is_active —— 在职状态由 end_date 派生
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

- **`ministry` 必须可空** —— 理事不属于任何 ministry。（`employment_type` 本来就可空，理事无薪。）
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

### D14 · 约束是强制层，`clean()` 是提示层 —— 两层都写，靠注释锁住
D9 决定了规则落到数据库约束。但约束报错默认挂在表单顶部、措辞是给程序员看的，
admin 里的人看不懂。所以每条业务约束**写两层**：

| 层 | 职责 | 权威性 |
|----|------|-------|
| `CheckConstraint` / `UniqueConstraint` | **真正的强制**，所有写入路径都绕不过 | 唯一真相 |
| `Model.clean()` | 把错误挂到具体字段上、给人话提示 | 纯 UI，不承担兜底 |

`clean()` **不是**兜底 —— Django 4.1 起 `full_clean()` 会自动校验约束，
就算不写 `clean()`，admin 也不会抛 `IntegrityError`，只是提示难看。写它纯粹为了体验。

**这条明知故犯地违反了"同一件事只记一个地方"**（D11 那句"不是两处都能记，
是只有一处能记"）。所以必须说清楚为什么这次可以：

- D11 防的是**数据**记两处 —— 两处会互相矛盾、静默腐烂、且不知道该信哪个；
- 这里重复的是**校验逻辑** —— 真相唯一（数据库），两处写岔了的后果只是提示变难看，
  数据永远是对的。

同一种气味的弱化版，不是同一个错误。但代价必须靠纪律兜住：

> **硬性要求：约束和 `clean()` 两处都写注释指认对方。** 用约束的 `name=`（比如
> `contact_name_matches_type`）互相引用，**不要写行号** —— 行号会漂移，约束名不会。
> 改一处必须改另一处，code review 时这是一个明确的检查项。

**适用范围**：`Contact` 的姓名规则、`Relationship` 的三条约束（见 `01-roadmap.md` A7），
以及 Phase B 之后所有新增的业务约束。字典表、`is_active` 这类不涉及业务规则的字段不适用。

### D15 · 关系用什么载体承载：三条路 + 选择规则
> 2026-07-27 讨论的产物。起因是「Assignment 拿走汇报线之后，Relationship 还有存在价值吗？
> 紧急联系人为什么不直接在 Contact 上加两个字段？」
>
> **2026-07-28 修订：紧急联系人改用载体一（Contact 字段），`Guardianship` 推迟到 Phase B 之后。**
> 修订的理由记在下面各节里 —— 和 D9 / D11 一样，**为什么改口比结论更值得记住**。

**结论：混合策略。** 三种载体各有适用面，按下面的规则分配，不搞一刀切。

#### 载体一：`Contact` 上加字段 —— ✅ 只用于紧急联系人（2026-07-28 从"已否决"改口）

原来的否决理由（仍然成立，只是不适用于紧急联系人这一例）：

- 同一个家长有三个孩子做志愿者 → 电话存三遍，改一次要改三处，且系统不知道这是同一个人
- 无法反查"这个监护人名下有哪些未成年志愿者"
- 每加一种关系加两个字段，Contact 迅速膨胀

**改口的理由：幽灵记录的代价被判定高于规范化的收益 —— 对紧急联系人这一例而言。**
紧急联系人可能是邻居、室友、同事，跟基金会没有任何其他关系。
为他们各建一条 Contact 记录，会让联系人列表里混进一批**只有姓名电话、
永远不会被真正使用的幽灵记录**，污染搜索、统计和导出。而上面三条否决理由里，
第一条（重复存储）在这个场景下发生频率很低，第二条（反查）压根没有需求 ——
没有人会问"这个邻居是几个志愿者的紧急联系人"。

**落地形态（Phase B）**：`Contact` 上三个字段 ——
`emergency_contact_name` / `emergency_contact_phone` / `emergency_contact_relationship`
（关系是自由文本："母亲""邻居"）。

**边界，写死不许越过：**

- **只有一组，不做第二组。** 需要第二组的那天，就是该升级成关系表的信号，
  而不是加 `emergency_contact_2_*`（用字段个数模拟"多个"是典型反模式，见载体二）。
- **字段保持"哑"** —— 纯文本，不做任何指向 Contact 的关联、不做去重。
  这样将来真要规范化时，手上是三个干净的字段，而不是三个字段外加半套自建的关联逻辑。
- **一条约束**：填了名字就必须有电话（`emergency_contact_name = '' OR
  emergency_contact_phone != ''`）—— 这个字段存在的**唯一**理由就是出事时能拨通。

#### 载体二：自引用外键（FK to self）—— ✅ 用在 `Assignment.reports_to`，❌ 不用于人际关系

考虑过"给 Contact 加一个指向 Contact 自己的外键，在同一张表里循环，就不用建表了"。
这个模式我们**确实在用** —— `Assignment.reports_to` 就是它。但它有硬性适用条件：

| 条件 | `Assignment.reports_to` | 监护 |
|---|---|---|
| 基数：最多一个？ | ✅ 一个岗位只有一个上级（D11 明确接受的限制） | ❌ 一个孩子可以有父母**两个**监护人 |
| 关系自己有属性吗？ | ✅ 没有 —— 岗位结束汇报关系自然结束，日期由 Assignment 行携带 | ❌ 有 —— 监护有自己的起止，与任何岗位无关 |
| 只有一种类型？ | ✅ 一种 | ❌ 多种（法定 / 委托 / …） |

**规则：三条全满足才能用自引用 FK；破任何一条就必须用表。**

关键在基数：**自引用 FK 只能表达一对多，且"多"必须在反向那一头。**
监护本质是多对多（多个监护人 × 多个孩子），FK 做不到 —— 这是关系模型的硬约束，不是偏好。
用 `guardian_1` / `guardian_2` 拿字段个数去模拟"多个"是典型反模式。

补充一点：Django 里写 `ManyToManyField("self", through=...)` 底层照样建中间表。
**表省不掉**，只能选它是"你自己定义、能加字段"还是"Django 帮你建、你加不了字段"。
而且自引用 FK 的目标仍必须是一条 Contact 记录，所以**"幽灵记录"这个成本一分没省** ——
这也正是紧急联系人最终走载体一、而不是走自引用 FK 的原因。

#### 载体三：通用表 vs 专用表 —— ✅ 混合（方案 C）

| | 通用 `Relationship` | 专用表（如 `Guardianship`） |
|---|---|---|
| 加新类型 | admin 加一行，零成本 | 建表 + 迁移 + admin + 测试 |
| 专有字段 | ❌ **放不下** | ✅ 各放各的 |
| 代码引用 | 靠 `code` 匹配（见 D6 补强） | `contact.guardianships.all()`，自解释 |
| 专属校验规则 | ❌ 没处写 | ✅ 写在自己的 `clean()` 里 |
| "此人所有关系" | ✅ 一次查询 | ❌ 查 N 张表再合并 |
| admin 录入体验 | 类型下拉越来越长 | 表单只显示相关字段，好得多 |

**升级为专用表的触发条件（满足任一条）：**

1. 这种关系有自己的字段（不只是 A、B、起止日期）
2. 这种关系有自己的校验规则或约束
3. 代码里需要频繁按这个类型精确查询

**三条都不满足 → 留在 `Relationship`。**

#### 当前分配

| 关系 | 载体 | 理由 |
|---|---|---|
| 汇报线 | `Assignment.reports_to` 自引用 FK | 自引用三条件全满足（D11） |
| **紧急联系人** | **`Contact` 上的三个字段** | 2026-07-28 改口：不值得为"出事拨的号码"制造幽灵记录，见载体一 |
| 法定监护人 | 专用表 `Guardianship`，**但推迟到 Phase B 之后** | 满足触发条件 1（同意书签署日期、监护类型、能否代签），但基金会有没有这个流程尚未答复，见下面「待确认」 |
| 外部组织归属 / 配偶 / 推荐人 | 通用 `Relationship` | 三条都不满足，只是 A—B + 日期 |

#### 监护人 ≠ 紧急联系人（重要区分）

这两个经常被当成一回事，但它们是**不同的概念**，所以放在不同载体里不算"同一件事记两处"：

- **法定监护人** —— 有法律意义：签同意书、接送、必须被通知。**需要在系统里有身份。**
- **紧急联系人** —— 出事时拨的电话，可能是邻居、同事、室友。**不一定需要身份。**

**这个区分正是两者最终分到不同载体的依据**：需要身份的走表，不需要身份的走字段。

#### 已知代价（不粉饰）

**紧急联系人走字段之后仍要付的代价：**

1. **同一个人重复存储** —— 一个家长的三个孩子都做志愿者，电话在三条 Contact 上各存一份，
   改号码要改三处，而且系统不知道这是同一个人。发生频率低，但会发生。
2. **未来的规范化方向是痛的那一头** —— 见下一节，这是明知代价的选择。

**将来 `Guardianship` 建起来之后要付的代价：**

3. **幽灵记录** —— 只有姓名电话的监护人 Contact 混在联系人列表里，影响搜索、统计、导出。
   这次是**值得付**的：监护人需要在系统里有身份（能被通知、能签同意书）。
4. **录入多两步** —— 要先建 Contact 再建关系；admin inline 能缓解但消除不了。
5. **两套机制** —— 需要靠上面的触发条件来判断新关系放哪，否则会乱放。

#### 为什么这次接受了"先用字段"（原则的例外，要说清楚）

原文写的是"**先规范化，以后要退化随时能退；反过来不行**"，理由是迁移方向不对称：

- 关系表 → 字段：**容易**。把关联记录的姓名电话拍平写进字段即可。
- 字段 → 关系表：**痛**。手上只有一堆姓名字符串，要去重、要判断两个"王秀英"是不是同一个人、
  要建 Contact、要连关系。

**这条原则本身没变，紧急联系人是明知故犯的例外。** 之所以可以：

- 痛的那一头**只在真的要升级时才付**，而升级的前提是"需要反查、需要第二组联系人"——
  这两件事目前都没有需求，且很可能永远不会有；
- 相比之下，幽灵记录的污染是**从录入第一条起就天天付**的；
- 上面写死的三条边界（只有一组、字段保持哑、不做关联）是为了让这笔债**不再增长** ——
  真要还的时候，手上是三个干净的文本字段，正是最容易迁移的形态。

**监护人不适用这个例外**，因为它满足触发条件 1（有专有字段），走字段一开始就存不下。

#### 待确认 ⏳ 等待向基金会核实中（2026-07-27 起）

**基金会接触未成年志愿者时，有没有同意书 / 家长授权这一环？**

- **有** → 建 `Guardianship` 专用表（形状见推迟清单）
- **没有**，只需要记"这孩子有事找谁" → 已经由紧急联系人字段覆盖，不建表

**状态（2026-07-28 更新）：这张表已整体移出 Phase B。** 原来的安排是"等答复，有就在 Phase B 建"，
现在改成无条件推迟 —— 因为紧急联系人改走 Contact 字段之后，
**"活动前通知未成年参与者的家长"这条真实需求在 Phase B 已经闭环**
（`is_minor` 筛人 + 紧急联系电话拨号），`Guardianship` 不再是任何功能的前置条件。
答复回来只决定它**什么时候**做，不再阻塞任何东西。

### D16 · 时间与日期的唯一口径（2026-07-28）
> 起因：`Assignment` 的「在职」判定要用到"今天"，而这个概念在 `USE_TZ=True` 的项目里
> 有三种写法，其中两种是错的，且**错了不报错，只是边界日的数字悄悄不对**。

**原则：数据库永远存 UTC，业务上的"今天"永远是基金会所在时区的今天。** 两件事不能混。

- ❌ `datetime.date.today()` —— 依赖服务器本地时区。部署到 Render 上是 UTC，本机是 PT，两边行为不一样。
- ❌ `timezone.now().date()` —— 那是 **UTC 日期**。太平洋时间 7 月 27 日下午 5 点，
  UTC 已经是 7 月 28 日，于是"今天在职"的判定**提前跨天**。
- ✅ `timezone.localdate()` —— 按 `settings.TIME_ZONE`（`America/Los_Angeles`）折算。

**三层落地，缺一层就守不住：**

1. **唯一入口。** `core/timeutils.py` 里一个 `local_today()`，全项目只有这里碰"现在"。
   Phase C 的报表还会往里加 `month_bounds()` 之类，同一个道理。
2. **把时钟注入 API，不要在函数体里隐式取。** 所有跟日期有关的 queryset 方法都写成
   `def active(self, on=None): on = on or local_today()`。
   默认值必须在**调用时**求值 —— 写成 `def active(self, on=local_today())` 是经典的
   进程启动时冻结 bug，长驻的 gunicorn worker 上会越跑越错。
   参数化顺带让"查某一天的名单"和测试边界都变成免费的。
3. **用 linter 钉死，不靠自觉。** 加 `ruff`，开 `DTZ` 规则组
   （`flake8-datetimez`，就是为这个问题存在的：`DTZ011` 禁 `date.today()`、
   `DTZ005` 禁裸 `datetime.now()`）。`DTZ` 抓不到 `timezone.now().date()`
   （那是 tz-aware 的，linter 认为合法），所以再补一条 grep 守卫测试放 `core/tests.py` ——
   **和 `test_no_model_changes_are_missing_a_migration` 是同一个套路：用测试当 lint。**

**代价**：多一个开发依赖（`ruff`，不进生产）、多一条守卫测试。
换来的是新人（含半年后的你）写错会当场变红，而不是等某个 11 月的傍晚发现在职人数不对。

### D17 · app 划分：一个 app 一个业务领域，敏感数据单独成 app（2026-07-28）
> Phase A 已经为这件事付过一次学费（`TimeStampedModel` 从 `contact` 搬到 `core`）。
> Phase B 一次加 6 个模型，同一个问题会再来一遍，而模型跨 app 搬家的痛苦程度
> 和换 `AUTH_USER_MODEL` 是一个量级 —— 正好符合"现在改成本≈0，以后改很痛"。

```
core        TimeStampedModel、core/timeutils.py、共享的 .active() QuerySet mixin
contact     Contact / RelationshipType / Relationship / Language
accounts    User
org         Ministry / Assignment / EmploymentType
events      EventType / Event / ParticipationRole / Participation
volunteer   VolunteerProfile
finance     （Phase C 预留）Contribution / FinancialType / PaymentMethod
payroll     （Phase D 之后预留）薪酬
```

几个取舍：

- **`org` 而不是 `hr` / `staff`** —— 它装的是 ministry + **所有** kind 的岗位
  （员工、志愿者、理事），不只是员工。
- **`Guardianship` 将来放 `contact`** —— 它是 Contact ↔ Contact 的关系，
  和 `Relationship` 同层，且未来非志愿者场景也会用到。
- **`payroll` 必须是独立 app，不能塞进 `org` 或 `finance`。**
  薪酬是本系统里敏感度最高的数据（D11 把它排除在 MVP 之外就是这个原因）。
  独立成 app，将来可以整个 app 级别地做权限隔离 ——
  Django 的权限是按 `app_label.model` 授予的，一个 Group 直接不给 `payroll.*`，
  比逐个字段配权限简单得多，也更不容易配漏。**这是现在就要占的位，不是以后再拆。**
- **依赖方向**：`INSTALLED_APPS` 按 `core` → `contact` → `org` → `events` → `volunteer`
  的顺序列，读的时候依赖方向一目了然。谁也不许反向 import。

---

## 四、当前进度

### ✅ 已完成 —— 数据核心设计，这是目前最有价值的部分

> 这部分已经按 D4–D9 验证过，**除非某条 D 记录被修订，否则不要动**。
> 尤其是：一张 Contact 表管人和机构、字典表而非枚举、自建 Language、业务规则落数据库约束。
> 另外**有测试** —— 这是后面敢重构的唯一底气，新增模型时一并补测试是硬要求。

已建好并有测试覆盖（Phase A 后的状态，共 27 个测试）：

**`contact` app：**

- **`Contact`** —— 人和组织统一表（见 D4）。含姓名（法定名/偏好名/机构名）、
  联系方式、人口统计（性别、生日、偏好语言、偏好联系方式）、结构化地址、
  `is_active` 状态、备注。带 `contact_name_matches_type` 约束 + 修改历史。
- **`RelationshipType`** —— 关系类型字典表（见 D5），带正反双向标签（如 `parent of` / `child of`）。
  注意：`manages` / `managed by` 已不用于组织架构，汇报线走 `Assignment`（见 D6 适用范围、D11）。
- **`Relationship`** —— 连接两个 Contact，带类型和起止日期（见 D6）。
  三条数据库约束：禁自我关系、禁完全相同的重复行（`NULLS NOT DISTINCT`）、`end_date >= start_date`。
  **镜像重复**（同一件事换个方向录一遍）数据库表达不了，Phase B 在类型层补防线（见那一节的三个缺口）。
  ⚠️ **`is_active` 字段将在 Phase B 删掉** —— 它和 `end_date` 是同一件事记两处，见 Phase B 的「单一真相」。
- **`Language`** —— 自建 ISO 639-3 表（见 D8），数据迁移已灌入 7923 行，
  English / Mandarin / Cantonese 已 pin 到最前。
- **Admin** —— 完整配置：搜索、筛选、autocomplete、Relationship inline、History 按钮，
  以及两段 JS（按 contact_type 隐藏无关名字字段、按国家切换州的下拉/文本框）。

**`core` app：** `TimeStampedModel` 抽象基类（给所有表加 created_at / updated_at），
以及一条全项目的迁移守卫测试（忘了 `makemigrations` 会当场变红）。

**`accounts` app：** 自定义 `User`（见 D12），带可空的 OneToOne → `Contact`。

**基础设施：** Postgres 18 + psycopg 3；配置拆成 `base`/`dev`/`prod` 包，敏感值走环境变量；
`django-simple-history` 已挂 `Contact`（`Assignment` / `Contribution` 之后必挂）。

### ✅ 已完成 —— Phase A 地基加固

**具体怎么做见 `01-roadmap.md`**（那份文档现在只讲 Phase A 的实施步骤）。
这些事的共同点是"**现在改成本≈0，以后改很痛**"，所以排在所有新功能之前。
**这个准入标准要守住** —— 顺手、便宜、"反正都在改这个文件了"都不是进 Phase A 的理由，
否则地基加固会变成一个什么都往里塞的口袋。

**验收标准（2026-07-27 修正措辞）：不新增任何功能，且原本合法的数据仍然全部能存得下，
测试全绿。**

> 原来写的是"功能上什么都没变"，这跟本阶段要做的事**自相矛盾** ——
> 加数据库约束的全部意义就是让系统不再接受某些输入，加完之后 admin 里能做的事
> 必然少了一些（原来能存"Alice 是 Alice 的母亲"，现在存不了）。
> **被约束挡掉的脏数据，是 Phase A 唯一允许出现的行为变化。**
> 除此之外任何"能做的事变了"都说明做多了。

| 事项 | 为什么不能拖 | 状态 |
|------|------------|------|
| 自定义 User model（`AUTH_USER_MODEL`，按 D12 带可空 Contact 外键） | Django 项目一旦有真实用户数据，换 `AUTH_USER_MODEL` 极其痛苦（要手写数据迁移、重建外键）。现在库里只有测试数据，成本≈0 | ✅ 完成 |
| 从 SQLite 切到 Postgres | 两者在约束、JSONField、大小写敏感、并发上行为不同。等写了几个月业务逻辑才切，等于所有东西重测一遍。本机 `postgresql@18` 已装 | ✅ 完成 |
| `SECRET_KEY` / `DEBUG` / `ALLOWED_HOSTS` / `STATIC_ROOT` 进环境变量 | 现 key 已进 git 历史，**已泄露的 key 不能再用**，上线前必须换新的；配置越早拆干净，上线时越不手忙脚乱 | ✅ 完成 |
| 建 `core` app，`TimeStampedModel` 从 `contact` 迁出 | 下一个 app（volunteer / event）要用它就得 `from contact.models import ...`，依赖方向反了，以后想单独理解或替换 `contact` 会被缠住 | ✅ 完成 |
| 移除装了没用的 `countries_plus` / `languages_plus` | 它们各自在库里建了几千行的表。既然已按 D8 自建 `Language`，删掉减少依赖和迁移噪音 | ✅ 完成 |
| `Relationship` 加数据库约束（禁自我关系、禁**完全相同**的重复行、`end_date >= start_date`） | 现在可以存"Alice 是 Alice 的母亲"，也可以把同一段关系重复存 10 遍，还能存"2020 年结束、2023 年开始"。约束加在数据库层，脏数据永远进不来；等表里有了真数据再加，就得先清洗存量数据 | ✅ 完成 |
| `Contact` 姓名规则加 `CheckConstraint`（见 D9 修订） | D9 原以为规则已经生效，其实 `save()` 不调 `clean()` —— 脚本和 `bulk_create` 一直能绕过去。这是"规则形同虚设"，不是"规则不够严" | ✅ 完成 |
| `DEFAULT_AUTO_FIELD` 设成 `BigAutoField` | 现在库要重建，改是免费的；有数据之后要 ALTER 每张表的主键**和所有指向它的外键列**。顺带消掉 `manage.py check` 现有的 3 条 W042 警告 | ✅ 完成 |
| `TIME_ZONE` 从 `UTC` 改成 `America/Los_Angeles` | ⚠️ **严格说它不满足上面的准入标准** —— `USE_TZ=True`，库里存的是 UTC，以后改也是一行的事、不痛。它是靠"顺手"进来的，不是靠规则进来的。做它是因为确实一行；记在这里是为了提醒：下一个"顺手"的东西要挡回去 | ✅ 完成 |
| 审计日志（`django-simple-history`） | "谁在什么时候改了这条记录"在基金会场景下是刚需，且是我们自己定的"值得抄"的一条。先挂 `Contact`，`Assignment` / `Contribution` 之后必挂 | ✅ 完成 |
| 写 `README.md`（删空的 `READ.md`） | 半年后的你（或下一个接手的人）需要知道怎么把这个项目跑起来 | ✅ 完成 |

### ⬜ 未开始 —— 后续阶段

> 这里记"要建什么、为什么"，不记具体步骤。每个 Phase 开工前，把当时的实施细节写进
> `01-roadmap.md`（那份文档一次只服务一个 Phase）。

#### Phase B · 人与活动 MVP —— 做完就能完整演示一遍

`Assignment` 是这一阶段的核心表，员工 / 志愿者 / 理事的岗位共用它（`kind` 区分），
**不建 `EmployeeProfile`**（见 D11）。**新模型落在哪个 app 见 D17。**

| 模型 | app | 字段要点 |
|------|-----|---------|
| `Ministry` | `org` | **不是纯字典表** —— 基金会的服务单元（食物银行、报税志愿、ESL…）。字段：`code`（唯一·不可改，见 D5）/ `name` / `description` / `is_active` / 成立日期（可空）。行政职能（财务、行政）也是这张表里的行，不另建 `Department` —— 一个组织没必要拆两套单元。**不挂 simple-history**（已确认：改动频率极低，不值得一张历史表）。<br>**这张表不能推迟**，理由见下面「Ministry 视图」 |
| `Assignment` | `org` | `contact` / `kind`(employee·volunteer·board) / `title`（自由文本，给人看；`save()` 里归一化空白，理由见下面「唯一约束」） / `is_leader`（布尔，**给代码查**） / `ministry`(**可空**) / `reports_to`(自引用 FK，可空) / `employment_type`(FK，**可空**) / `start_date` / `end_date`。**不加 `is_active`** —— 见下面「单一真相」。**挂 simple-history** |
| `EmploymentType` | `org` | 字典表：`code`（唯一·不可改）/ `name` / `is_active`。**取值基金会还没定**（全职 / 兼职 / 合同 / 实习只是我们猜的），所以做成字典表而不是 `TextChoices` —— 以后加一行就行，不改代码不写迁移。符合 D5 的判定规则：目前没有任何代码按它分支 |
| `EventType` | `events` | 字典表：`code`（唯一·不可改）/ `name` / `is_active` |
| `Event` | `events` | `name` / `event_type`(FK) / **`ministry`(FK，可空)** / `start_time` / `end_time` / `location` / `owner`(FK → `Contact`) / `status`（`TextChoices`：planned·confirmed·completed·cancelled）/ `capacity`（可空，**参考值，不强制**，见下） |
| `ParticipationRole` | `events` | 字典表：`code`（唯一·不可改）/ `name` / `is_active`。装的是**一次活动之内**的角色（签到台、搬运、翻译），**≠ `Assignment.title`**，见下面那条一句话定义 |
| `Participation` | `events` | `event`(FK) / `contact`(FK) / `role`(FK，可空) / `status`（`TextChoices`：registered·attended·absent·cancelled）/ **`hours`**（`DecimalField(max_digits=6, decimal_places=2)`，可空）。**同一个人在同一次活动里可以有多行**，靠 `role` 区分 —— 见下面「一人一活动多角色」。这张中间表是整个系统的价值所在 —— 工时统计、志愿者活跃度、活动回顾全靠它 |
| `VolunteerProfile` | `volunteer` | OneToOne → `Contact`。`background_check_status`（`TextChoices`）+ `background_check_completed_on`（可空）/ `availability_notes`。**`skills` 这个 M2M 跟着 `Skill` 一起推迟**。**不含** title / 上级 / 任职起始日（那些是岗位，归 `Assignment`）；**不含**紧急联系人（已改成 `Contact` 字段，见 D15） |
| `Contact` 加三个字段 | `contact` | `emergency_contact_name` / `emergency_contact_phone` / `emergency_contact_relationship`（自由文本）。**只有一组，不做第二组**（已确认）—— 需要第二组的那天，就是该升级成关系表的信号。见 D15 |
| `RelationshipType` 加两个字段 | `contact` | `code`（唯一·不可改，见 D5 / D6）+ `is_symmetric`（布尔，见 D15）。加到已有表要三步迁移，见下面「`code` 的三步迁移」 |
| ~~`Guardianship`~~ | — | **移出 Phase B**（2026-07-28 决定）。等基金会答复同意书流程再说 —— 家长通知这条真实需求在本阶段已经靠 `is_minor` + 紧急联系人字段闭环了。见推迟清单 |
| ~~`Skill`~~ | — | **推迟**（见推迟清单）—— 没有任何东西依赖它，ministry 视图不需要它 |

##### 本阶段内部的硬性顺序

三件事必须排在最前面，因为后面所有东西都压在它们上面：

1. **`RelationshipType.code`** —— 赶在写任何按类型查询的代码之前。晚了字符串匹配就扩散出去了，之后再收要改一片。
2. **`Contact.__str__` 消歧** —— 赶在建那几个 autocomplete 之前。理由见下面「`Contact` 重名」。
3. **关系的双向显示** —— 必须**先于**对称关系的 a/b 归一化落地。理由见下面「对称关系」。

其余的表（`Ministry` → `Assignment` → `Event` 一族 → `VolunteerProfile`）按依赖顺序建即可。
具体步骤开工前写进 `01-roadmap.md`。

##### 一人一活动多角色（2026-07-28 决定）

原计划是 `UniqueConstraint(event, contact)` —— 一个人在一次活动里只能有一行。
**这个假设不成立**：同一场活动里，一个人可能上午搬运、下午在签到台，
两段做的事不同、时长不同、以后要算的奖励也可能不同。合并成一行会把
"区分它们所需的维度"永久丢掉（只剩一个总工时和一个角色，事后拆不回来）。

**决定：唯一约束放宽成 `UniqueConstraint(event, contact, role)`，带 `nulls_distinct=False`。**

- 同一人 + 同一活动 + **同一角色** 的第二行 → 拒绝（防手滑重复录入的目的没丢）
- 同一人 + 同一活动 + **不同角色** → 放行（正是要支持的场景）
- `role` 可空，`nulls_distinct=False` 保证两行都不填角色时仍然算重复 —— 理由同 A7

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

`Assignment` 和 `Relationship` 共用同一套「在职 / 生效中」的派生逻辑，
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

- **`start_date` 那一半不能漏。** 只写 `end_date` 的话，一个 `start_date=2027-01-01`、
  没有结束日期的岗位**今天就算在职** —— 预录下季度上岗的志愿者，ministry 页面今天就把人算进去了，
  而且不报错，只是人数悄悄多了。
- **`on` 必须在调用时求值。** 写成 `def active(self, on=local_today())` 是经典的
  进程启动时冻结 bug，gunicorn worker 上会越跑越错。参数化顺带让"查某一天的在职名单"和
  测试边界都变成免费的。

`local_today()` 的时区口径见 **D16** —— 那条是硬性的，`timezone.now().date()` 会错一天。

顺带：显示姓名时记得 `select_related("contact")`，否则每行一次查询（N+1）。

##### 新表的约束必须和表同期落地（延续 A7 的教训）

A7 的原话是"等表里有了真数据再加，就得先清洗存量数据"。下面这些**不是**以后再补的优化项：

| 表 | 约束 | 不加会怎样 |
|---|---|---|
| `Participation` | `UniqueConstraint(event, contact, role)`，**带 `nulls_distinct=False`** | 同一人同一活动同一角色能登记 10 次，**工时统计直接错** —— 而工时是这张表的全部价值。`role` 可空且留空常见，`nulls_distinct=False` 不能省 |
| `Participation.hours` | `DecimalField`（**不是 `Float`**）+ `hours IS NULL OR hours >= 0` | Phase C 对钱写了"永远不用 `FloatField`"，工时同理（浮点累加会飘）；还能存出负工时。**`null=True`**：报名了还没发生 ≠ 干了 0 小时 |
| `Participation` | `status = 'attended' OR hours IS NULL OR hours = 0` | 否则能存出 `status=缺席` + `hours=5`。这和 `Relationship` 的 `is_active=True` + `end_date=2020` 是**同一种病**，见下面「单一真相」 |
| `Assignment` | `end_date >= start_date` | `Relationship` 在 A7 加了这条，`Assignment` 是新表却漏掉就不一致了 |
| `Assignment` | `UniqueConstraint(contact, ministry, kind, title, start_date)`，**带 `nulls_distinct=False`** | 见下面「唯一约束为什么带 `title`」 |
| `Assignment` | `reports_to` 不能指向自己那一行（`CheckConstraint`） | 见下面「汇报线的环」 |
| `Event` | `end_time >= start_time` | 同上 |
| `Event` | `capacity IS NULL OR capacity > 0` | 容量 0 或负数没有意义 |
| `Contact` | `emergency_contact_name = '' OR emergency_contact_phone != ''` | 只有名字没有电话的紧急联系人是无用数据 —— 而这个字段存在的**唯一**理由就是出事时能拨通 |
| `Ministry` / `EmploymentType` / `EventType` / `ParticipationRole` | `code` `unique=True` | 见 D5：不唯一的 `code` 不是锚点，`get(code=...)` 会抛 `MultipleObjectsReturned`，而且是在有人手滑建了第二行之后才炸 |
| `RelationshipType` | `UniqueConstraint(Lower("name_a_to_b"))` | 见下面「关系数据完整性的三个缺口」缺口 2 |

按 D14，每条约束都要配 `clean()` 提示层，两处互相注释指认。

##### `Assignment` 唯一约束为什么带 `title`

不带 `title` 的话，约束的含义是"同一个人、同一 ministry、同一 kind、同一起始日期，
最多一行"。而张三 2026-01-01 起在食物银行**同时**担任"志愿者协调员"和"库存管理"
是完全合法的 —— 两行除 `title` 外四列全同，第二行会被拒绝。
**D11 花大力气从 1:1 改成 1:N，就是为了支持这个场景**，唯一约束不能把它堵回去一半。

加进 `title` 之后：两个不同职务 → 两行，放行；四列全同**且 title 也全同** → 拦住（真手滑）。

**配套：`Assignment.save()` 里归一化 `title`**（strip 首尾空格 + 连续空白压成一个）。
不做的话 `"库存管理"` 和 `"库存管理 "` 在数据库看来是两个值，约束被静默绕过。

**不再上 `Lower("title")` 的函数唯一索引**（英文大小写不同也算重复）：
`UniqueConstraint` 用表达式时能否与 `nulls_distinct=False` 共存需要实测，
而收益只是多挡一种手滑。这条约束本来就只是防手滑的网、不是合法性判定
（真打错字的 title 它一样挡不住），复杂度不值得。

##### 汇报线的环

`CheckConstraint` 只挡得住深度 1（`reports_to` 指向自己那一行）。
**A 的上级是 B、B 的上级是 A 是两次各自合法的插入，数据库用 CHECK 表达不了跨行环路。**

后果不是脏数据而是挂死：任何递归走 `reports_to` 的代码（Phase C 的组织架构图、
ministry 页面）遇到环就是无限循环或 `RecursionError`。

两道防线，都要做：

1. **`Assignment.clean()` 向上走链**（带 `visited` 集合、限深 20）拒绝成环。
   按 D14 的标准这**只是提示层** —— `bulk_create` 和 `queryset.update()` 绕得过去，
   是一个已知的不完美，不粉饰。
2. **所有遍历汇报链的代码一律带 `visited` 兜底**，不假设数据是干净的。
   这条要写进 `Assignment` 的 docstring。

允许指向同一个人的另一个 Assignment（少见但合法，不额外禁止）。

##### 外键的 `on_delete` 一律显式指定

Phase B 一次加十几个外键，其中一个选错是灾难级的：

| 外键 | `on_delete` | 理由 |
|---|---|---|
| `Assignment.contact` | `CASCADE` | 人的档案删了，岗位记录没有意义 |
| `Assignment.ministry` | `PROTECT` | 删 ministry 不该静默带走岗位记录 |
| **`Assignment.reports_to`** | **`SET_NULL`** | ⚠️ 写成 `CASCADE` 的话，删掉项目总监那一行 → **整棵下属子树的 Assignment 连同岗位历史一起消失** |
| `Assignment.employment_type` | `PROTECT` | 字典表，同 `Contact.preferred_language` |
| `Event.event_type` / `Event.ministry` / `Event.owner` | `PROTECT` | `CASCADE` 会让删一个人带走整场活动 |
| `Participation.event` | `CASCADE` | 活动删了，参与记录没有意义 |
| `Participation.contact` | `PROTECT` | `CASCADE` 会让删一个联系人抹掉全部工时历史 —— Phase C 统计的基础 |
| `Participation.role` | `PROTECT` | 字典表 |
| `VolunteerProfile.contact` | `CASCADE` | 1:1 附属档案 |

**连带效果（是特性不是 bug）**：`Participation.contact` 用 `PROTECT` 之后，
有过活动记录的联系人就删不掉了，只能 `is_active=False` 停用。
这与推迟清单里"不做软删除、`is_active` 已覆盖停用语义"是一致的。

##### 索引

| 索引 | 服务什么查询 |
|---|---|
| `Assignment`：`Index(fields=["ministry", "kind", "end_date"])` | ministry 页面每次都是"这个 ministry + 这个 kind + 在职"，`end_date` 进索引能让整个查询走 index-only。不加就是全表扫 |
| `Event`：`Index(fields=["start_time"])` | 近期活动、admin 的 `date_hierarchy`、Phase C 的"本月活动" |
| `Event`：`Index(fields=["ministry", "start_time"])` | "食物银行这个月办了几场" —— ministry 视图的第二个数字 |
| `Participation` | `(event, contact, role)` 的唯一约束自带索引，覆盖活动侧；联系人侧走 FK 自动索引 |
| 各字典表的 `code` | `unique=True` 自带 |

小提醒：Django 给 FK 自动建单列索引，所以 `(ministry, kind, end_date)` 建好之后
`ministry` 单列索引就冗余了（最左前缀覆盖）。数据量小无所谓，知道就行。

##### 单一真相：`Assignment` 不加 `is_active`，并且**删掉 `Relationship.is_active`**

`Relationship` 现在同时有 `is_active` 和 `end_date`（`contact/models.py:247-249`），
于是可以存出 `is_active=True` + `end_date=2020-01-01` 这种自相矛盾的行。
**这违反 D11 自己那句"不是两处都能记，是只有一处能记"。**

`Assignment` 不重犯：在职状态由日期**派生**，做成上面那个 `.active()` + model property + admin 筛选器。

**`Relationship.is_active` 在本阶段删掉**（2026-07-28 修订，原计划是"既存字段不动"）。
改口的理由是原理由站不住：Phase A 刚把库整个重建过，现在只有开发数据，而这个字段
全项目只出现在 `contact/admin.py:85-86` 两行 `list_display` / `list_filter` 里，
**零业务逻辑引用**。删掉 = 一个迁移 + 改两行 admin；留着 = 一个永久自相矛盾的字段，
外加每个新人都要重新理解一次"该信哪个"。按 Phase A 反复用的那条标准
（"现在改成本≈0，以后改很痛"），就是现在删。删完 `Relationship` 复用同一个
`.active()` mixin，全项目只有一处日期派生逻辑。

##### `Contact.is_active` 和「在职」是两个概念

- `Contact.is_active` = **这条档案还在不在用**（第 564 行还把它当重复记录的墓碑用）
- Assignment / Relationship 的「在职·生效中」= **岗位或关系还在不在期内**，由日期派生

同名不同义，而 ministry 页面两个都要用到（可能出现一个已停用档案的人还挂着在职岗位）。
**规定：ministry 页面的查询必须同时过滤 `.active()` 和 `contact__is_active=True`。**

##### `Contact` 重名的处理（必须赶在建那些 autocomplete 之前）

本阶段要新增好几个指向 Contact 的 autocomplete（`reports_to` 经 Assignment、
`Participation.contact`、`Event.owner`）。而 `Contact.__str__` 现在对两个都叫"王强"的人
返回**完全一样的字符串** —— 下拉框里两个一模一样的选项，选错了不会报错，
是**静默的数据错误**（关系挂到了错的人身上）。

**不要用唯一约束禁止重名。** 重名是合法现实，这个领域**没有可靠的自然键**：
email 不能设 unique（一家人共用一个邮箱很常见）、电话同理。
这正是 CiviCRM 要做模糊查重、而不是加唯一约束的原因。

三件事，按性价比排：

1. **改 `Contact.__str__` 带上区分信息** —— `王强 (wang@example.com)`，
   没有 email 就退到电话，都没有就退到 `#42`。**这一条修好了所有下拉框**，
   是本组里唯一必做的。代价：邮箱/电话会出现在下拉和日志里（小基金会可接受，但要知道）。
   机构侧同理，不要只改个人那一支。
2. **admin 保存时软性查重提示** —— 存在同名（姓名归一化后比较：去空格、忽略大小写）
   就用 `messages.warning` 提示"已存在 2 个同名联系人，确认不是重复录入？"，
   **只提示、不阻止**（因为重名合法）。CiviCRM 查重的极简版。
3. **合并两条重复记录** —— 推迟（见推迟清单）。真攒出重复之前不做，
   因为合并要把关系、Assignment、Participation、以后的捐款全部改指到保留的那条上，是个真功能。
   过渡办法：把重复的那条 `is_active=False`，`notes` 里写"重复于 #42，已弃用"。

##### Ministry 视图：为什么 `Ministry` 表不能推迟

基金会有多个 ministry（食物银行、报税志愿、ESL…），明确想要的前端效果是
**看到各个 ministry、以及每个 ministry 的 leaders 和在职人员**。

**`Ministry` + `Assignment` 正好就是这个结构**，不需要新概念：

```
Ministry: Food Pantry
  ├─ Leaders     ← Assignment(ministry=食物银行, is_leader=True, 在职)
  ├─ Employees   ← Assignment(ministry=食物银行, kind=employee, 在职)
  └─ Volunteers  ← Assignment(ministry=食物银行, kind=volunteer, 在职)
```

**用词口径（2026-07-28 已确认）：基金会只有 employee 和 volunteer 两种说法，
没有 "worker" 这个概念** —— 界面上就分 Leaders / Employees / Volunteers 三组，
文案里不要出现 "worker"，也不要造一个把两者合起来的中间词。
（`Assignment.kind` 仍是 employee·volunteer·board 三种，理事走 `kind=board` 见 D11；
理事不属于任何 ministry，所以不出现在 ministry 页面上。）

三个已有决策刚好各自到位：

- **一人服务多个 ministry** —— 两行 `Assignment` 即可。正是 D11 从 1:1 改成 1:N 解决的场景
- **"在职"已定义** —— 不带 `is_active`，靠日期派生的 `.active()`
- **leader 用 `is_leader` 布尔标**，不要用 `title.contains("leader")` 查 ——
  `title` 是给人看的自由文本，`is_leader` 是给代码查的。同 D5 那条 code vs 显示名的道理。
  **一个 ministry 可以有多个 leader，不加约束**（联合负责人很常见，2026-07-28 确认）

**⚠️ Ministry 绝不做成 `contact_type=organization` 的 Contact 行。**
这个念头很自然（CiviCRM 风格），但在本设计里是错的：`Contact` 装的是人和**外部**组织
（D4/D6），ministry 是**内部**组织单元（D11 那一侧）。混进去会同时踩两个已知的坑 ——
联系人列表被非人记录污染，以及"外部组织归属"和内部结构的边界糊掉。

**为什么不能像 `Skill` 那样推迟**：推迟就意味着 `Assignment` 先用自由文本记 ministry 名，
以后收编成外键时要去重 "Food Pantry" / "food pantry" / "Pantry" ——
**正是 D15 论证过的那个痛的迁移方向（字段 → 关系表）**。现在建表几乎免费。

**专业系统的做法是收敛的**，这个结构不是自创：Salesforce NPSP 是
`Program` + `ProgramEngagement`（带角色和起止）、ERPNext 是 `Department` + Employee 的
department 字段、教会管理系统是 `Team` + `TeamMembership`（带 leader 角色）。
**共同点都是"一等的单元实体 + 一张带角色和起止日期的成员关系表"** —— 我们的
`Ministry` + `Assignment` 就是它。

**查询长什么样**（Phase B 建完之后）：

```python
active = Assignment.objects.active().filter(
    ministry__code="food_pantry", contact__is_active=True,
).select_related("contact")

leaders    = active.filter(is_leader=True)
employees  = active.filter(kind="employee")
volunteers = active.filter(kind="volunteer")

ministry.assignments.active()      # 从 ministry 那头看有哪些人
contact.assignments.all()          # 从人那头看服务哪几个 ministry（D11 的收益）
```

界面本身属于 Phase C（D2：前端推迟），但**数据结构必须现在就位**。
见 Phase C 里把 ministry 页面列为首选。

##### 未成年人要能查出来

`is_minor` 从 `Contact.birth_date` **派生**，**绝不要存 `age` 字段** ——
会过期，而且没有任何机制提醒你它过期了。

真实需求：**"这次活动有哪些未成年参与者、出事或活动前该拨谁的电话"**。
`Guardianship` 移出 Phase B 之后，这条需求由 `is_minor` + `Contact` 的紧急联系人字段
**完整闭环**，不依赖任何未建的表。

三件事，一件都不能少：

1. **`is_minor` 做成三态**（`True` / `False` / **`None` = 生日未知**）。
   `birth_date` 是可空的（`contact/models.py:100`），把未知折叠成 `False` 会让
   **没填生日的未成年人从家长通知名单里静默消失** —— 这正是这个功能最不能出的错。
2. **admin 里要有"生日为空的参与者"这个可见入口**，让"未知"看得见而不是被吞掉。
3. **`list_filter = ["is_minor"]` 不能用** —— property 无法进 ORM 过滤。
   必须写 `SimpleListFilter`，翻译成 `birth_date` 的区间查询
   （`> local_today() - 18 年`）。算年龄用 `dateutil.relativedelta` 或
   `date(y-18, m, d)` 加 try/except 兜 2/29，别自己数天数。日期口径见 D16。

##### `Participation.role` 和 `Assignment` 不是一回事

一句话定义，防止以后混淆：

- **`Participation.role`** = "在**这一次活动**里做什么"（临时、一次性：签到台、搬运、翻译）
- **`Assignment`** = "在**组织里**担任什么岗位"（长期：项目协调员）

不写下来，以后一定有人想把活动角色塞进 `Assignment`。

##### `Event.capacity` 是参考值，不是硬上限

超过容量只在 admin 里 `messages.warning`，**不做约束、不阻止**（2026-07-28 确认）。
口径同 `Contact` 重名：现实里超员登记是常事，系统的职责是提醒而不是拦路。
数据库层只保证 `capacity IS NULL OR capacity > 0`。

##### 背景审查：存完成日，不存到期日

`background_check_completed_on` + 有效期长度放 settings（`BACKGROUND_CHECK_VALID_DAYS`），
"是否过期"做成 property + admin 筛选器。理由和不存 `age` 完全一样：
政策改了（比如从 2 年缩到 1 年）不用洗数据。

**有效期具体多长基金会还没答复**，先用 730 天（2 年，美国非营利常见值）当占位，
`base.py` 里写清楚这是未确认的默认值。这不阻塞建模。

**敏感度**：背景审查结果是本系统里仅次于薪酬的敏感数据。D11 把薪酬排除在 MVP 之外的
理由（字段级权限、谁能看谁的）对它同样成立。Phase D 的权限方案里要和未成年人信息并列处理。

##### 关系类的收口（见 D6 / D15）

代码里查关系一律用 `RelationshipType.code`，**不用显示名**。

**补上关系的反向显示（现有缺口，必须在本阶段修）**：
`RelationshipType.name_b_to_a` 这个字段建对了 —— 一段关系只存**一行**，
靠正反两个标签从两头读，所以不需要存第二行、也不需要建第二个"反向"类型行。
**但目前没有任何代码读它**：`ContactAdmin` 只挂了一个 `fk_name="contact_a"` 的 inline
（`contact/admin.py:12`），`Relationship.__str__` 也只用 `name_a_to_b`。
结果是：录了「王强 parent of 小明」之后，**小明的页面上看不到王强**。
设计省下的那行数据已经省了，另一头的显示还欠着。要做的是二者之一：

1. 加第二个 inline（`fk_name="contact_b"`），用 `name_b_to_a` 作为标签显示；
2. 或做一个把两个方向合并起来的"此人的所有关系"列表（更好，但要自己写视图）。

两种做法都要处理**对称关系**：`is_symmetric=True` 时用 `name_a_to_b` 作为两侧的标签
（`name_b_to_a` 留空）。

**对称关系：`is_symmetric` 是显式字段，不靠推断。**
过去只能靠"`name_b_to_a` 为空"隐式推断对称性，但录入的人完全可能把 "spouse of"
同时填进正反两栏，推断就失效了。加一个显式布尔字段解决三件事：显示回落有明确依据、
归一化知道该对哪些行生效、以及缺口 3 那个"以后要冗余布尔列才能建条件唯一索引"的退路
需要的正是它。现在加成本为零。

**⚠️ 顺序不能反：双向显示必须先于归一化落地。**
对称类型在 `save()` 里把 id 小的换到 `contact_a`，会和"总是从 A 那一方的页面录入"打架 ——
用户在王强页面录了"配偶：李梅"，保存后若 a/b 交换，这条关系就跑到李梅那一侧，
而王强页面（只有 `fk_name="contact_a"` 的 inline）反而看不见了。
双向显示先做好，交换就无所谓了。

**录入方向的防线**：`(小明, 王强, parent of)` 在数据库看来完全合法，但意思反了 ——
这类语义错误约束抓不到（约束只能抓"自己是自己的父亲"）。
所以 admin 里**总是从 A 那一方的页面录入**：在王强页面开 inline，`contact_a` 自动是王强，
录的人只需选类型，方向不会错。这一点要写进 inline 的 `help_text` 或 README，
否则基金会的人一定会从任意一头录。

##### 关系数据完整性的三个缺口（A7 的约束没覆盖到，本阶段补）

A7 的唯一约束是 `(contact_a, contact_b, relationship_type, start_date)`。
它挡住了**结构重复**（一模一样的行存两次），但挡不住下面三类**语义重复** ——
数据库只认列值，不知道 "child of" 是 "parent of" 的反面。

| # | 缺口 | 现在会发生什么 | 补法 | 层级 |
|---|---|---|---|---|
| 1 | `RelationshipType` 建了反向类型 | 建一个 `name_a_to_b="child of"` 的类型，再录 `(小明, 王强, child of)` —— **三个字段和第一行全都不同，唯一约束不触发，不报错**。双向渲染做好后症状是小明页面上同一条关系**显示两遍** | `RelationshipType.clean()`：新类型的 `name_a_to_b` 撞上任何已有类型的 `name_b_to_a`（忽略大小写和首尾空格）就报错，并指出撞的是哪一行 | 提示层（类型表十几行，`clean()` 足够；这是唯一正确的拦截时机 —— 建类型那一次，而不是之后每条关系行） |
| 2 | `RelationshipType.name_a_to_b` 没有唯一约束 | 能建两个一模一样的 "parent of"，admin 下拉出现两个同名选项，选哪个都对但数据分裂成两半 | **`UniqueConstraint(Lower("name_a_to_b"))`** —— 必须大小写不敏感，普通唯一约束挡不住 "Parent of" vs "parent of"，那和缺口 1 的 `clean()` 口径也对不上。配 `save()` 里 strip 首尾空格 | **强制层** |
| 3 | 对称类型 a/b 调位 | `(王强, 李梅, spouse)` 和 `(李梅, 王强, spouse)` 同类型只是调了个位，唯一约束同样不触发 | `is_symmetric=True` 的类型在 `save()` 里归一化：一律把 id 小的放 `contact_a`，加测试钉住 | 只能到提示层 —— 见下面的诚实说明 |

> **缺口 3 做不到纯数据库强制，这一点不要粉饰。** Postgres 的函数唯一索引
> （`LEAST(a,b), GREATEST(a,b), type`）没法根据"这个类型是不是对称的"来条件生效，
> 因为那个信息在 `RelationshipType` 表里，索引跨不过去。
> 按 D14 的标准它只算提示层，**是一个已知的不完美**，不是"写了就等于防住了"。
> 真要强制，得在 `Relationship` 上冗余一个 `is_symmetric` 布尔列才能建条件索引 ——
> 那是用冗余换强制，现在不值得，记在这里等真出问题再说。

**根因说明（比三个补法更重要）**：缺口 1 的根本原因不是"存了第二行关系"，
而是**那个反向类型行本来就不该存在** —— "child of" 已经是 "parent of" 的 `name_b_to_a` 了。
类型行不存在，反向关系行就根本录不出来。所以防线加在类型层，不是关系层。

##### `code` 的三步迁移（给已有的 `RelationshipType` 加字段）

`unique=True, null=False` 的字段不能一步加到有数据的表上。必须三个迁移：

1. 加可空的 `code` 字段；
2. 数据迁移回填（从 `name_a_to_b` slugify，撞车的手工处理）；
3. 改成 `unique=True, null=False`。

**不要图省事用 `default=""` 一步到位** —— 那样所有行的 code 都是空字符串，
唯一约束当场炸。新建的字典表（`Ministry` / `EmploymentType` / `EventType` /
`ParticipationRole`）不受影响，建表时就带上。

**"不可改"怎么落地**（D5 只写了要求，没写机制）：
`editable=False` 只挡 ModelForm，脚本照改。做法是 admin 的 `get_readonly_fields`
在 change（非 add）页把 `code` 设为只读，加上 `clean()` 里比对数据库中的旧值。

##### 演示数据：`seed_demo` management command

本阶段要反复验证一人多岗、跨 kind 汇报线、一人一活动多角色这些场景，手点 admin 太慢。
`python manage.py seed_demo` 一条命令造出一组互相关联的假数据，B / C / D 都受益。

**三条安全要求，一条都不能省**：

1. **幂等** —— 全部用 `get_or_create`，跑三次不会得到三套张三（否则重名提示天天弹）；
2. **拒绝在非开发环境运行** —— 命令开头 `if not settings.DEBUG: raise CommandError(...)`，
   再加一个 `--force` 才能绕过。Phase D 上线后一次误运行就是往生产库灌假联系人，
   而按本设计它们和真人长得一模一样，事后极难清干净；
3. **只造假数据** —— 不要把任何真实的人写进代码库，名字也用明显虚构的。

##### 必须写的测试

Phase A 的 A10 用了"每条钉住什么"的清单，本阶段沿用。下面这些一条都不能少：

| 测试 | 钉住什么 |
|------|---------|
| `.active()` 边界：`end_date == 今天`算在职、`== 昨天`不算 | 派生逻辑的下界 |
| `.active()` 边界：`start_date` 在未来**不算**在职 | 上界 —— 原定义漏掉的那一半 |
| `.active(on=某日)` 能改变结果 | 时钟可注入，且没有被冻结在导入时 |
| 太平洋时间晚 8 点（UTC 已次日）判定不跨天 | D16 的时区口径 |
| 全项目没有 `date.today()` / `timezone.now().date()`（grep 守卫，放 `core/tests.py`） | D16 —— 同迁移守卫，用测试当 lint |
| `Assignment` 唯一约束在 `ministry` / `start_date` 同为空时生效 | `nulls_distinct=False` —— A7 的教训，新表重钉一遍 |
| 同 ministry 同 kind、**不同 `title`** 能存两行 | 唯一约束没有误伤"一人多岗"（D11 的核心场景） |
| 一人多岗各有不同上级，能分别查出 | D11 修订要解决的歧义 |
| 跨 kind 汇报线：执行总监(employee) `reports_to` 理事长(board) | D11 的理事会安排 |
| `reports_to` 指向自己那一行被数据库拒绝 | `CheckConstraint` |
| A→B→A 成环时 `clean()` 拒绝 | 提示层防线 |
| 删掉上级的 Assignment，下属那一行还在（`reports_to` 变 null） | `SET_NULL` 而不是 `CASCADE` |
| `Participation` 同活动同人**同角色**第二行失败；**不同角色**能存两行 | 一人一活动多角色 |
| `Participation.hours` 负数失败 | |
| `status != attended` 时 `hours` 非零失败 | 单一真相，不许自相矛盾 |
| `Event` `end_time < start_time` 失败 | |
| 两个都叫"王强"的联系人 `__str__` 不同 | 所有 autocomplete 的正确性 |
| 录「王强 parent of 小明」后，小明侧读到 "child of" | 反向显示的缺口 |
| `is_symmetric=True` 的类型显示时两侧都用 `name_a_to_b` | 对称回落 |
| `is_symmetric=True` 存入时归一化成 id 小的在前 | 缺口 3 |
| `RelationshipType` 建 "Parent of" 与已有 "parent of" 冲突 | 缺口 2 大小写不敏感 |
| `RelationshipType.code` 唯一，且 change 页只读 | D5 的锚点真的稳定 |
| `Ministry.code` / 其余字典表 `code` 唯一 | 同上 |
| `is_minor` 对 `birth_date=None` 返回"未知"而不是 `False` | 未成年人不会静默消失 |
| `is_minor` 边界：18 岁生日当天 | |
| 紧急联系人：填了名字没填电话被拒 | 该字段存在的唯一理由 |

##### 验收（2026-07-28 修正）

**你自己能在本机浏览器里完整跑通一遍**，数据全部来自 `seed_demo`：

1. 建一个 ministry，挂上一个 leader 和几个在职的 employee / volunteer，页面上分组正确；
2. 录一个志愿者（`Contact` + `VolunteerProfile` + 紧急联系人字段）；
3. 同一个人建两个 Assignment、各有不同上级，其中一条汇报线跨 kind（employee → board）；
4. 开一个活动，给**同一个人登记两个不同角色**、分别记工时，总工时对得上；
5. 一个有生日的未成年人参加活动，能在活动页筛出未成年参与者并看到他的紧急联系电话。

> **交付给基金会真用属于 Phase D，不是 Phase B。**
> 部署、备份、权限都在 Phase D，基金会的人没法用你笔记本上的 `runserver`。
> 所以 Phase B 的验收只到"我自己跑通"，用演示数据，**不进任何真实的人**。
>
> 相应地，Phase D 里那两件事是**交付的前置条件，不是可选项**：
> 1. **备份 + 真的演练一次恢复** —— 真实数据进来之前必须就位；
> 2. **非 superuser 的 staff 账号** —— 否则等于把未成年人的姓名、生日、地址、
>    紧急联系人和背景审查状态连同删库能力一起交出去，而一次误点就没了。
>
> 这两条叠加起来风险是乘法关系（能删 × 删了找不回），所以在 Phase D 里它们必须
> 排在"让基金会开始录真数据"之前，而不是同期。

#### Phase C · 资金追踪

建在新的 `finance` app 里（见 D17）。

- `Contribution`：FK Contact（捐款人） / `amount` + `currency` / `received_date` /
  `financial_type`（字典表，带 `code`：一般捐赠·指定用途·实物） / `payment_method`（字典表，带 `code`） /
  `status`（`TextChoices`：pending·completed·refunded —— 代码要按它算总额，见 D5 判定规则） /
  收据编号 / 关联 `Event`（可空 —— 某次活动筹到的钱） / 备注
- 金额一律 `DecimalField`，**永远不用 `FloatField`**。
- **必须挂 simple-history** —— 钱的记录必须能追溯是谁改的。
- "本月 / 本年"的边界一律走 D16 的 `core/timeutils` —— 用 UTC 切月份会把
  月末最后一天傍晚的捐款算进下个月。
- 第一批自己写的页面（不再是 admin），这时候上 HTMX 刚好（见 D2）。**优先级顺序**：
  1. **Ministry 视图（首选）** —— 列出各个 ministry，每个下面分 Leaders / Employees / Volunteers
     三组（用词口径见 Phase B：基金会没有 "worker" 这个说法）。
     数据结构在 Phase B 已就位（`Ministry` + `Assignment`，见那一节的「Ministry 视图」）。
     **排第一是因为它是运营工具，不是报表** —— 基金会每天都要看"食物银行现在谁在管、有几个人"，
     而捐款总额是一个月看一次的东西。先做天天用的。
  2. 某活动的总工时、志愿者活跃排行（靠 `Participation`）
  3. 本月 / 本年捐款总额（靠 `Contribution`）

#### Phase D · 上线与真实运营

> **本阶段内部有硬性顺序**：备份和权限**必须做完并验证过**，才能让基金会开始录真实数据。
> 这不是排期偏好 —— 两个风险是乘法关系（账号能删库 × 删了找不回）。
> Phase B 的验收注里记了为什么。

- 部署：Render 或 Fly.io + 托管 Postgres（保持 D3：一个 `pg_dump` 就能带走）
- **备份（交付前置条件）**：定时 `pg_dump` 到对象存储，并且**真的演练一次恢复**。
  没验证过的备份等于没有备份。
  顺带一提：D3"数据一个 `pg_dump` 就能带走"目前**还只是纸上承诺**，没有任何脚本落地 ——
  这一步才算真正兑现它。
- **权限（交付前置条件）**：用 Django Group 划分角色（管理员 / 项目协调员 / 只读），
  按 D12 不看"是不是员工"来判断，也别自造权限引擎。
  最低要求：基金会的人**不用 superuser 登录**，且默认**不给 delete 权限**。
  系统里有**未成年人的姓名、生日、地址、紧急联系人**，以及**背景审查状态** ——
  后者的敏感度仅次于薪酬（见 Phase B），要和未成年人信息一起单独考虑谁能看。
  薪酬真要入库时按 D17 走独立的 `payroll` app，整个 app 不授权给普通 Group。
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
| **`Guardianship` 法定监护专用表** | 2026-07-28 整体移出 Phase B。基金会有没有同意书流程还没答复，而"活动前通知未成年人家长"这条需求已由 `is_minor` + 紧急联系人字段闭环，它不再是任何功能的前置条件（见 D15） | 基金会答复"有同意书流程"时。形状：`minor` / `guardian` → Contact（都 `PROTECT`，不同 `related_name`）/ 监护类型 / 同意书签署日期 / 能否代签 / 起止日期；建的时候**必须**同期带上 `UniqueConstraint(minor, guardian, start_date, nulls_distinct=False)`、`end_date >= start_date`、"监护人不能是自己"、simple-history，以及**两侧的 admin inline**（否则会重犯 `Relationship` 的反向显示缺口）。放 `contact` app（D17） |
| 紧急联系人升级成关系表 | **方向反了**：2026-07-28 已决定走 `Contact` 上的三个字段（见 D15），现在欠的是"字段 → 关系表"这个痛的迁移方向。这是明知代价的选择，D15 写了为什么可以 | 真的需要反查"这个人是谁的紧急联系人"、或需要第二组联系人时 |
| 合并两条重复的 Contact 记录 | 是个真功能：要把关系、Assignment、以后的捐款全部改指到保留的那条上。过渡办法是把重复那条 `is_active=False` + `notes` 注明"重复于 #42" | 真攒出一批重复、且过渡办法开始碍事时 |
| `Skill` 字典表 + `VolunteerProfile.skills` | 需求不紧急，且**没有任何东西依赖它**（ministry 视图不需要技能）。设计已想清楚：字典表带 `code`（D5 通则）+ M2M 挂 `VolunteerProfile`，要加时照 D5 直接建，无需重新设计 | 真的要按技能找志愿者时（"谁会西班牙语翻译"）。注意语言偏好已有 `Contact.preferred_language`，别和技能混为一谈 |
| Ministry 的层级（子 ministry） | ERPNext 的 Department 是树形，但小基金会大概率是平的。真要加就是一个可空的 `parent` 自引用 FK —— 按 D15 的三条件检验：最多一个父、无独立属性、只有一种类型，**自引用 FK 正是对的载体** | 真出现"报税志愿下面还分几个小组"时 |
| `AssignmentRole` 字典表（取代 `is_leader` 布尔） | 现在只需要区分 leader / 非 leader，一个布尔够了 | 角色长到第三种时（如 leader / 副手 / 培训中），按 D5 升级成带 `code` 的字典表 |
| **活动的班次（`Shift`）** | 2026-07-28 决定：多班次一律拆成多个 `Event`。行业标准结构确实是三层（Salesforce V4S 的 Job → Shift → Hours），但拆 Event 之后时段差异由 Event 表达、做的事差异由 `Participation.role` 表达、时长差异由各行 `hours` 表达，三个维度一个不少，而少一张表少一层 admin 嵌套 | 一场活动的班次多到"拆成十个 Event"开始碍事时 |
| **活动的分组 / 系列（`Event.parent`）** | 拆成多个 Event 的代价是"上午场 / 下午场"在统计里算两场。按 D15 三条件检验：最多一个父、无独立属性、只有一种类型 → **自引用 FK 正是对的载体**，和 Ministry 层级同理 | 真的需要"这一整天总共来了多少人次"时 |
| **一个人一次活动的奖励规则** | 规则还不明确（按班次算？按角色算？按工时算？）。猜错就是白写字段。关键是**区分它所需的三个维度已经全部存下来了**（哪个 Event、什么 role、多少 hours），结构撑得住 | 基金会说清楚奖励怎么算时 —— 那时只是加字段，不用改结构 |
| 邮件群发 / 对外活动报名页 | 属于对外系统，和内部管理是两回事 | Phase D 之后 |

---

## 六、下一步

**Phase A 已完成**（2026-07-27，分支 `phase-a`，A1–A10 全部验收通过，27 个测试全绿）。

**本文档于 2026-07-28 按一轮 Phase B 评审全面修订**（新增 D16 / D17，重写 D5 / D15，
Phase B 整节重排）。改动清单见文末「2026-07-28 修订记录了什么」。

下一步是 **Phase B · 人与活动 MVP**，要建的表和实现要点见上面那一节。开工前先做两件事：

1. 把 `01-roadmap.md` 换成 Phase B 的实施步骤（那份文档一次只服务一个 Phase）；
2. 重读 D10 / D11 / D15 / D16 —— `Assignment` 是 Phase B 的核心表，而 D11、D15 都是
   被修订或经过多轮否决才定下的决策，**修订的理由比结论更重要**，
   照着旧直觉写会重新掉进同一个坑。D16 是新的，且它管的那类错误不报错。

**`Guardianship` 不再阻塞开工**（2026-07-28）：整张表已移出 Phase B，
等基金会答复同意书流程再说，见 D15 的「待确认」和推迟清单。

Phase B 的验收是：**你自己能在本机跑通一遍完整流程** —— 建 ministry 并挂上 leader 和成员、
录志愿者、一人多岗 + 跨 kind 汇报线、开活动并给同一个人登记两个角色和各自工时、
筛出未成年参与者并看到紧急联系电话。数据全部来自 `seed_demo` 造的假数据。

**交付给基金会真用属于 Phase D**，前置条件是备份演练过、且他们不用 superuser 登录。
理由记在 Phase B 的验收注和 Phase D 的开头。

### 还没定的（都不阻塞 Phase B 开工）

| # | 待定的事 | 等谁 | 不定会怎样 |
|---|---------|------|-----------|
| 1 | 未成年志愿者有没有同意书 / 家长授权流程 | 基金会 | 只决定 `Guardianship` 什么时候建，Phase B 已绕开 |
| 2 | 背景审查有效期多长 | 基金会 | 先用 730 天占位，`base.py` 里注明是未确认默认值 |
| 3 | `EmploymentType` 的实际取值 | 基金会 | 不影响建模 —— 正因为不知道才做成字典表，到时候 admin 里加行 |

---

## 七、2026-07-28 修订记录了什么

| # | 改了什么 | 为什么 |
|---|---------|-------|
| 1 | 交付策略从"能给基金会的人看"改成"可演示" | 与 Phase B 已修订的验收口径**自相矛盾**，总纲当时漏改了 |
| 2 | 紧急联系人从 `Relationship` 改成 `Contact` 上的三个字段（D15 载体一从❌改成✅） | 幽灵记录是天天付的代价，反查需求则很可能永远不出现。明知接受了"字段→表"这个痛的迁移方向，靠三条写死的边界防止债务增长 |
| 3 | `Guardianship` 整体移出 Phase B | 改 2 之后"活动前通知家长"已由 `is_minor` + 紧急联系电话闭环，它不再是任何功能的前置条件 |
| 4 | `.active()` 补上 `start_date` 判定 | 原定义只看 `end_date`，未来才上岗的人**今天就算在职**，而且不报错 |
| 5 | 新增 **D16 · 时间口径** | `timezone.now().date()` 是 UTC 日期，PT 下午 5 点后判定提前跨天。三层落地：单一入口 + 注入时钟 + ruff `DTZ` 与 grep 守卫 |
| 6 | 新增 **D17 · app 划分**，并预留 `finance` / 独立的 `payroll` | Phase A 已为依赖方向付过一次学费；`payroll` 独立成 app 是为了将来能整 app 级隔离权限 |
| 7 | D5 补「`TextChoices` vs 字典表」判定规则 | 原来的"会变的做字典表"不足以判定 —— 代码要按它分支的，做成字典表等于把枚举藏进数据库 |
| 8 | D5 的 `code` 补齐 `unique` + 小写归一化 + 不可改的落地机制 | 原来只写了"要带 `code`"。不唯一的 `code` 根本不是锚点，`editable=False` 也挡不住脚本 |
| 9 | `Participation` 唯一约束从 `(event, contact)` 放宽成 `(event, contact, role)` | 原约束假设"一个人在一次活动里只做一件事"，不成立。同时决定**不建 `Shift`**，多班次拆成多个 Event |
| 10 | `Assignment` 唯一约束加入 `title` | 原约束会误伤"同一 ministry 内一人两职"，而那正是 D11 从 1:1 改成 1:N 要解决的场景 |
| 11 | 全部外键显式指定 `on_delete`，`reports_to` 用 `SET_NULL` | 原文一个字没提。`reports_to` 写成 `CASCADE` 会让删一行带走整棵下属子树 |
| 12 | 补索引：`(ministry, kind, end_date)`、`Event.start_time`、`(ministry, start_time)` | 原文只有一个 `(ministry, kind)` |
| 13 | 决定在 Phase B **删掉 `Relationship.is_active`** | 原计划"既存字段不动"的理由站不住：库刚重建、全项目只有两行 admin 引用它 |
| 14 | 汇报线加"环"的防线（`clean()` 走链 + 遍历带 `visited`） | `CheckConstraint` 只挡得住深度 1，A→B→A 会让递归视图挂死 |
| 15 | `is_minor` 改三态，并明确 `list_filter` 要用 `SimpleListFilter` | 生日为空的未成年人会从家长通知名单里静默消失；property 不能进 ORM 过滤 |
| 16 | 背景审查改成存 `completed_on` + settings 里的有效期 | 同不存 `age` 的道理：政策变了不用洗数据。顺带补上它的敏感度 |
| 17 | `RelationshipType` 缺口 2 改成 `Lower()` 的大小写不敏感唯一约束 | 原方案与缺口 1 `clean()` 里"忽略大小写"的口径对不上 |
| 18 | `RelationshipType` 加显式 `is_symmetric`；**双向显示必须先于归一化落地** | 对称性原来靠"`name_b_to_a` 为空"隐式推断；顺序反了会让关系从录入者的页面上消失 |
| 19 | `Event` 加 `ministry` 外键 | ministry 视图是核心，却查不出"食物银行这个月办了几场" |
| 20 | `code` 加到已有表要三步迁移，写明 | 一步加 `unique + not null` 到有数据的表上会当场炸 |
| 21 | `seed_demo` 补幂等 + 非 DEBUG 拒绝运行 | 上线后一次误运行就是往生产库灌假联系人，而它们和真人长得一模一样 |
| 22 | 测试清单从 3 条扩到 26 条 | 沿用 A10「每条钉住什么」的格式。`.active()` 是全系统复用最多的谓词，边界必须钉死 |
