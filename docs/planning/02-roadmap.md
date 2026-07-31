# Phase B 实施手册 —— 人与活动，以及活动闭环

> 这份文档只讲 Phase B 怎么做。 要做什么、为什么这么定，全在 `goal.md` ——
> 那是唯一权威来源，本文与它冲突时以它为准。
> `01-roadmap.md` 是 Phase A 的实施手册，已完成，留作记录，不再更新。
>
> 写于 2026-07-28，**2026-07-29 从 B6 起整段重写**。

> ## 当前进度与去哪读（2026-07-29）
>
> | 步骤 | 状态 | 是什么 |
> |---|---|---|
> | B0–B5 | ✅ **已完成** | `core` 时间口径 + `contact` 三处收口 + `org` 四张表。下面 B0–B5 那几节原样保留，现在只在维护时才翻 |
> | B6–B13 | ⬜ **当前在做** | 按基金会 2026-07-29 给出的 14 条需求重写过。 跳到 [B6 起的那一段](#b6-起按-2026-07-29-的优先级重写) |
>
> 原来的 B6–B9（`events` 四张表 / `volunteer` / `seed_demo` / 验收）已被 B6–B13 取代。
> 主要差别：多了 `EventRole` 和 `MinistryRole` 两张表、多了一组自助页面、
> `VolunteerProfile` 和 `BackgroundCheck` 移出本阶段。
> 完整改动清单见 `goal.md`[七、2026-07-29 修订记录](revisions.md#七2026-07-29-修订记录了什么)。
>
> ⚠️ **下面「这一阶段要达成什么」「明确不做的」「为什么按这个顺序」三节写的是 B0–B5 的口径**，
> B6 起的对应内容在[那一段的开头](#这半程要达成什么)。
>
> **开工时的实测基线**（跑出来的，不是估的）：
>
> | 项 | 实测值 |
> |---|---|
> | `python manage.py test` | **27 个，全绿**（0.63s） |
> | `python manage.py check` | 0 issues, 0 silenced |
> | 已有 app | `core` / `contact` / `accounts` |
> | 已有模型 | `Contact` / `RelationshipType` / `Relationship` / `Language` / `User` |
> | `contact` 迁移 | 到 `0004_historicalcontact` |
> | 数据库 | Postgres 18 在跑，psycopg 3 |
> | Django / Python | 5.2.16 / 3.14.6 |
> | 开发库里的业务数据 | `Contact` 0 行、`Relationship` 0 行、`RelationshipType` 0 行、`Language` 7923 行、`User` 1 个 |
>
> ⚠️ **最后一行很重要，直接改变了两处做法**，见 B0 的「实测发现」。

---

## 这一阶段要达成什么

把"人"和"活动"两条线建起来，做完能在本机完整演示一遍基金会的日常：
有哪些编制、谁在哪个 ministry 占着哪个编制、哪个编制向哪个编制汇报、**哪些编制空着**；
办了什么活动、谁参加了、干了几小时。

> **2026-07-28 二次修订**：`Position`（编制）从 `Assignment` 里拆出来了，B5 整段重写。
> 见 `goal.md` D11「第二次修订」和文末「计划外记录」上面的那条说明。
>
> **2026-07-28 第九轮**：汇报链遍历收进 `org/services.py::build_org_tree()` 一处，
> 配 grep 守卫（B1）。原来那条"所有遍历都带 `visited` 兜底"是纪律，现在是结构。
> 同轮否决了"改用 Postgres `LTREE`"的评审建议 —— 见 `goal.md`「汇报线的环」。

**同时把 `contact` 现有的三个欠账收掉**（关系的反向显示、类型表的 `code`、
`Contact.__str__` 的重名消歧）—— 它们不收，后面新建的每一个 autocomplete
和每一处按类型的查询都会踩在流沙上。

### 验收标准

**你自己能在本机浏览器里跑通一遍完整流程，数据全部来自 `seed_demo`，
不进任何真实的人。** 逐条清单见 B13（**旧编号是 B9** —— B6 起重写后 B9 是自助页面，验收挪到了 B13）。

> **交付给基金会真用属于 Phase C**（2026-07-29 C / D 对调后；原文写的是 Phase D），
> 前置条件是备份演练过、且他们不用 superuser 登录。
> 理由见 [Phase B 的验收注](phase-b.md#验收2026-07-29-重写改成按-14-条需求逐条验收)
> 和 [Phase C 开头](progress.md#phase-c--上线与真实运营)。

### 明确不做的（免得中途手痒）

| 不做 | 去哪了 |
|---|---|
| `Guardianship` 法定监护表 | 移出 Phase B，等基金会答复同意书流程（`goal.md` D15 待确认） |
| `Skill` + `VolunteerProfile.skills` | 推迟清单 |
| 活动班次 `Shift` | 推迟清单 —— 多班次一律拆成多个 `Event` |
| 逐字段合并的交互界面 | 推迟清单 —— 合并功能本身要做，界面从简 |
| HTMX / 样式 / 任何面向外部用户的页面 | Phase C（D2：前端推迟） |
| ~~自己写的页面~~ | **两个例外**（都被 `goal.md` D18 的形状触发赶出 admin）：`/relationships/add/`（B3.1b，inline 拿不到 subject，且 Phase C 的 HTMX 不用 formset）和 `/contacts/merge/`（B4.4，二次确认页要吃 `admin/base_site.html`、"待处理 N 条"要覆盖 `admin/index.html`）。**都是单页面、无 HTMX、无样式、逻辑全在 `services.py` 里，Phase C 原样接管** |
| 薪酬 | 推迟清单 + `payroll` app 的位置已在 D17 预留 |
| 在 `clean()` 里重写一遍约束的规则 | 2026-07-28 D14 重写：规则只在约束里，字段级提示走 `CONSTRAINT_FIELD` 映射。`clean()` 只写约束表达不了的（跨表、跨行） |
| `Contact.is_reference_only` / `Contact.emergency_contact` / `Contact.objects.people()` | 2026-07-28 第六轮整体作废，紧急联系人改用 `EmergencyContact` 专用表（B4.2）。一个字段都不要加 |
| 把 `EmergencyContact.name` / `.phone` 升级成 FK → `Contact` | 推迟清单 —— **且这是「痛」的迁移方向**。重复存储是主动接受的代价，别在实施时顺手优化掉 |
| **带日期的编制层级**（组织架构的历史） | 推迟清单 —— 本阶段解决的是"**换人**"，不是"**重组**"。`Position.reports_to` 改了，旧架构只剩 simple-history |
| `Position.headcount`（编制人数） | 推迟清单 —— `vacant()` 只认"一个人都没有"，表达不了"3 个坑填了 2 个" |
| 把邻接表换成 `LTREE` / 递归 CTE | 推迟清单 —— 2026-07-28 评审建议过，未采纳。几十行的表，`build_org_tree()` 一次查询取全表就是最优解。**中途手痒时先读 `goal.md`「为什么不上 Postgres 的 LTREE 扩展」那张表** |

---

## 为什么按这个顺序

三条硬依赖决定了整个顺序，其余的按"谁先建谁被依赖"排：

1. **`core` 的时间口径和 `.active()` 必须最先** —— 后面每一张带日期的表都用它。
2. **`contact` 的收口必须排在所有新表之前** ——
   - `RelationshipType.code` 要赶在**任何按类型查询的代码**写出来之前（晚了字符串匹配就扩散了）；
   - `Contact.__str__` 消歧要赶在 **B5/B6 那三个新 autocomplete** 之前（晚了下拉框里全是一模一样的选项）；
   - `EmergencyContact.relationship_type` 依赖 `RelationshipType.usable_as_emergency_contact`。
3. **关系的双向显示必须先于对称归一化** —— 顺序反了，用户刚录的关系会从他的页面上消失
   （`goal.md` Phase B「对称关系」那一条）。

```
B0 基线与准备（分支 / ruff / 确认不阻塞项）                                    ✅
 └→ B1 core：local_today() + DateRangeQuerySet + 守卫测试 + 建空的 services.py  ✅
     └→ B2 contact①：RelationshipType 收口（code / is_symmetric / 唯一约束）    ✅
         └→ B3 contact②：Relationship 收口（双向显示 → 归一化 → 删 is_active）  ✅
             └→ B4 contact③：Contact 收口（__str__ / 紧急联系人 / 查重 / 合并）  ✅
                 └→ B5 org：Ministry + EmploymentType + Position + Assignment  ✅
                     └→ B6 起见下面那一段（2026-07-29 重写）
```

**B5 内部还有一条硬顺序**：`Ministry` → `Position` → `Assignment` ——
`Assignment` 只有 `position` 一个业务外键，没有 `Position` 它就是空壳。
**B6 起有一条同形状的硬顺序**（`EventRole` → `Participation`），
以及一条新的（**权限先于所有页面**）—— 见 [B6 那一段的「为什么按这个顺序」](#为什么按这个顺序-1)。

---

## B0 · 基线与准备

```bash
git switch -c phase-b
python manage.py test          # 应该是 27 个，全绿
python manage.py check         # 应该 0 issues
```

基线数字：27 个测试。 B13 验收时对比，只增不减。

### 装 ruff（`goal.md` D16 第三层）

`ruff` 是开发依赖，不进生产。新建 `requirements-dev.txt`：

```
-r requirements.txt
ruff==<装的时候的版本>
```

```bash
pip install ruff && pip freeze | grep -i ruff >> requirements-dev.txt
```

新建 `pyproject.toml`（项目现在没有这个文件）：

```toml
[tool.ruff]
target-version = "py314"
exclude = [".venv", "*/migrations/*"]

[tool.ruff.lint]
# DTZ = flake8-datetimez，就是为「时区错一天」这类问题存在的规则组。
# DTZ011 禁 date.today()、DTZ005 禁裸 datetime.now()。见 goal.md D16。
select = ["E", "F", "DTZ"]
```

迁移文件排除在外：`makemigrations` 生成的代码不归我们管。

**验证**：`ruff check .` 应该干净（现有代码里没有 `date.today()`）。

### ⚠️ 实测发现：开发库里业务表全是空的

`Contact` / `Relationship` / `RelationshipType` 都是 **0 行**。这改变两处做法：

1. `RelationshipType.code` 不需要 `goal.md` 写的三步迁移。
   三步法（加可空 → 数据迁移回填 → 改 unique/non-null）是**表里有数据时**的必要手续；
   0 行时一步加 `SlugField(unique=True)` 就行。
   **但 `goal.md` 里那条三步规则不要删** —— 它对以后任何"给有数据的表加唯一字段"仍然成立，
   只是这一次的前置条件不满足。B2 里会写清楚这个简化和它的适用条件。
2. 本阶段全程不需要写数据迁移。 所有新约束都加在空表上，不存在"先清洗存量数据"的问题 ——
   这正是 A7 说的"现在加是免费的"。

> 顺带记一笔：`Language` 有 7923 行，所以每次跑测试都会重灌一遍。
> **日常用 `python manage.py test --keepdb`**（README 里已写）。

---

## B1 · `core`：时间口径、共享 `.active()`、约束错误映射、分层守卫

**为什么最先做**：后面每一张带起止日期的表都要用 `.active()`，而它的定义里有一个
会静默出错的坑（时区）。定义只留一处，且在第一个使用者出现之前就位。
**约束错误映射（D14）同理** —— B2 起每张新表都要往里登记，机制必须先就位。

### `core/constraints.py`（新建，2026-07-28 D14 重写后新增）

规则**只写在约束里**，`clean()` 不再重写一遍。剩下的只是把约束错误从
`NON_FIELD_ERRORS` 挪到正确的字段上：

```python
"""约束名 → 字段名的映射，以及把约束错误挂到字段上的 mixin。见 goal.md D14。

这张表是纯呈现元数据，不含任何业务逻辑 —— 规则改了（比如年龄 18 改 16），
只改约束一处，这里一个字都不用动。
"""

CONSTRAINT_FIELD = {
    # violation_error_code: 该错误应该显示在哪个字段上
    "name_type_mismatch":  "legal_last_name",
    "reltype_name_taken":  "name_a_to_b",
    ...
}


class ConstraintErrorFieldMixin:
    """把 validate_constraints() 抛到 NON_FIELD_ERRORS 的错误改挂到具体字段。

    Django 没有内置办法让 CheckConstraint / UniqueConstraint 的错误落到字段上，
    而 admin 里的人需要看到「是姓氏这一栏错了」。按 error.code 查 CONSTRAINT_FIELD。
    """
```

每条业务约束都要带两样（Django 4.1+ / 5.0+，本项目 5.2 都有）：

```python
violation_error_message="个人必须填姓氏，机构必须填机构名。",   # 人话
violation_error_code="name_type_mismatch",                  # 编程锚点 → 映射表的键
```

⚠️ **`CheckConstraint.validate()` 遇到 `FieldError` 会静默跳过** ——
某些约束在 `full_clean()` 阶段根本不会被校验，只在真写库时由数据库拦下
（表现为 `IntegrityError`，不是友好的表单错误）。
**B2 起每加一条约束，都要在 admin 里实测一次提交违规数据看到什么**，
尤其是表达式约束（`Lower(Trim())` / `Least`/`Greatest`/`Coalesce`）。
测不通的，`clean()` 才真的写一份，并在 docstring 里注明
**"它是表单层唯一的拦截，不是提示层"**。

⚠️ **顺带要改存量代码**：Phase A 按**旧** D14（两层 + 注释纪律）写过
`contact_name_matches_type` 约束和配套的 `Contact.clean()`
（`01-roadmap.md` A7/A8 —— 那份手册已完成留档，不改，但它描述的代码要改）。
本步一并收编：约束加上 `violation_error_message` / `violation_error_code`、
往 `CONSTRAINT_FIELD` 登记、**删掉 `Contact.clean()` 里重写规则的那一段**，
只保留约束表达不了的部分（如果有的话）。
不收编的话守卫测试第一次跑就会红 —— 这正是它该做的事。

### `core/timeutils.py`（新建）

```python
"""项目里唯一允许获取「现在」的地方。见 goal.md D16。"""

from django.utils import timezone


def local_today():
    """基金会所在时区（settings.TIME_ZONE = America/Los_Angeles）的今天。

    不要用 datetime.date.today()  —— 依赖服务器本地时区，Render 上是 UTC。
    不要用 timezone.now().date()  —— 那是 UTC 日期，太平洋时间下午 5 点后就跨天了。
    """
    return timezone.localdate()
```

### `core/querysets.py`（新建）

```python
from django.db import models
from django.db.models import Q

from core.timeutils import local_today


class DateRangeQuerySet(models.QuerySet):
    """给所有「带 start_date / end_date 的表」共用的生效期判定。

    Assignment 和 Relationship 都用它 —— 定义只写一处。那个 Q 表达式会出现在
    ministry 页面、活跃统计、admin 筛选器等十来个地方，抄十遍就一定有一处抄错，
    而且错了不报错，只是数字悄悄不对。
    """

    def active(self, on=None):
        # on 必须在调用时求值。写成 def active(self, on=local_today()) 是
        # 进程启动时冻结的经典 bug，gunicorn worker 上会越跑越错。
        on = on or local_today()
        return self.filter(
            (Q(start_date__isnull=True) | Q(start_date__lte=on))
            & (Q(end_date__isnull=True) | Q(end_date__gte=on))
        )
```

**`start_date` 那一半不能漏**：只看 `end_date` 的话，一个 `start_date=2027-01-01`、
没有结束日期的岗位**今天就算在职**，而且不报错。

> ⚠️ `active()` 只管日期，`core` 这一层不认识 `status`。
> `Assignment` 在 B5 会自己加一个 `serving()`（= `active()` AND `status=active`），
> **不要把 `status` 塞进这个共享 mixin** —— `Relationship` 没有状态这回事，
> 关系不会被"停职"。见 `goal.md`「`Assignment.status`」。

用法（B3 / B5 都这么接）：

```python
objects = models.Manager.from_queryset(DateRangeQuerySet)()
```

用 `from_queryset` 而不是 `DateRangeQuerySet.as_manager()`，是为了以后还能往
manager 上加别的方法。**关联管理器会继承它**，所以 `ministry.assignments.active()` 直接可用。

再加一个 model 层的便利属性（admin 列表要显示）：

```python
@property
def is_currently_active(self):
    on = local_today()
    return ((self.start_date is None or self.start_date <= on)
            and (self.end_date is None or self.end_date >= on))
```

> ⚠️ 这个 property 和 `.active()` 是**同一条规则的两处实现**。真想彻底避免，
> 可以让 property 走 `type(self).objects.filter(pk=self.pk).active().exists()`，
> 但那是每行一次查询 —— admin 列表里就是 N+1。所以重复是主动选的。
>
> **别拿"两处都写注释指认对方"当解法** —— 那是**旧 D14** 的注释纪律，
> 第七轮已经判过刑（"要靠人每次都记得"）。落地时用的是结构：
> 两份实现**放在同一个文件里**（`core/querysets.py` 的 `DateRangeQuerySet.active()`
> 和 `DateRangeMixin.is_currently_active`），改一处时另一处就在眼皮底下，
> 外加下面那六条边界测试同时打两条路径。

### 测试（`core/tests.py`）

```python
# .active() 的四条边界 —— 这是全系统复用最多的谓词
def test_active_includes_a_row_ending_today(self)
def test_active_excludes_a_row_that_ended_yesterday(self)
def test_active_excludes_a_row_that_starts_in_the_future(self)   # 原定义漏掉的那一半
def test_active_includes_a_row_with_no_dates_at_all(self)

# 时钟可注入，且没有被冻结在导入时
def test_active_accepts_an_explicit_date(self)

# 时区：太平洋时间晚 8 点（UTC 已是次日）判定不跨天
def test_active_uses_the_foundation_timezone_not_utc(self)

# 用测试当 lint —— 同 test_no_model_changes_are_missing_a_migration 的套路
def test_nobody_computes_today_outside_core_timeutils(self)
```

**外加两条 D14 的守卫测试**（用测试当 lint，同迁移守卫 / D16 时间守卫）：

```python
def test_every_business_constraint_has_a_code_and_a_field_mapping(self):
    """遍历所有 model 的 Meta.constraints，断言每条都有 violation_error_code
    且在 CONSTRAINT_FIELD 里有映射。漏登记一条当场变红 ——
    这条测试就是 D14 原方案里「改一处必须改另一处」那条注释纪律的替代品。"""

def test_constraint_violations_surface_as_field_errors_not_integrity_errors(self):
    """每条约束提交违规数据，断言拿到的是 ValidationError 且挂在预期字段上。

    钉住 D14 那个坑：CheckConstraint.validate() 遇 FieldError 会静默跳过，
    那样的约束只会在写库时炸成 IntegrityError。这条测试让它当场暴露。"""
```


**再加一条 D18 分层的守卫测试**（2026-07-28 新增，用测试当 lint 第五次）：

```python
def test_business_logic_does_not_import_admin(self):
    """各 app 的 models.py / forms.py / services.py 里不许出现 django.contrib.admin。

    这三层是永久资产 —— Django 升级不会坏（ORM 和 django.forms 都在兼容承诺内），
    前端上来原样复用。而 admin.py 是一次性配置，前端上来直接删。
    这条测试让「表单能复用」从一句承诺变成机器检查的事实。
    见 goal.md D18「代码落点与文件分层」。

    ⚠️ views.py 不在检查范围内 —— 合并页面要用 staff_member_required（B4.4）。
    """
```

**判据的可执行版本**：这条测试 + B13 里那条"`admin.py` 搜不到
`save_model` / `save_related` / `get_queryset` 重写"，合起来就是
"**把 `admin.py` 删掉还剩全部业务逻辑**"。

**再加一条汇报链遍历的守卫测试**（2026-07-28 第九轮新增，用测试当 lint 第六次）：

```python
def test_nobody_traverses_reports_to_outside_org_services(self):
    """除 org/services.py 外，不许出现 `.reports_to` 的循环 / 递归用法。

    全项目只有 build_org_tree() 一处遍历汇报链，环的兜底和 N+1 的规避都在它里面。
    原方案是「所有遍历汇报链的代码一律带 visited 兜底」—— 那是纪律性保障，
    和 B4.2 判过刑的 Contact.objects.people() 是同一种东西。
    见 goal.md「汇报线的环」第九轮修订。

    ⚠️ 写法：找 while / for 循环体里出现 reports_to 的行，或 `.reports_to` 与
       函数自身名字同时出现的行。宁可宽一点（误报了加豁免注释），
       也别漏 —— 漏掉的症状是 Phase C 的组织架构图挂死。
       models.py 里作为字段定义的 `reports_to = FK(...)` 不算，按 `= ` 排除。
    """
```

⚠️ **这条测试 B1 时会空跑**（`org` app 还不存在），**B5 建 `Position` 时必须回来确认它真的会红** ——
B13 的清单里有这一项。

**顺带在 B1 就把 `contact/services.py` 建出来**（空文件 + 一行 docstring）。
B3.1b 的 `orient()` / `direction_choices()` 和 B4.4 的 `merge_contacts()` 都往这里放。
现在建成本为零，等到用时再建就会有人顺手写进 `models.py` 或 `Form` 里。
`org/services.py` 同理 —— B5 一 `startapp` 就建，`build_org_tree()` 是它的第一个住户。

三条 grep 守卫的写法：遍历项目下的 `*.py`（跳过 `.venv`、`*/migrations/*`
和 `core/timeutils.py` / `org/services.py` 各自），正则找 `date.today()` / `timezone.now().date()`，
按文件名过滤后找 `django.contrib.admin`，以及找循环里的 `reports_to`，
命中就 fail 并打印文件和行号。
**`ruff` 的 `DTZ` 抓不到 `timezone.now().date()`**（那是 tz-aware 的，
linter 认为合法），所以这条测试不能省。

前六条测试需要一个带 `start_date` / `end_date` 的模型。B1 时还没有 ——
**先用 `Relationship` 测**（它已经有这两个字段），B3 接上 `.active()` 之后自然成立。

**验证**：`python manage.py test core` 全绿；`ruff check .` 干净。

---

## B2 · `contact` ①：`RelationshipType` 收口

**为什么在这个位置**：`code` 必须赶在任何按类型查询的代码之前落地；
`usable_as_emergency_contact` 是 B4.2 `EmergencyContact` 表的前置。

> **2026-07-28 第三轮修订**：已确认 **`bulk_create` 会成为常态写入路径**
> （批量导入基金会现有数据）。所有"`save()` 归一化 + 唯一约束"的组合因此都是漏的 ——
> 唯一性一律改用 `Lower()` / `Trim()` / `Least()` 的**表达式约束**。
> 通则和判定方法见 `goal.md` D9「归一化通则」。本步和 B3.2、B5 都受影响。

### 三个新字段

```python
class RelationshipType(models.Model):
    # 代码只认 code，永远不认显示名。显示名可以在 admin 里随时改，
    # 而 filter(name_a_to_b="parent of") 会在改名之后静默失效。见 goal.md D5 / D6。
    # ⚠️ 字段上不写 unique=True —— 唯一性走下面的 UniqueConstraint(Lower("code"))，
    #    否则 bulk_create 能把 Food_Pantry 和 food_pantry 当两行插进来（D9 归一化通则）。
    code = models.SlugField(max_length=50)

    # 显式标记「配偶」「兄弟姐妹」这类正反同义的类型，不靠「name_b_to_a 为空」去推断 ——
    # 录入的人完全可能把 "spouse of" 同时填进正反两栏，推断就失效了。见 goal.md D15。
    is_symmetric = models.BooleanField(default=False)

    # 紧急联系人的关系标签复用本表；这个布尔把「employee of」这类外部组织归属
    # 从紧急联系人下拉里挡掉。同 Contact.preferred_language 的 limit_choices_to 写法。
    usable_as_emergency_contact = models.BooleanField(default=False)
```

### 两条真正的唯一约束（缺口 2 + `code`）

```python
class Meta:
    constraints = [
        # 规则只在这里 —— 不要在 clean() 里再写一遍（goal.md D14）。
        # Lower：普通 UniqueConstraint 挡不住 "Parent of" vs "parent of"。
        # Trim ：只靠 save() strip 的话 " parent of" 会被 bulk_create 塞进来。
        #        见 goal.md D9「归一化通则」。
        models.UniqueConstraint(
            Lower(Trim("name_a_to_b")),
            name="relationshiptype_name_a_to_b_ci_unique",
            violation_error_message="已经有一个同名的关系类型了。",
            violation_error_code="reltype_name_taken",   # → CONSTRAINT_FIELD 映射到 name_a_to_b
        ),
        # code 用 Lower() 版而不是字段上的 unique=True —— 同一条通则：
        # save() 转小写只保证「存进去的值好看」，bulk_create 能插 Food_Pantry + food_pantry。
        models.UniqueConstraint(
            Lower("code"),
            name="relationshiptype_code_ci_unique",
        ),
    ]
```

`from django.db.models.functions import Lower, Trim`。

⚠️ **`code` 字段上不要再写 `unique=True`** —— 两条唯一索引重复，一条就够。
`SlugField` 本身不带 unique，写成 `models.SlugField(max_length=50)` 即可。

### `save()` 归一化（现在只管好看，不管唯一性）

```python
def save(self, *args, **kwargs):
    # 注意：这三行都不再承担唯一性 —— 唯一性由上面 Lower()/Trim() 的约束保证。
    # 保留它们是为了「存进去的值本身是干净的」，以及 admin 里行为一致。
    self.code = self.code.strip().lower()
    self.name_a_to_b = " ".join(self.name_a_to_b.split())
    self.name_b_to_a = " ".join(self.name_b_to_a.split())
    super().save(*args, **kwargs)
```

> 这是 2026-07-28 第三轮修订的核心改动。 原来的写法是"`save()` 归一化 + `unique=True`"，
> 看上去像数据库在把关，其实**只要不经过 `save()` 就全漏**。
> 而 `bulk_create` 已确认会成为常态写入路径（批量导入基金会现有数据）。
> 判定方法见 `goal.md` D9 通则：**不经过 `save()` 直接写这两行，数据库会不会拒？**

### `clean()` 只做约束表达不了的那件事（缺口 1）

```python
def clean(self):
    """缺口 1：新类型的 name_a_to_b 撞上任何已有类型的 name_b_to_a 就报错。

    ⚠️ 这是「表单层唯一的拦截」，不是提示层 —— 它跨行查询，CheckConstraint
    表达不了，所以 bulk_create 绕得过去。这是一个已知的不完美，见 goal.md D14。

    根因：反向类型行本来就不该存在 —— "child of" 已经是 "parent of" 的
    name_b_to_a 了。类型行不存在，反向关系行就根本录不出来。
    所以防线加在类型层，不是关系层。
    """
```

**不要在这里重写唯一约束的人话版本**（2026-07-28 D14 重写后的规矩）。
那条规则只属于 `relationshiptype_name_a_to_b_ci_unique`，
人话来自它的 `violation_error_message`，挂到哪个字段来自 `CONSTRAINT_FIELD` 映射。
**`clean()` 里只写约束表达不了的东西**，这里就是缺口 1 那条跨行检查。

比较一律 strip + casefold。

### `code` 不可改

`editable=False` 只挡 ModelForm，脚本照改。两层：

```python
# admin
def get_readonly_fields(self, request, obj=None):
    return ["code"] if obj else []     # 新建时可填，编辑时只读
```

外加 `clean()` 里比对数据库中的旧值（`self.pk` 存在时查一次原值，不等就报错）。

### ⚠️ 迁移：这次可以一步到位

`goal.md` 写的是三步迁移（加可空 → 回填 → 改 unique/non-null）。
**那是表里有数据时的必要手续，而本机 `RelationshipType` 是 0 行**（B0 实测），
所以一个迁移就够：加 `SlugField(max_length=50)`（**字段上不写 `unique=True`**）
＋上面那条 `UniqueConstraint(Lower("code"))`。

```bash
python manage.py makemigrations contact
```

`makemigrations` 仍会为不可空字段索要一个 one-off default（它看的是 schema 不是数据）。
给 `""` 即可 —— 0 行时不会应用到任何行；生成后**把迁移文件里那个 `default=""` 删掉**、
`preserve_default=False`，免得它留在文件里误导以后的人。

> 这条简化只在"表是空的"这个前提下成立。 以后给任何有数据的表加唯一字段，
> 回去照 `goal.md` 的三步走。

### admin

`RelationshipTypeAdmin` 的 `list_display` 加 `code` / `is_symmetric` /
`usable_as_emergency_contact`，`list_filter` 加后两个，`search_fields` 加 `code`。

### 测试（`contact/tests.py`）

```python
def test_relationship_type_code_must_be_unique(self)
def test_relationship_type_code_is_lowercased_on_save(self)
def test_relationship_type_code_is_read_only_once_created(self)
def test_two_types_with_the_same_name_ignoring_case_are_rejected(self)   # 缺口 2
def test_a_type_whose_forward_name_collides_with_an_existing_reverse_name_is_rejected(self)  # 缺口 1

# —— 下面两条必须用 bulk_create，绝不能走 save() ——
# 绕过 save() 正是这些约束存在的理由；走 save() 的测试会全绿，什么也没验证。
def test_bulk_create_cannot_insert_a_code_differing_only_in_case(self)      # Food_Pantry vs food_pantry
def test_bulk_create_cannot_insert_a_name_differing_only_in_whitespace(self)  # " parent of" vs "parent of"
```

**验证**：`test` 全绿；`makemigrations --check` 报 "No changes detected"。

---

## B3 · `contact` ②：`Relationship` 收口

**内部顺序不能反**：先双向显示，再归一化，最后删 `is_active`。

### B3.1 双向显示（必须最先）

现在 `ContactAdmin` 只挂了一个 `fk_name="contact_a"` 的 inline（`contact/admin.py:12`），
`Relationship.__str__` 也只用 `name_a_to_b`。结果是：录了「王强 parent of 小明」之后，
**小明的页面上看不到王强**。设计省下的那行数据已经省了，另一头的显示还欠着。

**两个 inline，都是只读**（2026-07-28 定：录入移出 inline，见 B3.1b）：

```python
class RelationshipAsAInline(admin.TabularInline):
    model = Relationship
    fk_name = "contact_a"
    verbose_name_plural = "关系"
    extra = 0                      # 不在这里新增
    readonly_fields = [...]        # 全只读
    can_delete = True              # 删可以留在这儿，删不需要方向感

class RelationshipAsBInline(RelationshipAsAInline):
    fk_name = "contact_b"
    verbose_name_plural = "关系（对方那一侧）"
```

标签用 `name_b_to_a`；**`is_symmetric=True` 时回落到 `name_a_to_b`**
（配偶、兄弟姐妹的反向标签是空的）。

上方放一个「添加关系」按钮，链到 `/relationships/add/?subject=<当前 contact id>`。

> **只读换来的**：`extra=0` + 无表单 = **不需要往 inline 表单里塞父对象**，
> 那套 formset 管道整个不存在（B3.1b 说明为什么这很重要）。
> 顺带把原来"对称关系保存后从 inline A 跳到 inline B"那笔欠账也消解了 ——
> 用户本来就不是在这儿填的，一行显示在哪个盒子里只是排版。

### B3.1b 方向感知的录入表单 + 独立页面（2026-07-28，方案 b）

> **两个原方案都废弃了**：
> 1. 最早：靠 `help_text` 要求"总是从 A 那一方的页面录入" —— **把外键方向翻译成人工纪律**
>    （`goal.md` D18 的典型反例），且站在小明页面根本录不了"王强是我爸爸"。
> 2. 同日一度定为**挂在 inline 上的方向感知表单** —— 当天推翻，见下面「为什么不挂 inline」。

**录入走独立页面 `/relationships/add/?subject=<id>`**，形状同 B4.4 的合并页：

```
contact/forms.py       RelationshipForm(subject=...)   ← 纯 django.forms，永久资产
contact/services.py    direction_choices() / orient()  ← 永久资产
contact/views.py       RelationshipCreateView          ← staff_member_required
contact/urls.py        /relationships/add/
contact/templates/contact/relationship_form.html
```

```python
class RelationshipForm(forms.ModelForm):
    """类型下拉列出正反两个方向；contact_a/contact_b 由 save() 路由，用户看不到 A/B。"""

    # 选项形如 (f"{type_id}:fwd", "小明 是 ___ 的父亲")
    #          (f"{type_id}:rev", "小明 是 ___ 的儿子")
    # is_symmetric=True 的类型只生成一条（用 name_a_to_b）。
    direction_choice = forms.ChoiceField(label="关系")
    other = forms.ModelChoiceField(queryset=..., label="对方")

    def __init__(self, *args, subject: Contact, **kwargs):
        # ⚠️ subject 是显式关键字参数,不从 request / 父对象里摸。
        #    Phase C 的视图直接 RelationshipForm(subject=contact),一个字不改。
        super().__init__(*args, **kwargs)
        self.subject = subject
        self.fields["direction_choice"].choices = direction_choices(subject)

    def save(self, commit=True):
        _, direction = self.cleaned_data["direction_choice"].split(":")
        # ⚠️ 路由本身不写在这里 —— 见下面 services.orient()
        self.instance.contact_a, self.instance.contact_b = orient(
            subject=self.subject,
            other=self.cleaned_data["other"],
            subject_is_a=(direction == "fwd"),
        )
        ...
```

#### 为什么不挂 inline（这一条是这轮最值钱的判断）

三条理由，按分量排：

1. Phase C 用 HTMX 写这个功能，根本不会用 Django formset。 那时的写法就是
   "一个 subject + 一个表单片段，POST 回来插一行" —— **正好就是这个独立页面的形状**。
   挂 inline 等于 Phase B 写一套 formset 管道扔掉、Phase C 再把独立页面写一遍。
   同一件事写两遍，正是这一整轮要消除的东西。
2. **inline 表单默认拿不到父对象**，而 `subject` 是这个表单的**全部前提**。
   要拿到得覆盖 `InlineModelAdmin.get_formset()` 或自定义
   `BaseInlineFormSet._construct_form` —— **那是全项目最深的一处 admin 管道**，
   而它买到的东西前端上来一点都留不住。
   > 上一版这里写的 `self.instance_owner` **是个不存在的属性**，
   > 正是因为"从 inline 里拿父对象"这件事没有干净写法。留这句话在这儿当提醒。
3. 形状触发本来就指向它。 `goal.md` D18 第二条出栏触发（需要跨请求状态 /
   需要动 admin 管道）已经把合并页赶出去了，关系录入是同一个形状 ——
   两处用同一个模式，比一处 inline 一处页面好维护。

**代价（如实记）**：多一次跳页。可接受 —— 那一跳 Phase C 也要有（HTMX 里是弹一个片段），
而且录关系不是高频操作。

> **判据一句话（新增，记进 `goal.md` D18）：这段代码买到的东西，前端上来还留得住吗？
> 留不住就别买。** 两个方案代码量差不多，差别全在残值。

**方向路由和选项生成都放 `contact/services.py`，`Form` 只调用**（2026-07-28 D18 分层）：

```python
# contact/services.py
def direction_choices(subject) -> list[tuple[str, str]]:
    """(f"{type_id}:fwd", "小明 是 ___ 的父亲") … is_symmetric 的类型只出一条。"""

def orient(*, subject, other, subject_is_a: bool) -> tuple[Contact, Contact]:
    """返回 (contact_a, contact_b)。表单和以后的视图都调它。"""
```

**为什么必须抽出来**：`goal.md` D18 的落点表把「关系方向路由」明确划给 `services.py`。
写在 `Form.save()` 里字面上不违规（`Form` 不是 `ModelAdmin` 钩子），
但 Phase C 若把这个页面改成"此人的所有关系"合并视图（形状变了、表单复用不了），
路由就得抄一遍。抽成函数之后，抄不抄表单都无所谓。
这和 B4.3b 的"拦截逻辑放 model / services，`Form` 只调用"是同一条规矩。

⚠️ **对称类型的 id 排序不要写在这里，也不要写进 `orient()`** —— 它留在
`Relationship.save()`（B3.2）。规范化只有一处，理由见 `goal.md` D9 归一化通则
（导入路径根本不经过表单，也不经过 `orient()`）。

⚠️ **`contact/forms.py` 不许 import `django.contrib.admin`** —— B1 有守卫测试盯着。
这条表单是永久资产（`django.forms`，前端上来原样复用），不是给 admin 写的一次性代码。

### B3.2 无序对唯一约束（强制层）+ 对称关系归一化（显示层）

**两件事，先做约束再做归一化** —— 约束是正确性，归一化只是好看。

**① 替换掉 A7 那条唯一约束**（是替换，不是并存 —— 新的严格更强，旧的每种情形它都覆盖）：

```python
from datetime import date
from django.db.models import Value
from django.db.models.functions import Coalesce, Greatest, Least

models.UniqueConstraint(
    Least("contact_a", "contact_b"),
    Greatest("contact_a", "contact_b"),
    "relationship_type",
    Coalesce("start_date", Value(date.min)),
    name="relationship_unique_unordered_pair",
    violation_error_message="这两个人之间已经有一条同类型的关系了。",
    violation_error_code="relationship_pair_taken",   # → CONSTRAINT_FIELD 映射到 contact_b
)
```

不带条件，对所有类型一律生效。 缺口 1 修好之后（反向类型行根本不该存在），
同一对人 + 同一类型出现两个方向对**任何**类型都是错的：`spouse` 本就只该一行；
`(小明, 王强, parent of)` 意思是小明是王强的父亲，同一对人不可能双向成立。

**用 `Coalesce` 而不是 `nulls_distinct=False`**：表达式 `UniqueConstraint` 与 `nulls_distinct`
能否共存尚未实测（`Assignment` 那条正因此放弃了 `Lower("title")`）。
`Coalesce("start_date", Value(date.min))` 语义等价 —— 两行都为空时仍算重复 ——
且不依赖那个不确定的组合。这是主方案，不是退路。

⚠️ **实施时先跑一次确认表达式约束真的建出来了**（B13 的 `\d` 那一组会验），
Django 生成的是 `CREATE UNIQUE INDEX ... ON (LEAST(...), GREATEST(...), ...)`。

② `save()` 归一化（只对对称类型，只管显示）：

```python
def save(self, *args, **kwargs):
    # 对称类型（配偶、兄弟姐妹）一律把 id 小的放 contact_a，让存储和读法有个确定方向。
    # ⚠️ 这一步不承担正确性 —— 重复由 relationship_unique_unordered_pair 拒绝。
    # ⚠️ 非对称类型绝不能交换：方向带语义，换了意思就反了。
    if self.relationship_type_id and self.relationship_type.is_symmetric:
        if self.contact_a_id and self.contact_b_id and self.contact_a_id > self.contact_b_id:
            self.contact_a_id, self.contact_b_id = self.contact_b_id, self.contact_a_id
    super().save(*args, **kwargs)
```

> **为什么必须排在 B3.1 之后**：归一化会交换 a/b。用户在王强页面录了"配偶：李梅"，
> 保存后这条关系跑到李梅那一侧，而王强页面（只有 `fk_name="contact_a"` 的 inline）
> 反而看不见了。双向显示先做好，交换就无所谓了。
>
> **理由降级了但没消失**：从"数据会查不到"变成"用户会看不见自己刚录的东西"。
> 后者一样不可接受，所以顺序照旧不能反。
>
> **原来记在这里的那笔欠账已经消解**（2026-07-28，方案 b）：
> "两个 inline 下对称关系会从 inline A 跳到 inline B、用户困惑'我明明填在上面'" ——
> **录入移出 inline 之后，两个 inline 都是只读的**，用户本来就不是在那儿填的，
> 一行显示在哪个盒子里只是排版问题。见 B3.1b。

### B3.3 删掉 `is_active`，接上 `.active()`

```python
objects = models.Manager.from_queryset(DateRangeQuerySet)()
```

删字段 + 改 `contact/admin.py:85-86` 两行（`list_display` / `list_filter`）。
全项目对 `Relationship.is_active` 的引用只有这两处，**零业务逻辑**（已 grep 确认）。

`list_filter` 里换成一个基于日期的 `SimpleListFilter`（"生效中 / 已结束"），
translate 成 `.active()` / `.exclude(pk__in=active)`。

### 测试

```python
def test_the_reverse_label_is_shown_on_the_other_contact(self)
def test_a_symmetric_type_falls_back_to_the_forward_label(self)
def test_a_symmetric_relationship_is_normalised_to_lowest_id_first(self)
def test_relationship_active_uses_the_shared_queryset(self)

# B3.1b —— 方向感知表单：直接构造表单，不经过任何界面。
# 能这样测本身就证明了它前端上来可以复用。
def test_choosing_the_reverse_reading_puts_the_other_party_in_contact_a(self)
def test_choosing_the_forward_reading_puts_the_subject_in_contact_a(self)
def test_a_symmetric_type_appears_only_once_in_the_direction_choices(self)
def test_the_relationship_page_requires_a_staff_login(self)
def test_the_relationship_page_404s_without_a_valid_subject(self)

# —— 强制层：全部用 bulk_create，绝不能走 save() ——
def test_bulk_create_cannot_insert_a_mirrored_symmetric_pair(self)    # (王强,李梅) + (李梅,王强) spouse
def test_bulk_create_cannot_insert_a_mirrored_asymmetric_pair(self)   # parent of 也一样被拒 —— 约束不带条件
def test_the_same_pair_and_type_can_repeat_with_different_start_dates(self)
def test_the_same_pair_and_type_with_both_start_dates_null_is_rejected(self)  # Coalesce ≡ nulls_distinct=False
```

> **中间三条如果写成走 `save()` 的版本，会全绿而且什么也没验证** ——
> 绕过 `save()` 正是这条约束存在的全部理由。

外加 B1 那六条 `.active()` 边界测试现在正式挂在 `Relationship` 上。

**验证**：`test` 全绿；肉眼验一次 —— 从小明页面点「添加关系」→
选「小明 是 ___ 的儿子」+ 王强 → 小明页面看到「child of 王强」、
王强页面看到「parent of 小明」。两侧都能录，且方向不会反。

---

## B4 · `contact` ③：`Contact` 收口

本阶段最大的一步，四件事互相独立，可以分四个 commit。

### B4.1 `__str__` 消歧（必须赶在 B5/B6 的 autocomplete 之前）

现在两个都叫"王强"的人 `__str__` 返回**完全一样的字符串** —— 下拉框里两个一模一样的选项，
选错了不会报错，是**静默的数据错误**。

```python
def __str__(self):
    base = (self.organization_name if self.contact_type == self.ContactType.ORGANIZATION
            else (self.preferred_name or f"{self.legal_first_name} {self.legal_last_name}".strip()))
    if self.email:
        return f"{base} ({self.email})"
    if self.phone:
        return f"{base} ({self.phone})"
    return f"{base} #{self.pk}" if self.pk else base
```

机构侧同理，不要只改个人那一支。
**代价**：邮箱/电话会出现在下拉和日志里。小基金会可接受，但要知道。

**不要用唯一约束禁止重名** —— 重名是合法现实，这个领域没有可靠的自然键
（email 不能设 unique，一家人共用邮箱很常见）。

同时加：`phone` 字段 `db_index=True`（B4.3 的查重要按它查）。

### B4.2 `EmergencyContact` 专用表

> 2026-07-28 第六轮修订，本步整段重写。 原方案是在 `Contact` 上加三个字段
> （`emergency_contact` 自引用 FK / `emergency_contact_relationship` / `is_reference_only`），
> 并配一整套 `people()` 过滤纪律。全部作废，一个字段都不要加。
>
> 理由（`goal.md` D15「载体的第四条判据」）：紧急联系人可能是邻居、室友，
> **不是与基金会交互的主体，不该占一行 `Contact`**。留在 `Contact` 里的话，
> 任何一处 `Contact.objects.filter(...)` 忘了排除就是把志愿者通讯发给几百个第三方 ——
> **那是隐私事故，不是数字不准**。而靠"所有人每次都记得调用 `people()`"来防，
> 是纪律性保障，弱于结构性保障。

```python
class EmergencyContact(TimeStampedModel):
    """某个联系人的紧急联系人。姓名电话存文本，刻意不指向 Contact。

    ⚠️ 不要"顺手"把 name/phone 优化成 FK → Contact。那会把幽灵记录请回
    Contact 表，正是本次修订要根除的东西。重复存储是已知且主动接受的代价，
    见 goal.md D15「为什么最终选了文本方案」。
    """
    person = models.ForeignKey(
        Contact, on_delete=models.CASCADE, related_name="emergency_contacts",
    )
    name  = models.CharField(max_length=200)          # 必填
    phone = PhoneNumberField()                        # 必填，E.164（D7）
    relationship_type = models.ForeignKey(
        RelationshipType, on_delete=models.PROTECT, related_name="+",
        limit_choices_to={"usable_as_emergency_contact": True},
    )                                                 # 非空 —— 关系必填
```

**三个字段全部必填**：没有电话的紧急联系人没有意义；没有关系的说不清是谁。

**`person` 用 `CASCADE`**：紧急联系人是附属数据，没有独立生命周期，
档案删了它就该跟着走。（`relationship_type` 是字典表，照例 `PROTECT`。）

**「关系必填」现在只是一个 `null=False`** —— 原方案要写一条
`contact_emergency_contact_has_a_relationship` 的 `CheckConstraint`。
**这是拆表白捡的简化**，别再去写那条约束。

一条约束：

```python
models.UniqueConstraint(
    Lower(Trim("name")), "phone", "person",
    name="emergencycontact_unique_per_person",
)
```

防的是"同一个人身上把同一个紧急联系人录两遍"。
**归一化写进表达式，不靠 `save()`** —— D9 归一化通则（`bulk_create` 绕得过 `save()`）。

**不加任何"每人最多一个"的限制** —— 表天然支持多个，基金会目前只需要一个，
但这是数据自然形状，不是要强制的规则。原方案"每人 ≤1"是自引用 FK 的**硬限制**，
不是需求。

**方向约定**（不写死一定会录反）：`relationship_type` 一律读作
**「紧急联系人 是 本人 的 ___」**，即 `name_a_to_b`，a = 紧急联系人、b = 本人。
小明名下那一行填 `name=王秀英` + `parent of` = "王秀英是小明的母亲"。
这句话要原样写进 admin 的 `help_text`。

**admin**：`EmergencyContactInline`（`TabularInline`）挂在 `ContactAdmin` 上，`extra=0`。

> 不做查重、不做关联、不做预选。 原方案那五大段（自动建 reference-only、
> 命中唯一时预选、命中多条时提示、安全阀、同名同号父子的残留风险）**整体消失** ——
> 没有身份要认，就没有认错的可能。这是文本方案唯一比 FK 版简单的地方，享受它。
> `find_exact_duplicates()` 仍然要写，但那是给 `Contact` 本身用的（B4.3），
> 和紧急联系人无关。

### B4.3 录入与查重

**判定函数只写一处**，表单提示、admin 筛选器、批量命令三处共用：

```python
@classmethod
def find_exact_duplicates(cls, *, last_name, first_name, phone, exclude_pk=None):
    """归一化姓名一致 AND 归一化电话一致 —— 只有这一条规则。

    先按 phone 精确过滤（E.164，已归一化，且 B4.1 加了索引），
    再在 Python 里比归一化姓名 —— 过滤后只剩几行，不需要给姓名建冗余列。
    """
```

不要用电话相似度。 号码存的是 E.164：`+14085550102` 和 `+14085550103`
字符相似度 92%，却是完全不同的两个人 —— 号码没有"接近"这个语义。
而真正需要吸收的格式差异（`(408) 555-0102`）`phonenumber_field` 入库时已经归一化掉了。

漏掉的两种情况恰好都该漏掉：**同号不同名**（一家人共用号码）、
**同名不同号**（重名的另一个人）。

> 这条规则顺带保证**提示不披露用户尚未输入的信息** —— 必须已经同时知道姓名和号码
> 才可能命中。所以**不做按姓名的 autocomplete 下拉**（那是唯一会泄露"系统里有个同名的人"
> 的路径），改成两个字段都填完后再检查。提示里**只显示姓名**。

> ⚠️ 这个判定函数只服务 `Contact` 本身的查重（下面 B4.3b），与紧急联系人无关。
> 第六轮修订之前它还兼管"紧急联系人该关联到哪条 Contact"，
> **那一整套（自动建 reference-only、命中唯一时预选、命中多条时提示、
> 安全阀、同名同号父子会关联错的残留风险）已随专用表方案整体作废**，
> 见 B4.2 结尾。不要实现其中任何一条。

### B4.3b 联系人本身的重名：分级拦截（2026-07-28 新增）

`Contact` 的重名是**唯一还需要查重的地方**（紧急联系人已经不需要了）：

| 信号 | 频率 | 处理 |
|---|---|---|
| 仅**同名**（姓名归一化后比较） | 高 | `messages.warning`，**不阻断** |
| **同名 AND 同号**（`find_exact_duplicates()`） | 低 | ✅ **硬拦截**：`ValidationError` + `force_save` 复选框 |

> 原方案是"一律只警告不阻止"。 "重名合法"这个判断没错，但 `messages.warning`
> **是保存之后才出现的** —— 那时重复记录已经进库，操作员还得回头删。

```python
class ContactAdminForm(forms.ModelForm):
    force_save = forms.BooleanField(required=False, label="确认不是重复人员，强制保存")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # ⚠️ widget 的显隐在这里按 data 决定，不要在 clean() 里改 self.fields[...].widget
        #    再抛异常 —— 第二次提交若还有别的校验错误，复选框会退回隐藏态，
        #    用户会以为自己没勾。
        if not self._duplicate_hit_in_data():
            self.fields["force_save"].widget = forms.HiddenInput()
```

⚠️ 硬拦截只能绑同名同号，绝不能绑同名。 王强 / 李明 / 陈伟同名是常态 ——
每天弹 20 次，操作员会训练出"看到框就打勾"的条件反射，**拦截失效还多两次点击**。
这正是本节上面写过的"阻塞保存会让人学会绕过系统"。

⚠️ 按 D18，判定和拦截逻辑放 model / services 层，`Form` 只调用。
Phase C 的 HTMX 录入页要复用同一套。

### B4.4 合并重复记录

范围：最小可用。 逐字段合并界面推迟（推迟清单）。

```python
@transaction.atomic
def merge_contacts(keep, drop, *, actor=None):
    """把 drop 的所有引用改指到 keep，然后停用 drop。

    通用遍历 Contact._meta.related_objects —— 不要手写外键清单。
    手写的话 Phase C 的 Contribution 必定被漏掉，而漏掉的症状是
    捐款记录跟着废弃记录一起消失。
    """
```

四条规则：

1. **跳过 `simple_history` 生成的 `Historical*` 模型** —— 历史记录的是"当时发生了什么"，
   不该被改写。
2. **一对一冲突就拒绝**（两条都挂了 `User` 或 `VolunteerProfile`）—— 说清是哪一条挡住了，
   比自作主张删一边安全得多。
3. **唯一约束冲突就拒绝**（两条在同一活动同一角色都有 `Participation`）。
   实现从简：每次 `update()` 放进一个 savepoint，捕获 `IntegrityError` 就整体回滚并报告
   是哪个模型撞了 —— 比反射所有唯一约束简单得多，效果一样。
4. **留痕**：`Contact` 已挂 simple-history；另外在 keep 的 `notes` 里追加
   "已合并 #42（2026-08-01）"，让人肉眼也能看出来。字段合并规则从简：
   keep 的字段优先，drop 只在 keep 为空时补进来。

#### 界面：一个朴素的 Django 视图，**不做成 admin action**（2026-07-28 修订）

> **原方案**：admin action（选中两条 → 合并，带二次确认）+ admin 首页放
> "疑似重复待处理：N 条"的计数。**两样都被 `goal.md` D18 新增的形状触发命中** ——
> 二次确认页要 `extends "admin/base_site.html"`，首页计数要覆盖 `admin/index.html`
> 或自定义 `AdminSite`。那正好是全项目**最会随 Django 升级坏、且前端上来一定全丢**的那一格。

```
contact/views.py      ContactMergeView —— staff_member_required
                      GET  ?keep=<id>&drop=<id> → 并排显示两条记录 + 确认按钮
                      POST → merge_contacts(keep, drop) → 重定向回 keep 的 admin 页
contact/services.py   merge_contacts()  ← 上面那个函数，视图只是薄壳
contact/urls.py       新建，include 进 config/urls.py（admin 之外的第一条业务路由）
contact/templates/contact/merge_confirm.html
                      ⚠️ 放 **app 内**，不是项目根的 templates/ ——
                      settings 里 DIRS=[] 且 APP_DIRS=True，app 内的能直接被找到，
                      根目录那个要改 settings。少改一处配置。
                      模板不 extends admin 的任何东西。
```

**入口仍然在 admin**（那是纯呈现，按 D18 本来就该在 admin）：
`Contact` changelist 加一个「疑似重复（同名同号）」`SimpleListFilter`，
每行给一个链接跳到 `/contacts/merge/?keep=…&drop=…`。
再加一个只列清单的 management command。"待处理 N 条"就显示在合并页面顶部，不碰 admin 首页。

**为什么这样反而更便宜**：不用继承 admin 模板、不受升级影响、前端上来只换模板
（视图和 `merge_contacts()` 照旧）、削减 Phase B 范围时一个文件直接不写。

> 连带的好处：这是本项目第一个自己写的页面。 正好在模型已经稳定、
> 逻辑已经写好（`merge_contacts()`）、风险最低的一件事上，
> 把「视图 + 模板 + URL + staff-only 权限」这条路先跑通 ——
> 免得 Phase C 第一次写页面时同时踩四种坑。见 `goal.md` Phase C 的那条注。

⚠️ **权限**：用 `django.contrib.admin.views.decorators.staff_member_required`。
这是本阶段唯一允许从 admin import 的东西，**而且只在 `views.py` 里**
（`forms.py` / `services.py` / `models.py` 的守卫测试不覆盖 `views.py`）。

### B4.5 未成年人

```python
@property
def is_minor(self):
    """True / False / None（生日未知）—— 三态，不要把未知折叠成 False。

    birth_date 是可空的，把未知当成 False 会让没填生日的未成年人
    从家长通知名单里静默消失 —— 这正是这个功能最不能出的错。
    绝不存 age 字段：会过期，且没有任何机制提醒你它过期了。
    """
```

算年龄用 `dateutil.relativedelta`，或 `date(y - 18, m, d)` 加 try/except 兜 2/29。
"今天"走 `local_today()`。

**阈值算在 QuerySet 上，不算在筛选器里**（2026-07-28 收口）：

```python
class ContactQuerySet(models.QuerySet):
    def minors(self, on=None): ...            # birth_date > on - 18 年
    def adults(self, on=None): ...
    def birth_date_unknown(self): ...         # birth_date IS NULL
```

`on=None` 参数化时钟，同 `.active()` / `.vacant()`（D16 第 2 层）。

**`list_filter = ["is_minor"]` 不能用** —— property 无法进 ORM 过滤。
写一个 `SimpleListFilter`，三个选项（未成年 / 成年 / **生日未知**），
**每个选项只调上面一个方法，筛选器自己一行日期计算都不许有**。
第三个选项不能省 —— "未知"必须看得见。

> **为什么非抽不可**：D18 的落点表点名把 `is_minor` 划给 QuerySet 方法，而且
> 本项目另外三个筛选器（生效中 / 空缺 / 疑似重复）**都是在调 QuerySet 方法** ——
> 只有这个自己动手就是不一致。实质理由：
> "18 岁阈值 + 闰年 + D16 时区口径"这三样只该写一遍，
> Phase C 要"给所有未成年参与者的家长发通知"时直接 `.minors()`。
> 写在筛选器里的话，那段逻辑会跟着 `admin.py` 一起被删掉，然后在前端重写一遍。

> **不要试图用 ORM annotation 省掉这个 `SimpleListFilter`**（2026-07-28 评审提过，已核实否决）：
> `list_filter` 通过 `get_fields_from_path` 在**模型**上解析字段名，annotation 不是模型字段，
> 会在 system check 阶段报 `admin.E116` —— **与 Django 版本无关**，省不掉。
> 而且"annotation 就不用处理闰年"是假的：阈值仍然要在 Python 里用 `relativedelta` 算。
> **可选的加法**：annotation 能让 `list_display` 这一列可排序（配 `admin_order_field`），
> 想做可以加，但阈值必须在 `get_queryset()` 里按请求求值 ——
> 写成模块级常量就是 D16 那个"进程启动冻结时钟"的 bug 换了个地方。

### 测试

```python
# B4.1
def test_two_contacts_with_the_same_name_stringify_differently(self)
# B4.2 —— EmergencyContact 专用表
def test_an_emergency_contact_without_a_relationship_type_is_rejected(self)   # FK 非空
def test_duplicate_emergency_contact_for_the_same_person_is_rejected(self)    # 唯一约束
def test_bulk_create_cannot_insert_an_emergency_contact_differing_only_in_name_whitespace(self)
def test_one_person_can_have_two_different_emergency_contacts(self)           # 没有人为的基数限制
def test_deleting_a_contact_deletes_their_emergency_contacts(self)            # CASCADE
def test_contact_has_no_is_reference_only_field_and_no_emergency_contact_fk(self)
#   ↑ 钉住第六轮的结果：Contact 里不许有幽灵记录，也不许有那个自引用 FK
# B4.3b —— Contact 本身的查重
def test_same_name_same_phone_is_a_match(self)
def test_same_name_different_phone_is_not_a_match(self)
def test_same_phone_different_name_is_not_a_match(self)
def test_same_name_same_phone_blocks_saving_until_force_save(self)            # 硬拦截
def test_same_name_different_phone_only_warns(self)                           # 不绑错信号
# B4.4
def test_merge_moves_every_reverse_relation(self)                      # 见下
def test_merge_refuses_when_both_contacts_have_a_user(self)
def test_merge_refuses_on_a_unique_constraint_clash(self)
def test_the_merge_page_requires_a_staff_login(self)                   # 第一个自己写的视图
def test_a_get_on_the_merge_page_does_not_change_anything(self)        # 确认页不许有副作用
# B4.5
def test_is_minor_returns_none_when_the_birth_date_is_unknown(self)
def test_is_minor_on_the_eighteenth_birthday(self)
def test_minors_adults_and_unknown_partition_the_whole_table(self)   # 三者不重叠、并集是全表
def test_minors_accepts_an_explicit_date(self)                       # 时钟可注入
```

> **`test_merge_moves_every_reverse_relation` 的写法**（比 `goal.md` 里写的
> "测试里新造一张表"更好）：遍历 `Contact._meta.related_objects`，
> **断言每一项要么被搬走了、要么在显式的跳过名单里**（跳过名单只有 `Historical*`）。
> 这样以后任何人给 Contact 加了新外键却没决定合并时怎么处理，这条测试会当场变红。

**验证**：`test` 全绿；肉眼验 —— 给一个志愿者在 inline 里填一个紧急联系人
（姓名 + 电话 + 关系，三样都必填），保存后**确认 `Contact` 列表里没有多出任何记录**。

---

## B5 · `org`：`Ministry` + `EmploymentType` + `Position` + `Assignment`

> ⚠️ 本步在 2026-07-28 二次修订后重写。 `Position`（编制）是新拆出来的表，
> `Assignment` 身上原本的 `kind` / `title` / `ministry` / `is_leader` / `reports_to`
> **全部搬到了 `Position`**。理由见 `goal.md` D11「第二次修订」——
> 一句话：**自引用到任职行，就没有任何一行代表"空缺的编制"**。
> 如果你手上有旧版的 `Assignment` 骨架，整段丢掉重写，别改。

```bash
python manage.py startapp org
```

`INSTALLED_APPS` 里放在 `contact` 之后（依赖方向：`org` → `contact` → `core`）。

**app 内建表顺序**：`Ministry` / `EmploymentType` → `Position` → `Assignment`。
`Assignment` 只有 `position` 一个业务外键，没有 `Position` 它就是空壳。

### `Ministry`

`code`（唯一·不可改）/ `name` / `description` / `is_active` / `founded_on`（可空）。
**不挂 simple-history**（已确认：改动频率极低）。

行政职能（财务、行政）也是这张表里的行，**不另建 `Department`**。

⚠️ **绝不做成 `contact_type=organization` 的 Contact 行** —— `Contact` 装的是人和
**外部**组织，ministry 是**内部**组织单元。混进去会污染联系人列表、糊掉内外边界。

### `EmploymentType`

字典表：`code` / `name` / `is_active`。
**取值基金会还没答复**（全职/兼职/合同/实习只是猜的），所以做字典表而不是 `TextChoices` ——
以后 admin 里加一行就行。符合 D5 判定规则：目前没有任何代码按它分支。

### `Position`（编制 —— 组织架构的骨架，与人无关）

```python
class Position(TimeStampedModel):
    """一个编制 = 组织结构里的一个格子。没人在任时它照样存在（空缺）。

    汇报线挂在这里，不挂在 Assignment 上 —— 换人时下属一行都不用改。见 goal.md D11。
    ⚠️ 不要自己递归 reports_to —— 走 org.services.build_org_tree()。
       跨行环路数据库拦不住，环的兜底和 N+1 的规避都在那个函数里，
       全项目只有它一处遍历汇报链（core/tests.py 有 grep 守卫盯着）。
    """
    code       = SlugField()                           # 代码只认它，不认 name
                                                       # ⚠️ 不写 unique=True —— 唯一性走下面的
                                                       #    UniqueConstraint(Lower("code"))
    name       = CharField()                           # "项目总监"，save() 归一化空白
    kind       = CharField(choices=Kind)               # employee / volunteer / board
    ministry   = FK(Ministry, PROTECT, null=True, blank=True, related_name="positions")
    reports_to = FK("self", PROTECT, null=True, blank=True, related_name="direct_reports")
    is_leader  = BooleanField(default=False)           # 给代码查；name 是给人看的
    is_active  = BooleanField(default=True)            # 编制还设不设 ≠ 有没有人在任
    description = TextField(blank=True)

    history = HistoricalRecords()                      # 组织架构变更必须留痕
    objects = Manager.from_queryset(PositionQuerySet)()
```

**四个字段为什么在这里而不在 `Assignment`**：空缺编制必须说得出自己是有薪岗还是志愿岗
（`kind`）、属于哪个 ministry、是不是个 leader 位、下属是谁 —— 招人的时候正是要知道这些。
挂在 `Assignment` 上的话空缺**这些全都没有**。
连带收益：**只查 `Position` 一张表就能画出完整组织架构图**，不 join 任何任职数据。

`Position` 是编制类型，不是座位。 三个食物银行志愿者 = **一个** `Position` +
三行 `Assignment`。这是这张表能保持在几十行、不膨胀成几百行的原因。
**因此不加"一个编制同时只能有一个在职任职"的约束** —— 它既挡不住合法的多人共岗，
也挡不住合法的交接期重叠。

⚠️ `reports_to` 用 `PROTECT`，不是 `SET_NULL`。
`CASCADE` 是灾难（删一个编制带走整棵下属子树）；但 `SET_NULL` 也不行 ——
它会把一整棵子树**静默地**变成架构图的根，事后看不出出过事。
`PROTECT` 强迫你先把下属改挂到别处，是唯一会让你注意到的选项。
（旧版挂在 `Assignment` 上时选的是 `SET_NULL`，因为任职记录会被删；
编制几乎不删，撤销走 `is_active=False`，所以前提变了。）

### `Position` 的约束与查询

```python
constraints = [
    CheckConstraint(~Q(reports_to=F("id")), name="position_reports_to_is_not_self"),
    UniqueConstraint(Lower("code"), name="position_code_ci_unique"),
]
indexes = [models.Index(fields=["ministry", "kind", "is_active"])]
```

⚠️ `code` 用 `UniqueConstraint(Lower("code"))`，字段上不写 `unique=True`。
`save()` 转小写只保证"存进去的值好看"，`bulk_create` 能插 `Food_Pantry` + `food_pantry` 两行。
**`Ministry` / `EmploymentType` 以及 B6 的 `EventType` / `ParticipationRole` 一律照此办理** ——
见 `goal.md` D9「归一化通则」。

`name` **不加**唯一约束 ——
两个 ministry 各有一个"协调员"是合法的，靠 `__str__` 带上 ministry 消歧
（同 `Contact` 重名的口径：重名合法，靠显示消歧不靠约束禁止）。

**`save()` 里归一化 `name`**（strip + 连续空白压一个）。
**`code` 的不可改**照 B2 的 `RelationshipType` 同一套做法（admin `get_readonly_fields`
在 change 页只读 + `clean()` 比对数据库旧值）。

空缺查询 —— 这是拆出这张表的首要理由，必须一起落地：

```python
class PositionQuerySet(models.QuerySet):
    def vacant(self, on=None):
        """还设着、但当天没有任何在职任职的编制。"""
        on = on or local_today()                       # core.timeutils，见 D16
        return self.filter(is_active=True).exclude(
            id__in=Assignment.objects.active(on=on).values("position_id")
        )
```

三个点一个都不能省：

1. **`filter(is_active=True)`** —— 撤销的编制不是空缺，别混进来。
2. **`on=None` 参数化时钟** —— 同 `.active()`，理由见 D16 第 2 层；
   顺带白捡"某一天有哪些编制空着"。
3. **admin 里要有可见入口** —— `PositionAdmin` 加一个「空缺」`SimpleListFilter`。
   看不见的空缺等于没建这张表。

### 汇报线的环 + 唯一的一处遍历（2026-07-28 第九轮修订）

`CheckConstraint` 只挡得住深度 1。**A→B→A 是两次各自合法的插入，
数据库用 CHECK 表达不了跨行环路**，后果是任何递归走 `reports_to` 的代码挂死。

两道防线：

1. `Position.clean()` 向上走链（带 `visited` 集合、限深 20）拒绝成环。
   按 D14 这**只是提示层** —— `bulk_create` 绕得过去，是已知的不完美，不粉饰。
2. **全项目只有一处遍历汇报链**：`org/services.py` 的 `build_org_tree()`。

> **第 2 条原文是"所有遍历汇报链的代码一律带 `visited` 兜底"** —— 那是**纪律性保障**，
> 和 B4.2 判过刑的 `Contact.objects.people()`（"靠所有人每次都记得调用"）是同一种东西。
> 同一条标准这里没执行，第九轮修订补上。见 `goal.md`「汇报线的环」。

```python
# org/services.py —— D18 落点：永久资产，Phase C 的组织架构图 import 同一个函数
def build_org_tree(positions=None):
    """一次查询取全表，在内存里建树。全项目唯一一处遍历汇报链的代码。

    ⚠️ 环的兜底在这里，不在调用方 —— 调用方拿到的已经是树，
       不需要知道「数据可能有环」这回事。见 goal.md「汇报线的环」。
    ⚠️ 一次查询取全表，不要逐级 position.reports_to 往上取（那是 N+1）。
       Position 是几十行的表，全表取回在内存里建树比任何递归查询都快。
    """
    if positions is None:
        positions = list(Position.objects.select_related("ministry"))
    by_id = {p.id: p for p in positions}
    children = defaultdict(list)
    roots = []
    for p in positions:
        seen, cur = set(), p          # 沿 reports_to 上溯，撞到自己就是环
        while cur.reports_to_id and cur.id not in seen:
            seen.add(cur.id)
            cur = by_id.get(cur.reports_to_id)
            if cur is None:           # 上级不在本次查询范围内，当根处理
                break
        ...                           # 成环的那一支挂到根上 + logger.warning
    return roots
```

**三个要点，一个都不能省：**

1. `visited` 在函数里，不在调用方。 这就是它和原方案的全部区别。
2. 一次查询。 测试用 `assertNumQueries(1)` 钉住 —— 防止以后有人改回逐级取。
3. 喂进一个环不许挂死。 测试必须用 `bulk_create` 直接插环（`clean()` 绕过去），
   断言 `build_org_tree()` 正常返回并记了 warning。

> 好消息：环现在只可能出现在几十行的编制表里，而不是每次招人都新增一行的任职表里。
> 防线照做，但风险等级从"迟早会踩"降到"基本不会踩"。

> ⚠️ 不要换成 LTREE / 递归 CTE。 2026-07-28 有过一轮这个建议，未采纳 ——
> 量级不对（几十行）、LTREE 的 path 维护依赖 `save()`（违反 D9，`bulk_create` 绕得过）、
> 丢掉 `reports_to` 的 `PROTECT`、或者 FK + path 并存违反 D11。
> 完整论证和重启条件见 `goal.md`「为什么不上 Postgres 的 LTREE 扩展」+ 推迟清单。

### `Assignment`（任职 —— 谁在什么时候占了哪个编制）

```python
class AssignmentQuerySet(DateRangeQuerySet):
    def serving(self, on=None):
        """在任期内 AND 当前可服务。ministry 的「当值名单」用它。

        ⚠️ status 只能和日期做 AND，永远不能单独用 —— 单独用就退回了
        Relationship.is_active 那个「两个维度被当成二选一」的老病。
        花名册（谁属于这个团队）用 active()，请假的人仍然是成员。
        """
        return self.active(on).filter(status=Assignment.Status.ACTIVE)


class Assignment(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE    = "active",    "在岗"
        ON_LEAVE  = "on_leave",  "请假中"
        SUSPENDED = "suspended", "停职"
        # ⚠️ 绝不加 "ended" —— 结束只由 end_date 表达，加了就是记两处。

    contact         = FK(Contact, PROTECT, related_name="assignments")   # 2026-07-30 从 CASCADE 改
    position        = FK(Position, PROTECT, related_name="assignments")
    employment_type = FK(EmploymentType, PROTECT, null=True, blank=True)
    status          = CharField(choices=Status, default=Status.ACTIVE)
    start_date      = DateField(null=True, blank=True)
    end_date        = DateField(null=True, blank=True)

    history = HistoricalRecords()
    objects = Manager.from_queryset(AssignmentQuerySet)()
```

六个字段。 没有 `kind` / `title` / `ministry` / `is_leader` / `reports_to`，
它们全在 `Position` 上。

**`status` 和任期是正交的两个维度，不是 `is_active` 的马甲**（2026-07-28 新增，
基金会已确认跟踪请假 / 停职）：

- **不加 `is_active`** —— 那会让 `is_active=True` + `end_date=2020` 存得进去；
- **`status` 只描述任期「之内」的当前状态** —— 结束永远只由 `end_date` 表达；
- **请假绝不允许靠截断 `end_date` 表达** —— 那会算错任期长度、篡改真实协议日期，
  原始日期只剩在 simple-history 里。这是加这个字段的**全部理由**。

**不加"状态必须和日期一致"的约束** —— 那要在 `CheckConstraint` 里引用"今天"，
不是不可变表达式，数据库会拒绝。而且没必要：`status=on_leave` + 已过期的 `end_date`
是**惰性的**，`serving()` 先 AND 了日期，已离任的人不会被放回来。

⚠️ `position` 用 `PROTECT`。 写成 `CASCADE` 的话，删一个编制
→ **占过它的所有人的任职历史一起消失**。同 `Participation.contact` 的道理。

⚠️ **`contact` 也用 `PROTECT`**（2026-07-30 从 `CASCADE` 改，代码和迁移已落地：
`org/migrations/0003_alter_assignment_contact.py`）。
原理由"人的档案删了，任职记录没有意义"**对调到 `MinistryRole.contact` 上同样通顺**，
而那一格选的是 `PROTECT`。同一张表两个外键用互相矛盾的理由，说明其中一个是事后合理化的 ——
这条判据当初翻的是 `MinistryRole.ministry`，这一格漏掉了。
`Assignment` 挂着 simple-history、又是 R8 的唯一支撑，**只做过员工、没做过志愿者的人
原来是删得掉的**（有 `Participation` 的人本来就被挡着），删掉就静默带走全部任职历史。
停用走 `Contact.is_active`，不做软删除。

```python
constraints = [
    CheckConstraint(end_date >= start_date, name="assignment_end_date_after_start_date"),
    UniqueConstraint(
        fields=["contact", "position", "start_date"],
        name="assignment_unique_tenure",
        nulls_distinct=False,
    ),
]
indexes = [models.Index(fields=["position", "status", "end_date"])]
```

三列一次覆盖 `serving()`（编制 + 状态 + 日期）；`active()` 走最左的 `position` 也够用。

唯一约束简化了。 旧版是 `(contact, ministry, kind, title, start_date)`，
还专门论证过"为什么必须带 `title`"（否则张三在食物银行同时当两个职务时第二行被误杀）——
**拆出 `Position` 之后那整段论证作废**：两个职务本来就是两个 `Position`，天然放行。
> 记一笔：约束越加越长往往是模型没拆干净的症状。 这次就是。

**`nulls_distinct=False` 不能省**：`start_date` 可空且留空常见，
Postgres 默认 `NULL != NULL`，不加就形同虚设 —— A7 的教训。

**`employment_type` 只对 `kind=employee` 的编制有意义**，但那是跨表判断
（`employment_type` 在这张表、`kind` 在 `Position`），`CheckConstraint` 表达不了，
只能落在 `clean()` 提示层。按 D14 记一笔，不假装它是强制的。

### `__str__` 和 admin

- `Position.__str__` 带上 ministry：`项目总监（食物银行）` —— 否则 `reports_to`
  下拉里两个 ministry 的"协调员"分不出来（同 `Contact.__str__` 消歧的道理）。
- `Assignment.__str__`：`张三 — 项目总监（食物银行）`。
- `PositionAdmin`：`search_fields`（`name` / `code`）—— 否则 `Assignment.position`
  的 autocomplete 用不了；`list_filter` 含 ministry / kind / is_leader / **空缺**；
  `list_select_related = ["ministry", "reports_to"]`。
- `AssignmentAdmin`：`search_fields` 含 `contact__legal_last_name` / `position__name`；
  `list_select_related = ["contact", "position"]` 防 N+1（**两个都要**）。
- `ContactAdmin` 加一个 `Assignment` 的 inline。
- `MinistryAdmin` 加一个 `Position` 的 inline —— 建 ministry 时顺手把编制建了。

### 测试

```python
# —— Position ——
def test_position_code_must_be_unique(self)
def test_bulk_create_cannot_insert_a_position_code_differing_only_in_case(self)  # Lower("code")
def test_position_code_is_read_only_on_change(self)
def test_position_cannot_report_to_itself(self)                  # CheckConstraint
def test_a_reporting_cycle_is_rejected_by_clean(self)            # A→B→A，提示层
def test_deleting_a_position_with_reports_is_blocked(self)       # PROTECT，不是 SET_NULL
def test_deleting_a_position_with_assignments_is_blocked(self)   # 任职历史不跟着消失
def test_deleting_a_contact_with_assignments_is_blocked(self)    # 另一头同理（2026-07-30 补）
def test_a_reporting_line_can_cross_kinds(self)                  # 执行总监(employee) → 理事长(board)

# —— build_org_tree()：第九轮修订的验收点 ——
# 前两条钉住「遍历只有一处」这个结构，第三条钉住它没退化回 N+1。
def test_build_org_tree_survives_a_cycle_inserted_by_bulk_create(self)
    # clean() 那道防线绕得过去，所以遍历必须自己扛得住脏数据：
    # 不挂死、不 RecursionError、成环那一支挂到根上并记 warning。
def test_build_org_tree_nests_children_under_their_manager(self)
def test_build_org_tree_uses_a_single_query(self)                # assertNumQueries(1)

# —— 空缺（这次修订的验收点）——
def test_a_position_becomes_vacant_when_its_last_tenure_ends(self)
def test_a_vacant_position_still_reports_its_kind_ministry_and_reports(self)
def test_an_inactive_position_is_not_listed_as_vacant(self)      # 撤销 ≠ 空缺
def test_vacant_accepts_an_explicit_date(self)                   # 时钟可注入

# —— 这一条是整次修订的意义所在，其余都可以没有，它不能没有 ——
def test_replacing_a_position_holder_does_not_touch_the_reporting_lines(self)

# —— Assignment ——
def test_one_person_can_hold_two_positions_in_the_same_ministry(self)   # 一人多岗，D11 核心场景
def test_one_person_can_hold_the_same_position_in_two_separate_stints(self)  # 离开又回来
def test_two_positions_for_one_person_can_have_different_managers(self)
def test_duplicate_assignment_with_null_start_date_is_rejected(self)    # nulls_distinct
def test_assignment_end_date_cannot_precede_start_date(self)
def test_ministry_code_must_be_unique(self)

# —— status 与任期正交（这一组的意义是「永远不用截断 end_date 表达请假」）——
def test_going_on_leave_leaves_the_tenure_dates_untouched(self)
def test_a_person_on_leave_is_excluded_from_serving_but_still_in_active(self)
def test_a_stale_on_leave_status_on_an_ended_tenure_is_inert(self)   # 两个谓词都排除他
def test_assignment_status_has_no_ended_value(self)                 # 结束只由日期表达
```

**验证**：`test` 全绿；admin 里能建 ministry、在它下面建几个 `Position`、
挂上 `Assignment`、`reports_to` 和 `position` 的下拉都能搜到；
**把某个 `Position` 上的人换掉（旧的填 `end_date` + 新建一行），确认下属的汇报线一个字没动**。

---

# B6 起：按 2026-07-29 的优先级重写

> B0–B5 已完成，上面那部分原样保留。
>
> 2026-07-29 基金会给出了一套完整需求（14 条，`goal.md`[零、当前优先级](goal.md#零当前优先级2026-07-29-定)），
> 它成为唯一优先级。**原来的 B6–B9 已被下面的 B6–B13 取代**，改动清单见
> `goal.md`[七、2026-07-29 修订记录](revisions.md#七2026-07-29-修订记录了什么)。
>
> **本阶段完成的定义**：R1–R8 + P1–P6 全部跑通，扮三个角色各走一遍
> （验收清单在 B13）。

## 这半程要达成什么

一句话：ministry 的 admin 能发活动征人，志愿者能自己注册报名，活动办完能出统计。

```
R1–R8  报表：多少场活动 / 属于哪个 ministry / 多久 / 几个工种 /
             每个工种几人 / 总工时 / 分工种工时 / 本 ministry 的 employee 参与情况
P1–P6  流程：注册建 Contact / ministry admin 发活动 / 普通用户报名（未成年要同意）/
             看报名数 + 签到 + 统计 / 上一级指定 ministry admin /
             活动改时间时通知所有报名者（未成年人通知家长）
```

### 三个不能松的判断（松了就白做）

| 判断 | 出处 | 松了会怎样 |
|---|---|---|
| 工种是一张表，不是 `Participation` 上的一个字段 | `goal.md` D19 | 零报名的工种在系统里不存在，R4 静默答错，而 P2 最想看的就是"哪个工种还缺人" |
| 权限要带 ministry 作用域，Django Group 顶不上 | `goal.md` D20 | 授出 `events.add_event` = 能给任何 ministry 发活动，P2 / P4 直接不成立 |
| 权限必须先于自助页面 | `goal.md` D21 | 中间有一段时间任何登录用户能看到所有人的资料，而库里有未成年人的地址和电话 |
| 通知的收件人解析绝不进适配器 | `goal.md` D22 | 「未成年人通知家长」是本基金会特有的规则，写进 backend 就等于换一次 provider 重写一遍 |

### 明确不做的（免得中途手痒）

| 不做 | 去哪了 |
|---|---|
| `VolunteerProfile` / `BackgroundCheck`（原 B7） | 推迟清单 —— 14 条需求一条都没碰技能 / 背景审查。⚠️ **但"背景审查必须独立成 model"这条决定不撤销** |
| `Guardianship` 法定监护表 | 推迟清单 —— P3 要的是"这次活动的同意记录"，落在 `Participation` 的六个同意字段上（2026-07-29 晚补了 `consent_email` / `consent_phone` 之后是六个，不是四个） |
| 匿名（不登录）报名页 | 推迟清单 —— 需求原话是"每个普通 **account** 可以看到" |
| 等候名单 / 报名审批 | 推迟清单 —— `needed_count` 只提醒不阻止 |
| 资金 / `Contribution` | Phase D（2026-07-29 从 Phase C 后移） |
| 活动班次 `Shift` | 推迟清单 —— `EventRole` 的维度是工种不是时间，两回事 |
| React / Vue / 前后端分离 | 永远不做（D2 仍然成立的那一半）。自助页面就是 Django 模板 + 视图 |
| 邮件群发 / 简报 / 募捐信 | 推迟清单。⚠️ **和 P6 不是一回事** —— P6 是事务性通知（这场活动改时间了，通知这场活动的报名者），范围由 `Participation` 天然界定；群发没有边界 |
| 逐个收件人的送达状态 / 退信 / 重试队列 | 推迟清单 —— 要接 provider 的 webhook，是一整套东西。⚠️ **但「联系不上」那一组现在就要做**，那是本系统自己算得出来的，和送达状态是两回事（D22 ②） |
| 真的接通 Novu | B11 只写一个做 HTTP 调用的薄壳 + mock 测试。本机没有域名，发不出去也验不了 —— **接通放 Phase C** |
| CSS / 好看 | 本阶段一律不管。能点、能用、权限对，就算过 |

## 为什么按这个顺序

```
B6  events 的表：EventType / Event / EventRole / ParticipationRole / Participation
 └→ B7  org：MinistryRole + permissions.py          ← 必须在任何页面之前
     └→ B8  accounts：注册流程（P1）
         └→ B9  自助页面①：看活动 + 报名（P3）
             └→ B10 自助页面②：ministry admin 侧（发活动 / 报名名单 / 签到）（P2, P4）
                 └→ B11 活动变更通知（P6）：适配器 + resolve_recipients() + 通知页
                     └→ B12 统计：R1–R8 的查询 + 页面
                         └→ B13 seed_demo 补充 + 验收
```

> 2026-07-30 修正：本图漏了 B11。 D22 那一轮"插入 B11"插了正文没插图，
> 于是图上 B11 还是统计、B12 还是验收，和下面的小节标题差一位。

**B7 卡在所有页面前面，这是硬的**（`goal.md` D21）。
B6 和 B7 之间没有依赖，可以并行，但 B8 起的每一步都要用 `permissions.py`。

---

## B6 · `events`：五张表

```bash
python manage.py startapp events
```

> ⚠️ **和 2026-07-28 版的差别**：多了 `EventRole`，`Participation` 改挂它，
> `Event` 改了三处。照下面写，不要照记忆写。

### 五张表

| 模型 | 要点 |
|---|---|
| `EventType` | 字典表：`code`（唯一·不可改）/ `name` / `is_active`。照 `Ministry` 抄，`ImmutableCodeMixin` + `UniqueConstraint(Lower("code"))` |
| `ParticipationRole` | 字典表，同上。**必须 seed 一行 `code=general`**（"通用志愿者"）—— `Participation.event_role` 非空之后，"没有具体分工"要有地方落。<br>落点是**数据迁移**（`events/migrations/0003_seed_general_participation_role.py`），不是 `seed_demo`：它是 schema 的一条不变量，而 `seed_demo` 拒绝在 `DEBUG` 关掉时运行，只靠它的话生产库起来就没有这一行（2026-07-31 补，见「计划外（三方核对）」） |
| `Event` | 见下 |
| `EventRole` | **本步的核心新表** —— 见下 |
| `Participation` | 见下 |

### `Event`

```python
class Event(ConstraintErrorFieldMixin, TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT     = "draft",     "Draft"          # 还没发布，只有本 ministry 的人看得到
        OPEN      = "open",      "Open for signup"  # 已发布，志愿者看得到、能报名
        CONFIRMED = "confirmed", "Confirmed"      # 人齐了，不再收报名
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    name        = CharField(max_length=200)
    event_type  = FK(EventType, PROTECT)
    ministry    = FK(Ministry, PROTECT)          # ⚠️ 非空
    start_time  = DateTimeField()
    end_time    = DateTimeField()
    location    = CharField(max_length=200, blank=True)
    owner       = FK(Contact, PROTECT, related_name="events_owned")
    status      = CharField(choices=Status.choices, default=Status.DRAFT)
    description = TextField(blank=True)

    history = HistoricalRecords()                # ⚠️ 对外发布的东西，改时间地点必须留痕
```

三处 2026-07-29 的改动，每一处都有理由，别改回去：

1. **`ministry` 非空** —— R2 / R8 / P2 全部以它为轴。可空 = 一场无主、无人有权管的活动。
2. **`status` 加 `draft` / `open`** —— P3「看到**发布的** event」需要一个明确的可见性闸门。
   2026-07-29 晚更正：可见性 ≠ `status == OPEN`。 原文这一条（和 B9 那条）
   把"志愿者能看到"直接写成了 `filter(status=OPEN)`，**后果是活动一被标 `confirmed`
   （"人齐了，不再收报名"），已经报名的人就打不开它的详情页了** —— 而 P6 通知里
   那句"新时间来不了请点这里取消"的链接正好会 404，且专门发生在招满的活动上。
   **两个状态集合，各自显式列全**（不许用补集，同 B5 复盘）：

   ```python
   VISIBLE_TO_VOLUNTEERS = {OPEN, CONFIRMED, COMPLETED, CANCELLED}   # 详情页 / 我的报名 / 通知链接
   OPEN_FOR_SIGNUP       = {OPEN}                                    # 活动列表页 / 报名
   ```

   见 `goal.md`[可见性与生命周期](phase-b.md#可见性与生命周期两个谓词不是一个-status2026-07-29-晚新增)。
   `draft` 仍然只有本 ministry 有权限的人看得到，这一条没变。
3. **没有 `capacity`** —— 被 `EventRole.needed_count` 取代。"搬运要 5 个、翻译要 2 个"
   整场一个数说不出来。

```python
constraints = [
    CheckConstraint(end_time >= start_time, name="event_end_time_not_before_start_time"),
]
indexes = [
    Index(fields=["start_time"]),                  # R1
    Index(fields=["ministry", "start_time"]),      # R2
    Index(fields=["status", "start_time"]),        # P3 —— 志愿者列表页，被打得最多
]
```

`__str__` 带上日期和 ministry（同 `Position` 带 ministry 的理由：下拉里要分得清）。

### `EventRole` —— 本步最重要的一张表

```python
class EventRoleQuerySet(models.QuerySet):
    def with_signup_counts(self):
        """加两列真实 SQL 列。annotation 不是 property —— 同 PositionQuerySet.with_headcounts()。

        一次查询算完任意多行，能排序能筛选能直接进 API。property 做不到，且每行一次查询。
        """
        return self.annotate(
            registered_count=Count("participations", distinct=True,
                filter=~Q(participations__status=Participation.Status.CANCELLED)),
            attended_count=Count("participations", distinct=True,
                filter=Q(participations__status=Participation.Status.ATTENDED)),
        )

    def understaffed(self):
        """报名人数还没到 needed_count 的工种。

        ⚠️ needed_count 为空 = 不限人数 = 永远不算缺人，不是"缺无穷多人"。
        ⚠️ 这个列表必须包含零报名的工种 —— 那正是这张表存在的理由（goal.md D19）。
        """
        return self.with_signup_counts().filter(
            needed_count__isnull=False, registered_count__lt=F("needed_count")
        )


class EventRole(ConstraintErrorFieldMixin, TimeStampedModel):
    """这场活动开了哪个工种、要几个人。人没报名它也存在 —— 那是重点。

    EventRole 之于 Participation，就是 Position 之于 Assignment：
    一个「格子」，和「占格子的人」。合并成一张表的话，空着的格子就没有行来代表它，
    于是「这场活动开了几个工种」只能靠数报名反推 —— 零报名的工种静默消失。
    这正是 goal.md D11 第二次修订判过一次死刑的那个病。见 goal.md D19。
    """
    event        = FK(Event, CASCADE, related_name="roles")
    role         = FK(ParticipationRole, PROTECT, related_name="+")
    needed_count = PositiveIntegerField(null=True, blank=True,
                       help_text="Leave empty for no limit. Advisory only — signups are never blocked.")
    notes        = TextField(blank=True)

    history = HistoricalRecords()      # needed_count 是对外发布出去的承诺，见下面 Participation 那条

    objects = models.Manager.from_queryset(EventRoleQuerySet)()

    class Meta:
        constraints = [
            UniqueConstraint(fields=["event", "role"], name="eventrole_unique_per_event", ...),
            CheckConstraint(needed_count IS NULL OR needed_count > 0,
                            name="eventrole_needed_count_is_positive", ...),
        ]
```

**`needed_count` 只提醒不阻止** —— 口径同 `Contact` 重名、同原来的 `capacity`。
超员报名现实里是常事，系统的职责是提醒不是拦路。

### `Participation`

```python
class Participation(ConstraintErrorFieldMixin, TimeStampedModel):
    class Status(models.TextChoices):
        REGISTERED = "registered", "Registered"
        ATTENDED   = "attended",   "Attended"
        ABSENT     = "absent",     "No-show"
        CANCELLED  = "cancelled",  "Cancelled"

    event_role     = FK(EventRole, CASCADE, related_name="participations")
    contact        = FK(Contact, PROTECT, related_name="participations")
    status         = CharField(choices=Status.choices, default=Status.REGISTERED)

    registered_at  = DateTimeField(null=True, blank=True)
    checked_in_at  = DateTimeField(null=True, blank=True)    # P4：是否来过
    checked_out_at = DateTimeField(null=True, blank=True)
    hours          = DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # P3：未成年人的家长同意。不是 Guardianship —— 这是「这一次活动」的一条事件记录
    consent_given_by     = CharField(max_length=200, blank=True)
    consent_relationship = FK(RelationshipType, PROTECT, null=True, blank=True, related_name="+")
    consent_at           = DateTimeField(null=True, blank=True)
    consent_method       = CharField(choices=ConsentMethod.choices, blank=True)   # verbal/paper/online
    consent_email        = EmailField(blank=True)             # ⚠️ 见下
    consent_phone        = PhoneNumberField(blank=True)       # ⚠️ 见下

    history = HistoricalRecords()      # ⚠️ 见下
```

⚠️ **`consent_email` / `consent_phone` 是 2026-07-29 晚补的，而且是 P6 的硬前提**：
D22 说"未成年人通知家长"，可 `consent_given_by` **只是一个姓名**，解析不出任何地址。
`sign_up()` 里要求未成年人**至少填一个**（提示层，同同意本身那条）。

⚠️ **`history` 也是同日补的** —— `goal.md` 的模型表里每张表都表过态，
唯独 `Participation` 空着。它上面有**全系统唯一一个可以手工改写的权威值**（`hours`，
纸质补录场景），而工时将来可能连到奖励：谁把 3 小时改成 8 小时必须查得出来。
同一条口径下 `EventRole` 也挂上（`needed_count` 是对外发布出去的承诺）。

⚠️ **没有 `event` 字段，也没有 `role` 字段** —— 都在 `event_role` 里。
保留 `event` 的话 `participation.event` 和 `participation.event_role.event` 能指向两场
不同的活动，**而这是跨表条件，`CheckConstraint` 表达不了**（同 `Assignment.employment_type`）。
按 D11 那句"不是两处都能记，是只有一处能记"，删掉。查询走 `event_role__event`。

```python
constraints = [
    # 两列都非空，所以不需要 nulls_distinct=False —— 拆表把约束缩短了，
    # 这是本项目第二次（第一次是拆 Position）。
    UniqueConstraint(fields=["event_role", "contact"], name="participation_unique_per_event_role", ...),
    CheckConstraint(hours IS NULL OR hours >= 0,           name="participation_hours_not_negative", ...),
    CheckConstraint(status = 'attended' OR hours IS NULL OR hours = 0,
                    name="participation_hours_only_when_attended", ...),
    CheckConstraint(checked_out_at IS NULL OR checked_in_at IS NULL
                    OR checked_out_at >= checked_in_at,     name="participation_checkout_after_checkin", ...),
    CheckConstraint(checked_in_at IS NULL OR status <> 'absent',
                    name="participation_checked_in_is_not_absent", ...),
]
```

`hours` 必须 `null=True`：报名了还没发生 ≠ 干了 0 小时。

### 签到签退：`hours` 是权威值

```python
# events/services.py
def check_in(participation, *, at=None):
    """记 checked_in_at，并把 status 推到 attended。"""

def check_out(participation, *, at=None):
    """记 checked_out_at，并把时长【写入】hours。

    ⚠️ 写入，不是派生。之后有人手工改了 hours 就以手工值为准，不要再从时间戳重算覆盖。
       理由：有人忘记签退、有人是纸质表事后补录、有人中途离开又回来 ——
       这三种情况下时间戳都答不出工时，而 hours 答得出。
    ⚠️ 也不要把 hours 做成 property。两个字段各算各的 = Relationship.is_active + end_date
       那个病：两个答案，可以互相矛盾，没有任何机制会告诉你。
    """
```

### `on_delete`

| 外键 | 选什么 | 为什么 |
|---|---|---|
| `Event.event_type` / `.ministry` / `.owner` | `PROTECT` | `CASCADE` 会让删一个人带走整场活动 |
| `EventRole.event` | `CASCADE` | 活动没了，它开的工种没有意义 |
| `EventRole.role` | `PROTECT` | 字典表 |
| `Participation.event_role` | `CASCADE` | ⚠️ **两级级联**：删 `Event` → 删 `EventRole` → 删 `Participation`。风险和原来"删 Event 直接带走 Participation"等价，但**更不显眼** —— 所以 `delete_event` 权限不给普通 Group（B13 验收要查） |
| `Participation.contact` | `PROTECT` | `CASCADE` 会抹掉全部工时历史，那是 R6 / R7 的基础 |
| `Participation.consent_relationship` | `PROTECT` | 字典表 |

### admin

`EventAdmin` 只挂一个 inline：`EventRoleInline`（开工种）。**不挂 `ParticipationInline`** —— 原计划是两个，当场改掉了，理由见下。

> ⚠️ 原计划是"`EventAdmin` 用 inline 直接登记参与者"。改掉了 ——
> `Participation` 现在挂 `EventRole` 不挂 `Event`，做成嵌套 inline 需要第三方包
> （admin 不支持两层嵌套），正好撞上 D18 的形状触发。
> **登记参与者走 B10 那个自己写的页面**，那里本来就要做签到。
> `ParticipationAdmin` 单独一个 changelist 就够（`list_filter` 按 `event_role__event`）。

`date_hierarchy = "start_time"`；`list_display` 里放 `ministry` / `status` / 工种数。
**⚠️ 工种数走 annotation，不要写成方法** —— B0–B5 复盘那条"`list_display` 里的方法是每行调一次的"。

### 测试

```python
def test_an_event_role_with_no_signups_still_counts_as_a_role(self)   # ⭐ D19 的核心
def test_understaffed_lists_a_role_that_nobody_signed_up_for(self)    # 同上，正面版
def test_understaffed_ignores_roles_with_no_needed_count(self)        # 不限人数 ≠ 缺人
def test_the_same_role_cannot_be_opened_twice_on_one_event(self)
def test_one_person_can_take_two_roles_in_one_event(self)
def test_the_same_person_cannot_sign_up_for_one_role_twice(self)
def test_signup_counts_take_one_query_for_any_number_of_roles(self)   # assertNumQueries
def test_negative_hours_are_rejected(self)
def test_hours_on_a_non_attended_row_are_rejected(self)
def test_checkout_before_checkin_is_rejected(self)
def test_a_checked_in_participant_cannot_be_marked_absent(self)
def test_check_out_writes_hours(self)
def test_check_out_does_not_overwrite_a_manually_entered_hours(self)  # hours 是权威值
def test_event_end_time_cannot_precede_start_time(self)
def test_deleting_a_contact_with_participation_is_blocked(self)       # PROTECT
def test_total_hours_equals_the_sum_of_per_role_hours(self)           # R6 = ΣR7
def test_rows_with_null_hours_are_not_counted_as_zero(self)
```

**验证**：`test` 全绿；admin 里开一场活动、开三个工种（其中一个 `needed_count=1`）、
给两个人报同一个工种 → `understaffed()` 里这个工种消失，**零报名的那个还在**。

---

## B7 · `org`：`MinistryRole` + `permissions.py`

> ⚠️ **这一步卡在所有页面前面**（`goal.md` D21）。先写页面后加权限 =
> 中间有一段时间任何登录用户都能看到所有人的资料。

### `MinistryRole`

```python
class MinistryRole(ConstraintErrorFieldMixin, DateRangeMixin, TimeStampedModel):
    """谁在哪个 ministry 有什么权限。

    ⚠️ 不要用 Position(is_leader=True) 代替这张表。在组织里担任什么职务，和在系统里
       能操作什么，是两个问题 —— accounts/models.py 的 docstring 早就写着
       "employment and access are different questions"（goal.md D12 / D20）。
       混起来的话：授权要先造编制、撤权要动组织架构、没编制的人（外部审计、临时代管）
       无处安放。

    ⚠️ 也不要用 Django Group 代替。Group 是全局的，表达不了"食物银行的 admin"——
       授出 events.add_event 就是能给任何 ministry 发活动。
    """
    class Role(models.TextChoices):
        ADMIN       = "admin",       "Ministry admin"
        COORDINATOR = "coordinator", "Coordinator"      # 预留，本阶段只用 admin

    contact    = FK(Contact,  PROTECT, related_name="ministry_roles")
    ministry   = FK(Ministry, PROTECT, related_name="roles")     # 2026-07-29 晚从 CASCADE 改
    role       = CharField(choices=Role.choices, default=Role.ADMIN)
    start_date = DateField(null=True, blank=True)
    end_date   = DateField(null=True, blank=True)
    granted_by = FK(settings.AUTH_USER_MODEL, SET_NULL, null=True, blank=True, related_name="+")

    history = HistoricalRecords()          # 授权变更必须留痕
    objects = models.Manager.from_queryset(DateRangeQuerySet)()      # 白捡 .active()
```

三个 `on_delete`：

- `contact` → `PROTECT`：删一个人不该静默撤掉授权记录；
- `ministry` → **`PROTECT`**（2026-07-29 晚从 `CASCADE` 改）：和上一条一致。
  > **原来的理由不成立，值得记一笔**：当时写的是"食物银行的 admin 权限在食物银行
  > 不存在之后没有意义"。**把这句话原样搬到 `contact` 上也同样通顺**（人删了授权也没意义），
  > 而 `contact` 那一格选的恰恰是 `PROTECT`，理由是"**授权是要留痕的事**"。
  > 同一张表上两个外键用互相矛盾的理由，说明其中一个是事后合理化的。
  > 另外两条：`Ministry` 有 `is_active`（撤销走停用、几乎不删，同 `Position` 那条论证），
  > 而这张表**挂着 simple-history 声称"授权变更必须留痕"**，却允许删一个 ministry
  > 静默带走一批授权行 —— 自相矛盾。代价是真要删 ministry 得先把授权行填 `end_date`，
  > 一年遇不上一次，而那正是应该被迫看见的事。
- `granted_by` → **`SET_NULL`**：授权人的账号被删，**授权本身必须还在**。
  `CASCADE` 会连锁撤销一批人的权限，是灾难级。

```python
constraints = [
    UniqueConstraint(fields=["contact", "ministry", "role", "start_date"],
                     name="ministryrole_unique_grant", nulls_distinct=False, ...),
    CheckConstraint(end_date >= start_date, name="ministryrole_end_date_not_before_start_date", ...),
]
indexes = [Index(fields=["contact", "end_date"])]    # 每次权限判断都走它，全系统最热
```

### `org/permissions.py` —— 全项目唯一一处权限判断

```python
def ministry_ids_administered_by(user, on=None) -> set[int]:
    """这个人今天管着哪几个 ministry（id 集合）。所有权限判断的地基。

    ⚠️ 名字里带 ids，因为它返回的就是 id 不是对象 —— 2026-07-30 统一成 D20 里的写法
       （本文档原来叫 ministries_administered_by，两份文档对不上，而这是全项目
       调用频率最高的一个函数）。要对象的地方自己 Ministry.objects.filter(id__in=...)。

    ⚠️ 三个过滤条件一个都不能少：MinistryRole.active(on) + ministry__is_active
       + user.contact 非空。漏 active() 的症状是过期授权还生效，
       而漏权限检查的症状是【静默越权，不报错】。
    """

def can_publish_event(user, ministry) -> bool: ...
def can_manage_event(user, event) -> bool:       ...   # 改 / 开工种 / 看名单 / 签到
def can_view_registrations(user, event) -> bool: ...
def can_grant_ministry_admin(user) -> bool:      ...   # P5：查全局 Group，不查 MinistryRole
```

P5 用 Django Group，不用 `MinistryRole`。 判据（`goal.md` D20）：
这个权限句子里有没有"某个 ministry 的"这个定语？有 → `MinistryRole`；没有 → Group。
"谁能指定 ministry admin"是真·全局的，所以是一个 `foundation_admin` Group。

> ⚠️ **ministry admin 不能自己给自己发展下线** —— `can_grant_ministry_admin()`
> 只看 Group，一个字都不看 `MinistryRole`。B13 验收要专门试这一条。

**默认拒绝**：所有函数在 `user` 未登录、无 `contact`、无匹配授权时一律返回 `False`，
不要写成"没有明确禁止就允许"。

### 守卫测试（第七次「测试当 lint」）

```python
def test_only_permissions_py_queries_ministryrole(self):
    """views.py / admin.py / forms.py 里不许出现 MinistryRole.objects。

    同 build_org_tree() 的守卫（B1/B5）：权限判断散在各处 = 迟早有一处漏了 .active()。
    区别是漏遍历会挂死（看得见），漏权限检查是静默越权（看不见）——所以这条更重要。
    """
```

### 测试

```python
def test_a_ministry_admin_can_publish_for_their_own_ministry(self)
def test_a_ministry_admin_cannot_publish_for_another_ministry(self)      # ⭐ D20 的核心
def test_an_expired_grant_stops_conferring_permission(self)              # end_date 在昨天
def test_a_future_grant_does_not_confer_permission_yet(self)             # .active() 的另一半
def test_a_user_with_no_grants_is_denied_everything(self)                # 默认拒绝
def test_a_user_with_no_contact_is_denied_everything(self)               # superuser 也走这条
def test_a_grant_on_an_inactive_ministry_confers_nothing(self)
def test_ministry_admins_cannot_grant_ministry_admin(self)               # P5 只认 Group
def test_deleting_a_ministry_with_grants_is_blocked(self)                # PROTECT，不是 CASCADE
def test_deleting_the_granting_user_keeps_the_grant(self)                # SET_NULL
def test_duplicate_grant_with_no_start_date_is_rejected(self)            # nulls_distinct
```

**验证**：`test` 全绿；admin 里给一个人授食物银行的 admin，
`ministry_ids_administered_by()` 返回一个元素；填上 `end_date=昨天`，返回空集。

---

## B8 · `accounts`：注册流程（P1）

```python
# accounts/services.py
@transaction.atomic
def register_account(*, username, email, password, legal_first_name, legal_last_name, **contact_kwargs) -> User:
    """建一个登录账号，同时给它建一份 Contact。P1。

    ⚠️ 一个事务。半个账号（有 User 没 Contact）比没有账号更难查。
    ⚠️ 不要把 User.contact 改成 null=False —— superuser 是技术账号、不对应真人（D12）。
       P1 是【流程约束】，落在这个函数里，不是字段约束。
       （这是 D9「能用约束表达的就用约束」的一个反例：这条规则有合法的例外，而约束不认例外。）
    """
```

**账号形状**：`is_staff=False`、`is_superuser=False`，不加任何 Group。
志愿者不进 admin —— `/admin/` 对他们必须返回 403，不是跳登录页（D21 第 1 条）。

**注册表单**：`accounts/forms.py::RegistrationForm`，字段是账号三样 + `Contact` 的最少几样
（姓、名、email、电话、生日）。**生日要收** —— P3 的未成年人判定靠它，
而 `is_minor` 对 `birth_date=None` 返回"未知"（B4.5），未知也要走同意流程（保守侧）。

> ⚠️ `ContactForm` 的同名同号硬拦截（B4.3b）不要套在注册上。
> 那是给操作员用的（"你是不是录重了"），套到自助注册上会变成"系统说你已经存在，
> 但你又登不进去"。**注册照建，重复留给 `merge_contacts()` 事后处理** ——
> 那个函数会遍历 `related_objects`，`Participation` / `MinistryRole` 自动被覆盖。

### 测试

```python
def test_registering_creates_both_a_user_and_a_contact(self)
def test_a_failed_registration_leaves_neither(self)                  # 事务性
def test_a_new_account_is_not_staff(self)
def test_a_volunteer_account_gets_403_on_admin(self)                 # D21 第 1 条
def test_user_contact_may_still_be_null(self)                        # 别顺手改成非空
def test_registration_does_not_hard_block_on_a_duplicate_name_and_phone(self)
```

---

## B9 · 自助页面 ①：看活动 + 报名（P3）

四个页面，全部 `LoginRequiredMixin`。模板放 `events/templates/events/`。

| URL | 做什么 |
|---|---|
| `/events/` | 已发布活动列表 —— `open_for_signup()` + `start_time__gte=now`（**不是** `filter(status=OPEN)`，见下面第 1 条） |
| `/events/<pk>/` | 详情 + 按工种显示"需要 N 人 / 已报 M 人"（`with_signup_counts()`） |
| `/events/<pk>/signup/` | 选工种报名；未成年人多一段同意表单 |
| `/me/participations/` | 我的报名，含工时 |

### 三条硬要求

1. 可见性在查询层，不在模板层。 列表页 `open_for_signup()`，
   **不是**在模板里 `{% if %}` 掉草稿。模板里不显示 ≠ 数据没发出去。
   ⚠️ **两个谓词分开用**（2026-07-29 晚更正，原文只有一个）：
   **列表页 / 报名**用 `open_for_signup()`（`{OPEN}`）；
   **详情页 / `/me/participations/` / 通知里的链接**用 `visible_to_volunteers()`
   （`{OPEN, CONFIRMED, COMPLETED, CANCELLED}`）——
   否则活动一 `confirmed`，**已报名的人就打不开它了**，见 B6 那一条。
   ⚠️ 两个集合都**显式列全**，**不要**写 `exclude(status=DRAFT)` —— 用补集定义状态，
   B5 复盘那条已经踩过一次（加第六档时它会默默变成可见的）。
2. "我的"就是我的。 `/me/participations/` 一律
   `filter(contact=request.user.contact)`，别人的 id 打进来只能是 404。
3. 逻辑不进视图。 报名走 `events/services.py::sign_up(contact, event_role, consent=...)`，
   视图只负责取参数、调函数、渲染。**统计不许写在视图里**（B13 验收要 grep）。

### 未成年人的同意（P3）

```python
# events/services.py
def sign_up(*, contact, event_role, consent=None):
    """报名。未成年人（或生日未知）必须带 consent，否则拒绝。

    ⚠️ 跨表判断（年龄在 Contact 上、报名在 Participation 上），CheckConstraint 表达不了。
       按 D14 记为【提示层】—— bulk_create 绕得过去，不假装它是强制的。
    ⚠️ 生日未知也走同意流程。is_minor 是三态（B4.5），把「未知」折叠成「成年」
       会让没填生日的未成年人静默漏过 —— 这正是当初做成三态要防的那件事。
    """
```

同意表单收**六**样：同意人姓名 / 关系（`RelationshipType`，复用 `usable_as_emergency_contact`
那个过滤）/ 方式（口头·纸质·线上）/ 时间（自动填 `now`）/
**email** / **电话**（后两个**至少填一个**，`consent_email` / `consent_phone`）。

> ⚠️ 后两样 2026-07-29 晚补，原文只收四样。 少了它们，P6 那条"未成年人通知家长"
> 就只有一个**姓名**可用 —— **解析不出任何投递地址**，最需要被通知的那群人会全部落进
> `unreachable`（B11 的规则 2 原文还写着"找 `consent_given_by` 对应的联系方式"，
> 而那个东西不存在）。`sign_up()` 里一并校验，同上面那条同意规则，按 D14 记为提示层。

### 测试（直接打 URL，不看页面）

```python
def test_the_event_list_shows_only_open_events(self)
def test_a_cancelled_event_does_not_appear_in_the_list(self)      # 补集定义的坑
def test_a_signed_up_volunteer_can_still_open_a_confirmed_event(self)   # 可见性 ≠ 可报名
def test_a_draft_event_detail_page_is_404_for_volunteers(self)
def test_every_event_status_is_in_exactly_one_of_the_two_sets_or_neither(self)
    # partition 测试：五档逐一过一遍，别漏、别两边都在（同 .minors()/.adults() 那条）
def test_a_volunteer_cannot_open_another_persons_participation(self)
def test_a_minor_cannot_sign_up_without_consent(self)             # ⭐ P3
def test_a_volunteer_with_unknown_birth_date_also_needs_consent(self)  # 三态
def test_an_adult_can_sign_up_without_consent(self)
def test_signing_up_twice_for_the_same_role_is_rejected(self)
def test_signing_up_over_needed_count_is_allowed_but_flagged(self)  # 只提醒不阻止
def test_anonymous_users_are_redirected_to_login(self)
```

---

## B10 · 自助页面 ②：ministry admin 侧（P2, P4）

| URL | 权限 | 做什么 |
|---|---|---|
| `/events/new/` | `can_publish_event()` | 发活动 —— ministry 下拉**只列出他管的那几个** |
| `/events/<pk>/roles/` | `can_manage_event()` | 开工种、填 `needed_count` |
| `/events/<pk>/registrations/` | `can_view_registrations()` | 报名名单（P4 上半） |
| `/events/<pk>/attendance/` | `can_manage_event()` | 签到 / 签退 / 手工填工时（P4 下半） |
| `/events/<pk>/report/` | `can_manage_event()` | R3–R8 的那一页（2026-07-31 补进本表）。原来它只在 B12 以「统计的页面」一句话存在，没给 URL 也没给权限 —— 而验收 ② 的 `R4–R7` 和 `R8` 两条勾都是点它 |
| `/ministries/<pk>/admins/` | `can_grant_ministry_admin()` | P5：指定 ministry admin。⚠️ 视图和表单都在 `org`（`org/views.py` + `org/forms.py`），不在 `events` —— 主语是 ministry，且 `events → org` 是单向的，见「计划外（三方核对）」 |

### 三条硬要求

1. **每个视图第一件事是权限判断**，`if not can_xxx(...): raise PermissionDenied`。
   **判断本身一个字都不写在视图里** —— 只调 `org/permissions.py`（守卫测试盯着）。
2. 下拉也要过滤。 发活动页的 ministry 下拉只列
   `Ministry.objects.filter(id__in=ministry_ids_administered_by(request.user))`。
   ⚠️ **但服务端仍然要再判一次** —— 下拉是防手滑，POST 里换个 id 是防越权，两件事。
3. 签到页要显示未成年参与者和他们的紧急联系电话
   （`is_minor` + `EmergencyContact`，B4.2 + B4.5）——
   这是**现场出事时拨号**用的，够用。
   ⚠️ **别把它当成"家长通知的完整闭环"**（原文这么写过）：
   `EmergencyContact` 没有 email，而 P6 的默认后端是邮件 ——
   活动前发通知走的是 `consent_email` / `consent_phone`，紧急联系人只是回落。见 B11。

### 测试

```python
def test_publishing_for_another_ministry_returns_403(self)          # 越权，POST 侧
def test_the_ministry_dropdown_lists_only_administered_ministries(self)
def test_viewing_another_ministrys_registrations_returns_403(self)  # 越权，GET 侧
def test_a_plain_volunteer_gets_403_on_every_admin_url(self)
def test_a_ministry_admin_cannot_open_the_grant_page(self)          # P5 只认 Group
def test_checking_in_sets_status_to_attended(self)
def test_the_attendance_page_shows_minors_emergency_phone(self)
```

---

## B11 · 活动变更通知（P6）

> 需求方 2026-07-29 当日追加。 设计见 `goal.md`
> [D22](decisions/D22-event-notifications.md#d22--活动变更通知收件人解析是业务逻辑投递是可替换的适配器2026-07-29)。
> **"快速找到报名者"这半句 B10 的报名名单页已经做完了**，这一步做的是另外三件：
> 未成年人通知家长、联系不上的人要看得见、通知要留痕。

### 先建适配器（`core/notifications/`）

```
core/notifications/
  base.py        Message / DeliveryResult / NotificationBackend(Protocol) / get_backend()
  console.py     ConsoleBackend      —— 开发默认，print 出来
  locmem.py      LocmemBackend       —— 测试用，收进一个 list
  django_email.py DjangoEmailBackend —— 不依赖任何外部服务的兜底
  novu.py        NovuBackend         —— 统一通知平台
```

```python
@dataclass(frozen=True)
class Message:
    to: str          # 一个邮箱 / 一个电话号 / 一个 provider subscriber id
    channel: str     # "email" | "sms"
    subject: str
    body: str

class NotificationBackend(Protocol):
    def send(self, messages: Sequence[Message]) -> list[DeliveryResult]: ...
```

⚠️ 后端只认这三样，不认 `Contact`、不认 `Participation`、不认「未成年人」。
一旦让它知道什么是未成年人，换 provider 就要把那条规则重写一遍。
配一条 grep 守卫（第八次「测试当 lint」）：
`core/notifications/` 下面出现 `Contact` / `Participation` / `is_minor` 就变红。

```python
# settings/base.py —— 默认 console，实际值走环境变量
NOTIFICATION_BACKEND = env("NOTIFICATION_BACKEND", "core.notifications.console.ConsoleBackend")
# 测试 override 成 locmem
```

> 2026-07-31 改的是这一句，代码是对的。 原文写「`settings/prod.py` 换成
> `novu.NovuBackend`」，而 `prod.py` 只有 `from .base import *` ——
> 后端名和 Novu 的凭据一样走环境变量（同 `SECRET_KEY`，Phase A 已经拆好了配置）。
> 把类名硬写进 `prod.py` 等于换 provider 要改代码、要发一次版，
> 而那正是 D22 拆出适配器要避免的事。

> **Novu 的凭据走环境变量**（同 `SECRET_KEY`，Phase A 已经拆好了配置）。
> **别在这一步接真实的 Novu** —— 本机没有域名，发不出去也验不了。
> 先把 `NovuBackend` 写成一个只做 HTTP 调用的薄壳 + 一条 mock 测试，
> **真的接通放 Phase C**（有域名和 sender identity 之后）。

### 收件人解析（`events/services.py`）—— 这是业务逻辑，永久资产

```python
@dataclass(frozen=True)
class Recipient:
    participation: Participation
    to: str
    channel: str
    is_guardian: bool          # 界面上要标出来"这是发给家长的"

@dataclass(frozen=True)
class Unreachable:
    participation: Participation
    why: str                   # "没有邮箱也没有电话" / "未成年且没有家长联系方式"

def resolve_recipients(event) -> tuple[list[Recipient], list[Unreachable]]:
    """谁该收到通知、用什么地址。换 provider 时这个函数一个字不改。

    三条规则：
    1. 成年人 → 他自己，按 Contact.preferred_communication_method 选渠道，
       该渠道为空就回落到另一个；
       ⚠️ 字段名 2026-07-30 更正：本文档和 D22 原来写的是 preferred_contact_method，
          而 contact/models.py 里那个字段叫 preferred_communication_method ——
          照原文写会 FieldError。同 consent_given_by 那次，是"引用了一个不存在的东西"。
          顺带：它的四档里 mail 不是可投递渠道，phone 归到 sms，见实现；
    2. 未成年人 → 【家长】。依次找：这条 Participation 的 consent_email /
       consent_phone（B9 报名时收的）→ contact.emergency_contacts 的第一条（只有电话 ⇒ sms）。
       ⚠️ 15 岁的志愿者可能根本没有自己的手机，发给他等于没发；
       ⚠️ 原文写的是"consent_given_by 对应的联系方式"——【那个东西不存在】，
          consent_given_by 只是一个姓名文本。2026-07-29 晚给同意字段补了
          consent_email / consent_phone，见 B9 和 goal.md 的模型表。
          不补的话这一整条规则解析不出任何地址，未成年人会全部落进 unreachable。
    3. birth_date 为空 → 【按未成年处理】。B4.5 的三态口径，保守侧 ——
       折叠成"成年"会让没填生日的未成年人静默漏掉。

    ⚠️ 两个都不能省：unreachable 这一组必须自己算出来。通知平台答得了
       "这封信送到了吗"，答不了"这个人根本没有地址"——它连这个人存在都不知道。
    """
```

### 编排 + 留痕

```python
@transaction.atomic
def notify_event_change(event, *, reason, message, sent_by) -> EventNotification:
    """解析收件人 → 投递 → 落一条 EventNotification。

    ⚠️ recipients 和 unreachable 两个 M2M 都是【快照】，不要做成 property 事后重算 ——
       当时联系不上不等于今天联系不上，重算会把这条历史记录改成
       "当时全都通知到了"，那是假的。同 hours 是权威值那条。
    ⚠️ message 是快照。之后再改活动，这条记录说过的话不能跟着变。
    """
```

### `EventNotification`

```python
class EventNotification(ConstraintErrorFieldMixin, TimeStampedModel):
    class Reason(models.TextChoices):
        TIME_CHANGED     = "time_changed",     "Time changed"
        LOCATION_CHANGED = "location_changed", "Location changed"
        CANCELLED        = "cancelled",        "Event cancelled"
        OTHER            = "other",            "Other"

    event             = FK(Event, CASCADE, related_name="notifications")
    reason            = CharField(choices=Reason.choices)
    message           = TextField()                      # 快照
    sent_at           = DateTimeField()
    sent_by           = FK(settings.AUTH_USER_MODEL, SET_NULL, null=True, related_name="+")
    recipients        = M2M(Participation, related_name="notifications", blank=True)
    unreachable       = M2M(Participation, related_name="notifications_unreachable", blank=True)
    provider_ref      = CharField(max_length=200, blank=True)

    # ⚠️ 2026-07-29 晚：unreachable 从 PositiveIntegerField(default=0) 改成 M2M。
    #    只存一个计数的话，事后答不出"上次是哪 3 个人没通知到"—— 想知道就得重算，
    #    而重算正是上面那条注禁止的事。D22 ② 要的就是"这几个人别静默消失"。
    #    逐人的原因（Unreachable.why）只在预览页出现，不入库；要存就得上 through 表。

    # 不挂 simple-history —— 它本身就是一条不可变的事件记录，改它就是伪造

    class Meta:
        indexes = [Index(fields=["event", "-sent_at"])]   # 二次确认页要显示"上次什么时候发的"
```

`sent_by` 用 `SET_NULL`：发通知的人离职、账号删了，**这条记录必须还在**。
同 `MinistryRole.granted_by` —— **留痕类字段一律不 `CASCADE`**。

### 页面：`/events/<pk>/notify/`

权限走 `can_manage_event()`（**和签到页同一条**，不新造一个）。

- **GET** = 预览页：正文输入框（带一个按 `reason` 生成的默认文案）+
  **三组名单**：本人收（N）/ **家长代收（N，标出来）** / **联系不上（N）**
- 页面顶部显示"这场活动上次通知是 X 分钟前，通知了 N 人" ——
  **这是防重复发送的唯一缓解**（不建队列、不做幂等键，见 D22 代价 3）
- **POST** = 确认发送 → 调 `notify_event_change()`

⚠️ **默认文案里不写未成年人姓名**，只写活动信息 + "您的孩子报名的活动"
（D22 代价 2 的缓解：即使走第三方平台，泄露面也只有一个邮箱地址加一段活动公告）。
文案末尾带一句"新时间来不了请点这里取消报名"，链到 `/me/participations/`。

> ⚠️ **实现时这句话没有变成真的链接**（2026-07-30 如实记）：正文是纯文本，
> 要生成绝对 URL 得先有域名，而本机没有 —— 这正是 `NovuBackend` 只写薄壳、
> 真接通留给 Phase C 的同一个原因。现在写的是"请到「我的报名」里取消"。
> **有域名之后把它换成 `settings.SITE_URL + reverse(...)` 即可**，
> 收件人解析和留痕都不用动。记在这里，免得以后当成漏做。

> **报名照旧，`Participation` 一个字段不加**（2026-07-29 定）。
> 别顺手加 `needs_reconfirmation` —— 那是把"这个人和某次改动的关系"塞进
> "这个人怎么样了"那个字段，两个维度。见 `goal.md` D22 末尾。

### 测试

```python
def test_an_adult_is_notified_at_their_own_address(self)
def test_a_minor_is_notified_through_their_guardian(self)              # ⭐ D22 ①
def test_a_minor_with_only_consent_phone_is_notified_by_sms(self)      # 家长地址真的解析得出来
def test_a_minor_with_no_guardian_contact_lands_in_unreachable(self)
def test_a_participant_with_unknown_birth_date_is_treated_as_a_minor(self)
def test_a_participant_with_no_email_and_no_phone_lands_in_unreachable(self)   # D22 ②
def test_unreachable_rows_are_not_counted_as_recipients(self)
def test_who_was_unreachable_is_still_queryable_afterwards(self)       # M2M 而不是计数
def test_unreachable_rows_do_not_change_after_the_phone_is_filled_in(self)     # 快照
def test_the_message_snapshot_survives_editing_the_event(self)
def test_cancelled_participations_are_not_notified(self)
def test_deleting_the_sending_user_keeps_the_notification(self)        # SET_NULL
def test_notifying_another_ministrys_event_returns_403(self)
def test_resolve_recipients_makes_no_network_calls(self)               # locmem 后端
def test_the_backend_never_imports_contact_or_participation(self)      # grep 守卫
def test_the_default_message_does_not_contain_a_minors_name(self)      # PII
```

**验证**：`test` 全绿；把一场活动的时间改掉 → 打开通知页 →
**三组名单都在，"联系不上"那组里确实有 `seed_demo` 造的那个没邮箱没电话的人** →
确认发送 → 控制台打出消息 → 回到活动页看到"刚刚通知过 N 人"。

---

## B12 · 统计：R1–R8

全部落在 QuerySet 方法 / `services.py`，不落在视图。
理由：换个界面这些要跟着搬 —— 而这次"换界面"是必然会发生的（D18 的判据）。

```python
# events/services.py 或 EventQuerySet —— 一场活动的统计口径只写一遍
def event_summary(event) -> dict:
    """R3–R7 一次算完：时长 / 工种数 / 每工种人数 / 总工时 / 每工种工时。"""

def ministry_staff_participation(event):
    """R8：开设这场活动的 ministry 下面的 employee 谁参与了、分别负责什么。"""
```

### R8 的三个坑（写之前先读）

```python
on = event.start_time.date()          # ⚠️ 坑 1：活动当天，不是今天

Participation.objects.filter(
    event_role__event=event,
    contact__assignments__in=Assignment.objects.active(on=on).filter(   # ⚠️ 坑 2：active 不是 serving
        position__kind=Position.Kind.EMPLOYEE,
        position__ministry=event.ministry,
        position__is_active=True,
    ),
).select_related("contact", "event_role__role").distinct()              # ⚠️ 坑 3：distinct
```

1. 时间口径是活动当天。 用默认值（今天）查一场去年的活动，会漏掉之后离职的人，
   **而且不报错**。`.active(on=...)` 那个参数就是为这种查询准备的（D16 第 2 层）。
2. `.active()` 不是 `.serving()`。 问的是"他当时是不是这个 ministry 的员工"，
   不是"他今天能不能当值"。请假中的人参加了活动照样算。
3. `.distinct()` 不能省。 一人在同 ministry 占两个 employee 编制（一人多岗，
   D11 的核心场景）时，join 之后他会出现两遍，**人数悄悄多一个**。

### R1 / R2 的时间边界

"某段时间有多少场"的月份 / 年份边界一律走 `core/timeutils`（D16）——
用 UTC 切月份会把月末最后一天傍晚的活动算进下个月。

### 测试

见 `goal.md`「必须写的测试」新增的那一批（R4–R8 那几条），**一条都不能少**。
其中 **R8 的三条**（时间口径 / distinct / active-not-serving）是这一步的核心。

---

## B13 · `seed_demo` 补充 + 验收

### `seed_demo` 要补的（原有的 B0–B5 场景保留）

- **一个 `foundation_admin` 账号**（全局 Group）
- **两个不同 ministry 的 admin 账号** —— 用来试越权，**必须是两个**，一个试不出来
- **两个普通志愿者账号**，其中一个未成年（有生日）
- **一场 `status=open` 的活动**，开三个工种：一个报满、一个报了一半、
  **一个零报名**（验收 R4 要用）
- 一场 `status=draft` 的活动（验收"志愿者看不见"要用）
- 一场已结束的活动，参与者有签到签退和工时（验收 R6 / R7 要用）
- **一个活动当天在职、之后离职的 employee** —— 验收 R8 的时间口径要用
- **一个既没有 email 也没有电话的报名者** —— 验收 P6 的「联系不上」那一组要用。
  **这个人必须有**，否则那一组永远是空的，看上去"通过了"其实什么也没验证
- **一个未成年报名者，家长联系方式挂在 `EmergencyContact` 上**（只有电话 ⇒ 走 sms）
  —— 验收收件人解析的**第二条**回落路径
- **一个未成年报名者，带 `consent_email` / `consent_phone`** —— **第一条**路径
  （2026-07-29 晚补：这两个字段是同日才加的，见 B6 / B9）
- **一个生日为空的报名者** —— `is_minor` 三态的保守侧：按未成年处理、通知家长
- **一个 `hours` 手工填、没有签到时间戳的 `Participation`** —— 纸质补录照样算数
  （`hours` 是权威值），验收 ② 有一条勾在打它
- **一场 `status=confirmed` 的活动，且里面有报名者** —— 验收"招满之后已报名的人
  **仍然打得开**"（[可见性 ≠ 可报名](phase-b.md#可见性与生命周期两个谓词不是一个-status2026-07-29-晚新增)，同日新增）

三条安全要求不变：幂等（`get_or_create`）、非 DEBUG 拒绝运行（除非 `--force`）、只造假数据。

### 验收

**完整清单在 `goal.md`[验收](phase-b.md#验收2026-07-29-重写改成按-14-条需求逐条验收)** ——
扮三个角色各走一遍，加上分层 grep。这里只列自动化部分：

- [ ] `python manage.py test` 全绿，**测试数只增不减**
- [ ] `python manage.py check` 零警告 / `makemigrations --check --dry-run` 无变更 / `ruff check .` 干净
- [ ] **D14 映射守卫**：新加的每条约束都有 `violation_error_code` 且在 `CONSTRAINT_FIELD` 里有映射
- [ ] **权限守卫真的会红**：临时在 `events/views.py` 里写一句 `MinistryRole.objects.filter(...)`，
      跑测试确认变红，再删掉
- [ ] **约束真的在数据库里**：
      ```bash
      python manage.py dbshell
      \d events_eventrole        # eventrole_unique_per_event
      \d events_participation    # participation_unique_per_event_role，且【没有】 event_id 列
      \d org_ministryrole        # ministryrole_unique_grant 显示 UNIQUE NULLS NOT DISTINCT
      ```
- [ ] `events_participation` **没有** `event_id` 列、**没有** `role_id` 列（都在 `event_role_id` 里）
- [ ] `events_event` **没有** `capacity` 列

### 收尾

- [x] 本文档末尾的「计划外记录」填上实施时才发现的事（B12 / B10 / B13 三条）
- [x] README 里补上 `events` app 和自助页面的说明
- [ ] `goal.md` 的 Phase B 状态改成 ✅ —— **等浏览器那一遍走完再改**。
      B6–B13 的代码和自动化验收已经全绿（见下），但[验收清单](phase-b.md#验收2026-07-29-重写改成按-14-条需求逐条验收)
      的三个角色仍然要在浏览器里真的点一遍：表单排版坏了、链接指向空处，
      断言看不出来。**清单本身现在是 `AcceptanceWalkTests`**，浏览器那一遍是复核，不是唯一防线
- [ ] 「还没定的」按基金会的实际答复更新（5 个问题都还没回，都不阻塞）

#### 自动化部分的实测结果

> 日期从标题里拿掉了（2026-07-31）：它原来叫「…（2026-07-30）」，而重跑一次就要改标题，
> **改标题会打断所有指向它的链接** —— `MarkdownLinkGuardTests` 当场就红了一条
> （`goal.md` 指过来的那条）。这类"每次更新都会变"的东西不该进标题。

| 项 | 结果 |
|---|---|
| `python manage.py test` | 363 个，全绿（开工基线 192）。<br>⚠️ 这一格 2026-07-30 写的是 `353`，而同一天晚些的 middleware 那个 commit 加了测试没回来改它 —— **写死的数字是一种会过期又不报错的东西**，和文档里写代码行号同一类。留着它是因为验收清单要的是"只增不减"，那需要一个基线数 |
| `python manage.py check` | 0 issues, 0 silenced |
| `makemigrations --check --dry-run` | No changes detected |
| `ruff check .` | All checks passed |
| `events_participation` 有没有 `event_id` / `role_id` 列 | 没有 —— 都在 `event_role_id` 里 |
| `events_event` 有没有 `capacity` 列 | 没有 |
| `ministryrole_unique_grant` | `UNIQUE NULLS NOT DISTINCT (contact_id, ministry_id, role, start_date)` |
| `eventrole_unique_per_event` / `participation_unique_per_event_role` | 都在库里 |
| 12 条 grep 守卫**双向**验证 | 该红的都红、不该红的没红（脚本见下） |

守卫的双向验证是照 [B5 复盘那条](#-计划外b0b5-复盘守卫验过会红不等于该红的都红)做的：
往每个守卫管的文件里塞一段"它该抓的"和一段"它不该抓的"，两边都对才算过。
汇报链那条特意塞了四种写法（多行循环体 / `_id` 后缀 / 递归 / 推导式），
因为那正是它上次漏掉的四种。

---

## 分工与提交节奏

**B0–B4 是一串咬合的动作**（准备 → core → contact 三步收口），中间状态留在磁盘上
过夜容易忘记做到哪，建议一口气做完 B0–B3，再单独做 B4（它自己就有四个独立小块）。

> ⚠️ **B3.1b 带进本项目的第一条 URL、第一个视图、第一个模板**（`contact/urls.py`
> 要 include 进 `config/urls.py`，模板放 **app 内** `contact/templates/contact/`
> —— settings 里 `DIRS=[]` + `APP_DIRS=True`，放 app 内不用改配置）。
> **单独一个 commit**，出问题好回退。B4.4 的合并页是第二次用同一套，那时就轻车熟路了。
> 2026-07-29 补：B6 起的提交节奏。 B6 建议**两个 commit**
> （`Event` + 两张字典表一个，`EventRole` + `Participation` 一个 —— 后者是这半程的核心，
> 单独一个好回退）；B7 单独一个（表 + `permissions.py` + 守卫测试一起，
> **权限判断和它的守卫不要分两次提交**）；B9 / B10 各一个，
> 每个都带进新的 URL + 视图 + 模板，出问题好定位。

**B5 / B6 彼此独立**，可以分开做、分开提交。**但 B7 必须先于 B9 / B10**（权限先于页面）。

每个 B 步至少一个 commit；B4 建议**五个**（消歧 / `EmergencyContact` / 查重 /
**合并 + 那个页面** / 未成年人）—— 合并单独一个 commit，因为它带进来了本项目的第一条
URL、第一个视图和第一个模板，出问题时好回退。
**B5 二次修订后变大了，建议拆两个 commit**：`Ministry` + `EmploymentType` + `Position`（含
`vacant()` 和环的防线）一个，`Assignment` 一个 —— 前者是组织架构的骨架，
自己就能跑测试、自己就能在 admin 里看，不必等任职表。

**始终归你的**：B13 那一串肉眼验收、以及待答复问题的跟进。

---

## 待答复问题（都不阻塞开工，2026-07-29 更新）

| # | 问题 | 影响 | 没答复时怎么办 |
|---|---|---|---|
| 1 | 同意流程具体长什么样（口头 / 纸质 / 线上签） | `consent_method` 的取值 | 先放三档。⚠️ **P3 本身要做**，不能因为流程没定就跳过 |
| 2 | `EmploymentType` 的实际取值 | 字典表里 seed 哪几行 | 正因为不知道才做成字典表；先只 seed 两行 |
| 3 | `status` 除 `on_leave` / `suspended` 外还要哪几种 | `Assignment.Status` | 不阻塞 —— `TextChoices`，加值就是改代码 |
| 4 | `MinistryRole` 除 admin 外还要哪几档 | `MinistryRole.Role` + `permissions.py` | **先只做 `admin` 一档** —— 需求原文只要求了这一档。`coordinator` 已在枚举里占位，但没有任何代码按它分支 |
| 5 | 工时是志愿者自己填还是 admin 填 | 哪个页面上有那个按钮 | 两条路径都走 `check_out()` 那一个函数。**先做 admin 侧** —— 需求原话是"跟 event 同个 ministry 的权限的人可以统计" |
| ~~6~~ | ~~背景审查有效期多长~~ | — | 随 `BackgroundCheck` 移出本阶段，不再需要答复 |
| ~~7~~ | ~~跟不跟踪请假 / 停职~~ | ✅ **已答复：跟踪** | `Assignment.status` 已进 B5 |
| ~~8~~ | ~~未成年志愿者有没有同意书流程~~ | ✅ **需求原文已答复：有**（"如果是 minor，可能涉及 guardian consent"） | 落在 `Participation` 的**六个**同意字段上（含 2026-07-29 晚补的 `consent_email` / `consent_phone`），见 B9 |

---

## 计划外记录（实施时回来填）

`01-roadmap.md` 里最有价值的两段就是"⚠️ 计划外：迁移图会断"和
"⚠️ 计划外：admin 路径根本不经过 middleware" —— 写下来的坑比顺利完成的步骤值钱。
这一段留白，遇到就往下写：

### ⚠️ 计划外（B1）：一条约束只能说一件事，否则映射不出去

**症状**：B1 按 D14 收编 Phase A 的代码 —— 约束加 `violation_error_code`、
删掉 `Contact.clean()` 里重写规则的那段 —— 之后
`test_organization_requires_organization_name` 变红：机构漏填机构名时，
错误挂到了 **`legal_last_name`** 上。

**根因**：`contact_name_matches_type` 一条约束表达了**两条规则**
（个人要姓氏 **或** 机构要机构名），而 `CONSTRAINT_FIELD` 是
「一个 `code` → 一个字段」。两条规则的出错字段不同，一个映射写不出来。
旧 D14 的两层写法察觉不到这件事 —— `clean()` 里是两个 `if`，各挂各的字段，
一条约束配两个分支从来没人觉得别扭。

**修法**：拆成两条约束，各说一件事、各有自己的 `code` 和字段。
**外加第三条 `contact_type_is_known`** —— 原来那条 OR 形式**顺带**还管住了
`contact_type` 的取值（第三种类型也不满足它），不显式写出来，拆完就静默丢了。
`Contact` 0 行，迁移免费。

**一般化的判据（新的，记进这里）**：
> 一条约束只能说一件事。 判定方法：这条约束被违反时，
> 你能不能说出**唯一一个**该变红的字段？说不出来，就是两条规则挤在一条里，
> 拆开 —— 不是给映射表加特例。

**连带**：`goal.md` D4 那句"真要加 Household 的话，除了 `TextChoices`
还要改 D9 那条 `CheckConstraint` —— **只有这一处**"现在要读成**两处**
（`contact_type_is_known` 的白名单 + 新类型自己的姓名规则约束）。

### ⚠️ 计划外（B3.1b）：`ModelForm` 会把不在表单上的字段的约束**整条跳过**

**症状**：`RelationshipForm` 按方案 b 写完，`contact_a` / `contact_b` 刻意不放在表单上
（那正是这个设计的全部意义 —— 录入的人不该看见 A/B）。结果是：
录一条重复关系，表单**校验通过**，然后在 `save()` 时炸成 `IntegrityError` 500。

**根因**：`ModelForm._post_clean()` 会把「不在表单上的字段」放进 `exclude`，
而 `Model.validate_constraints(exclude=...)` **跳过任何提到被排除字段的约束**。
于是 `relationship_no_self_reference` 和 `relationship_unique_unordered_pair`
在表单层根本没跑过。

> 这就是 D14 那个「`CheckConstraint.validate()` 会静默跳过」的坑，
> **只不过是从另一头撞上的** —— D14 提醒的是表达式约束在 `validate()` 里出错被吞掉，
> 这里是约束压根没被调用。症状一模一样：表单绿灯，写库时 500。

**修法**：`RelationshipForm._check_constraints()` 里显式调一次
`self.instance.validate_constraints()`，把错误 `add_error()` 到表单字段上。
另外 `CONSTRAINT_FIELD` 把这两条约束映射到 `contact_b`，
而表单上没有 `contact_b` —— 直接 `add_error("contact_b", ...)` 会抛 `ValueError`
（`ModelForm._update_errors` 不认识的字段就报错，`core/constraints.py` 的
docstring 已经预警过）。所以表单里加一张 `FIELD_ALIASES`
把 `contact_a` / `contact_b` 都落到用户看得见的 `other` 上。

**一般化（新的，记进这里）**：
> **凡是「表单字段 ≠ 模型字段」的表单，都要问一句：
> 这条约束在表单层真的跑了吗？** 判定方法和 D9 那句同构 ——
> 提交一条违规数据，看到的是表单错误还是 500？
> B4.3b 的 `ContactForm`、B4.4 的合并页、以后每一个自定义表单都要过这一问。

### ⚠️ 计划外（B4.4）：捕获 `IntegrityError` 之后，手写 savepoint 回滚是不行的

**症状**：`merge_contacts()` 按 roadmap 写成「每次 `update()` 放进一个 savepoint，
捕获 `IntegrityError` 就回滚」，唯一约束冲突那条测试报的却不是 `MergeConflict`，
而是 `TransactionManagementError: An error occurred in the current transaction.`

**根因**：Postgres 一旦报错，整个事务进入 **aborted** 状态 ——
在回滚到 savepoint 之前，**任何**语句都会被拒。而
`transaction.savepoint_rollback(sid)` 自己就是一句语句，
于是它在执行自己的那一刻就先撞上了这道墙。手写 savepoint 这条路是死的。

**修法**（Django 文档的写法）：用内层 `with transaction.atomic():` 包住每次
`update()`。内层 `atomic` 本身就是一个 savepoint，而且它在异常退出时会
**顺带把连接状态恢复好**，外面才能继续捕获、继续查询。

```python
try:
    with transaction.atomic():          # 这个 atomic 就是 savepoint，不是多余的
        rows.update(**{field_name: keep})
except IntegrityError as error:
    raise MergeConflict(...) from error  # 外层 @transaction.atomic 负责整体回滚
```

**一般化**：
> 在 `atomic` 块里捕获数据库异常，必须用内层 `atomic` 包住可能出错的那一句。
> 光 `try/except` 不够 —— 它捕到了异常，但连接已经不能再用了。
> B6 的 `Participation` 批量登记、以后任何「试着写，撞了就换个说法」的代码同理。

### ⚠️ 计划外（B4.5）：`python-dateutil` 没装，也不该为这个装

roadmap 写「算年龄用 `dateutil.relativedelta`」，但项目依赖里没有它。
为一次「减 18 年」引入一个生产依赖不划算 —— 和 D8 拒绝 `languages-plus`
是同一把尺子（**包比需求大**）。改用 stdlib：

```python
try:
    return on.replace(year=on.year - AGE_OF_MAJORITY)
except ValueError:                      # 2/29，且落到的那年不是闰年
    return on.replace(year=..., day=28)
```

闰日那一支必须往**前**退到 28 号（不是进到 3/1）：
2028-02-29 减 18 年取 2010-02-28，这样 2010-03-01 出生的人今天仍算未成年 ——
他确实还差一天满 18。**这一支有专门的测试**，因为它错了不报错，只是差一天。

### ⚠️ 计划外（B1）：grep 守卫第一次跑，抓到的是它自己

三条 grep 守卫写完第一次跑，两条红了 —— 命中的是**守卫自己**：
时间守卫的注释里原样写了要禁的那两种写法；汇报链守卫的过滤行上
同时出现了 `reports_to` 和 `for`（在 `\b(for|while)\b` 这个正则里）。

**修法**：正则一律写成**转义形式**并抽成模块级常量，让文件里永远不出现
它要找的那串字面量（`date\.today\(\)` 这串文本 ≠ `date.today()`）；
一行里不许同时出现两个模式。注释里也不许把被禁的写法拼出来。

> 这不是麻烦，是守卫真的在扫全项目的证据 —— 它连自己都不放过。
> 换成"跳过 `core/tests.py`"就等于给守卫开了个后门。

### ⚠️ 计划外（B5）：往别的 app 的 admin 上挂 inline，方向是反的

roadmap 写「`ContactAdmin` 加一个 `Assignment` 的 inline」。照字面做，
就是 `contact/admin.py` 去 `import org.models` —— **依赖方向被倒过来了**
（D17 定的是 `org` → `contact` → `core`），`contact` 从此装不上除非 `org` 也在。

**修法**：装配写在**下游那个 app** 里。`org/admin.py`：

```python
admin.site.unregister(Contact)          # contact 在 INSTALLED_APPS 里排在前面，
                                        # 它的 admin.py 已经跑过了
@admin.register(Contact)
class ContactWithAssignmentsAdmin(ContactAdmin):
    inlines = [*ContactAdmin.inlines, AssignmentInline]
```

Django 没有比"注销 + 注册一个子类"更窄的钩子。看着别扭，但它是唯一
不把依赖方向弄反的写法，而且两个 `admin.py` 本来就是一次性配置（D18）。

**一般化**：
> 跨 app 的 admin 装配一律写在下游 app 里，别让上游去 import 下游。
> B6 的 `Participation` 要挂到 `Contact` 页上时，同一套写法再用一次。

### ⚠️ 计划外（B5）：每加一个 inline，所有 admin POST 测试都会变绿灯下的红灯

**第三次踩了**（B3.1 的 `relationships_as_b`、B4.2 的 `emergency_contacts`、
这次的 `assignments`）。症状每次一模一样：admin 的 POST 测试收到 **200 而不是 302**，
表单看着没错，`context_data["errors"]` 里写的是
`ManagementForm data is missing or has been tampered with.`

原因：`ModelAdmin` 会为每个 inline 要一份管理表单，少一份就整页不提交，
而**它不是字段错误**，所以 200 里看不到任何一个红框。

**修法**：测试的 `_admin_form_data()` 里补上那个 inline 的四个键
（`TOTAL_FORMS` / `INITIAL_FORMS` / `MIN_NUM_FORMS` / `MAX_NUM_FORMS`）。
已经在 helper 上写了注释，免得第四次再查一遍。

**一般化**：
> 加完一个 inline，先跑一遍 admin 的 POST 测试。 收到 200 就直接去
> `context_data["errors"]` 里看，不要从表单字段开始找。

### ⚠️ 计划外（B0–B5 复盘）：`list_display` 里的方法，是每行调一次的

> 下面三条是 B5 做完之后回头验收 B0–B5 时发现的，不是某一步实施当场撞上的。
> 记在这里理由相同：**三条里有两条是「防线看着在，其实没在」**，
> 而那正是这个项目反复判过刑的那类东西。

**症状**：`Contact` changelist 的查询数随行数线性增长 ——
5 行 10 次、40 行 45 次、100 行约 105 次。页面能用，只是越用越慢，不报错。

**根因**：`merge_link` 是 `list_display` 里的一个方法，**Django 每渲染一行就调一次**，
而它里面调 `find_exact_duplicates()`，每次一到两次查询。
讽刺的是集合级的判定 `possible_duplicates()` 早就写好了（「疑似重复」筛选器在用），
只是那个列没走它。`list_select_related` 救得了外键列，救不了自定义方法列。

**修法**：判定和配对都下沉到 QuerySet（D18 —— admin 只渲染，不判断）：

```python
# contact/models.py
def duplicate_partners(self):
    """{pk: 另一条同名同号记录的 pk}，一次查完整页。"""

# contact/admin.py —— 每请求算一次，缓存在 request 上
def get_list_display(self, request):        # ⚠️ 不是 get_queryset，见下
    if not hasattr(request, "_contact_duplicate_partners"):
        request._contact_duplicate_partners = Contact.objects.duplicate_partners()
    ...                                     # 列做成闭包，闭在这张表上
```

**为什么是 `get_list_display` 而不是 `get_queryset`**：B13 的清单里有一条
「`admin.py` 搜不到 `save_model` / `save_related` / `get_queryset` 重写」——
那条判据就是「把 `admin.py` 删掉还剩全部业务逻辑」的可执行版本，不能为了顺手破掉它。

**连带的好处**：这一列和「疑似重复」筛选器现在同一个定义。
以前两边各算各的（`find_exact_duplicates()` 用 `casefold()` + 压空白，
`possible_duplicates()` 用 `Lower(Trim())`），完全可能出现
「筛选器说它是重复、行里却没有合并链接」。

**测试**：`ChangelistCostTests` 比较 5 行和 25 行的查询数**是否相等**，
不钉死具体数字 —— Django 自己的基线查询数以后会变，而"每行一次"这件事不该变。

**一般化**：
> 凡是写进 `list_display` 的方法，先问一句「它查库吗」。
> 判定方法：造 N 行数一次查询数，造 2N 行再数一次，**两个数不一样就是 N+1**。
> 这条对 B6 的 `Event` 参与人数、工时合计一样成立 —— 那两个尤其像会写成 property。

### ⚠️ 计划外（B0–B5 复盘）：`full_clean()` 会把「已经有错的字段」的约束整条跳过

**症状**：给 `position_reports_to_self` 补字段级错误测试时，走 `full_clean()`
永远拿不到约束自己的 `violation_error_message`，拿到的是 `Position.clean()` 的措辞。

**根因**：`Model.full_clean()` 收完 `clean_fields()` / `clean()` 的错误之后，
会**把已经出错的字段名加进 `exclude`** 再传给 `validate_constraints()`，
而后者跳过任何提到被排除字段的约束。`clean()` 的环检查已经在 `reports_to` 上挂了错，
所以那条约束在这条路径上**根本没跑**。

> 这是 B3.1b 那条坑的第三个变体。 三个变体的症状一模一样 ——「约束没跑」：
>
> | 变体 | 约束为什么没跑 |
> |---|---|
> | D14 原文提醒的 | 表达式约束在 `validate()` 里抛 `FieldError`，被静默吞掉 |
> | B3.1b 撞上的 | 字段不在表单上 → `_post_clean` 把它 `exclude` 了 |
> | 本条 | 字段上**已经有别的错误** → `full_clean` 把它 `exclude` 了 |

**结论（不粉饰）**：**同一个字段上，`clean()` 和 `CheckConstraint` 都说话时，
表单层永远只会看到 `clean()` 的那句话**，约束的 `violation_error_message`
在这条路径上是死代码。它仍然有价值 —— 那是 `bulk_create` / psql 的兜底 ——
但别以为界面上出现的是它。

**修法**：该测哪一层就从哪个门进。`position_reports_to_self` 的测试直接调
`self.position.validate_constraints()`，绕开 `clean()`。
**这个门不是为测试造的** —— B3.1b 的 `RelationshipForm._check_constraints()`
走的就是它，那里前面没有 `clean()`。

**一般化**：
> **一条规则不要在 `clean()` 和约束里各说一遍**（这本来就是 D14 重写的初衷）。
> 真要两边都有（跨行环路这种约束表达不了、又想在表单上提示的），
> 就明确知道：界面上出现的是 `clean()` 的话，约束只是 bulk 路径的兜底。

### ⚠️ 计划外（B0–B5 复盘）：守卫「验过会红」不等于「该红的都红」

**症状**：汇报链 grep 守卫（B1 写的，B5 按清单确认过"真的会变红"）
对最自然的那种写法视而不见：

```python
for _ in range(20):
    nxt = p.reports_to      # 漏 —— for 和 reports_to 不在同一行
```

**根因两个，都是判据本身太窄**：

1. 判据是「**同一行**里既有 `reports_to` 又有 `for`/`while`」。
   而 roadmap 原话是「找 **循环体里** 出现 `reports_to` 的行」——
   实现取了字面上更省事的那一种读法，正好把多行写法全放过了。
2. `\breports_to\b` **匹配不上 `reports_to_id`** —— `_` 是 word 字符，
   两者之间没有词边界。而 `build_org_tree()` 自己用的就是 `.reports_to_id`
   （为了不 N+1，函数里有注释专门说明），**抄它的人多半连这个一起抄**。
3. roadmap 还写了第二个信号 ——「`.reports_to` 与**函数自身名字**同时出现的行」，
   也就是递归 —— **实现里根本没有这一条**。递归是遍历汇报链的第三种写法，
   而且是最容易挂死的那种。

B5 验收时只试了「同一行 `while`」那一种就签收了 —— 而那恰好是唯一能被抓到的那种。

**修法**：`core/tests.py::repeated_uses()` 改成按缩进跟踪 `for`/`while` 块，
三个信号任一命中即算：**循环体内 / 单行推导式 / 递归**（行里调了所在函数自己的名字）。
模式放宽成 `reports_to(_id)?`。`def` 会切断外层循环的作用范围（函数体自成一个 scope）。

**代价**：一处误报 —— `org/tests.py` 里一个集合推导式读了两个编制各自的上级
（读一层，不是走链）。用 `loop-guard-ok` 注释豁免，标记**写在本行或上一行都认**，
这样理由有地方写得下。宁可宽、误报了加注释，也别漏 —— roadmap 原文就是这么要求的。

**一般化（这条是三条里最值钱的）**：
> **守卫写完必须反向验：造几个它「该抓」的例子，确认真的红；
> 再造几个「不该抓」的，确认没红。** 只验一个例子等于只验了自己想到的那种写法。
> 一条只在自己的示例上会红的守卫，比没有守卫更糟 —— 它让人以为有防线。
> B6 之后每加一条 grep 守卫，都照这个双向清单走一遍。

### ⚠️ 计划外（B5 复盘）：用补集定义状态，等于赌只有两种状态

**症状**：`Position` 列表页的「空缺」筛选器，**已撤销的编制显示在「有人在任」那一档里** ——
一个去年撤掉、现在一个人都没有的编制，界面上说它有人。

**根因**：`Position` 有**三种**状态，筛选器只给了两个选项，第二个用补集实现：

```python
lookups = [("yes", "空缺"), ("no", "有人在任")]
...
if self.value() == "no":
    return queryset.exclude(pk__in=queryset.model.objects.vacant())   # ← 这里
```

`vacant()` 自己**从来是对的**（第一步就 `filter(is_active=True)`，而且有测试钉着）。
错的是"不是空缺的都算有人在任"这个推论 —— **补集只在状态恰好两种时才等价**。
撤销的编制既不空缺、也没人在任，于是被补集捞了进去。

> 补集写法最坏的地方不是算错，是它让你不必给状态命名。
> 三个分支都写成 QuerySet 方法的话，你得给第三种状态起个名字（`retired()`）——
> **而起名字的那一刻就会发现自己漏了它**。写成 `exclude(...)` 就永远不会碰到这一步。

**连带**：这个 `exclude(pk__in=...)` 是筛选器**自己在做集合运算**，
和它自己 docstring 里写的「每个分支都是一次 QuerySet 方法调用」对不上（D18）。
**判断跑进 admin 的那一处，正好就是出错的那一处** —— 这不是巧合。

**修法**：三种状态三个方法，各自独立定义，谁也不靠否定谁：

```python
def vacant(self, on=None):      # 还设着 且 没人
def occupied(self, on=None):    # 还设着 且 有人   ← 不再是 "not vacant"
def retired(self):              # 已撤销 —— 第三种状态，必须看得见
```

顺带做掉的三件（都在这一步免费）：

- `NOT IN (子查询)` → `NOT EXISTS` 相关子查询：能在第一条匹配就停、走
  `Assignment.position` 的索引、也没有 `NOT IN` 遇 NULL 的坑。两种都在数据库里跑，
  这个执行计划更好。
- 日期谓词抽成 `core/querysets.py::in_effect_on(on, prefix)`，`active()` 自己也改成调它。
  **不抽的话，聚合里就得把那个表达式再抄一遍** —— 而抄的那份迟早和原版不一致。
- `with_headcounts()`：`COUNT(...) FILTER (WHERE ...)` 条件聚合，
  一次查询给出每个编制的在任 / 在岗人数。**做成 annotation 而不是 property**，
  因为前端要能排序、过滤、分页、直接序列化，而 property 四样都做不到、还是 N+1。

**验收抄的是 `MinorFilter` 的先例**：三个选项各调一个方法，
外加一条 **partition 测试** —— 三者两两不重叠、并集是全表。
这条测试的价值在于：**以后再多出第四种状态，它会当场变红**，
而不是等到某一档默默多算了几行。

**一般化**：
> 不要用补集定义状态。 判定方法：把所有状态列出来数一数 —— 超过两种，补集就是错的。
> 项目里三态的先例早就有了（`MinorFilter` 的 未成年 / 成年 / **生日未知**），
> 当时 roadmap 专门强调过「第三个选项不能省 —— 未知必须看得见」，
> **同一条道理这里没执行**。
>
> **B6 直接受影响**：`Event.status` **五**档（draft / open / confirmed / completed / cancelled）、
> `Participation.status` 四种（registered / attended / absent / cancelled）。
> （2026-07-29 晚更正：原文写的是 `Event.status` 四种「planned / confirmed / completed /
> cancelled」—— `planned` 已被 `draft` + `open` 取代，见 B6。这条教训当天正是在
> `Event.status` 上又救了一次：可见集初稿写成了 `exclude(DRAFT)`。）
> 任何「已完成 = 不是已取消」「缺席 = 没签到」这类写法都是同一个病，
> 而且状态越多，补集捞进来的越多。一律列全 + partition 测试。

### ⚠️ 计划外（B12）：`event.start_time.date()` 是 UTC 的那一天

**症状**：R8 的 `ministry_staff_participation()` 按 roadmap 原文写成
`on = event.start_time.date()`（B12 那段代码块里就是这么写的），
一条月份边界的测试变红 —— 一场"8 月 1 日"的活动不在 8 月的窗口里。

**根因**：`DateTimeField` 取回来是 **UTC**。太平洋时间 7 月 31 日下午 6 点的活动，
`.date()` 答的是 **8 月 1 日**。于是 R8 那句"活动当天在职的 employee"
问的是**错的那一天**，差一天，**而且不报错** —— 正是 D16 存在的全部理由，
只是这次不在"今天"上，在"某个存下来的时刻"上。

**修法**：`core/timeutils.py` 加 `local_date_of()` / `local_month_of()`，
R8 改用前者。顺带加第三条时间守卫：`\w+_(time|at)\.date\(\)`
（本项目所有 datetime 字段都叫 `*_time` 或 `*_at`）。

**这条守卫加完当场又抓到两处** —— 都在同一天写的测试里，
而那两处测试正是用来验 R8 时间口径的。**一条只在自己身上验过的守卫等于没有守卫**
（B5 复盘那条），这次是反过来的证据：守卫一上线就找出了写它的人刚犯的同一个错。

**一般化**：
> D16 原来只管"今天"。 现在是两句：**取今天走 `local_today()`，
> 问某个存下来的时刻是哪一天走 `local_date_of()`**。
> `.date()` 这个方法在本项目里没有正确的用法。

顺带第三次踩到 B1 那条：**守卫的注释里不许把被禁的写法拼出来** ——
`local_date_of()` 的 docstring 原本举了反例，守卫当场抓到了自己。

### ⚠️ 计划外（B10）：权限守卫第一次跑，红的是 P5 自己那一页

**症状**：`MinistryRole.objects` 的 grep 守卫按"除 `permissions.py` 外一律不许"
写完，第一次跑就红 —— 命中的是 `org/views.py`，也就是 P5 的授权页本身。
那一页**按定义就要写这张表**（授权、撤销、列出）。

**根因**：守卫的范围写宽了。 `goal.md` D20 和 roadmap B7 的原话都是
"**`views.py` / `admin.py` / `forms.py`** 里不许出现"，而不是"除 permissions.py 外"。
两者的差别正好是 `services.py`。

**修法**：不是放宽豁免，是把写操作搬进 `org/services.py`
（`ministry_admins()` / `grant_ministry_admin()` / `find_grant()` / `revoke_ministry_role()`），
视图变成薄壳。守卫按文档原文改成只扫那三个文件名。

**一般化（值得记住的是这条分工）**：
> **`permissions.py` 负责判断（"他能不能"），`services.py` 负责写（授权 / 撤销），
> 视图两件都不做，只调其中一个。** 守卫红了的时候，先问是范围写错了
> 还是代码放错了 —— 这次两样都有一点，而放宽豁免会把后一半永久盖住。

### ⚠️ 计划外（B13）：验收清单跑成测试，当场抓出两个 500 / 403

**这一条本身就是结论**：B13 的三个角色各走一遍，我先写成了自动化的
`AcceptanceWalkTests`（跑 `seed_demo` 的数据，逐条对着 phase-b.md 的勾）。
第一次跑就红了两条 —— **而这两处 B6–B12 的单元测试全绿**。

#### 一、未成年人报名，关系那一栏留空 ⇒ 500

`SignUpForm.consent()` 把五个同意字段统一填成 `""`，
而 `consent_relationship` 是**外键** —— 给外键赋 `""` 直接
`ValueError: Cannot assign ""`。关系本来就是可空的、留空很常见，
所以这不是边角情况，**是那条路上最普通的一次点击**。
单元测试没抓到，是因为它们都直接调 `sign_up()`，绕过了表单那一层。

> 一般化：**表单往 model 送空值时，外键的空是 `None`，字符字段的空是 `""`**。
> 一句 `or ""` 扫过所有字段看着整齐，遇到外键就是 500。

#### 二、`foundation_admin` 这个 Group 什么权限都没有

验收 ① 要求总管"在 admin 侧看 R1–R3"，而 `foundation_admin_group()`
只是 `get_or_create` 了一个**空组** —— `is_staff=True` 加一个空组，
admin 里 403。D20 原文写着这个组要授 `org.add_ministryrole` 那几条，
**而代码只建了组、没授权限**。

> 一般化（这条更值钱）：**一个空的 Group 和一个满的 Group 在任何列表里长得一模一样**，
> 只有真的有人去用它的时候才知道是空的。所以权限跟着组一起在代码里建，
> 不留给"以后去 admin 里点一下"。

#### 三、连带发现：验收清单本身是可执行的

原以为"扮三个角色各走一遍"只能靠人点。实际上其中**大部分**能写成
`self.client.login(...)` + 打 URL + 断言，而且每一条都对应清单上的一个勾。
浏览器那一遍仍然要走（表单排版坏了、链接指向空处，断言看不出来），
但它现在是**复核**，不是唯一防线 —— 同 2026-07-29 晚把四条分层验收
从清单搬进 grep 守卫的那一次，一模一样的动作。

### ⚠️ 计划外（三方核对）：第二遍核对抓到的三处，都在第一遍的表格之外

2026-07-30 那次三方核对按八张表（模型 / 约束 / `on_delete` / 索引 / 函数签名 /
页面 / 测试 / seed）逐格核过，结论是"结构层面全部对得上"。**那句话是真的，
而下面三处一处都不在那八张表里** —— 它们分别是一条规则的第二句、一页的谓词、
和一个 `import` 的方向。记在这里，因为**核对表本身有形状**：
它盯得住"这个字段在不在"，盯不住"这条规则的后半句实现了没有"。

#### 一、`check_in()` 少了同意闸门 —— 文档写了两遍，代码里没有

`phase-b.md` 的同意字段那一行和「必须写的测试」那张表，都写着同一条 P3 规则：
未成年人没有同意记录，**既报不了名，也不能被 `check_in()` 标成 `attended`**。
`sign_up()` 实现了前半句，后半句一个字都没有，测试也没有。

**可达路径不是边角**：`ParticipationAdmin` 能直接建一行未成年人的 `Participation`
（模型层没有同意校验，也不该有 —— 那是跨表判断），签到页一点就 `attended`。
而 `attended` 正是工时、统计和通知挂着的那个状态。

**修法**：`events/services.py::_mark_attended()` —— 通往 `attended` 的三条路
（`check_in` / `check_out` / `record_hours`）全部走它。
只补 `check_in()` 的话，纸质补录那条路照样绕得过去。

> **一般化**：**一条规则有几个入口，就要问岗哨是不是只有一个。**
> 判定方法：grep `status = ...ATTENDED`，数一数有几处 —— 超过一处，
> 就把那一处抽成函数，而不是在每一处各写一遍判断。
> 这和 D9 那句"不经过 `save()` 直接写会不会被拒"是同一把尺子，换了一层。

#### 二、`/me/participations/` 没走 `visible_to_volunteers()`

B9 的第 1 条和 `phase-b.md`[可见性与生命周期](phase-b.md#可见性与生命周期两个谓词不是一个-status2026-07-29-晚新增)
都把这一页和详情页、通知链接列在同一组里，点名用 `visible_to_volunteers()`。
实现只有 `filter(contact=...)`。

后果具体得刚好是这两个谓词当初要防的那件事，只是从另一头来的：
这一页**每一行都链到详情页**，而详情页走 `visible_to_volunteers()` ——
所以一条建在 `draft` 活动上的报名（admin 按纸质名单代录的常规动作），
在这一页上列得出来，**点过去是 404**。

> 当时那一节盯的是"活动一 `confirmed`，报过名的人打不开它"。
> 同一对谓词漏在列表页那一侧，症状换成"列得出来、点不开"，
> 一样不报错。**成对的谓词要成对地核**，只核被写进正文那半条不够。

#### 三、`GrantForm` 放在 `events`，把依赖方向弄反了

P5 的表单定义在 `events/forms.py`，唯一使用者是 `org/views.py` ——
全项目唯一一条反向跨 app import。而 `INSTALLED_APPS` 的注释自己写着
"events depends on both"，D17 定的是 `events → org → contact → core`，
上面「计划外（B5）」那条教训的一般化原话是
"跨 app 的装配一律写在下游 app 里，别让上游去 import 下游"。

视图当时放对了（`org/views.py`，主语是 ministry），**表单跟着走的时候落在了原地**。
搬进新建的 `org/forms.py`，`Contact` 的 import 顺带从函数体里提回模块级
（原来写在函数里正是为了绕开这个方向问题）。

> **一般化**：**"这段代码归哪个 app"要问主语，不问它是被谁调用的。**
> 上一次这条教训是关于 admin inline 的（B5），这次是表单 —— 同一条判据第二次没执行，
> 说明它现在**只在 admin 那一格里被记住了**。它对 `forms.py` / `services.py` /
> `views.py` 一样成立。
