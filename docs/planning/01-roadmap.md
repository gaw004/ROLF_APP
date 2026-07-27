# Phase A 实施手册 —— 地基加固

> **这份文档只讲 Phase A 怎么做。** 要做什么、为什么这么定，全在 `goal.md` ——
> 那是唯一权威来源，本文与它冲突时以它为准。Phase B/C/D 的内容也在 `goal.md`，
> 等各自开工前再回来把本文换成那个 Phase 的实施步骤。
>
> 写于 2026-07-27。当时状态：`contact` app 有 Contact / Relationship / RelationshipType /
> Language 四个模型 + admin + 106 行测试，数据库还是 SQLite，配置是 `startproject` 的默认单文件。

---

## 这一阶段要达成什么

跑在 Postgres 上、有自定义 User、配置可安全部署、共享代码归位、脏数据进不来、改动有据可查。

**验收标准：功能上什么都没变，测试全绿。** 这一阶段不加任何业务功能 ——
如果做完之后 admin 里能做的事和做之前不一样，说明做多了。

## 为什么按这个顺序

所有会改变表结构的动作（删依赖、迁 `TimeStampedModel`、加 User、加约束、加审计）
**全部赶在切 Postgres 之前完成**，最后在一个全新的空库上一次性 `migrate`。
这样整个 Phase A 不需要写任何数据迁移，也不用处理 `AUTH_USER_MODEL` 换表的经典难题 ——
因为那张表还没建出来。

```
A1 基线      → A2 配置/环境变量 → A3 清依赖 → A4 core app → A5 accounts+User
           ↓
A6 建 Postgres 库并首次 migrate（这里才第一次碰数据库）
           ↓
A7 Relationship 约束 → A8 审计日志 → A9 README → A10 验收
```

现有 `db.sqlite3` 里只有开发时的测试数据，**直接丢弃，不迁移**。
真要留个念想就先 `cp db.sqlite3 db.sqlite3.bak`（`.gitignore` 已忽略 `db.sqlite3`，
`.bak` 记得别提交）。

---

## A1 · 拉基线

开工前确认现在是绿的，否则后面分不清是新问题还是旧问题。

```bash
python manage.py test          # 应全绿
git status                     # 应干净；不干净就先提交
git switch -c phase-a          # 在分支上做，随时能回退
```

把当前测试数量记下来（现在是 `contact/tests.py` 里的那几个），A10 验收时对比。

---

## A2 · 配置拆分 + 环境变量 + 换 SECRET_KEY

