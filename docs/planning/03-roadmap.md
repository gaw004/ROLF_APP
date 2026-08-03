# Phase C 实施手册 —— 上线与真实运营

> 这份文档只讲 Phase C **怎么做**。要做什么、为什么这么定、做完怎么算数，
> 在 [`phase-c.md`](phase-c.md)；和 [`goal.md`](goal.md) 冲突时以 `goal.md` 为准。
> [`01-roadmap.md`](01-roadmap.md) / [`02-roadmap.md`](02-roadmap.md) 是 Phase A / B 的手册，
> 已完成，留作记录，不再更新。
>
> 写于 2026-07-31。

## 开工时的实测基线

跑出来的，不是估的（2026-07-31，分支 `phase-b`，`HEAD` = `3b5c059`）：

| 项 | 实测值 |
|---|---|
| `python manage.py test` | **334 个，全绿**（22.8s） |
| `python manage.py check` | 0 issues, 0 silenced |
| `makemigrations --check` | No changes detected |
| `ruff check .` | All checks passed |
| Django / Python / Postgres | 5.2.16 / 3.14 / 18（psycopg 3） |
| 模板 | **16 个** `.html`（events 11 · accounts 2 · core 1 · org 1 · contact 1） |
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

四条硬性顺序，其余可以调：

1. **C0.2 补缺口必须在 C2 之前。** 补出来的是**五个新页面**；
   排在样式之后就要再排一遍。这也是 C0.2 插在验收之前的理由 ——
   拿一份不完整的功能去走浏览器验收，等于走两遍。
2. **C1 必须在 C2 之前。** Tailwind 构建先就位，模板才能一次过
   （同时上 class 和把文案改成英文）。反过来每个模板要动两遍。
