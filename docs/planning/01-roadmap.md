# Phase A 实施手册 —— 地基加固

> 这份文档只讲 Phase A 怎么做。 要做什么、为什么这么定，全在 `goal.md` ——
> 那是唯一权威来源，本文与它冲突时以它为准。
>
> ✅ **Phase A 已于 2026-07-27 完成，本文不再更新，留作记录** ——
> 尤其是文末那两段「⚠️ 计划外」（迁移图会断、admin 路径不经过 middleware），
> 那是踩出来的，比顺利完成的步骤值钱。
> Phase B 的实施手册是 `02-roadmap.md`。
>
> 写于 2026-07-27，同日按 12 项决策修订过（见文末「本次修订记录了什么」）。
>
> ⚠️ **读的时候要换算两处**（本文档不改，按约定 2「旧的不覆盖、不删除」）：
> ① 文中的 **Phase C / Phase D 是 2026-07-29 对调前的编号**（那时 C = 资金、D = 上线），
>    现在 C = 上线与真实运营、D = 资金追踪；
> ② A7/A8 描述的是 **旧 D14**（约束 + `clean()` 两层 + 注释纪律）。
>    D14 已于 2026-07-28 重写成"规则只写在约束里 + `CONSTRAINT_FIELD` 映射 + 守卫测试"，
>    那段存量代码在 `02-roadmap.md` B1 里已经收编过了。
>
> **开工时的实测基线**（不是估计，是跑出来的）：
> - `contact` app 有 Contact / Relationship / RelationshipType / Language 四个模型 + admin
> - `python manage.py test` → **11 个测试，全绿**
> - `python manage.py check` → **3 条 W042 警告**（`DEFAULT_AUTO_FIELD` 没设）
> - 数据库是 SQLite，配置是 `startproject` 的默认单文件
> - Django 5.2.16 / Python 3.14.6 / `postgresql@18` 已装但 `brew services` 状态是 `none`
> - `.env` / `.env.example` 都不存在；旧 `SECRET_KEY` 在提交 `eceb26a` 里，已泄露

---

## 这一阶段要达成什么

跑在 Postgres 上、有自定义 User、配置可安全部署、共享代码归位、脏数据进不来、改动有据可查。

验收标准：不新增任何功能，原本合法的数据仍然全部能存得下，测试全绿。

> 注意这跟原来写的"功能上什么都没变"不是一回事 —— 那个说法和本阶段要做的事自相矛盾。
> 加数据库约束的全部意义就是让系统不再接受某些输入，所以 admin 里能做的事**必然**
> 少掉一些（原来能存"Alice 是 Alice 的母亲"，做完就存不了了）。
> **被约束挡掉的脏数据是唯一允许出现的行为变化**，除此之外任何"能做的事变了"都说明做多了。

## 为什么按这个顺序

所有会改变表结构的动作（删依赖、迁 `TimeStampedModel`、加 User、改主键类型）
**全部赶在切 Postgres 之前完成**，最后在一个全新的空库上一次性 `migrate`。
这样整个 Phase A 不需要写任何数据迁移，也不用处理 `AUTH_USER_MODEL` 换表的经典难题 ——
因为那张表还没建出来。

```
A1 基线 → A2 配置/环境变量/主键/时区 → A3 清依赖 → A4 core app → A5 accounts+User
        ↓
A6 建 Postgres 库并首次 migrate（这里才第一次碰数据库）
        ↓
A7 数据库约束 → A8 审计日志 → A9 README → A10 验收
```

**约束（A7）为什么排在切库之后**：`UniqueConstraint(nulls_distinct=False)` 在 SQLite 上
会被静默忽略，测试跑绿了也不能说明什么。必须在 Postgres 上写、在 Postgres 上验。

现有 `db.sqlite3` 里只有开发时的测试数据，**直接丢弃，不迁移**。

---

## A1 · 拉基线

开工前确认现在是绿的，否则后面分不清是新问题还是旧问题。

```bash
python manage.py test          # 应该是 11 个，全绿
git status                     # 应干净；不干净就先提交
git switch -c phase-a          # 在分支上做，随时能回退
```

基线数字：11 个测试。 A10 验收时对比。

