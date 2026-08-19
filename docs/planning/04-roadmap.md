# Phase C 前端分册 —— 构建链、设计系统、20 个模板

> 这一份是 **Phase C 的前端部分**（C1 / C2），从 [`03-roadmap.md`](03-roadmap.md) 拆出来。
> Phase C 的其余步骤（C0 · C0.5 · C3 · C4 · C5）仍在 `03-roadmap.md`。
>
> **为什么拆**：2026-08-03 前端目标从「上个样式」改成
> 「Tailwind + HTMX + Alpine，现代且经得起看」（[D24](decisions/D24-htmx-alpine-tailwind.md)），
> C1 / C2 两步装不下了。
> ⚠️ 这**破了「一个 Phase 一份 `0N-roadmap.md`」的约定**——处置写在
> [`goal.md` 的约定 2](goal.md#-文件地图2026-07-30-拆分)：
> `04` 归 Phase C 的前端分册，**Phase D 的手册用 `05-roadmap.md`**。
>
> 要做什么、为什么这么定，在 [`phase-c.md`](phase-c.md)；
> 颜色 / 字号 / 组件 / HTMX 与 Alpine 的具体用法在 [`design-system.md`](design-system.md)；
> 和 [`goal.md`](goal.md) 冲突时以它的[零、当前优先级](goal.md#零当前优先级2026-07-29-定)为准。
>
> 写于 2026-08-03。

## 进度：整份做完了（2026-08-17 复核）

**C1 ✅ · C2 ✅。** 构建链、设计系统、20 个模板全部落地，
[C2 的验收表](#c26-两条新守卫)六行全过（一个组件、删掉 CSS 仍可用、关掉 JS 六个写操作走得完、
375px 不横向滚、深浅两色各一遍）。C2 之后又长出来一批不在本册计划内的东西：
[D25](decisions/D25-public-front-page.md) 首页、[D26](decisions/D26-palette-from-the-hero.md) 品牌色、
[D27](decisions/D27-ministry-report.md) 报表、[D28](decisions/D28-qr-checkin.md) 扫码签到、
[D29](decisions/D29-memories-wall.md) 照片墙 —— 它们的坑都记在下面的[计划外记录](#计划外记录)里。

⚠️ 唯一还挂着的是一处**文档漂移**：[C1.1](#c11-npm-与三个依赖) 正文写的是源文件放
`static/src/`，实际落在 `assets/`（`ManifestStaticFilesStorage` 会去解析
`@import "tailwindcss"` 然后让部署当场失败）。经过记在计划外记录里，正文没有回改。

Phase C 其余各步的进度在 [`03-roadmap.md` 的进度表](03-roadmap.md#进度2026-08-17-收盘)。

## 编号没有变

C1 / C2 **还是叫 C1 / C2**，只是正文搬到了这里——同 `goal.md` 拆分时的做法：
**正文搬走了，「见 C2.5」这种引用仍然成立**，因为 `03-roadmap.md` 一定能把你导过来。

## 顺序：为什么 C1 必须在 C2 之前

构建链先就位，模板才能**一次过**——同时上 Tailwind class、写 `dark:` 对、
接 HTMX / Alpine、把文案改成英文。反过来每个模板要动两遍甚至四遍。

而 C1 / C2 整体必须在 [C0.5](03-roadmap.md#c05--上线前的三条死链) 之后：
那一步产出的是 403 / 404 / 500 三个模板和一批文案，
排在样式之后就要再排一遍——和当初「C0.2 必须先于 C2」是同一条理由。

⚠️ **[C3.0](03-roadmap.md#c30-域名--发信服务--最先做因为它靠别人)（买域名 → 发信服务的域名验证）
建议在 C1 开工当天就启动。** 它是整个 Phase C 里唯一靠别人的事（DNS 生效、
当初还要等 SES 的沙箱审核），让这段等待和前端工作重叠，等于白赚两天。

---

## C1 · 构建链

**目标**：让 C2 能一次过。**这一步不碰任何模板文案。**

> 原计划的 **C1.1「i18n 骨架」已删除**——[D23](decisions/D23-i18n-interface-only.md)
> 2026-07-31 改口：界面统一英文，不做双语。
> `LocaleMiddleware` / `LANGUAGES` / `LOCALE_PATHS` / `set_language` 一律不加。
> 那份方案原样留在 D23 折叠的那一节里，重启时照抄。
>
> 原计划的「Tailwind standalone CLI，不引 Node」**2026-08-03 推翻**，改走 npm，
> 理由是要装的不止 Tailwind 一样东西（还有 htmx、alpine，以及将来的插件）。

### C1.1 npm 与三个依赖

- `package.json`：`tailwindcss`、`htmx.org`、`alpinejs`，外加构建脚本；
- 源文件 `static/src/app.css`（Tailwind 入口 + `@theme` 里的
  [设计令牌](design-system.md#一设计令牌)）和 `static/src/js/app.js`
  （引 htmx 和 alpine，注册那几个具名的 Alpine 组件）；
- 产物 `static/css/app.css` 和 `static/js/app.js`；
- `content` 指向 `["./*/templates/**/*.html"]`——⚠️ **漏配这一行的表现是
  样式在开发时正常、构建后大面积消失**，因为 Tailwind 扫不到模板就把 class 全摇掉了。

> ⚠️ **上面这两条实施时都改掉了**（2026-08-03），各有一次实测：
> 源文件从 `static/src/` 挪到了 **`assets/`**（放 `static/` 里会让部署当天的
> `collectstatic` 直接失败），`content` 那条在 Tailwind v4 上**不成立**，
> 而真正要写的是另一样东西。经过在[计划外记录](#c11--源文件不能放在-static-里面)。

### C1.1 的实际落点（2026-08-03 实测）

| 项 | 值 |
|---|---|
| 版本 | tailwindcss 4.3.3 · htmx.org 2.0.10 · alpinejs 3.15.12 · esbuild 0.28.1 |
| 源文件 | `assets/app.css` · `assets/js/app.js`（**不是** `static/src/`，见下） |
| 产物 | `static/css/app.css`（6.5 KB，模板还没上 class）· `static/js/app.js`（104.6 KB） |
| 构建 | `npm run build` = Tailwind CLI + esbuild bundle，实测 **65ms** |
| JS 打包器 | **esbuild** —— 原计划没提。`app.js` 要 `import` htmx 和 alpine，浏览器吃不了裸 `import`，得有人把它们打成一个文件 |
| 扫描口径 | `@import "tailwindcss" source(none)` + 五条 `@source` 指到各 app 的 `templates/` |
| 令牌 | 品牌 10 档 · 中性 11 档 · 语义 4 组 ×（`-fg`/`-bg`/`-fg-dark`/`-bg-dark`） |
| 新守卫 | `core.tests.ContrastGuardTests` —— 对比度做成跑得出来的数字，见下 |

### C1.2 产物走 CI，不进主分支

**GitHub Actions 构建 → 推部署分支**，Render 盯那个分支。

- 一个 workflow：`npm ci && npm run build` → 把 `static/css` / `static/js` 的产物
  提交到部署分支；
- **Render 的部署分支不是 `main`**——这一条要写进 `render.yaml` 和 C3.5，
  否则会出现「合进 main 了但线上没变」，而且看不出为什么。

> **为什么不在 Render 上装 Node**：Render 的 Python runtime 不带 Node，
> 要么上 Dockerfile（多一层要维护的东西），要么把构建搬到 CI。
> 选后者，Render 的构建仍然只剩 `pip install` + `collectstatic` + `migrate`。
>
> **为什么不直接把产物提交进 `main`**：改样式后忘记重跑构建这件事**一定会发生**，
> 而它的表现是线上样式停在上一版、没有任何报错。交给 CI 就不存在「忘记」。

### C1.3 两层守卫防御接进 CI

[C0.5](03-roadmap.md#c05--上线前的三条死链) 已经建好
`.pre-commit-config.yaml` 和 CI 的 workflow，这里只是把前端那几步接进同一个 workflow：
`npm run build` 之后跑一次 `test` / `check` / `ruff`，**红灯禁止合并**。

### C1.4 whitenoise

- `requirements.txt` 加 `whitenoise`；
- `MIDDLEWARE` 里 `whitenoise.middleware.WhiteNoiseMiddleware`
  **紧跟在 `SecurityMiddleware` 之后**；
- `STORAGES["staticfiles"]` 用
  `whitenoise.storage.CompressedManifestStaticFilesStorage`——
  ⚠️ **只在 `prod.py` 里启用**。dev 里启用的话，没跑过 `collectstatic` 就会
  在渲染时抛 `Missing staticfiles manifest entry`。

**验证**：`npm run build` 产出两个文件；
`python manage.py collectstatic --noinput` 成功；
`test` 全绿且测试数不低于 C0.5 收尾时的实测数；`ruff check .` 干净。

**实测结果（2026-08-03，C1 四步全部做完）**：

| 项 | 结果 |
|---|---|
| `npm run build` | 两个产物都在（CSS 6.5 KB · JS 104.6 KB），65ms |
| `collectstatic`（dev） | 387 files copied |
| `collectstatic`（**prod 的 Manifest 存储**） | 653 post-processed，产物带 hash 和 `.gz` |
| `python manage.py test` | **411 个，全绿**（37.0s）—— C0.5 收尾是 409，对比度守卫带来 2 个 |
| `check` / `makemigrations --check` / `ruff check .` | 干净 / No changes / All checks passed |
| 新增文件 | `package.json` · `package-lock.json` · `assets/app.css` · `assets/js/app.js` · `.github/workflows/deploy-branch.yml` |
| 改动 | `base.py`（whitenoise 中间件 + `STATICFILES_DIRS`）· `prod.py`（Manifest 存储）· `requirements.txt`（whitenoise）· `.gitignore`（产物不进 git）· `ci.yml`（接前端 + prod collectstatic） |

⚠️ **CI 里多加了一步不在计划里的**：用 **prod 的 settings** 跑一遍 `collectstatic`。
dev 用的是普通存储，什么都不检查；会失败的是 prod 的
`CompressedManifestStaticFilesStorage`，而它失败的时刻本来是**部署当天**。
这一步把那一刻提前到 PR 上 —— 它正是下面第一条计划外记录的守卫。

---

## C2 · 设计系统与 20 个模板

**目标**：页面好看、文案是英文、深浅两色都对。**一个模板只碰一次。**

⚠️ **是 20 个模板**（events 13 · accounts 3 · org 2 · core 1 · contact 1）。
本文档一度写「16 个」和「21 个」，都错过——16 是 C0.2 之前的数，21 是估的。
C0.5 会加 3 个错误页、C3.2 会再加 4–5 个密码重置模板，**它们各自在自己那一步里写好样式**，
不回到这一步。

落点规矩见 [`phase-c.md` 三、落点规矩](phase-c.md#三落点规矩)；
颜色 / 字号 / 组件 / HTMX 与 Alpine 的具体写法**全部在
[`design-system.md`](design-system.md)**，这里不重复。

### C2.1 写 `design-system.md`，再做 `base.html`

顺序不能反：**先把词汇表写下来，再动第一个模板**。
反过来做的结果是 `base.html` 变成事实上的规范，而它只存在于代码里，
半年后新页面必然跑偏。

`core/templates/core/base.html` 这一步要拿到的东西：
版心宽度、字号阶梯、配色、导航条（含 C0.2.4 加的那两个管理入口）、
消息提示样式、页脚、**深色模式那段内联脚本**、`hx-headers` 的 CSRF、
以及 `core/templates/core/components/` 下的那套组件片段。

⚠️ **深色模式的内联脚本必须在 `<head>` 里、在样式表之后**，
放别处会先按浅色画一遍再跳成深色。详见
[design-system.md 二、深色模式](design-system.md#二深色模式)。

这个文件的注释里写着「Phase C 替换这一个文件就能上样式，
views / forms / services 原样带走」——这一步就是兑现它。**注释要跟着更新**，
别留一句已经发生过的预告。

### C2.2 志愿者路径（用户最多，要手机友好）

`event_list` → ~~`past_events`~~ → `event_detail` → `event_signup` →
`my_participations` → `participation_cancel` → `accounts/profile`。

- HTMX 用在 `event_list` / ~~`past_events`~~ 的时间段筛选（2026-08-17：后者整页删了，前者的筛选改成实时）、
  `accounts/profile` 的增删紧急联系人；
- ⚠️ `event_signup` 的未成年人同意分支（姓名 / 关系 / 方式 / **邮箱或电话至少一个**）
  是这一组里唯一有条件显示的表单。它的显隐可以交给 Alpine，
  但**别把「邮箱或电话至少一个」那句提示丢了**——丢了不报错，
  只是用户不知道为什么提交被拒。

### C2.3 ministry admin 路径（表单重、表格多）

`event_manage_list` → `event_form`（建 / 改共用）→ `event_roles` →
`event_registrations` → `event_attendance` → `event_report` → `event_notify`。

- 表格一律加**横向滚动容器**，否则窄屏上整页横向滚动；
- HTMX 用在 `event_attendance` 的逐人签到签退、`event_roles` 的增删工种。
  ⭐ **两处都必须保留一条不依赖 JavaScript 的表单路径**——
  口径见 [D24](decisions/D24-htmx-alpine-tailwind.md#渐进增强的口径只管写操作)；
- `event_report` 是 R4–R7 的落点，**数字全部来自 queryset**，
  重排时不许把任何计算搬进模板（`core/tests.py` 的守卫会红）；
- `event_notify` 的**「联系不上（N 人）」那一组必须显著**——
  它是 P6 里唯一会静默失败的地方（见 [D22](decisions/D22-event-notifications.md)）。

### C2.4 账号页与其余

`accounts/register.html` → `accounts/login.html` →
`org/ministry_list.html` → `org/ministry_admins.html` → `contact/merge_confirm.html`。

### C2.5 Python 侧的文案改成英文

范围就是[界面语言落点](phase-c.md#界面语言的落点英文写在哪中文允许留在哪)那张表的左列：

- **~34 处** `label=` / `help_text=` / `verbose_name=`
  （`accounts/forms.py` 8 处、`contact/models.py` 11 处、`org/models.py` 8 处、
  `events/models.py` 3 处、`events/forms.py` 10 处、`contact/forms.py` / `org/forms.py` 各几处）；
- **10 处** `messages.*()` 和 **10 处** `ValidationError()` 的字面量；
- `org/permissions.py` 的 `SCOPED_DENIAL`——**只改那个字符串，那个模块的逻辑一个字不动**。
  ⚠️ 改它之前先确认 [C0.5](03-roadmap.md#c05--上线前的三条死链) 的 `403.html` 已经做了，
  否则这是一次**没有任何效果**的修改：没有那个模板，这段文案根本不会出现在任何页面上；
- `TextChoices` 的 **label** 改英文，⚠️ **value 一个字不改**
  （它们在库里，改了就是数据迁移）；
- 约束的 `violation_error_message`（它会冒到表单上）——大部分**本来就是英文**，
  只改剩下的中文那几条。

**注释和 docstring 不动。** 本项目的推理都写在注释里，翻成英文是纯损失。

### C2.6 两条新守卫

1. **模板里不许有中日韩字符**（`{% comment %}` 块除外）。
   这条比原计划那条（「未被 `{% trans %}` 包裹的中日韩字符」）**更强**：
   那条只能查「忘了包」，这条直接查「有没有」，没有漏网的中间态；
2. **`x-` 属性里不许出现业务判断**——权限、工时 / 金额计算、日期运算的关键字。
   理由和实现口径在 [design-system.md 五、Alpine 的用法](design-system.md#五alpine-的用法)。

⚠️ **写这两条测试时，别在注释里拼出它自己要找的那个模式**——
守卫测试会扫自己，这个项目已经因此踩过四次（见 `README.md` 末节）。

**验证**：

- `test` 全绿，且测试数比 C1 之后**又多两条**；
- `check` / `makemigrations --check` / `ruff` 干净；
- 浏览器走完三条路径，**深浅两色各一遍**；
- **375px 宽度**过一遍志愿者那几页；
- [`design-system.md` 六、验收](design-system.md#六验收)那七条逐条勾——
  尤其「删掉 `app.css` 页面仍可用」和「删掉所有 `x-` 属性写操作仍能完成」这两条。

**实测结果（2026-08-04，C2 六步全部做完）**：

| 项 | 结果 |
|---|---|
| `python manage.py test` | **414 个，全绿**（37.9s）—— C1 收尾是 411，两条新守卫 +2、一条零工时回归测试 +1 |
| `check` / `makemigrations --check` / `ruff check .` | 干净 / No changes / All checks passed |
| 重写的模板 | 20 个，各只碰一次 |
| 新增的组件片段 | `core/templates/core/components/` 下 8 个：`button` · `_button_tag` · `_nav_link` · `field` · `form_fields` · `badge` · `empty` · `messages` · `_messages_oob` |
| 新增的 HTMX 片段 | `_event_list_results` · ~~`_past_events_results`~~（2026-08-17 删）· `_period_filter` · `_event_roles_panel` · `_event_roles_swap` · `_attendance_row` · `_attendance_row_swap` · `_event_nav` · `_event_nav_link` |
| 新增的迁移 | `events/0006_consent_method_labels_in_english`（**只改 label，value 一个字没动**） |
| 新增的守卫 | `InterfaceLanguageGuardTests`（模板里注释块之外不许有中日韩字符）· `AlpineStaysUiOnlyGuardTests`（`x-` 属性里不许有权限 / 工时 / 日期运算） |

**七条验收逐条的结果**：

| 判据 | 怎么验的 | 结果 |
|---|---|---|
| 两个页面的主按钮像同一个产品 | 截图并排看 | ✅ 全部走 `button.html` 一个组件，没有第二种写法 |
| 删掉 `app.css` 页面仍然可用 | 去掉 `<link>` 重新渲染签到页截图 | ✅ 每个按钮都在、每条信息都读得到；状态标签带文字 |
| 删掉所有 `x-` 属性写操作仍能完成 | **414 个测试本身就是这一条** —— 它们从不发 `HX-Request`，走的全是服务端表单路径 | ✅ |
| 关掉 JavaScript，六个写操作走得完 | 同上，加上每处 `hx-post` 都配了 `method="post" action=`| ✅ |
| 375px 不横向滚动 | 见下面[计划外记录](#c22--375px-的横向滚动量出来的和看出来的不是一回事) | ✅ 八个页面 `scrollWidth == 375` |
| 深浅两色各切一遍 | 每页两张截图 | ✅ |
| 只用键盘走完一次报名 | 焦点环写在 `app.css` 的 `:focus-visible`，skip link 在 `base.html` | ⏳ **留给浏览器那一遍**，键盘顺序机器验不了 |

⚠️ **一处文档打架，这里定了**：`03-roadmap.md` 的 C0.5.2 写「三个错误页的样式留到 C2 统一上」，
而本文档 C2 开头写「C0.5 加的三个错误页各自在自己那一步里写好样式，不回到这一步」。
**按 `03-roadmap.md` 那条办**：403 / 404 在 C2 里跟着 `base.html` 一起上了样式
（它们 `extends base.html`，本来就是顺带的）；**500 仍然是自足的**，不继承、自带内联样式，
理由见 [`03-roadmap.md` 的计划外记录](03-roadmap.md#c052--500html-不能-extends-basehtml)。

---

## 计划外记录

> **实施时才发现的坑记在这里。** 这一节是这个项目最贵的资产之一
> （见 [`goal.md` 的约定 2](goal.md#-文件地图2026-07-30-拆分)）——一条都不删。

### C1.1 · 源文件不能放在 `static/` 里面

计划写的是 `static/src/app.css`。**照着做会在部署当天失败**，而且在本机怎么跑都发现不了。

`static/` 是 `collectstatic` 要收走并对外提供的目录。生产用的
`CompressedManifestStaticFilesStorage` 会**解析每个收进来的 CSS 里的 `@import`
和 `url()`**，把它们当成静态文件去找。而 Tailwind 入口文件的第一行正是：

```css
@import "tailwindcss";
```

它不是一个文件。实测（把源文件放回 `static/src/` 复现的）：

```
Post-processing 'src/app.css' failed!
MissingFileError: The file 'src/tailwindcss' could not be found
```

⚠️ **在本机永远看不到它** —— dev 用的是普通存储，不做 post-processing。
所以这件事只会在 Render 第一次跑 `build.sh` 时发生，那时 20 个模板都写完了。

处置两条：

1. 源文件挪到 **`assets/`**，`static/` 只装构建产物。
   `STATICFILES_DIRS = [BASE_DIR / "static"]`；
2. CI 里加一步**用 prod 的 settings 跑 `collectstatic`**。
   光挪目录只是绕开了这一次，那一步才让下一次也撞不上。

### C1.1 · Tailwind v4 的扫描口径：计划里那条警告是反的

计划写着「`content` 指向 `["./*/templates/**/*.html"]`——⚠️ 漏配这一行的表现是
样式在开发时正常、构建后大面积消失」。**那是 Tailwind v3 的问题，v4 上不成立。**
实测把 `@source` 全删掉，产物里照样有 `bg-brand-600` —— v4 会从 cwd 自动扫描。

⚠️ **但自动扫描更糟，而且糟得不报错。** 实测不写 `@source` 时，
`rounded-full` / `shadow-lg` / `text-4xl` / `max-w-6xl` / `overflow-x-auto`
全都进了产物 —— 它们**一个模板都没用过**，只出现在
[`design-system.md`](design-system.md) 的**正文里**。自动扫描把设计文档当成了
class 的来源（它尊重 `.gitignore`，而 `docs/` 是提交进 git 的）。

代价不是多几 KB，是**它会掩盖拼错**：模板里写 `bg-brnad-600`，
只要正确拼法在某份文档里出现过，产物里就有那条规则 —— 页面照样不对，
而唯一能指望的信号（「这个 class 没被生成」）已经被喂饱了。

⭐ **然后踩了第二脚**：加上 `@source` 之后再量，那五个 class **还在**。
因为 v4 的 `@source` 是**追加**，不是替换。要关掉自动扫描得写：

```css
@import "tailwindcss" source(none);
```

⚠️ 少了 `source(none)`，上面那一整段警告一条都没挡住，
而产物看起来完全正常 —— 加了 `@source` 那一刻你会以为已经解决了。

### C1.1 · 对比度是算得出来的数，凭感觉写规范就会写错

[`design-system.md`](design-system.md) 原来写「`brand-500` 在白底上做正文文字不够，
**做背景配白字够**」。写令牌时顺手算了一遍：**不够，实测 3.65:1**。
对比度是对称的 —— 换个方向不会变大，白字配 `brand-500` 和 `brand-500` 做文字是同一个数。
主按钮改用 `brand-600`（5.21:1）。

同一次量出来的第二条：**`ink-500` 在白底上是 4.34:1**，差 4.5 那条线一点点。
它看着就像个「次要文字」色，实际只能做边框和分隔线，次要文字最浅到 `ink-600`。

> ⚠️ **这两条最要命的地方是目测查不出来** —— 4.34 和 4.5 在屏幕上没有任何区别。
> 所以补了一条守卫 `core.tests.ContrastGuardTests`：色值从 `assets/app.css`
> **解析**出来（不在测试里抄第二份），照 design-system.md 那张表逐对算。
> 验证过它抓得住：把 `brand-600` 调回 `brand-500` 的值，
> 报的是「主按钮：白字配品牌底: white on brand-600 = 3.65, 要求 4.5」。

### C1.2 · 产物分支的 `git commit` 失败不能 `exit`

第一版写成：

```bash
git commit -m "build: ..." || { echo "Nothing changed."; exit 0; }
git push --force origin deploy
```

⚠️ **`push` 被跳过了**。「产物没变」和「main 前进了」是两件事，而后者仍然要推 ——
表现会是**只改了 Python 的那次提交永远上不了线**，而 CI 全绿、Render 说部署成功
（它部署的是上一版）。改成 `|| echo`，`push` 永远跑。

### C1.2 · 构建出空壳不会报错，所以给产物定了个下限

Tailwind 扫不到任何模板时**不报错**，只是产出一个几百字节的空壳，
后面每一步都照常成功 —— 直到有人打开线上页面。
所以 `deploy-branch.yml` 里加了一步：CSS 小于 2KB 或 JS 小于 20KB 就红。

> 这条和上面「自动扫描掩盖拼错」是同一个形状：
> 前端构建的失败模式**大多是「安静地少做了一点」，不是「炸掉」**。
> 所以每一处都要有一个能量出来的下限。

### C2.2 · 375px 的横向滚动：量出来的和看出来的不是一回事

先踩了一个**工具的坑**，再抓到一个**真 bug**，两件都值得记。

**工具那件**：`--window-size=375,900` 截出来的图是 375px 宽，
但 **macOS 上的 Chrome 把窗口宽度下限卡在 500px** —— 页面按 500px 布局，
再裁成 375px 交给你。于是「卡片右边被切掉、导航条不见了」看起来像溢出，
实际是**裁剪假象**。照着这张图去改布局，改的是一个不存在的问题。

改用**同源 iframe** 强制 375px，读 `contentDocument.documentElement.scrollWidth`。
这才是一个数字：`scrollWidth > clientWidth` 就是溢出，没有别的解释。

**真 bug 那件**：签到页实测 `viewport=375 scrollWidth=632`，
而 `body` 和 `main` 都是 375、表格也确实在 `.table-wrap` 里滚。
差的那一层是 —— **`overflow` 只裁剪以它为包含块的绝对定位后代**。
`.table-wrap` 当时是 `position: static`，于是表格里那个给读屏用的
`<label class="sr-only">Hours for …</label>`（Tailwind 的 `.sr-only` 是
`position: absolute`）把**初始包含块**当成了自己的包含块，
逃出裁剪，停在表格自然宽度那个 x 坐标上，把整个文档撑到 632。

修法是 `.table-wrap` 加一行 `position: relative`。632 → 375。

> ⚠️ **这个 bug 的形状值得记住**：一个**为无障碍加的、屏幕上根本看不见的元素**，
> 把整页的横向滚动撑开了。它不会出现在任何截图里 —— 你只会看到页面能横着拖，
> 而拖到头是一片空白。八个页面里只有签到页有这个 `sr-only`，所以也只有它中招。

### C2.2 · `|default:` 把「记了 0」和「没记」显示成同一个东西

截图验收当场看出来的：seed 数据里有人 3:44 签到、3:45 签退，
状态写着 **Attended**，工时那一格却是「—」。

原因是模板写的 `{{ row.hours|default:"—" }}`。Django 的 `default` 在值为**假**时替换，
而 `Decimal("0.00")` 是假。于是：

| 库里 | 应该显示 | 实际显示 |
|---|---|---|
| `None`（没记过） | — | — |
| `Decimal("0.00")`（记了，是零） | 0.00 | — |

这两件事差别很大：**0 小时是一条要解释的记录**（人来了又走了，或者忘了签退），
**没记过是一件还没做的事**。一个要去问，一个要去做。

⚠️ 这个 bug **是从旧模板原样搬过来的**，不是 C2 引入的 —— C2 只是第一次
有人真的盯着那一格看。全仓 `|default:` 逐个过了一遍，
凡是「0 有意义」的都换成 `default_if_none:`（工时、`needed_count`、签到签退时间、
授权的起止日期）。`{{ type|default:'submit' }}` 那种字符串默认值不动。

配套一条回归测试 `test_zero_recorded_hours_do_not_read_as_no_hours_recorded`，
验证过它抓得住：换回 `|default:` 就红。

### C2.3 · OOB 的 messages 不能写进被整页复用的那个片段

HTMX 的写操作响应不经过 `base.html`，所以 `messages.success()` 在那条路径上会
**既不显示、又不消失** —— 它躺在 session 里，等下一次整页加载才冒出来，
表现是「刚才那次操作的提示，在你点开另一个页面时才弹出来」。

解法是片段响应里带一个 `hx-swap-oob` 的 `#messages` 回填。
⚠️ **但那一句不能写进片段模板本身**：`_event_roles_panel.html` 整页也要用，
而整页里 `base.html` 已经画了一个 `<div id="messages">` —— 同一个 id 出现两次，
HTML 无效，而 OOB 正是靠 id 找目标的。**坏的方式是「有时候换对了有时候换错了」**，
取决于哪个先被找到。

所以多一层壳：`_event_roles_swap.html` = 片段 + OOB，视图只在 HTMX 分支渲染它。

### C2 · 两条测试断言的是文案，不是行为

C2 改文案时红了两条，都不是代码坏了：

| 测试 | 断言 | 为什么红 |
|---|---|---|
| `test_sending_leaves_a_record_naming_who_was_missed` | `assertContains(…, "most recently")` | 那句话挪到了句首，`m` 变成了 `M` |
| `test_both_sides_of_a_duplicate_pair_offer_a_merge` | `page.count("合并掉") == 2` | 链接文案按 D23 改成了英文 |

两条都改成断言**不随文案移动的东西**：前者大小写无关地找关键词，
后者数 `?keep=` 这个查询串 —— 那是「这条链接能不能真的走到合并页」的充要条件。

> **一般形式**：断言页面上有某句话，测的往往是**措辞**而不是**行为**。
> 每改一次文案就要回来改一次测试，而那种修改**永远是照着报错改的**，
> 久了就变成「把期望值改成实际值」的橡皮图章 —— 那时它已经不拦任何东西了。

### C2.3 · 「这个工种还缺人」不能写进模板的 `{% if %}`

报名名单页想给缺人的工种加一个显眼标记。第一版写的是
`{% if role.needed_count and role.registered_count < role.needed_count %}`。

它是对的，但它是**规则的第二份拷贝** —— `EventRoleQuerySet.understaffed()`
已经定义了同一件事，而那条规则里有个坑：**`needed_count` 为 NULL 表示「不限」，
所以这种工种永远不算缺人，而不是「缺无穷多」**。
带坑的规则抄两份，两份不会一直同步。

改法是把它提成一个 annotation（`is_short`），`understaffed()` 反过来 filter 它。
模板只问 `{% if role.is_short %}`，一处定义，两处使用。

### C6.4 · 在 JS 里 toggle 一个 Tailwind class，那条 class 根本不存在

iPad 那一页的 Check in / Check out 两个按钮，选中态第一版是这么写的：

```js
button.classList.toggle("bg-brand-600", on);
button.classList.toggle("border-brand-600", on);
```

产物里 `border-brand-600` **一条规则都没有**：

```
$ grep -c "\.border-brand-600" static/css/app.css
0
```

Tailwind 是**扫源码文本**生成 CSS 的。模板里出现过的 class 才会被生成，
只活在 JS 字符串里的不会。`bg-brand-600` 侥幸有，是因为**别的模板**用过它 ——
于是这个 bug 的表现是「三条里两条生效、一条不生效」，
比三条全不生效难看出得多。

⚠️ **而它不报错。** `classList.toggle` 加一个不存在的 class 是完全合法的操作，
页面照常渲染、测试照常绿，只是那个按钮少了一圈边框 ——
而「少一圈边框」没有任何人会去写测试。

改法不是把 class 塞进 safelist，是**让 JS 别碰 class**：

```html
<button aria-pressed="false"
        class="… aria-pressed:border-brand-600 aria-pressed:bg-brand-600 …">
```
```js
button.setAttribute("aria-pressed", on ? "true" : "false");
```

class 的名字回到模板里（扫得到），JS 只改状态。

> **一般形式，和 `AssetPathsComeFromTemplatesGuardTests` 那条守卫同源**：
> **凡是构建期需要「看见」的字符串 —— 静态文件路径、Tailwind class ——
> 都不能在 JS 里拼或藏。** 它们在开发时都工作，在产物里都消失，
> 而两种消失都不报错。
>
> 上一条守卫是路径，这一条是 class 名。**下一次遇到「构建工具扫源码」的东西，
> 先问一句：它扫得到我写的这个地方吗？**

### C6.4 · 三个数字互相矛盾，而每一个单独看都合法

iPad 那一页的倒计时，第一版是这么写的：

```js
countdown.textContent = `New code in ${Math.ceil(left / 1000)}s`;
bar.style.width = `${Math.min(100, (left / REFRESH_MS) * 100)}%`;
```

而 `left` 是从服务端给的 `expires_at`（90 秒有效期）算出来的，`REFRESH_MS` 是 20 秒。
于是屏幕上：

| | |
|---|---|
| 文案说的 | 「还有 82 秒换新码」 —— 而实际 20 秒就换 |
| 进度条量的 | `82/20 = 410%`，被 `Math.min` 夹到 100% |
| 进度条画出来的 | 一根**七十秒纹丝不动**的满条 |

**一句话说着一个数、量着另一个数、画出来第三个数。** 三个都不报错。

⚠️ 这是**看屏幕才看出来的**，测试一条都没红 —— 因为每一个断言单独写出来都是对的：
`expires_at` 是对的、`REFRESH_MS` 是对的、`Math.min` 也是对的。
错的是把两个不同的量当成了同一个，而没有任何一条单元测试的形状能表达这件事。

改法是不再引入第二个量：`lifetimeMs = expires_at - 收到它的时刻`，
文案改成 `Code expires in Xs`。这样量的是**「屏幕上这张码还能不能用」**，
而不是页面自己的实现细节。

> **顺带修好了一个更要紧的东西。** 因为倒计时现在读的是绝对时刻，
> iPad 息晚回来它会直接归零 —— 于是可以在归零时**把二维码盖掉**。
> 而「死掉的码和活的长得一模一样」正是这个功能当天最可能出的事故，
> 原来那个按自己节奏走的计时器**永远不会归零**，也就永远没有机会发现它。

### C6.3 · 组件的默认 variant 是 secondary，而我在最该用 primary 的地方没写

`core/components/button.html` 的默认变体是 **secondary**（白底描边）。
扫码确认页的「Check in」是这一屏唯一要做的事，第一版没传 `variant`，
于是它画得和旁边的「My Signups」一样重。

⚠️ 同样是**看截图才看出来的**。`assertContains(response, "Check in")` 是绿的 ——
按钮在，字也对，只是它没告诉任何人该按哪个。

`button.html` 顶上那条「一屏最多一个 primary」的注释，防的是**多**写；
这一次犯的是**少**写，而那条注释没提，因为默认值本身就是「不显眼」——
而这一条值得单独记：**默认值安全，不等于默认值正确。**

同一轮把拒绝页那个「去报名 / 去补资料」的按钮也改成了 primary：
一个被拦下来的人最需要的就是「那我现在该做什么」。

### C6.3 · 全站第一个手写的 radio，于是它是蓝的

工种选择页是全站唯一一处模板里手写 `<input type="radio">` 的地方
（别处的表单控件都由 Django 的 widget 渲染）。结果它用的是浏览器默认的**蓝色**，
而全站主题色是紫的 —— 页面上唯一一个蓝东西，恰好是那一屏要人去点的那个。

修法是往 `assets/app.css` 加一条 `accent-color`，
⚠️ 挂在 `input[type=radio]` 上而**不是** `.field` 的后代 ——
这是全站唯一一处故意不收进 `.field` 的规则，因为手写的那个 radio 不在 `.field` 里，
而写两遍就会分叉。深色模式换 `brand-500`：`brand-600` 压在深色底上，
选中和没选中隔一米看是同一个灰点。

⚠️ 用 `accent-color` 而不是 `appearance:none` 自绘 —— 原生控件的键盘操作、
读屏播报和手机上的点击热区都是白拿的。同 `.field select` 不自绘箭头那条判据。