3. **C3 的备份和权限复核必须在放真人之前。** 见
   [`phase-c.md` 的判据](phase-c.md#判据什么必须做完才能放真人什么可以边用边加) ——
   两个风险是乘法关系。
4. **C4 排在 C5 之后开工**（试点期间并行）。它们是可后补的，
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
其余是界面口径。** 全部做完，测试 334 → **404**。

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

### C0.4 修文档漂移，Phase B 标 ✅

1. ✅ **已做**：`02-roadmap.md` 的实测结果表里 `363` → **`334`**，
   并把「过期过两次」的经过记在同一格里；
2. `goal.md` 两处：第四节进度表里的「353 个测试全绿」、
   [六、下一步](goal.md#六下一步)里的「测试从 192 涨到 363」，都改成 C0.2 之后的实测数；
3. `goal.md` 第四节那张表的 Phase B 改成 ✅，并把「只差浏览器里那一遍」删掉 ——
   ⚠️ **要等 C0.2 和 C0.3 都做完**，别照原计划在补缺口之前就标；
4. `phase-c.md` 里[测试数基线的新口径](phase-c.md#测试数基线只增不减的新口径)已经写好，
   `02-roadmap.md` 的验收那条不改（它记录的是当时的口径）。

**验证**：`python manage.py test core.tests.MarkdownLinkGuardTests` 绿。

---

## C1 · Tailwind 构建

**目标**：让 C2 能一次过。**这一步不碰任何模板文案。**

> **原计划的 C1.1「i18n 骨架」已删除** —— [D23](decisions/D23-i18n-interface-only.md)
> 2026-07-31 当天改口：界面统一英文，不做双语。
> `LocaleMiddleware` / `LANGUAGES` / `LOCALE_PATHS` / `set_language` 一律不加。
> 那份方案原样留在 D23 折叠的那一节里，重启时照抄。

### C1.1 Tailwind 构建

用 **Tailwind standalone CLI**（单个二进制，**不引 Node / npm**）：

- `tailwind.config.js`：`content` 指向 `["./*/templates/**/*.html"]`；
- 源文件 `static/src/app.css`，产物 `static/css/app.css`，**产物提交进 git**；
- `.gitignore` 加 `tailwindcss`（那个二进制本身不进仓库）。

> **为什么提交产物**：Render 的构建就只剩 `pip install` + `collectstatic`，
> 不需要在生产环境装 Node。代价是改样式后要记得重跑一次 CLI ——
> 写进 `README.md`，并在 C3 的部署检查里带一条。

### C1.2 whitenoise

- `requirements.txt` 加 `whitenoise`；
- `MIDDLEWARE` 里 `whitenoise.middleware.WhiteNoiseMiddleware`
  **紧跟在 `SecurityMiddleware` 之后**；
- `STORAGES["staticfiles"]` 用
  `whitenoise.storage.CompressedManifestStaticFilesStorage` ——
  ⚠️ **只在 `prod.py` 里启用**。dev 里启用的话，没跑过 `collectstatic` 就会
  在渲染时抛 `Missing staticfiles manifest entry`。

**验证**：`python manage.py collectstatic --noinput` 成功；
`test` 334 全绿；`ruff check .` 干净。

---

## C2 · 模板逐页重写（Tailwind + 改成英文，一次过）

**目标**：页面好看、文案是英文。**一个模板只碰一次。**

⚠️ **是 21 个模板，不是 16 个** —— C0.2 新增了「我管理的活动」「往期活动」
「我的资料」「改活动」（复用 `event_form.html`）等几个页面。
**C0.2 里新写的模板直接写英文**，不要先写中文再来这一步改。

落点规矩见 [`phase-c.md` 的样式落点](phase-c.md#样式的落点css-只许出现在两个地方)
和[界面语言落点](phase-c.md#界面语言的落点英文写在哪中文允许留在哪)。

### C2.1 `base.html` 先做，它定调子

`core/templates/core/base.html`：版心宽度、字号阶梯、配色、导航条
（含 C0.2.4 加的那两个入口）、消息提示（`messages`）样式、页脚。

> 原计划这里还要加一个语言切换器，随 [D23](decisions/D23-i18n-interface-only.md)
> 改口一起删掉了。

这个文件的注释里已经写着「Phase C 替换这一个文件就能上样式，
views / forms / services 原样带走」—— 这一步就是兑现它。**注释要跟着更新**，
别留一句已经发生过的预告。

### C2.2 志愿者路径（用户最多，要手机友好）

`event_list` → `past_events` → `event_detail` → `event_signup` →
`my_participations` → `participation_cancel` → `accounts/profile`。

⚠️ `event_signup` 的未成年人同意分支（姓名 / 关系 / 方式 / **邮箱或电话至少一个**）
是这一组里唯一有条件显示的表单，重排时**别把「邮箱或电话至少一个」那句提示丢了** ——
丢了不报错，只是用户不知道为什么提交被拒。

### C2.3 ministry admin 路径（表单重、表格多）

`event_manage_list` → `event_form`（建 / 改共用）→ `event_roles` →
`event_registrations` → `event_attendance` → `event_report` → `event_notify`。

- 表格一律加**横向滚动容器**，否则窄屏上整页横向滚动；
- `event_report` 是 R4–R7 的落点，**数字全部来自 queryset**，
  重排时不许把任何计算搬进模板（`core/tests.py` 的守卫会红）；
- `event_notify` 的**「联系不上（N 人）」那一组必须显著** ——
  它是 P6 里唯一会静默失败的地方（见 [D22](decisions/D22-event-notifications.md)）。

### C2.4 账号页与其余

`accounts/register.html` → `accounts/login.html` →
`org/ministry_admins.html` → `contact/merge_confirm.html`。

### C2.5 Python 侧的文案改成英文

范围就是[界面语言落点](phase-c.md#界面语言的落点英文写在哪中文允许留在哪)那张表的左列：

- **~34 处** `label=` / `help_text=` / `verbose_name=`
  （`accounts/forms.py` 8 处、`contact/models.py` 11 处、`org/models.py` 8 处、
  `events/models.py` 3 处、`events/forms.py` 10 处、`contact/forms.py` / `org/forms.py` 各几处）；
- **10 处** `messages.*()` 和 **10 处** `ValidationError()` 的字面量；
- `org/permissions.py` 的 `SCOPED_DENIAL`（403 页面上给人看的）——
  **只改那个字符串，那个模块的逻辑一个字不动**；
- `TextChoices` 的 **label** 改英文，⚠️ **value 一个字不改**
  （它们在库里，改了就是数据迁移）；
- 约束的 `violation_error_message`（它会冒到表单上）—— 大部分**本来就是英文**，
  只改剩下的中文那几条。

**注释和 docstring 不动。** 本项目的推理都写在注释里，翻成英文是纯损失。

### C2.6 第 13 条 grep 守卫：模板里不许有中文

`core/tests.py` 加一条：**模板里出现中日韩字符 → 红**。
`{% comment %}` 块除外 —— 模板顶部那些解释性注释可以是中文。

> 这条比原计划那条（「未被 `{% trans %}` 包裹的中日韩字符」）**更强**：
> 那条只能查「忘了包」，这条直接查「有没有」，**没有漏网的中间态**。
> 单语的一个附带好处。

⚠️ **写这条测试时，别在注释里拼出它自己要找的那个模式** ——
守卫测试会扫自己，这个项目已经因此踩过四次（见 `README.md` 末节）。

**验证**：`test` 全绿且测试数比 C0.2 之后**又多一条**；
`check` / `ruff` 干净；浏览器走完三条路径；375px 宽度过一遍志愿者那几页。

---

## C3 · 交付前置

**目标**：做完这一段才可以放真实用户。见
[`phase-c.md` 的判据](phase-c.md#判据什么必须做完才能放真人什么可以边用边加)。

### C3.1 首页 `/`

`core/views.py`（现在只有一行注释）加 `home`，按角色分流：
未登录 → 介绍 + 登录/注册入口；志愿者 → 活动列表；
ministry admin → C0.2.4 那个「我管理的活动」；**foundation_admin → ministry 列表 + P5 的授权页**。

⚠️ **判断「是不是 ministry admin」只能问 `org/permissions.py`**，
不许在这个视图里碰 `MinistryRole.objects` —— 守卫测试盯着这条。
C0.2.4 已经把这两个判断放进上下文处理器了，这里直接用。

### C3.2 密码重置

挂 Django 自带的四个视图（`PasswordResetView` / `Done` / `Confirm` / `Complete`），
写四个模板 + 一封邮件模板（**英文**）。
`accounts/urls.py` 加四条路由，`base.html` 的登录页加入口。

口径见 [`phase-c.md` 的已知缺口](phase-c.md#五已知缺口与处置)：
**只服务自行注册的账号**，代录的 `Contact` 本来就不登录。

### C3.3 真实发信

- `prod.py` 配 SMTP（`EMAIL_HOST` / `PORT` / `USER` / `PASSWORD` / `USE_TLS`
  全走环境变量）+ `DEFAULT_FROM_EMAIL`；
- `NOTIFICATION_BACKEND` 在生产环境变量里指向
  `core.notifications.django_email.DjangoEmailBackend`
  （**适配器早就写好了，这一步只是接线**）。

**验证**：线上真的注册一个账号 → 收到邮件；改一场活动时间 → 通知发出去 → 收到。

### C3.4 生产加固

`config/settings/prod.py` 从空壳补齐：
`SECURE_SSL_REDIRECT` / `SECURE_HSTS_SECONDS` + `_INCLUDE_SUBDOMAINS` + `_PRELOAD` /
`SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` / `X_FRAME_OPTIONS` /
`SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`。

⚠️ **`SECURE_PROXY_SSL_HEADER` 不配，`SECURE_SSL_REDIRECT` 会造成无限重定向** ——
Render 在反代后面终止 TLS，应用看到的是 http。

**验证**：`python manage.py check --deploy` 零警告。这是跑得出来的数字，不是判断题。

### C3.5 部署到 Render

- `requirements.txt` 加 `gunicorn`；
- `render.yaml`：Web Service + PostgreSQL，健康检查指向 `/`；
- `build.sh`：`pip install -r requirements.txt` → `collectstatic --noinput` → `migrate`
  （原计划中间那步 `compilemessages` 随 [D23](decisions/D23-i18n-interface-only.md) 改口删掉）；
- 环境变量：`DJANGO_SETTINGS_MODULE=config.settings.prod`、
  **新生成的** `DJANGO_SECRET_KEY`、`DJANGO_ALLOWED_HOSTS`、SMTP 四项、
  `NOTIFICATION_BACKEND`（`DATABASE_URL` 由 Render 注入）；
- **先用 `xxx.onrender.com` 跑通**，域名留到 C5。

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

- 一个 shell 脚本：`pg_dump` → 上传对象存储（**不写成 management command**，
  理由见 [`phase-c.md` 的备份落点](phase-c.md#备份脚本的落点)）；
- Render Cron Job 每天跑一次；
- ⭐ **演练一次恢复**：取回 dump → 灌进空库 → `migrate --check` 通过 → 跑一遍测试。
  **三样都做了才算**，口径见
  [`phase-c.md`](phase-c.md#备份什么叫演练过)。

### C3.7 权限复核

四件事，拿真账号做，清单见
[`phase-c.md`](phase-c.md#权限复核拿真账号做的四件事)。

---

## C4 · 运营功能（试点期间并行）

顺序按 [`progress.md` 已定的](progress.md#phase-c--上线与真实运营)，**不改**：

1. **Ministry 视图** —— 各 ministry 下分 Leaders / Employees / Volunteers **加「空缺」**四组。
   ⚠️ **直接 `from org.services import build_org_tree`，不要自己递归 `reports_to`** ——
   环的兜底和 N+1 的规避都在那个函数里，Phase B 已写好并测过，
   而且 `core/tests.py` 有一条守卫盯着「只有 `org/services.py` 能走汇报链」；
2. **组织架构图** —— 同一步，只依赖 `Position` 一张表，不 join 任职数据；
3. **志愿者活跃排行、跨活动总工时** —— 靠 `Participation`。
   ⚠️ 口径写进 queryset 方法，**不写进视图**（守卫盯着「视图里没有 `Sum` / `Count`」）；
4. **CSV 导出**。

---

## C5 · 试点

- **一个 ministry、一场真实活动。** 影响面小、反馈直接。
- **顺序有依赖**：employee 先注册 → 给他们建 `Assignment` → 再办活动，
  否则 R8 会安静地返回空名单（见
  [`phase-c.md` 的已知缺口](phase-c.md#五已知缺口与处置)）。
- **域名在这一步买并挂上**：Render 加 custom domain + 自动证书。
  ⚠️ `DJANGO_ALLOWED_HOSTS` 和 `CSRF_TRUSTED_ORIGINS` **必须同时改** ——
  只改前者的话页面能打开，但所有 POST 表单被拒。
- 试点期间每周跑一次 `python manage.py list_duplicate_contacts`。

---

## 验收

- [ ] ⭐ **14 条需求每一条都能从某个链接点得到** —— 不是「service 写好了」，
      是「用户从哪进去」。这是 C0.2 那五处缺口的成因，见
      [`phase-c.md`](phase-c.md#phase-b-的五处缺口2026-07-31-发现)
- [ ] `python manage.py test` 全绿；测试数**高于 334**（下降必须伴随一次功能删除，
      口径见 [`phase-c.md`](phase-c.md#测试数基线只增不减的新口径)）
- [ ] `check` 零警告 / `makemigrations --check` 无变更 / `ruff check .` 干净
- [ ] `python manage.py check --deploy` **零警告**
- [ ] 中英各切一遍，三条路径走通；375px 宽度可用
- [ ] 把 `static/css/app.css` 删掉，页面**仍然可用**（判据见
      [`phase-c.md`](phase-c.md#样式的落点css-只许出现在两个地方)）
- [ ] **备份恢复演练三样都做过**
- [ ] ⭐ **越权实测**：A ministry 的 admin 打 B ministry 三个 URL 全 403；
      志愿者打 `/admin/` 得 403
- [ ] 线上完成一次「注册 → 收密码重置邮件 → 改密 → 登录」
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
