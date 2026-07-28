# Phase B 实施手册 —— 人与活动 MVP

> **这份文档只讲 Phase B 怎么做。** 要做什么、为什么这么定，全在 `goal.md` ——
> 那是唯一权威来源，本文与它冲突时以它为准。
> `01-roadmap.md` 是 Phase A 的实施手册，已完成，留作记录，不再更新。
>
> 写于 2026-07-28。
>
> **开工时的实测基线**（跑出来的，不是估的）：
>
> | 项 | 实测值 |
> |---|---|
> | `python manage.py test` | **27 个，全绿**（0.63s） |
> | `python manage.py check` | **0 issues, 0 silenced** |
> | 已有 app | `core` / `contact` / `accounts` |
> | 已有模型 | `Contact` / `RelationshipType` / `Relationship` / `Language` / `User` |
> | `contact` 迁移 | 到 `0004_historicalcontact` |
> | 数据库 | Postgres 18 在跑，psycopg 3 |
> | Django / Python | 5.2.16 / 3.14.6 |
> | **开发库里的业务数据** | `Contact` 0 行、`Relationship` 0 行、`RelationshipType` 0 行、`Language` 7923 行、`User` 1 个 |
>
> ⚠️ **最后一行很重要，直接改变了两处做法**，见 B0 的「实测发现」。

---

## 这一阶段要达成什么

把"人"和"活动"两条线建起来，做完能在本机完整演示一遍基金会的日常：
有哪些编制、谁在哪个 ministry 占着哪个编制、哪个编制向哪个编制汇报、**哪些编制空着**；
办了什么活动、谁参加了、干了几小时。

> **2026-07-28 二次修订**：`Position`（编制）从 `Assignment` 里拆出来了，B5 整段重写。
> 见 `goal.md` D11「第二次修订」和文末「计划外记录」上面的那条说明。

**同时把 `contact` 现有的三个欠账收掉**（关系的反向显示、类型表的 `code`、
`Contact.__str__` 的重名消歧）—— 它们不收，后面新建的每一个 autocomplete
和每一处按类型的查询都会踩在流沙上。

### 验收标准

**你自己能在本机浏览器里跑通一遍完整流程，数据全部来自 `seed_demo`，
不进任何真实的人。** 逐条清单见 B9。

> **交付给基金会真用属于 Phase D**，前置条件是备份演练过、且他们不用 superuser 登录。
> 理由在 `goal.md` 的 Phase B 验收注和 Phase D 开头。

### 明确不做的（免得中途手痒）

| 不做 | 去哪了 |
|---|---|
| `Guardianship` 法定监护表 | 移出 Phase B，等基金会答复同意书流程（`goal.md` D15 待确认） |
| `Skill` + `VolunteerProfile.skills` | 推迟清单 |
| 活动班次 `Shift` | 推迟清单 —— 多班次一律拆成多个 `Event` |
| 逐字段合并的交互界面 | 推迟清单 —— 合并功能本身要做，界面从简 |
| 自己写的页面 / HTMX | Phase C（D2：前端推迟）。本阶段全部在 admin 里完成 |
| 薪酬 | 推迟清单 + `payroll` app 的位置已在 D17 预留 |
| **带日期的编制层级**（组织架构的历史） | 推迟清单 —— 本阶段解决的是"**换人**"，不是"**重组**"。`Position.reports_to` 改了，旧架构只剩 simple-history |
| **`Position.headcount`**（编制人数） | 推迟清单 —— `vacant()` 只认"一个人都没有"，表达不了"3 个坑填了 2 个" |

---

## 为什么按这个顺序

三条硬依赖决定了整个顺序，其余的按"谁先建谁被依赖"排：

1. **`core` 的时间口径和 `.active()` 必须最先** —— 后面每一张带日期的表都用它。
2. **`contact` 的收口必须排在所有新表之前** ——
   - `RelationshipType.code` 要赶在**任何按类型查询的代码**写出来之前（晚了字符串匹配就扩散了）；
   - `Contact.__str__` 消歧要赶在 **B5/B6 那三个新 autocomplete** 之前（晚了下拉框里全是一模一样的选项）；
   - `Contact.emergency_contact_relationship` 依赖 `RelationshipType.usable_as_emergency_contact`。
3. **关系的双向显示必须先于对称归一化** —— 顺序反了，用户刚录的关系会从他的页面上消失
   （`goal.md` Phase B「对称关系」那一条）。

```
B0 基线与准备（分支 / ruff / 确认不阻塞项）
 └→ B1 core：local_today() + DateRangeQuerySet + 两条守卫测试
     └→ B2 contact①：RelationshipType 收口（code / is_symmetric / 唯一约束）
         └→ B3 contact②：Relationship 收口（双向显示 → 归一化 → 删 is_active）
             └→ B4 contact③：Contact 收口（__str__ / 紧急联系人 / 查重 / 合并 / is_minor）
                 ├→ B5 org：Ministry + EmploymentType + Position + Assignment
                 │   └→ B6 events：EventType + Event + ParticipationRole + Participation
                 └→ B7 volunteer：VolunteerProfile
                     └→ B8 seed_demo
                         └→ B9 验收
```

B5 和 B7 之间没有依赖，但 B6 依赖 B5（`Event.ministry` → `Ministry`）。
**B5 内部还有一条硬顺序**：`Ministry` → `Position` → `Assignment` ——
`Assignment` 只有 `position` 一个业务外键，没有 `Position` 它就是空壳。

---

## B0 · 基线与准备

```bash
git switch -c phase-b
python manage.py test          # 应该是 27 个，全绿
python manage.py check         # 应该 0 issues
```

