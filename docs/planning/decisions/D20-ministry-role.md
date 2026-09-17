# D20 · 范围化权限 `MinistryRole`，不走 Django Group（2026-07-29）

> 本文件是 `../goal.md` 拆出来的一条决策记录（2026-07-30 拆分，内容一字未改）。
> **`goal.md` 仍是唯一入口**：决策一览表和「去哪找」都在那里，
> 代码注释里写的 `goal.md D20` 指的就是本文件。

**结论：新建 `MinistryRole(contact, ministry, role, 起止, granted_by)`，
"某人在某 ministry 有 admin 权限"由它表达。全局的那一级仍然走 Django Group。**

```python
MinistryRole(
    contact     → Contact,      # PROTECT
    ministry    → Ministry,     # PROTECT（2026-07-29 晚从 CASCADE 改，见 on_delete 表）
    role        = admin | coordinator,      # TextChoices：代码要按它分支（D5）
                                            # ⚠️ coordinator 只是占位：本阶段没有任何
                                            #    代码按它分支，permissions.py 只认 admin。
                                            #    见六·4 —— 加一档要同时改枚举和判断。
    start_date, end_date,                   # 复用 core 的 .active()
    granted_by  → User (可空, SET_NULL),     # P5：谁授的权
)
history = HistoricalRecords()               # 授权变更必须留痕
```

## 为什么 Django Group 顶不上（这是硬事实，不是偏好）

Django 的权限是 **`app_label.codename`**，**全局生效，没有对象级作用域**。
授出 `events.add_event` 就是"能给**任何** ministry 发活动"。
而 P2 / P4 要求的是"食物银行的 admin **只**能给食物银行发活动、**只**能看食物银行的报名"。

> Phase D 原来写的是"用 Django Group 划分角色（管理员 / 项目协调员 / 只读）"。
> **那句话在当前需求下是不成立的** —— 它描述的是"能做什么"，而需求问的是
> "能对**哪个 ministry** 做"。已在原地改掉。