留个念想就 `cp db.sqlite3 db.sqlite3.bak`。`.gitignore` 只忽略了 `db.sqlite3`，
不忽略 `.bak` —— **顺手把 `*.bak` 加进 `.gitignore`**，别指望自己记得。

---

## A2 · 配置拆分 + 环境变量 + 主键类型 + 时区

`config/settings.py` 现在是 `startproject` 的原样：`SECRET_KEY` 硬编码、`DEBUG = True`、
`ALLOWED_HOSTS = []`、没有 `STATIC_ROOT`、没有 `DEFAULT_AUTO_FIELD`。
而且那个 key **已经进了 git 历史，等于已泄露，作废**。

### 拆成一个 settings 包

```
config/settings/
├── __init__.py      # 空
├── base.py          # 共用的一切，敏感值从环境变量读
├── dev.py           # from .base import *  + 本机开发的覆盖
└── prod.py          # from .base import *  + 上线时的覆盖
```

删掉旧的 `config/settings.py`（内容搬进 `base.py`）。

### base.py 的关键改动

```python
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent   # 多了一层，注意
load_dotenv(BASE_DIR / ".env")

def env(key, default=None, required=False):
    value = os.getenv(key, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value

SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)
DEBUG = env("DJANGO_DEBUG", "False").lower() in {"1", "true", "yes"}
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"     # .gitignore 已有 /staticfiles/

# 决策 #3：现在库要重建，改主键类型是免费的；有数据之后要 ALTER 每张表的主键
# 和所有指向它的外键列。也消掉 manage.py check 现有的 3 条 W042 警告。
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 决策 #4：基金会在 Santa Clara, California。USE_TZ 保持 True，库里存的仍是 UTC，
# 这个设置只影响 admin 显示和「本月/本年」这类聚合的边界（Phase C 的报表要用）。
TIME_ZONE = "America/Los_Angeles"
```

> ⚠️ `BASE_DIR` 因为多了一级目录必须多一个 `.parent`，漏了的话 `.env` 和 `STATIC_ROOT`
> 会指到 `config/` 里去。这是拆配置时最常见的一个坑。

`DEBUG` 默认 `False`：忘了配环境变量时坏在安全的一侧，而不是把调试页面暴露到公网。

`dev.py` 里覆盖成开发友好的值（`DEBUG = True`、`ALLOWED_HOSTS = ["localhost", "127.0.0.1"]`），
`prod.py` 留着放 Phase D 的安全设置（现在可以先只 `from .base import *`）。

### 主键类型的迁移

设完 `DEFAULT_AUTO_FIELD` 立刻生成迁移：

```bash
python manage.py makemigrations contact    # 预期 3 个 AlterField：Contact / Relationship / RelationshipType
```

`Language` 用 `code` 当主键，不受影响 —— 如果它也出现在迁移里，说明哪里搞错了。
这个迁移此刻不用 apply（还在 SQLite 上，反正 A6 要丢），A6 在空库上一次性跑掉。

### 切换入口

`manage.py`、`config/wsgi.py`、`config/asgi.py` 里的
`DJANGO_SETTINGS_MODULE` 从 `"config.settings"` 改成 `"config.settings.dev"`
（生产环境用环境变量指到 `config.settings.prod`）。三个文件都要改，漏一个就是启动时报错。

### 生成新 key、写 .env

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

`.env`（**不进 git**，`.gitignore` 已有）：

```
DJANGO_SECRET_KEY=<上面生成的新 key>
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=postgres://<你的用户名>@localhost:5432/rolf_dev
```

> **这把是开发用的 key**（决策 #8）。上线用的是另一把，Phase D 时在部署平台上生成、
> 只存在平台的环境变量里、永远不落盘、也不进任何文件。两把不要混用。

`.env.example`（**进 git**）：同样的键，值留空或写占位符 + 一行注释说明怎么生成 key。
这是新人（含半年后的你）能把项目跑起来的关键。

**验证**：`python manage.py check` 通过且**没有 W042 警告了**；把 `.env` 临时改名，
`check` 应该因为缺 `DJANGO_SECRET_KEY` 而明确报错 —— 报错说明必填校验真的生效了。

---

## A3 · 清掉没用的依赖

