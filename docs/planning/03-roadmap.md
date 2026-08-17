# Phase C 实施手册 —— 上线与真实运营

> 这份文档只讲 Phase C **怎么做**。要做什么、为什么这么定、做完怎么算数，
> 在 [`phase-c.md`](phase-c.md)；和 [`goal.md`](goal.md) 冲突时以 `goal.md` 为准。
> [`01-roadmap.md`](01-roadmap.md) / [`02-roadmap.md`](02-roadmap.md) 是 Phase A / B 的手册，
> 已完成，留作记录，不再更新。
>
> **前端那两步（C1 / C2）的正文在 [`04-roadmap.md`](04-roadmap.md)**（2026-08-03 拆出去）。
> 编号没有改——「见 C2.5」这种引用照旧成立，本文档负责把你导过去。
>
> 写于 2026-07-31，2026-08-03 按当轮拍板的二十条重排。

## 开工时的实测基线

跑出来的，不是估的（2026-07-31，分支 `phase-b`，`HEAD` = `3b5c059`）：

| 项 | 实测值 |
|---|---|
| `python manage.py test` | **334 个，全绿**（22.8s） |
| `python manage.py check` | 0 issues, 0 silenced |
| `makemigrations --check` | No changes detected |
| `ruff check .` | All checks passed |
| Django / Python / Postgres | 5.2.16 / 3.14 / 18（psycopg 3） |
| 模板 | **16 个** `.html`（events 11 · accounts 2 · core 1 · org 1 · contact 1）—— C0.2 之后是 20 个 |
| 模板文案的语言 | 全中文（[D23](decisions/D23-i18n-interface-only.md) 定：改成英文） |
| `config/settings/prod.py` | **一句** `from .base import *` |
| 访问 `/` | **404**（`config/urls.py` 没有根路由） |
| 密码重置 | **不存在**（`accounts/urls.py` 只有 register / login / logout） |
| 发信 | `NOTIFICATION_BACKEND` 默认 console；`core/notifications/django_email.py` **已写好但没接线** |
| 部署文件 | 无（没有 `render.yaml` / `Procfile` / `Dockerfile`） |
| `requirements.txt` | 无 `gunicorn`、无 `whitenoise` |
| 本机 `rolf_dev` | ⚠️ **落后两个迁移**，且残留已删的 `contact_relationship` 表 |

⚠️ **最后一行直接决定了 C0.1 必须是重建而不是 `migrate`** —— 库里记录的是被删掉的旧迁移链，
而 `contact_historicalcontact` 表已经存在，直接 `migrate` 会撞 `CREATE TABLE` 冲突。

### 同日追加：14 条需求的五处缺口