这也是 [D18](D18-admin-boundary.md#-权限的形状会倒推模型敏感字段必须独立成模型) 那条「权限的形状会倒推模型」的第二个实例。
第一个实例是背景审查（没有字段级权限 → 独立成 model），这一个是范围化权限
（没有对象级权限 → 独立成表）。**同一条规律：Django 权限系统表达不了的粒度，
必须用一张表把它表达出来。**

## 为什么不复用 `Position(is_leader=True)`

诱惑很大 —— "ministry 的 leader 不就是它的 admin 吗"。**不行，理由是 [D12](D12-user-on-contact.md#d12--登录账号user挂在-contact-上与任职状态解耦) 已经写过的那一条：
在组织里担任什么职务，和在系统里能操作什么，是两个问题。**
`accounts/models.py` 的 docstring 里就写着 *"employment and access are different questions"*。

混起来的三个具体后果：

1. **授权得先造编制** —— 想让财务的人临时帮食物银行看报名，得在食物银行给他建一个 `Position`
   + 一行 `Assignment`，**污染组织架构图**；
2. **撤权得动组织架构** —— 收回权限 = 改 `is_leader` 或结束任职，而那个人可能还在岗；
3. **有权限但没编制的人无处安放** —— 外部审计、临时代管、系统管理员本人。

**代价（如实说）**：多一张表、两处维护，且会出现"他是 leader 但没有系统权限"的困惑。
**缓解：`PositionAdmin` 上显示这个人有没有对应的 `MinistryRole`，
但绝不让任何一边自动推导另一边** —— 自动推导就等于把两个概念又焊回去了。

## P5：更高一级的权限，用 Group 就对了

"谁能指定某个 ministry 的 admin"是**真·全局**的权限，没有 scope ——
这正是 Django Group 适用的形状。一个 `foundation_admin` Group，授
`org.add_ministryrole` / `change` / `delete`。

> **判据（值得记住）：这个权限句子里有没有"某个 ministry 的"这个定语？
> 有 → 进 `MinistryRole`；没有 → Django Group。**

> ### 2026-09-15：判据补第三档（[D47](D47-event-level-grant.md)）
>
> 上面那两档是**同一条判据的两个答案**，而它还有第三个：一个句子可以比
> 「某个 ministry 的」**更窄**。
>
> | 句子的形状 | 落在哪 |
> |---|---|
> | 没有范围（「谁可以任命 ministry admin」） | Django `Group` |
> | 「某个 ministry 的……」 | `org.MinistryRole` |
> | 「某**一场活动**的……」 | `events.EventGrant`（D47） |
>
> 基金会要的是「ministry admin 可以把某一场活动的完全管理权交给指定的人」——
> 那句话过不了上面那条判据（它比一个 ministry 窄），所以它是第三张表，
> 而不是 `MinistryRole` 上多一档。⚠️ 判断仍然只写在 `org/permissions.py`，
> 那条 grep 守卫现在盯着两张表。
>
> ⭐ **而本条「判断只有一处」那个主张，是在 D47 落地那天第一次真的被兑现的**：
> 「完全一致」这个范围改的是**一个函数** —— 编辑、开工种、签到、出勤、记工时、
> 群发通知那些页面一个字都不用动。散在各个 view 里的话，那是二十处修改，
> **而漏掉的那一处是静默的**。

> ### 2026-09-16：上面那张表**判的是「落在哪张表」，不是「谁能做」**（[D48](D48-foundation-tier-writes.md)）
>
> 当天 foundation tier 拿到了「替任何一个 ministry 发布活动」和「改得动每一场」。
> 读起来像是推翻了第二行 —— **不是**。那张表回答的是「这条权限**存在哪儿**」，
> 而 D48 改的是「Django `Group` 那一档**包含**什么」。
>
> | | 上面那张表说的 | D48 改的 |
> |---|---|---|
> | 问的问题 | 「某个 ministry 的活动」这句话落在哪张表 | 基金会那一层的 `Group` 里装着什么 |
> | 答案 | 仍然是 `MinistryRole` | 多了两样：发布、以及对每一场的写 |
>
> ⚠️ 所以判据一个字没改，而**第一行的成员多了**。写下来是因为下一个读这张表的
> 人会问「foundation tier 改得了别人 ministry 的活动，那第二行还成立吗」——
> 成立：ministry admin 这个**身份**仍然只由 `MinistryRole` 给。
>
> ⭐ 而 D48 是这条决策「判断只有一处」第二次被兑现：放开写权限改的还是
> **一个函数**（`can_manage_event()`），二十个页面一个字没动。
>
> ⚠️ **而「一处」要数到页面那一层才算数**（2026-09-16）：判断确实只写在
> `org/permissions.py`，但**「哪一页去问哪个判断」**在活动管理侧曾经写了七遍
> —— 七个视图各拼一份导航上下文。结果是同一条判断被七处各引一次，而其中一处
> 引错了（问了 `can_grant`，而那一页的门是更宽的 `can_revoke_event_grant`），
> 页面照常渲染、测试照常绿。
> 现在那七处收成 `events.views._event_page_context()`，带一条守卫。
> 这不是一条新规矩 —— 是**本条同一句话**在上下文构造这一层的样子。
> ⚠️ 代价也在同一处：那一个函数一改，**六处模板分支和一个测试类当场失去意义**
> —— 判断集中的另一面是，改它的影响面同样集中而且立刻可见。那比散在二十处
> 好，但不等于便宜。

## 判断只写一处

```python
# org/permissions.py —— 全项目唯一一处回答"他能不能对这个 ministry 做这件事"
def ministry_ids_administered_by(user, on=None) -> set[int]: ...
def can_publish_event(user, ministry) -> bool: ...
def can_view_registrations(user, event) -> bool: ...
def can_grant_ministry_admin(user) -> bool: ...        # P5，查 Group
```

**`user.contact` 为空时怎么办 —— 这一层必须给出答案**（2026-07-29 晚补。
本文档原来一个字没写，只有 `02-roadmap.md` B7 的一条测试注顺带表了态，
而这是全项目最容易被随手瞎写的一个分支）：

> **`user.contact is None` → 所有 `can_*()` 一律返回 `False`，而且不抛异常。
> `is_superuser` 不特批。**

理由链是现成的：`MinistryRole` 挂在 `Contact` 上，而权限入口是 `user`；
[D12](D12-user-on-contact.md#d12--登录账号user挂在-contact-上与任职状态解耦) / [D21 第 3 条](D21-self-service-and-permissions.md#三条最低要求一条都不能省)又要求 `User.contact` **保持可空**（superuser 是技术账号、
不对应真人）。于是"没有 `Contact` 的账号"必然存在，**它不是异常，是一种正常状态**：

- **不能抛异常** —— 一个没挂 `Contact` 的账号会把每个受保护的视图炸成 500；
- **不能特批 superuser** —— 那等于在 `permissions.py` 里开一个绕过 ministry 范围的后门，
  而 D20 的全部意义是"范围化"。**superuser 走 `/admin/`，不走这些页面**，
  它本来就能在 admin 里做任何事。
- **代价（如实说）**：拿 superuser 登录去点自助页面 / ministry admin 页面会一路 403，
  看上去像坏了。**这是对的，但要在报错文案里说清"这些页面按 ministry 授权，
  请用 `seed_demo` 造的角色账号"** —— 不说清的话，下一个人会去改视图里的判断来绕过它，
  而那正是这条守卫测试要防的事。

按 [D18 的落点规矩](D18-admin-boundary.md#逻辑落点的硬规矩成本为零现在就要守)，**一个字都不许写进 `admin.py` 或视图**。
视图里只能出现 `if not can_publish_event(...): raise PermissionDenied`。
配一条 grep 守卫测试（第七次「测试当 lint」）：`views.py` / `admin.py` 里
不许出现 `MinistryRole.objects` 的直接查询 —— 要问就调 `permissions.py`。

理由和 `build_org_tree()` 完全一样：**权限判断散在各处 = 迟早有一处漏了 `.active()`
或漏了 `is_active`，而漏权限检查的症状是静默越权，不报错。**