`countries_plus` / `languages_plus` 在库里各建了几千行的表，而我们按 D8 自建了 `Language`
（`django-countries` 和 `localflavor` 是**在用的**，别删错 —— 名字很像）。

`base.py` 的 `INSTALLED_APPS` 删掉：

```python
'countries_plus',      # 删
'languages_plus',      # 删
```

卸载并同步 requirements。`django-countries-plus` 会带进来 `requests` 那条依赖链
（`requests` / `certifi` / `idna` / `urllib3` / `charset-normalizer`），一并清掉：

```bash
pip uninstall -y django-countries-plus django-languages-plus requests certifi idna urllib3 charset-normalizer
pip freeze > requirements.txt
```

> 直接删传递依赖是有风险的习惯 —— 万一别的包也需要 `requests` 就删坏了。
> 这次安全（当前 `requirements.txt` 里没有别的包依赖它们），但删完**必须**跑
> `pip check` 确认没有断掉的依赖，别省这一步。

**必须留着的**：`pycountry`（seed 迁移靠它灌 7900 行语言数据）、
`python-stdnum`（`localflavor` 的依赖）。

### ⚠️ 计划外：迁移图会断（实施时才发现）

删掉 `languages_plus` 之后 `manage.py check` **仍然能过**，但 `test` / `migrate` 一跑就：

```
NodeNotFoundError: Migration contact.0001_initial dependencies reference
nonexistent parent node ('languages_plus', '0004_auto_20171214_0004')
```

原因：迁移历史把 D8 的决策过程固化进去了 ——
`0001` 的 `preferred_language` 原本指向 `languages_plus.Language`，
`0002` 才建自己的 `Language`、`0004` 再把外键改指过来。删掉那个 app，`0001` 的依赖就悬空了。

处理（2026-07-27 决策）：重建迁移历史。 库反正要丢，零风险：

```bash
cp contact/migrations/0003_seed_languages.py <安全的地方>   # 手写的，必须保住
rm contact/migrations/000*.py
python manage.py makemigrations contact                    # 生成干净的 0001_initial
cp <安全的地方>/0003_seed_languages.py contact/migrations/0002_seed_languages.py
# 把 seed 迁移里的 dependencies 改成 ("contact", "0001_initial")
```

结果是两个迁移：`0001_initial` + `0002_seed_languages`，零 `languages_plus` 痕迹，
`BigAutoField` 从一开始就在（A2 那个 `0005` AlterField 也随之消失，被折进 initial）。

**代价**：迁移历史里看不到「先用现成包、后改自建表」那段转折了。可接受 ——
`goal.md` D8 本来就是记录这件事的权威位置，迁移文件不是。

> 重建之后 `showmigrations` 会把 `0001_initial` 显示成已应用 `[X]` ——
> 那是旧 sqlite 库里按名字残留的记录，不是真的。A6 删库后自然消失，不用管。

**验证**：`pip check` 干净；`check` 无警告；`makemigrations --check` 报 "No changes detected"；
`test` 依然 11 个全绿（语言那几个测试通过 = seed 迁移仍在工作）。

---

## A4 · 建 `core` app，`TimeStampedModel` 搬家

现在 `TimeStampedModel` 住在 `contact/models.py:7`。下一个 app 要用它就得
`from contact.models import TimeStampedModel` —— 依赖方向反了。

```bash
python manage.py startapp core
```

- `core/models.py` 放 `TimeStampedModel`（原样搬过去，连 docstring 一起）。
- `contact/models.py` 删掉本地定义，改成 `from core.models import TimeStampedModel`。
- `INSTALLED_APPS` 加 `'core'`，**放在 `'contact'` 前面**（读的时候依赖方向一目了然）。
- `core/views.py` 用不上，可以留空。

> 不会产生迁移。 `TimeStampedModel` 是抽象基类，换个地方定义不改变 `Contact` /
> `Relationship` 的任何字段。此时 `makemigrations` 应该只有 A2 那个主键迁移，不该多出新的。
> 如果它检测到字段改动，说明搬的时候定义被改了 —— 回去核对。

顺手在 `core/tests.py` 放一个**全项目级的迁移守卫**（决策 #9）：