开工当天按优先级逐条重查代码，发现 Phase B **并没有全部落地**。
五处缺口的证据和成因在
[`phase-c.md`](phase-c.md#phase-b-的五处缺口2026-07-31-发现)，这里只记结论：

| # | 缺口 | 实测证据 |
|---|---|---|
| 1 | 没有「改活动」的页面 | `events/urls.py` 无编辑路由；`services.reschedule()` **零调用者、零测试** |
| 2 | R1 没有页面 | `events_in_period()` 只有测试调用 |
| 3 | 管理侧入口断了 | `event_create` / `ministry_admins` 在任何模板里都没有链接 |
| 4 | 没有「我的资料」页 | `accounts/urls.py` 只有 register / login / logout |
| 5 | `RelationshipType` 没有种子数据 | `contact/migrations/` 只有 `0002_seed_languages` |

**它们进 C0.2，不进 C4** —— 是 Phase B 的收尾，不是 Phase C 的新功能。

## 为什么按这个顺序

六条硬性顺序，其余可以调：

1. **C0.2 补缺口必须在 C2 之前。** 补出来的是**五个新页面**；
   排在样式之后就要再排一遍。这也是 C0.2 插在验收之前的理由 ——
   拿一份不完整的功能去走浏览器验收，等于走两遍。
2. **C0.5 必须在 C1 之前，理由和上一条完全相同。** 它产出的是三个错误页模板
   和一批文案（2026-08-03 新增）。
3. **C1 必须在 C2 之前。** 构建链先就位，模板才能一次过
   （同时上 class、写 `dark:` 对、接 HTMX / Alpine、把文案改成英文）。
   反过来每个模板要动两遍甚至四遍。正文见 [`04-roadmap.md`](04-roadmap.md)。
4. **C3.0（域名 + 发信服务）建议在 C1 开工当天就启动**（2026-08-03 新增）。
   它是整个 Phase C 里唯一**靠别人**的事 —— 域名认证要等 DNS 生效，
   而当初选的 SES 还要等出沙箱的审核。让这段等待和前端工作重叠，等于白赚两天。
   2026-08-17 结果：域名买到了，**SES 沙箱没出来**，改用 Brevo 先跑（见 C3.0）。
5. **C3 的备份和权限复核必须在放真人之前。** 见
   [`phase-c.md` 的判据](phase-c.md#判据什么必须做完才能放真人什么可以边用边加) ——
   两个风险是乘法关系。
6. **C4 排在 C5 之后开工**（试点期间并行）。它们是可后补的，
   而真实使用会告诉你它们该长什么样。

---

## C0 · 本机收口，Phase B 结案

**目标**：把 Phase B 真正画上句号。三件事，半天。

### C0.1 重建本机库

```bash
dropdb rolf_dev && createdb rolf_dev
python manage.py migrate            # 顺带灌 7923 行语言 + general 工种那一行
python manage.py createsuperuser
python manage.py seed_demo          # 账号和共享密码在命令输出里
```

**验证**：`python manage.py showmigrations` 全是 `[X]`；
`psql -d rolf_dev -c "\dt"` **搜不到 `contact_relationship`**（那张表已随通用关系表删掉）。

### C0.2 · 补齐 14 条的功能缺口

**五件事，做完 Phase B 才算真的做完。** 缺口的证据在
[`phase-c.md`](phase-c.md#phase-b-的五处缺口2026-07-31-发现)。
**每件都要带测试** —— 五处里有四处的成因就是「没有 URL，所以测试也没有 URL 可打」。

#### C0.2.1 `RelationshipType` 种子迁移（先做，别的几件要用它）

照 `events/migrations/0003_seed_general_participation_role.py` 的写法，
写一条数据迁移，灌一批 `usable_as_emergency_contact=True` 的行
（`parent` / `guardian` / `grandparent` / `spouse` / `sibling` / `relative` / `friend`，
`is_symmetric` 给后四个）。

⚠️ **不能加 `child of`** —— `RelationshipType.clean()` 不允许新类型的正向名
等于已有类型的反向名，而它已经是 `parent of` 的反向标签。详见
[计划外记录](#c021--给字典表加种子迁移打红了-40-个测试)。

⚠️ **`code` 一旦发布就不能改**（[D5](decisions/D05-lookup-tables-not-enums.md) 通则）——
迁移里写死的是 `code`，显示名基金会以后可以在 admin 里改。
反向迁移**只删这几个 `code`**，不要 `.all().delete()`。

#### C0.2.2 「改活动」页 —— 把 `reschedule()` 接上

- `events/views.py::event_update(request, pk)`，路由 `events/<int:pk>/edit/`，
  复用 `_managed_event()` 那道门和 `EventForm`（模板也复用 `event_form.html`）；
- **时间变了才走 `services.reschedule()`**：

  ```python
  moved = form.time_changed()          # ⚠️ 不是 form.changed_data，见下
  event = form.save(commit=False)      # 其余字段先落到实例上
  if moved:
      services.reschedule(event, start_time=..., end_time=...)   # 服务层负责写
  else:
      event.save()
  ```

  这样 `reschedule()` 有了唯一调用者，而它的 `full_clean` + `@transaction.atomic`
  在**所有**改时间的路径上都生效；
- ⚠️ **判断挪进 `EventForm.time_changed()`，比到分钟为止。**
  本文原来写的 `form.changed_data` 那版是错的，而且**错得不报错** ——
  实施当天被测试抓出来，经过记在
  [计划外记录](#c022--时间变没变不能问-formchanged_data)；
- **改完时间跳到 `event_notify`**（`reason` 预选 `time_changed`）。
  这一步把 P6 闭上：在此之前系统能发「时间改了」的通知，却改不了时间。

⚠️ `EventForm` 现在带 `status` 字段，所以「活动结束了」也靠这个页面置
`completed` —— 不做按时间自动流转（那是一层新的定时任务基础设施，换不来东西）。

#### C0.2.3 R1 的两个页面（所有人可见）

需求原话是「在特定时间有多少 events 开展」，而**志愿者要靠它决定参加哪一场** ——
所以不是管理员报表，是两个**所有登录用户都能看**的列表：

- `event_list` 加时间段筛选（`start` / `end` 两个 GET 参数）+ **在页面上显示条数**；
- 新增 `past_events`（`events/past/`）：`visible_to_volunteers()` 里已经结束的，
  倒序，同样能筛时间段、同样显示条数。**这是活动一结束就从界面上消失的解药**；
- 模型侧加 `EventQuerySet.past(now=None)`，和已有的 `upcoming()` 并排。

⚠️ **日期默认值不许写在视图里** —— `core/tests.py` 的守卫盯着
「视图里不许出现 `timedelta(` / `local_now(` / `month_bounds(`」。
放 `events/forms.py` 的一个筛选表单里，或 `services.py`。

#### C0.2.4 管理侧入口

- **「我管理的活动」页**（`events/manage/`）：列出
  `ministry_ids_administered_by(user)` 名下的全部活动，**含草稿和已结束的**，
  每行链到 roles / registrations / attendance / report / notify / edit；
- `core/context_processors.py` 新增一个：把
  `can_grant_ministry_admin(user)` 和「有没有管理的 ministry」放进模板上下文，
  `base.html` 据此显示两个入口。
  ⚠️ **上下文处理器里也只能问 `org/permissions.py`** —— 守卫盯着
  「只有 `permissions.py` 能查 `MinistryRole.objects`」；
- `base.html` 补入口。⚠️ **实施时发现还缺一个页面**：`org:ministry_admins`
  需要一个 ministry 的 pk，而**没有任何地方给得出来** —— 想用 P5 就得先知道
  某个 ministry 的 id 再手敲 URL。所以另加了 `org:ministry_list`（`ministries/`），
  和授权页同一道 `can_grant_ministry_admin` 门。

#### C0.2.5 「我的资料」页

`accounts/views.py::profile`，路由 `me/profile/`：

- 改 `Contact` 的 `legal_first_name` / `legal_last_name` / `birth_date` /
  `email` / `phone` / `preferred_communication_method`；
- **紧急联系人**：列出 + 添加 + 删除（`EmergencyContact` 三个字段全必填，
  `relationship_type` 的下拉靠 C0.2.1 那条迁移才有内容）；
- **生日自由改**（2026-07-31 拍板）。⚠️ 代价如实记在
  [`phase-c.md` 的已知缺口](phase-c.md#五已知缺口与处置)：
  未成年人可以把生日改大绕过同意门，缓解只有 simple-history 留痕。

**验证**：`test` 全绿且**测试数明显上涨**（五件事各自带测试）；
`check` / `makemigrations --check` / `ruff` 干净。

**实测结果（2026-08-03，五件全部做完）**：

| 项 | 结果 |
|---|---|
| `python manage.py test` | **372 个，全绿**（31.2s）—— 开工时 334，五件事带来 38 个 |
| `check` / `makemigrations --check` / `ruff check .` | 干净 / No changes / All checks passed |
| 新增的路由 | `events/past/` · `events/manage/` · `events/<pk>/edit/` · `ministries/` · `me/profile/` |
| 新增的模板 | `past_events.html` · `event_manage_list.html` · `org/ministry_list.html` · `accounts/profile.html`（**都直接写的英文**，见 [D23](decisions/D23-i18n-interface-only.md)） |
| 新增的其它文件 | `contact/migrations/0004_seed_relationship_types.py` · `core/context_processors.py` |

⚠️ **三处发现文档原来写错了**，都已就地改掉并记进[计划外记录](#计划外记录)：
`form.changed_data` 判时间（会静默误发通知）、种子迁移打红 40 个测试、
`past()` 不能写成 `upcoming()` 的反面。

### C0.2.6 · 浏览器验收后的返工（2026-08-03）

C0.2 交付之后先走了一遍浏览器，回来带了 10 条。**其中 1 条是 bug，1 条是新需求，
其余是界面口径。** 全部做完，测试 **372 → 404**（开工基线是 334，
这一段的起点是 C0.2 交付后的 372 —— 原文这里写的是「334 → 404」，
把两个不同的起点混成了一个）。

| 需求 | 落点 |
|---|---|
| 取消后不能重报 | `services.sign_up()` 复用取消行 —— 见[计划外记录](#c03--浏览器验收当场抓出来的一个真-bug) |
| 管理列表要看得到时间、改得了状态 | 列表加只读 Starts / Ends + 行内 status，写入走 `services.set_status()`。**时间仍然只能进 Edit 改** —— 挪时间要通知报名者，那条路必须经过通知页 |
| 工种页要能返回 | `event_roles` / `registrations` / `attendance` / `report` 四页都补了返回链接 |
| 关系下拉别写 `parent of` | 种子数据改成名词（`contact/0005`），读法示例移进 help text |
| 我的资料要能填地址 | `ProfileForm` 加地址五项，**全部选填**（部分填写不是错，拒绝它只会逼人编内容） |
| 每个能登录的人都要有 email | 注册本来就必填；`ProfileForm` 补上「不许清空」。⚠️ **没有加数据库约束** —— 见[已知缺口](phase-c.md#五已知缺口与处置) |
| 未成年人必须有紧急联系人 | `sign_up()` 拦在**同意问题之前**。顺序不能反：说「家长邮箱没填」而真实答案是「先去加紧急联系人」，等于把人指去改错的页面 |
| 未成年报名可一键套用紧急联系人 | `SignUpForm.use_emergency_contact`。⚠️ **复制不是引用** —— 以后改紧急联系人不能篡改当时已经给过的同意，同 `hours` 和通知快照 |
| 报名成功要发通知 | `services.confirm_signup()`。未成年人**本人和家长都发**。⚠️ 发不出去**不算错**，报名照样成立 |
| 每个活动可以自己决定要不要管未成年 | **新字段 `Event.requires_guardian_consent`**，默认 `True` |

⚠️ 那个新字段的两条要害：

1. **默认必须是 `True`。** 反过来的失败是「未成年人报了名，没有任何人被告知」，
   而这件事在活动当天之前**完全看不出来**；
2. **判断收在 `services.consent_required_for(contact, event)` 一个函数里**，
   报名和签到两道门问的是同一个问题。分开写就会出现「报名被拒、签到却放行」，
   或者更糟的反过来 —— **人干完了活，工时算不上**。

不勾的时候，**紧急联系人那条要求也跟着关掉**：否则会出现「说了不需要同意，
却还是被拒绝，而页面从没提过为什么」。

### C0.3 三角色浏览器验收

⭐ 照 [`phase-b.md` 的验收清单](phase-b.md#验收2026-07-29-重写改成按-14-条需求逐条验收)
逐条勾。三个角色：总管（P5、R1–R3）→ 食物银行 admin（P2、越权 403、P4、R4–R8、P6 及越权）
→ 普通志愿者（P1、`/admin/` 403、P3、未成年人同意、越权）。

⚠️ **这一遍不能用测试代替。** 清单上大部分勾已经有 `events.tests.AcceptanceWalkTests` 覆盖，
但**表单排版坏了、链接指向空处，断言看不出来** —— 那正是浏览器这一遍存在的理由。
C0.2 的五处缺口正是这一类：334 个测试全绿，而四个功能没有入口。

**验证**：清单每条都勾上；踩到的坑记进本文档末尾的[计划外记录](#计划外记录)。

**状态**（2026-08-03 核对）：还没走。

C0.2.6 记的那一遍浏览器**不是这一遍** —— 它带回来的 10 条里，
清单上这两个验收点一条都没碰到：

- 「撤销授权填 `end_date`，**不删行**」（总管那一组）；
- 「把一个 employee 的任职 `end_date` 改到活动之前，**他应该从 R8 名单里消失**」
  （食物银行 admin 那一组，时间口径是活动当天不是今天）。

所以那是一次**功能自查**，不是照清单逐条勾的三角色验收。
两者的区别正是这份清单存在的理由：自查看的是「我改的东西对不对」，
验收看的是「清单上每一条都过了没有」。

> 这一节做完之后，**照 C0.2 的样子在这里补一张「实测结果」表**。
> C0.2 有那张表而 C0.3 没有，是这次能一眼看出「它没走完」的唯一线索 ——
> 下一次也要留得下这条线索。

### C0.4 修文档漂移，Phase B 标 ✅

1. ✅ **已做**：`02-roadmap.md` 的实测结果表里 `363` → **`334`**，
   并把「过期过两次」的经过记在同一格里；
2. ✅ **已做（2026-08-03）**：`goal.md` 两处测试数改成 **404**（返工之后的实测）。
   写的时候一并把[六、下一步](goal.md#六下一步)那段「下一步是 C0.2」改成
   「下一步是 C0.3 + C0.5」；
3. `goal.md` 第四节那张表的 Phase B 改成 ✅，并把「只差浏览器里那一遍」删掉 ——
   ⚠️ **要等 C0.2 和 C0.3 都做完**，别照原计划在补缺口之前就标。
   2026-08-03 状态：C0.2 做完了，**C0.3 还没走**，所以这一格暂时只改数字不改状态；
4. `phase-c.md` 里[测试数基线的新口径](phase-c.md#测试数基线只增不减的新口径)已经写好，
   `02-roadmap.md` 的验收那条不改（它记录的是当时的口径）。

**验证**：`python manage.py test core.tests.MarkdownLinkGuardTests` 绿。

### C0.5 · 上线前的三条死链

2026-08-03 新增。这一步的三件事有一个共同形状，
和 [C0.2 那五处缺口](phase-c.md#phase-b-的五处缺口2026-07-31-发现)完全一样：
**代码是对的，只是没人能走到它。** 404 个测试全绿，一条都没抓到。

#### C0.5.1 `LOGIN_URL` —— 匿名点导航第一个链接就是 404

实测：

```
GET /events/          -> 302 /accounts/login/?next=/events/
GET /accounts/login/  -> 404
GET /login/           -> 200
```

`config/settings/base.py` 没有设 `LOGIN_URL`，Django 用了默认值 `/accounts/login/`，
而 `accounts/urls.py` 挂在根前缀下，真实路径是 `/login/`。
`base.html` 对**未登录**访客也画 `Events` / `Past events` 两个链接，
两个视图都 `@login_required` —— 所以**任何人第一次访问这个站，
点导航第一个链接，得到的是 404**。

改法是 `base.py` 一行 `LOGIN_URL = "/login/"`。

⚠️ **带一条测试：匿名 GET 一个 `@login_required` 页面，
`follow=True` 之后必须 200。** 现有测试没抓到它，
是因为它们要么先登录、要么只断言 302 不跟随 —— 和「没有 URL 的功能测试也没 URL 可打」
是同一个成因的两种表现。

#### C0.5.2 三个错误页模板

仓库里**没有 `403.html` / `404.html` / `500.html`**。
Django 在模板不存在时返回一个 `details` 为空的裸页面，
于是 `raise PermissionDenied(SCOPED_DENIAL)` 传出去的那段文案**一个字都不会显示**。

打在两处已经写下的口径上：
[`phase-c.md` 的界面语言落点](phase-c.md#界面语言的落点英文写在哪中文允许留在哪)
把 `SCOPED_DENIAL` 列进「必须是英文（用户看得见）」，而 [C2.5](04-roadmap.md#c25-python-侧的文案改成英文)
专门有一步改它的字符串 —— **在这三个模板存在之前，那是一次没有任何效果的修改。**

三个模板放 `core/templates/`，**直接写英文**（同 C0.2 新模板的做法）。
403 那个要把 `{{ exception }}` 显示出来 —— 那正是 `SCOPED_DENIAL`
和 `org/views.py` 的 `FOUNDATION_ONLY`、`core/middleware.py` 的那段提示语的落点。

⚠️ **带一条测试：403 页面上出现 `SCOPED_DENIAL` 的原文。**
没有这条断言，模板以后被换掉、`{{ exception }}` 被删掉，都不会有任何东西变红。

样式留到 [C2](04-roadmap.md#c2--设计系统与-20-个模板) 那一遍统一上，
这里只求「读得到」。

#### C0.5.3 守卫的两层防御

现在那 12 条 grep 守卫只在有人主动跑 `manage.py test` 时才生效。改成两层：

1. **本地**：`.pre-commit-config.yaml`，`git commit` 时闪电跑一遍守卫。
   有人赶进度 `--no-verify` 跳过也没关系，还有第二道；
2. **强制**：`.github/workflows/ci.yml` —— `test` + `check` +
   `makemigrations --check` + `ruff check .`，**不过就红灯，禁止合并**。

> 第 2 道才是底线。本项目的记录里反复出现同一件事：
> **只写在验收清单里的规则跨不过下一轮**（`views.py` 不许有 `Sum` 那条、
> `admin.py` 四个钩子那条，都是先只写在清单里、后来才补成守卫的）。
>
> 这个 workflow 在 [C1.2](04-roadmap.md#c12-产物走-ci不进主分支) 会被复用来构建前端产物，
> 所以现在就建好它，不是提前投资，是少建一次。

**验证**：`test` 全绿且**测试数比 404 多两条**；
`check` / `makemigrations --check` / `ruff` 干净；
匿名浏览器打开站点，**点导航每一个链接都不出现 404**；
拿 A ministry 的 admin 打 B ministry 的 `/registrations/`，403 页面上**读得到为什么**。

**实测结果（2026-08-03，三件全部做完）**：

| 项 | 结果 |
|---|---|
| `python manage.py test` | **409 个，全绿**（34.6s）—— 这一段的起点是 404，新增 5 条（计划里写的是「多两条」） |
| `check` / `makemigrations --check` / `ruff check .` | 干净 / No changes / All checks passed |
| 新增的文件 | `core/templates/403.html` · `404.html` · `500.html` · `.pre-commit-config.yaml` · `.github/workflows/ci.yml` |
| 改动 | `config/settings/base.py` 加 `LOGIN_URL`；`requirements-dev.txt` 加 `pre-commit` |
| 死链复现 | `/events/` → 302 `/accounts/login/?next=…` → **404**，而真实路径是 `/login/`（和计划写的一字不差） |
| 守卫真的拦得住 | 故意在 `events/views.py` 里查一次 `MinistryRole`：本地 pre-commit `Failed`，`manage.py test` **exit 1** |

新增的 5 条测试（计划写「两条」，实际拆成 5 条，因为它们盯的是 5 件不同的事）：

| 测试 | 盯的是 |
|---|---|
| `test_following_the_login_redirect_lands_on_a_real_page` | **`follow=True` 是这条的全部价值** —— 不跟随的断言证明的是「跳了」，而坏掉的是「跳到哪」 |
| `test_every_link_the_anonymous_navigation_draws_resolves` | 扫渲染出来的 HTML 取 `href`，不在测试里列 URL —— 这样 `base.html` 新加一个链接**当天就被覆盖** |
| `test_the_403_page_prints_the_reason_it_was_refused` | 断言 `SCOPED_DENIAL` **原文**出现，不是「页面非空」 |
| `test_the_404_page_is_ours` | `assertTemplateUsed` |
| `test_the_500_template_renders_with_no_request_at_all` | Django 渲染 500 时**不带 request**，见下面[计划外记录](#c052--500html-不能-extends-basehtml) |

⚠️ **两条要害，都不在原计划里**：

1. **「红灯禁止合并」不在 `ci.yml` 里** —— 那是 GitHub 仓库的**分支保护规则**。
   这份 workflow 只负责变红；要让红了就不许合，得去
   Settings → Branches 把 `guards` 这个 job 设成 required status check。
   ⚠️ **少了那一步，这个文件的作用只是「PR 页面上显示一个红叉，然后照样能点合并」** ——
   而那正是本节开头说的「只写在清单里的规则」的另一种形态；
2. pre-commit 的守卫**按模块跑（`core.tests`），不按类名列**。
   `core/tests.py` 开头写着「Project-wide guards … they police every app」——
   按模块跑，新加的守卫当天自动生效；按类名列，新守卫要等有人记得回来改配置。

---

## C1 / C2 · 前端 —— 正文在 [`04-roadmap.md`](04-roadmap.md)

**这两步的正文 2026-08-03 搬去 [`04-roadmap.md`](04-roadmap.md) 了**，
因为前端目标从「上个样式」改成了「Tailwind + HTMX + Alpine，现代且经得起看」
（[D24](decisions/D24-htmx-alpine-tailwind.md)），两步装不下。

**编号一个字没改**：那边仍然叫 C1 / C2 / C2.5 / C2.6 ——
同 `goal.md` 拆分时的做法，正文搬走了，「见 C2.5」这种引用仍然成立。

| 步 | 内容 |
|---|---|
| [C1](04-roadmap.md#c1--构建链) | npm + Tailwind + htmx + Alpine；产物走 GitHub Actions 推部署分支；whitenoise |
| [C2](04-roadmap.md#c2--设计系统与-20-个模板) | 写 [`design-system.md`](design-system.md) → `base.html` 定调子 → 20 个模板逐页重写（class + `dark:` + 文案改英文 + HTMX/Alpine，一次过）→ Python 侧文案 → 两条新守卫 |

⚠️ **C1 必须在 C0.5 之后**，理由和「C0.2 必须先于 C2」完全一样：
C0.5 产出的是三个模板和一批文案，排在样式之后就要再排一遍。

---

## C3 · 交付前置

**目标**：做完这一段才可以放真实用户。见
[`phase-c.md` 的判据](phase-c.md#判据什么必须做完才能放真人什么可以边用边加)。

> **2026-08-03 从 7 步扩到 11 步。** 新增的四步（C3.0 / C3.8 / C3.9 / C3.10）
> 来自同日那次对账 —— 原来的 C3 覆盖的是「系统是对的」，
> 缺的是「坏了你怎么知道、被滥用了你怎么挡、出了事谁负责」。
> 四条都按本阶段自己的判据判过：**不做，出的事可逆吗？**

### C3.0 域名 + 发信服务 —— 最先做，因为它靠别人

**这一步是整个 Phase C 里唯一等别人的事**，所以建议在 C1 开工当天就启动，
让等待和前端工作重叠。

1. **买域名**（2026-08-03 决定：从 C5 提前到这里）—— ✅ **已买**（2026-08-17 前）；
2. **域名认证 + DKIM / SPF / DMARC** 三条 DNS 记录；
3. ~~提交 SES 出沙箱申请 —— 审核 24–48h~~。

🔴 **2026-08-17 改口：发信服务从 Amazon SES 换成 Brevo**，
因为**SES 的出沙箱申请没有通过**。定的是「先用 Brevo 跑，网站真的用起来一段时间之后
再申请一次 SES」—— 那时有真实的发信用途、真实的退信和投诉处理可以写进申请里，
而现在没有。⚠️ **换回去要重跑 C3.3 的验证，不能当成纯改配置**：
Brevo 免费档走的是共享 IP 池，SES 出沙箱后是自己认证过的域名，
两者的送达表现不是同一回事。

代码侧的代价≈0，这是当初选 SES 时就付过账的（「换家的成本≈0 —— 都是同样那四个
环境变量」）：`EMAIL_HOST` / `PORT` / `USER` / `PASSWORD` 是供应商无关的 SMTP 凭据，
`render.yaml` 里的四个占位一个字不用改。**DNS 那三条要重做一遍**（DKIM 换成 Brevo
签发的，SPF 换 include，DMARC 那条留着），但域名本身没白买。

⚠️ **发件地址钉在自己的域名上，不要用 Brevo 的共享子域。** 两个理由都不是审美：
将来换回 SES 时收件人看到的地址不变，而寄信地址一换，攒起来的送达信誉是从零开始。

⚠️ **沙箱里的 SES 只能发给已验证过的地址。** 这一条记在这里是因为它解释了
为什么 [C3.11 邮箱验证](#c311-邮箱验证注册--改-email)当初被堵死：不出沙箱的话，
密码重置和 P6 通知对真实志愿者**全部发不出去**，而 [C3.3](#c33-真实发信) 的验证
（「线上注册一个账号 → 收到邮件」）**发给你自己的邮箱大概率能通过** ——
这正是本项目反复判过刑的那种验收：它测的不是真实路径。
换成 Brevo 之后这道墙没有了，⚠️ 换回 SES 那天它**会原样回来**。

> **为什么域名不能再拖到 C5**：原来 `phase-c.md` 的待定表里写着
> 「域名不阻塞，先用 `xxx.onrender.com` 跑通」。那句话在 2026-08-03 之后不成立了 ——
> 选了 SES（正式发信要验证域名）+ 把「邮件送达」升级成交付硬前置，
> 两个决定叠起来就把域名变成了 C3 的前置。**应用仍然先跑在 `onrender.com` 上**，
> 挂自定义域名仍然在 C5；这一步买域名是为了**发信**，不是为了访问。

### C3.1 首页 `/`

> **2026-08-05 推翻并重做**，见 [D25](decisions/D25-public-front-page.md)。
> 本步原来的计划是一个**按角色分流的调度页**（未登录 → 介绍；志愿者 → 活动列表；
> ministry admin → 我管理的活动；foundation_admin → ministry 列表）。
> 那个设计有用，但它**不是首页** —— 把链接发给一个从没听过这个基金会的人，
> 他打开看到的是一张登录表单，而「链接是 404」和「链接是登录表单」
> 是同一个问题的两种形态。
>
> ⚠️ 连带代价：本步从「约一小时的硬前置」变成了**几天的门面工程**，
> 而它**不阻塞试点**。当天明确接受了这个排序。

**做完的样子**（2026-08-05）：`/` 对所有人开放，登录与否看到同一页 ——
满幅的图或视频、一段经文、平时藏起来的顶栏、左侧滑出的菜单，形制参照 LV 的首页。

| 落点 | 文件 |
|---|---|
| 单例模型（背景图 / 视频 / 经文正文 / 出处） | `core/models.py::HomePage` |
| 只有 foundation tier 能改 | `org/permissions.py` 的 `core.change_homepage` |
| 视图（**没有** `login_required`） | `core/views.py::home` |
| 模板（**不继承 `base.html`**） | `core/templates/core/home.html` |
| 顶栏淡入 / 菜单逐项入场 / 滚动呼吸 | `assets/app.css` 的 `.home-bar` / `.home-menu` / `.scroll-breathe` |

**实测结果**：

| 项 | 结果 |
|---|---|
| `python manage.py test` | **503 个，全绿** —— 上一段收尾是 492 |
| 白字对比度（拿一张带白色云团的刁难图量的） | 顶栏区 **9.94:1**、经文区 **5.57:1**，都过 4.5 |
| 菜单交互（浏览器里驱动验证） | 滑出 ✅ · 逐项延迟 160→325ms ✅ · hover 展开顶栏 ✅ · Esc 关闭 ✅ |

⚠️ **三件为可达性守住的，不是可选项**：窄屏顶栏常驻（触屏没有 hover，
否则手机用户在首页上没有任何入口）· `:focus-within` 也触发（键盘用户同理）·
菜单用 `translateX` 移出视口而不是 `display:none`（后者让链接对读屏软件不存在）。

### C3.2 密码重置

挂 Django 自带的四个视图（`PasswordResetView` / `Done` / `Confirm` / `Complete`），
写四个模板 + 一封邮件模板（**英文**）。
`accounts/urls.py` 加四条路由，`base.html` 的登录页加入口。

**做完的样子**（2026-08-17）：

| 落点 | 文件 |
|---|---|
| 四个视图（各自只加模板、限流和去处） | `accounts/views.py::VolunteerPasswordReset*View` |
| 四条路由 + 确认页那条 `<uidb64>/<token>` | `accounts/urls.py` |
| 六个模板（四页 + 一封信 + 一个 429 页） | `accounts/templates/accounts/password_reset*` |
| 新密码表单（**加了 `max_length`**，`SetPasswordForm` 没有） | `accounts/forms.py::VolunteerSetPasswordForm` |
| 每 IP / 全站两个桶 | `core/ratelimit.py::password_reset_rate_*` |
| 开发机上打印到控制台 | `config/settings/dev.py` 的 `EMAIL_BACKEND` |

⚠️ **限流挡的不是「猜账号」**，链接只会发到库里存的那个地址上。挡的是**发信额度**：
这是一个能让本应用给任意地址发信的表单，而额度和域名信誉是和真人的密码重置、
活动通知共用的。换成免费档之后这条从「以后再说」变成了现在就要有
（原计划把它归在 [C3.9](#c39-登录限流)）。

**九条测试**，其中三条钉的是「它不肯说什么」：地址不存在时**同一个页面、同一个跳转、
零封信**（否则这一页就是一个「这个邮箱是不是志愿者」的查询接口，而库里有未成年人）·
代录的 `Contact` 拿不到信 · 信里**只有地址和链接，没有姓名**（同 D22 对通知内容的口径）。

口径见 [`phase-c.md` 的已知缺口](phase-c.md#五已知缺口与处置)：
**只服务自行注册的账号**，代录的 `Contact` 本来就不登录。

### C3.3 真实发信

**服务商 = Brevo**（2026-08-17 定；原定 Amazon SES，出不了沙箱，经过见 [C3.0](#c30-域名--发信服务--最先做因为它靠别人)）。

- `prod.py` 配 Brevo 的 SMTP relay（`EMAIL_HOST` / `PORT` / `USER` / `PASSWORD` / `USE_TLS`
  全走环境变量）+ `DEFAULT_FROM_EMAIL` + `SERVER_EMAIL`；
- `NOTIFICATION_BACKEND` 在生产环境变量里指向
  `core.notifications.django_email.DjangoEmailBackend`
  （**适配器早就写好了，这一步只是接线**）。

> **为什么当初选 SES，尽管刚否决过 AWS**：否决的是**把应用跑在 AWS 上**
> （见 [`phase-c.md` 的「考虑过并否决的：AWS」](phase-c.md#考虑过并否决的aws)），
> 理由是 VPC / ALB / NAT 那一整套的复杂度和月成本。
> SES 是一个**单独的 SMTP 端点**，四个环境变量，不带来任何那些东西，
> 而它是三家里最便宜的（$0.10/千封）。这不是改口，是两个不同的问题。
>
> 换家的成本仍然≈0 —— 都是同样那四个环境变量。
> **2026-08-17 这句话第一次被兑现**：换成 Brevo，`render.yaml` 一个字没改。

⚠️ **免费档有每日发信上限，而真正会撞上它的是 P6，不是密码重置。**
一场 100 人的活动，admin 点一次 Notify 就是 100 封；密码重置和邮箱验证是零散量。
开通那天照 Brevo 页面上的**当天**额度，拿「最大的一场活动有多少人报名」算一次，
**具体数字不写进本文档**（同 [C3.5 账单那一段](#c35-部署到-render)：写死的数字会过期又不报错）。
⚠️ 撞上限的表现是**一部分人没收到**——`notify_event_change()` 现在会把
发失败的人单独记下来（见[计划外记录](#c33--发信在事务里而额度是会用完的)），
但**页面上那句「Notified 47」仍然只报数**，谁没收到要点进那条通知记录才看得见。

**代码那一半 2026-08-17 做完了**：`prod.py` 的 `EMAIL_*`（四项 `required=True`）+
`DEFAULT_FROM_EMAIL` + `SERVER_EMAIL`，开发机走 console。
⚠️ `required=True` 的代价记在 `render.yaml` 里：**凡是 import 这份 settings 的服务都要有**，
包括一封信都不发的清图 cron —— 少填一项它每天凌晨两点静静地死，
而那看起来和「今天没有图片要删」一模一样。守卫盯着这件事（含 CI 那一步）。
剩下的是**你那一半**：Brevo 的域名认证 + 四个值填进 Render。

**验证**：**必须用一个不属于你自己的邮箱**注册一个账号 → 收到邮件；
改一场活动时间 → 通知发出去 → 收到；**并且不在垃圾箱里**。

⚠️ **发给自己的邮箱不算验证过。** 自己的域名 / 自己常收的地址会被邮箱服务商放行，
而 SPF / DKIM 没配好的信对**陌生收件人**才会进垃圾箱 —— 这件事不报错、
不退信，只是「他说他没收到」。C3.0 那三条 DNS 记录就是为这一条存在的。
⚠️ 换成 Brevo 之后这一条**更要紧**：免费档走共享 IP 池，
那个池子里别人的行为也算进你这封信的送达判断。

### C3.4 生产加固

`config/settings/prod.py` 从空壳补齐：
`SECURE_SSL_REDIRECT` / `SECURE_HSTS_SECONDS` /
`SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` / `X_FRAME_OPTIONS` /
`SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`。

⚠️ **`SECURE_PROXY_SSL_HEADER` 不配，`SECURE_SSL_REDIRECT` 会造成无限重定向** ——
Render 在反代后面终止 TLS，应用看到的是 http。

**HSTS 分两步开**（2026-08-03 改口，原计划是三个一次开满）：

| | 这里（C3.4） | [C5](#c5--试点)，域名稳定跑一周之后 |
|---|---|---|
| `SECURE_HSTS_SECONDS` | `3600` | 一年 |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | 不开 | 开 |
| `SECURE_HSTS_PRELOAD` | 不开 | 开 |

⚠️ **HSTS 记在用户的浏览器里，改服务器没用。** 在自己的域名和证书稳定之前
上长 `max-age` + `includeSubDomains`，任何一个还没有 HTTPS 的子域会被浏览器
**硬性拒绝**，而且 `max-age` 期内你做什么都撑不住。
preload 更是单向门 —— 退出要等月级。

~~短值一样通过验收：`check --deploy` 只看这个设置**有没有值**，不看值多大。~~

🔴 **2026-08-17：上面那句话只对了一半，而错的那一半正好落在验收上。**
它对 `W004`（「你没设 HSTS」）成立，而**设了 HSTS 会当场点亮另外两条警告** ——
`W005` 要 `includeSubDomains`、`W021` 要 `preload`，
正是两步走里说好「域名稳定之前绝不打开」的那两个。
所以这一步不可能既照两步走做、又拿到一个空的 `check --deploy`。

处置：**按值静默**，不是列一张清单。
`prod.py` 里 `SILENCED_SYSTEM_CHECKS` 挂在 `SECURE_HSTS_SECONDS < 一年` 这个条件上 ——
C5 把时长调上去，这两条警告**自己回来**，问的恰好是 C5 要打开的那两个设置。
写死一张静默清单的话，它会一直静默下去，而且没有任何东西会提起这件事。

**做完的样子**（2026-08-17）：

| 落点 | 内容 |
|---|---|
| `config/settings/prod.py` | `SECURE_PROXY_SSL_HEADER` + `SECURE_SSL_REDIRECT` + 两个 secure cookie + `X_FRAME_OPTIONS` + HSTS 三项（走环境变量） |
| `CSRF_TRUSTED_ORIGINS` | **从 `ALLOWED_HOSTS` 推出来**，不另写一份 —— C5 挂域名时两者必须同时改，而第二份清单就是第二个会忘的地方 |
| 守卫 | `core.tests.ProductionHardeningGuardTests` 六条，逐条盯的是「失败时看起来像别的毛病」的那几个 |

**验证**：`python manage.py check --deploy` 零警告（**2 条静默**，且静默会随 C5 自己失效）。
2026-08-17 实测通过。

### C3.5 部署到 Render

- ✅ **先查 Render 支持到哪个 Python 版本**（本机跑的是 3.14）。
  ⚠️ 这一条排在最前面是有原因的：它是**唯一一件到这一步才发现就已经太晚的事** ——
  那时 Tailwind、20 个模板、prod 配置全都做完了。
  需要的话加 `.python-version` 或降版本，**降完在本机先跑一遍全部测试**。

  2026-08-12 查了，结论是**不用降**：Render 2026-02-11 之后新建的服务默认就是
  Python 3.14.3，本机是 3.14.6。加了 `.python-version` = `3.14`（省略 patch，
  平台取最新的那个）—— 不是因为默认值不对，是因为**默认值会自己变**，
  而这份部署不该跟着它变。这一条最贵的分支没有发生，一行代码都没改；
- ✅ `requirements.txt` 加 `gunicorn`（`26.0.0`，连它的 `packaging` 一起 pin）；
- ✅ `render.yaml`：Web Service + **Render 自己的托管 Postgres**（2026-08-03 定），
  健康检查指向 `/`。
  ⚠️ **分支盯的是 [C1.2](04-roadmap.md#c12-产物走-ci不进主分支) 那个部署分支，不是 `main`** ——
  前端产物由 CI 推到那里。盯错分支的表现是「合进 main 了但线上没变」，且看不出为什么。
  这一条现在有守卫了：`core/tests.py::RenderBlueprintGuardTests`，
  连同「没有任何一档是 free」「Postgres 大版本和 CI 一致」
  「`.python-version` 和 CI 一致」「`prod.py` 里每个 `required=True` 的变量
  都真的发到了每个跑 Django 的服务上」一共八条。

  ⚠️ 健康检查指向 `/` 有一个前提，容易在换首页时踩掉：**它必须在空库上也返回 200**。
  第一次部署时库里一行 `HomePage` 都没有，首页 500 的话流量永远切不过去，
  而现象是「健康检查一直红」，不是「首页坏了」。
  `core/tests.py::test_an_empty_home_page_still_renders` 钉着这一条。
  顺带查清了 [C3.4](#c34-生产加固) 那天会担心的事：Render 认 2xx 和 3xx 都算健康，
  所以 `SECURE_SSL_REDIRECT` 的 301 不会把它搞红；
- **数据库这一项有三条要当场定，不能等到出事**：
  1. ⚠️ **不能用免费档的 Postgres。** Render 的免费数据库**到期会被直接删掉**，
     而试点期间里面装的是基金会的真实数据。这不是性能问题，是数据会没。
     **开最便宜的那个付费档**（2026-08-03 定）—— 试点是一个 ministry、
     一场活动、几十个人，最小档绰绰有余，**而且事后能原地升档，不用搬数据**。
     ⚠️ 最小档的**自带备份保留期也是最短的**，这反过来又是下面第 3 条的理由：
     真正的备份是 [C3.6](#c36-备份--恢复演练) 那个往 R2 推的脚本，不是它；
  2. **major 版本 pin 成和本机一样**（本机是 18）。跨大版本时
     `pg_dump` 的客户端比服务端旧会**直接拒绝导出** ——
     而备份 cron 里那个客户端就是最容易和服务端不同步的东西。
     [C3.6](#c36-备份--恢复演练) 的恢复演练之所以要求「灌进空库 + 跑测试」，
     防的正是这一类。
     2026-08-12 查了：18 在 Render 上有，而且已经是新库的默认值，
     所以这一条不用妥协，`postgresMajorVersion: "18"` 直接写死；
  3. **Render 自带的备份留着当第二道，不当第一道** —— 理由见
     [D3 的 2026-08-03 补](decisions/D03-portable-postgres.md#2026-08-03-补render-的托管-postgres-过不过这一关)：
     库和备份在同一家，平台出事两边一起没；
- ✅ `build.sh`：`pip install -r requirements.txt` → `collectstatic --noinput`
  （原计划中间那步 `compilemessages` 随 [D23](decisions/D23-i18n-interface-only.md) 改口删掉；
  **不需要 `npm`**，产物是 CI 构建好推过来的）。

  🔴 **2026-08-12 改口：`migrate` 从这里挪到 `render.yaml` 的 `preDeployCommand`。**
  原文把它写成 `build.sh` 的第三步，那是 `preDeployCommand` 还不存在时的写法。
  两个理由，第二个才是真的要害：三个服务在同一次 push 上**各自 build**，
  写在 `build.sh` 里就是三份并发跑同一套迁移；而更糟的是**构建期的迁移已经改完库了** ——
  后面任何一步让这次部署失败，留下的是「库比正在服务的代码新」，
  那是最难收拾的一种不一致。`preDeployCommand` 在 build 之后、切流量之前跑一次，
  失败则整次部署失败、旧版本继续服务。经过在 [revisions.md 三十三](revisions.md)。
  ⚠️ 它跑在一台独立实例上，**对文件系统的改动不会带进正在跑的服务** ——
  `migrate` 只改库所以没关系，但别往那一行加任何写文件的步骤；
- ✅ **两个 Cron Job，不是一个**：备份那个在 [C3.6](#c36-备份--恢复演练)；
  另一个每天跑 `python manage.py purge_event_images`，删掉已结束活动的图片。
  ⚠️ 它**是** management command 而备份**不是**，两者不矛盾：备份必须在应用起不来
  的时候照样能跑，而这一个要问 ORM「哪些活动结束了」——没有不依赖 Django 的版本。

  两个都已经写进 `render.yaml` 了，但**备份那个现在同步不过去**，是故意的：
  它指向 `scripts/backup/`，而那个目录由 [C3.6](#c36-备份--恢复演练) 创建。
  第一次同步 blueprint 之前，要么先把 C3.6 做完，要么把那一段临时注释掉。
  之所以现在就写，是因为漏掉它的表现是**没有表现** —— 备份不存在不报错，
  只在需要它的那天才发现。
  ⚠️ 备份那个是 `runtime: docker` 而不是 `python`，理由就是上面数据库第 2 条：
  `pg_dump` 的客户端版本必须是 18，而 Render 的 python runtime 带哪个版本
  不由我们决定、还会随平台升级变。docker 是唯一能把「客户端就是 18」写进 git 的写法；
- ✅ **`STORAGES` 已经接上 R2**（2026-08-06 做完，`config/settings/prod.py`）。
  三个别名各指一个桶，划分和理由在 [C3.6](#c36-备份--恢复演练) 那张表。
  ⚠️ 少了这一步的表现是：Render 的磁盘每次部署都会清空，图片
  **不是活动结束时消失，是下次部署时消失** —— 而那是随机的，看起来像 bug 不像设计。
  ⚠️ `prod.py` 缺任何一个 R2 变量就**拒绝启动**，故意的：另一种做法（退回本地磁盘）
  正是上面那种静默失败；
- ✅ 环境变量：`DJANGO_SETTINGS_MODULE=config.settings.prod`、
  **新生成的** `DJANGO_SECRET_KEY`、`DJANGO_ALLOWED_HOSTS`、发信服务的 SMTP 四项、
  `NOTIFICATION_BACKEND`、`SENTRY_DSN`、R2 的七项
  （`R2_ENDPOINT_URL`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`、
  三个桶名、`R2_PUBLIC_BASE_HOST` —— 名字和逐条说明在 `.env.example`）
  （`DATABASE_URL` 由 Render 注入）。
  名字全部写进了 `render.yaml`，值一个都没有：秘密走 `sync: false`（第一次同步时
  平台挨个问你要），`DJANGO_SECRET_KEY` 走 `generateValue: true`（Render 现生成，
  不进任何文件，正好满足「必须是新生成的」）。
  ⚠️ 三个跑 Django 的服务共用一个 env group，**理由不是省字数**：
  `generateValue` 每写一次就生成一份新的值，在两个服务里各写一次
  会得到两个不同的 `SECRET_KEY`，而那件事不报错 —— 表现是 session 和签名过的
  链接偶尔莫名其妙失效。反过来，`sync: false` 的**不能进 group**（Render 的限制），
  所以 R2 那七项在 web 和 purge cron 里是逐字重复的，靠守卫盯着两边一致；
  ⚠️ **`NOTIFICATION_BACKEND` 现在指着发信的适配器，而 [C3.3](#c33-真实发信) 还没做。**
  在 `prod.py` 补上 `EMAIL_*` 之前，Django 会退回默认 SMTP 后端去连 localhost:25
  然后失败。第一次真实部署之前，要么 C3.3 先落地，要么临时把它改成 console backend；
- **先用 `xxx.onrender.com` 跑通**，挂自定义域名留到 C5
  （域名本身在 [C3.0](#c30-域名--发信服务--最先做因为它靠别人) 就买好了，那是给发信用的）；
- ⚠️ **别用 Render 的免费档跑试点。** 免费 Web Service 无请求十几分钟就休眠，
  冷启动几十秒 —— 基金会第一次点开链接等半分钟，会直接读成「这东西坏了」，
  而你在本机永远看不到这个现象。

**顺带把账单算一次**（2026-08-03）：Web Service 和 Postgres 都开最便宜的付费档，
加上域名（年付摊到月）、发信（Brevo 免费档；原按 SES 的按量价算，这个量级几乎为零）、
R2 和 Sentry（都在免费额度内）—— 合计**每月十几美元量级**。

这是[终极目标](goal.md#一终极目标)那张表里「便宜：起步阶段月成本控制在几十美元内」
第一次真的被结算，**结论是够用，还有余量**。

⚠️ **这里不写具体价格。** 云厂商的定价会变，而
「写死的数字是一种会过期又不报错的东西」——
这条教训 [`02-roadmap.md`](02-roadmap.md#自动化部分的实测结果) 已经为测试数付过一次学费。
开通那天照页面上的实际价格算一遍，**只要总数还在「几十美元内」这个判据里就通过**。

**账号，两个不是一个**：

1. `createsuperuser` 造一个，加进 `foundation_admin` group ——
   这是**你的**救火账号，日常不登录；
2. 给基金会的人建一个**非 superuser 的 staff 账号**：`is_staff=True`
   + 同一个 `foundation_admin` group，**不给 delete 权限**
   （尤其 `delete_event`，它两级级联到 `Participation`）。
   R1–R3 之外的全局视角、以及 P5 的授权页，用的是这个账号。

⚠️ **只造第 1 个是最容易漏的一步** —— 它跑得通、看着没问题，
于是「日常不用 superuser 登录」这条规矩当天就破了，而且没人会注意到。

**基础数据**：在 admin 里建齐 `Ministry` / `Position` / `EmploymentType` /
`EventType` / `ParticipationRole`（`general` 那一行迁移已经灌了）。
`RelationshipType` **不用手工录** —— C0.2.1 那条迁移会灌。

### C3.6 备份 + 恢复演练

**存储 = Cloudflare R2**（2026-08-03 定，待定 #2 结案）。
选它是因为**出流量不收钱** —— 而恢复演练的第一步就是把 dump 拉回来，
这一条直接打在下面那个验收口径上。三家都是 S3 协议，脚本只认 endpoint 和密钥。

- 一个 shell 脚本：`pg_dump` → 上传 R2（**不写成 management command**，
  理由见 [`phase-c.md` 的备份落点](phase-c.md#备份脚本的落点)）；
- Render Cron Job 每天跑一次；
- ⚠️ **四个桶，不是两个**（2026-08-05 定了前两个，2026-08-06 加到四个）。
  每一个桶是「谁能读」和「什么东西有权删它」两个问题的一组答案，
  而**一个桶装不下两组答案** —— 合并的诱惑一直是那句「反正都是 R2」。
  开通那天照这张表建：

  | 桶 | 环境变量 | 谁能读 | 什么能删它 |
  |---|---|---|---|
  | 备份 | 脚本自己的（不经过 Django） | 私有 | — |
  | 活动图片 | `R2_BUCKET_EVENT_IMAGES` | 私有，签名 URL | `purge_event_images` 每天扫 |
  | memories | `R2_BUCKET_MEMORIES` | 私有，签名 URL | ⚠️ **只有人手动删** |
  | 首页素材 | `R2_BUCKET_PUBLIC` | 公开 | — |

- ⚠️ **memories 桶不许挂任何 lifecycle 规则，活动图片桶将来可以。**
  这就是它得单开一个桶的理由：照片墙上的照片是全系统唯一**既不能重建、
  又不在 `pg_dump` 里**的东西 —— 库能从 dump 恢复，活动图片本来就该消失，
  只有这些误删一次就永久没了。给活动图片桶挂的任何自动清理，
  挂上去那天就同时挂在了照片墙上；

- 🔴 **2026-08-12 改口：R2 没有 object versioning。**
  这两条原来写的是「活动图片桶版本控制必须**关**（开了 `purge_event_images` 的删除
  只是个标记，而控制台上看确实显示已删除）· memories 桶必须**开**，那是它唯一的安全网」。
  去控制台上建桶时发现**这个功能不存在** —— R2 只有 bucket lock（保留策略）
  和 lifecycle 规则。于是前一条天然成立（⚠️ 换到 S3 / B2 时它立刻又是一个要守的开关），
  后一条**做不到，主动接受**：照片误删一次永久没了。
  ⚠️ **不用 bucket lock 顶替** —— 它会连正当的撤下请求一起挡住，
  而 [C3.10](#c310-一页隐私说明) 正要承诺「怎么要求删除」。
  完整经过在 [D29](decisions/D29-memories-wall.md#2026-08-12-改口r2-没有-object-versioning)，
  代价记在 [`phase-c.md` 的已知缺口](phase-c.md#五已知缺口与处置)；
- ⚠️ **首页素材桶是公开的，而且必须是。** 首页是全站唯一不需要登录的页面，
  给公开内容签发限时 URL 是自相矛盾的，还会让那个 hero 视频（每个访客都要下完整份）
  在 CDN 上一次都缓存不住。
  ⚠️ 反过来，**另外两个桶必须是私有的**：它们靠 Django 签发的一小时期限 URL 提供，
  而那正是 `@login_required` 在照片上生效的方式。公开桶会让登录闸门只挡住页面 ——
  照片 URL 一旦流出就是永久公开的，而画面里有未成年人；
- **桶必须是私有的**，密钥只给写权限，开服务端加密。
  ⚠️ dump 里是**未成年人的姓名、生日、住址、紧急联系电话、家长邮箱**的全量明文。
  一个默认公开的桶就是一次全库泄露 —— 而它比删库更难发现：
  什么都不会坏，什么都不会报错，你只是不知道有人下载过。
  这条是 2026-08-03 升级进硬前置的，原来只写了「上传对象存储」；
- ⭐ **演练一次恢复**：取回 dump → 灌进空库 → `migrate --check` 通过 → 跑一遍测试。
  **三样都做了才算**，口径见
  [`phase-c.md`](phase-c.md#备份什么叫演练过)。

**实测结果（2026-08-12，脚本和演练都做完了）**：

| 项 | 结果 |
|---|---|
| 落地的文件 | `scripts/backup/backup.sh` · `Dockerfile`（`FROM postgres:18-alpine` + aws-cli）· `scripts/backup/README.md`（含恢复 runbook） |
| 端到端 | 拿 **MinIO 当 R2**、一个临时 Postgres 18 当生产库，本机完整跑通：37 张表、259 KB、传完回头比对字节数一致 |
| 恢复演练四步 | 从桶里取回 → 灌进空库（`pg_restore` **0 个错误**、37 张表）→ `migrate --check` 通过 → **925 条测试全绿** |
| 失败路径（退出码都必须是 1） | 空库 ✅ · 少一个环境变量 ✅ · 密钥错 ✅ · 库连不上 ✅ —— 四种都红，**且桶里不留任何东西** |
| 新增守卫 | 6 条，进 `core.tests.RenderBlueprintGuardTests`，逐条反向验过 |

⚠️ **这一遍证明的是脚本和流程，不是验收。** 验收清单上那一条要拿**真的 R2 和
真的生产库**再走一遍 —— MinIO 不会告诉你桶的权限配错了，一个临时容器也不会告诉你
跨大版本的 `pg_dump` 会不会被拒。

⚠️ **上线第一天不会被那个表数下限误伤**（同日实测）：一个刚 `migrate` 完、
一行业务数据都没有的库，dump 出来仍然是 37 个 `TABLE DATA` 段。
反过来说，这条检查查的是 **schema 在不在**，
它**拦不住**「连到了一个 schema 齐全却空着的库」—— 那一种只有恢复演练查得出来。

### C3.7 权限复核

四件事，拿真账号做，清单见
[`phase-c.md`](phase-c.md#权限复核拿真账号做的四件事)。
第 4 条（未成年人数据谁能看）在 2026-08-03 改写成了一张**页面清单** ——
原来那句「逐个账号过一遍」不是一个能当场做的动作。

### C3.8 错误可见性

2026-08-03 新增。现在仓库里 `LOGGING` / `ADMINS` / `SERVER_EMAIL` **一个都没有**，
所以 `DEBUG=False` 之后未捕获异常既不写有用的日志、也不通知任何人：
用户看到一个裸 500，而**你永远不会知道它发生过**。

- **Sentry 免费档**：`sentry-sdk` + `SENTRY_DSN` 环境变量。
  ⚠️ **`send_default_pii` 保持关闭** —— 这个库里装着未成年人的资料，
  不该有第二份副本躺在第三方的服务里；
- `LOGGING` 兜底：`django.request` 的 `ERROR` 打到 stderr（Render 抓得到），
  这样 Sentry 挂了或额度用完时还剩一条线。

**做完的样子**（2026-08-17）：`sentry-sdk==2.68.0` 进 `requirements.txt`，
`prod.py` 里 DSN 非空才 init（`send_default_pii=False`、`traces_sample_rate=0`），
`LOGGING` 兜底到 stderr。
⚠️ **故意没有 `mail_admins`**，这一条是对 Django 默认行为的偏离：
报错邮件和志愿者的密码重置共用同一份每日额度，
而出事那天正是它会反复发的那天 —— 把额度花在通知我们自己上，
锁在门外的人反而收不到信。错误去日志和 Sentry，那两条都不会「用完」。

**验证**：线上故意打一个会 500 的 URL，Sentry 里看得到。⚠️ 这一条要等你填了 DSN。

**验证**：线上故意打一个会 500 的 URL（临时加一个抛异常的视图，验完删掉），
**Sentry 里看得到它，并且收到告警邮件**。

### C3.9 登录限流

2026-08-03 新增。开放注册的站上，**密码重置端点可以被拿来给任意邮箱发信** ——
发信额度和域名信誉一起烧，而发信服务对退信率和投诉率是会封端点的 ——
⚠️ Brevo 免费档的额度比出了沙箱的 SES 小得多，这条的紧迫性跟着上去了。

- `django-axes` 管登录爆破；**还没做**；
- ~~密码重置加一条 per-IP 节流~~ —— **2026-08-17 随 [C3.2](#c32-密码重置) 一起做了**，
  而且是两个桶不是一个（每 IP + 全站）。提前的理由是换了免费档：
  那条节流护的是每日发信额度，而额度从「基本撞不到」变成了「天天可能」。

**注册限流已经做了**（2026-08-06）：`django-ratelimit`，per-IP 和全站两个桶，
额度走环境变量。做法和「为什么额度定得松」在 `core/ratelimit.py` 里。
部署时**必须同时确认两件事**，两件都属于「不做也不报错」那一类：

| 要确认的 | 不做会怎样 |
|---|---|
| `prod.py` 里 `TRUST_PROXY_CLIENT_IP = True`（已经写进去了，别删） | Render 在反代后面，`REMOTE_ADDR` 是代理 —— 全世界共用一个桶，这一小时头 20 次注册用光所有人的额度 |
| cache 表存在（`core/0004` 迁移会建，所以跑过 `migrate` 就有了） | `django-ratelimit` 在 cache 里计数，表不在就是注册页报错 |

⚠️ **不要把 cache 换成 Django 默认的本地内存**。gunicorn 多 worker 时那是各数一份，
额度悄悄变成 worker 数倍，而且不报错。要换就换 Redis，别退回默认值。

**注册的邮箱验证仍然没有做**（2026-08-03 定，2026-08-06 复核）：
限流挡的是脚本批量灌库，**挡不住有人用不属于自己的地址手工注册**。
邮箱验证依赖真实发信，所以按「依赖上线后才存在的东西现在不做」推迟 ——
它要和「改 email 的重新验证」一起做，见 [C3.11](#c311-邮箱验证注册--改-email)。
⚠️ 这条**只在试点期成立** ——
重看条件写进 [`phase-c.md` 的已知缺口](phase-c.md#五已知缺口与处置)：公开放开注册之前必须补。

### C3.11 邮箱验证（注册 + 改 email）

2026-08-06 新增，**排在 [C3.3](#c33-真实发信) 之后**，因为它整件事都依赖真实发信。

⚠️ **它一定要排在 C3.3 验证通过之后，不能并行。** 失败模式很难看：账号建出来是未激活的，
而激活邮件发不出去 —— 于是注册被整个锁死，且每个人看到的都是「注册成功，去收邮件」。

两半，必须一起做：

1. **注册**：`is_active=False` 建账号 → 发激活链接 → 点了才能登录。
   - 链接用 Django 自带的 `default_token_generator`（和密码重置同一套 `uidb64/token`），
     **不要自己发明 token**：那一套自带过期（`PASSWORD_RESET_TIMEOUT`），不用新建表；
   - 登录页要为「地址存在但没激活」单独说一句话 + 一个「重发激活邮件」入口（也要限流）。
     ⚠️ 不加这句话的话，`ModelBackend` 对未激活账号返回的是通用错误，
     人看到的是「邮箱或密码不对」，而他的密码是对的 —— 于是他会一遍遍重置密码；
   - 一条清理命令删「N 天未激活」的账号。⚠️ 它建的 `Contact` 也要删，
     但**只能删「从未激活 + 没有任何 Participation / MinistryRole / Assignment」的那些**，
     否则会删到真人。反射的写法照 `contact/services.py::merge_contacts()` 反过来用，
     默认 `--dry-run`。
2. **改 email**：加 `pending_email` 字段 + 第二套确认流程，确认之前登录名不变。
   ⚠️ 只做第一半是留一道现成的绕路：注册时验证了地址，然后随手改成任意地址。

**验证**：用一个**不属于你自己的**邮箱注册 → 收到激活邮件 → 点了才能登录；
未激活时登录，页面明确说「去收邮件」而不是「密码不对」。

### C3.12 Google 预填要的那个 Client ID（你做的部分）

2026-08-06 新增。注册页上「Continue with Google」的代码**已经写完了**，
按钮在没配 `GOOGLE_OAUTH_CLIENT_ID` 时不渲染 —— 所以现在缺的只是这一个值。
⚠️ **和注册 AWS 账号一起做**（同一次坐下来办完外部账号的事）。

它只填三个框：Google 交出 email / first name / last name，密码仍然自己设，
建出来的账号是普通密码账号。**不是「用 Google 登录」**，见 `accounts/google.py`。

你要做的：

1. Google Cloud Console → 建一个项目（或用现成的）；
2. APIs & Services → **OAuth consent screen**：External，填应用名和支持邮箱；
3. APIs & Services → Credentials → Create credentials → **OAuth client ID** →
   Application type = **Web application**；
4. **Authorized JavaScript origins** 填 `http://localhost:8000`（本机），
   上线后把正式来源加进去；
   ⚠️ **Authorized redirect URIs 不用填** —— 这个流程不做 code 交换；
5. 把 Client ID 填进 `.env` 的 `GOOGLE_OAUTH_CLIENT_ID`，线上填进环境变量。

⚠️ **只要 Client ID，不要 client secret。** 这个流程里没有任何需要 secret 的一步，
而把 secret 放进环境变量是给自己多一个要保管的东西。

**验证**（要在浏览器里做，测试替不了）：注册页出现 Google 按钮 → 点 → 选账号 →
回到注册页，email / first name / last name 三个框**已经填好** → 设个密码 → 注册成功。
⚠️ 再确认一遍数据库里那个账号是**普通密码账号**：`has_usable_password()` 是 True。

### C3.10 一页隐私说明

2026-08-03 新增。系统里存着**未成年人的姓名、生日、住址、紧急联系电话、
家长的邮箱和电话**，而且对公众开放注册。原来的计划里关于这些数据只有一句
「谁能看要过一遍」—— 那是**内部**的访问控制，不是对**当事人**的交代。

一页，四段：存什么 · 谁看得到 · 留多久 · 怎么要求删除。
注册页和页脚各一个链接。

**初稿按代码里实际存的字段写**（`Contact` / `EmergencyContact` /
`Participation` 的六个同意字段），不要凭印象写 —— 写多了是承诺，写少了是漏。
⚠️ **内容要基金会确认后才能上线**：留存期限和删除流程是他们的决定，不是技术决定。

---

## C4 · 运营功能（试点期间并行）

顺序按 [`progress.md` 已定的](progress.md#phase-c--上线与真实运营)，**不改**：

1. **Ministry 视图** —— 各 ministry 下分 Leaders / Employees / Volunteers **加「空缺」**四组。
   ⚠️ **直接 `from org.services import build_org_tree`，不要自己递归 `reports_to`** ——
   环的兜底和 N+1 的规避都在那个函数里，Phase B 已写好并测过，
   而且 `core/tests.py` 有一条守卫盯着「只有 `org/services.py` 能走汇报链」；
2. **组织架构图** —— 同一步，只依赖 `Position` 一张表，不 join 任职数据；
3. ~~**志愿者活跃排行、跨活动总工时**~~ —— **2026-08-05 提前做掉了**，
   在管理列表旁边那块报表面板里（[D27](decisions/D27-ministry-report.md)）。
   排行是「Most hours」那张图，跨活动总工时是「已记录工时」那个数字。
   ⚠️ 口径确实写在 `services.py` 里而不是视图里，守卫照旧生效。
   这一步剩下的是**别的切法**（按 ministry 的年度榜、给志愿者看自己的累计），
   等试点反馈再定要不要做；
4. **CSV 导出**。⚠️ 报表面板做完之后这一条的价值变了 ——
   原来它是「唯一能把数字拿出去」的路，现在页面上已经有数字了，
   它变成「拿去做别的分析」。先问基金会真的要拿去做什么，再定导什么列。

---

## C5 · 试点

- **一个 ministry、一场真实活动。** 影响面小、反馈直接。
- **顺序有依赖**：employee 先注册 → 给他们建 `Assignment` → 再办活动，
  否则 R8 会安静地返回空名单（见
  [`phase-c.md` 的已知缺口](phase-c.md#五已知缺口与处置)）。
- **域名在这一步挂上**：Render 加 custom domain + 自动证书。
  ⚠️ `DJANGO_ALLOWED_HOSTS` 和 `CSRF_TRUSTED_ORIGINS` **必须同时改** ——
  只改前者的话页面能打开，但所有 POST 表单被拒。
  2026-08-03 更正：**买域名不在这一步了，提前到了
  [C3.0](#c30-域名--发信服务--最先做因为它靠别人)**（发信要验证域名）。
  这里只剩「挂到应用上」。
- **域名稳定跑一周之后，把 HSTS 调长**：`SECURE_HSTS_SECONDS` 改成一年，
  加 `INCLUDE_SUBDOMAINS` 和 `PRELOAD`。理由见 [C3.4](#c34-生产加固) 那张两步表。
- 试点期间每周跑一次 `python manage.py list_duplicate_contacts`。

---

## C6 · 现场扫码自助签到

**决策全文在 [D28](decisions/D28-qr-checkin.md)。** 这一节只讲照着做的顺序，
不重复「为什么」——每一步后面标的是 D28 里对应的那一节。

它解决的是一件具体的事：**一场一百人的活动，让一个 admin 点四十次签到、
四十次签退，不会被真的执行**，而不执行的后果是 D27 那张报表的已记录工时和
缺勤率同时失真，且失真方向跟着「哪个 admin 比较负责」走。

⚠️ 动手前先读 D28 的[唯一的不变量](decisions/D28-qr-checkin.md#-唯一的不变量这是一个减少录入的工具不是一个防作弊的机制)。
这个功能**不承诺任何一行是真的** —— 权威仍然是 admin 的 attendance 页。
把它当成防作弊机制去实现，会在几个地方做出相反的取舍
（尤其是 [token 一次性化](decisions/D28-qr-checkin.md#三token-一次性化是一个-bug不是一个加固)那一条，
照原需求写会让一百人的队伍里九十九个人打不上卡）。

### C6.1 `events/tokens.py` —— 纯函数，先写这一层

无 DB、无 request、无 settings 读取（除了 `SECRET_KEY` 经由 `salted_hmac`）。

```python
issue(event_id, mode, *, at=None) -> str
verify(token, *, at=None) -> (event_id, mode)   # 失败抛自己的异常
window_is_open(event, *, at=None) -> bool       # start-2h ~ end+4h
```

⚠️ `at=None` 不是可选的写法，是这一层的**全部理由**。本项目所有跟时间有关的写路径
都注入时钟（`check_in(participation, *, at=None)`、`upcoming(now=None)`），
`TimestampSigner` 因为注不进时钟才被否决 ——
见 [D28 为什么不用 TimestampSigner](decisions/D28-qr-checkin.md#为什么不用-timestampsigner)。
写成读 `local_now()` 的版本，测「刚好 90 秒」和「刚好 91 秒」就得 mock，
而这一层存在的意义就是不用 mock。

⚠️ `key_salt="events.checkin"` 不能省，理由同上一节末尾。

⚠️ 放 `tokens.py` 而不是 `views.py`：`ViewsAreThinGuardTests` 禁止 views.py 出现
`local_now(`，会直接红。

**这一步的测试（不碰 DB，`SimpleTestCase` 就够）**：

| | |
|---|---|
| 签发再验证 | 拿回同一个 `(event_id, mode)` |
| 89 秒 / 91 秒 | 边界两侧各一条，注入时钟，不 mock |
| 改一个字符 | 拒绝 |
| 换 `event_id` 重签 | 拒绝（签名覆盖了它） |
| 换 `mode` 重签 | 拒绝 |
| 时间窗 | `start-2h` 前一分钟拒、后一分钟过；`end+4h` 同理 |

### C6.2 `checked_in_method` —— 一次迁移

`Participation` 加一列，`blank=True`，**没有 default**
（[为什么不给 default](decisions/D28-qr-checkin.md#四checked_in_method一次迁移一列出事时想看的那一列)）。

`check_in()` / `check_out()` / `record_hours()` 三个都接 `method=` 参数，默认 `ADMIN`。
⚠️ **三个都要接**：`_mark_attended()` 的注释里已经写过「一个规则三个入口一个守卫，
等于两条绕路」，来源字段是同一个形状。

测试：三条写路径各自记下正确的来源；空字符串的老行不被读成 admin。

### C6.3 session 凭据 + 两个志愿者页面

```
GET  /events/checkin/<token>/     验签 → 写 session → login_required 跳转
GET  /events/checkin/confirm/     读凭据 → （多工种则让他选）→ 显示确认
POST /events/checkin/confirm/     transaction.atomic() + select_for_update()
                                  → check_in() / check_out()
                                  → redirect My Signups + 一条 success
```

⚠️ **确认页的表单里不带 token。** POST 校验的是 session 凭据。带上 token 会诱使人
重新验一次，那就把 90 秒的窗口套回到「打完密码之后」，
[两段式](decisions/D28-qr-checkin.md#为什么-②-和-③-一定要分开两个理由各自都是硬的)就白分了。

⚠️ 凭据的读写规则（10 分钟、绑 event 和 mode）放 `services.py`，不放视图 ——
它是业务规则。

⚠️ 锁里面**不许有任何 I/O**，也**不许**用 `.update()` 绕过 `full_clean()` 和
simple-history（会不留 history 行，而那是 `undo_attendance()` 敢删 hours 的前提）。

**这一步的测试**：

| 情况 | 断言 |
|---|---|
| 未登录扫码 | 跳登录，`next` 指向确认页；登录回来仍能完成 |
| token 过期 | 拒绝，页面指向「重扫屏幕上的码」 |
| 凭据过了 10 分钟 | 拒绝 |
| 没报名这场活动 | 拒绝 + 报名链接，**且库里没有新建 Participation** |
| 未成年缺同意 | `ConsentRequired` 渲染成一句人话，**不是 500** |
| 连打两次 | 幂等：`checked_in_at` 不变，history 只多一行 |
| mode 和状态对不上 | 「你 9:03 已经签到了」/「先签到」 |
| 已 cancelled 的报名 | 拒绝 |
| 一人两工种 | 出选择页；选完只有那一行被打 |
| 签退 | 写 `hours`，且 `checked_in_method` 是 `self_qr` |

⚠️ 单元测试里**测不了真并发**（sqlite 上 `select_for_update` 近乎无操作）。
所以这里断言的是幂等，真并发走 C6.5。

### C6.4 admin 的平板页

`npm i qrcode` → `assets/js/` 一个新模块（**不是 Alpine** —— 它有网络和定时器）。

⚠️ 二维码里的 URL **从服务端来，JS 不拼** —— 同
`AssetPathsComeFromTemplatesGuardTests` 的教训。

⚠️ [`setInterval` 单独用当天一定翻车](decisions/D28-qr-checkin.md#setinterval-单独用当天一定翻车)。
四条对策缺一不可，其中最反直觉的一条：**fetch 失败时要把二维码盖掉**。
留着它，失败会静默转移到志愿者身上。

测试：token 端点对非 `can_manage` 的账号 403（**这是整个方案最关键的一条** ——
不检查的话任何志愿者在家里就能给自己发码）；时间窗外拒签；draft / cancelled 拒签；
响应里带 `expires_at`；限流生效。

### C6.5 入口、My Signups、压测

- `/events/manage/` 的 Go to 列加 **Check-in QR**（`can_manage` 才画 ——
  同一列里已经有「只读身份不画 Edit / Notify」的规矩）；attendance 页顶部也放一个；
- `my_participations.html` **合并成一列 `Attendance`**，
  [不能再加两列](decisions/D28-qr-checkin.md#my-signups-那张表不能再加两列)；
- staging 打 200 个并发 POST：同一个人只产生一次 `check_in`、p95 延迟、零 500；
- ⚠️ 顺带核对 `gunicorn workers × threads` 对 Render Postgres 连接上限的算式 ——
  [一百人真可能撞到的墙是连接数，不是 CPU](decisions/D28-qr-checkin.md#真正要处理的四件事)。

---

## 验收

- [ ] ⭐ **14 条需求每一条都能从某个链接点得到** —— 不是「service 写好了」，
      是「用户从哪进去」。这是 C0.2 那五处缺口的成因，见
      [`phase-c.md`](phase-c.md#phase-b-的五处缺口2026-07-31-发现)
- [ ] `python manage.py test` 全绿；测试数**高于 404**（下降必须伴随一次功能删除，
      口径见 [`phase-c.md`](phase-c.md#测试数基线只增不减的新口径)）
- [ ] `check` 零警告 / `makemigrations --check` 无变更 / `ruff check .` 干净
- [ ] `python manage.py check --deploy` **零警告**
- [ ] CI 的 workflow 在守卫不过时**真的红灯**（故意破坏一条，确认它拦得住）
- [ ] **匿名**打开站点，导航上每一个链接都点一遍，**没有 404**
- [ ] 三条路径走通，**深浅两色各一遍**；375px 宽度可用
- [ ] 把 `static/css/app.css` 删掉，页面**仍然可用**（判据见
      [`phase-c.md`](phase-c.md#样式的落点css-只许出现在两个地方)）
- [ ] 把所有 `x-` 属性删掉、关掉 JavaScript，**每个写操作仍然能完成**
      （判据见 [D24](decisions/D24-htmx-alpine-tailwind.md#渐进增强的口径只管写操作)）
- [ ] **备份恢复演练三样都做过**；R2 的四个桶**逐个点开确认过**三件事 ——
      读权限（备份 / 活动图片 / memories 三个私有，首页素材公开）·
      **四个桶都没有 lifecycle 规则**（尤其 memories）· **都没有设 bucket lock**。
      ⚠️ 这几项没有一项能靠代码或测试验出来，只能在 R2 控制台上看，
      而弄错的表现都是「一切正常」：memories 桶上挂一条自动清理，
      照片会在某天集体消失；设了 bucket lock，正当的撤下请求会删不掉。
      ⚠️ 2026-08-12：这一条原来查的是「版本控制开/关」，
      而 **R2 没有 object versioning**，见 [C3.6](#c36-备份--恢复演练)
- [ ] ⭐ **越权实测**：A ministry 的 admin 打 B ministry 三个 URL 全 403；
      志愿者打 `/admin/` 得 403。**且 403 页面上读得到为什么**
- [ ] 线上完成一次「注册 → 收密码重置邮件 → 改密 → 登录」，
      用**一个不属于你自己的邮箱**，且信**不在垃圾箱里**
- [ ] 线上故意打一个 500，**Sentry 里看得到**
- [ ] 隐私说明上线，且基金会确认过内容
- [ ] 一个 ministry 的真实志愿者报名并完成了一场真实活动

---

## 计划外记录

> **实施时才发现的坑记在这里。** 这一节是这个项目最贵的资产之一
> （见 [`goal.md` 的约定 2](goal.md#-文件地图2026-07-30-拆分)）—— 一条都不删。

### C0.3 · 浏览器验收当场抓出来的一个真 bug

**报名 → 取消 → 再报名，报不了。** 自动化测试全绿，334 个里没有一个发现它 ——
因为**每个取消报名的测试都停在取消那一步**，没有人接着再报一次。

成因：`cancel()` 是改状态、不是删行（通知历史指着这些行），
所以那一行还占着 `(event_role, contact)` 的唯一约束，而 `sign_up()` 永远新建一行。
第二次报名撞约束，回来的话是「你已经报过这个工种了」—— **既错，志愿者自己还解不开**。

改法：`sign_up()` 先查有没有已存在的行；是取消状态就**复用它**（状态回到
`registered`、`registered_at` 前移、同意字段按这一次重新填），不是取消状态才报重复。

> **这条值得记的不是修法，是它怎么活下来的**：测试覆盖了「取消」这个动作，
> 没覆盖「取消之后」。**下一次写状态流转的测试，问一句「从这个状态还能回去吗」。**

### C0.3 · 演示数据也得转英文（推翻了自己刚写的落点规矩）

[界面语言落点](phase-c.md#界面语言的落点英文写在哪中文允许留在哪)那张表原来把
「`seed_demo` 造的演示数据」放在「中文照旧」那一列。**上手验收时立刻发现这行不通** ——
英文界面里满屏中文活动名和 ministry 名，人根本没法核对页面对不对。

推翻它的判据就是那一节自己写的分界线：「**这句话会不会出现在浏览器里**」。
演示数据会，而且是验收那一遍**唯一**会出现在浏览器里的数据。
表已就地改掉，`seed_demo` 的 ministry / 活动 / 工种 / 人名全部改英文。

⚠️ 两条**重命名迁移是新增的，不是改旧的**（`contact/0005`、`events/0005`）——
`get_or_create` 匹配的是 `code`，就地改旧迁移会让「新装的库」和「升级过的库」
拼写不一致，**而这种不一致不报错**，只是两台机器上的下拉框长得不一样。

### C0.2.1 · 给字典表加种子迁移，打红了 40 个测试

`contact/0004_seed_relationship_types` 一写完，`test` 从全绿变成
**39 errors + 1 failure**。原因不是迁移写错了，是**迁移会在每个测试数据库里先跑一遍** ——
而那 40 个测试都假设 `RelationshipType` 一开始是空的：

- 16 个自己造了一行 `code="parent_of"` / `name_a_to_b="parent of"`，
  和种子行撞上 `Lower(Trim(name_a_to_b))` 那条唯一约束；
- 1 个断言「能用于紧急联系人的类型」这个列表**恰好等于** `["mother_of"]`；
- 其余是 `seed_demo` 也自己造了同一行，连带 `NotificationTests` 全组倒下。

**修法是让它们用种子行，不是给种子改名。** 这张表现在是 schema 的一部分
（必填 FK 指着它），测试再造一份就是造第二份真相：

- `seed_demo` 和 `events/tests.py` 改成 `RelationshipType.objects.get(code="parent")`；
- `RelationshipTypeTests` 的 fixture 整体挪到一组**不在种子里**的名字
  （`mentor of` / `mentee of`）—— 它测的是规则，不是词汇表，
  借用种子里的名字只会让每个用例在够到规则之前先撞名字约束；
- 那条列表相等的断言改成**包含关系**。⚠️ **对字典表断言「全表恰好等于什么」
  从来都是错的** —— 基金会在 admin 里加一行，测试就红，而它其实没坏。

**顺带发现的一条模型规则**（写种子数据之前必须知道）：
`RelationshipType.clean()` 不允许**新类型的正向名等于已有类型的反向名**，
所以种子里**不能同时有 `parent of` 和 `child of`** —— 后者已经是前者的反向标签。
成年子女当紧急联系人用 `relative of`。这条规则迁移本身看不见
（迁移走的是历史模型，`clean()` 根本不执行），所以补了一条测试专门盯着它。

### C0.2.2 · 「时间变没变」不能问 `form.changed_data`

roadmap 原来写的做法是 `moved = bool({"start_time", "end_time"} & set(form.changed_data))`。
**它是错的，而且错得不报错** —— 写完当场被新测试抓出来两次：

1. `datetime-local` 控件**不带秒**。库里存的是 `09:00:37`，表单显示 `09:00`，
   原样提交回来就是 `09:00:00` —— 一个 `!=` 就把它判成改了时间。
   后果：**管理员改一下地点，所有报名的人收到一封「时间改了」**。
   这正是让人开始无视通知的那种邮件；
2. 顺带的一条：`ModelForm` 在 `is_valid()` 里就把提交值写进了 `instance`，
   所以视图里 `event.start_time` 事后再比是**比不出来的** —— 它已经是新值了。

**改法**：判断挪进 `EventForm.time_changed()`，读 `self.initial`（构造时从实例填的，
校验不会动它），**比到分钟为止**。放表单不放视图，是因为这个答案取决于
「控件能表达多少精度」，那是表单自己的事；视图只决定拿这个答案做什么。

⚠️ **这一条会静默失败，所以有一条测试专门盯着它**
（`test_seconds_the_widget_cannot_show_are_not_read_as_a_reschedule`）——
把库里的时间改成带 `37` 秒，然后只改地点，断言**不**跳通知页。

### C0.5.2 · 500.html 不能 extends base.html

403 和 404 都 `extends "core/base.html"`，500 **不能** —— 而这件事看代码看不出来，
要去读 Django 的 `django.views.defaults.server_error`：

```python
return HttpResponseServerError(template.render())   # 注意：没有 request
```

**不带 request**，所以 context processors 一个都不跑、`user` 是空的、
`{% csrf_token %}` 拿不到 token。这些大多不会当场炸，但 **500 页面自己再抛一次异常
是这一层最糟的结局**：用户拿到的是 Django 的兜底纯文本，而你连这一页写过什么都看不见。

所以 500 那份是自足的：不继承、不依赖 context。**底下那个链接也故意写死成 `/events/`，
不用 `{% url %}`** —— 理由不是 `{% url %}` 在这里跑不动（它只查 urlconf，跑得动），
而是它**会抛 `NoReverseMatch`**：路由名改了这一页就自己炸掉，而别处炸了有它兜着，
它炸了没有下一层。⚠️ 代价如实说：路由改了这里不会跟着改，**也不会有测试变红**。

配套那条测试打的正是这个条件本身 ——
`loader.get_template("500.html").render()`，不给 context，
和 Django 真实调用的形状一样。用 `self.client.get()` 触发一个真 500 测不出这件事，
因为那条路径上 request 是有的。

### C0.5.3 · pre-commit 里不能用 `--keepdb`

第一版写的是 `manage.py test --keepdb core.tests`，图它快。
**实测差别只有 1 秒**（2.0s → 3.0s），而它留下的 `test_rolf_dev` 会让**下一次普通的
`manage.py test` 停在一个交互提问上**（「要不要删掉这个测试库」）——
当场就撞到了：跑全套的时候拿到的是 `EOFError`，而那个报错的样子完全不像
「上一次用了 `--keepdb`」。

一秒换一个每天要撞很多次的坑，不划算。改成 `--noinput`，
顺带解决 pre-commit 没有 tty 时任何交互提问都变成 `EOFError` 的问题。

> **这条的一般形式**：**优化一个 2 秒的东西之前，先量一下它到底省几秒。**

### C4.3 · 报表的月份图全是 1，而二十条测试全绿（2026-08-05）

管理列表右边那块报表面板做完，第一张截图上：
左边的列表列着**十一场**八月的活动，右边的「Events by month」写着「Aug 2026 — 1 event」。

成因是 Django 的一条规则，而它只在**显式**排序时生效：

```python
events = events.order_by("-start_time")          # 视图里，为了让列表倒序
...
events.annotate(month=TruncMonth("start_time")).values("month").annotate(n=Count("pk"))
```

那个 `order_by` **也进了 GROUP BY** —— 于是分组键是「月 + 精确到秒的开始时间」，
一场活动一组，每组都是 1。

⚠️ Django 3.1 之后会为聚合查询忽略 `Meta.ordering`，**但从不忽略谁写的 `order_by()`**。
很容易记成「Django 已经处理好了」。

改法是在每一处 `values().annotate()` 之前 `.order_by()` 清空。

> **这条值得记的不是修法，是它怎么活下来的**：当时二十条测试全绿，
> 因为**每一条都自己造了一个没排序的 queryset**。视图交出来的那个是排过序的，
> 而没有任何测试走过那条路。
>
> **下一次给一个"接收 queryset"的函数写测试，问一句「调用它的人手里那个 queryset
> 长什么样」** —— 尤其是排序、`distinct()`、`select_related()` 这些不改内容、
> 只改 SQL 形状的东西。它们改的正是聚合的行为。

另外，**这个是看截图发现的，不是测试发现的。** 数字本身完全合理 ——
「八月一场活动」不会让任何人皱眉，是它**旁边就摆着那十一行**才暴露的。
把报表和它描述的列表放在同一屏，本来只是布局决定，结果成了一道校验。

### C3.3 · 发信在事务里，而额度是会用完的

2026-08-17，换发信服务那天顺出来的。**不是换供应商引入的 bug，是换供应商让它变成
一件会发生的事** —— 出了沙箱的 SES 基本撞不到每日上限，免费档天天可能。

`notify_event_change()` 当时是这个形状：

```python
@transaction.atomic
def notify_event_change(event, *, reason, message, sent_by, backend=None):
    ...
    results = backend.send([...])          # ← 网络 I/O，在事务里面
    notification = EventNotification.objects.create(...)
    notification.recipients.set([r.participation for r in recipients])
```

一场 100 人的活动，额度在第 47 封断掉 → `send_mail(fail_silently=False)` 抛异常
→ 整个事务回滚 → **那条 `EventNotification` 根本不存在**。
而 46 个人的收件箱里已经躺着一封信了。库里的说法是「没人被通知过」。

⚠️ **这条记录本身就是为了对付「谁没被通知到」而存在的**（D22 ②：
`unreachable` 存的是名字不是数字，因为数字答得了「几个」答不了「哪三个」）。
它在最需要它的那一刻消失，而消失的方式是**看起来什么都没发生**。

三处改动，缺一不可：

| 改哪 | 改成什么 | 为什么单独这一条不够 |
|---|---|---|
| `DjangoEmailBackend` | 逐封捕获 `OSError`（`smtplib.SMTPException` 是它的子类），返回 `accepted=False`，**不抛** | 这一条才是真正让记录活下来的那一条。⚠️ 但它单独存在时，失败会被上一层丢掉 —— 见下一行 |
| `notify_event_change()` | 按回执分组：接受的进 `recipients`，没接受的进新的 `failed` | 回执一直都在（`DeliveryResult.accepted`），**是这个函数把它扔了**：它把「试过的人」全记成了「告知了的人」 |
| 同上 | 发信挪到 `transaction.atomic()` **外面** | 一批一百封会把数据库事务按住几分钟 —— 而 Render Postgres 的连接数是[真会撞到的墙](#c65-入口my-signups压测) |

⚠️ **`failed` 是第三个 M2M，不是塞进 `unreachable`。**
「这个人我们压根没有他的联系方式」和「19:04 那封信服务器没收」是两件事实，
前者的修法是找他要个邮箱，后者的修法是去看供应商。合成一栏之后，
第一个问题**永远回答不了了** —— 这条记录里没有任何别的东西记得当时是哪一种。

> **值得记下来的那一点**：`NovuBackend` 从第一天起就是对的，
> 它那句注释写着「Reported, never raised: one address failing must not stop the
> rest of the batch, **and the caller records what happened**」。
> 三个适配器里两个守着这个约定，而**调用方从来没有记**。
> 一个约定只写在其中一个实现的注释里，等于没写 —— 所以这次把它写进了
> `NotificationBackend` 这个 Protocol 的 docstring：**约定要写在接口上，不是写在某个实现里**。

⚠️ 「发信挪出事务」这一半**没有任何计数断言盯得住** ——
测试本来就跑在事务里，问「现在在不在事务里」两种写法都答 True。
最后是量**深度**（`connection.savepoint_ids` 的长度）：
外面几层就该是几层，多一层就说明发信被人包回事务里了。
把 `@transaction.atomic` 放回去反向验过，红的（`[2] != [1]`）。


### C3.4 · 健康检查打的是 `/`，而 `/` 现在会跳转

2026-08-17，上线当天第一次真部署就红了。报错是这一句：

```
Timed out after waiting for internal health check to return a successful
response code at: rolf-v.org:10000/
==> Build failed
```

这句话描述的是**一个死掉的或者慢得离谱的应用**。而应用两样都不是 ——
它在**好好地回 301**。

Render 的健康检查**直接打实例**，走的是纯 HTTP，不经过它自己那层反代，
所以那个请求上没有 `X-Forwarded-Proto: https`。`SECURE_SSL_REDIRECT` 一看是
http，就照规矩跳去 https。平台要 2xx，拿到 301，判定失败。

⚠️ **整条链路上没有任何一个字提到设置、路径或文件。** 部署日志里连一行红字都没有
（301 不是错误），Render 说的是「超时」，而超时听起来像要去查 gunicorn 和数据库。

改法是给健康检查一个自己的端点，而不是让首页兼职：

| 落点 | 内容 |
|---|---|
| `core/health.py` | 只有一个常量 `HEALTH_PATH`，**没有任何 import** —— `prod.py` 要读它，而 settings 求值时 app registry 还没起来 |
| `core/views.py::healthz` | 200 + 一个字符串。**不碰数据库、不渲染模板** |
| `prod.py` | `SECURE_REDIRECT_EXEMPT` 只放这一条路径；首页照旧跳转 |
| `render.yaml` | `healthCheckPath: /healthz` |
| 守卫 | `core.tests.HealthCheckGuardTests` 三条，两个方向都反向验过 |

⚠️ **健康检查不许碰数据库**，这一条比它看起来重要：Render 会重启一个不回话的实例，
所以健康检查依赖什么，什么就有权把整个站点拖下去。数据库抖一下，
本该是「页面变慢」，会变成「所有实例被杀」—— 而被杀掉的那些正是唯一能报告出事了的东西。
平台问的是「这个进程还能应答吗」，不是「下游一切正常吗」。

⚠️ 守卫的第二条断言是「首页**仍然**跳转」。只写第一条的话，
把整站都豁免掉一样能让测试变绿 —— 而那时没有任何一个页面被强制上 HTTPS。

> **一般形式**：**平台的报错描述的是它观察到的现象，不是原因。**
> 「健康检查超时」是现象；原因可以是应用死了、端口不对、Host 不在 `ALLOWED_HOSTS` 里
> （那是 400），或者像这次一样 —— 应用完全正常，只是回了一个平台不接受的状态码。
> 这一类错误值不值得写守卫，判据是「读着它能不能找到原因」，而不是「它红不红」。

### C3.4 · 「短值一样零警告」是错的，而它错在验收上

2026-08-17。C3.4 原文写着：短的 HSTS 一样能拿到干净的 `check --deploy`，
因为「这条检查只看设置有没有值，不看值多大」。

前半句是真的，后半句把结论扩大了一倍。`W004` 确实只问「你设了没有」，
而**一旦设上，Django 会另外点亮两条**：`W005` 要 `SECURE_HSTS_INCLUDE_SUBDOMAINS`、
`W021` 要 `SECURE_HSTS_PRELOAD` —— 正是[两步走那张表](#c34-生产加固)里
写明「域名稳定之前绝不打开」的那两个。

于是这一步的验收和这一步的决定**直接冲突**：不可能既守住两步走、又拿到零警告。
⚠️ 而这不是一句写错了的话的问题 —— 它的下场是可以预测的：
`check --deploy` 长期挂着两条警告，然后所有人学会忽略它，
接着第三条真的警告出现时没有人看得见。**一个总是有警告的检查等于没有检查。**

处置是**让静默跟着值走**：

```python
_HSTS_IS_STILL_PROVISIONAL = SECURE_HSTS_SECONDS < 31536000
SILENCED_SYSTEM_CHECKS = (
    ["security.W005", "security.W021"] if _HSTS_IS_STILL_PROVISIONAL else [])
```

C5 把时长调到一年，这两条自己回来，问的恰好是 C5 那一步要打开的两个设置。

> **一般形式**：**要静默一条检查，就把静默挂在「当初为什么要静默」那个条件上，
> 而不是挂在一张清单上。** 清单不会过期，理由会 ——
> 而清单和理由分家的那一天，没有任何东西会说。

⚠️ 顺带记一条**没写进任何清单、只靠守卫抓到的**：`required=True` 的变量每加一个，
CI 里那步「用 prod settings 跑 collectstatic」就多一个要给的值。
这件事 2026-08-09 的体检已经发生过一次（接 R2 那天起 CI 就是红的，
红在缺变量上而不是它要守的那件事上）。这次加邮件变量是同一个形状，
所以顺手把它从注释升级成了守卫
（`RenderBlueprintGuardTests` 里那条 `test_the_ci_step_that_imports_prod_settings_has_every_required_variable`），
并且拿掉一行反向验过。
