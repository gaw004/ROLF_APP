# Phase D · 员工与排班 —— 实施步骤

> **要点、判据、页面总表、已知缺口**在 [`phase-d.md`](phase-d.md)。
> **为什么这么定**在 [D32](decisions/D32-worker-axes-schedule-and-assignment.md)–[D38](decisions/D38-served-as-volunteer-or-work.md)。
> 这一份只讲照着做的顺序 —— 每一步后面标的是对应的那条决策。
>
> ⚠️ **Phase 编号动过**：原来的 Phase D 是资金追踪，现在**顺延成 Phase E**。
> 理由和 2026-07-29 那次 C / D 对调一样 —— 排序跟着"哪件事先做"走，不跟着"当初写在哪"走。
>
> ⚠️ **本文件 2026-08-14 重写过一次**（同日）。初版的三批（D1 / D2 / D3）里
> 十一个页面全是管理侧、少了四条不报错的规则、引了一处不存在的决策。
> 自查清单在 [`phase-d.md` 第六节](phase-d.md#六自查这一轮砍掉和补上的东西)，
> **初版的错不删，记在那里** —— 那是这个项目最贵的资产之一。

## 动手前先读这四条

1. **[D32 的唯一不变量](decisions/D32-worker-axes-schedule-and-assignment.md)**：
   一个人在基金会里只有一条在编路径（`Position` + `Assignment`）。
   本轮任何一步如果开始出现"给这类人单独建一张表"的念头，先回去读那一节；
2. **[D38 的唯一不变量](decisions/D38-served-as-volunteer-or-work.md)**：
   「这一次是工作还是志愿」只由 `served_as` 一个字段承载，**不从任何别的字段推**。
   本轮有两处地方会诱惑你去推（`source` 和"他那天有没有排班"），两处都已经判过了；
3. **三处会静默改变结果**：`kind` 的取值迁移、R8 的名单、R6 / R7 的分母。
   它们各自单独一步，各配一条固定新口径的测试。
   ⚠️ **`vacant()` 不在这三处里，因为它不改** —— 编制人数用新增的
   `understaffed()` 回答（[D37 代价 1](decisions/D37-hris-fields-and-credentials.md)）。
   **能不改口径就不改口径，比改完再配一条测试便宜**；
4. **[D18 的落点规矩](decisions/D18-admin-boundary.md)** 照旧：
   逻辑进 `services.py`，权限只在 `org/permissions.py`，统计在 queryset，视图是薄壳。
   本轮新增六条守卫，见[守卫测试](#本轮新增的守卫测试)。

## 交付节奏：四批，中间留一次反馈

按 [`goal.md` 的交付策略](goal.md#交付策略)（「每个阶段结束都必须是可演示的状态，永远不憋大版本」）。

| 批次 | 装什么 | 为什么是这个顺序 |
|---|---|---|
| **D1** 拆轴 · 身份轴 · 字段与页面 | 拆轴迁移 + `served_as` + HRIS 字段 + 证照 + 五个页面 | 几乎没有新结构，一交付就能看到东西。**身份轴放在最前**，因为需求原文 ② 是本轮的核心，而且它不依赖班表 |
| **D2a** 班表结构与自助 | `WorkPattern` + `Shift` + 生成器 + cron + **批量建模板** + **My Schedule + `.ics`** | 员工第一次能看到自己的排班。⚠️ 到这里为止**只读**，不碰出勤和工时口径（`staff_hours()` 的 `scheduled` 一档除外，见 D2a.6） |
| ⏸ | 试点反馈 | 见下 |
| **D2b** 出勤 · 请假 · 口径 | 例会批量确认 + `Leave` + `staff_hours()` + 报表加块 | 这一批每一个口径决定（未确认怎么显示、约定值怎么写、例会怎么确认）**都会被真实使用改写** |
| **D3** 指派与代录 | `Participation` 两档 + 两条写入路径 + 统计口径 | 修好 R8 的写入侧。依赖 D1 的 `kind=staff` 和 `served_as` |

> ⏸ **中间那一格不是留白，是这一轮的一个决定。**
> 初版 roadmap 在 D1 那一栏写着「他们的反馈会直接改善 D2 的班表设计」，
> 却把三批连着排 —— 那句话没有落点。现在有了：
> **D2a 交付后先让基金会用两周**，D2b 照反馈调。
> ⚠️ 而 Phase C 的 [C0.3 浏览器验收](03-roadmap.md#c03-三角色浏览器验收)、C3 部署、
> C5 试点仍然优先于本阶段的 D2b / D3 —— 排序理由见
> [`goal.md`](goal.md#判定规则的第一次换锚2026-08-14)。

---

# D1 · 拆轴、身份轴、字段与页面

## D1.1 拆轴：`kind` 收窄 + `compensation`（[D32](decisions/D32-worker-axes-schedule-and-assignment.md)）

### 落库的形状，照抄进 `org/models.py`

```python
class Kind(models.TextChoices):
    STAFF = "staff", "Staff"
    BOARD = "board", "Board member"          # EMPLOYEE / VOLUNTEER 去掉

class Compensation(models.TextChoices):
    PAID = "paid", "Paid"
    UNPAID = "unpaid", "Unpaid"
    STIPEND = "stipend", "Stipend"

kind = models.CharField(max_length=20, choices=Kind.choices,
                        default=Kind.STAFF)          # ⚠️ 旧的 default 是 VOLUNTEER
compensation = models.CharField(
    max_length=20, choices=Compensation.choices,
    default=Compensation.UNPAID,                     # ⚠️ 见下
    help_text="Whether this post is paid. Unpaid staff are still staff.",
)
```

三件事写在这里，因为每一件都被问过一次：

1. 为什么是 `TextChoices` 而不是字典表 —— [D5 的判定规则](decisions/D05-lookup-tables-not-enums.md)：
   有代码 branch 就用 `TextChoices`。`compensation` 被志愿小时数的口径
   （[D38 第七节](decisions/D38-served-as-volunteer-or-work.md)）和 FLSA 提示
   （[D38 第八节](decisions/D38-served-as-volunteer-or-work.md)）直接 branch。
   反方向的例子就在隔壁：`EmploymentType` 没有任何 branch，所以它是表；
2. `max_length=20` 跟着 `kind` 现有的宽度走，不动 —— 最长的值 `stipend` 是 7 个字符，
   改宽度会多一次 `AlterField`，而它一个字节都不换来；
3. `choices` 的 label 就是 UI 文案。界面上不许管无薪的人叫 employee
   （[D32 第四节](decisions/D32-worker-axes-schedule-and-assignment.md)），
   而 `Staff (unpaid)` 这种组合写法在页面上拼，不塞进 label —— 塞进去的那一刻，
   `board` + `unpaid` 也会跟着显示成 "Staff (unpaid)"。

两个 default 都在这一步定死，而且它们和同一轮 `served_as`「不许有 default」的规矩
方向相反 —— 正因为相反才要各写一次：`compensation` 是编制的属性（一个岗位必然有
报酬状态），`served_as` 是一句声明（可以没有人说过）。
默认 `unpaid` 而不是 `paid`：新建的空缺岗位默认无薪更安全 ——
错了是招人时被问一句，反过来是报表上凭空多一批薪资岗。

### 两个 migration，以及为什么可以分开

```
org/migrations/0007_position_compensation.py            # schema
org/migrations/0008_kind_to_staff_and_compensation.py   # RunPython，带反向
```

⚠️ 分开的前提是一件 Django 的事实，写下来免得有人以为中间态是坏的：
`choices` 不落到 Postgres 上（`CharField` 不生成 CHECK，也不是 enum 类型）。
所以 0007 跑完、0008 还没跑的那一刻，库里仍然是 `employee` / `volunteer`，
读写都正常，只是 `get_kind_display()` 回显原始值。
「出事时能单独回滚数据那一半」靠的就是这一点。

0007 里 `makemigrations` 会生成**四个** operation —— `Position` 和
`HistoricalPosition` 各一次 `AddField` 加一次 `AlterField`。
⚠️ 影子表那两个不是噪音，不要手工删掉：0008 要在两张表上跑同一张映射表。

### 映射表（正向），两张表逐字相同

| 旧 `kind` | 新 `kind` | 新 `compensation` |
|---|---|---|
| `employee` | `staff` | `paid` |
| `volunteer` | `staff` | `unpaid` |
| `board` | `board` | `unpaid` |

写法定死成一个模块级常量加三句 `update()`，两张表共用同一段代码：

```python
FORWARD = {                       # 旧 kind → (新 kind, compensation)
    "employee":  ("staff", "paid"),
    "volunteer": ("staff", "unpaid"),
    "board":     ("board", "unpaid"),
}

def forwards(apps, schema_editor):
    for label in ("Position", "HistoricalPosition"):
        model = apps.get_model("org", label)
        for old, (kind, compensation) in FORWARD.items():
            model.objects.filter(kind=old).update(kind=kind, compensation=compensation)
```

- 用 queryset 的 `update()` 而不是逐行 `save()`：三条 UPDATE 一次跑完，
  并且不依赖「迁移里的 fake model 不挂 simple-history 的 signal」这个细节 ——
  依赖它是对的，但写成不需要知道它更好；
- ⚠️ 影子表一起迁，理由不是「历史也要好看」，是**不迁会更糟**：
  0007 给历史行的 `compensation` 填的是字段默认值 `unpaid`，于是一条去年的历史行
  会写着 `employee` 加 `unpaid` —— 一个自相矛盾、而且看起来像事实的组合。
  按映射表迁则两列同时对上：「当年这是带薪雇员岗」这个事实由
  (`staff`, `paid`) 原样保住，换掉的只是词汇表，不是事实；
- 现存的历史行全是试点前的测试数据（2026-08-19 与基金会侧确认，上线前会清空），
  所以这一步在真实数据上的风险是零。函数照写 —— 上线之后不会再有第二次机会。

### 反向函数，以及它到底丢了什么

```python
BACKWARD = {                      # (kind, compensation) → 旧 kind
    ("staff", "paid"):    "employee",
    ("staff", "stipend"): "employee",   # 报表上它跟 paid 归堆（D32 第二节）
    ("staff", "unpaid"):  "volunteer",
}
# board 一律回 board，compensation 丢掉
```

docstring 里要写明两句有损，第二句比第一句要紧：

1. `staff` 加 `unpaid` 一律回 `volunteer` —— 它原来是 `employee` 还是 `volunteer`，
   信息在正向是增加的，回不去；
2. `stipend` 这一档**在旧词汇表里根本不存在**，回滚时按保守口径落到 `employee`。
   于是回滚之后再正向跑一次，那个人会从 `stipend` 变成 `paid`，而两次都不报错。
   所以反向函数是止血手段，**不是一扇可以来回走的门**。

### 跟着改的地方，逐个点名（漏了大多不报错）

⚠️ 只 grep `Kind.EMPLOYEE` 是漏的，`Kind.VOLUNTEER` 要一起：

```
grep -rn "Kind.EMPLOYEE\|Kind.VOLUNTEER" --include="*.py" --include="*.html" .
```

改完这条命令必须**零命中**（迁移文件里写的是字符串字面量 `"employee"`，不是枚举）。

| 在哪 | 改什么 |
|---|---|
| `org/models.py::Position.Kind` | 删两档、加 `Compensation`、`default` 改 `STAFF` |
| `org/models.py::Assignment.clean()` | 那条 `employment_type` 规则整条删，⚠️ 必须在**这一步**删，见下 |
| `org/admin.py::PositionInline.fields` | 加 `compensation` —— 建岗位那一屏就要填，它是编制的属性 |
| `org/admin.py::PositionAdmin` | `list_display` 与 `list_filter` 各加 `compensation` |
| `org/admin.py::AssignmentAdmin.list_filter` | `position__kind` 旁边加 `position__compensation` |
| `events/services.py::ministry_staff_participation()` | 改成 `kind=STAFF` **且** `compensation=PAID`，见下 —— ⚠️ 这一步**一个答案都不改** |
| `events/management/commands/seed_demo.py` | 三处（两个 `EMPLOYEE` 加 Driver 那个 `VOLUNTEER`），并补一个 `stipend` 岗位，见下 |
| `org/tests.py` | 六处（263 / 265 / 320 / 339 / 388 / 532 行附近） |
| `events/tests.py` | 四处（1179 / 1235 / 1249 / 1264），⚠️ 最后一处要整条反转，见 D1.2 |

⚠️ `Assignment.clean()` 那条规则**在这一步删，不能等 D1.6**：`Kind.EMPLOYEE`
在这一步就不存在了，留着它整个文件 import 不了。删它的理由是
[D37 第一节](decisions/D37-hris-fields-and-credentials.md)（`fte` 接管了「每周来几天」，
`employment_type` 回到合同形态的字面意思），连同 `org/tests.py` 里那条测试一起删。

⚠️ 而删完之后 `clean()` 里只剩 `super().clean()` —— 那就把整个方法删掉，
不要留一个只调父类的空壳（下一个人会以为那里本来有东西、被删漏了）。
D37 第一节说「在 `clean()` 的 docstring 里写明为什么删」，方法都没有了就没有那个
docstring：**那句话改写进 `Assignment` 的类 docstring**，
它本来就是讲这张表的字段边界的那一段。

⚠️ `seed_demo.py` 要造得出三档，否则 D1.2 的验收在浏览器里走不出来：
`pantry_lead` 与 `pantry_staff` 是 `paid`，Driver 那个空缺岗是 `unpaid`
（空缺也说得出自己有没有预算，正是 D11 拆表的理由），
再补一个 `stipend` 的（例如带津贴的 `pantry_intern`，并给它一个在职的人）。

### R8 那一行必须在这一步动，但它不许改变答案

⚠️ D1.1 的表里原来写着「见 D1.2，不在这一步顺手改」。**那句话执行不了**：
`Kind.EMPLOYEE` 在这一步就消失了，不动那一行的话 `events/services.py`
整个 import 不了。

处置（2026-08-20 落地时定）：这一步把它写成**两个条件**，
合起来和旧的 `kind=employee` 一字不差：

```python
position__kind=Position.Kind.STAFF,
position__compensation=Position.Compensation.PAID,     # ← D1.2 删掉的正是这一行
```

于是拆轴这一步真正做到了「一个答案都不改」——
迁移前后 R8 返回同一批人，既有那条 volunteer 的测试照样绿。
**D1.2 于是变成一次真正的一行改动**：删掉 `compensation` 那半个条件，
名单当场变宽，而那一行就是口径的分水岭。

### 索引与约束：一个字不动，而这是结论不是遗漏

`Position` 现有的 `Index(fields=["ministry", "kind", "is_active"])` 继续成立 ——
R8 和员工名册都按 (ministry, kind) 收窄，`compensation` 只在结果集里分组；
一张几十行的表上再加一个索引是纯成本。
`compensation` 也不加任何 `CheckConstraint`：它没有跨列规则 ——
`board` 加 `paid` 是合法的（带薪理事在别的机构里存在），
`staff` 加 `unpaid` 正是需求原文 ① 那批人。

### 测试（四条，都在 `org/tests.py`）

1. 映射表逐行：把 `FORWARD` 从迁移模块 import 进测试，断言它就是上面那三行。
   ⚠️ 这一条盯的不是数据，是**那张表本身没有被人改过**；
2. 迁移后的库里没有旧值：`Position.objects.filter(kind__in=["employee", "volunteer"])`
   为空，`HistoricalPosition` 上同样断言一次；
3. `Position.Kind` 只剩两档（`len(Position.Kind.choices) == 2`）——
   防的是以后有人偷偷加回第三档，加回去拆轴就白拆了；
4. 新建一个 `Position` 不传 `compensation` → 值是 `unpaid` 而不是 `paid`。

⚠️ 第 2 条有一个必须说破的局限：Django 的测试库是**按迁移建起来的空库**，
所以它证明的是「迁移在空库上没炸」，不是「映射对不对」。
真正的映射验收只有一条路 —— 在一份**有旧数据**的库上跑一次（拿 `seed_demo`
造的那份，验收第一条就是它）。这两件事很容易被当成一件，写下来。

## D1.2 R8 换口径（单独一步，因为它改的是答案）

`events/services.py::ministry_staff_participation()` 里**删掉一行**
（D1.1 留下的那个占位条件，见上）：

```python
position__kind=Position.Kind.STAFF,
position__compensation=Position.Compensation.PAID,     # ← 删掉这一行，就是全部改动
```

⚠️ 同一个 filter 里另外两个条件一个字不动，而且都不是摆设：
`position__ministry=event.ministry`（问的是**办这场活动的**那个 ministry）、
`position__is_active=True`（退休的岗位不算）。
上面那三条 `.active(on=…)` / `.distinct()` 的注释同样一个字不动。

这一步之后 R8 会返回**不同的名单**，而且改之前和改之后都不报错。

### 既有的那条测试要整条反转，这是本轮唯一一条

`events/tests.py::R8Tests::test_a_volunteer_of_the_same_ministry_does_not_appear`
（1257 行附近）断言的正是旧口径。改法不是删，是**反转并改名**：

```
test_a_volunteer_of_the_same_ministry_does_not_appear
    → test_an_unpaid_staff_member_of_the_same_ministry_appears
```

⚠️ 反转的是它的结论，不是它的形状：同样一个人、同样一个岗位、同样一场活动，
断言从「不在名单里」变成「在名单里」。注释里写明它是口径的分水岭 ——
一条被反转过的测试，如果没有人写下它为什么反转，
下一个人只会以为当初写错了。

### 新加的两条测试

1. 同一个 ministry 下建 `paid` / `unpaid` / `stipend` 三个在编人员，
   都参加同一场活动 → 三个人**都在** R8 的结果里（D32 第三节的新口径）；
2. 同一个 ministry 下的理事（`kind=board`）参加同一场活动 → **不在**名单里。

⚠️ 第 2 条不是补漏，是把一个**有意的选择**钉住（2026-08-19 确认）：
R8 问的是「办这场活动的部门里，我们自己的工作力量谁参与了」，
理事不是那个部门的工作力量，而且理事的岗位多半挂在基金会层面
（`Position.ministry` 可空），本来也过不了 ministry 那个条件。
没有这条测试，将来有人发现「理事不在名单上」会当成拆轴时漏改的 bug 顺手改掉 ——
而那是一次静默的口径变更。

### 保留原来那三条坑的测试，一个字不动

`.active(on=活动当天)` 不是 `.active()`；`.active()` 不是 `.serving()`；
`.distinct()` 不是可选的。它们和本步无关，但改这个函数时最容易碰坏 ——
`test_somebody_who_left_before_the_event_does_not_appear` 那一条尤其，
因为 D1.3 的 `default_served_as()` 马上要在**同一个形状**上再踩一次同一个坑。

### R8 的**页面**也要改，而这一格差点被漏掉

改完查询，`events/templates/events/event_report.html` 上那个小标题还写着
「Employees of this ministry who took part」—— 而这份名单现在有三分之二不是 employee。
[D32 第四节](decisions/D32-worker-axes-schedule-and-assignment.md) 那条
「界面上不要管无薪的人叫 employee」正是为这一格写的：
真出争议时，机构自己系统里的措辞就是对方的证据。

三处一起改，缺一处就留一句反例：

| 在哪 | 改成 |
|---|---|
| 小标题 | This ministry's own people who took part (R8) |
| 空名单那句 | 不再出现 employee 这个词（「Nobody who held a post here on the day…」） |
| 底下那行注脚 | 明写这份名单含 paid / unpaid / stipend，**并且明写理事不在里面** |

⚠️ 最后那半句是新加的，理由很具体：**空名单和「你以为该在里面的那个人是理事」
在页面上长得一模一样**。查询里那条有意的排除，如果页面上一个字都不说，
它就只在代码和决策文档里存在 —— 而看这一页的人两样都不会读。

⚠️ 这一步的验收测试**打在页面上而不是打在服务层**：查询对了之后，
还能错的地方就只剩措辞了（`assertNotContains(response, "Employees of this ministry")`）。

### 「身份」那一列不在这一步

[D32 第三节](decisions/D32-worker-axes-schedule-and-assignment.md) 末尾要求 R8 名单加一列身份，
而那个字段 D1.3 才存在。所以这一步只换 filter，
名单上那一列跟着 D1.3 一起做（见下），
⚠️ 写在这里是因为「R8 那一列呢」这个问题**一定**会在 review 时被问一次。

## D1.3 ⭐ 身份轴 `served_as`（[D38](decisions/D38-served-as-volunteer-or-work.md)）

本轮的核心一步。同样是两个 migration，同样一张影子表要一起过。

### 落库的形状

```python
class ServedAs(models.TextChoices):
    VOLUNTEER = "volunteer", "Volunteering"      # ⚠️ 措辞见下
    WORK = "work", "Scheduled work"

class DeclaredBy(models.TextChoices):
    SELF = "self", "Said by the volunteer"
    ADMIN = "admin", "Set by an admin"

served_as = models.CharField(max_length=20, choices=ServedAs.choices, blank=True)
served_as_declared_by = models.CharField(
    max_length=20, choices=DeclaredBy.choices, blank=True)
```

🔴 **两个字段都没有 `default`。** 给 `served_as` 加 `default="volunteer"`
是替没有人核实过的事作证 —— 同 `Participation.checked_in_method` 已经写在模型里的
那条（"A default of ADMIN would back-date a claim onto every historical row"）。
一模一样的形状，抄那条的处理，**并且把那句话的出处写进这两个字段的注释里**：
空值只可能来自下面那一次回填。

⚠️ 考虑过并否决：给这两列加一条「有身份就必须有声明人」的 `CheckConstraint`
（`Q(served_as="") | ~Q(served_as_declared_by="")`）。看起来完全符合
[D9](decisions/D09-rules-in-db-constraints.md)，而且挡的正是 D38 第四节点名的那个坏形状。
**不行** —— 回填出来的行恰恰是 (`volunteer`, 空)：身份从数据里证得出来，
但没有任何人声明过。约束会当场挡住那次迁移。
记在这里，因为这是一个看起来很对、下一个人一定会想加的约束。

### 措辞：这一步不许自己定一版

[D38 第六节](decisions/D38-served-as-volunteer-or-work.md) 是这两档措辞的**唯一落点**，
最终英文措辞还没和基金会过（`goal.md` 待定表第 5 条），先用那一节写下的工作默认值：

```
How were you serving this time?
  ● Volunteering — my own time
  ○ Scheduled work — counts as my work time
```

⚠️ 它出现在报名表单、`/me/participations/`、R8 名单和（D3 之后的）邀请页上，
改一次是四处 —— 定下来写回 D38 第六节，不要在模板里各写一版。
模板里一个汉字都不许有（`core.tests.InterfaceLanguageGuardTests` 盯着）。

### 迁移 ①：schema

```
events/migrations/0013_participation_served_as.py
```

四个 operation：`Participation` 和 `HistoricalParticipation` 各两个 `AddField`。

### 迁移 ②：回填，只填能从数据里证出来的那一半

```
events/migrations/0014_backfill_served_as.py
```

| 旧行的情况 | 回填 `served_as` | 回填 `declared_by` |
|---|---|---|
| 活动当天没有在职的 `kind=staff` 任职 | `volunteer` —— 证得出来：他当时没有在编路径 | 不填 |
| 活动当天有在职的 `kind=staff` 任职 | 不填 —— 这才是真的不知道 | 不填 |

⚠️ 判据里那半句「`kind=staff`」是 2026-08-19 定的，
它消掉了 D38 自己的一处不一致：第九节的回填表只说「有没有在职 `Assignment`」，
而第五节说理事根本不该被问这个问题。按第九节的字面，一个理事的旧行会进
「身份未记录」；按这里的判据，它回填成 `volunteer`，和新行的规则一字不差。
⚠️ 结论：**回填和 `default_served_as()` 用的是同一个判据**，
所以那个判据只写一次（见下），迁移 import 它。

⚠️ 回填一个字都不写 `declared_by`（D38 第九节）：那批行没有人声明过任何事，
写 `admin` 是在说「某个管理员判断过」，写 `self` 更糟。

写法（不许一行一个查询，几千行会跑到天亮）：

```python
# ① 一次查出所有 staff 任职的 (contact_id, start_date, end_date)
# ② 一次查出所有 Participation 的 (pk, contact_id, event.start_time)
# ③ 在 Python 里判：local_date_of(start_time) 落在那个人的任一区间里吗
# ④ 判不出在编的那一批 → bulk_update(served_as="volunteer")，一次
```

⚠️ 第 ③ 步的日期只能用 `core.timeutils.local_date_of()`，
不许写 `start_time.date()` —— 那是 UTC 的那一天，
而 `core.tests.TimeSourceGuardTests` 扫**所有** `.py` 文件，迁移不例外。
这不是守卫多事：R8 当年就是在这一行上错了一天（[D16](decisions/D16-time-and-dates.md)）。

### 唯一的写入处，和它的两个函数

```python
events/services.py::default_served_as(contact, event) -> (value, ask)
    # ⚠️ 一对，不是一个值加一个 None —— 见下
events/services.py::set_served_as(participation, value, *, declared_by)
    # 唯一写这两列的地方（守卫七盯着）
```

#### ⚠️ 返回值是一对，`-> str | None` 那个签名是错的（2026-08-20 实现时发现）

D38 第五节那张表**有两列**，而且两列的答案不一样：外部志愿者「默认 `volunteer`」
**并且**「问题完全不出现」。写成 `-> str | None`、用 `None` 表示「不问」，
就把不问的那批人的**值**一起丢掉了。代价有两条，都不报错：

- R6 的志愿者工时口径是 `served_as=volunteer`（D1.4），而外部志愿者是这个数的
  绝大部分 —— 他们会**一个不剩地掉出去**；
- 回填迁移正好把这批人写成 `volunteer`（数据证得出来）。于是新写进去的行
  **比它旁边那些历史行还要空**。

改成返回 `(value, ask)`：表单读 `ask` 决定画不画，服务层读 `value` 决定写什么。
⚠️ D38 第五节真正要的那条「必须是同一个函数的返回值」**没有被破坏** ——
理事那一格和离职那一天仍然只有一处判断。

#### `declared_by` 只在真的问过的时候才写

顺着上面那条推下来的一条口径：**「不问」的人照样落值，但 `declared_by` 留空。**

`declared_by` 的意思因此变得很干净：**有人回答过这个问题**。
外部志愿者没被问过，所以没有人替他声明 —— 这和回填迁移
「一个字都不写 `declared_by`」画的是同一条线，理由也一样：
「某人说过」和「数据证得出来」是两种不同的事实，只有前一种是证据。

⚠️ 「默认值是什么」和「问不问」是**同一个函数的一个返回值**，
不许表单自己判一次、服务层再判一次 —— 两处判断会在理事那一格、
在离职那一天各走散一次，而走散的形状是「表单没问、服务层写了个默认值」，不报错。

`default_served_as()` 本轮的全部内容：

```python
def default_served_as(contact, event):
    on = local_date_of(event.start_time)          # ⚠️ 不是 event.start_time.date()
    on_the_books = Assignment.objects.active(on=on).filter(
        contact=contact,
        position__kind=Position.Kind.STAFF,       # 理事因此不被问
        position__is_active=True,
    )
    if not on_the_books.exists():
        return None                               # 外部志愿者 / 理事 / 那天不在编的人
    return Participation.ServedAs.VOLUNTEER       # 自己报名的，推定是爱心
```

- ⚠️ `active(on=活动那一天)`，不是 `active()`。按今天判断两个方向都错 ——
  去年离职的人今天报名会被问，今天入职的人报去年的活动不会被问。
  这是 R8 那个 `on=` 老坑第二次出现在同一个形状上，
  而 D1.2 特意留下的那条测试就是它的前哨；
- ⚠️ `position__is_active=True` 和 R8 同款：退休的岗位不算在编路径；
- ⚠️ [D38 第五节](decisions/D38-served-as-volunteer-or-work.md) 那张表有四行，
  这个函数本轮只实现得出两行 —— 「被指派 → `work`」那一行**没有输入**，
  因为指派这条路 D3 才有。**不要**现在写一个读不出「被指派」的分支，
  也不要预留一个没有调用者的参数；docstring 里写明第四条规则在 D3.2 补，
  而且**必须补在这个函数里**（D3.2 那一步的清单里也记一笔）。

`set_served_as()` 的全部内容：

```python
def set_served_as(participation, value, *, declared_by):
    if value is None:                     # 不该被问的人，一个字都不写
        return participation
    participation.served_as = value
    participation.served_as_declared_by = declared_by
    participation.save(update_fields=["served_as", "served_as_declared_by", "updated_at"])
    return participation
```

⚠️ `update_fields` 里带 `updated_at`，照 `mark_absent()` 现有的写法 ——
漏掉它这一行的更新时间就停在上一次，而 simple-history 里那条新记录的时间是对的，
两个时间从此不一样。
⚠️ 这个函数**不做任何推断**：推断在 `default_served_as()` 里，这里只落值。
一个函数既推断又落值，就是把「默认规则」复制了一份。

### 报名路径怎么接：`sign_up()` 多收一个关键字，内部落值

```python
def sign_up(*, contact, event_role, consent=None, served_as=None):
    ...
    participation.save()
    set_served_as(participation, resolved, declared_by=DeclaredBy.SELF)
```

⚠️ **不要**在视图里写「先 `sign_up()`、再 `set_served_as()`」——
那是「两个动作必须配对」，本项目为这个病判过一次
（[D35 第二节](decisions/D35-event-assignment-path.md) 说的正是它），
而漏掉第二句的表现是一行没有身份的报名，不报错。

`resolved` 由服务层自己算，**不信表单送上来的值**：

```python
default = default_served_as(contact, event_role.event)
resolved = None if default is None else (served_as or default)
```

两个方向都要挡住，而且都会真的发生：

- 外部志愿者的表单里根本没有这个 name，但**直接 POST 一个 `served_as=work` 上来**
  是几秒钟的事 —— `default is None` 于是一个字都不写；
- 表单画了这个问题、人没选（浏览器禁用 JS、或者被绕过）→ 落默认值，
  不留空。留空的行会掉进「身份未记录」那一格，而那一格的意思是
  「这一行早于本轮，没人问过」，不是「有人没答」。

⚠️ 两句写在同一个 `transaction.atomic()` 里 —— 报名成立而身份没落地，
比两件事都没发生更糟。

### 表单

`events/forms.py::SignUpForm` 加一个字段，落点只有这一个类
（整页报名和右面板报名共用同一个 form 和同一份 `_event_signup_body.html`）：

```python
served_as = forms.ChoiceField(
    choices=Participation.ServedAs.choices,
    widget=forms.RadioSelect, required=False,
)
```

`__init__` 里：

```python
default = default_served_as(contact, event)          # ⚠️ 同一个函数，不重判
if default is None:
    del self.fields["served_as"]                     # ⚠️ 删掉，不是 HiddenInput
else:
    self.fields["served_as"].initial = default       # 预选，不是预填
    self.fields["served_as"].required = True
```

- ⚠️ 用 `del` 而不是 `HiddenInput`（consent 那几个字段用的是后者）：
  hidden 的字段会把值提交上来，而这一个字段外部志愿者**根本不该有**。
  服务层那一道复核仍然留着 —— 表单决定画什么，服务层决定写什么；
- ⚠️ 「预选而不是预填」（[D38 第五节](decisions/D38-served-as-volunteer-or-work.md)）：
  两个选项都画出来，其中一个已选中。藏起来的默认值等于替人做了声明；
- ⚠️ 两档的排版必须中性 —— 不许把「志愿服务」那一档做得更醒目、更值得表扬
  （[D38 第六节](decisions/D38-served-as-volunteer-or-work.md) 第 3 条）。
  这条**没有测试盯得住，只能靠 review**，写进这一步的 PR 检查项。

### admin：两列 readonly，而这挡住的不是错值

```python
events/admin.py::ParticipationAdmin.readonly_fields = [
    "served_as", "served_as_declared_by",
]
```

⚠️ Django admin 是一条**不写代码就存在**的写入路径，守卫七 grep 的是代码里的赋值，
admin 表单它一个字都拦不住。从那里改出来的行 `declared_by` 是空的 ——
报表把它当「身份未记录」，FLSA 提示读不到是谁说的，而页面上一切正常。
⚠️ 顺带把 `checked_in_method` 一起设成 readonly：同一条规矩用在第二个事实上，
[D28 第四节](decisions/D28-qr-checkin.md) 立那个字段时就该有这一行。

### 更正身份的入口：报名管理页，一次一行

⚠️ 这一格不是可选的。两列 readonly 之后，如果这一批不做页面动作，
**全系统就没有任何一条路径能更正身份** —— 而那正是本项目栽过三次的病
（服务层写好了、没有页面）。2026-08-19 定的落点：

| 五列 | 内容 |
|---|---|
| 页面 | 报名管理页 `/events/<pk>/registrations/` 上每一行一个动作 |
| URL | `events/registrations/<int:pk>/served-as/`，POST only |
| 谁能进 | 该活动 ministry 的 admin（`can_manage_event`） |
| 从哪里点进去 | 报名管理页每一行 |
| 批次 | D1 |

三条实现要点：

1. ⚠️ 那一页现在是**只读**的，权限是 `can_view_event_records`（foundation tier
   看得到任何活动）。加了写动作之后按考勤页现成的形状分成两问：
   读用 `can_view_event_records`，**POST 用 `can_manage_event`**。
   不画按钮是界面，界面挡不住任何人 —— 从别处 POST 过来的表单形状一模一样；
2. 调 `set_served_as(participation, value, declared_by=ADMIN)`，
   HTMX 换回那一行，照 `_attendance_row_swap.html` 现成的做法；
3. 🔴 **不做批量改身份的入口**（[D38 第四节](decisions/D38-served-as-volunteer-or-work.md)）。
   一次改一行、留痕、本人看得见 —— 批量按钮会让这个字段在一次点击里
   失去全部证据价值。

### 本人看得见：`/me/participations/` 每一行

D38 第四节那条「看得见不可交易」在这一批的落点是**我的报名**那一页
（My Schedule 要到 D2a 才有）。每一行写两件事：

```
Volunteering — my own time          You said this
Scheduled work — counts as my work  Set by an admin
```

⚠️ 验收清单里原来写的是「我的资料」，改成这一页 —— 身份是挂在**报名行**上的，
`/me/profile/` 上没有一行报名可以挂它。

### R8 名单加「身份」一列

D1.2 欠的那一列在这一步补：`ministry_staff_participation()` 的结果已经带着
`Participation`，模板直接读 `served_as` 和 `served_as_declared_by`，
不加查询、不加服务函数。空值显示「Not recorded」，⚠️ 不显示成任何一档。

### 守卫七这一步就要落地

「身份只有一处写入」那条守卫（见[本轮新增的守卫测试](#本轮新增的守卫测试)）
和字段同期，不等 D3：除 `events/services.py::set_served_as()` 外
没有别处赋值 `served_as`，外加一条断言 ——
`ParticipationAdmin.readonly_fields` 里有这两个字段（断言，不是 grep）。
⚠️ 白名单里要写上那两个迁移文件，否则守卫会挡住回填自己。
按项目惯例做一次双向验证：故意在视图里写一行赋值，确认它真的打红。

### 测试（九条，都是不报错的那一类）

1. 外部志愿者报名 → 表单上**没有**那个问题，落库的值是 `volunteer`、
   `declared_by=self`；
2. 理事（`kind=board`）报名 → 同样没有那个问题（D38 第五节那一格）；
3. 在编 `staff` 报名 → 有问题、预选 `volunteer`、`declared_by=self`；
4. 去年离职的人今天报名 → 没有那个问题（`active(on=活动日期)` 那一条）；
5. 外部志愿者**直接 POST** `served_as=work` → 库里那两列仍然是空
   （服务层那道复核，⚠️ 这一条没有 UI 能测出来）；
6. admin 从报名管理页改一个人的身份 → `declared_by` 变 `admin`、
   simple-history 里查得到，且本人在 `/me/participations/` 上看到的是新值加
   「Set by an admin」；
7. `ParticipationAdmin.readonly_fields` 里有这两个字段；
8. 回填后：活动当天有在职 `staff` 任职的旧行 `served_as` 为空，
   且报表把它算进「身份未记录」而**不是**任何一边；
9. 回填后：一个**理事**的旧行 `served_as` 是 `volunteer`
   （2026-08-19 那个判据的分水岭，⚠️ 按 D38 第九节的字面它会是空的）。

## D1.4 R6 / R7 换成一个 filter（第三处静默语义变更）

「志愿者工时」= `Participation.hours` where `served_as=volunteer`。

⚠️ **同时删掉初版计划里那套排除规则**（排除本 ministry 的 `paid` / `stipend`、
跨 ministry 不排除、`stipend` 归堆）—— 三条规则一起删，换成一个 filter。
理由见 [D38 第二节](decisions/D38-served-as-volunteer-or-work.md)：那是代理变量，
两头都错，还会让一批小时**从两个账本里同时消失**。

**落点是两处，不是一处**：

| 在哪 | 改什么 |
|---|---|
| `events/services.py::event_summary()` | R6 = `total_hours`、R7 = 每个 role 的 `hours_total`。⚠️ **两个 `Sum` 要在同一次改动里都加上 `filter=Q(served_as=volunteer)`** —— 那个函数的注释写着"总数和分解从同一批行来，所以不会漂"，只改一个它当场就漂了 |
| D27 报表 | 新加一个「志愿者工时」，⚠️ **既有的 `hours`（已记录工时）保持原义不动** —— 它和旁边的 `hours_records` / `hours_missing` 是一组，回答的是"这个总数建立在多少条记录上"。换成志愿口径，分子分母说的就不是同一件事了 |

**报表上的写法（⚠️ 不是并排三个平级的数）**：

```
志愿者工时  1,240 小时（其中在编人员 180 小时）      身份未记录  36 小时
```

「在编人员的志愿投入」**是**「志愿者工时」的子集（R6 的 filter 含在编人员，
[D38 第二节](decisions/D38-served-as-volunteer-or-work.md) 整节讲的就是为什么不能把他们排除掉）。
三个数并排画，第一反应就是加 —— 而这一轮花了整条 D36 防这件事。
「身份未记录」才是真正平级的第三个数，它必须画出来
（同 D27 「未成年无同意记录为 0 也要画」）。

**测试**：同一场活动里一个外部志愿者、一个本 ministry 带薪员工（`served_as=volunteer`）、
一个本 ministry 带薪员工（`served_as=work`），断言前两个进 R6、第三个不进。
⚠️ 这条测试就是初版口径和现在口径的分水岭 —— 初版会把第二个人也排除掉。

## D1.5 FLSA 提示（[D38 第八节](decisions/D38-served-as-volunteer-or-work.md)）

`events/services.py::flsa_flags(event)` 一个函数，两条提示，**都不拦截**：

- `served_as=volunteer` + 当天在**该 ministry** 持 `paid` / `stipend` 编制；
- `served_as=volunteer` + 活动时段落在他自己的 `Shift` 里（**D2a 之后才生效**，
  先写好、先测好，班表还没有时它返回空）。

⚠️ 提示里要显示 `declared_by` —— 「这是他自己填的」和「这是管理员填的」
在 FLSA 上不是一回事。

## D1.6 HRIS 字段（[D37](decisions/D37-hris-fields-and-credentials.md)）

一次 migration，**已经按第二节的判据砍过一轮**，剩下的每个都写得出读者：

```python
Position    + flsa_status   = exempt | non_exempt | not_applicable
            + headcount     PositiveIntegerField(null=True)      # 编制人数，≠ 在任人数

Assignment  + fte                    DecimalField(3, 2, null=True)   # 0.50 = 半职
            + hours_tracking         = scheduled | agreed | not_tracked
            + agreed_hours_per_week  DecimalField(4, 1, null=True)

Contact     + service_start_date  DateField(null=True)
            + employee_number     CharField(blank=True)
```

⚠️ **不做**（[D37 第二节](decisions/D37-hris-fields-and-credentials.md)）：
`location` · `work_arrangement` · `end_reason` + `EndReason` 字典表 · `is_rehirable`。
砍掉的连带好处：**少一次字典表种子迁移**
（[C0.2.1 的教训](03-roadmap.md#c021--给字典表加种子迁移打红了-40-个测试)：会打红一批既有测试）。

三件同期的事：

1. **删掉 `Assignment.clean()` 里那条 `employment_type` 规则**，
   连同它的测试一起删，并在 `clean()` 的 docstring 里写明为什么删；
2. `employee_number` 加 `UniqueConstraint(Lower("employee_number"), condition=~Q(employee_number=""))`。
   ⚠️ 不能靠 `nulls_distinct` —— 它是 `CharField(blank=True)`，存空串不是 NULL；
3. `hours_tracking` **默认 `scheduled`，并且要显示在 D1.9 的员工名册上** ——
   一个只有 admin 看得见、从不出现在页面上的字段，没有人会去维护它。

### `headcount` 要的是新增 `understaffed()`，不是改 `vacant()`

```python
PositionQuerySet.understaffed(on=None)   # 在任人数 < headcount；headcount 为空 → 不在结果里
PositionQuerySet.vacant(on=None)         # ⚠️ 一个字不动
```

⚠️ 改 `vacant()` 的定义会打破那个 queryset 自己写下的不变量
（「vacant / occupied / retired **划分**整张表」）——
编制 3 人在任 1 人的岗位会**同时**是 vacant 和 occupied，
而 `org/admin.py::StaffingFilter` 是三选一的过滤器，同一行会在两个选项下各出现一次。
新增一个方法则零静默变更，而且 `understaffed` 这个名字
和 `EventRoleQuerySet.understaffed()` **同名同形同语义**（要的比有的多）。

**配一条三格测试**：满编 / 部分填满（`understaffed` 命中、`vacant` 不命中、
`occupied` 命中，⚠️ **三态划分仍然成立**）/ 一个人都没有。

**每个新字段本轮的落点（⚠️ 填不出这一列的字段就该推迟）**：

| 字段 | 出现在哪 |
|---|---|
| `flsa_status` | 员工名册上的**矛盾提示**：`non_exempt` + `hours_tracking=not_tracked`（非豁免员工的工时是法律要求记的）。这是它唯一的读者 |
| `headcount` | `understaffed()` → ministry 详情页 · D27 报表 |
| `fte` · `hours_tracking` · `agreed_hours_per_week` | 员工名册各一列（`agreed` 而值为空时标「约定值未填写」） |
| `service_start_date` | 员工名册的默认排序 + 「服务年数」列 |
| `employee_number` | 员工名册一列 + 搜索框 |
| `EmploymentType`（既有） | ⚠️ 删掉 `clean()` 那条规则之后它没有任何 branch 也没有页面 —— **本轮把它显示在岗位详情和名册上，或者下一轮连它一起推迟**（[D37 第一节](decisions/D37-hris-fields-and-credentials.md)） |

## D1.7 证照：`CredentialType` + `Credential`

```python
CredentialType(code, name, is_active, default_valid_days=null)
Credential(contact → PROTECT, credential_type → PROTECT,
           issued_on, expires_on=null, reference="", notes="")
```

⚠️ **不吃掉 `BackgroundCheck`** —— 把这句话写死在 `Credential` 的 docstring 里，
因为这正是以后最容易被顺手合并的地方（[D37 第三节](decisions/D37-hris-fields-and-credentials.md)）。

queryset：`expiring_within(days)` / `expired(on=None)`，时间口径走 [D16](decisions/D16-time-and-dates.md)。

**读者两处，写下来才算数**：员工名册上「谁的证下个月过期」那条提示 · 我的资料。
`default_valid_days` 的读者只有一个：**录入表单按它预填 `expires_on`**，人可以改
（⚠️ 这里预填是对的 —— 有效期是发证机构定的客观事实，不是当事人的声明。
和 `served_as` 那条「预选不预填」不冲突，因为那一个是声明）。

⚠️ **这一步过不了 [`phase-d.md` 第二节](phase-d.md#二判据这一阶段的东西该不该做) 判据的第 1 条**
（证照和第二批需求那三句话没有关系），[D37 第三节](decisions/D37-hris-fields-and-credentials.md)
把它记成了一次**有意的破例**。
**所以：D1 这一批如果时间紧，它是第一个该砍的** —— 砍掉之后
没有任何一句需求原文答不出来，而每一个页面都不是这样。

## D1.8 `org/services.py` 的名册与统计

```python
ministry_roster(ministry, *, on=None)     # Leaders / Staff(paid) / Staff(unpaid) / Board / 空缺
staff_directory(ministries, *, on=None)   # 收筛过的 queryset，不收 id
ministry_headcounts(ministries, *, on=None)
```

⚠️ **这三个函数里有两处会长出第二份口径，动手前先接上**：

- `ministry_headcounts()` **必须建立在既有的
  `PositionQuerySet.with_headcounts()` 之上**（它已经在 SQL 里算 `holder_count`
  / `serving_count` 了）。另写一遍 `Count` 就是"在任人数"有两个定义，
  而 `understaffed()` 也在读同一个数；
- `ministry_roster()` 和 `staff_directory()` 都在回答"这个 ministry 里有谁"，
  只是一个分组一个平铺。**分组那一版从平铺那一版的结果上分**，
  不要两条各写一个 filter —— 两条 filter 迟早在"离职当天算不算"上分家。

⚠️ **`staff_directory()` 收的是已经按权限收窄过的 ministry queryset，不是 id 列表。**
理由和 [D27 的唯一不变量](decisions/D27-ministry-report.md)一字不差：
收 id 的写法需要在函数里再判一次权限，而那一处判断迟早和页面那一处走散。

## D1.9 五个页面

照 [`phase-d.md` 的页面与入口总表](phase-d.md#-四页面与入口总表)做，**五列缺一不可**。
本批交付：Ministry 详情 · 组织架构图 · 员工名册 · 岗位详情 · 我的资料扩展。

⚠️ **「我的邀请」不在本批**（初版把它作为占位排在 D1）。
一个在 D3 之前永远空着的页面，既不能验收、也不能演示，
而顶栏那个红点会一直是 0 —— 它跟着 D3.2 一起做。

两条要点：

- 组织架构图 ⚠️ **直接 `from org.services import build_org_tree`**，不要自己递归 ——
  `core/tests.py` 有守卫盯着。**它到今天为止没有任何真实调用者**（只有测试打它），
  这一步是第一个，也是 [C4 第 1、2 条](03-roadmap.md#c4--运营功能试点期间并行)欠着的那一步；
- 权限照 [`phase-d.md` 第五节那张表](phase-d.md#五权限第一次真正吃紧)，
  ⚠️ **写在 `org/permissions.py`，模板里只问一个布尔**。

---

# D2a · 班表结构与自助（[D33](decisions/D33-work-schedule.md)）

## D2a.1 `WorkPattern`

```python
WorkPattern(assignment → CASCADE, weekday 0..6,
            start_time, end_time, start_date, end_date=null,
            batch → PatternBatch (null, SET_NULL))    # ← D40，只有批量建的才有

PatternBatch(id=UUID, ministry → PROTECT, created_by → Contact PROTECT,
             created, undone_at=null, undone_by → Contact null)
```

⚠️ `PatternBatch` 只为[整批撤销](decisions/D40-undo-a-pattern-batch.md)存在，
**对任何报表都没有贡献** —— 判据第 2 条靠的是两个页面（确认屏、批次列表），
不是任何一条统计。写明免得以后有人把它当数据源。
⚠️ 单条建的模板 `batch` 为空、撤不了，这是**有意的**（D40 第四节）：
撤销一条本来就只有一步。

约束：`end_time > start_time`；`end_date >= start_date`；
`UniqueConstraint(assignment, weekday, start_time, start_date, nulls_distinct=False)`
（⚠️ `nulls_distinct=False` 是 A7 的老教训，`start_date` 可空）。

**跨午夜的班次先不支持**，`end_time > start_time` 直接挡掉。

## D2a.2 `Shift`

```python
Shift(assignment → PROTECT, date, start_time, end_time,
      status = scheduled | worked | on_leave | absent,   # ⚠️ 四档，没有 cancelled
      actual_start=null, actual_end=null,          # 只在偏离时填 —— 例外记录法
      source = generated | manual | edited,
      generated_from → WorkPattern (null, PROTECT),   # ⚠️ D40 改，原来是 SET_NULL
      notes="")
history = HistoricalRecords()                      # ⚠️ 见下
```

`assignment` 用 `PROTECT` 而不是 `CASCADE`：班次行是工时记录，删任职不能带走它。

⚠️ **`generated_from` 也是 `PROTECT`**（2026-08-15 改，原来是 `SET_NULL`）：
一行 `source=generated` 而 `generated_from=NULL`、日期在未来的班次是**孤儿** ——
生成器按 `generated_from=pattern` 过滤，**再也没有任何东西会去收它**，
它会一直站在周视图上直到有人手工删。改成 `PROTECT` 之后
「删掉一条还有班次的模板」在数据库层面不可能（[D9](decisions/D09-rules-in-db-constraints.md)），
理由全文见 [D40 第三节](decisions/D40-undo-a-pattern-batch.md)。

⚠️ **`cancelled` 那一档删掉**（初版有）：说不出谁会写它 ——
生成器写 `scheduled`、例会那一屏写 `worked` / `absent`、请假写 `on_leave`、
任职结束是**删行**。留着只会给 `staff_hours()` 多一个答不出来的分支。

⚠️ **`actual_start` / `actual_end` 两头都要有落点，否则这两列没有读者**：
写它的只有 D2a.8 那个 `edit_shift()`，读它的只有 D2b.3 的
**实到时长 = `COALESCE(实际, 排班)`**。

约束：`UniqueConstraint(assignment, date, start_time)`；`end_time > start_time`；
索引 `(assignment, date)` 和 `(date, status)`。
⚠️ **那个唯一约束不是幂等的依据** —— `start_time` 是人改得动的列，见 D2a.3。

⚠️ **simple-history 是必须的**（初版漏了）：[D33 第七节](decisions/D33-work-schedule.md)
刚说完「一行 `Shift` 就是那份法律认的例外记录表」，
而全库唯一那张法律记录不能是唯一没有历史的重要表。
**而生成器必须用 `bulk_create`**（不触发 history），否则每周 cron 往历史表灌一遍全量 ——
这句话写进生成器的注释里。

**日期时间的形状**：`date` + `time`，**不是 aware datetime**。
这是对 [D16](decisions/D16-time-and-dates.md) 的一次有意例外，
理由（墙钟时间、DST）写进模型 docstring —— 否则将来有人"顺手统一一下"，
夏令时那两天会静默错一小时。

## D2a.3 生成器 —— 纯函数先行

照 [C6.1 的做法](03-roadmap.md#c61-eventstokenspy--纯函数先写这一层)：无 DB、无 request。

```python
occurrences(pattern, since, until) -> list[date]     # 纯函数，先写这一层
generate_shifts(assignment, until)                   # 落库，幂等
```

⚠️ **四条规则，缺一条都会静默出错**：

1. **只动 `date > today` 且 `source == generated` 的行**（本轮最重要的一条）；
2. ⭐ **「动」= 先删再插，两句话**：

   ```python
   _drop_generated_after(Shift.objects.filter(generated_from=pattern), today)  # ① 先收自己造的
   Shift.objects.bulk_create(rows, ignore_conflicts=True)                      # ② 再造
   ```

   ⚠️ 那两个过滤条件（`date__gt` + `source=GENERATED`）**抽成一个函数，全仓只此一处**：

   ```python
   org/services.py::_drop_generated_after(shifts, after)
       # shifts.filter(date__gt=after, source=GENERATED).delete()
       # 三个调用方：生成器 · 任职结束清理（D2a.4）· 整批撤销（D2a.8b）
   ```

   这比给守卫一的白名单再加两条更硬（[D40 的不变量](decisions/D40-undo-a-pattern-batch.md)）——
   守卫从此盯的是「这两个条件只有一处」，而不是「这几个地方可以删班次」，
   ⚠️ 而白名单正是[守卫最容易被悄悄放宽的地方](decisions/D36-two-hour-ledgers.md)；

   ⚠️ `ignore_conflicts` **只插不删**。漏掉①的表现是：把例会从周二改成周三之后，
   **旧模板生成的那一批未来班次一行都不会消失**，新行插在旁边 ——
   一个人一天两个班，而下面那条"过去的行没动"的测试**全绿**；
3. 落在 `Leave` 里的日期，**直接生成 `status=on_leave` 的行**
   （[D33 第四节 ①](decisions/D33-work-schedule.md)）。
   ⚠️ **不是"跳过不生成"**（初版）—— 跳过会让同一次请假有两种形状：
   已生成过的日子是 N 行 `on_leave`，没生成到的日子是**没有行**，
   于是「请假」那个数跟着录入时机走，销假时两边也不一样；
4. **幂等的键是 `(assignment, date, generated_from)`，不是那个唯一约束**。
   `UniqueConstraint(assignment, date, start_time)` 里的 `start_time`
   正是「改单个班次」会改的列：有人把 19:00 改成 20:00 并留下 `source=edited`，
   下次生成 19:00 那一行**不冲突**，那天就变成两行。
   ⚠️ 唯一约束仍然要留着当兜底（cron 重跑和手工补跑同时发生时的竞态），
   但**判"这天这条模板派生过没有"要按上面那三列**，和规则②的 `delete()` 同源。

⚠️ 「今天」只有一种写法，走 [D16](decisions/D16-time-and-dates.md)。
`datetime.date.today()` 会在生产的 UTC 下**静默错一天**，而错的正好是边界那一天。

**这一步的测试是本轮最重要的一组，四条，缺哪条就放过哪条**：

1. 造一批过去的班次（含一行 `source=edited`），改模板重新生成 ——
   断言过去的行**一个字节都没动**，且未来那行 `source=edited` 的也没动；
2. 改模板之后，未来按旧模板生成的行**一行都不剩**
   （断言那一天只有一行班次，时间是新的）。这条测试就是规则②的分水岭，
   初版的实现会让它红；
3. 把一行未来的班次改时间并标 `source=edited`，重新生成 ——
   断言那一天**只有那一行**（规则④）；
4. 造一段 `Leave` 再生成 —— 断言区间里生成出来的行是
   **`status=on_leave`**，不是没有行，也不是 `scheduled`（规则③）。

## D2a.4 任职结束时清理未来班次

`Assignment.end_date` 落地时（服务层一处）：

- `_drop_generated_after(Shift.objects.filter(assignment=a), end_date)`
  —— ⚠️ **调 D2a.3 那个函数，不要自己再写一次那两个条件**；
- `source` 是 `manual` / `edited` 的**不删，只提示** —— 那是有人手工碰过的
  （那个函数本来就不碰它们）。

⚠️ 不做的话，离职的人会出现在下个月的周视图上，并计入未确认工时，且不报错。

## D2a.5 生成 cron

`org/management/commands/generate_shifts.py` + `render.yaml` 第三个 cron，
**滚动窗口 13 周**（一个季度，[D33 第三节](decisions/D33-work-schedule.md)）。

⚠️ **`render.yaml` 开头那段警告从此适用于三个 cron**：
「两个 cron，不是一个」漏掉的表现是**没有表现**。
本步同时把那段注释里的"两个"改成"三个"，并把 R2 那七项环境变量
逐字复制进新 cron —— 那七项在现有两个 cron 里就是逐字重复的，
理由写在文件里，不要试图去重。

`schedule` 用 UTC（⚠️ Render 的 cron 一律 UTC，现有两个 cron 的注释里已经踩过一次）。

## D2a.6 ⭐ My Schedule（需求原文 ③ 的落点）

形状：一页、两条泳道、三个数、**永不求和**。
形状、四条设计规矩和那段线框图在
[`phase-d.md`](phase-d.md#my-schedule-的形状)，**照着做**。

```python
org/services.py::my_timeline(contact, start, end)
    # 读两个源：Shift（本人的在职 assignment）+ Participation
    # 每一行带 lane（work / volunteer）和 declared_by
    # ⚠️ 返回的是行，不是总数
```

**三个数从哪里来 —— 这一格初版是自相矛盾的**：它一边写着"求和是
`staff_hours()` 的事（D2b）"，一边要求 D2a 就显示「排班工作时间」。
D2a 没有 `staff_hours()`，那句话的结果是**有人在视图里就地 sum 一次**，
然后 D2b 再写第二次。

**处置：把 `staff_hours()` 的 `scheduled` 那一档提前到 D2a**（就三行 SQL），
口径从第一天起就只有一处。`agreed` / `not_tracked` 两档留给 D2b。

⚠️ 三个数**必须是同一个时间窗**（本月），窗口写在它们上面；
「今年累计志愿服务」单独一行、自己写明年份
（[`phase-d.md` 的线框图](phase-d.md#my-schedule-的形状)）。
初版是「本月 / 今年 / 上周」三个数三个窗并排 —— 读的人只会去比大小，
而它们根本不可比。

⚠️ 任何一格算不出来时显示「—」，**不显示 0**
（[D36](decisions/D36-two-hour-ledgers.md) 的不变量，本轮通用）。

## D2a.7 `.ics` 订阅

`/me/schedule.ics`，带不可猜且**可撤销**的 token。拼字符串，**不引库**。

⚠️ 一个泄漏出去的日历链接会一直泄漏下去 —— 所以"撤销并换一个"这个按钮
和订阅按钮**同期落地**，不是以后再补。

⚠️ **「可撤销」意味着它不能照抄 D28 的做法。** `events/tokens.py` 的 token 是
`salted_hmac(SECRET_KEY, …)` 的纯函数（那个模块的第一句就是"没有数据库"），
**纯签名撤销不了** —— 撤销它只能换 `SECRET_KEY`，而那会同时废掉签到、
会话和密码重置。所以要多一列：

```python
Contact.calendar_token = CharField(blank=True)   # 随机串；「撤销并换一个」= 重新生成
```

⚠️ 它是本轮**唯一一个不在 [D37](decisions/D37-hris-fields-and-credentials.md)
字段表里的新列**，因为它不是 HRIS 字段，是这个端点自带的成本。

### ⚠️ 它输出**两条泳道**，不是只有班次（2026-08-15 改）

初版这个端点只拼 `Shift`。**那和它自己的论证对不上** ——
建它的理由是「排班要真的被人看到，得进他自己的日历」，
而 My Schedule 上是两条泳道，日历里只有一条。

⚠️ 少掉的那条恰恰是**更需要进日历的**：志愿服务是自己选的、一次性的、
没有周模式可以记住的，**最容易忘的就是它**。

做法是同一个拼字符串的函数多收一个 queryset（未取消的 `Participation` → 它的 `Event`），
零新结构。⚠️ 但 `UID` 的前缀必须分开：

```
shift-<pk>@<域名>            班次
participation-<pk>@<域名>    活动
```

不分开的话，两张表的 pk 撞上就是同一个事件 —— 就是下面第 1 条那个重影问题，
只不过这次它每天都会发生，而不是刷新时才发生。

另外三件，省一件都会在手机上出问题：

1. **`UID` 稳定**（见上面那两个前缀）—— 不稳定的话，日历每次刷新都把同一个
   班次当成新事件，一周之后是一堆重影；
2. **时间用 floating**（不带 `TZID`、不带 `Z`）—— 和
   [D33 第二节](decisions/D33-work-schedule.md) 选墙钟时间同一个理由，
   而带 `TZID` 就得连 `VTIMEZONE` 一起手写，那和"不引库"是两码事。
   ⚠️ `Event` 那一条是 aware datetime（[D16](decisions/D16-time-and-dates.md)），
   拼进去之前要先**按机构时区落成墙钟时刻** —— 两条泳道进的是同一个文件，
   一半带时区一半不带，手机上会错一整个时区；
3. **带时间窗**（前 4 周 + 后 13 周）。「不许有列出全部班次的入口」这条
   对它一样成立 —— 它恰恰是最容易被写成"把这个人所有班次拼出来"的那一个。
   ⚠️ 窗口对两条泳道**同时**生效。

## D2a.8 班表管理页面

周班表 `/org/schedule/` · 班表模板编辑 · 某天出勤页（D2b 才有确认按钮）
· **批量建例会模板**。

⚠️ **必须带时间窗**，不能有"列出全部班次"的入口（[D33 代价 1](decisions/D33-work-schedule.md)）。
⚠️ 改模板（影响未来）和改单个班次（只影响那一天）是**两个不同的按钮**，
页面上要说清楚哪个是哪个。

**改单个班次也只有一处实现**：

```python
org/services.py::edit_shift(shift, **changes)
    # 写时间 / status / actual_* / notes
    # ⚠️ 并且把 source 从 generated 翻成 edited —— 这一句是它存在的理由
```

⚠️ 不翻 `source` 的表现：那一行在下次生成时仍然算生成器造的，
被 D2a.3 规则②那句 `delete()` **删掉** ——
而 [D33](decisions/D33-work-schedule.md) 明写「人造的东西只有人能收」。
页面上不许直接 `shift.save()`，这是本轮第二条守卫。

**⭐ 批量建例会模板** `/org/ministries/<pk>/pattern/`：
勾一批在职任职 + 填一个周模式 → 一次 `bulk_create`。

⚠️ 这不是锦上添花，是**需求原文 ① 的第一道门槛**：那个 ministry 的人的
共同点恰恰是**同一个固定开会时间**，而 `WorkPattern` 挂 `Assignment` ——
没有这个入口，就是让基金会把同一个「周二 19:00」敲三十遍。
「第一次敲的过程就是想清楚怎么排班的过程」这句话对**排班**成立，
对**同一个例会重复三十遍**不成立。

### ⚠️ 提交之前必须有一屏确认，写清楚这一下会造成什么（2026-08-15 加）

算一下这个按钮的当量：勾 30 个人 → 30 条 `WorkPattern` →
每条向前生成 13 周 → **约 390 行 `Shift`，一次点击**。
这是本轮**单次操作影响面最大**的一个入口，而初版设计里它没有任何预览。

NN/g 复杂应用启发式第 5 条（Error Prevention）说的正是这个：
在人按下去之前，把这一下的结果预览给他看。做法是一屏，不是新结构：

```
将为 30 位成员各建 1 条周模式：
    每周二 19:00–21:00 · Food Pantry 例会
    生效 2026-08-18 起，先生成到 2026-11-16（13 周）
    合计约 390 个班次

⚠️ 其中 2 位已有覆盖同一时段的周模式，将被跳过：张三、李四

    [ 确认建立 ]   [ 返回修改 ]
```

三条要点：

1. **数字要真的算出来**（人数 × 周数），不写「若干」——
   一个不给数字的确认屏和没有确认屏是一回事；
2. ⚠️ **已有同时段模式的人要在这一屏列出来并跳过**，不是静默跳过也不是报错整批失败。
   跳过而不说，表现是「我明明勾了 30 个人，怎么只有 28 条」；
3. **生效日期和窗口末尾都要写出来** —— 窗口是 13 周（D2a.3），
   而"生成到哪天为止"是这个页面上唯一能看见它的地方。

⚠️ **撤销那一半本轮不做**（要给这一批一个 `batch` 标识才收得回来）。
主动接受，理由是数据本身收得回来：改模板走 `delete` + `bulk_create`，
只动未来、只动 `generated`。代价是**收拾它要一条一条删 `WorkPattern`** ——
这一条进[已知缺口表](phase-d.md#七已知缺口与处置)，
重启条件是「真的有人批量建错过一次」。

## D2a.8b 整批撤销（[D40](decisions/D40-undo-a-pattern-batch.md)）

```python
org/services.py::undo_pattern_batch(batch, *, by) -> UndoResult
    # ① 每条 pattern：_drop_generated_after(shifts_of(pattern), today)
    # ② 一行都不剩的 pattern → delete()；还剩行的 → end_date = today
    # ③ batch.undone_at / undone_by 落地
    # 返回：删了几条模板、留了几条、留下的每条为什么留
```

入口两个，⚠️ **两个都要有，缺一个就是本项目栽过三次的那个病**：

| 在哪 | 为什么两个都要 |
|---|---|
| 建完之后那条横幅上的「撤销刚才那一批」 | 这是 95% 的用法 —— 他刚发现建错了 |
| 批量建模板页上的「最近 7 天的批次」列表 | ⚠️ 横幅**一刷新就没了**，只有横幅等于**没有 URL 的功能** |

⚠️ 撤销前那一屏必须写出**留下来的那部分**（[D40 第一节](decisions/D40-undo-a-pattern-batch.md)）：

```
撤销 8/15 14:32 由 Alice 建立的那一批
  30 条周模式 · 每周二 19:00–21:00
  将删除 386 个未来的班次
  ⚠️ 4 个班次会保留，因此 2 条模板不会被删除，改为「即日停止」：
       2 个已经过去（已确认出勤）· 2 个被人手工改过
  [ 确认撤销 ]   [ 返回 ]
```

不写这一段的后果不是「少知道一件事」——是他以为撤干净了，
而下个月周视图上还站着两个人，**那时他已经不记得自己撤销过什么**。

三条边界：

1. **窗口 7 天**，且 `undone_at` 非空的批次列表里灰掉（D40 第五节）；
2. ⚠️ **并发**：服务开头先读一次 `undone_at`，非空就直接返回「已经撤销过」，
   **不报错** —— 两个 admin 同时撤同一批是会发生的；
3. 🔴 **不做重做（redo）** —— 要恢复就是再建一次，批量入口还在。

## D2a.9 调度冲突：一个函数，三处调用（[D39](decisions/D39-scheduling-conflicts.md)）

```python
org/services.py::conflicts_for(contact, on, start=None, end=None) -> list[Conflict]
    # 唯一的一处。四类：① 班次撞活动 ② 班次撞假期
    #                  ③ 指派撞假期或离职 ④ 活动撞活动
    # ⚠️ 一类都不拦截 —— 调用方负责显示，并给一个「仍然继续」
```

⚠️ **先做这个函数，D3 的指派页才有东西可用** —— 它排在 D2a 而不是 D3，
因为 ② 那一类（往假期里加班次）在 D2b 的 `Leave` 落地之前就已经能撞了，
而 ① ④ 两类在 D2a 交付当天就成立（班表一有，报名就可能撞）。

本批只接**班表侧那两处**（手工加班次 · `edit_shift()` 那一屏 · 批量建模板），
活动侧三处（报名 / 指派 / 代录）在 D3 接上，见 D3.2。

三条实现要点：

1. ⚠️ **② 和 ③ 都读 `Leave`，但返回两种措辞** —— 一个是「别在假期里排班」，
   一个是「别指派一个不在的人」。合并成一句「他请假了」会让两屏各显示一句不对题的话；
2. ⚠️ **④ 是白拿的，不要专门排除它** —— 这个函数按定义就是"列出他这段时间已经占住的东西"，
   `Participation` 和 `Shift` 是同一个窗口上的两个 queryset，**漏掉它要多写代码**；
3. **不要用红色**（[D27](decisions/D27-ministry-report.md) 的红色是"有没有出事"）。
   冲突是"你可能不知道"，走警示黄，对比度照 [`design-system.md`](design-system.md) 算。

## D2a.10 「某天谁在班」只有一处实现

```python
on_duty(ministry, date)      # org/services.py，唯一的一处
                             # ⚠️ ministry=None → 基金会层面的岗位（Position.ministry 可空）
```

⚠️ 班表页、ministry 详情页、例会那一屏、以后的报表**都必须问它**。
这是本轮第三条守卫。

⚠️ **`ministry` 那个参数要允许 `None`**：`Position.ministry` 是可空的
（"Executive Director 之类的基金会级岗位"，`org/models.py` 写着），
按 ministry 收窄的周班表会让这些人**一个页面都进不去** ——
不报错，只是他们不在任何一张班表上。foundation tier 的周班表看全部。

---

# ⏸ 试点反馈

D2a 交付后**让基金会真的用两周**，然后再开 D2b。要带回来的三件事：

1. 例会实际有几场、时间对不对 —— 直接决定 D2b 那一屏长什么样；
2. 「未确认」的量有多大 —— 决定那三条缓解够不够；
3. My Schedule 上两条泳道的标签，员工看不看得懂 ——
   ⚠️ 这一条只能问人，测试测不出来。

---

# D2b · 出勤、请假与口径

## D2b.1 例会出勤：批量翻状态（[D33 第五节](decisions/D33-work-schedule.md)）

```python
services.confirm_shifts(shifts, *, absent_pks=())
    # scheduled → worked，勾掉的 → absent
    # ⚠️ 已经 worked / on_leave 的不动
```

**落点是 D2a.8 那个某天出勤页上的一个表单**，不是独立功能
（初版只画了 UI、没说它在哪一页）。

⚠️ 那一屏**默认全勾**，只需取消缺席的；ministry admin 页面显示「上周有 N 小时未确认」；
🔴 **不做「超过两周自动确认」** —— 那是把假定值伪装成观测值，比现在更糟。

## D2b.2 `LeaveType` + `Leave`（[D34](decisions/D34-leave.md)）

```python
LeaveType(code, name, is_active)      # 种子：annual / sick / personal / bereavement / unpaid
Leave(assignment → PROTECT, leave_type → PROTECT, start_date, end_date, note="")
      + end_date >= start_date 约束 + simple-history
```

⚠️ **理由用字典表，不用自由文本**（初版是 `reason` 自由文本）——
那一列会装进病名和家里出的事，而 ministry admin 全看得见。
`note` 降级成备注，页面上明写「不需要填写医疗细节」。

`services.apply_leave(leave)`：把区间内 **`status=scheduled`** 的班次翻成 `on_leave`。
已 `worked` 的不动（既成事实），已 `absent` 的不动（另一个人的判断）。
销假：只把 `on_leave` 翻回 `scheduled`（[D34 第二节](decisions/D34-leave.md)）。

**权限**：类型和备注只有本人 + foundation tier 看得见；
ministry admin 看得到日期和「请假」这个事实。写在 `org/permissions.py`。

**录入口在自助侧**：`/me/leave/new/`，本人提交。
⚠️ 这不是可选的，它决定上面那条权限是不是一句空话 ——
**录入的人必须选类型**，录入口开在 ministry admin 那里，
他建这一行的时候就已经看见类型了，之后再"看不见"只是把他刚输进去的东西藏起来。
管理侧只留一个 `/org/leave/new/` 给 **foundation tier** 代录，
用于没有账号的在编人员（[D34 第四节](decisions/D34-leave.md)）。

**`Assignment.status` 的 `on_leave` 档退休（单独一小步 + 一次数据迁移）**：

```python
Status:  ACTIVE · SUSPENDED                    # ON_LEAVE 去掉
迁移：   现存的 on_leave 行 → active
serving(on) = active(on) AND status=ACTIVE AND 没有覆盖 on 的 Leave
```

⚠️ 初版写的是"保留为当前值缓存，不删"。**那个缓存没有人写** ——
`apply_leave()` 只翻 `Shift`。于是「他这两周在不在」有两个答案，
而 `serving()` 读的正是没人维护的那一个：**值班名单会说休假的人在岗**。
`SUSPENDED` 留着，停职不是请假，它本来就是人手工设置、人手工解除的。

## D2b.3 「实际投入」的唯一口径（配守卫）

```python
staff_hours(assignments, start, end)      # org/services.py，唯一的一处
# ⚠️ 返回按 hours_tracking 分组，不是三个数：
{
  "scheduled":   {"worked": …, "unconfirmed": …, "on_leave": …},
  "agreed":      {"hours": …, "missing_value": [assignment, …]},
  "not_tracked": {"people": …},                # 只有人数，没有小时
}
```

⚠️ **为什么不是三个数**：它收的是一批 `assignment`，而 `hours_tracking`
挂在每一个 `assignment` 上 —— 一批里必然混着三种模式，三种模式答的又不是同一种东西。
不这么定的话，第一个调用方会自己分组，第二个会分得不一样，
**而这个函数存在的全部理由就是不让那件事发生**。

`hours_tracking` 决定每一组怎么算：

| 模式 | 返回什么 |
|---|---|
| `scheduled` | 读 `Shift`，三个数（这一档 D2a 就要写，见 D2a.6） |
| `agreed` | 用 **`agreed_hours_per_week`** 折算，带「约定值，非观测值」标记。⚠️ 字段为空 → 进 `missing_value`，页面显示「约定值未填写」，**不是 0** |
| `not_tracked` | 返回"不计工时"这个状态本身，**不返回数字** |

**四条口径，全都只写在这一个函数里**：

1. **实到时长 = `COALESCE(actual_start/end, 排班起止)`** ——
   不这么读，`Shift.actual_*` 就是一对没有读者的字段，
   而它们正是例外记录法里记"偏离"的地方；
2. **「未确认」只数 `date < today`**（[D36 第一节](decisions/D36-two-hour-ledgers.md)）——
   不写死的话，报表会把未来 13 周的排班全算成未确认，
   变成一个上千小时、而且看起来完全合理的数字；
3. ⚠️ **「请假」只对已生成窗口内的日期成立**（窗口外那几天还没有行）。
   它不是"今年请了几天" —— 后者是 `Leave` 上的一个 `Sum`，**两个数不要混用**；
4. ⚠️ **`absent` 的小时数不进任何一格**，这是有意的：那段时间没有发生。
   （例会那一屏勾掉两个人之后，一定有人去报表上找他们的小时。）

⚠️ **它绝不和 `Participation.hours` 相加。**

这一格永远显示模式的标签，**永远不显示裸的 `0`**。
配一条测试：三种模式各断言一次、断言 `not_tracked` 那一档**不返回 0**、
**并且断言一批混着三种模式的 assignment 传进去能分开答**。

### 顺带做 `workweek_totals()`（[D33 第七节](decisions/D33-work-schedule.md) 2026-08-15 补）

```python
settings.WORKWEEK_STARTS_ON = 0      # 0 = 周一。⚠️ 定了不要改 ——
                                     #   改一次，所有历史周合计的边界一起变
org/services.py::workweek_totals(assignment, start, end)
    # -> [(week_start, scheduled_hours, actual_hours), …]
    # ⚠️ 时长口径**复用** staff_hours() 那一条（COALESCE(实际, 排班)），
    #    不许自己再算一遍 —— 那就是第二份口径
```

员工名册上给 `flsa_status=non_exempt` 的人出一条提示：**某一周合计超过 40 小时**。

⚠️ 三条边界，一条都不能省：

1. 🔴 **只算小时数，不算加班费** —— 那是 payroll（[D36 第九节](decisions/D36-two-hour-ledgers.md)）；
2. ⚠️ **`exempt` / `not_applicable` 的人不算也不提示** —— 他们的工时法律上就不要求跟踪，
   对全员算是把一条法律区分抹掉了；
3. **提示不拦截** —— 一周 43 小时完全合法，它只是必须被记下来、被看见。

## D2b.4 报表加「在编人员投入」+ 那个可印的志愿小时数

读 D2b.3，三个数：**实到 · 未确认 · 请假**，和「志愿者工时」**并排，不相加**。

**同时做那个可印的数**（[D38 第七节](decisions/D38-served-as-volunteer-or-work.md)）：

```python
volunteer_hours_total(ministries, start, end) -> (hours, uncounted_people)
    # hours = served_as=volunteer 的活动工时（所有人）
    #         + compensation=unpaid 的在编人员的 Shift 实到工时
    # uncounted_people = compensation=unpaid 且 hours_tracking != scheduled
    #         的在职 assignment 人数 —— 他们一行 Shift 都没有，所以不在 hours 里
    # ⚠️ 守卫白名单里唯一一处允许相加的地方，注释里写清楚为什么
```

⚠️ 这是**守卫四的唯一白名单例外**。白名单是守卫最容易被悄悄放宽的地方，
所以那一行注释要写死：**只有这一个函数，理由是两个来源按定义不相交。**

⚠️ **第二个返回值不是可选的**（[D38 第七节](decisions/D38-served-as-volunteer-or-work.md) 2026-08-15 补）：
`agreed` / `not_tracked` 的人**没有 `WorkPattern`，一行 `Shift` 都不生成**
（[D33 第一节](decisions/D33-work-schedule.md)），
而他们正是需求原文 ① 那批「像员工的志愿者」—— 不画出来，这个要印进年报和
grant 申请的数字就系统性偏低，**而且不报错**。

页面上是一行小字，跟着那个数走：

```
另有 4 位无薪在编成员按约定值或按产出考核，未计入上面这个数
```

⚠️ **人数为 0 时这行字也要在** —— 同 [D27](decisions/D27-ministry-report.md)
那条「未成年无同意记录为 0 也要画出来」。
🔴 **不要**把 `agreed` 的人按 `agreed_hours_per_week` 折算补进 `hours` ——
那是把约定值加进一批观测值，正是 [D36 第四节](decisions/D36-two-hour-ledgers.md) 拒绝过的形状。

## D2b.5 请假与班表的通知

走 `core/notifications/` 的投递适配器（[D22](decisions/D22-event-notifications.md)），
**不新建投递代码**。

⚠️ 不接的话，班表改了没有任何人知道 —— 而 My Schedule 是"你要自己去看"的页面。

**触发点只有三个，而且都是「与你有关的变化」**：

| 发 | 不发 |
|---|---|
| 你的某个班次被改了 / 被取消了 | 🔴 **「下周班表已生成」** |
| 你的假已录入（代录的那条路径尤其要发） | 每周的生成结果本身 |
| 你被指派进一场活动（D3） | — |
| **你被排进了一条新的例会**（一条 `WorkPattern` 建到你头上） | — |

🔴 **不做"下周班表已生成"这条周期性通知**（初版有）。
每周发给每个人，而绝大多数周它什么都没变 ——
**这是本轮最容易失分的一处打扰**，而且它会顺带训练所有人忽略这个发件人，
连真正该看的"你的班次改了"一起忽略掉。
班表本来就有两个"自己去看"的入口（My Schedule 和 `.ics`），
而 `.ics` 进了手机日历之后，变化是自动出现的。

### ⚠️ 「你被排进了一条新的例会」这一条不能省（2026-08-15 加）

`WorkPattern` 的录入路径**全部在管理侧**（岗位详情 · 员工名册 · 批量建模板），
而这一轮的对象**恰恰是志愿者**（需求原文 ①），不是雇员。

对雇员单方面排班是正常的；对无薪成员单方面排班而且不告诉他，
是这类系统里最容易掉人的地方 —— Planning Center 的 blockout
之所以设计成本人自己设、还能附一句话，面对的就是同一批人。

⚠️ 它和上面那条 🔴 **不矛盾**，判据是同一条：
「下周班表已生成」每周发、绝大多数周什么都没变；
「你被排进了一条新的例会」是**与你有关的一次变化**，一个人一辈子收不了几次。

配套的还有一处零成本的：My Schedule 的每一行本来就要写
「这个身份是谁定的」（[D38 第四节](decisions/D38-served-as-volunteer-or-work.md)），
**例会那一行顺带写「由谁排的」** —— 同一个模式，同一处代码。

⚠️ blockout 本身**仍然不做**（[D33 第十节](decisions/D33-work-schedule.md) /
[D39 第七节](decisions/D39-scheduling-conflicts.md)）。这两件是缓解，不是替代。

---

# D3 · 指派与代录（[D35](decisions/D35-event-assignment-path.md)）

## D3.1 `Participation` 加两档

```python
Status  + INVITED = "invited"     + DECLINED = "declined"
```

⚠️ **不加 `source` 字段** —— 初版有，[D38](decisions/D38-served-as-volunteer-or-work.md)
落地后取消了。理由和它为什么危险，见 [D35 第二节](decisions/D35-event-assignment-path.md)。

## D3.2 两条写入路径

| 服务 | 落地状态 | 入口 |
|---|---|---|
| `invite(contact, event_role, invited_by)` | `INVITED` | 活动管理页「指派」，只列本 ministry 在编人员 |
| `register_on_behalf(contact, event_role, consent=None)` | `REGISTERED` | 活动管理页「代录」 |
| `respond_to_invite(participation, accepted, *, served_as)` | `REGISTERED` / `DECLINED` | 「我的邀请」 |

⚠️ **两条路都必须经过 `sign_up()` 的那两道门**（紧急联系人、未成年同意）。
那个函数的注释里已经写着"an admin entering somebody from a paper list reaches
this function too" —— 本步要让这句话第一次变成真的，**不是绕开它**。

### 门之前先看一眼冲突（[D39](decisions/D39-scheduling-conflicts.md)）

活动侧这三处（报名 / 指派 / 代录）在这一步接上 `conflicts_for()`（D2a.9 已经建好）。
⚠️ **两件事顺序不能反，性质也不一样**：

```
① conflicts_for(contact, 活动日期, 起, 止)   → 提示，带一个「仍然指派」，不拦
② assert_signup_allowed(...)                → 拦
```

⚠️ **冲突要在选完人那一刻就显示，不是提交之后** —— 提交之后再说，
等于让他做完了才知道。而两道门是提交时的校验，位置不变。

**⚠️ 「经过」不等于「调用」，落法是把门抽出来**：

```python
events/services.py::assert_signup_allowed(contact, event, consent)   # 两道门，唯一的一处
    ↑ sign_up()          ↑ invite()          ↑ register_on_behalf()
```

直接让 `invite()` 去调 `sign_up()` 是不行的：那个函数会写
`status=REGISTERED` 和 `registered_at`，**指派会变成报名**，
然后再改回来 —— 又一次"两个动作必须配对"。

⚠️ `INVITED` 行的 `registered_at` **留空，接受的时候才写**：
它的意思是"报名成立于何时"，而被邀请的人还没答应。
（`Participation.Meta.ordering` 是 `["-registered_at", "contact"]`，
空值的排序位置实现时看一眼 —— 邀请列表按活动日期排，不靠它。）

**⚠️ `sign_up()` 要多认两种可复用的旧行**（它现在只复用 `CANCELLED`）：

| 场景 | 不改会怎样 | 该怎样 |
|---|---|---|
| admin 先指派了他，他没看邮件，自己去活动页报名 | 撞上「You have already signed up for this role.」—— 而他确实没报过 | 当成**接受邀请**：`INVITED → REGISTERED`，`declared_by=self` |
| 他拒绝过，后来改主意 | 同样被拒，且从他那一侧无法自救 | 当成重新报名：`DECLINED → REGISTERED` |

⚠️ 这是"两条路都走同一道门"必然带出来的第二半：门统一了，
**门后面那张表的旧行也就必须一起统一**。
（第一半是 C0.2 那次"取消之后报不回来"，也是在浏览器里发现的，
不是测试发现的 —— 每个测试取消完就结束了。）

**答复那一下顺便答身份**（[D35 第五节](decisions/D35-event-assignment-path.md)）：
邀请页上带 `served_as` 的两个选项，**默认预选「工作安排」**（他是被安排的），
但两档都画出来，`declared_by=self`。**不多一次操作。**

## D3.3 统计口径

口径一句话：`INVITED` / `DECLINED` **不算报名**。

⚠️ **但口径不能写成「只数 `REGISTERED` + `ATTENDED`」** ——
现在的 `with_signup_counts()` 数的是 `~CANCELLED`，**里面含着 `ABSENT`**，
而缺席的人当初确实报了名。写成正数枚举，会在同一次改动里
**把缺席的人从报名数里删掉**，那是一次没有人在找的静默变更。

**口径收成两个 queryset 方法，各一处**：

```python
ParticipationQuerySet.counted()      # exclude(CANCELLED, INVITED, DECLINED)  算不算报名
ParticipationQuerySet.notifiable()   # exclude(CANCELLED, DECLINED)           该不该通知
```

**受影响的是六处，一处一处改一定漏**：

| 落点 | 现在 | 不改会怎样 |
|---|---|---|
| `EventRoleQuerySet.with_signup_counts()` | `~CANCELLED` | 满员率、`is_short`、`understaffed()` 把待答复的人算成已报名 |
| `ministry_report()` 的 `parts` | `.notifiable()` | `signups` / `volunteers` / `repeat_rate` 虚高；⚠️ **`hours_missing` 尤其难看** —— 被邀请的人永远不会有 hours，那个"缺多少条工时记录"会跟着待答复人数涨 |
| `_absence()` 的分母 | 同上 | 同上 |
| `_top_volunteers()` · `_monthly_series()` · `_role_gap()` | 同上 | 排行榜、月度图、工种缺口图各错一点 |
| 🔴 **`resolve_recipients()`** | `.notifiable()`（只排 `CANCELLED`） | **已经拒绝的人还会收到"活动改期"通知**。`DECLINED` 要和 `CANCELLED` 一样出局，⚠️ 而 `INVITED` 要留下 —— 他还没答复，改期正是他需要知道的 |
| 报表面板 | — | **并排加一个数「待答复 M 人」**（藏起来的话，「没人答应」和「还没问」长得一模一样） |

⚠️ 缺勤率的分母（[D27](decisions/D27-ministry-report.md)）问的是
「这场活动还有没有报名停在 `registered`」。**`INVITED` 的行不能算进那个判断** ——
"还没答复"和"没人处理过考勤"是两件事，混在一起会让分母虚高。

**三条测试**，因为它们错了都不报错：
2 人已确认 + 3 人待答复 → 满员率分子是 2 不是 5；
1 人缺席 → **仍然算在报名数里**（这条盯的是上面那个正数枚举的坑）；
1 人已拒绝 → 活动改期时**不在收件人里**，而待答复的那个人在。

## D3.4 被邀请没答复的人当天来了

**直接签到，状态盖成 `attended`。** 沿用 D27 里 `check_in()` 把 `absent`
盖回 `attended` 的先例：要求先走完流程，结果是没人走流程。

---

# 本轮新增的守卫测试

现有 12 条之外加 9 条，都放 `core/tests.py`（和既有的汇报链守卫同一处）：

| 守卫 | 盯什么 | 不守会怎样 |
|---|---|---|
| 只有一处生成班次 | 除 `org/services.py` 外没有别处 `Shift.objects.create` / `bulk_create`。⚠️ **白名单要连生成器那句 `delete()` 一起写**，否则守卫会挡住它自己 | 第二个生成器的删／插规则和第一个不一样，两边都不报错 |
| 只有一处**改**班次 | 除 `org/services.py::edit_shift()` 外没有别处写 `Shift.status` / `start_time` / `actual_*`（白名单：生成器、`apply_leave()`、`confirm_shifts()`） | 没人把 `source` 翻成 `edited`，下一次生成把人手工改过的班次**删掉** |
| 只有一处回答"某天谁在班" | 除 `org/services.py::on_duty()` 外没有别处按 `Shift.status` 过滤（白名单：admin 的 `list_filter`、模板里只上色的分支） | `status` 该数哪几档有两份口径，页面和报表对不上 |
| 生成器不碰过去 | grep + 行为测试双管：生成函数里必须出现 `date__gt` 那个条件 | ⚠️ 静默改写考勤史，本轮唯一那条真正会出事的 |
| 两个工时账本不相加 | 除白名单外，**没有第二个函数同时出现 `Shift` 的小时聚合和 `Participation.hours` 的聚合**（grep 按函数体扫，不是按文件）。⚠️ **白名单只有 `volunteer_hours_total()` 一个** | 重复计算，且加出来的数看起来更完整、更像对的 |
| 只有一处检测冲突 | 除 `org/services.py::conflicts_for()` 外，没有第二个函数同时查 `Shift` 和 `Participation` 的时间窗。⚠️ 同守卫四，**按函数体扫不按文件扫** | 报名页和指派页各写一套重叠判断，两边算出来的「撞没撞」不一样，而两边都不报错（[D39 第五节](decisions/D39-scheduling-conflicts.md)） |
| 身份只有一处写入 | 除 `events/services.py::set_served_as()` 外没有别处写 `served_as`；**外加一条断言：`ParticipationAdmin.readonly_fields` 里有这两个字段** | 三条写入路径各带一套默认规则；⚠️ 而 admin 是**不写代码就存在**的第四条，grep 拦不住它 —— 从那里改出来的行 `declared_by` 是空的 |

| 只有一处删生成班次 | 除 `org/services.py::_drop_generated_after()` 外，没有别处同时出现 `date__gt` 和 `source=GENERATED` 的删除。⚠️ 白名单是**空的** —— 三个调用方全都调它 | 撤销 / 任职结束 / 生成器各写一遍那两个条件，其中一处漏掉 `date__gt` 就是**静默改写考勤史**（[D40 的不变量](decisions/D40-undo-a-pattern-batch.md)） |
| 「第 N 节」引用指得到 | `core.tests.DecisionSectionReferenceGuardTests`：全仓每一处 `D33 第七节` 这样的正文引用，那一节必须真的存在。⚠️ 「」里的**不算** —— 那是在引用一个引用（多半正是它在更正的那个坏引用），同强调守卫对反引号里的 `⭐` 的处理 | 拆一次决策、插一节，正文里的节号就全错位了，而[链接守卫查的是文件和锚点，查不到正文](phase-d.md#九第二次自查对着行业标杆和法规量了一遍2026-08-15)。⚠️ 2026-08-14 拆 D32 时真的漏了两处，全绿放了一天 |

⚠️ 「两个账本不相加」那一条**要按函数体扫，不能按文件扫**：
`org/services.py` 本来就会同时 import 两张表，按文件扫的守卫要么永远绿
（白名单开太大），要么天天红。**一条扫不准的守卫比没有守卫更糟** ——
它会被人加白名单加到失效，而失效之后没有人知道。

⚠️ 按项目惯例，每条守卫都要做**双向验证**：故意写一处违规的代码，确认它真的打红。

---

# 验收

按四批分别验收，每批走完标一次。

> ## 每批末尾那两条「错了之后」（2026-08-15 加）
>
> 起因是一次自查：**六十多条验收，覆盖的全是正向路径和静默错误的防线，
> 没有一条测的是「操作的人自己搞错了，救不救得回来」。**
> 这是 NN/g 复杂应用启发式第 3 条（User Control and Freedom）在验收清单上的落点 ——
> 而验收清单是这个项目真正的规格书。
>
> ⚠️ 这两条**不只是测试，它会反向逼出缺失的入口**。
> 本项目已经三次栽在「服务层写好了、没有页面」上
> （[C0.2 的五处缺口](phase-c.md#phase-b-的五处缺口2026-07-31-发现)、C0.5 的 `LOGIN_URL`、
> [D35](decisions/D35-event-assignment-path.md) 说的 `sign_up()`），
> 而每一次都是走查抓出来的，不是断言抓出来的。
> 「撤销那个动作从哪个链接点进去」和「那个功能从哪个链接点进去」是同一个问题。

## D1

- [ ] 迁移前后各建一批 `Position`，映射表逐行成立；反向迁移跑得通（且 docstring 注明有损）
- [ ] R8：`paid` / `unpaid` / `stipend` 三个在编人员都在名单里，**且名单上有「身份」一列**
- [ ] 外部志愿者报名 → 页面上**看不到**身份那个问题
- [ ] 在编人员报名 → 看得到、默认「志愿服务」、`declared_by=self`
- [ ] admin 从**报名管理页**改一个人的身份 → 本人在「我的报名」（`/me/participations/`）上看得到是管理员设置的
- [ ] 理事参加了活动 → **不在** R8 名单里（口径是有意的，D1.2）
- [ ] 外部志愿者**直接 POST** 一个 `served_as=work` → 库里那两列仍然是空
- [ ] 回填：有在职任职的旧行 `served_as` 为空，报表上进「身份未记录」而**不是**任何一边
- [ ] 本 ministry 带薪员工 `served_as=volunteer` 参加活动 → 工时**进** R6，且**出一条 FLSA 提示**
- [ ] 报表上写的是「志愿者工时 X（**其中**在编人员 Y）」，**不是三个并排的数**
- [ ] 理事报名 → 页面上**看不到**身份那个问题
- [ ] 去年离职、今天报名的人 → **看不到**那个问题（`active(on=活动日期)`）
- [ ] admin 里那两个身份字段是 **readonly**（改不动）
- [ ] `understaffed()` 三格：满编 / 部分填满 / 一个人都没有；**且 `vacant()` 的结果一个都没变**
- [ ] 名册上 `non_exempt` + `not_tracked` 的人**出一条矛盾提示**
- [ ] 组织架构图画得出来，且 `build_org_tree()` 只跑一次查询
- [ ] 拿 ministry admin 的真账号看**别的** ministry 的员工名册 → 403
- [ ] 普通志愿者账号看组织架构图 → 看得到；看 `compensation` → 看不到

错了之后：

- [ ] admin 把一个人的身份**改错了再改回来** → 两次改动在 simple-history 里都查得到，本人在「我的报名」上看到的是最新那次**和是谁设的**
- [ ] 证照的有效期录错了 → 改得动（`default_valid_days` 是**预填不是写死**），且名册上那条到期提示跟着变

## D2a

- [ ] 改模板重新生成：过去的行零改动，未来 `source=edited` 的行零改动
- [ ] **把例会从周二改成周三 → 未来的周二那批行一行不剩**（不是新旧并存）
- [ ] 把一行未来的班次改时间标 `edited` → 重新生成后那天**只有一行**
- [ ] 已有 `Leave` 的区间里，重新生成出来的是 **`status=on_leave` 的行**（不是没有行，也不是 `scheduled`）
- [ ] 任职填上 `end_date` → 未来的 `generated` 行没了，`edited` 的还在并有提示
- [ ] cron 在 Render 上真的跑过一次，班表窗口真的往前推了（13 周）
- [ ] ⚠️ 停掉 cron 一周，确认班表**在窗口末尾断掉**而不是报错 —— 确认这个失败模式长什么样，因为将来它一定会发生
- [ ] 周视图没有任何"列出全部"的入口；`.ics` 也带窗口
- [ ] **批量建例会模板**：勾 10 个人 + 一个周模式 → 10 条 `WorkPattern`，一次点击
- [ ] **刚建完就撤销**：30 条模板 + 约 390 行班次，**两步**（点撤销 → 确认）全部消失，库里干净得像没发生过
- [ ] 三周后再撤销同一批：过去的行**一个字节都没动**，`source=edited` 的也没动；那几条模板**没被删**，而是 `end_date=today`
- [ ] 确认屏上写出了「4 个班次会保留 · 2 条模板改为即日停止」，且数字是真算出来的
- [ ] 撤销之后那一批在列表里**灰掉**；再点一次 → 返回「已经撤销过」，**不报错**
- [ ] 单条建的模板（`batch` 为空）**没有撤销按钮**
- [ ] 8 天前建的批次**不在列表里**，而那些模板照样改得动、删得掉
- [ ] 撤销**一行 `Leave` 都没碰**（请假是权威，班次是派生的）
- [ ] 往一段已批准的假期里**手工加一个班次** → 出提示，**而且加得进去**（提示不拦截）
- [ ] 提示走的是警示黄那一档，**不是红色**（红色在本项目里是「有没有出事」）
- [ ] 那一屏确认上的**三个数字是真算出来的**（人数 · 周数 · 班次数），且**已有同时段模式的人被列出来并跳过**（不是静默跳过，也不是整批失败）
- [ ] **拿一个员工的真账号打开 My Schedule**：两条泳道分得清、两个数没有被加起来、**三个数是同一个时间窗**、每一行都写得出是谁定的身份
- [ ] `.ics` 订阅进手机日历，班次显示正确；**刷新两次不出现重影**（`UID` 稳定）；**撤销 token 之后旧链接失效**
- [ ] `.ics` 里**两条泳道都在**（班次 + 自己报名的活动），且两者 `UID` 前缀不同
- [ ] 活动那一条进日历后**时刻正确**（`Event` 是 aware datetime，拼进去前落成墙钟时刻）
- [ ] 给一个人建一条新的 `WorkPattern` → **他收到一条通知**；而每周 cron 推窗口**不发任何东西**
- [ ] My Schedule 上「待确认」那个数**排版上退了一格**，且底下有一行「这些小时还没有人核实」
- [ ] 基金会级岗位（`Position.ministry` 为空）的人**在某张班表上出得来**
- [ ] 改一个班次 → `Shift` 的历史表里查得到是谁改的，**且那一行的 `source` 变成了 `edited`**

错了之后：

- [ ] **批量建模板时勾错了人**（或时间填错）→ 数一遍收拾它要几步；确认那批未来的班次真的跟着没了，而**别人的没被误伤**
- [ ] 一个员工把 `.ics` 链接发错了人 → **他自己**能撤销并换一个，不需要找 admin；换完旧链接立刻失效

## D2b

- [ ] 两周假 = 1 行 `Leave` + N 个班次翻成 `on_leave`，且已 `worked` 的不翻
- [ ] 销假 → 只有 `on_leave` 的翻回去，期间被改成 `worked` 的不动
- [ ] **员工用自己的账号在 `/me/leave/new/` 录一次假**，全程不需要找 admin
- [ ] 拿 ministry admin 的真账号看请假 → 看得到日期，**看不到类型和备注**，**也没有录入按钮**
- [ ] 休假中的人**不在 `serving()` 的结果里**（`Assignment.status` 已经没有 `on_leave` 这一档）
- [ ] 例会勾掉的那两个人，他们的小时**不在三个数的任何一格里**（这是有意的，演示时要先说）
- [ ] 例会那一屏：8 人排班，勾掉 2 人，一次点击翻完；已 `worked` 的那行没被翻回去
- [ ] 「未确认」那个数**不包含**未来的班次
- [ ] 一个 `non_exempt` 的人某一周排了 43 小时 → 名册上**出一条提示**，且提示写的是**那一周**不是那个月
- [ ] 同样的 43 小时换成 `exempt` 的人 → **不出提示**，也不算周合计
- [ ] `workweek_totals()` 和 `staff_hours()` 对同一段时间**算出同一个总数**（证明时长口径只有一份）
- [ ] `hours_tracking=not_tracked` 的人，报表那一格显示「按产出考核」而**不是** `0`
- [ ] `hours_tracking=agreed` 且 `agreed_hours_per_week` 为空 → 显示「约定值未填写」，**不是 0**
- [ ] D27 报表上「志愿者工时」和「在编人员投入」是两块，页面上没有任何地方把它们加起来
- [ ] 那个可印的志愿小时数：无薪在编人员的班表工时**进**，带薪的**不进**
- [ ] 一个 `compensation=unpaid` + `hours_tracking=agreed` 的人：他的小时数**不在**那个数里，而他**被数进了下面那行「另有 N 位…未计入」**
- [ ] 把那批人全部删掉、人数变成 0 → 那行字**仍然在**（不是消失）

错了之后：

- [ ] 员工自己**录错了假**（日期错 / 类型错）→ 他自己在 `/me/leave/` 改得动或删得掉，班次跟着翻回 `scheduled`；⚠️ 期间被人改成 `worked` 的那几行**不动**（[D34 第二节](decisions/D34-leave.md)）
- [ ] 例会那一屏**勾错了人** → 再翻回去；`Shift` 的历史表里两次都查得到，且那一行的 `source` 反映有人碰过

## D3

- [ ] 指派 → 对方收到通知 → 接受（同时选了身份）→ 状态变 `REGISTERED`
- [ ] 指派 → 拒绝 → `DECLINED`，且不进满员率
- [ ] **拒绝过的人，活动改期时收不到通知；待答复的人收得到**
- [ ] **被指派、没答复、自己跑去活动页报名 → 变成接受，不是"你已经报过了"**
- [ ] 被邀请没答复的人当天扫码签到 → 直接 `ATTENDED`
- [ ] 2 人确认 + 3 人待答复的活动，满员率分子是 2
- [ ] **1 个缺席的人仍然算在报名数里**（正数枚举那个坑）
- [ ] 给一个没有紧急联系人的未成年人发指派 → 被 `sign_up()` 那两道门挡住
- [ ] 把一个人指派进一场**落在他班次里**的活动 → **选完人那一刻**就出冲突提示，写明撞的是哪个班次、撞在哪一段；点「仍然指派」指派得成
- [ ] 同一个人选「工作安排」→ **照样出提示**（措辞是「他已经排了班」）；选「志愿服务」→ 出的是那条 FLSA 风险提示。⚠️ 两条走**同一个检测**
- [ ] 把一个当天在 `Leave` 里的人指派进活动 → 出提示（措辞是「他那天不在」，**不是**「别在假期里排班」）
- [ ] 把本 ministry 的带薪员工指派进活动、他选「工作安排」→ 工时**不进** R6 / R7
- [ ] 同一个人改成「志愿服务」→ 工时**进** R6 / R7，另外出一条 FLSA 提示

错了之后：

- [ ] admin **指派错了人** → 撤销指派，对方收到「指派被取消」的通知（[D35 第六节](decisions/D35-event-assignment-path.md) 的三个触发点之一），且那一行不再计入满员率
- [ ] admin **替人拒绝错了**（[D35 代价 3](decisions/D35-event-assignment-path.md)）→ 本人自己从 `DECLINED` 报得回来（`DECLINED → REGISTERED`），且两次都留痕

## 全轮

- [ ] `python manage.py check` / `makemigrations --check` / `ruff` 干净
- [ ] 测试数只增不减（[口径见 `phase-c.md`](phase-c.md#测试数基线只增不减的新口径)）
- [ ] 21 条守卫全部做过双向验证
- [ ] `python manage.py test core.tests.MarkdownLinkGuardTests core.tests.EmphasisGuardTests core.tests.DecisionSectionReferenceGuardTests` 绿
      —— 本轮改了十二份文档，⚠️ **三条都要跑**：链接那条挡指不到的文件和锚点，
      **节号那条挡正文里的假引用**（2026-08-15 加，起因是拆 D32 时真漏了两处），
      强调那条挡「`⭐` 和加粗越写越多」（[`goal.md` 约定 6](goal.md)），
      而后者在写决策文档的时候最容易一路失守

---

# 计划外记录

> 实施时才发现的坑写在这里。**这一节是这个项目最贵的资产之一**，
> 每个 roadmap 都留着它，不要因为"这次很顺"就不写。

[开工前那一轮自查](phase-d.md#六自查这一轮砍掉和补上的东西)已经先记了一批，
那是**文档自己的坑**：初版把十一个页面全做成了管理侧、把代理变量当成了事实、
把「见 D32 第五节末尾」指到了一段不存在的内容上。下面是动手之后才发现的。

## D1.1 · 「这一步不改 R8」这句话执行不了（2026-08-20）

D1.1 的改动表里原来写着「`ministry_staff_participation()` 见 D1.2，
不在这一步顺手改」。⚠️ 那是**照着做会卡住**的一句话：`Kind.EMPLOYEE`
在 D1.1 就从枚举里消失了，那一行不动，`events/services.py` 整个 import 不了。

改法和它带来的好处都写在 D1.1 里了 —— 用两个条件（`STAFF` 且 `PAID`）
把旧语义原样接住，于是拆轴那一步真的一个答案都没改，
而 D1.2 变成删掉一行的一次纯粹口径变更。

**教训不是「文档写错了」**，是：一条「这一步不要动 X」的指示，
要先问一句「不动它，这一步编译得过吗」。

## D1.1 · 一个 `docstring` 的落点跟着方法一起没了（2026-08-20）

[D37 第一节](decisions/D37-hris-fields-and-credentials.md) 写着
「连同它的测试一起删，并在 `clean()` 的 docstring 里写明为什么删」。
而删掉那条规则之后 `Assignment.clean()` 只剩 `super().clean()` ——
方法整个该删，于是那句话没有地方可写。

处置：写进 `Assignment` 的类 docstring（它本来就是讲这张表字段边界的那一段），
并且在那里明说「没有 `clean()` 是一个决定，不是删漏了」。
⚠️ 顺带把那条测试**改**了而不是删：`test_employment_type_is_allowed_on_an_unpaid_position`
——「无薪的兼职」在这个基金会里是真实存在的安排，
而只删不改会让「现在到底允不允许」没有任何一处说得出来。

## D1.1 · 演示数据一动，验收走查测试跟着红（2026-08-20）

给 seed 加了两个人（无薪 + 津贴各一，因为 D1.2 的验收要三档并排看得见），
`events.tests.AcceptanceWalkTests` 里那条 `total_hours == 9.00` 当场变成 15.00。

⚠️ 这不是坏事，是**耦合本来就该被看见**：那条测试断言的是演示数据的属性，
所以演示数据长了，它就该跟着改，并且在注释里写明那 6 小时是谁的。
记在这里是因为下一次往 seed 里加人时，第一个红的还会是它。

## D1.3 · `-> str | None` 那个签名和 D38 自己的表打架（2026-08-20）

最贵的一条，而且**是测试逼出来的，不是读文档读出来的**。全文在 D1.3 那一节
和 [D38 第五节](decisions/D38-served-as-volunteer-or-work.md) 的更正框里。
一句话：那张表有两列（默认值 · 问不问），一个返回值装不下两列，
而装不下的那一半正好是志愿者工时那个数的绝大部分。

⚠️ 记在这里是因为**这类错只有实现能发现**：文档里那个签名读起来完全合理，
D38 写完之后被读过至少三遍（含开工前那两轮自查），没有一次看出问题。
第六节那两把尺子（「这行数据有没有人读」「这个人做错了会怎样」）都量不到它 ——
它要的是第三把：**照着这个签名写一遍，能不能把表里每一格都表达出来**。

## D1.3 · 一个通用组件缺一档，而缺的那一档正好是本轮要用的（2026-08-20）

`core/components/field.html` 只认 checkbox 和「其它」两支，没有单选组 ——
而单选组走「其它」那一支的结果是一个 `<label for>` 指着**第一颗**单选钮：
读屏念到第二个选项时，那句问题已经不在了，人听到的是两个没有问题的答案。

处置：给那个组件补一支 `<fieldset>` + `<legend>`，形状照抄扫码打卡那一屏
现成的单选样式（`label.card` 包着原生 radio）——
`app.css` 里那条 accent-color 的注释早就写着「写两遍就会分叉」，这是第二处。

## D1.3 · 演示数据里那个人没有账号，于是那条验收根本走不了（2026-08-20）

D38 第四节把「本人看得见」写成不可交易的一条，D1 的验收里也有它。
写测试时才发现：演示数据里唯一有身份的无薪在编成员 **Ada 没有登录账号** ——
admin 改完之后没有任何办法以她的身份打开「我的报名」看一眼。

给她开了一个 demo 账号（`ada@example.invalid`）。⚠️ 这一条和 D1.1 那条
「演示数据一动，走查测试跟着红」是**同一件事的两面**：
演示数据不是装饰，它决定了哪些验收走得了、哪些走不了。

## D1.3 · 一次报名留两条历史（主动接受）（2026-08-20）

`sign_up()` 先 `save()` 建行、再由 `set_served_as()` 写身份，
于是每一次报名在 `HistoricalParticipation` 里留下两行 ——
第一行的身份是空的。

⚠️ 主动接受，并且**用断言钉住**（`ServedAsTests` 里那条历史测试写死了三条记录）。
换掉它的唯一办法是让 `set_served_as()` 多一个「只赋值不落库」的模式，
而那就是第二条写入路径 —— D38 整条不变量防的正是它。
钉住是为了让以后改这个形状的人必须是**故意**的。

## D1.1 · 迁移的真验收只能在有旧数据的库上做（2026-08-20）

单元测试建的是**按迁移跑出来的空库**，证明不了映射对不对（这一点 D1.1 里
先写下来了，动手时确认属实）。实际做法：建一个一次性的 `rolf_migcheck`
库，迁到 0006、灌进三行旧数据、再迁到 0008。两张表六行全部落在映射表上。

⚠️ 顺带把反向也走了一遍，而 docstring 里预告的那个有损边界**真的复现了**：
一行 `stipend` 反向成 `employee`，再正向一次变成 `paid`，两趟都不报错。
文档里那句「反向是止血手段，不是一扇可以来回走的门」现在有实测撑着。