```python
class NoMissingMigrationsTests(TestCase):
    def test_no_model_changes_are_missing_a_migration(self):
        # makemigrations --check --dry-run 非零退出 = 有模型改动没生成迁移。
        # 放在 core 是因为它管的是整个项目，不属于任何单个 app。
        call_command("makemigrations", "--check", "--dry-run", verbosity=0)
```

这条测试以后每个 Phase 都在替你干活：忘了 `makemigrations` 会当场变成红色，
而不是等部署时才炸。

以后所有 app 共享的抽象基类都放 `core`。

---

## A5 · 建 `accounts` app + 自定义 User（按 D12）

**这是整个 Phase A 最紧急的一件事**，一旦库里有真实用户就极难改。

```bash
python manage.py startapp accounts
```

`accounts/models.py`：

```python
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """登录账号。与 Contact 是可选的一对一（见 goal.md D12）。

    contact 可空是刻意的：superuser 这类纯技术账号不对应任何真人，
    也不是每个员工都需要登录账号。
    """

    contact = models.OneToOneField(
        "contact.Contact",
        on_delete=models.SET_NULL,     # 删联系人不应该连账号一起删掉
        null=True, blank=True,
        related_name="user",
    )
```

`base.py` 加：

```python
INSTALLED_APPS = [..., 'accounts', 'core', 'contact']
AUTH_USER_MODEL = "accounts.User"
```

`accounts/admin.py` 注册一下，用 Django 自带的 `UserAdmin` 起步，把 `contact` 加进
fieldsets 并设成 autocomplete（`ContactAdmin` 已有 `search_fields`，能直接支持）。

**按 D12 记住三件事**：
- `contact` **可空** —— 见上面 docstring 的理由；
- User 挂在 `Contact` 上而**不是** `Assignment` 上 —— 一个人可以多岗，但只该有一个账号；
- 权限一律走 Django Group，**不要**加 `is_employee` 这类字段来判断权限。

现在**不加**其他字段。这一步的全部意义是"趁没数据把这个开关占住"，字段以后随时能加。

```bash
python manage.py makemigrations accounts    # 不碰数据库，安全
```

### 测试（决策 #9）

原来这一步没有任何测试，违反了 `goal.md` 自己定的"新增模型时一并补测试是硬要求" ——
而且偏偏是最紧急、最不能出错的一步。`accounts/tests.py` 写三条：

```python
def test_auth_user_model_points_at_accounts_user(self):
    # get_user_model() 是 accounts.User。这条钉住的是「AUTH_USER_MODEL 真的接上了」，
    # 配置写漏了会当场红。

def test_superuser_can_be_created_without_a_contact(self):
    # create_superuser(...) 不传 contact 能建成，且 user.contact is None。
    # 这正是 D12 要求 contact 可空的那个场景 —— 纯技术账号不对应任何真人。

def test_user_can_be_linked_to_a_contact(self):
    # 建个 Contact，挂上去，contact.user 反查得到。验的是 related_name。
```

---

## A6 · 切到 Postgres，在空库上一次性建表

前面所有结构改动都做完了，现在才第一次碰数据库。

### 起服务、建库

```bash
brew services start postgresql@18      # 现在是 none，没在跑
createdb rolf_dev
psql -d rolf_dev -c "select version();"   # 确认能连上，且确认是 18
```

### 驱动：换 psycopg 3（决策 #6）

`psycopg2-binary` 已经装着能用，但 psycopg 3 是 Django 5.2 的推荐驱动、PG 18 下性能更好，
而 psycopg2 已进入维护模式。**现在换成本为零**（库还没建、代码一行不改）；
以后换要重跑一遍全部测试。

```bash
pip uninstall -y psycopg2-binary && pip install "psycopg[binary]" && pip freeze > requirements.txt
```

### DATABASES 从环境变量读（决策 #7）

```bash
pip install dj-database-url && pip freeze > requirements.txt
```

`base.py`：

```python
import dj_database_url

# 单个 DATABASE_URL 搞定。Render / Fly.io 都直接注入这个变量，
# Phase D 部署时这里一行都不用改（见 goal.md D3 / Phase D）。
DATABASES = {"default": dj_database_url.parse(env("DATABASE_URL", required=True))}
```

