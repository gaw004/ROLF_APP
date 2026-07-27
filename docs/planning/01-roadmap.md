# 路线图：从 Contact 到可用的志愿者 & 资源管理系统

> 承接 `00-getting-started.md`。那篇讲"该怎么想"，这篇讲"接下来按什么顺序做"。
> 写于 2026-07-27，当时的状态：`contact` app 已有 Contact / Relationship /
> RelationshipType / Language 四个模型 + admin + 测试，数据库还是 SQLite。

---

## 现状盘点

**已经做对的，不要动：**

- `Contact` 用 `contact_type` 区分 individual / organization —— 这是 CiviCRM 的核心设计，
  一张表管人和机构，避免了"公司捐款人"这类记录无处安放。
- `RelationshipType` 是数据表而不是 Python 枚举 —— 基金会以后想加"理事会成员"、
  "紧急联系人"这类关系，不用改代码、不用迁移，在 admin 里加一行就行。这正是"需求变了还能用"。
- `Language` 自建表（ISO 639-3）而不是用 `languages_plus`（ISO 639-1）—— 决策理由已写在
  模型 docstring 里，且能覆盖 Mandarin/Cantonese/Hmong。
- 业务规则写在 `Contact.clean()` / `Contact.save()` 而不是只写在 form 里 —— 所以从
  admin、脚本、以后的 API 存都一致。
- 有测试。第一次写全栈就有测试，这是后面敢重构的唯一底气。

**必须在加新功能之前解决的（按紧急程度）：**

| # | 问题 | 为什么不能拖 |
|---|------|------------|
| 1 | 没有自定义 User model | Django 项目一旦有了真实用户数据，换 `AUTH_USER_MODEL` 极其痛苦（要手工写数据迁移、重建外键）。现在库里只有测试数据，改的成本≈0 |
| 2 | 还在 SQLite | SQLite 和 Postgres 在约束、JSONField、大小写敏感、并发上行为不同。等写了几个月业务逻辑才切，等于要重新测一遍所有东西。`postgresql@18` 本机已装 |
| 3 | `SECRET_KEY` 硬编码且已进 git 历史 | 上线前必须换成新的、从环境变量读。已泄露的 key 不能再用 |
| 4 | `DEBUG = True`、`ALLOWED_HOSTS = []`、无 `STATIC_ROOT` | 部署前必须改；越早拆成 dev/prod 配置越不容易上线时手忙脚乱 |
| 5 | `TimeStampedModel` 住在 `contact/models.py` | 下一个 app（volunteer / event）要用它，就得 `from contact.models import ...`，依赖方向反了。以后想单独理解或替换 contact 会被缠住 |
| 6 | `countries_plus` / `languages_plus` 装了但没用 | 它们各自建了几千行的表在你库里。既然已经自己写了 `Language`，删掉减少依赖和迁移噪音 |
| 7 | `Relationship` 缺数据库约束 | 现在可以存"Alice 是 Alice 的母亲"，也可以把同一段关系重复存 10 遍。约束加在数据库层，脏数据就永远进不来 |
| 8 | 没有审计日志 | 基金会场景下"谁在什么时候改了这条捐款记录"是刚需，而且这是你自己文档里列为"值得抄"的一条 |
| 9 | `READ.md` 是空的 | 半年后的你（或下一个接手的人）需要知道怎么把这个项目跑起来 |

---

## 一个关键架构决策：志愿者不是一张新表的"人"

马上要建 Volunteer，这里有个岔路口，选错了以后很难回头：

- ❌ **建一个独立的 `Volunteer` 模型，里面又有姓名、电话、地址** ——
  那么一个人既捐款又做志愿者时，就有两份档案、两个地址，改了一个另一个不同步。
  这是小系统最常见的死法。
- ✅ **`Contact` 是所有人的唯一档案；"志愿者"是这个人的一个角色**：

  ```
  Contact  ←1:1→  VolunteerProfile   (技能、可服务时段、背景审查状态、紧急联系人)
  Contact  ←1:N→  Contribution       (捐款)
  Contact  ←N:M→  Event  via Participation  (参与活动 + 工时)
  ```

  同一个人可以同时是志愿者、捐款人、活动负责人，档案只有一份。
  这就是 CiviCRM 的做法，也是为什么它一个 Contact 表能撑起整个产品。

判断标准：**凡是"这个人是谁"的信息（姓名、联系方式、地址、语言）→ 放 `Contact`。
凡是"这个人在某个身份下的信息"（技能、审查状态、捐款额）→ 放对应的角色表。**

---

## Phase A — 地基加固（先做完这个，再写任何业务功能）

目标：跑在 Postgres 上、有自定义 User、配置可安全部署、共享代码归位。
做完的验收标准是**功能上什么都没变，测试全绿**。

1. **建 `core` app**，把 `TimeStampedModel` 挪进去，`contact` 改为从 `core` 引入。
   以后所有 app 共享的抽象基类都放这。
2. **建 `accounts` app + 自定义 User**（继承 `AbstractUser` 即可，先不加字段，
   关键是把 `AUTH_USER_MODEL = "accounts.User"` 这个开关先占住）。
   趁库里没有真实用户，直接删掉 `db.sqlite3` 重建最省事。
