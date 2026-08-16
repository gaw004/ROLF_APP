# D35 · 活动的指派与代录路径（2026-08-14）

结论：admin 能把本 ministry 的在编人员**指派**进某个 `EventRole`（对方要答复），
也能替人**代录**。两条路都落在 `Participation` 上，**不新建表**：
`Status` 加 `INVITED` / `DECLINED` 两档。

> 2026-08-14 从 [D32](D32-worker-axes-schedule-and-assignment.md) 第六节拆出来独立成条。
> ⚠️ 拆分时有两处实质改动：
> ① 原第六节末尾那套「R6 / R7 排除本 ministry 带薪员工」的规则
> **已被 [D38](D38-served-as-volunteer-or-work.md) 推翻并删除**，理由见那一条第二节；
> ② 原计划的 `Participation.source` 字段**当天就被取消了**，见下面第三节。

## ⭐ 唯一的不变量：两条新路径都必须经过 `sign_up()` 的那两道门

紧急联系人、未成年同意 —— 这两道门现在写在 `events/services.py::sign_up()` 里，
而那个函数的注释里已经写着：

> an admin entering somebody from a paper list reaches this function too

本决策要让这句话**第一次变成真的**，不是绕开它。
⚠️ 绕开的表现是：一个没有紧急联系人的未成年人被 admin 指派进活动，
一路畅通，直到活动当天才发现没人可以打电话。

### ⚠️ 2026-08-15：两道门之外，还要看一眼冲突

`assert_signup_allowed()` 那两道门是**会拦下来的**（缺紧急联系人、未成年没同意）。
调度冲突不是门 —— 它是一条提示，**从不拦截**（[D39](D39-scheduling-conflicts.md)）。

所以指派页和代录页上是两件事，顺序不能反：

```
① conflicts_for(contact, 活动日期, 起, 止)   → 有就显示，带一个「仍然指派」
② assert_signup_allowed(...)                → 有就拦住
```

⚠️ 冲突要在**提交之前**就显示出来（选完人那一刻），不是提交之后才告诉他 ——
提交之后再说，就等于让他做完了才知道。而两道门是提交时的校验，位置不变。

### ⚠️ 「经过 `sign_up()`」不等于「调用 `sign_up()`」

`sign_up()` 会写 `status=REGISTERED` 和 `registered_at` —— 而指派要落的是
`INVITED`。直接调它，指派就变成了报名。所以落地的形状是**把门抽出来**：

```python
events/services.py::assert_signup_allowed(contact, event, consent)   # 两道门，唯一的一处
    ↑ sign_up()          ↑ invite()          ↑ register_on_behalf()
```

三条路径**第一件事**都是调它。抽出来而不是互相调用，
是因为三条路径落地的状态各不相同，而门是同一道 ——
让 `invite()` 去调 `sign_up()` 再把状态改回来，是又一次"两个动作必须配对"。

⚠️ 顺带定义 `INVITED` 行的 `registered_at`：**留空，接受的时候才写**。
它的意思是"报名成立于何时"，而被邀请的人还没答应。
（`Participation.Meta.ordering` 是 `["-registered_at", "contact"]`，
空值的排序位置要在实现时看一眼 —— 邀请列表按活动日期排，不靠这个。）

## 一、起因：R8 现在是半瘫的

服务层早就为替人录入写好了，**但没有任何 URL 能走到它** ——
`events/views.py:event_signup` 只能给自己报名。

于是在编人员要出现在 R8 的名单里，只能自己去公开活动页报名。
这是本项目诊断过两次的同一种病（「没有 URL 的功能，测试也没有 URL 可打」），
第三次发作。

## 二、两条写入路径

| 路径 | 落地状态 | 什么时候用 |
|---|---|---|
| 指派 | `INVITED` | admin 把本 ministry 的在编人员放进某个 `EventRole`，对方要答复 |
| 代录 | `REGISTERED` | 纸质名单 / 当面已经答应了，admin 直接录入 |

