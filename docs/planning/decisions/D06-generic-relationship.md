# D6 · 通用 Relationship 表：承载"薄"关系

> 本文件是 `../goal.md` 拆出来的一条决策记录（2026-07-30 拆分，内容一字未改）。
> **`goal.md` 仍是唯一入口**：决策一览表和「去哪找」都在那里，
> 代码注释里写的 `goal.md D6` 指的就是本文件。

`Relationship(contact_a, contact_b, relationship_type, start_date, end_date)`
适用于只需要记录「A 和 B 有某种联系 + 起止日期」、没有专有字段的关系。

剩余适用范围（2026-07-27 两次收窄后）：

1. 外部组织归属 —— "张三是 XX 公司员工"（企业配捐、企业志愿者团队）、"李四是 XX 中学学生"
2. 家庭 / 配偶 —— 家庭作为一个捐赠单元
3. 推荐人 —— 谁介绍谁来的
4. 亲属关系中不涉及法律责任的部分
5. 以后冒出来的、暂时说不清要什么字段的新关系（先记下来，够格了再升级成专用表）

被拿走的两块（记录在此以免重复讨论）：

- ❌ **基金会内部编制与汇报线** → 走 `Position` + `Assignment`（见 D11）。
  原因：Relationship 说不清"这条汇报线属于这个人的哪个身份"，
  更说不清"这个编制现在空着"。`manages` / `managed by` 关系类型**不再用于组织架构**。
- ❌ **法定监护等带专有字段的关系** → 走专用表（见 D15）。
  原因：通用表放不下"同意书签署日期"这类字段。
  **该专用表（`Guardianship`）已于 2026-07-28 推迟到 Phase B 之后**，见推迟清单。
- ❌ **紧急联系人** → 走专用表 `EmergencyContact`，姓名电话**存文本**（见 D15 第三次修订）。
  原因**不是**形状放不下（自引用 FK 三条件全过），而是 D15 新增的**第四条判据**：
  紧急联系人可能是邻居、室友，不是与基金会交互的主体，**不该占一行 `Contact`**。
  **但关系标签仍然复用本表的 `RelationshipType`**，见下面补强的第三条。

**补强**：`RelationshipType` 要加三个字段：

- **`code`**（不可改的 slug，如 `guardian_of`）—— 代码里一律引用 `code` 而**不是**显示名。
  否则 `filter(relationship_type__name_a_to_b="guardian of")` 这种字符串匹配，
  会在有人于 admin 里改了显示名之后**静默失效**。这是通用表最大的脆弱点，一个字段修掉大半。
  唯一性、小写归一化、不可改的落地方式见 D5 的三条要求。
- **`is_symmetric`**（布尔）—— 显式标记"配偶""兄弟姐妹"这类正反同义的类型，
  不靠"`name_b_to_a` 为空"去推断。理由见 D15。
- **`usable_as_emergency_contact`**（布尔，默认 `False`）—— 见下。

## 为什么紧急联系人的关系标签复用本表，而不是另建一张词表（2026-07-28 确认）

`EmergencyContact.relationship_type` 是一个指向 `RelationshipType` 的**非空**外键，
**不新建 `EmergencyRelationship` 词表**。
（这条 2026-07-28 第三次修订后依然成立 —— 换的是载体，不是词表。）

**收益**：亲属那一簇（母亲 / 父亲 / 配偶 / 子女 / 兄弟姐妹）两边完全重合，
共用一套词表就不会攒出"母亲 / 妈 / mother"三种写法 —— 而这正是 D15 论证过的
"字段 → 结构化"那个痛的方向。另外 `RelationshipType` 已经有 `code` 和正反双标签，
拆成 `EmergencyContact` 专用表之后词表直接就用上了。

**代价（要如实说）**：两边并不完全重合。本表按上面第 1 条会有
`employee of` / `student at` / `referred by` 这类**外部组织归属**的行
（注意它们**不是**只存在于 `Position` —— `Position` / `Assignment` 管的是基金会内部编制与任职，
外部归属留在 `Relationship`，见适用范围第 1 条）；反过来，为紧急联系人加的
"邻居 / 朋友 / 同事"也会出现在 `Relationship` 的类型下拉里。两个方向加起来四五行。

处理：加一个 `usable_as_emergency_contact` 布尔，用 `limit_choices_to` 过滤下拉。
代码里已有同款先例 —— `Contact.preferred_language` 就是用
`limit_choices_to={"language_type": LIVING}` 从 7900 行里筛出活语言的。一个布尔解决两个方向的噪音。

> **方向约定（不写死一定会录反）**：`EmergencyContact.relationship_type` 一律读作
> **「紧急联系人 是 本人 的 ___」**，即 `name_a_to_b`，其中 a = 紧急联系人、b = 本人。
> 小明名下的 `EmergencyContact` 行填 `name=王秀英` + `parent of`，意思是"王秀英是小明的母亲"。
> admin 的 `help_text` 必须把这句话原样写出来 —— 同 Phase B「录入方向的防线」那一条。