**基线数字：27 个测试。** B9 验收时对比，只增不减。

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

1. **`RelationshipType.code` 不需要 `goal.md` 写的三步迁移。**
   三步法（加可空 → 数据迁移回填 → 改 unique/non-null）是**表里有数据时**的必要手续；
   0 行时一步加 `SlugField(unique=True)` 就行。
   **但 `goal.md` 里那条三步规则不要删** —— 它对以后任何"给有数据的表加唯一字段"仍然成立，
   只是这一次的前置条件不满足。B2 里会写清楚这个简化和它的适用条件。
2. **本阶段全程不需要写数据迁移。** 所有新约束都加在空表上，不存在"先清洗存量数据"的问题 ——
   这正是 A7 说的"现在加是免费的"。

> 顺带记一笔：`Language` 有 7923 行，所以每次跑测试都会重灌一遍。
> **日常用 `python manage.py test --keepdb`**（README 里已写）。

---

## B1 · `core`：时间口径与共享 `.active()`

**为什么最先做**：后面每一张带起止日期的表都要用 `.active()`，而它的定义里有一个
会静默出错的坑（时区）。定义只留一处，且在第一个使用者出现之前就位。

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

> ⚠️ 这个 property 和 `.active()` 是**同一条规则的两处实现**，按 D14 的纪律
> **两处都要写注释指认对方**。真想彻底避免，可以让 property 走
> `type(self).objects.filter(pk=self.pk).active().exists()`，但那是每行一次查询 ——
> admin 列表里就是 N+1。**选了重复实现，就必须靠注释和测试兜住。**

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

最后一条 grep 守卫的写法：遍历项目下的 `*.py`（跳过 `.venv`、`*/migrations/*`
和 `core/timeutils.py` 自己），正则找 `date.today()` 和 `timezone.now().date()`，
命中就 fail 并打印文件和行号。**`ruff` 的 `DTZ` 抓不到第二种**（那是 tz-aware 的，
linter 认为合法），所以这条测试不能省。

前六条测试需要一个带 `start_date` / `end_date` 的模型。B1 时还没有 ——
**先用 `Relationship` 测**（它已经有这两个字段），B3 接上 `.active()` 之后自然成立。

**验证**：`python manage.py test core` 全绿；`ruff check .` 干净。

---

## B2 · `contact` ①：`RelationshipType` 收口

**为什么在这个位置**：`code` 必须赶在任何按类型查询的代码之前落地；
`usable_as_emergency_contact` 是 B4 紧急联系人字段的前置。

> ⚠️ **2026-07-28 第三轮修订**：已确认 **`bulk_create` 会成为常态写入路径**
> （批量导入基金会现有数据）。所有"`save()` 归一化 + 唯一约束"的组合因此都是漏的 ——
> 唯一性一律改用 `Lower()` / `Trim()` / `Least()` 的**表达式约束**。
> 通则和判定方法见 `goal.md` D9「归一化通则」。本步和 B3.2、B5 都受影响。

### 三个新字段

```python
class RelationshipType(models.Model):
    # 代码只认 code，永远不认显示名。显示名可以在 admin 里随时改，
    # 而 filter(name_a_to_b="parent of") 会在改名之后静默失效。见 goal.md D5 / D6。
    code = models.SlugField(max_length=50, unique=True)

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
        # ⚠️ PAIRED WITH RelationshipType.clean() —— 见 goal.md D14。
        # Lower：普通 UniqueConstraint 挡不住 "Parent of" vs "parent of"，
        #        那和 clean() 里「忽略大小写」的口径也对不上。
        # Trim ：只靠 save() strip 的话 " parent of" 会被 bulk_create 塞进来。
        #        见 goal.md D9「归一化通则」。
        models.UniqueConstraint(
            Lower(Trim("name_a_to_b")),
            name="relationshiptype_name_a_to_b_ci_unique",
            violation_error_message="已经有一个同名的关系类型了。",
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

> **这是 2026-07-28 第三轮修订的核心改动。** 原来的写法是"`save()` 归一化 + `unique=True`"，
> 看上去像数据库在把关，其实**只要不经过 `save()` 就全漏**。
> 而 `bulk_create` 已确认会成为常态写入路径（批量导入基金会现有数据）。
> 判定方法见 `goal.md` D9 通则：**不经过 `save()` 直接写这两行，数据库会不会拒？**

### `clean()` 提示层 + 反向类型拦截（缺口 1）

```python
def clean(self):
    """⚠️ PAIRED WITH relationshiptype_name_a_to_b_ci_unique —— 见 goal.md D14。"""
    # 1. 唯一约束的人话版本（把错误挂到 name_a_to_b 字段上）
    # 2. 缺口 1：新类型的 name_a_to_b 撞上任何已有类型的 name_b_to_a 就报错，
    #    并指出撞的是哪一行。
    #    根因：反向类型行本来就不该存在 —— "child of" 已经是 "parent of" 的
    #    name_b_to_a 了。类型行不存在，反向关系行就根本录不出来。
    #    所以防线加在类型层，不是关系层。
```

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
所以一步加 `SlugField(unique=True)` 即可。

```bash
python manage.py makemigrations contact
```

`makemigrations` 仍会为不可空字段索要一个 one-off default（它看的是 schema 不是数据）。
给 `""` 即可 —— 0 行时不会应用到任何行；生成后**把迁移文件里那个 `default=""` 删掉**、
`preserve_default=False`，免得它留在文件里误导以后的人。

> **这条简化只在"表是空的"这个前提下成立。** 以后给任何有数据的表加唯一字段，
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

本阶段取**做法 1**（加第二个 inline），因为做法 2 要自己写视图，属于 Phase C 的范围：

```python
class RelationshipAsBInline(admin.TabularInline):
    model = Relationship
    fk_name = "contact_b"
    verbose_name = "关系（对方发起）"
    extra = 0
    readonly_fields = [...]        # 只读：录入统一走下面那个方向感知的 inline
    can_delete = False