`config/settings.py` 现在是 `startproject` 的原样：`SECRET_KEY` 硬编码、`DEBUG = True`、
`ALLOWED_HOSTS = []`、没有 `STATIC_ROOT`。而且那个 key **已经进了 git 历史，等于已泄露，作废**。

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
```

> ⚠️ `BASE_DIR` 因为多了一级目录必须多一个 `.parent`，漏了的话 `.env` 和 `STATIC_ROOT`
> 会指到 `config/` 里去。这是拆配置时最常见的一个坑。

`DEBUG` 默认 `False`：忘了配环境变量时坏在安全的一侧，而不是把调试页面暴露到公网。

`dev.py` 里覆盖成开发友好的值（`DEBUG = True`、`ALLOWED_HOSTS = ["localhost", "127.0.0.1"]`），
`prod.py` 留着放 Phase D 的安全设置（现在可以先只 `from .base import *`）。

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

`.env.example`（**进 git**）：同样的键，值留空或写占位符 + 一行注释说明怎么生成 key。
这是新人（含半年后的你）能把项目跑起来的关键。

**验证**：`python manage.py check` 通过；把 `.env` 临时改名，`check` 应该因为缺
`DJANGO_SECRET_KEY` 而明确报错 —— 报错说明必填校验真的生效了。

---

## A3 · 清掉没用的依赖

`countries_plus` / `languages_plus` 在库里各建了几千行的表，而我们按 D8 自建了 `Language`
（`django-countries` 和 `localflavor` 是**在用的**，别删错）。

`base.py` 的 `INSTALLED_APPS` 删掉：

```python
'countries_plus',      # 删
'languages_plus',      # 删
```

卸载并同步 requirements。注意 `django-countries-plus` 会带进来 `requests` 这条依赖链
（`requests` / `certifi` / `idna` / `urllib3` / `charset-normalizer`），一并清掉：

```bash
pip uninstall -y django-countries-plus django-languages-plus requests certifi idna urllib3 charset-normalizer
pip freeze > requirements.txt
```

**别手改 requirements.txt** —— 用 `pip freeze` 重新生成，避免删漏或删多。

`pycountry` **必须留着**：`contact/migrations/0003_seed_languages.py` 靠它灌 7900 行语言数据。
`python-stdnum` 也留着，是 `localflavor` 的依赖。

**验证**：`python manage.py check` 和 `python manage.py test` 依然通过
（此时还在 SQLite 上，测试用的是临时库，不受影响）。

---

## A4 · 建 `core` app，`TimeStampedModel` 搬家

现在 `TimeStampedModel` 住在 `contact/models.py:7`。下一个 app 要用它就得
`from contact.models import TimeStampedModel` —— 依赖方向反了。

```bash
python manage.py startapp core
```

- 删掉 `core/` 下用不上的 `views.py`、`admin.py`、`tests.py` 可留空。
- `core/models.py` 放 `TimeStampedModel`（原样搬过去，连 docstring 一起）。
- `contact/models.py` 删掉本地定义，改成 `from core.models import TimeStampedModel`。
- `INSTALLED_APPS` 加 `'core'`，**放在 `'contact'` 前面**（读的时候依赖方向一目了然）。

> **不会产生迁移。** `TimeStampedModel` 是抽象基类，换个地方定义不改变 `Contact` /
> `Relationship` 的任何字段，`makemigrations` 应该输出 "No changes detected"。
> 如果它检测到改动，说明搬的时候字段定义被改了 —— 回去核对。

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

生成迁移（此时还没有数据库连接的问题，`makemigrations` 不碰库）：

```bash
python manage.py makemigrations accounts
```

---

## A6 · 切到 Postgres，在空库上一次性建表

前面所有结构改动都做完了，现在才第一次碰数据库。

### 起服务、建库

```bash
brew services start postgresql@18      # 现在是 none，没在跑
createdb rolf_dev
psql -d rolf_dev -c "select version();"   # 确认能连上
```

### 驱动

`psycopg2-binary` 已经装着，能用。但 Django 5.2 + PG 18 建议用 psycopg 3
（性能更好、是 Django 现在的推荐驱动），换的成本就是一条命令、不改任何代码：

```bash
pip uninstall -y psycopg2-binary && pip install "psycopg[binary]" && pip freeze > requirements.txt
```

嫌麻烦就留着 psycopg2-binary，不影响 Phase A 的任何验收。

### DATABASES 从环境变量读

`base.py`：

```python
DATABASES = {"default": dj_database_url.parse(env("DATABASE_URL", required=True))}
```

`dj-database-url` 需要 `pip install dj-database-url`（一行搞定 URL 解析，且 Render / Fly.io
都直接提供 `DATABASE_URL`，Phase D 部署时零改动）。不想加这个依赖就手写
`ENGINE` / `NAME` / `USER` / `HOST` / `PORT` 各读一个环境变量，效果一样，只是 `.env` 长一点。

### 首次迁移

```bash
rm db.sqlite3                      # 或先 cp 一份 .bak
python manage.py migrate           # 全部 app 一次性建表，含 7900 行语言 seed
python manage.py createsuperuser
python manage.py test
```

**这一步必须全绿才能继续。** SQLite 和 Postgres 在约束、大小写敏感、并发上行为不同，
测试在 Postgres 下跑通才算数 —— 这正是现在切而不是三个月后切的原因。

顺手起服务确认 admin 能开、Contact 能存、语言下拉里 English/Mandarin/Cantonese 排在最前：

```bash
python manage.py runserver
```

---

## A7 · 给 `Relationship` 加数据库约束

现在可以存"Alice 是 Alice 的母亲"，也可以把同一段关系重复存 10 遍。约束放在数据库层，
脏数据就永远进不来 —— 无论是从 admin、脚本还是以后的 API。

`contact/models.py` 的 `Relationship.Meta`：

```python
class Meta:
    indexes = [...]                     # 保留现有的两个
    constraints = [
        models.CheckConstraint(
            condition=~models.Q(contact_a=models.F("contact_b")),
            name="relationship_no_self_reference",
        ),
        models.UniqueConstraint(
            fields=["contact_a", "contact_b", "relationship_type", "start_date"],
            name="relationship_unique_per_type_and_start",
            nulls_distinct=False,
        ),
    ]
