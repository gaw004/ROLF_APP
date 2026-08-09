# 重大决策记录 D1–D31

每条记录：**结论 → 为什么 → 代价 / 何时重新考虑**。

> **一条决策一个文件**（2026-07-30 从 `goal.md` 的「三、重大决策记录」拆出来，**内容一字未改**）。
>
> - **`../goal.md` 仍是唯一入口**，那里有「[这件事该不该做](../goal.md#常见问题--去哪找)」的导航表；
> - **代码注释和 roadmap 里写的 `goal.md D9` / `goal.md D14`，指的就是这里的 D9 / D14** ——
>   编号是稳定引用，永远不要改；文件名可以改；
> - 拆开顺带解决了一件事：原来 D1–D18 全部塞在 `<details>` 里折叠，
>   **几十处链接点过去都是收起状态**。现在每条决策是一页，链接落在正文上。
> - 被后来的修订**取代掉的小节**（D11 的两次修订、D15 的载体一）仍然折叠 ——
>   那是真的历史，不是当前结论。**本文档的价值恰恰在"为什么改口"**，所以折的只是显示，一个字都没删。

| # | 决策 | 它回答的问题 |
|---|---|---|
| [D1](D01-django-postgres-admin.md) | Django + PostgreSQL + Admin 起步 | 技术栈为什么是这套 |
| [D2](D02-frontend-deferred.md) | 前端推迟（⚠️ 2026-07-29 部分作废） | 什么时候才开始写页面 |
| [D3](D03-portable-postgres.md) | 数据一个 `pg_dump` 能带走 | "数据自主"的具体定义 |
| [D4](D04-contact-one-table.md) | Contact 统一人和组织 | 人和机构为什么共用一张表 |
| [D5](D05-lookup-tables-not-enums.md) | 字典表 vs 枚举 | 分类字段怎么选，`code` 为什么必须唯一且不可改<br>↳ [判定规则](D05-lookup-tables-not-enums.md#判定规则什么时候用字典表什么时候用-textchoices2026-07-28-补) · [`code` 通则](D05-lookup-tables-not-enums.md#通则每张字典表都带一个唯一且不可改的-code) |
| [D7](D07-standard-field-libraries.md) | 电话 / 国家 / 州用成熟库 | 为什么不自己列 |
| [D8](D08-language-iso-639-3.md) | Language 自建 ISO 639-3 | 为什么现成的包不能用 |
| [D9](D09-rules-in-db-constraints.md) | 规则落数据库约束 | `clean()` 不是强制层（这条修订过）<br>↳ [归一化通则](D09-rules-in-db-constraints.md#通则归一化如果被约束依赖就必须写进约束的表达式里2026-07-28-补) —— `bulk_create` 会绕过 `save()` |
| [D10](D10-person-role-position-assignment.md) | 人 / 角色 / 编制 / 任职四层 | 一条信息该放哪张表 |
| [D11](D11-position-and-assignment.md) | `Position` + `Assignment` | 一人多岗、空缺编制、汇报线挂在哪（这条**修订过两次**） |
| [D12](D12-user-on-contact.md) | User 挂在 Contact 上 | 登录账号和岗位为什么解耦 |
| [D13](D13-single-email-phone-address.md) | 单个 email / 电话 / 地址 | 什么时候才拆成一对多 |
| [D14](D14-constraint-is-the-only-rule.md) | 约束是唯一的规则 | 字段级提示靠**映射表 + 守卫测试**，不靠把规则写两遍 |
| [D15](D15-relationship-carriers.md) | 关系的载体 + 四条判据 | 新关系用字段 / 自引用 FK / 专用表；**第四条判据「主体性」决定它该不该进 `Contact`**（紧急联系人这一支同日改过**两次**）<br>↳ [第四条判据：主体性](D15-relationship-carriers.md#载体的第四条判据主体性--这个实体该不该进-contact2026-07-28-新增) · [`EmergencyContact` 的形状与代价](D15-relationship-carriers.md#emergencycontact-的形状以及为什么最终选了文本方案) · [监护人 ≠ 紧急联系人](D15-relationship-carriers.md#监护人--紧急联系人重要区分) |
| [D16](D16-time-and-dates.md) | 时间与日期的唯一口径 | **"今天"只有一种写法**，另两种会静默错一天 |
| [D17](D17-app-layout.md) | app 划分 | 新模型放哪，以及 `payroll` 为什么必须独立 |
| [D18](D18-admin-boundary.md) | Admin 的边界 | 这段逻辑该不该写在 admin 里；以及权限粒度为什么倒推出一张新表<br>↳ [**代码落点与文件分层**](D18-admin-boundary.md#代码落点与文件分层什么会随升级坏什么换界面还用得上2026-07-28-补) —— 哪一层会随 Django 升级坏 · [两条出栏触发](D18-admin-boundary.md#什么时候-admin-整体不够用了) |
| [**D19**](D19-event-role.md) | 活动的工种编制 `EventRole` | 「这场活动开了几个工种、每个要几人」；以及为什么不能靠 `Participation` 反推 |
| [**D20**](D20-ministry-role.md) | 范围化权限 `MinistryRole` | 「食物银行的 admin」这句话在数据库里长什么样；为什么 Django Group 顶不上 |
| [**D21**](D21-self-service-and-permissions.md) | 对外账号 + 自助页面提前 | 志愿者能登录之后，权限为什么不能再排最后 |
| [**D22**](D22-event-notifications.md) | 活动变更通知 | **通知名单 ≠ 报名名单**（未成年人通知家长）；换通知服务商为什么不该动模型 |
| [**D23**](D23-i18n-interface-only.md) | 界面统一英文，双语推迟（⚠️ 2026-07-31 当天改过口） | 界面写哪种语言；以及**双语的成本是真的、收益是猜的**。早上那份双语方案原样留在文件里，重启时照抄 |
| [**D24**](D24-htmx-alpine-tailwind.md) | 前端 = Tailwind + HTMX + Alpine | 三层各管什么、边界在哪；为什么不是 React；以及 Alpine 为什么需要一条自己的落点规矩<br>↳ [三层的分工](D24-htmx-alpine-tailwind.md#三层的分工) · [渐进增强的口径](D24-htmx-alpine-tailwind.md#渐进增强的口径只管写操作) |
| [**D25**](D25-public-front-page.md) | `/` 是公开门面页，不是角色调度器 | 推翻 C3.1 的设计；公开/登录的分界线动了一格；首页内容归 foundation tier 改；白字压在用户上传的照片上，对比度只能自己造<br>↳ [推翻了什么](D25-public-front-page.md#推翻了什么) · [媒体文件](D25-public-front-page.md#媒体文件和活动图片同一条规矩不同一套参数) |
| [**D26**](D26-palette-from-the-hero.md) | 品牌色从首页照片推出来 | 只取色相和饱和度、**钉住相对亮度**，于是对比度对任何照片都精确成立；守卫改成测生成器而不是测色值；深色模式改成半透明黑压在压暗的照片上<br>↳ [为什么是钉相对亮度](D26-palette-from-the-hero.md#为什么是钉相对亮度而不是调明暗) · [深色模式](D26-palette-from-the-hero.md#深色模式半透明的黑压在压暗的照片上) |
| [**D27**](D27-ministry-report.md) | 管理列表旁的 ministry 报表；absent 终于有了写入路径 | 报表收的是**筛过的 queryset** 而不是 ministry id，于是两种身份共用一段代码、报表不可能比页面宽；十四个指标里四个带着自己的注脚，缺勤率的分母专门讲了一节；五张图全是 CSS 横条，没引图表库；面板跟列表齐平，完整版可打印成 PDF；三个列表分页<br>↳ [唯一的不变量](D27-ministry-report.md#-唯一的不变量报表描述的是这一次筛选) · [缺勤率的分母](D27-ministry-report.md#缺勤率分母是这一条里唯一难的地方同日追加) · [三个不报错的错](D27-ministry-report.md#踩到的三个坑都是不报错的错) |
| [**D28**](D28-qr-checkin.md) | 现场扫码自助签到 | 动态二维码**不是防作弊机制**，是减少录入的工具 —— 它只把攻击成本抬到「需要一个现场的活人当中继」；「到场」和「你是谁」拆成两段（token 90 秒 / session 凭据 10 分钟），于是 GET 不再写、手机上登录慢也不影响；token 一次性化是一个会让 99 个人打不上卡的 bug；一人多工种时让他选，因为另外两种方案都在编造数字；一百人同时扫算下来是 20 rps，不上队列<br>↳ [唯一的不变量](D28-qr-checkin.md#-唯一的不变量这是一个减少录入的工具不是一个防作弊的机制) · [时钟误差那一条基于误解](D28-qr-checkin.md#时钟误差原需求注意事项-①这一条基于一个误解) · [一次性化是个 bug](D28-qr-checkin.md#三token-一次性化是一个-bug不是一个加固) · [并发](D28-qr-checkin.md#八并发一百人同时扫) |
| [**D29**](D29-memories-wall.md) | Memories 照片墙：独立 app + 自己的桶 + 每日抽签 | 为什么它是一个谁都不依赖的 app（于是最容易删）；**种子是日期**所以页面不在人眼皮底下重排，而 `hash()` 做种子的 bug 在单进程下永远复现不出来；桶必须分开是因为「删了还能不能找回来」两边**正好相反**；原图不留（GPS 会把志愿者家住址发布出去），代价是 1600px 就是以后能看到的最大尺寸；去重只到「同一份字节」为止<br>↳ [唯一的不变量](D29-memories-wall.md#二-唯一的不变量这一页描述的是今天) · [桶为什么不能共用](D29-memories-wall.md#三桶为什么-memories-不和活动图片共用一个) · [被删掉的那一半](D29-memories-wall.md#七被删掉的那一半2026-08-07) |
| [**D30**](D30-registration-and-login.md) | 邮箱即账号；Google 只用来预填；注册限流 | **拿着 Google 账号在这里什么都不代表** —— 它只填三个框，建出来的是普通密码账号；没有邮箱就没有登录（D12 本来就允许的形状）；限流挡得住脚本、挡不住手动冒用，后者推迟而不是做一半；🔴 `client_ip()` 不写的话线上**静默地**让全世界共用一个桶，而顺手信 `X-Forwarded-For` 比这个 bug 更糟<br>↳ [Google 那一段是预填](D30-registration-and-login.md#二-google-那一段它是预填不是登录) · [client_ip](D30-registration-and-login.md#-client_ip不写这个函数线上会静默地量错东西) |
| [**D31**](D31-overlays-in-the-top-layer.md) | 覆盖层一律 `<dialog>` + `showModal()`（top layer） | `position: fixed` **不等于**相对视口 —— 祖先有 `transform`/`filter`/`contain`/`backdrop-filter` 中任一个就成了包含块，而这个项目到处是深色玻璃；改密码弹窗被关进卡片（用户报的「有时候」= 只在深色下），Memories 悬浮窗**同样中招却一直看着正常**（`.wall` 恰好满屏），后者才是这条决策的理由；🔴 `<dialog open>` 不进 top layer 而且看起来是对的；顺带把 Esc/焦点/inert 五件事交回浏览器，其中两件原来没做<br>↳ [唯一的不变量](D31-overlays-in-the-top-layer.md#-唯一的不变量覆盖层的包含块必须是视口而这件事只有-top-layer-保证得了) · [为什么不是「注意别放进卡片」](D31-overlays-in-the-top-layer.md#为什么不是以后注意别把弹窗放进卡片里) · [守卫](D31-overlays-in-the-top-layer.md#守卫coretestsoverlaysliveinthetoplayerguardtests) |

## 加一条新决策时

1. 编号接着往下（D32…），文件名 `D32-<kebab-slug>.md`，**开头一行 `# D32 · 结论`**；
2. 回到这张表加一行；
3. 如果它推翻或修改了旧决策，**去那条决策的文件里就地写修订说明**，
   不要只在新决策里说 —— 本项目已经因此吃过一次亏（[D20](D20-ministry-role.md) 声称"已在原地改掉"，
   实际漏了两处，见 [`../revisions.md`](../revisions.md)）。

### ⚠️ 第 2 步是最容易漏的一步，而漏掉它没有任何症状

D29 和 D30 都是**事后补的**（2026-08-09 的一次仓库体检翻出来）：
Memories 整个 app 带着自己的桶、自己的 URL 区域和 107 条测试跑了三天，
Google 登录和注册限流也在跑，而**决策索引里一条都没有** ——
过程全记在 `revisions.md` 里，那是流水账，不是索引。

⚠️ 症状是什么都没有：代码在跑、测试全绿、文档也写了，只是**下一个人找不到**。
判据因此定成一句话：

> **新加一个 app、一张表、一个桶、一条对外的路，或者一个"为什么不用显而易见那个做法"，
> 就欠一个编号。** 写在 `revisions.md` 里不算 —— 那里记的是"怎么改的口"，
> 这张表记的是"现在的结论是什么"。