3. **接 Postgres**：本机 `postgresql@18` 建一个 `rolf_dev` 库，`DATABASES` 从环境变量读
   （`python-dotenv` 已在 requirements 里），写 `.env.example` 进 git、`.env` 不进。
   重跑所有迁移和测试，确认在 Postgres 下依然全绿。
4. **拆配置**：`config/settings/base.py` + `dev.py` + `prod.py`（或单文件 + 环境变量分支，
   项目还小，单文件也行）。`SECRET_KEY`、`DEBUG`、`ALLOWED_HOSTS`、`DATABASE_URL` 全部进环境变量。
   **生成一个新的 `SECRET_KEY`**，旧的已经在 git 历史里，作废。
5. **清依赖**：`INSTALLED_APPS` 移除 `countries_plus`、`languages_plus`，requirements 同步删掉。
6. **给 `Relationship` 加约束**：
   `CheckConstraint(contact_a != contact_b)` + `UniqueConstraint(contact_a, contact_b, relationship_type, start_date)`，
   并补上对应的测试。
7. **加审计**：装 `django-simple-history`，挂在 `Contact` 上（以后 `Contribution` 一定要有）。
   一个 decorator 的成本，换来完整的修改历史。
8. **写 `README.md`**：怎么建虚拟环境、怎么起数据库、怎么跑测试、怎么起服务。
   顺手删掉空的 `READ.md`。

---

## Phase B — 志愿者与活动 MVP（第一个能给基金会看的东西）

按 `00-getting-started.md` 里的 Phase 1，但模型按上面的"角色"思路建。

- `volunteer` app：
  - `Skill`（字典表，和 `RelationshipType` 同理，admin 里能加）
  - `VolunteerProfile`：OneToOne → Contact，字段包括 skills(M2M)、
    background_check_status + 日期、start_date、availability_notes、
    emergency_contact（指向另一个 Contact，或先用文本字段）
- `event` app：
  - `Event`：名称、类型（字典表）、起止时间、地点、负责人(FK Contact)、状态、容量
  - `Participation`：FK Event + FK Contact + 角色 + 状态（报名/出席/缺席）+ **hours**
    这张中间表是整个系统的价值所在——工时统计、志愿者活跃度、活动回顾全靠它
- Admin：Event 页面用 inline 直接登记参与者；Contact 页面用 inline 显示 TA 参加过什么
- 验收：**基金会的人能在浏览器里录一个真志愿者、开一个真活动、登记出席和工时**

这一步结束就该让基金会开始真用了。不要等到"做完了"再给人看 ——
真实使用暴露出的需求，比你现在猜的准得多。

---

## Phase C — 钱（资源追踪）

参考 CiviCRM 的 Contribution 模型，只抄字段设计：

- `Contribution`：FK Contact（捐款人）、amount + currency、received_date、
  `financial_type`（字典表：一般捐赠 / 指定用途 / 实物）、
  `payment_method`（字典表）、`status`（pending / completed / refunded）、
  收据编号、关联 Event（可空——某次活动筹到的钱）、备注
- **一定要挂 simple-history**，钱的记录必须能追溯谁改的。
- 金额用 `DecimalField`，永远不要用 `FloatField`。
- 第一批自己写的页面（不再是 admin）：本月/本年捐款总额、某活动总工时、
  志愿者活跃排行。这时候上 HTMX 刚好。

---

## Phase D — 上线与真实运营

- 部署：Render 或 Fly.io + 托管 Postgres（保持"一个 `pg_dump` 就能带走"）
- **备份**：定时 `pg_dump` 到对象存储，并且**真的演练一次恢复**。
  没验证过的备份等于没有备份。
- 权限：用 Django Group 划分角色（管理员 / 项目协调员 / 只读），
  别自己造权限引擎（你自己的文档里也说了"别碰复杂权限引擎"）
- CSV 导出、简单报表
- 安全：`SECURE_SSL_REDIRECT`、HSTS、`SESSION_COOKIE_SECURE`、
  跑一遍 `manage.py check --deploy` 直到没有警告

---

## 明确推迟的事（记下来，免得反复纠结）

| 事情 | 为什么现在不做 | 什么时候再看 |
|------|--------------|------------|
| 一个 Contact 多个 email / 电话 / 地址 | CiviCRM 拆成独立表，但现在单字段够用，且随时能加表迁移过去 | 真的出现"志愿者有工作和私人两个邮箱要分别用"时 |
| Membership / 会员制 | 基金会未必有会员概念 | 需求出现时 |
| REST API、前后端分离 | admin + HTMX 能撑很久 | 要做志愿者自助登录的手机端时 |
| 软删除 | 现有 `is_active` 已覆盖"停用"语义，别同时上两套 | 出现"误删要恢复"的真实事故时 |
| 邮件群发 / 活动报名页 | 属于对外系统，和内部管理是两回事 | Phase D 之后 |

---

## 下一步

Phase A 的 8 条是可以连着做完的一串（大半天到一天），做完之后项目就站在
"能安全上线、能长期加功能"的地基上了。建议从 1、2、3 开始——它们互相咬合，
一起做比分开做省事。
