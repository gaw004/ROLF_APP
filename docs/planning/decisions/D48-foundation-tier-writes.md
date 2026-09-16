# D48 · 基金会那一层能写，而不只是能读（2026-09-16）

结论：foundation tier 现在**替任何一个 ministry 发布活动**，并且**改得动每一场**
—— 编辑、开工种、签到、记工时、群发通知。判断照旧只写在
[`org/permissions.py`](../../../org/permissions.py)。

> 本条**推翻了 2026-08-05 定下、9-03 重申的「它读得了每一场、改不了任何一场」**。
> 不是绕过它，是明说换掉。为它写的那个测试类和六处分支，跟这次改动一起清掉了 ——
> 清掉的方式写在第四节。

## ⭐ 触发它的不是「想要更多权限」，是一个陷阱

用户当天只提了半句：「foundation admin 也有发布 event/programs 的权力。」

而只放开发布是走不通的，这是执行时才看见的：
`event_create` 发布成功之后重定向到编辑页，而 `can_manage_event()` **故意不含**
foundation tier。于是他会：

1. 打开发布页，填完，按发布 —— 成功；
2. **下一秒 403**。开不了工种、发不了通知、管理列表上那一行也没有状态下拉。

发出来的是一个**没有任何工种、谁也报不了名的壳**，而这个仓库自己的话是
「what you publish is not finished until it says what help it needs」。

所以这条决策真正的问题不是「要不要给更多」，而是**「发布」这个动词到底包含什么**。
答案是：包含把它办起来。

## 一、代价是先量过再拍板的

放开之前实测了一遍：**11 条测试会红**，其中 7 条属于一个专门为这件事写的类
`FoundationTierReadOnlyTests`，它的 docstring 第一行就是
「2026-08-05 feedback: the foundation tier reads any event, changes none」。

用户看着这个数字拍的板。写下来是因为「拆掉一个带测试的既有设计」和
「加一个功能」是两件事，而只有前者需要一次明确的授权。

## 二、这条决策**没有**动的东西

| 仍然关着 | 为什么 |
|---|---|
| 把一场活动**转授**给第三个人（`can_grant_event_admin`） | 「这一场交给谁办」是那个 ministry 的事（[D47](D47-event-level-grant.md)）。它缺的不是知情权 |
| 被授权人（D47）发布 / 管系列 | 放宽的是 foundation tier 那一层，不是所有人 |
| `org.assignment` 等 `SUPERUSER_ONLY` 那一列 | 那份名单自己写着「widening this tier is the foundation's call and not a tidy-up」 |

## 三、一个没有合并的巧合

D48 之后 `can_view_event_records()` 和 `can_manage_event()` 对**每一类人**答案相同。
两个函数都留着，而且**不许**把前者改写成 `return can_manage_event(...)`：

* 它们是**两个问题**（「看得见吗」／「改得动吗」），而这个仓库已经付过一次
  「两个问题共用一个判断」的钱；
* 成员集合**分开过，而且是往两个方向分的**：8-05 到 9-16 之间 foundation tier
  只在读那一边；[D47](D47-event-level-grant.md) 的被授权人至今只在**写**那一边
  （他管得了这一场，却持不了 `MinistryRole`）。

今天相等是一个巧合，不是一条规律。合并会把它固化成「永远相等」，而下一次分家
将无声无息。

## 四、被撤销的规矩，怎么记下来的

**一条断言都没有删。** 那个测试类整个改名 `FoundationTierReachTests`，
每一条反过来钉：

| 原来钉的 | 现在钉的 |
|---|---|
| 不能签到 | 能签到 |
| 三个写页面关着 | 开着 |
| 不画 Edit / Notify 链接 | 画 |
| 别人 ministry 的行是只读 | 别人的行他也改得动 |
| 看得见的多了、能改的一个没多 | 看得见的多了、能改的也多了 |

⚠️ **手法一处没反**：这些断言仍然走 POST 而不是读 HTML。撤的是
「读得了改不了」那一档，**不是**「画不画按钮不是权限」那条道理。

⚠️ 一条规矩被撤销时把守着它的测试删掉，等于连「这件事曾经是反过来的」也一起删掉。

## 五、连带清掉的死代码

| 清掉的 | 为什么它死了 |
|---|---|
| `event.can_manage`（管理列表逐行的旗子） | 那一页的行只有三个来源，三种今天都改得动 —— 它只剩一个答案 |
| 它在模板里的六处分支 | 同上，外加一句只有在一个不存在的身份上才会出现的「Read only」 |
| `administers_one_of()` / `holds_grant_on()` | 零调用方（[phase-d](../phase-d.md) 判据 2）。删它们时把「为什么需要过」和「回来时该长什么样」写在了原来的位置上 |

## 六、一句只有浏览器走查才抓得到的假话

管理页顶上那段横幅写着
「publishing, editing and notifying stay with each ministry's own admins」。

我 grep 的是「read only / 只读」，而这句话**一个字都没写**。
**一句假话不会用关键词标注自己。** 它现在改写了，并且有一条测试钉着原文不再出现。

## 七、顺手修的一个既有脆弱点

`in_foundation_tier()` 对一个**没存过**的 `User()` 会抛 `ValueError` ——
Django 的 `AbstractBaseUser.is_authenticated` 是硬编码的 `True`，拦不住它，
而 `user.groups` 对一个没有主键的实例直接抛。它的兄弟
`ministry_ids_administered_by()` 早就兜住了同一种输入。

两个并排的谓词对同一个输入一个答 False、一个 500，是这个模块最不该有的那种不一致。
守卫在旧代码上验过会红。