### 首次迁移

```bash
rm db.sqlite3                      # A1 已经 cp 过 .bak 了
python manage.py migrate           # 全部 app 一次性建表，含 7900 行语言 seed
python manage.py createsuperuser
python manage.py test
```

这一步必须全绿才能继续。 SQLite 和 Postgres 在约束、大小写敏感、并发上行为不同，
测试在 Postgres 下跑通才算数 —— 这正是现在切而不是三个月后切的原因。

> ⏱ 从这里开始每次跑测试都会重灌那 7900 行语言（数据迁移 `0003`），比 SQLite 上慢得多。
> 日常开发用 `python manage.py test --keepdb` 复用测试库。
> **测试变慢是"有测试所以敢改"最常见的死法**，A9 的 README 里要写上这一条。

顺手起服务确认 admin 能开、Contact 能存、语言下拉里 English/Mandarin/Cantonese 排在最前：

```bash
python manage.py runserver
```

---

## A7 · 数据库约束（按 D9 + D14）

这一步是 D9 修订的兑现。 D9 原以为"名字必须匹配 contact_type"的规则已经生效，
其实 `save()` 不调 `clean()` —— 脚本、`bulk_create`、`queryset.update()` 一直能绕过去。
约束落到数据库层，所有写入路径就都绕不过了。

按 **D14** 每条规则写两层：**约束负责强制，`clean()` 负责给人话提示**，
**两处都写注释指认对方**。

### Contact：姓名必须匹配类型（决策 #1）

```python
class Meta:
    ordering = ["legal_last_name", "legal_first_name"]
    constraints = [
        # ⚠️ 这条规则同时写在 Contact.clean() 里（见 D14）。clean() 只负责把错误
        # 挂到具体字段上给人话提示，真正的强制在这里。改一处必须改另一处。
        models.CheckConstraint(
            condition=(
                (models.Q(contact_type="individual") & ~models.Q(legal_last_name=""))
                | (models.Q(contact_type="organization") & ~models.Q(organization_name=""))
            ),
            name="contact_name_matches_type",
            violation_error_message="个人必须填法定姓氏，机构必须填机构名。",
        ),
    ]
```

> 坑：Meta 里不能写 `ContactType.INDIVIDUAL`。 嵌套类的类体拿不到外层类的命名空间，
> 会直接 `NameError`。只能写字面量 `"individual"` / `"organization"` ——
> 这意味着取值字符串又多了一处副本，**注释里要说明**，跟 `TextChoices` 的定义对上。

`Contact.clean()` 已经实现了同一条规则，不用改逻辑，**只加一句注释指回约束**。

### Relationship：三条约束（决策 #2）

```python
class Meta:
    indexes = [...]                     # 保留现有的两个
    constraints = [
        models.CheckConstraint(
            condition=~models.Q(contact_a=models.F("contact_b")),
            name="relationship_no_self_reference",
            violation_error_message="一个联系人不能和自己建立关系。",
        ),
        models.UniqueConstraint(
            fields=["contact_a", "contact_b", "relationship_type", "start_date"],
            name="relationship_unique_per_type_and_start",
            nulls_distinct=False,
            violation_error_message="这条关系已经记录过了。",
        ),
        models.CheckConstraint(
            condition=(
                models.Q(end_date__isnull=True)
                | models.Q(start_date__isnull=True)
                | models.Q(end_date__gte=models.F("start_date"))
            ),
            name="relationship_end_date_not_before_start_date",
            violation_error_message="结束日期不能早于开始日期。",
        ),
    ]
```

三个细节：

- `nulls_distinct=False` 不能省。 Postgres 默认认为 `NULL != NULL`，而 `start_date`
  是可空的 —— 不加这个参数，两条起始日期都为空的相同关系照样能重复插入，约束等于白加。
  PG 15+ 支持，我们是 18，没问题。**这也是 A7 必须排在切库之后的原因**：
  SQLite 上这个参数会被静默忽略。
- Django 5.1 起 `CheckConstraint` 的参数叫 `condition`（旧的 `check` 已废弃）。
- 日期约束里的两个 `isnull` 分支在 SQL 层其实可以省（`NULL >= NULL` 是 NULL，
  CHECK 遇到 NULL 视为通过），但写出来更自明，也省得以后有人怀疑它会不会误伤空值。

