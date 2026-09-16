# D49 · 一门课的讲次，由一条规则在发布时排完（2026-09-16）

结论：发布页上「A course or program — sign up once」那一档，现在和「每周」那一档
**共用同一个重复选择器**；按下发布时那条规则**展开一次**，落成 N 个
[`Session`](../../../events/models.py)。规则**不存**。

> 本条让 [`../phase-c.md`](../phase-c.md) 的 L5.6 真的落了地，并且作废了
> `services.add_session()` docstring 里那句「⚠️ Until L5.6 its only callers are
> tests」。两处都已就地改口。

## ⭐ 它补的是一个「半个功能」

`Session` 这张表、`Session.clean()` 那三条规则、`services.add_session()` ——
从 L5 起就都在。缺的是**门**：全站没有一条 URL 到得了 `add_session()`，
只有 Django admin 的 `SessionAdmin` 能加，而 ministry admin 被
`StaffOnlyAdminMiddleware` 挡在 `/admin/` 外面。

所以在这条决策之前，发布一门课得到的是**一个有起止日期、一讲都没有的壳**。
这是 [`../phase-d.md`](../phase-d.md) 第四节点名三次、
`core/context_processors.py` 开头列了五个的同一种缺口的第六次。

## 一、和 `EventSeries` 的分界（决定 16 那张表的第四格）

两档用同一个选择器，产出的东西**完全不同**：

| | 一条 series | 一门课 |
|---|---|---|
| 产出 | N 个**互相独立的** `Event` | 一个 `Event` + N 个 `Session` |
| 报名 | 每一场各自报 | 报一次管一学期 |
| 规则 | 存进 `EventSeries.rule` 一列 | **不**存 |
| 往后 | 每年滚着再生成 | 排完就完了 |

**共用的是「怎么问」，不是「存不存」。** 于是选择器抽成
`RecurrencePickerMixin`，而「拼好的规则最终写成什么」是它的一个钩子
（`rule_to_store()`）：系列那边覆写成原来的 `_rule_for()`（因为 `rule` 在那儿是
一**列**，一变就撞 `_refuse_rewriting_the_rule()`），课这边直接就是答案。

### 为什么课不存那条规则

它**没有第二个读者**。不滚动生成、不续排、不重算。存下来就是「这门课什么时候上」
有两个答案 —— 而 [D14](D14-constraint-is-the-only-rule.md) 的整个要点是它只许有一个。

## 二、学期的两端是**推**出来的，不是问来的

`Event.start_time` = 第一讲的开始，`Event.end_time` = 最后一讲的结束。

⚠️ 问一遍就是同一件事有两个来源：有人把结束日期填在最后一讲之前，
于是 `Session.clean()` 拒掉末尾几讲 —— 一个「我填了 12 次、只排出来 9 次」的页面，
**而它不报错**。

🔴 **顺序因此是死的**：先把两端写到活动上、保存，再落讲次。倒过来做，
`Session.clean()`（「一讲必须落在它挂着的那场活动自己的两端之内」）当场拒掉第一讲
—— 因为那时活动还没有两端。

## 三、「不结束」那一档在课上不存在，而理由是硬的

`Event.end_time` 是 `NOT NULL`，而它由**最后一讲**推出来 ——
一条不结束的规则没有最后一讲。

⚠️ 不是「对课没意义」。写成后者的话，下一个人会觉得这是个可以商量的产品判断。

## 四、展开只有一次

页面上那份预览、表单的校验、和真正落库的那一串，是**同一个列表**
（`ProgramForm.clean()` 把它放进 `cleaned_data["moments"]` 交出去）。

各算一遍的结果是「页面说 12 讲、按下去排了 13 讲」，而两边各自都渲染正常。

⚠️ 「写规则我自己来」那一档的校验**就是这次展开**：系列那边靠
`EventSeries.clean()`（`rule` 在那儿是一列，模型判得了它），课没有那张表。

## 五、手工加 / 删一讲

新页 `/events/<pk>/meetings/`，权限问的是 `can_manage_event()`（排课表是办这场
活动的一部分），对一场单场活动答 **404** —— 那个地址在那里不存在，不是
「存在但不给你」。

* **加**：落在学期之外的拒绝**不重写**，它是 `Session.clean()` 的那一句
  （「This run ends on …」）。本轮**不自动放宽学期的两端** —— 那会悄悄改一件
  已经通知过报名者的事；那句拒绝旁边画了一条到编辑页的链接。
* **删**：判据用现成的 `services.register_kept_at()`，**不另写**。它的 docstring
  记着上一版为什么不够：一个人**来了但不记工时**的场合（决定 20）那个函数完全
  看不见，于是一门十二个学生全部点到的课，删除键是亮的。
  ⚠️ Django admin 的删除权限问的也是它 —— **两扇门，一条判据**。
* 这是这个项目里少数几处**真删行**而不是记结束日期的地方：一个排错了的晚上
  不是一件发生过的事，它只是一行写错的日程。真发生过的那一半删不动。
  这一笔已经签进 `DeletesInServicesAreEnumeratedGuardTests` 的名单。

## 六、执行时踩到的四件事

1. **纯 mixin 上声明的表单字段会被整个丢掉。** Django 只从「自己的类体」和
   **带 `declared_fields` 的基类**收集字段。`RecurrencePickerMixin` 因此要
   `metaclass=DeclarativeFieldsMetaclass`。⚠️ 它之所以**吵**（62 条同时红）
   纯属运气 —— `clean()` 里恰好有一句 `add_error("repeat_weekdays", …)`。
   没有那一句的话，这九格会安安静静地不出现在页面上。
2. **`SessionForm` 这个名字已经有人用了**（admin 的那张）。后定义的悄悄盖掉先
   定义的，而症状是「表单多了两格必填」。改名 `MeetingForm`，并把「为什么有两张」
   写在了它上面。
3. **预览那一行说的是「12 occasions」**，而一门课排的是「讲」。浏览器走查抓到的，
   这个分支已经为同一种假名词修过好几次。
4. **那块月历的翻页键写死了 `series_preview`。** 在课的页面上翻一个月，会把一份
   课的表单打到系列的预览视图上 —— 它按 `EventSeriesForm` 读，说的是
   「occasions」，算的可能是另一批日期，**而整件事不报任何错**。

⚠️ 后两条都是**先在浏览器里看见错的输出**才写的测试，所以反向已验。

## 七、标题只有两个（用户拍板）

`Publish an Event` / `Publish a Program`。第三档（Every week）**也叫
Publish an Event**，代价如实记在 `_publish_heading.html` 里：那一档建的是
`EventSeries`。两个标题比三个好记，而人来这一页要办的事确实只有两件。

🔴 那个 `<h1>` 在 `#when-block` **外面**，而换档那一趟 HTMX 只换块内 ——
所以它和 `_filter_summary.html` / `_event_count.html` 一样要画两遍，
第二遍带 `hx-swap-oob`。这是同一个形状的第四次。
