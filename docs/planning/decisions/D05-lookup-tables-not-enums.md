# D5 · 会变的分类做成字典表，不做 Python 枚举

> 本文件是 `../goal.md` 拆出来的一条决策记录（2026-07-30 拆分，内容一字未改）。
> **`goal.md` 仍是唯一入口**：决策一览表和「去哪找」都在那里，
> 代码注释里写的 `goal.md D5` 指的就是本文件。

`RelationshipType` 是数据库表。基金会以后想加"推荐人"、"校友"这类关系，
在 admin 里加一行就行 —— 不用改代码、不用写迁移、不用重新部署。
**这是"需求变了还能用"最直接的体现**，后续 Ministry、Skill、活动类型、捐款类型、
付款方式一律照此办理。

> 注意"理事会成员"**不是**关系类型 —— 理事走 `kind=board` 的 `Position` + 一行 `Assignment`（见 D11）。
> 判断方法：这个人在基金会担任的职务 → `Position` / `Assignment`；出事时该打谁的电话 → `EmergencyContact`。

## 判定规则：什么时候用字典表，什么时候用 `TextChoices`（2026-07-28 补）

"会变的做字典表"这句话不够判定 —— 几乎所有分类看上去都会变。真正的分界是：

> **代码要按它分支的（状态机、权限判断、统计口径）→ `TextChoices`；
> 纯粹是给人看的标签分类 → 字典表 + `code`。**

理由：字典表的全部价值是"不改代码就能加一行"。而只要代码里存在
`if status == X` 这样的分支，加一行就**必然**要改代码 —— 那字典表只是把枚举藏进了数据库，
额外多一次查询、多一个静默失效的风险，什么也没换来。
`contact_type` 走 `TextChoices` 正是这个道理（D9 的"代价"那一段已经隐含说了）。

当前分配：

| `TextChoices`（代码按它分支） | 字典表 + `code`（纯标签） |
|---|---|
| `Contact.contact_type`、`Position.kind`、`Assignment.status`、`Event.status`、`Participation.status`、`Participation.consent_method`、`MinistryRole.role`、`EventNotification.reason`、`BackgroundCheck.status`（已推迟） | `RelationshipType`、`Ministry`、`EmploymentType`、`EventType`、`ParticipationRole`、`Skill`（已推迟）、Phase D 的 `financial_type` / `payment_method` |

> **2026-07-29 晚补进三个**：`Participation.consent_method`、`MinistryRole.role`、
> `EventNotification.reason`。本表自称"当前分配"，而这三个新的 `TextChoices`
> 一直没登记；`financial_type` / `payment_method` 的 Phase 编号也随 C / D 对调改了。

> `kind` 挂在 `Position` 而不是 `Assignment`，理由见 D11 —— 空缺编制也必须说得出自己是有薪岗还是志愿岗。

## 通则：每张字典表都带一个唯一且不可改的 `code`

代码只认 `code`，永远不认显示名。

字典表的全部价值是"显示名可以在 admin 里随时改" —— 那么代码里凡是引用显示名的地方
都会在某人改名之后**静默失效**（`filter(name="parent of")` 不报错，只是查不到东西了）。
`code` 是给代码用的稳定锚点，显示名是给人看的、可变的。

三个要求缺一不可（2026-07-28 补齐后两条）：

1. 唯一，而且是 `UniqueConstraint(Lower("code"))`，不是 `unique=True`
   （2026-07-28 二次修正，原文写的是 `unique=True`）。不唯一的 `code` 根本不是锚点，
   `get(code="food_pantry")` 会抛 `MultipleObjectsReturned`，而且是在有人手滑建了第二行之后才炸。
   **为什么必须是 `Lower()` 版**：光有 `unique=True` 的话，`bulk_create` 能把
   `Food_Pantry` 和 `food_pantry` 当两个不同的字符串一起插进去 —— 见 D9 的
   [归一化通则](D09-rules-in-db-constraints.md#通则归一化如果被约束依赖就必须写进约束的表达式里2026-07-28-补)。
2. **小写归一化** —— 在 `save()` 里 `self.code = self.code.strip().lower()`。
   **注意它现在只负责"存进去的值好看"，不再承担唯一性** —— 唯一性由上面那条约束保证。
3. **真的不可改** —— `editable=False` 只挡 ModelForm，脚本照改。落地方式是
   admin 的 `get_readonly_fields` 在 change（非 add）页把 `code` 设为只读，
   加上 `clean()` 里比对数据库中的旧值。

适用范围：**一张不落**。新建字典表时就带上，成本为零；
`RelationshipType` 是已有表，Phase B 补加（三步迁移，见 Phase B）。