```

标签用 `name_b_to_a`；**`is_symmetric=True` 时回落到 `name_a_to_b`**
（配偶、兄弟姐妹的反向标签是空的）。

### B3.1b 方向感知的录入表单（2026-07-28 新增，取代「总是从 A 侧录入」）

> **原方案已废弃**：靠 `help_text` 要求"总是从 A 那一方的页面录入"。
> 那是**把外键方向翻译成人工纪律**（`goal.md` D18 的典型反例），
> 而且有功能缺口 —— **站在小明页面根本录不了"王强是我爸爸"**。

给录入用的 inline 配一个自定义 `ModelForm`，把类型下拉换成**带方向的读法**：

```python
class RelationshipInlineForm(forms.ModelForm):
    """类型下拉列出正反两个方向；contact_a/contact_b 由 save() 路由，用户看不到 A/B。"""

    # 选项形如 (f"{type_id}:fwd", "小明 是 ___ 的父亲")
    #          (f"{type_id}:rev", "小明 是 ___ 的儿子")
    # is_symmetric=True 的类型只生成一条（用 name_a_to_b）。
    direction_choice = forms.ChoiceField(label="关系")
    other = forms.ModelChoiceField(queryset=..., label="对方")   # 走 autocomplete

    def save(self, commit=True):
        type_id, direction = self.cleaned_data["direction_choice"].split(":")
        me, other = self.instance_owner, self.cleaned_data["other"]
        if direction == "fwd":
            self.instance.contact_a, self.instance.contact_b = me, other
        else:
            self.instance.contact_a, self.instance.contact_b = other, me
        ...
```

⚠️ **对称类型的 id 排序不要写在这里** —— 它留在 `Relationship.save()`（B3.2）。
表单只做**方向路由**，规范化只有一处，理由见 `goal.md` D9 归一化通则
（导入路径根本不经过表单）。

⚠️ **按 D18，选项的生成和路由逻辑放 model / services 层**，
`Form` 只调用 —— Phase C 的 HTMX 页面要复用同一套。

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
)
```

**不带条件，对所有类型一律生效。** 缺口 1 修好之后（反向类型行根本不该存在），
同一对人 + 同一类型出现两个方向对**任何**类型都是错的：`spouse` 本就只该一行；
`(小明, 王强, parent of)` 意思是小明是王强的父亲，同一对人不可能双向成立。

**用 `Coalesce` 而不是 `nulls_distinct=False`**：表达式 `UniqueConstraint` 与 `nulls_distinct`
能否共存尚未实测（`Assignment` 那条正因此放弃了 `Lower("title")`）。
`Coalesce("start_date", Value(date.min))` 语义等价 —— 两行都为空时仍算重复 ——
且不依赖那个不确定的组合。**这是主方案，不是退路。**

⚠️ **实施时先跑一次确认表达式约束真的建出来了**（B9 的 `\d contact_relationship` 会验），
Django 生成的是 `CREATE UNIQUE INDEX ... ON (LEAST(...), GREATEST(...), ...)`。

**② `save()` 归一化（只对对称类型，只管显示）：**

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
> **欠账要记明**：B3.1 取的是"两个 inline"的做法，那么交换之后这一行会**从 inline A 跳到 inline B**
> —— 数据没丢、标签也对，但用户会困惑"我明明填在上面"。合并视图版没有这个问题。
> 见 `goal.md`「关系类的收口」。时间不够可以先这样，但这是欠账不是等价选项。

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

# —— 强制层：全部用 bulk_create，绝不能走 save() ——
def test_bulk_create_cannot_insert_a_mirrored_symmetric_pair(self)    # (王强,李梅) + (李梅,王强) spouse
def test_bulk_create_cannot_insert_a_mirrored_asymmetric_pair(self)   # parent of 也一样被拒 —— 约束不带条件
def test_the_same_pair_and_type_can_repeat_with_different_start_dates(self)
def test_the_same_pair_and_type_with_both_start_dates_null_is_rejected(self)  # Coalesce ≡ nulls_distinct=False
```

> **中间三条如果写成走 `save()` 的版本，会全绿而且什么也没验证** ——
> 绕过 `save()` 正是这条约束存在的全部理由。

外加 B1 那六条 `.active()` 边界测试现在正式挂在 `Relationship` 上。

**验证**：`test` 全绿；肉眼验一次 —— 在王强页面录「parent of 小明」，
打开小明页面能看到「child of 王强」。

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

### B4.2 紧急联系人三个字段

```python
emergency_contact = models.ForeignKey(
    "self", on_delete=models.PROTECT, null=True, blank=True,
    related_name="listed_as_emergency_contact_by",
)
emergency_contact_relationship = models.ForeignKey(
    RelationshipType, on_delete=models.PROTECT, null=True, blank=True,
    related_name="+", limit_choices_to={"usable_as_emergency_contact": True},
)
is_reference_only = models.BooleanField(default=False, db_index=True)
```

`PROTECT` 而不是 `SET_NULL`：**紧急电话静默变空比"删不掉这个人"危险得多。**

两条约束（按 D14 各配 `clean()` 提示层，两处互相注释）：

```python
models.CheckConstraint(
    condition=~models.Q(emergency_contact=models.F("id")),
    name="contact_emergency_contact_is_not_self",
),
models.CheckConstraint(
    condition=(models.Q(emergency_contact__isnull=True)
               | models.Q(emergency_contact_relationship__isnull=False)),
    name="contact_emergency_contact_has_a_relationship",
    violation_error_message="记了紧急联系人就必须写清关系。",
),
```

**方向约定**（不写死一定会录反）：`emergency_contact_relationship` 一律读作
**「紧急联系人 是 本人 的 ___」**，即 `name_a_to_b`，a = 紧急联系人、b = 本人。
小明的记录上填 `emergency_contact=王秀英` + `parent of` = "王秀英是小明的母亲"。
**这句话要原样写进 admin 的 `help_text`。**

### `is_reference_only` 的纪律

```python
class ContactQuerySet(models.QuerySet):
    def people(self):
        """排除只作为参照存在的记录（自动建出来的紧急联系人）。

        所有面向人的列表 / 统计 / 导出一律走它。默认 manager 必须保持全集 ——
        把 Contact.objects 改成默认过滤会让 admin、外键校验、get() 的行为
        变得诡异，是个比原问题更大的坑。见 goal.md D15 / Phase B。
        """
        return self.exclude(is_reference_only=True)