```
Participation.Status  加两档：INVITED（已邀请未答复）· DECLINED（拒绝）
```

### 为什么是加两档而不是加一张表

`invited → registered → attended` 是同一条生命线上的位置，
和 `registered / attended / absent / cancelled` 同维。

对照推迟清单里被否决的 `needs_reconfirmation` —— 那个是
「这个人和**某一次改动**的关系」，确实是另一个维度，所以那条要用表。这条不是。

### ⚠️ 加两档之后，`sign_up()` 要多认两种可复用的旧行

`sign_up()` 现在只复用 `CANCELLED` 的行，别的一律报
「You have already signed up for this role.」。加两档之后这句话会打到两批真人：

| 场景 | 现在会发生什么 | 该发生什么 |
|---|---|---|
| admin 先指派了他，他没看邮件，自己去活动页报名 | 被拒，而且理由看起来像"你已经报过了"（他确实没报过） | 当成**接受邀请**：`INVITED → REGISTERED`，`declared_by=self` |
| 他拒绝过，后来改主意 | 被拒，且从他那一侧无法自救 | 当成重新报名：`DECLINED → REGISTERED` |

⚠️ 这一条是"两条路都走同一道门"必然带出来的第二半 ——
门统一了，**门后面那张表的旧行也就必须统一处理**。
第一半（[C0.2](../03-roadmap.md) 时发现的"取消之后报不回来"）当初也是在浏览器里
才发现的，不是测试发现的：每个测试取消完就结束了。

### 取消了的 `source` 字段（2026-08-14 同日）

本条原本还要加一个 `Participation.source`（`self_signup` / `assigned`），
记「这一行是怎么进来的」。**当天取消**，因为 [D38](D38-served-as-volunteer-or-work.md)
落地之后它不再挣钱：

- `source` 当初的用处是**当身份轴的代理变量** —— 在没有 `served_as` 的世界里，
  「谁点的按钮」是唯一能猜出「这算不算工作」的线索。真事实一旦被直接记下来，
  代理变量就没有读者了；
- 它剩下的那件事（这一行是谁造的）**simple-history 的创建行已经答了**，
  而且答得更细：哪个 admin、什么时候。

⚠️ 而留着它是有代价的，不只是没用：`source` 和 `served_as` 会在同一次迁移落地、
出现在同一个表单上、名字都很短、值还都是「自己 / 别人」——
**两个长得几乎一样、其中一个不承重的字段，下一个人一定会把它们合并成一个**，
而合并之后错的是承重的那一个。

**真正要保住的那半件事**（这句身份是谁说的）改挂在事实上，不挂在行上：

```
Participation.served_as_declared_by = self | admin
```

`self_signup` ≡ `declared_by=self`；代录 / 指派 ≡ `declared_by=admin`。
它还能答旧字段答不了的那一格：**自己报的名、事后被 admin 改成了工作安排**。
形状同 `Participation.checked_in_method`（「这一行的考勤是他填的还是我填的」）——
不是新规矩，是同一条规矩用在第二个事实上。用处见
[D38 第四节](D38-served-as-volunteer-or-work.md)。

## 三、统计口径：不定就会自己变，而且不报错

R5（每个工种多少 volunteer）、满员率、缺勤率分母现在都在数 `Participation` 行。
加两档之后如果不定口径，这些数字会自己变。

**口径**：`INVITED` 和 `DECLINED` **不算报名**。

⚠️ **但不能写成「只数 `REGISTERED` + `ATTENDED`」** ——
现在的 `with_signup_counts()` 数的是 `~CANCELLED`，**里面含着 `ABSENT`**，
而缺席的人当初确实报了名。写成正数枚举，会在同一次改动里
**把缺席的人从报名数里删掉**，那是第二次静默语义变更，而且没有人在找它。

**口径写成排除式**：

```python
counted   = exclude(status__in=[CANCELLED, INVITED, DECLINED])   # 算不算报名
notifiable = exclude(status__in=[CANCELLED, DECLINED])           # 该不该通知
```

