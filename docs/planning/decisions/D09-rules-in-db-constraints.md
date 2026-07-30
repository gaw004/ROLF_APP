# D9 · 业务规则落到数据库约束

> 本文件是 `../goal.md` 拆出来的一条决策记录（2026-07-30 拆分，内容一字未改）。
> **`goal.md` 仍是唯一入口**：决策一览表和「去哪找」都在那里，
> 代码注释里写的 `goal.md D9` 指的就是本文件。

> 这条在 2026-07-27 修订过。 原文是"`Contact.clean()` 和 `Contact.save()` 承载规则，
> 所以从 admin 存、从脚本存、从以后的 API 存行为都一致"。这个说法是错的。

`clean()` 只有 ModelForm 和显式 `full_clean()` 会调用，**`save()` 不调用它** ——
`Contact.objects.create(contact_type="individual")` 不填姓氏一直是能存进去的。
`contact/tests.py` 里那两个校验测试必须手写 `.full_clean()` 才触发得到，这就是证据。
保留修订记录，是因为"**以为规则生效了、其实没生效**"比规则本身更值得记住 ——
这种错误不会报错，只会安静地放脏数据进来。

修订后的原则：能用数据库约束表达的规则，就落到数据库约束。
数据库是所有写入路径唯一绕不过去的一层 —— admin、脚本、`bulk_create`、
`queryset.update()`、以后的 API、直接连 psql 全都绕不过。而 Python 层的校验
管不住后面这几种，其中 `bulk_create` 恰恰是批量导入数据时最常用的。

具体到"名字必须匹配 contact_type"：改成 `CheckConstraint`
（个人必须有 `legal_last_name`，机构必须有 `organization_name`），
提示层怎么配合见 D14。

**`Contact.save()` 里清空不适用名字字段的行为不变** —— 那是数据整理，不是校验，两回事。

**代价**：约束把 `contact_type` 的两种取值写进了数据库。以后要加第三种
（CiviCRM 其实有 Individual / Household / Organization 三种，见 D4）就多一处要改。
可接受 —— `contact_type` 本来就是 `TextChoices` 枚举、本来就是代码而非字典表，
加一种本来就要改代码，只是从改一处变成改两处。

## 通则：归一化如果被约束依赖，就必须写进约束的表达式里（2026-07-28 补）

D9 允许 `save()` 做**整理**（清空不适用字段、`code` 转小写、压空白），这和"规则落数据库约束"
不冲突 —— 整理不是校验。**但有一种情况会让这句话变成漏洞：**

> **当某条唯一约束的正确性，依赖于 `save()` 先做过某次归一化时，
> 这条约束就和 `save()` 一样可以被绕过 —— 而它看上去像是数据库在把关。**

`code` 的 `unique=True` 是最清楚的例子：`save()` 转小写之后 `Food_Pantry` 和 `food_pantry`
确实存不成两行 —— 但那是**因为 `save()` 跑了**。`bulk_create` 一条 SQL 插两行，
两个字符串在数据库看来本来就不相等，唯一约束**根本不认为它们重复**，一声不吭就进去了。

**判定方法**：问一句"**不经过 `save()` 直接写这两行，数据库会不会拒？**"
答"不会"，这条约束就是漏的。

**修法：把归一化搬进约束的表达式**，`save()` 那一份保留（它仍然负责让存进去的值好看、
让 admin 里的行为一致），但**正确性不再依赖它**：

| 归一化 | 漏的约束 | 改成 |
|---|---|---|
| `code` 转小写 | `unique=True` | `UniqueConstraint(Lower("code"))` |
| 显示名 strip 空白 | `UniqueConstraint(Lower("name_a_to_b"))` | `UniqueConstraint(Lower(Trim("name_a_to_b")))` |
| 对称关系交换 a/b | A7 的 `(contact_a, contact_b, type, start_date)` | `UniqueConstraint(Least(a,b), Greatest(a,b), type, …)` —— 见缺口 3 |

**不受影响的**（判定方法过一遍就知道）：`Assignment` 的唯一约束走的是外键 id，
没有文本归一化这回事；`Position.name` 本来就没有唯一约束；
`Contact` 的姓名规则已经是 `CheckConstraint`，从来没依赖过 `save()`。

**起因（记下来）**：2026-07-28 确认了 `bulk_create` 一定会成为常态写入路径
（批量导入基金会现有数据）。在那之前这条漏洞一直"存在但不发作" ——
因为写入路径只有 admin 和 `seed_demo`，两者都走 `save()`。
**"目前所有写入路径都恰好走 `save()`"不是安全，是运气**，而 D9 的全部要点就是不靠运气。