```

配一条 grep 守卫测试（同 D16 的套路）。admin 的 Contact 列表加一个
**默认预选**"仅真实联系人"的 `SimpleListFilter`，需要时能切到全部。

两条相关小规则：

- reference-only 记录仍要满足 `contact_name_matches_type` ——
  **所以只有电话没有姓氏时建不出来**，要在录入界面上拦，不要等数据库报错。
- "某个 reference-only 记录已经有了 `Assignment` 或 `Participation`"说明它早该被提升了，
  做成一条数据质量提示（跨表，`CheckConstraint` 表达不了，不做约束）。

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

**不要用电话相似度。** 号码存的是 E.164：`+14085550102` 和 `+14085550103`
字符相似度 92%，却是完全不同的两个人 —— 号码没有"接近"这个语义。
而真正需要吸收的格式差异（`(408) 555-0102`）`phonenumber_field` 入库时已经归一化掉了。

漏掉的两种情况恰好都该漏掉：**同号不同名**（一家人共用号码）、
**同名不同号**（重名的另一个人）。

> 这条规则顺带保证**提示不披露用户尚未输入的信息** —— 必须已经同时知道姓名和号码
> 才可能命中。所以**不做按姓名的 autocomplete 下拉**（那是唯一会泄露"系统里有个同名的人"
> 的路径），改成两个字段都填完后再检查。提示里**只显示姓名**。

**表单形态**（`ContactAdminForm` 上加三个非模型字段：`ec_last_name` / `ec_first_name` / `ec_phone`）：

| 情形 | 行为 |
|---|---|
| 没命中 | 保存时自动建一条 `is_reference_only=True` 的 Contact 并关联 |
| **命中唯一一条** | **表单里预先关联好**（可见的默认值），旁边一个"不是同一个人，改为新建"的纯文字链接 |
| 命中 2 条以上 | **不预选**，`messages.warning` 列出候选（只显示姓名 + 链接），仍然放行；这条记录会进"疑似重复"筛选器 |

**"命中时预选"是"用户懒得关联"唯一有效的解法** —— 懒惰的用户什么都不点、
直接保存，做的恰好是对的事。

这和"不能自动关联"不矛盾，界线是**「静默替换」vs「可见的默认值」**：
保存前就写在表单上、指名道姓、一次点击可撤销、且 `Contact` 已挂 simple-history 全程留痕。

> ⚠️ **残留风险：同名同号的父子 / 母女会被默认关联错。** 在服务对象里
> "父子同名 + 共用手机号"不是不可能。接受它 —— 错了是可见的、可一键撤销、有审计。
> 真出过一次事，就把"命中时预选"降级成"只提示不预选"，一行开关。

**紧急联系人这一支不要阻塞保存，也不要弹窗强制选择。** 漏网的进合并队列。

### B4.3b 联系人本身的重名：分级拦截（2026-07-28 新增）

上面那张表管的是**紧急联系人**的关联。**联系人本人的重名是另一件事**，规则也不同：

| 信号 | 频率 | 处理 |
|---|---|---|
| 仅**同名**（姓名归一化后比较） | 高 | `messages.warning`，**不阻断** |
| **同名 AND 同号**（`find_exact_duplicates()`） | 低 | ✅ **硬拦截**：`ValidationError` + `force_save` 复选框 |

> **原方案是"一律只警告不阻止"。** "重名合法"这个判断没错，但 `messages.warning`
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

⚠️ **硬拦截只能绑同名同号，绝不能绑同名。** 王强 / 李明 / 陈伟同名是常态 ——
每天弹 20 次，操作员会训练出"看到框就打勾"的条件反射，**拦截失效还多两次点击**。
这正是本节上面写过的"阻塞保存会让人学会绕过系统"。

⚠️ **按 D18，判定和拦截逻辑放 model / services 层，`Form` 只调用。**
Phase C 的 HTMX 录入页要复用同一套。

### B4.4 合并重复记录

**范围：最小可用。** 逐字段合并界面推迟（推迟清单）。

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
   **keep 的字段优先，drop 只在 keep 为空时补进来。**

**入口**：admin 的 `Contact` changelist 加一个「疑似重复（同名同号）」筛选器
+ 一个 admin action（选中两条 → 合并，带二次确认）；再加一个只列清单的
management command。admin 首页放一个"疑似重复待处理：N 条"的计数。

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

**`list_filter = ["is_minor"]` 不能用** —— property 无法进 ORM 过滤。
写一个 `SimpleListFilter`，三个选项（未成年 / 成年 / **生日未知**），
翻译成 `birth_date` 的区间查询。第三个选项不能省 —— "未知"必须看得见。

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
# B4.2
def test_a_contact_cannot_be_their_own_emergency_contact(self)
def test_an_emergency_contact_without_a_relationship_is_rejected(self)
def test_people_excludes_reference_only_contacts(self)
def test_nobody_lists_contacts_without_going_through_people(self)      # grep 守卫
# B4.3
def test_same_name_same_phone_is_a_match(self)
def test_same_name_different_phone_is_not_a_match(self)
def test_same_phone_different_name_is_not_a_match(self)
def test_a_unique_match_is_preselected_on_the_form(self)
def test_multiple_matches_are_not_preselected(self)
def test_the_duplicate_hint_does_not_block_saving(self)
def test_the_hint_reappears_on_every_save(self)                        # 不是一次性
def test_an_unknown_emergency_contact_is_created_as_reference_only(self)
# B4.4
def test_merge_moves_every_reverse_relation(self)                      # 见下
def test_merge_refuses_when_both_contacts_have_a_user(self)
def test_merge_refuses_on_a_unique_constraint_clash(self)
# B4.5
def test_is_minor_returns_none_when_the_birth_date_is_unknown(self)
def test_is_minor_on_the_eighteenth_birthday(self)
```

