# 图：ERD · DFD · app 边界（2026-07-30）

把 [`../goal.md`](../goal.md) 和它下面那几份文档里已经拍板的东西画成图。
文字仍然以那些文档为准 —— 这里是同一批决策的另一种读法，不是第二处真相。

## 🔴 这张图停在 2026-08-03，落后四轮。下面逐条列出已知不准的地方

**别拿它当现状读。** 2026-09-08 核对了一遍，欠的账写在这里 ——
[`../06-roadmap.md`](../06-roadmap.md) 给过两条路（补全，或者写明停在哪天、
哪几处不准），选的是第二条：补全是四轮的账，而**继续假装它是完整的**才是真正
要避免的那件事（[D27](../decisions/D27-ministry-report.md)：没有和没算不能长得一样）。

| 缺什么 / 错什么 | 真相在哪 |
|---|---|
| `Participation.served_as`（志愿 / 工作 / 不适用）整列没有 | [D38](../decisions/D38-served-as-volunteer-or-work.md) |
| `ParticipationRole.nature`（helping / attending）整列没有 | [`../participants.md`](../participants.md) 第六节 L1 |
| 受众四件套（`visible_to_outsiders` / `visible_to_all_staff` / `visible_to_ministries` / 两张 through 表）在 `Event` 和 `EventRole` 上都没有 | 同上 L2/L3 |
| `EventRole.stop_at_needed_count` 没有 | [D19](../decisions/D19-event-role.md) |
| `Position.compensation` 没有 | [D32](../decisions/D32-worker-axes-schedule-and-assignment.md) |
| **整张 `Notice` 表**没有（以及 `notices` app 本身） | [D41](../decisions/D41-notices-are-not-events.md) |
| **整张 `Session` 表**没有 | [`../06-roadmap.md`](../06-roadmap.md) L5.1 |
| `Event.status` 画的是 `confirmed` | 2026-08-19 已改名 `full`（迁移 0011） |
| 谓词画的是 `visible_to_volunteers` | 2026-08-20 已改名 `visible_to_participants` |
| 画着 `EventType` 和 `Event.event_type` | 2026-09-04 连表一起删了 |

⚠️ 下面那张「一节画什么」的表里写着「**全部字段**」和一个表数 —— 那句话今天不成立，
上面这张表就是它的更正。真要重画，按本目录的重生成步骤走一次即可；
在那之前，**这一份是 2026-08-03 的快照**。

## 怎么看

用浏览器打开 [`data-and-flow.html`](data-and-flow.html)（双击即可，不需要起服务、不联网）。
四节：

| 节 | 画什么 | 回答的问题 |
|---|---|---|
| 一 · ERD | 15 张业务表的全部字段、唯一约束、谓词，以及每条外键的 `on_delete` | 「这条信息存在哪、删一行会连带删掉什么」 |
| 二 · DFD | Level 0 上下文 + Level 1 的十四条需求走的路，每个处理标了落在哪个文件 | 「这个动作从哪进来、经过谁、写到哪张表」 |
| 三 · app 地图 | 五个 app 各自的表、11 条跨 app 外键、单向依赖链 | 「新模型该放哪个 app」（配合 [D17](../decisions/D17-app-layout.md)） |
| 四 · 表册 | 逐表：记什么、连向谁、挂不挂 history、服务 R1–R8 / P1–P6 的哪几条 | 「这张表为什么存在」 |

每张图右上角有「⤢ 全屏」：铺满屏幕看，滚轮缩放、按住拖动平移、双击在「适应屏幕」
和 100% 之间切换，`+` `−` `0` 也管用，`Esc` 退出。ERD 图幅约 4850 × 3070，
全屏打开时默认缩到 26% 给个全景，放大到 100% 左右字就清楚了。
不进全屏也能看：图版内可以横竖拖动，页面本身不会横向滚。

## 怎么改

改 [`src/page.html`](src/page.html)（正文 + 五块 mermaid 源码都在里面），
然后重新生成 `data-and-flow.html`：

```
cd docs/planning/diagrams/src
npm init -y && npm i mermaid@11 puppeteer-core
node build.mjs
```

依赖是临时的：不进 `requirements.txt`、不进 git，用完可以把 `node_modules` 删掉。
需要本机装了 Chrome —— 渲染必须走真浏览器，mermaid 的排版依赖真实字体度量。

`data-and-flow.html` 是生成物，里面的图是预渲染的内联 SVG（约 960KB）。
选它而不是「运行时加载 mermaid」，是为了这一页十年后还能打开：
没有 CDN、没有 `node_modules`、没有任何外部请求。
唯一一段 JS 是页面末尾那几十行，管「全屏看图」和图的真实尺寸，
它也内联在文件里 —— 关掉 JS 图照样看得见，只是没有放大器。
代价是这个文件比源码大一个量级，且**不要手改** —— 手改会在下次生成时被覆盖。

## 画的时候踩到的四个坑（都不报错）

1. `fk` / `uk` 不能当属性类型用。 mermaid 的 `PK` / `FK` / `UK` 主键标记大小写不敏感，
   写 `fk contact FK "…"` 会被当成两个标记，整块 `erDiagram` 语法失败 ——
   而语法失败的表现是**页面上留着一坨源码**，不是报错。现在用的是 `ref` / `uniq`。
2. 不要给 mermaid 设 `fontFamily`。 它用自己的默认字体去量文字宽度，
   换成等宽字体后格子还是按默认字体算的，652 个标签里有 482 个被裁掉最后一两个字符
   （`Language` 显示成 `Languag`）。`build.mjs` 里那条「最大裁切」的体检就是为它加的。
3. mermaid 给 svg 的是 `width:100%`。 近 5000px 宽的图会被压进栏宽里，缩 4 倍，
   字全糊 —— 看上去像「图太小」，实际是尺寸被覆盖了。生成时按 viewBox 钉死真实尺寸。
4. 克隆 svg 时不能把它的 `id` 抹掉。 mermaid 的样式写在 svg 内部，
   整段用 `#<svg 的 id>` 限定作用域；id 一没，样式全失效，关系线丢掉 `fill:none`，
   在放大器里变成一大片黑色实心块。做法是换一个新 id，并把样式里的旧 id 一起改掉。

## 和文档的关系

图跟着文档走，不反过来。改了模型或决策，先改 [`../phase-b.md`](../phase-b.md) /
[`../decisions/`](../decisions/README.md)，再回来重画 —— 顺序反了就会出现
「图上有、文档里没有」的第三处真相，而这个项目已经为「同一件事记两个地方」
付过好几次代价（见 [`../revisions.md`](../revisions.md)）。