**明确不做的**：镜像重复 —— `(Alice, Bob, 'parent of')` 和 `(Bob, Alice, 'child of')`
是同一件事存两遍，唯一约束挡不住（决策 #2 选择不处理）。
数据库表达不了这个，真要管只能写在 `clean()` 里。记在这里是为了以后别以为已经解决了。

### `clean()` 提示层

给 `Relationship` 加一个 `clean()`，做和上面三条约束相同的检查，把错误挂到具体字段上
（`contact_b` / `end_date`），每条都注释指回对应的约束名。

> Django 4.1 起 `full_clean()` 会自动校验约束，所以就算不写 `clean()`，
> admin 也**不会**抛 `IntegrityError`，只是提示挂在表单顶部、措辞是给程序员看的。
> 写 `clean()` 纯粹为了体验，不承担兜底 —— 这是 D14 的原话，别把它当安全网。

### 测试（决策 #9）

`contact/tests.py` 补五条：

```python
# Contact 姓名约束 —— 钉住的正是 D9 原来漏掉的那条路
def test_create_bypassing_full_clean_still_cannot_break_the_name_rule(self):
    # Contact.objects.create(contact_type="individual") 不填姓氏 → IntegrityError。
    # 这是修订前能存进去的那条路径。
def test_organization_without_a_name_is_rejected_at_the_database(self):

# Relationship 三条约束
def test_cannot_relate_a_contact_to_itself(self):
def test_cannot_store_the_same_relationship_twice(self):
    # 同一对 contact + 同一 type + 同为空的 start_date，第二次应失败。
    # 这条专门验 nulls_distinct=False 真的生效了。
def test_end_date_cannot_be_before_start_date(self):
```

> 测数据库约束时记得 `from django.db import transaction` 并用
> `with self.assertRaises(IntegrityError), transaction.atomic():` —— 少了 `atomic()`
> 事务会被标记为中止，同一个测试里后面的查询全会报错。
>
> ✅ **未决点已验证（实施时实测）**：Django 的 Python 侧 `validate_constraints()`
> **认** `nulls_distinct=False`，行为与数据库一致 —— `full_clean()` 会拦下
> `start_date` 同为空的重复行，且用的就是约束上写的 `violation_error_message`。
> 所以 `clean()` 里**不需要**再手写一遍重复检查（也确实没写）。
>
> 另外在 psql 里核对了四条约束真的落地了，不是「Django 以为建了」：
> `\d` 显示 `UNIQUE NULLS NOT DISTINCT (contact_a_id, contact_b_id, relationship_type_id, start_date)`。

---

## A8 · 审计日志

"谁在什么时候改了这条记录"在基金会场景下是刚需，也是我们自己认定"值得抄"的一条。
一个 decorator 的成本换完整修改历史。

```bash
pip install django-simple-history && pip freeze > requirements.txt
```

- `INSTALLED_APPS` 加 `'simple_history'`；
- `MIDDLEWARE` 加 `'simple_history.middleware.HistoryRequestMiddleware'`，
  按库文档放在 `AuthenticationMiddleware` 之后；
- `Contact` 加 `history = HistoricalRecords()`，并让 `ContactAdmin` 继承
  `SimpleHistoryAdmin`（admin 里就多出一个 History 按钮）；
- `makemigrations` + `migrate`。

> 这一步必须排在 A5 之后：`HistoricalRecords` 的 `history_user` 是指向
> `AUTH_USER_MODEL` 的外键，User 得先存在。

现在只挂 `Contact`。`Assignment`（Phase B）和 `Contribution`（Phase C）按 `goal.md` 是**必挂**的，
到时候别忘。`Language` 这种字典表不用挂。

### 测试（决策 #9）

### ⚠️ 计划外：admin 路径根本不经过 middleware（实施时才发现）

最初只写了一条「改一条 Contact，`history_user` 记到人」的测试，走的是 admin。
**把 middleware 整条删掉，这个测试照样绿** —— 因为
`simple_history/admin.py:317` 直接做了 `obj._history_user = request.user`，
`SimpleHistoryAdmin` 自己就把人填上了，压根不查 thread-local。

