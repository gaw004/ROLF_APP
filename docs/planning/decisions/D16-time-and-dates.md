# D16 · 时间与日期的唯一口径（2026-07-28）

> 本文件是 `../goal.md` 拆出来的一条决策记录（2026-07-30 拆分，内容一字未改）。
> **`goal.md` 仍是唯一入口**：决策一览表和「去哪找」都在那里，
> 代码注释里写的 `goal.md D16` 指的就是本文件。

> 起因：`Assignment` 的「在职」判定要用到"今天"，而这个概念在 `USE_TZ=True` 的项目里
> 有三种写法，其中两种是错的，且**错了不报错，只是边界日的数字悄悄不对**。

**原则：数据库永远存 UTC，业务上的"今天"永远是基金会所在时区的今天。** 两件事不能混。

- ❌ `datetime.date.today()` —— 依赖服务器本地时区。部署到 Render 上是 UTC，本机是 PT，两边行为不一样。
- ❌ `timezone.now().date()` —— 那是 **UTC 日期**。太平洋时间 7 月 27 日下午 5 点，
  UTC 已经是 7 月 28 日，于是"今天在职"的判定**提前跨天**。
- ✅ `timezone.localdate()` —— 按 `settings.TIME_ZONE`（`America/Los_Angeles`）折算。

## 第二句：问「某个存下来的时刻是哪一天」（2026-07-30 补）

> 上面那三条只管**取**今天。B12 实施时踩到的是另一半：**已经存在库里的
> `DateTimeField` 是哪一天**。R8 那句"活动当天在职的 employee"照 roadmap 原文写成
> 直接问那个时刻要日期，而它取回来是 UTC —— 太平洋时间 7 月 31 日下午 6 点的活动，
> 答的是 **8 月 1 日**，于是整条查询问错了一天，差一天，**照例不报错**。

- ❌ 直接问存下来的时刻要 `.date()` / `.year` / `.month` —— 那是 UTC 的那一天。
- ✅ `core.timeutils.local_date_of(moment)` / `local_month_of(moment)`。

所以 D16 现在是两句，不是一句：

> 规矩两句话：取"今天"走 `local_today()`，问"某个存下来的时刻是哪一天"走
> `local_date_of()`。**对一个 `DateTimeField` 直接取日期，在本项目里没有正确的用法。**

守卫也补了第三条模式（本项目所有 datetime 字段都叫 `*_time` 或 `*_at`）。
它一上线就又抓到两处 —— 都在同一天写的、**专门用来验 R8 时间口径的测试**里。
经过见 [`02-roadmap.md` 的计划外记录](../02-roadmap.md#-计划外b12eventstart_timedate-是-utc-的那一天)。

## 第三句：那个 aware 的「现在」，也不许被问日子（2026-09-02 补）

> 上面两条守住了**取今天**和**问一个存下来的字段**。第三个变体两条都不沾：
> 一个 aware 的 datetime **变量** —— `local_now()` 本身，或者本项目每个测试模块
> 开头都会写的 `NOW = local_now()` 常量 —— 被问了它的日子。
> 那仍然是 UTC 的那一天，而 `STORED_INSTANT_DATE` 那条守卫按字段名匹配
> （`*_time` / `*_at`），一个叫 `NOW` 的变量它一眼都不看。

🔴 **它是四个变体里最难发现的一个，因为它一天里只有一部分时间是错的。**
太平洋时间中午写的测试是绿的；同一条测试下午六点跑就红了 —— UTC 已经翻页，
于是「昨天」算出来正好等于本地的今天。所以它**带着绿色上线**，几天之后在一次
什么都没改的运行里变红，而那读起来是「代码坏了」，不是「这条测试从来就是错的」。

2026-09-02 一小时之内撞到两次：一次是当天新写的 `dashboard/tests.py`，
另一次在前一天写的 `notices/tests.py` 里 —— 后者写完时全绿，第二天下午才红。
补上守卫之后它当场又抓出**三处早就躺在 `events/tests.py` 里的**同类写法，
其中两处是给一个孩子设生日、用来验「十八岁生日当天算不算未成年」的。

- ❌ 对 `local_now()` 或 `NOW` 求日期，无论中间有没有先减一个 `timedelta`。
- ✅ 需要日期就从 `local_today()` 出发，**算术做在 date 上，不做在 instant 上**。

守卫是 `core.tests.TimeSourceGuardTests.test_nobody_takes_the_day_off_an_aware_now`。
⚠️ 它的正则**不许在注释里被写成字面** —— 这个类开头那句话早就写着，
而补这一条的时候还是先在自己的注释上红了一次。

**三层落地，缺一层就守不住：**

1. 唯一入口。 `core/timeutils.py`，全项目只有这里碰"现在"。
   现在住着 `local_today()` / `local_now()` / `local_date_of()` / `local_month_of()` /
   `day_start()` / `month_bounds()` —— 后三个是 B12 的报表边界要的，
   原文预言的"Phase C 还会往里加 `month_bounds()`"提前兑现了。
2. 把时钟注入 API，不要在函数体里隐式取。 所有跟日期有关的 queryset 方法都写成
   `def active(self, on=None): on = on or local_today()`。
   默认值必须在**调用时**求值 —— 写成 `def active(self, on=local_today())` 是经典的
   进程启动时冻结 bug，长驻的 gunicorn worker 上会越跑越错。
   参数化顺带让"查某一天的名单"和测试边界都变成免费的。
3. 用 linter 钉死，不靠自觉。 加 `ruff`，开 `DTZ` 规则组
   （`flake8-datetimez`，就是为这个问题存在的：`DTZ011` 禁 `date.today()`、
   `DTZ005` 禁裸 `datetime.now()`）。`DTZ` 抓不到 `timezone.now().date()`
   （那是 tz-aware 的，linter 认为合法），所以再补一条 grep 守卫测试放 `core/tests.py` ——
   和 `test_no_model_changes_are_missing_a_migration` 是同一个套路：用测试当 lint。

**代价**：多一个开发依赖（`ruff`，不进生产）、多一条守卫测试。
换来的是新人（含半年后的你）写错会当场变红，而不是等某个 11 月的傍晚发现在职人数不对。
