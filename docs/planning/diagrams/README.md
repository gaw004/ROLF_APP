# 图：ERD · DFD · app 边界（2026-07-30）

把 [`../goal.md`](../goal.md) 和它下面那几份文档里已经拍板的东西画成图。
文字仍然以那些文档为准 —— 这里是同一批决策的另一种读法，不是第二处真相。

## 怎么看

用浏览器打开 [`data-and-flow.html`](data-and-flow.html)（双击即可，不需要起服务、不联网）。
四节：

| 节 | 画什么 | 回答的问题 |
|---|---|---|
| 一 · ERD | 17 张业务表的全部字段、唯一约束、谓词，以及每条外键的 `on_delete` | 「这条信息存在哪、删一行会连带删掉什么」 |
| 二 · DFD | Level 0 上下文 + Level 1 的十四条需求走的路，每个处理标了落在哪个文件 | 「这个动作从哪进来、经过谁、写到哪张表」 |
| 三 · app 地图 | 五个 app 各自的表、11 条跨 app 外键、单向依赖链 | 「新模型该放哪个 app」（配合 [D17](../decisions/D17-app-layout.md)） |
| 四 · 表册 | 逐表：记什么、连向谁、挂不挂 history、服务 R1–R8 / P1–P6 的哪几条 | 「这张表为什么存在」 |

ERD 图幅约 4700 × 3100，在图版内横竖拖动看；页面本身不会横向滚。

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

`data-and-flow.html` 是生成物，里面的图是预渲染的内联 SVG（约 940KB）。
选它而不是「运行时加载 mermaid」，是为了这一页十年后还能打开：
没有 CDN、没有 `node_modules`、没有一行 JS。代价是这个文件比源码大一个量级，
且**不要手改** —— 手改会在下次生成时被覆盖。

## 画的时候踩到的三个坑（都不报错）

1. `fk` / `uk` 不能当属性类型用。 mermaid 的 `PK` / `FK` / `UK` 主键标记大小写不敏感，
   写 `fk contact FK "…"` 会被当成两个标记，整块 `erDiagram` 语法失败 ——
   而语法失败的表现是**页面上留着一坨源码**，不是报错。现在用的是 `ref` / `uniq`。
2. 不要给 mermaid 设 `fontFamily`。 它用自己的默认字体去量文字宽度，
   换成等宽字体后格子还是按默认字体算的，652 个标签里有 482 个被裁掉最后一两个字符
   （`Language` 显示成 `Languag`）。`build.mjs` 里那条「最大裁切」的体检就是为它加的。
3. mermaid 给 svg 的是 `width:100%`。 4700px 宽的图会被压进栏宽里，缩 3.9 倍，
   字全糊 —— 看上去像「图太小」，实际是尺寸被覆盖了。生成时按 viewBox 钉死真实尺寸。

## 和文档的关系

图跟着文档走，不反过来。改了模型或决策，先改 [`../phase-b.md`](../phase-b.md) /
[`../decisions/`](../decisions/README.md)，再回来重画 —— 顺序反了就会出现
「图上有、文档里没有」的第三处真相，而这个项目已经为「同一件事记两个地方」
付过好几次代价（见 [`../revisions.md`](../revisions.md)）。