也就是说：**middleware 覆盖的是 admin 以外的所有保存路径** ——
Phase C 的 HTMX 页面、以后的 API、任何自己写的 view。只测 admin 等于没测 middleware。

最终写了四条：

```python
# 走 admin（不经过 middleware，测的是 SimpleHistoryAdmin）
def test_editing_a_contact_records_the_previous_value(self):
def test_editing_through_the_admin_records_who_changed_it(self):

# 走真实中间件栈：用 override_settings(ROOT_URLCONF=...) 挂一个测试专用的
# 非 admin view，在请求里保存 Contact
def test_saving_during_a_non_admin_request_records_the_user(self):
def test_the_middleware_is_installed(self):
```

后两条已验证：删掉 middleware 后会红（`AssertionError: None != <User: coordinator>`）。

> **顺带纠正一个流传的说法**：常说 `HistoryRequestMiddleware` "必须放在
> `AuthenticationMiddleware` 之后，否则记不到是谁改的"。**实测排序不影响结果** ——
> 这个 middleware 只是把 request 对象存进 thread-local，`request.user` 是保存时才读的，
> 那时 `AuthenticationMiddleware` 已经在同一个对象上填好了。
> 把它挪到 Auth 之前，历史测试照样全绿。按文档顺序放是因为没有代价，不是因为放错会坏。

**另外肉眼验一次**：admin 里改一条 Contact，History 页面能看到改了什么、谁改的、什么时候。

---

## A9 · 写 README.md

删掉空的 `READ.md`（顺便，这个文件名本身就是打错的）。`README.md` 至少要能让人照着跑起来：

1. 前置条件（Python 3.14、`postgresql@18`）
2. 建虚拟环境、`pip install -r requirements.txt`
3. `brew services start postgresql@18` / `createdb rolf_dev`
4. `cp .env.example .env`，怎么生成 `DJANGO_SECRET_KEY`
5. `python manage.py migrate` / `createsuperuser` / `runserver`
6. `python manage.py test`，**并说明日常用 `--keepdb`**（否则每次重灌 7900 行语言，
   慢到你开始不想跑测试）
7. 一段话说明项目是什么，并指向 `docs/planning/goal.md`

标准是：照着做能从零跑起来，不用问任何人。

---

## A10 · 验收

逐条确认，全过才算 Phase A 完成：

### 测试（决策 #10）

原来这条写的是"数量 = 基线 + 2"，一个精确数字会卡住自己（每加一个测试都要改文档），
而且信息量还不如列清单。改成 **只增不减 + 逐项列出**：

- [ ] `python manage.py test` 全绿，测试数 **≥ 11**（A1 实测基线），且一个都没被删
- [ ] 新增的测试逐条对得上，每条钉住的东西如下：

| 测试 | 钉住什么 | 来自 |
|------|---------|------|
| `test_auth_user_model_points_at_accounts_user` | `AUTH_USER_MODEL` 真的接上了 | A5 |
| `test_superuser_can_be_created_without_a_contact` | D12 要求的 `contact` 可空 | A5 |
| `test_user_can_be_linked_to_a_contact` | `related_name` 反查通 | A5 |
| `test_no_model_changes_are_missing_a_migration` | 忘了 `makemigrations` 会当场红 | A4 |
| `test_create_bypassing_full_clean_still_cannot_break_the_name_rule` | D9 原来漏掉的那条路 | A7 |
| `test_organization_without_a_name_is_rejected_at_the_database` | 同上，机构侧 | A7 |
| `test_cannot_relate_a_contact_to_itself` | 自我关系挡得住 | A7 |
| `test_cannot_store_the_same_relationship_twice` | `nulls_distinct=False` 真的生效 | A7 |
| `test_end_date_cannot_be_before_start_date` | 日期倒挂挡得住 | A7 |
| `test_editing_a_contact_records_who_changed_it` | middleware 顺序对，记得到"谁" | A8 |

### 其余

