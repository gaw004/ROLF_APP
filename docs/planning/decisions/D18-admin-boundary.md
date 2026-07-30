# D18 · Admin 的边界，以及业务逻辑的落点（2026-07-28）

> 本文件是 `../goal.md` 拆出来的一条决策记录（2026-07-30 拆分，内容一字未改）。
> **`goal.md` 仍是唯一入口**：决策一览表和「去哪找」都在那里，
> 代码注释里写的 `goal.md D18` 指的就是本文件。

> 起因：一轮外部评审指出文档"把 Django Admin 当业务系统"，并举了三个例子
> （关系的 A/B 侧录入、重名只警告不拦截、`is_minor` 要写 `SimpleListFilter`）。
> **三个例子里只有第一个支持它的归因**，但它戳中了一个真实空缺：
> 文档从没写下 Admin 能承载什么、什么时候必须写真表单。 D2 只说"前端推迟"，没有判据。

> **怎么读本文里的"Phase C"**（2026-07-30 加的一句总注，本条写于 2026-07-28）：
> 那时的排期是"Phase C 才开始写自己的页面"。**这件事已经提前发生了** ——
> D18 自己的形状触发当天就赶出了 `/contacts/merge/` 和 `/relationships/add/`，
> [D21](D21-self-service-and-permissions.md#d21--对外账号志愿者自助页面提前权限成为它的前置条件2026-07-29) 又把整套志愿者自助页面提进了 Phase B。
> 所以下文凡是"Phase C 的视图 / 前端上来"，**读作"我们自己写的页面"**，
> 它已经是现在进行时；真正留在 Phase C 的只剩 Ministry 视图和组织架构图那一批
> （见 [Phase C](../progress.md#phase-c--上线与真实运营)）。**论证一个字没变，只是时间到了。**

先划清一件事：Admin 不是问题，"业务逻辑长在 Admin 里"才是。

前端推迟（D2）现在依然成立 —— 光是 2026-07-28 这一天，模型就改了两轮
（拆出 `Position`、唯一约束全面换成表达式版）。**模型还在动的时候写界面等于白写**，
这一天本身就是证据。所以答案不是"现在就上 HTMX"，而是：

> 把业务逻辑放在将来的界面够得着的地方。 界面可以推迟，落点不能推迟 ——
> 逻辑一旦长进 `ModelAdmin` 的钩子里，Phase C 写 HTMX 视图时只有两条路：抄一遍，或者重构。

## 逻辑落点的硬规矩（成本为零，现在就要守）

| 逻辑 | 放哪 | 不许放哪 |
|---|---|---|
| 跨表写入（合并 Contact、关系方向路由） | `services.py` 或 model 方法 | ❌ `ModelAdmin.save_model()` / `save_related()` |
| 派生判定（`.active()`、`vacant()`、`is_minor`、`find_exact_duplicates()`） | QuerySet 方法 / model property | ❌ admin 的 `get_queryset()` 里就地算 |
| 校验 | 数据库约束（唯一规则）+ 约束名→字段的映射表，见 D14 | ❌ `ModelForm.clean()` 里写唯一真相；❌ 把规则在 `clean()` 里重写一遍 |
| 纯呈现（列怎么排、筛选器长什么样、字段显不显示） | ✅ **admin，本来就该在这** | |

判据一句话：换一个界面，这段代码要不要跟着搬？要搬，就不该在 admin 里。

文档里已有的东西大都合规（合并走 `contact/services.py` 的 `merge_contacts()`、`.active()` 是 QuerySet），
**这条规矩主要是防止 Phase B 新写的东西跑偏** —— 尤其是紧急联系人的
"就地自动创建 + 命中预选"，那是最容易顺手写进 `ModelAdmin` 的一块。

## 代码落点与文件分层：什么会随升级坏，什么换界面还用得上（2026-07-28 补）

> 起因：一轮外部评审说"在 `ModelAdmin` 和 `ModelForm` 里塞定制逻辑，Django 大版本升级会很痛"。
> **它把 `ModelForm` 和 admin 内部 API 并列了，那是错的** —— 但它问的问题
> （"升级会不会逼我重构""前端上来了这些还能不能用"）正是本项目最该回答的两个问题，
> 而 D18 原文只给了判据、没给**落点**。这一节补上。

**先把"会坏"这件事说准。** Django 有正式的向后兼容承诺，**它覆盖文档化的 API，
不覆盖 admin 生成的 HTML 结构和 CSS class**。分界线就在这里：

| 写的东西 | 属于哪一层 | 升级会坏吗 | 换界面要搬吗 |
|---|---|---|---|
| `models.py`：字段、约束、QuerySet 方法、`find_exact_duplicates()` | ORM，公开 API | 不会 | ❌ 不用搬 |
| `services.py`：普通 Python 函数 | 自己的代码 | 不会 | ❌ 不用搬 |
| `forms.py`：`ModelForm` 子类、`clean()`、加非模型字段、改 `choices` | `django.forms`，与 admin 无关 | 不会 | ❌ **不用搬，原样复用** |
| `admin.py`：`list_display` / `inlines` / `form = XxxForm` | 公开 API，只是啰嗦 | 基本不会 | ✅ 搬不了，但**本来就是一次性的**，直接删 |
| 注入 admin 的 JS，靠 `.form-row` 之类找元素 | admin 的 HTML 结构，不在兼容承诺内 | ✅ **会，而且不报错** | ✅ 全丢 |

结论：全项目只有最后一行是赌注。 现在的两处是
`contact/static/contact/admin/contact_type_toggle.js` 和 `address_state_toggle.js`
（前者第 21 行 `field.closest(".form-row")`）。
`force_save` 虚拟字段（`forms.BooleanField(required=False)`）和方向感知下拉（重写 `choices`）
**都在第三行，不在第五行** —— 它们是 `django.forms`，跟 admin 一点关系没有，只是暂时借 admin 显示。

### 判据的等价说法（比"要不要搬"更好操作）

> 把 `admin.py` 整个删掉，系统还剩下什么？
> 剩下的必须是**全部业务逻辑**。`admin.py` 里剩的应该只有配置，删了不心疼。

按这个标准，Phase B 的文件是这样分的：

```
contact/
  models.py     约束、find_exact_duplicates()、.active() / .serving() / .minors()
                → 永久资产
  services.py   跨表写入：orient() / direction_choices() / merge_contacts()
                → 永久资产
  forms.py      ContactForm（含 force_save）、RelationshipForm（方向感知下拉）
                → 永久资产，Phase C 的视图 import 同一个类
  admin.py      form = ContactForm、inlines、list_display、SimpleListFilter
                → 纯配置，前端上来直接删
  static/*.js   ⚠️ 唯一会随 Django 升级坏、也是唯一换界面会丢的东西
  views.py      ✅ 本阶段就写了（/contacts/merge/、/relationships/add/），
                import forms.py + services.py。原文写的是"Phase C 才写"——
                D18 自己的形状触发当天就把这两个页面赶出了 admin

org/
  models.py     Position / Assignment 的约束、PositionQuerySet.vacant()、.serving()
                → 永久资产
  services.py   build_org_tree() —— 全项目唯一一处遍历汇报链（见「汇报线的环」）
                → 永久资产，Phase C 的组织架构图 import 同一个函数
```

同一个 `ContactForm`，今天挂在 `ModelAdmin.form` 上，明天挂在自己写的 `CreateView` 上。
它不是"为 admin 写的"，是"为业务写的、顺便先给 admin 用"。

### 四条现在就要守的规矩（成本≈0）

1. **`services.py` 现在就建**，别等有需要再建。关系方向路由、合并流程的编排放这里。
   建空文件成本为零，以后不用搬家（同 D17 的道理：跨文件搬代码比一开始放对贵得多）。
2. `forms.py` 不许 import 任何 admin 的东西。 加一条 grep 守卫测试：
   `contact/forms.py` 等文件里出现 `django.contrib.admin` 就变红。
   **这是文档第五次用"测试当 lint"**（迁移守卫 / D16 / `bulk_create` / D14 映射表 / 本条）。
   守住这一条，"表单能不能复用"就不再是一句承诺，而是机器检查的事实。
3. **JS 那两段要注明它是全项目唯一的 admin 内部结构依赖**，并在代码注释里写死一句
   "Django 大版本升级后必须人工点一遍：切 contact_type 看字段隐没隐、切国家看州字段变没变"。
   它坏了不报错，所以只能靠这一句提醒。**不为它写测试** —— 前端上来它就没了，不值得。
4. **要动 admin 模板、`AdminSite`、或 admin 首页的，一律不做**，改写成一个朴素的
   Django 视图。判据见下面「什么时候 Admin 整体不够用了」新增的第二条触发。

## Admin 允许承载什么

| ✅ Admin 够用 | ⚠️ 要写自定义 `Form`（仍在 admin 里） | ❌ 要等自己写的页面（Phase C+） |
|---|---|---|
| 单表 CRUD、字典表维护、只读查看（含只读 inline）、列表筛选导出 | **需要拦截确认的流程**（重名硬拦截 `force_save`）、**一个表单写多张表**（联系人 + 紧急联系人 inline） | 多步流程、跨记录批量操作、**表单需要"当前是谁的页面"这类上下文**（关系录入）、面向外部用户的任何东西、Ministry 视图这类聚合页 |

**中间那一栏是关键**：它不需要放弃 Admin，只需要放弃"默认表单 = 表结构"。
一个 `ModelForm` 子类就能把 A/B 这种物理细节封在里面 —— **这是 Admin 的正常用法，不是绕过它**。
而且按上一节的分层，`ModelForm` 是 `django.forms`，**前端上来原样复用，一行不改**。

> **"多步流程"在这张表里的准确含义：需要跨请求保持状态**（第一步存了东西、第二步才提交，
> 中间要记住第一步）。**一次表单被重新提交不算** —— 所以「同名同号硬拦截 + `force_save`」
> 在中间栏，不在 ❌ 栏：它是同一个表单交两次，服务端不记任何东西，
> Django 自己的删除确认也是这个形状。
> 这条要写死，否则中间那一栏会变成一个什么都能塞的口袋。

## 什么时候 Admin 整体不够用了

两条触发条件，满足任一条就出栏。

> 2026-07-29 更新：触发一已经发生了，**比本条原文预计的早了两个阶段**。
> 原文写的是"非开发者拿到账号，按现在的排期是 Phase D，不必等到那时候" ——
> 而 [D21](D21-self-service-and-permissions.md#d21--对外账号志愿者自助页面提前权限成为它的前置条件2026-07-29) 把志愿者自助页面提到了 Phase B，
> 志愿者**就是**非开发者、**现在**就要拿到账号。所以下面两条不再是"那时必须到位"，
> 是**本阶段必须到位**，而且权限要先于页面。

触发一（事件）：非开发者拿到账号。 **已触发（Phase B，D21）**。同时必须到位的是：

1. 上面「❌」那一栏的页面；
2. **权限**（原来列在上线阶段的交付前置条件，D21 之后提前成了自助页面自己的前置）——
   而权限有一个**现在就要付、拖了要做数据迁移**的代价，见下。

触发二（形状）：这段功能需要跨请求保持状态，或需要动 admin 模板 / `AdminSite` / admin 首页。
—— 这两样正好是上一节表里"会随升级坏"和"换界面全丢"的那一格。
碰到就**立刻写一个朴素的 Django 视图**（`views.py` + 一个模板），业务逻辑照旧在
`models.py` / `services.py` 里，视图只是薄壳。

> **为什么要加这条形状触发**：原来只有事件触发，那是一个**日期**，不是一个**信号** ——
> 而"⚠️ 要写自定义 `Form`"那一栏没有上限，每个新流程都能论证自己属于它。
> D18 本来就是为了防这种滑坡才写的，所以它自己必须带一个出栏条件。
> **本阶段被这条触发的有两处**：「合并重复记录」和「关系录入」，见 Phase B 各自那一节。

"需要上下文"也算形状触发。 关系录入是这么出栏的：那个表单的全部前提是
"当前站在谁的页面上"（`subject`），而 **admin 的 inline 表单默认拿不到父对象** ——
要拿到得覆盖 `InlineModelAdmin.get_formset()` 或自定义 `BaseInlineFormSet._construct_form`，
**那是 admin 最深的一处管道，而且 Phase C 用 HTMX 时根本不会用 formset**。
判据可以直接问：这段代码买到的东西，前端上来还留得住吗？留不住就别买。

## ⚠️ 权限的形状会倒推模型：敏感字段必须独立成模型

Django 的权限粒度是 **`app_label.model_codename`**，**没有字段级权限**。
D17 让 `payroll` 独立成 app 正是这个道理（"一个 Group 直接不给 `payroll.*`"）。

**同一条逻辑对背景审查同样成立，而文档漏了**：
`background_check_status` 现在是 `VolunteerProfile` 上的一个字段，
而文档自己说它"敏感度仅次于薪酬"。**你没法只禁掉一个字段** ——
要么整张 `VolunteerProfile` 不给看（连技能和可服务时段一起锁掉，过度），要么全给看（泄露）。

**所以背景审查必须是独立模型**（`volunteer.BackgroundCheck`，OneToOne → `Contact`），
不留在 `VolunteerProfile` 里。这样上线时一个 Group 不授 `volunteer.view_backgroundcheck` 即可。

- **拆开的成本≈0** —— `volunteer` app 一行代码还没写；
- **合着建、以后再拆要做数据迁移** —— 建表、搬两个字段、改所有引用，
  而且那时表里是真实的人的审查结果。

注意 `payroll` 用了整个 app、背景审查只用一个 model，
区别在于薪酬是一整块业务领域，背景审查只是一张附属表。

> 2026-07-29 修订：这一条的**时机**变了，**结论没变**。
> 原文写的是"**所以背景审查在 Phase B 就拆成独立模型**……**按 Phase A 的准入标准，
> 这条属于必须现在做**"。而 `VolunteerProfile` / `BackgroundCheck` **两张表整体移出了
> Phase B**（[零的「排除了什么」](../goal.md#排除了什么以及它们去哪了)：14 条需求一条都没碰背景审查）。
>
> **推迟的是建表，不是推翻拆表** —— 两张表将来建的时候仍然是两个 model，
> 因为上面那个理由（没有字段级权限）跟哪个阶段建它无关。
> 本条现在读作：**建它的那一天，它必须是独立的 model**。见[推迟清单](../deferred.md#五明确推迟的事)。
