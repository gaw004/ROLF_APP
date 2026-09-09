# D43 · 给出去的时间和接受到的时间，是两个方向相反的数（2026-09-08）

结论：`Participation.hours` 和 `SessionAttendance.hours` 装的是**一个人给出去的时间**。
一个来接受服务的人有另一个数 —— **他被服务了多久** —— 而那个数**不存在任何一列里**，
从 `Session` 的起止两列算出来。

⚠️ **这不是教育项目专有的。** ESL 是第一个撞上它的服务形态，不是它的边界：
六次财务辅导、八周的支持小组、职业培训、法律咨询、健康筛查工作坊 ——
凡是按次进行的服务，「基金会花在他身上多少时间」都问得出来。
行业里也不叫「课时」：Salesforce PMM 那一列叫 `Quantity`，单位是可配的
（hours of instruction / sessions / items），**正因为它不假设你在办的是课**；
NRS 叫 contact hours，是因为成人教育这一行只报这一种单位。
本文件一律写「接受到的时数」，ESL 只作例子出现。

这条决策**不推翻** [`../participants.md`](../participants.md) 第六节的 L1 性质轴，
也不动 [D38](D38-served-as-volunteer-or-work.md) 的 `not_applicable`。它改的是那条轴的
**措辞**：「`attending` 不记工时」一直被读成「来接受服务的人的时间不重要」，
而准确的读法是「他的时间不进这个账本，因为它是反方向的」。两处已就地写了修订说明
（[README 加一条新决策时](README.md#加一条新决策时)的第 3 条）。

## ⭐ 唯一的不变量：两个方向永远并排，永远不相加

| | 装什么 | 方向 | 谁在读 |
|---|---|---|---|
| `Participation.hours` / `SessionAttendance.hours` | 他**给出去**的时间 | 人 **→** 基金会 | 年报的志愿小时数、FLSA、[D27](D27-ministry-report.md) 的已记录工时 |
| 接受到的时数（算出来的） | 他**接受到**的时间 | 基金会 **→** 人 | 「我们提供了多少服务」、按次服务的项目要报的时数 |

[D36](D36-two-hour-ledgers.md) 那条「两个账本永远不相加」管的是两个**流入**账本
（活动工时 + 班表）。这个数是**流出** —— 它连账本都不是同一类，所以那条不变量在这里
不是被扩大，是被**再用一次**：加起来得到的是「我们收到的时间 ＋ 我们发出的时间」，
一个没有定义的量，而它看起来完全合理。

⚠️ 这是本项目第四次判同一件事。前三次分别是 D36 的两个账本、
[D27](D27-ministry-report.md) 的两组指标不相加、以及 D38 第七节那个**可以**相加的例外
（同一件事的两个来源，按定义不相交）。这一次不属于那个例外：方向相反的两个数，
无论如何都不相交，也无论如何都不该相加。

## 一、行业查证（2026-09-08）

基金会走查时提出的原话是：来接受服务的人「肯定希望自己 attend 了多少小时」。
查下来这不是一个可选项，而成人教育恰好是被强制要求的那一类：

1. **WIOA Title II / NRS**（美国成人教育的联邦报表系统）把 contact hours 列为必报的
   参与度指标，各州要求教学时数至少每月记录一次；并且有一条 **12 小时门槛** ——
   一个人拿到 12 小时服务之前，他连 reportable participant 都不算。
   ESL 明确在这套要求之内。[NRS Technical Assistance Guide](https://nrsweb.org/sites/default/files/NRS-TA-Mar2021-2024-508.pdf)
2. **两个对象、两个字段，不是同一列。** Salesforce PMM 的 `ServiceDelivery` 上是
   `Quantity` 加一个可配的单位，点名界面默认就是 Client + Quantity + Attendance
   Status；而志愿工时在**完全另一套对象**上。
   [Track Service Deliveries](https://trailhead.salesforce.com/content/learn/modules/service-delivery-with-program-management-module-pmm/track-service-deliveries)
3. **这个数通常是算出来的，不是一个个手填的。** clock-hour 那套系统的说法是
   "awarded time"：默认按这一次的排定时长给，偏离时按模型折算，并留下一份
   「这个数是怎么算出来的」说明。[Clock Hour Attendance Records](https://support.coursekeyeducation.com/hc/en-us/articles/360039671374-Clock-Hour-Attendance-Records)
   · [Adult Education Proxy Models](https://www.azed.gov/adultedservices/adult-education-proxy-models)

⚠️ 只有教会那一类产品（ChurchSuite）是纯粹的「来没来」不记时长 ——
因为教会聚会本来就不报这个数。**产品形态的差别在于报给谁，不在于这件事重不重要。**

## 二、为什么它不能进 `hours` 那一列

最省事的做法是把 `hours` 放开给 `attending` 用，而它有一个具体的坏后果：
[`../06-roadmap.md`](../06-roadmap.md) L5.7 要把报表的四个工时口径改成两个来源相加
（`Participation.hours` ＋ `SessionAttendance.hours`）。一旦接受服务的那一档也往这一列写，
那四个 `Sum` 加出来的就是流入 ＋ 流出。

⚠️ **它不报错，也不难看** —— 「今年 3,200 小时」比「1,240 小时」更像一个好数字，
而每一个环节看起来都对。这正是本项目反复判死的那个形状。

第二个坏后果在措辞上：`hours` 这一列上挂着 FLSA 提示、志愿小时数、
`hours_per_helper` 三样东西的定义，全都以「这是给出去的时间」为前提。
让一列同时装两个方向，那三处定义**同时**失去前提，而它们不在一个文件里。

## 三、为什么一个存、一个算

这是本条决策里唯一一处需要判断的地方，而判据是「**这个数会不会和排定值不一致**」。

**给出去的时间必须存**，理由 `check_out()` 的 docstring 已经写了三条：有人忘了签退
（时间戳会说 0 小时，而他干了 4 小时）、有人是从纸质签到表补录的（根本没有时间戳）、
有人走了又回来（一对时间戳表达不了）。**所以它必须是一个人可以更正的权威值。**

**接受到的时数可以算**：这一次服务办多久他就接受多久，而
[`../06-roadmap.md`](../06-roadmap.md) L5.1 已经把每一次的起止两列存进 `Session` 了。

> 他接受到的时数 ＝ 他**出勤过**的那几次的时长之和

同款先例本仓库有两处：`Event.duration`（"Derived, never stored: two columns already
say it"）和 `services.scheduled_hours()`。而后者的 docstring 里已经写着这条分界线的
另一半 ——「这是**排定**的长度，永远不是任何人实际做了什么」。

⚠️ 早退这个口子不需要新列：这一行以后有 `checked_in_at` / `checked_out_at` 时
（点名页，L5.8），按实际时段算即可 —— 仍然是算，不存第二份。

⚠️ 落点是 `SessionAttendance.hours_received`（一次）和
`events/services.py::hours_received()`（加总）。加总落在服务层而不是模型上，
同 `scheduled_hours()` 的理由：日期算术不许出现在视图里，而那条有守卫盯着。

⚠️ **命名**：行业术语是 contact hours，但本仓库 `contact` 是**人**
（`participation.contact`），`contact_hours` 会被读成「某个人的小时数」。
所以叫 `hours_received` —— 和 `hours`（给出去的）配成一对，方向写在名字里；
对外报表的名字写在 docstring 里，让 grep 找得到。

## 四、L1 那条轴不推翻，改的是它的措辞

| 原来的读法 | 准确的读法 |
|---|---|
| 「`attending` 的角色不记工时」 | `hours` 这一列记的是**这个人给出去多少时间**。来接受服务的人没有给出时间，是基金会花时间在他身上，所以那一列在他行上必须是空的 |
| 「不记工时是他不关心的事」 | 他关心的正是自己接受到多少。不关心的是**这一列** |

判据、约束、迁移**一个都不动**：

- `ParticipationRole.Nature` 两档不变，L1 的落点不变；
- `Participation.records_hours` 的语义不变（它答的是「这一行进不进给出去那个账本」）；
- [D38](D38-served-as-volunteer-or-work.md) 的 `not_applicable` 语义**完全不变** ——
  它问的是**身份**（这次算志愿还是算工作），不是时长。一个受助者没有身份可答，
  和他接受了两小时服务，是两句互不相干的话；
- L4 那条数据库约束不变，不需要回填。

## 五、代价，以及三个写明的缺口

**代价一：报表上会多一个数。** 改口径属于 [`../06-roadmap.md`](../06-roadmap.md) L5.7，
本条只提供那个数本身。它上报表时必须和给出去的工时**并排、不相加**，
且照 [D27](D27-ministry-report.md) 给它自己的空态。

**代价二：「不许相加」今天没有守卫。** D36 那条不变量配了守卫，这一条暂时没有 ——
本轮全仓只有一个调用方，一条守卫的白名单会比被保护的地方还长（那正是守卫失效的方式）。
⚠️ 重启条件写死：**L5.7 开始在同一屏上打印两个数的那一刻**，守卫跟着写。

代价三，也是最容易写错的一格：**没有讲次的活动上，这个数不是 0。**
时长是从每一次的起止两列算的，所以这个问题只在**有 `Session`** 的活动上答得出来：
一门课、一个八周小组、一期辅导算得出；一场单次的发放日、一次上门送餐没有讲次，
那个问题在那一行上**不成立**。一个人来领了一箱食物，说他「接受了 0 小时服务」是错的 ——
不是零，是这个问题问错了。而 `Sum` 在没有行的时候返回的正好是空，
被渲染成 `0` 只差一个 `or 0`。[D27](D27-ministry-report.md) 那条
「没有和没算不能长得一样」在这里是第四次应用。

推迟的，各带重启条件：

| 推迟 | 什么时候再看 |
|---|---|
| 早退 / 迟到按实际时段折算 | 点名页写入 `checked_in_at` / `checked_out_at` 之后（L5.8） |
| 单次服务（发放日、上门送餐）也要一个时数 | 出现这个需求时。⚠️ 它**不能**套用这里的算法（没有讲次），而 [`../participants.md`](../participants.md) 第九节「不来活动的服务没有落点」那条缺口正压在同一格上 |
| 异步、自学时长（NRS 的 proxy hours 三种模型） | 基金会真的要报 proxy hours 时。⚠️ 那一档按定义没有讲次，同上一行 |
| 12 小时门槛（reportable participant） | 基金会真的要报 NRS 时。它是一个读口径，不是一个字段 |