- [ ] `python manage.py check` **零警告**（包括 A2 之前那 3 条 W042）
- [ ] 数据库是 Postgres（`python manage.py dbshell` 进的是 psql），驱动是 psycopg 3
- [ ] `grep -rn "django-insecure" config/` 没有结果；`.env` 不在 `git status` 里；`.env.example` 在 git 里
- [ ] 空 `.env` 时启动会明确报"缺少 DJANGO_SECRET_KEY"，而不是用某个默认值悄悄跑起来
- [ ] `AUTH_USER_MODEL = "accounts.User"`，`User.contact` 可空
- [ ] `TimeStampedModel` 在 `core`，`contact` 从 `core` 引入
- [ ] `INSTALLED_APPS` 和 requirements 里都没有 `countries_plus` / `languages_plus`；`pip check` 干净
- [ ] `DEFAULT_AUTO_FIELD` 是 `BigAutoField`，`TIME_ZONE` 是 `America/Los_Angeles`
- [ ] **D14 的注释纪律真的执行了** —— 每条约束和对应的 `clean()` 两处都有注释指认对方
- [ ] admin 里改一条 Contact 能看到 History，且显示了是谁改的

### 肉眼验（自动化测试覆盖不到）

- [ ] admin 里按 contact_type 切换，无关的名字字段会隐藏（`contact_type_toggle.js`）
- [ ] 国家选 US 时州变成下拉、选别的国家时变成文本框（`address_state_toggle.js`）
- [ ] 语言下拉里 English / Mandarin / Cantonese 排在最前

### 行为变化确认

- [ ] **没有新增任何功能**
- [ ] **原本合法的数据仍然全部能存得下** —— admin 里能录的合法记录一条没少
- [ ] 唯一少掉的是脏数据：自我关系、重复关系、日期倒挂、没有姓名的联系人

做完回 `goal.md` 把 Phase A 那张表的状态改成 ✅，然后把本文档换成 Phase B 的实施步骤。

---

## 分工与提交节奏（决策 #12）

A1–A6 由 Claude 做完，停下来交给你验收，通过后再继续 A7–A9。

A1–A6 是一串咬合的动作（配置 → 依赖 → 结构 → 切库），中间状态（比如改了
`AUTH_USER_MODEL` 但还没建库）留在磁盘上过夜容易忘记做到哪，所以一口气做完。
分多个 commit 提交，方便你逐个 review。

A7 / A8 / A9 彼此独立，可以分开做、分开提交。

**始终归你的**：`.env` 里生产密钥的保管（Phase D）、上面「肉眼验」那三条。

---

## 本次修订记录了什么

2026-07-27 按 12 项决策修订。相对初版的实质改动：

| # | 改了什么 | 为什么 |
|---|---------|-------|
| 1 | A7 增加 `Contact` 姓名 `CheckConstraint` | D9 原来是**假生效**的，`save()` 不调 `clean()` |
| 2 | A7 增加 `end_date >= start_date`；明确**不做**镜像重复 | 日期倒挂便宜且以后加要洗数据；镜像重复数据库表达不了 |
| 3 | A2 增加 `DEFAULT_AUTO_FIELD` | 原计划漏了，导致 A10 的"check 无警告"当时根本过不了 |
| 4 | A2 增加 `TIME_ZONE` | Phase B/C 要用；但它不满足 Phase A 准入标准，是"顺手"进来的 |
| 5 | A7 明确约束 + `clean()` 两层 + 注释纪律 | 新增 D14；这是明知故犯违反"一件事记一处"，靠纪律兜住 |
| 6 | A6 拍板换 psycopg 3 | 原文"嫌麻烦就留着"没拍板 |
| 7 | A6 拍板用 `dj-database-url` | 同上；且 Phase D 部署时零改动 |
| 8 | A2 明确开发 key 与生产 key 是两把 | 原文没区分 |
| 9 | A4/A5/A7/A8 全部补测试 | 原 A5 建了 User 模型却零测试，是 roadmap 违反 goal.md |
| 10 | A10 验收从"精确数字"改成"只增不减 + 清单" | 精确数字会卡住自己，且信息量更低 |
| 11 | 基线数字从"106 行测试"改成实测的"11 个测试" | 行数不是可验收的量 |
| 12 | 验收口径从"功能上什么都没变"改成允许脏数据被挡 | 原口径与"加约束"这件事**自相矛盾** |
