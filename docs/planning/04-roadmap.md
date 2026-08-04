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

## 编号没有变

C1 / C2 **还是叫 C1 / C2**，只是正文搬到了这里——同 `goal.md` 拆分时的做法：
**正文搬走了，「见 C2.5」这种引用仍然成立**，因为 `03-roadmap.md` 一定能把你导过来。

## 顺序：为什么 C1 必须在 C2 之前

构建链先就位，模板才能**一次过**——同时上 Tailwind class、写 `dark:` 对、
接 HTMX / Alpine、把文案改成英文。反过来每个模板要动两遍甚至四遍。

而 C1 / C2 整体必须在 [C0.5](03-roadmap.md#c05--上线前的三条死链) 之后：
那一步产出的是 403 / 404 / 500 三个模板和一批文案，
排在样式之后就要再排一遍——和当初「C0.2 必须先于 C2」是同一条理由。

⚠️ **[C3.0](03-roadmap.md#c30-域名--ses--最先做因为它靠别人)（买域名 → SES 域名验证 → 出沙箱申请）
建议在 C1 开工当天就启动。** 它是整个 Phase C 里唯一靠别人的事（审核 24–48h），
让这段等待和前端工作重叠，等于白赚两天。

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

`event_list` → `past_events` → `event_detail` → `event_signup` →
`my_participations` → `participation_cancel` → `accounts/profile`。

- HTMX 用在 `event_list` / `past_events` 的时间段筛选、
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
