# 重大决策记录 D1–D23

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

## 加一条新决策时

1. 编号接着往下（D23…），文件名 `D23-<kebab-slug>.md`，**开头一行 `# D23 · 结论`**；
2. 回到这张表加一行；
3. 如果它推翻或修改了旧决策，**去那条决策的文件里就地写修订说明**，
   不要只在新决策里说 —— 本项目已经因此吃过一次亏（[D20](D20-ministry-role.md) 声称"已在原地改掉"，
   实际漏了两处，见 [`../revisions.md`](../revisions.md)）。
