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
  注意：`manages` / `managed by` 已不用于组织架构，汇报线走 `Position.reports_to`（见 D6 适用范围、D11）。
- **`Relationship`** —— 连接两个 Contact，带类型和起止日期（见 D6）。
  Phase A 建了三条约束：禁自我关系、禁完全相同的重复行（`NULLS NOT DISTINCT`）、`end_date >= start_date`。
  ✅ **第二条已在 B3.2 被"无序对唯一约束"替换** —— A7 当时判定"镜像重复数据库表达不了"，
  **那个判断是错的**（用 `Least`/`Greatest` 表达得了，只是当时以为需要按类型条件生效）。见缺口 3。
  ✅ **`is_active` 字段已在 B3.3 删掉** —— 它和 `end_date` 是同一件事记两处，见 Phase B 的「单一真相」。
  ✅ **反向显示已在 B3.1 补上**（两个只读 inline），录入移到 `/relationships/add/`。
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

**具体怎么做见 `01-roadmap.md`**（Phase A 的实施手册，已完成，留作记录）。
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
> 一份新的 `0N-roadmap.md`（一份只服务一个 Phase，旧的留作记录）。
> **Phase B 的实施手册是 `02-roadmap.md`；B0–B5 已完成，B6–B13 按 2026-07-29 的优先级重写过。**

#### Phase C · 上线与真实运营

> ⚠️ **2026-07-29 重排。** 原来的 Phase C 是「资金追踪」、Phase D 是「上线与权限」。
> **两者对调了**，理由是[零](goal.md#零当前优先级2026-07-29-定)：14 条需求里没有一条碰钱，
> 而它们全都需要基金会的人**真的用起来**才有意义（发活动、报名、签到都是真人的动作）。
> **对着假数据做资金模块，是这次要纠正的那类偏离的翻版。**

> **本阶段内部有硬性顺序**：备份**必须做完并验证过**，才能让基金会开始录真实数据。
> 这不是排期偏好 —— 两个风险是乘法关系（账号能删库 × 删了找不回）。
>
> **权限不在这一阶段了** —— 它随 D21 提前进了 Phase B。这里只剩"检查它真的到位"。

- 部署：Render 或 Fly.io + 托管 Postgres（保持 D3：一个 `pg_dump` 就能带走）
- **备份（交付前置条件）**：定时 `pg_dump` 到对象存储，并且**真的演练一次恢复**。
  没验证过的备份等于没有备份。
  顺带一提：D3"数据一个 `pg_dump` 就能带走"目前**还只是纸上承诺**，没有任何脚本落地 ——
  这一步才算真正兑现它。
- **权限复核（交付前置条件）**：Phase B 已经建好 `MinistryRole` + `permissions.py`（D20）
  和志愿者账号的隔离（D21）。这里要做的是**验证**，不是设计：
  - 基金会的人**不用 superuser 登录**，且默认**不给 delete 权限**
    （尤其是 `delete_event` —— 它两级级联到 `Participation`，见 `on_delete` 表）
  - 志愿者账号 `is_staff=False`，`/admin/` 返回 403
  - 拿一个 A ministry 的 admin 账号，**试着去看 B ministry 的报名名单**，确认被挡
  - 系统里有**未成年人的姓名、生日、地址、紧急联系人**和**同意记录** —— 谁能看要过一遍
  - 薪酬真要入库时按 [D17](decisions/D17-app-layout.md#d17--app-划分一个-app-一个业务领域敏感数据单独成-app2026-07-28) 走独立的 `payroll` app，整个 app 不授权给普通 Group
- **运营页面**（Phase B 的自助页面之外，给内部人员用的）——
  ⚠️ **2026-07-29 晚从「Phase D 资金追踪」搬过来的**，它被落在那一节里是 C/D 对调时的漏改，
  而本文档有两处（[合并页那一节](phase-b.md#界面一个朴素的-django-视图不做成-admin-动作2026-07-28-修订)、
  [Ministry 视图那一节](phase-b.md#ministry-视图为什么-ministry-表不能推迟)）一直写着"见 Phase C 的优先级顺序"：
  1. **Ministry 视图（首选）** —— 列出各个 ministry，每个下面分 Leaders / Employees / Volunteers
     **加上「空缺」**四组（用词口径见 Phase B：基金会没有 "worker" 这个说法）。
     数据结构在 Phase B 已就位（`Ministry` + `Position` + `Assignment`）。
     **组织架构图**也在这一步，它只依赖 `Position` 一张表，不 join 任职数据。
     **视图直接 `import org.services.build_org_tree`，不要自己递归 `reports_to`** ——
     环的兜底和 N+1 的规避都在那个函数里，Phase B 已经写好并测过（见「汇报线的环」）。
     **排第一是因为它是运营工具，不是报表** —— 基金会每天都要看"食物银行现在谁在管、有几个人"，
     而捐款总额是一个月看一次的东西。先做天天用的。
  2. 志愿者活跃排行、跨活动的总工时（靠 `Participation`；单场活动的 R4–R8 Phase B 已有）
  > 这里**不是**"第一批自己写的页面"了 —— Phase B 已经出了
  > `/contacts/merge/`、`/relationships/add/` 和一整套自助页面（D21）。
  > 到这一步"视图 + 模板 + URL + 权限"这条路已经跑通过多次，可以直接上 HTMX。
- CSV 导出、简单报表
- 安全：`SECURE_SSL_REDIRECT`、HSTS、`SESSION_COOKIE_SECURE`，
  跑 `manage.py check --deploy` 到没有警告

#### Phase D · 资金追踪

建在新的 `finance` app 里（见 D17）。**整体后移**（2026-07-29），理由见上。

- `Contribution`：FK Contact（捐款人） / `amount` + `currency` / `received_date` /
  `financial_type`（字典表，带 `code`：一般捐赠·指定用途·实物） / `payment_method`（字典表，带 `code`） /
  `status`（`TextChoices`：pending·completed·refunded —— 代码要按它算总额，见 D5 判定规则） /
  收据编号 / 关联 `Event`（可空 —— 某次活动筹到的钱） / 备注
- 金额一律 `DecimalField`，**永远不用 `FloatField`**。
- **必须挂 simple-history** —— 钱的记录必须能追溯是谁改的。
- "本月 / 本年"的边界一律走 D16 的 `core/timeutils` —— 用 UTC 切月份会把
  月末最后一天傍晚的捐款算进下个月。
- 报表页：本月 / 本年捐款总额（靠 `Contribution`）。用 HTMX，同 Phase C 的运营页面。

> ⚠️ **2026-07-29 晚删掉了紧跟在这一节后面的整个「Phase D · 上线与真实运营」小节。**
> 那是 C / D 对调时**漏删的旧版本**，和上面的 [Phase C](#phase-c--上线与真实运营) 近乎逐字重复
> （于是文档里一度有两个 Phase D），而且它里面留着两句已经作废的话：
> ① "用 Django Group 划分角色（管理员 / 项目协调员 / 只读）" ——
> [D20](decisions/D20-ministry-role.md#d20--范围化权限-ministryrole不走-django-group2026-07-29) 明确判定这句话在当前需求下不成立，
> 并声称"已在原地改掉"，**实际上它在这里和 D12 里各存活了一份**（D12 那份同日一并改掉了）；
> ② "背景审查状态……见 Phase B" —— `BackgroundCheck` 已移出 Phase B。
> 唯一还有效的那句（薪酬走独立 `payroll` app）已并入 Phase C 的权限复核。

---
