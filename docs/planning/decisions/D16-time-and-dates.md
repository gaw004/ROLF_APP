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