```

两个细节：

- **`nulls_distinct=False` 不能省。** Postgres 默认认为 `NULL != NULL`，而 `start_date`
  是可空的 —— 不加这个参数，两条起始日期都为空的相同关系照样能重复插入，约束等于白加。
  PG 15+ 支持，我们是 18，没问题。
- Django 5.1 起 `CheckConstraint` 的参数叫 `condition`（旧的 `check` 已废弃）。

同时补 `Contact.clean()` 之外的 model 层校验？**不必** —— 这条规则数据库能完整表达，
按 D9 的精神，能落到数据库的约束就落到数据库，比 Python 校验更兜底。
但 admin 里保存时会抛 `IntegrityError` 而不是友好的表单错误，所以在 `Relationship`
上加一个 `clean()` 做同样的检查、给出人话提示是值得的（两层都要，不是二选一）。

`makemigrations` 后，在 `contact/tests.py` 补两个测试：

```python
def test_cannot_relate_a_contact_to_itself(self):
    # 期望 IntegrityError（用 transaction.atomic 包住，否则后续断言会因事务中止而失败）

def test_cannot_store_the_same_relationship_twice(self):
    # 同一对 contact + 同一 type + 同为空的 start_date，第二次应失败
```

> 测数据库约束时记得 `from django.db import transaction` 并用
> `with self.assertRaises(IntegrityError), transaction.atomic():` —— 少了 `atomic()`
> 事务会被标记为中止，同一个测试里后面的查询全会报错。

---

## A8 · 审计日志

"谁在什么时候改了这条记录"在基金会场景下是刚需，也是我们自己认定"值得抄"的一条。
一个 decorator 的成本换完整修改历史。

```bash
pip install django-simple-history && pip freeze > requirements.txt
```

- `INSTALLED_APPS` 加 `'simple_history'`；
- `MIDDLEWARE` 加 `'simple_history.middleware.HistoryRequestMiddleware'`
  （**放在 `AuthenticationMiddleware` 之后**，否则记不到"是谁改的"，只能记到改了什么）；
- `Contact` 加 `history = HistoricalRecords()`，并让 `ContactAdmin` 继承
  `SimpleHistoryAdmin`（admin 里就多出一个 History 按钮）；
- `makemigrations` + `migrate`。

现在只挂 `Contact`。`Assignment`（Phase B）和 `Contribution`（Phase C）按 `goal.md` 是**必挂**的，
到时候别忘。`Language` 这种字典表不用挂。

**验证**：admin 里改一条 Contact，History 页面能看到改了什么、谁改的、什么时候。

---

## A9 · 写 README.md

删掉空的 `READ.md`（顺便，这个文件名本身就是打错的）。`README.md` 至少要能让人照着跑起来：

1. 前置条件（Python 3.14、`postgresql@18`）
2. 建虚拟环境、`pip install -r requirements.txt`
3. `createdb rolf_dev`
4. `cp .env.example .env`，怎么生成 `DJANGO_SECRET_KEY`
5. `python manage.py migrate` / `createsuperuser` / `runserver`
6. `python manage.py test`
7. 一段话说明项目是什么，并指向 `docs/planning/goal.md`

标准是：**照着做能从零跑起来，不用问任何人。**

---

## A10 · 验收

逐条确认，全过才算 Phase A 完成：

- [ ] `python manage.py test` 全绿，且测试数量 = A1 记的数量 + A7 新增的 2 个
- [ ] `python manage.py check` 无警告
- [ ] 数据库是 Postgres（`python manage.py dbshell` 进的是 psql）
- [ ] `grep -rn "django-insecure" config/` 没有结果；`.env` 不在 `git status` 里；`.env.example` 在 git 里
- [ ] `AUTH_USER_MODEL = "accounts.User"`，`User.contact` 可空
- [ ] `TimeStampedModel` 在 `core`，`contact` 从 `core` 引入
- [ ] `INSTALLED_APPS` 和 requirements 里都没有 `countries_plus` / `languages_plus`
- [ ] admin 里改一条 Contact 能看到 History
- [ ] 空 `.env` 时启动会明确报"缺少 DJANGO_SECRET_KEY"，而不是用某个默认值悄悄跑起来
- [ ] **功能上什么都没变** —— admin 能做的事和 Phase A 之前完全一样

做完回 `goal.md` 把 Phase A 那张表的状态改成 ✅，然后把本文档换成 Phase B 的实施步骤。

---

## 一次做完还是分几次

A1–A6 是一串咬合的动作（配置 → 依赖 → 结构 → 切库），**建议一口气做完再提交**，
中间状态（比如改了 `AUTH_USER_MODEL` 但还没建库）留在磁盘上过夜容易忘记做到哪。
大半天到一天的量。

A7 / A8 / A9 彼此独立，随时可以分开做、分开提交。