> **`test_merge_moves_every_reverse_relation` 的写法**（比 `goal.md` 里写的
> "测试里新造一张表"更好）：遍历 `Contact._meta.related_objects`，
> **断言每一项要么被搬走了、要么在显式的跳过名单里**（跳过名单只有 `Historical*`）。
> 这样以后任何人给 Contact 加了新外键却没决定合并时怎么处理，这条测试会当场变红。

**验证**：`test` 全绿；肉眼验 —— 录一个志愿者填一个系统里没有的紧急联系人，
再录第二个志愿者填同名同号的，第二次应该自动关联到第一次建出来的那条。

---

## B5 · `org`：`Ministry` + `EmploymentType` + `Position` + `Assignment`

> ⚠️ **本步在 2026-07-28 二次修订后重写。** `Position`（编制）是新拆出来的表，
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
    ⚠️ 任何递归走 reports_to 的代码必须带 visited 兜底：跨行环路数据库拦不住。
    """
    code       = SlugField(unique=True)                # 代码只认它，不认 name
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

**`Position` 是编制类型，不是座位。** 三个食物银行志愿者 = **一个** `Position` +
三行 `Assignment`。这是这张表能保持在几十行、不膨胀成几百行的原因。
**因此不加"一个编制同时只能有一个在职任职"的约束** —— 它既挡不住合法的多人共岗，
也挡不住合法的交接期重叠。

⚠️ **`reports_to` 用 `PROTECT`，不是 `SET_NULL`。**
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

⚠️ **`code` 用 `UniqueConstraint(Lower("code"))`，字段上不写 `unique=True`。**
`save()` 转小写只保证"存进去的值好看"，`bulk_create` 能插 `Food_Pantry` + `food_pantry` 两行。
**`Ministry` / `EmploymentType` 以及 B6 的 `EventType` / `ParticipationRole` 一律照此办理** ——
见 `goal.md` D9「归一化通则」。

`name` **不加**唯一约束 ——
两个 ministry 各有一个"协调员"是合法的，靠 `__str__` 带上 ministry 消歧
（同 `Contact` 重名的口径：重名合法，靠显示消歧不靠约束禁止）。

**`save()` 里归一化 `name`**（strip + 连续空白压一个）。
**`code` 的不可改**照 B2 的 `RelationshipType` 同一套做法（admin `get_readonly_fields`
在 change 页只读 + `clean()` 比对数据库旧值）。

**空缺查询 —— 这是拆出这张表的首要理由，必须一起落地：**

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

### 汇报线的环

`CheckConstraint` 只挡得住深度 1。**A→B→A 是两次各自合法的插入，
数据库用 CHECK 表达不了跨行环路**，后果是任何递归走 `reports_to` 的代码挂死。

两道防线：

1. `Position.clean()` 向上走链（带 `visited` 集合、限深 20）拒绝成环。
   按 D14 这**只是提示层** —— `bulk_create` 绕得过去，是已知的不完美，不粉饰。
2. **所有遍历汇报链的代码一律带 `visited` 兜底**，不假设数据是干净的。
   这条写进 `Position` 的 docstring（上面骨架里已经写了）。

> 好消息：环现在只可能出现在几十行的编制表里，而不是每次招人都新增一行的任职表里。
> 防线照做，但风险等级从"迟早会踩"降到"基本不会踩"。

### `Assignment`（任职 —— 谁在什么时候占了哪个编制）

```python
class Assignment(TimeStampedModel):
    contact         = FK(Contact, CASCADE, related_name="assignments")
    position        = FK(Position, PROTECT, related_name="assignments")
    employment_type = FK(EmploymentType, PROTECT, null=True, blank=True)
    start_date      = DateField(null=True, blank=True)
    end_date        = DateField(null=True, blank=True)

    history = HistoricalRecords()
    objects = Manager.from_queryset(DateRangeQuerySet)()
```

**就这五个字段。** 没有 `kind` / `title` / `ministry` / `is_leader` / `reports_to`，
它们全在 `Position` 上。**不加 `is_active`** —— 在职由日期派生。

⚠️ **`position` 用 `PROTECT`。** 写成 `CASCADE` 的话，删一个编制
→ **占过它的所有人的任职历史一起消失**。同 `Participation.contact` 的道理。

```python
constraints = [
    CheckConstraint(end_date >= start_date, name="assignment_end_date_after_start_date"),
    UniqueConstraint(
        fields=["contact", "position", "start_date"],
        name="assignment_unique_tenure",
        nulls_distinct=False,
    ),
]
indexes = [models.Index(fields=["position", "end_date"])]
```

**唯一约束简化了。** 旧版是 `(contact, ministry, kind, title, start_date)`，
还专门论证过"为什么必须带 `title`"（否则张三在食物银行同时当两个职务时第二行被误杀）——
**拆出 `Position` 之后那整段论证作废**：两个职务本来就是两个 `Position`，天然放行。
> **记一笔：约束越加越长往往是模型没拆干净的症状。** 这次就是。

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
def test_a_reporting_line_can_cross_kinds(self)                  # 执行总监(employee) → 理事长(board)

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
```

**验证**：`test` 全绿；admin 里能建 ministry、在它下面建几个 `Position`、
挂上 `Assignment`、`reports_to` 和 `position` 的下拉都能搜到；
**把某个 `Position` 上的人换掉（旧的填 `end_date` + 新建一行），确认下属的汇报线一个字没动**。

---

## B6 · `events`：`Event` 一族

```bash
python manage.py startapp events
```

### 四张表

| 模型 | 要点 |
|---|---|
| `EventType` | 字典表：`code`（唯一·不可改）/ `name` / `is_active` |
| `Event` | `name` / `event_type`(FK) / **`ministry`(FK，可空)** / `start_time` / `end_time` / `location` / `owner`(FK → Contact) / `status`(`TextChoices`：planned·confirmed·completed·cancelled) / `capacity`（可空，**参考值**） |
| `ParticipationRole` | 字典表：`code` / `name` / `is_active`。**一次活动之内**的角色（签到台、搬运、翻译） |
| `Participation` | `event` / `contact` / `role`(可空) / `status`(`TextChoices`：registered·attended·absent·cancelled) / `hours`(`Decimal(6,2)`，**可空**) |

`Event.ministry` 不能漏 —— 没有它就查不出"食物银行这个月办了几场"。

### 一人一活动多角色

```python
UniqueConstraint(
    fields=["event", "contact", "role"],
    name="participation_unique_per_role",
    nulls_distinct=False,
)
```

同一人 + 同一活动 + **同一角色**的第二行 → 拒绝（防手滑）；
**不同角色** → 放行（上午搬运、下午签到台，两段时长不同、奖励也可能不同）。

**不建 `Shift` 表** —— 多班次一律拆成多个 `Event`。时段差异由 Event 表达、
做的事差异由 `role` 表达、时长差异由各行 `hours` 表达，三个维度一个不少。
代价是"上午场/下午场"在统计里算两场，真要归成一次时用 `Event.parent` 自引用 FK（推迟清单）。

### 其余约束

```python
# Event
CheckConstraint(end_time >= start_time)
CheckConstraint(capacity IS NULL OR capacity > 0)
Index(fields=["start_time"])
Index(fields=["ministry", "start_time"])

# Participation
CheckConstraint(hours IS NULL OR hours >= 0)
CheckConstraint(status = 'attended' OR hours IS NULL OR hours = 0)
```

最后一条防的是 `status=缺席` + `hours=5` —— 和 `Relationship` 的
`is_active=True` + `end_date=2020` 是同一种病。

`hours` 必须 `null=True`：**报名了还没发生 ≠ 干了 0 小时。**

`capacity` 超了只在 admin 里 `messages.warning`，**不做约束、不阻止** ——
现实里超员登记是常事，系统的职责是提醒而不是拦路。

### `on_delete`

`Event.event_type` / `.ministry` / `.owner` → `PROTECT`（`CASCADE` 会让删一个人带走整场活动）；
`Participation.event` → `CASCADE`；`Participation.contact` → **`PROTECT`**
（`CASCADE` 会抹掉全部工时历史，那是 Phase C 统计的基础）；`Participation.role` → `PROTECT`。

> **连带效果（是特性不是 bug）**：有过活动记录的联系人就删不掉了，只能停用。
> 这与推迟清单里"不做软删除、`is_active` 已覆盖停用语义"一致。

### admin

`EventAdmin` 用 inline 直接登记参与者；`ContactAdmin` 加一个显示 TA 参加过的活动的 inline。
`date_hierarchy = "start_time"`。活动页加一个"未成年参与者"的视图或筛选器 ——
能看到他们的紧急联系电话（B4.5 + B4.2 合起来就是家长通知的完整闭环）。

### 测试

```python
def test_one_person_can_take_two_roles_in_one_event(self)
def test_the_same_person_and_role_cannot_be_registered_twice(self)
def test_duplicate_participation_with_no_role_is_rejected(self)     # nulls_distinct
def test_negative_hours_are_rejected(self)
def test_hours_on_a_non_attended_row_are_rejected(self)
def test_event_end_time_cannot_precede_start_time(self)
def test_deleting_a_contact_with_participation_is_blocked(self)     # PROTECT
def test_minor_participants_can_be_listed_with_their_emergency_phone(self)
```

**验证**：`test` 全绿；admin 里开一个活动、给同一个人登记两个角色、总工时对得上。

---

## B7 · `volunteer`：`VolunteerProfile`

```bash
python manage.py startapp volunteer
```

**两个模型，不是一个**（2026-07-28 修订，见 `goal.md` D18）：

```python
class VolunteerProfile(TimeStampedModel):
    contact = OneToOneField(Contact, CASCADE, related_name="volunteer_profile")
    availability_notes = TextField(blank=True)
    # skills 跟着 Skill 一起推迟


class BackgroundCheck(TimeStampedModel):
    """独立成模型，因为 Django 权限粒度是 app_label.model —— 没有字段级权限。

    留在 VolunteerProfile 里的话，Phase D 只有两个选择：整张表不给看
    （连技能和可服务时段一起锁掉，过度），或者全给看（泄露本系统里仅次于
    薪酬的敏感数据）。拆开之后一个 Group 不授 volunteer.view_backgroundcheck 即可。
    同 D17 让 payroll 独立成 app 的逻辑，区别只是粒度。

    挂 Contact 而不是 VolunteerProfile：背景审查是对「人」做的（D10 角色层，
    换岗不用重查），而且将来员工、理事也可能需要。
    """
    contact      = OneToOneField(Contact, CASCADE, related_name="background_check")
    status       = CharField(choices=Status)        # TextChoices：代码要按它分支
    completed_on = DateField(null=True, blank=True)
    notes        = TextField(blank=True)

    history = HistoricalRecords()
```

⚠️ **现在拆成本≈0**（`volunteer` app 一行代码还没写）；
以后拆要建表 + 搬两个字段 + 改所有引用，而那时表里是真人的审查结果。
**按 Phase A 的准入标准（"现在改成本≈0，以后改很痛"），这条属于必须现在做。**

**存完成日，不存到期日。** 有效期长度放 settings：

```python
# base.py —— 基金会尚未答复实际期限，730 天（2 年）是美国非营利常见值，占位用。
BACKGROUND_CHECK_VALID_DAYS = 730
```

"是否过期"做成 property + admin 筛选器。理由和不存 `age` 完全一样：
政策改了（比如从 2 年缩到 1 年）不用洗数据。

**不含** title / 上级 / 任职起始日（那些是岗位，归 `Assignment`）；
**不含**紧急联系人（在 `Contact` 上）；**`skills` 跟着 `Skill` 一起推迟。**

**敏感度**：背景审查结果是本系统里仅次于薪酬的敏感数据。
Phase D 的权限方案里要和未成年人信息一起单独处理 —— 这条现在只是记着，本阶段不实现权限。

### 测试

```python
def test_a_background_check_expires_after_the_configured_period(self)
def test_a_check_without_a_completed_date_is_not_reported_as_expired(self)
def test_background_check_permissions_are_separable_from_volunteer_profile(self)
#   ↑ 断言 volunteer.view_backgroundcheck 和 volunteer.view_volunteerprofile
#     是两个独立的 Permission 行 —— 这就是拆表的全部目的，钉住它
```

---

## B8 · `seed_demo`

```
volunteer/management/commands/seed_demo.py   （或放 core，随意，但只此一份）
```

**三条安全要求，一条都不能省：**

1. **幂等** —— 全部 `get_or_create`，跑三次不会得到三套张三（否则重名提示天天弹）。
2. **拒绝在非开发环境运行**：

   ```python
   if not settings.DEBUG and not options["force"]:
       raise CommandError("seed_demo 只能在 DEBUG=True 下运行。真要跑请加 --force。")
   ```

   上线后一次误运行就是往生产库灌假联系人，而按本设计它们和真人长得一模一样，事后极难清干净。
3. **只造假数据** —— 不要把任何真实的人写进代码库，名字用明显虚构的。

**必须造出来的场景**（B9 验收要用）：

- 三个 ministry（食物银行 / 报税志愿 / ESL）+ 各自的 leader 编制，且都有人在任
- **一个空缺编制**（`Position` 建了、没有在职 `Assignment`）—— 验收第 1 条要用
- **一个换过人的编制**（一行已结束的 `Assignment` + 一行在职的），且它下面挂着下属编制 ——
  验收"换人不动下属"要用
- 一个人占两个不同 ministry 的两个 `Position`，两个编制各有不同上级
- 一条跨 kind 的汇报线：执行总监编制（employee）→ 理事长编制（board）
- 一个未成年志愿者（有生日）+ 一个只作参照的紧急联系人
- 一场活动，同一个人两个角色、各自工时
- 一对同名同号的重复 Contact（专门留给验收时试合并）

---

## B9 · 验收

全过才算 Phase B 完成。

### 自动化

- [ ] `python manage.py test` 全绿，测试数 **≥ 27**（B0 实测基线），且一个都没被删
- [ ] **那 8 条 `bulk_create` 测试真的用了 `bulk_create`** —— 逐条扫一眼，
      任何一条改成走 `save()` 都会变成"全绿但什么也没验证"。
      快速自检：把某条约束从 `Lower("code")` 改回 `unique=True`，对应测试必须变红
- [ ] `python manage.py check` **零警告**
- [ ] `python manage.py makemigrations --check --dry-run` 报 "No changes detected"
- [ ] `ruff check .` 干净
- [ ] 两条守卫测试真的会红：临时写一句 `date.today()` 和一句
      `Contact.objects.all()` 当人员列表，跑测试确认变红，再删掉

### 约束真的在数据库里（不是"Django 以为建了"）

```bash
python manage.py dbshell
\d org_position
\d org_assignment
\d events_participation
\d contact_contact
\d contact_relationship
\d contact_relationshiptype
```

- [ ] `relationship_unique_unordered_pair` 是一条**表达式索引**，
      定义里能看到 `LEAST(...)` / `GREATEST(...)` / `COALESCE(...)`；
      **A7 那条 `(contact_a, contact_b, relationship_type, start_date)` 已经不在了**（是替换不是并存）
- [ ] `relationshiptype_name_a_to_b_ci_unique` 里能看到 `lower(btrim(...))`
- [ ] 所有 `code` 的唯一索引都是 `lower(code)` 形式，**没有**任何一张表还留着裸的 `unique=True`
      （`\d` 里看到 `UNIQUE (code)` 而不是 `UNIQUE (lower(code))` 就是漏了）
- [ ] `assignment_unique_tenure` 显示 `UNIQUE NULLS NOT DISTINCT`
- [ ] `participation_unique_per_role` 同上
- [ ] `contact_emergency_contact_is_not_self` / `..._has_a_relationship` 在
- [ ] `position_reports_to_is_not_self` 在
- [ ] `org_position.reports_to_id` 的外键是 **`ON DELETE NO ACTION`**（Django 的 `PROTECT`
      在应用层实现，`\d` 里看不到 `SET NULL` 就对了 —— 确认没写成 `CASCADE`）
- [ ] `Index(ministry, kind, is_active)` 在 `org_position` 上、
      `Index(position, end_date)` 在 `org_assignment` 上

### 肉眼跑通（自动化覆盖不到）

- [ ] 建一个 ministry，在它下面建几个 `Position`（一个 leader 位 + 若干 employee / volunteer 位），
      给其中一部分挂上在职的人，分组显示正确
      （**用词：Leaders / Employees / Volunteers，界面上不出现 "worker"**）
- [ ] **没人在任的那个编制出现在「空缺」里**，且照样显示它的 kind / ministry / 下属；
      把它 `is_active=False` 之后**从空缺列表里消失**（撤销 ≠ 空缺）
- [ ] **换人不动下属**：给一个有下属的编制换在任者（旧的填 `end_date`、新建一行 `Assignment`），
      确认下属编制的 `reports_to` **一个字节没改**，且旧任者的任职历史还在
      —— **这一条是本轮修订的全部意义，其余都过了它不过就是没做成**
- [ ] 录一个志愿者，填一个**系统里没有**的紧急联系人 → 自动建出 reference-only 记录并关联，
      关系必填
- [ ] 再录第二个志愿者，填**同名同号**的紧急联系人 → 表单**自动关联**到刚才那一条，
      不产生第二条 reference-only 记录
- [ ] 联系人列表默认**看不到** reference-only 记录，切换筛选器能看到
- [ ] 用 `seed_demo` 造的那对重复记录试一次合并，验证引用全部改指过去、`notes` 里有记录
- [ ] 同一个人建两个 `Assignment`、指向两个不同 `Position`、各有不同上级，
      其中一条汇报线跨 kind（employee 编制 → board 编制）
- [ ] 开一个活动，给**同一个人登记两个不同角色**、分别记工时，总工时对得上
- [ ] 一个有生日的未成年人参加活动 → 活动页能筛出未成年参与者并看到他的紧急联系电话
- [ ] 在王强页面录「parent of 小明」→ 小明页面能看到「child of 王强」
- [ ] **在小明页面选「小明 是 ___ 的儿子」+ 王强 → 存出的是 `(王强, 小明, parent of)`**，
      两侧显示都对（方向感知表单：两头都能录，不再有"必须从 A 侧录"这条规矩）
- [ ] 录一条**同名同号**的联系人 → **保存被打断**，出现"强制保存"复选框；
      勾上再存才进库。再录一条**只同名不同号**的 → 只出黄条警告，**不打断**
- [ ] 一个生日为空的参与者，在"未成年"筛选器里落进**"生日未知"**那一档，不是"成年"

### 收尾

- [ ] `goal.md` 的 Phase B 状态改成 ✅，「还没定的」那张表按实际答复更新
- [ ] 本文档末尾的「计划外记录」填上实施时才发现的事
- [ ] README 里补上新 app 的说明

---

## 分工与提交节奏

**B0–B4 是一串咬合的动作**（准备 → core → contact 三步收口），中间状态留在磁盘上
过夜容易忘记做到哪，建议一口气做完 B0–B3，再单独做 B4（它自己就有四个独立小块）。
**B5 / B6 / B7 / B8 彼此独立**，可以分开做、分开提交。

每个 B 步至少一个 commit；B4 建议四个（消歧 / 紧急联系人 / 查重合并 / 未成年人）。
**B5 二次修订后变大了，建议拆两个 commit**：`Ministry` + `EmploymentType` + `Position`（含
`vacant()` 和环的防线）一个，`Assignment` 一个 —— 前者是组织架构的骨架，
自己就能跑测试、自己就能在 admin 里看，不必等任职表。

**始终归你的**：B9 那一串肉眼验收、以及三个待答复问题的跟进。

---

## 三个待答复问题（都不阻塞开工）

| # | 问题 | 影响 | 没答复时怎么办 |
|---|---|---|---|
| 1 | 未成年志愿者有没有同意书 / 家长授权流程 | 决定 `Guardianship` 什么时候建 | 已移出 Phase B，本阶段完全绕开 |
| 2 | 背景审查有效期多长 | `BACKGROUND_CHECK_VALID_DAYS` | 用 730 天占位，`base.py` 里注明未确认 |
| 3 | `EmploymentType` 的实际取值 | 字典表里 seed 哪几行 | 正因为不知道才做成字典表；先只 seed 两行，到时候 admin 里加 |

---

## 计划外记录（实施时回来填）

`01-roadmap.md` 里最有价值的两段就是"⚠️ 计划外：迁移图会断"和
"⚠️ 计划外：admin 路径根本不经过 middleware" —— **写下来的坑比顺利完成的步骤值钱。**
这一段留白，遇到就往下写：

- （待填）