### ⚠️ 这两个口径各有一处，而受影响的是六处

不这么收的话，下面每一处都会自己变，而且**全都不报错**：

| 落点 | 现在的定义 | 不改会怎样 |
|---|---|---|
| `EventRoleQuerySet.with_signup_counts()` | `~CANCELLED` | 满员率、`is_short`、`understaffed()` 全部把待答复的人算成已报名 |
| `ministry_report()` 的 `parts` | `.notifiable()` | `signups` / `volunteers` / `repeat_rate` 虚高；⚠️ **`hours_missing` 尤其难看** —— 被邀请的人永远不会有 hours，那个"缺多少条工时记录"会跟着待答复人数涨 |
| `_absence()` 的分母 | 同上 | 同上 |
| `_top_volunteers()` / `_monthly_series()` / `_role_gap()` | 同上 | 排行榜、月度图、工种缺口图各错一点 |
| 🔴 **`resolve_recipients()`** | `.notifiable()`（只排 `CANCELLED`） | **已经拒绝的人还会收到"活动改期"通知** ——`DECLINED` 必须和 `CANCELLED` 一样出局，而 `INVITED` 要留下（他还没答复，改期正是他需要知道的） |

⚠️ 这就是为什么口径要收成上面那两个 queryset 方法，
而不是在六个地方各写一次 `exclude(...)`：本项目已经为"同一条规则的两份副本"
付过三次学费（`understaffed()` 的 `is_short`、`consent_required_for()`、`on_duty()`）。

**报表上分开列两个数**：「已确认 N 人」和「待答复 M 人」并排。
藏起来的话，「没人答应」和「还没问」在页面上长得一模一样 ——
同 [D27](D27-ministry-report.md) 里「未成年无同意记录为 0 也要画出来」的规矩。

⚠️ 缺勤率的分母那一节（[D27](D27-ministry-report.md)）问的是
「这场活动还有没有报名停在 `registered`」。**`INVITED` 的行不能算进那个判断** ——
"还没答复"和"没人处理过考勤"是两件事，混在一起会让分母虚高。

## 四、被邀请没答复的人当天来了

**直接签到，状态盖成 `attended`。** 沿用 [D27](D27-ministry-report.md) 里
`check_in()` 把 `absent` 盖回 `attended` 的先例：
要求先走完流程，**结果是没人走流程**。

## 五、答复的那一下，顺便把身份也答了

被指派的人本来就要点一次「接受」。[D38](D38-served-as-volunteer-or-work.md)
的身份问题挂在这一下上 —— **不多一次操作**：

```
Food Pantry 邀请你参加「3/22 春季发放日 · 接待」

  你这次以什么身份参加？
    ○ 志愿服务 —— 我自己的时间
    ● 工作安排 —— 算在我的工作时间里     ← 被指派的默认在这一档

  [ 接受 ]   [ 拒绝 ]
```

⚠️ 默认预选在「工作安排」，因为他是被安排的；**但两个选项都画出来**，
他可以改成志愿。D38 第四节整节讲的就是为什么这一下必须由他自己点。

## 六、通知

走 `core/notifications/` 的投递适配器（[D22](D22-event-notifications.md)），
**不新建投递代码**。三个触发点：被指派、指派被取消、活动改期（已有）。

## 七、代价

1. **多两个状态档**，于是每一处数 `Participation` 的地方都要重看一遍口径（第三节）；
2. **指派只能指派本 ministry 的在编人员** —— 跨 ministry 借人现在做不到，
   要走"请他自己报名"。主动接受：跨部门指派要先回答"谁有权指派谁"，
   而那是权限模型的下一轮；
3. ⚠️ **admin 可以替人拒绝**（改 `DECLINED`）。没有拦，因为电话里说了不来是常事 ——
   但它和 [D38](D38-served-as-volunteer-or-work.md) 的身份字段一样留痕。
