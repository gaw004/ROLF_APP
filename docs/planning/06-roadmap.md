# 参与者 L1～L5 · 实施步骤

> 要点、框架、行业对照、已知缺口在 [`participants.md`](participants.md)。
> 这一份只讲照着做的顺序 —— 每一步后面标的是它对应的那一层和那条决策。
>
> 建立于 2026-08-21，起因是 [`participants.md` 第十节](participants.md) 末尾那句
> 「L1～L5 的实现本轮不做，形状已经在第六节定下来」。形状定了，这一份把它拆成能照着敲的步骤。
>
> 关于编号：本文件的步骤号直接用层号（`L1.1`、`L2.1`…），不另发明一套。
> 层号已经是这一轮的共同语言，而 `P1`–`P6` 在本项目里指的是需求，不能重用。
> 三个交付批次叫 `批一 / 批二 / 批三`。

## 和 Phase D 的关系（先说清楚，否则两份 roadmap 会互相矛盾）

[`05-roadmap.md`](05-roadmap.md) 与 [`phase-d.md`](phase-d.md) 目前**只执行到 D1.3**
（拆轴、R8 换口径、身份轴 `served_as`）。D1.4 之后的每一步都还没动，
其中和本轮直接相邻的有三处：

| Phase D 的步骤 | 状态 | 和本轮的关系 |
|---|---|---|
| D1.4 R6 / R7 换成一个 `served_as=volunteer` 的 filter | 未做 | 正交。本轮让 `attending` 的行**根本没有工时**，D1.4 是把剩下的工时拆成志愿/工作。两步叠加不冲突，顺序也不限定 |
| D2a `WorkPattern` / `Shift` / `PatternBatch` | 未做，仓库里一行代码都没有 | [`participants.md` 第六节](participants.md) 写「正确形状仓库里已经有了一份」，指的是 [D33](decisions/D33-work-schedule.md) / [D40](decisions/D40-undo-a-pattern-batch.md) 这两份**文档**。批三是这个形状在本仓库的第一次实现 |
| D3 指派与代录 | 未做 | 它会给 `Participation` 加两档状态，和本轮的 `nature` / 可见性字段不碰同一列 |

出现出入时，以本文件的实际操作为准，回头改 `05-roadmap.md` 和 `phase-d.md`。

## 动手前先读这五条

1. [D32 的唯一不变量](decisions/D32-worker-axes-schedule-and-assignment.md)：
   在编只有一套结构（`Position` + `Assignment`），不许有第二套。本轮 L2 / L3 的
   全部判据都建立在它上面，**不许出现第二种人员分类**，也不许出现
   `Contact.is_beneficiary` 这一类字段；
   ⚠️ 它说的是**路径不是条数** —— 一个人可以同时持有多条 `Assignment`
   （[D11](decisions/D11-position-and-assignment.md) 拆表的起因就是一人多岗）。
   所以本轮每一条在编判据都写成「**存在**一条满足条件的在职任职」，
   `.get()` / `.first()` 在这张表上都是 bug。这一条 2026-08-21 补进 D32；
2. [D38 的唯一不变量](decisions/D38-served-as-volunteer-or-work.md)：「这一次是工作还是志愿」
   只由 `served_as` 一个字段承载。本轮给它加了第三档 `not_applicable`，
   那**不是**第三种身份，是「这个问题在这一行上不成立」——
   写法和理由见 [L1.2](#l12-servedas-加第三档-not_applicable配一条约束)；
3. 三处会静默改变结果：`hours_missing` 的分母、`hours_per_participant` 的分母、
   活动列表的可见集合。各自单独一步，各配一条钉住新口径的测试；
4. [D18 的落点规矩](decisions/D18-admin-boundary.md)照旧：逻辑进 `services.py`，
   权限只在 `org/permissions.py`，统计在 queryset，视图是薄壳。本轮新增五条守卫；
5. 本轮改十份文档。[`core/tests.py`](../../core/tests.py) 里那三条文档守卫
   （`MarkdownLinkGuardTests` / `DecisionSectionReferenceGuardTests` / `EmphasisGuardTests`）
   每一条都要跑，理由见[全轮](#全轮)。

## 七个已定的决定（2026-08-21）

动手前逐条问过，答案写在这里，不要在实施中途重开：

| # | 问题 | 定案 |
|---|---|---|
| 1 | 全机构岗位（`Position.ministry` 为空，如执行主任）在「本 ministry 在编」这一档算不算 | 不算。它只满足「全体在编」 |
| 2 | ~~公告怎么和「还没建完」区分~~ | ❌ **2026-08-31 推翻**，见 [D41](decisions/D41-notices-are-not-events.md)。这个开关一行代码都没写过，而它两头都不成立：有固定时间的那一半归下面第 3 条，没固定时间的那一半**根本进不了 `Event`**（起止两列都是 NOT NULL）。公告改成独立的 `Notice` 表 |
| 3 | 想参加公告的人怎么「记住」它 | 不做新东西。需要被记住的一律开一个 `attending` 角色（`needed_count` 留空）。**2026-08-31 起这一条承重**：删掉第 2 条那个开关之后，它是「有固定时间、想来就来」唯一的落点 |
| 4 | L5 的载体 | 独立 `EventSeries` 表，规则必须带结束条件，一次生成完，不加 cron。⚠️ 2026-08-26 收窄：它**只服务 recurring events 那一档**，Programs 是另一个形状（[L5.0](#l50-六个决定以及为什么是三档不是四格)） |
| 5 | 生成场次的角色从哪来 | `EventSeriesRole` 模板表，生成时逐场复制成真的 `EventRole`。⚠️ 同上，只对 recurring events |
| 6 | 可见性判「哪一天在编」 | 活动当天，和 L2 资格同一把尺 |
| 7 | `attending` 行的 `served_as` | 加第三档 `not_applicable`；`hours_per_participant` 分母改成「帮忙的人」 |

⚠️ 第 1 条的措辞被 2026-08-26 那批改写了：「本 ministry 在编」这一档不存在了，
现在是显式勾选的 ministry。结论没变 —— 全机构岗位（没有 ministry）落不进任何一个
具体 ministry 的勾，只满足「全体在编」。

## 又八条（2026-08-26，批二开工前）

批一交付后走查提出来的两件事：兜底工种要分档，可见性要能多选。
第二件**推翻了上面第 1 条所属的那整套三档枚举**，理由和代价写在
[L2.1 那一节](#-2026-08-26三档枚举被推翻了改成多选这一节整个重写)。

| # | 问题 | 定案 |
|---|---|---|
| 8 | 「没有特定工种」的兜底 | 拆成两行，helping / attending 各一，见 [L1.6](#l16-兜底工种拆成两个2026-08-26-追加) |
| 9 | 可见性的形状 | 一组勾选，不是三档枚举：外部人员 / 全体在编 / 各 ministry（可多选） |
| 10 | 「外部人员」这一档的语义 | **只有**没有在职任职的人看得见，在编的人**看不见**。它不是最宽的一档 |
| 11 | 「所有人」怎么存 | 不存。它是表单上的一个便利勾，库里存的是「外部人员 + 全体在编」两项 |
| 12 | 一项都不勾 | 拒绝保存 |
| 13 | 勾了「全体在编」之后各 ministry | 界面置灰，**且服务层拒绝**手工造出来的冗余组合 |
| 14 | 新建活动的默认可见性 | 空着（只预勾发布者自己的 ministry），逼他选一次 |
| 15 | 新开角色的默认可报范围 | 等于活动勾了什么 |

⚠️ 第 10 条是原来那三档**没有**的一档，也是这次改动里除了多选之外的第二个新东西：
「只给受助者的物资发放，不想让员工报名占位」在旧形状里表达不出来。

第 4、5 两条的行业依据：V4S 的 `Job_Recurrence_Schedule__c` 是独立对象、
规则和「要几个人」都挂在它身上，生成 `Volunteer_Shift__c` 行；
CiviCRM 靠 `linkedEntities` 把子记录逐场拷贝；
ChurchSuite 的 event sequence 每一场都是真行，「更新整个序列」只影响今天以后的场次，
并明确警告它会覆盖单场改动。三家的共同点是同一句话：
规则只存一份，场次全部物化成真行，「改未来」是一次带范围的编辑。

## 交付节奏：三批

| 批 | 装什么 | 为什么是这个顺序 |
|---|---|---|
| 批一 · L1 + L4 | 性质轴 + 记账口径 | 结构最小（字典表加一列），却当场修掉一个正在涨的静默 bug，并让报表第一次答得出「我们服务了多少人」。不依赖 L2 / L3 |
| 批二 · L3 + L2 | 可见性 + 资格 + 公告（`EventType` 上页面那一条作废，见 L2.6） | 一个整体：不变量横跨两层，拆开交付会留一个「角色比活动宽」的窗口期。本轮权限面最大的一批，必须配浏览器验收 |
| 批三 · L5 | 一期 / 各报各的 / 单场 | 三张新表（`Session`、`SessionAttendance`、`EventSeries`）。⚠️ 2026-08-26 重写：初版只装了需求 4 的一半，另一半（Programs：报一次管全部）是 [`participants.md` 第九节](participants.md)第一条缺口的出栏 |

---

# 批一 · L1 性质轴与 L4 记账

## L1.1 `ParticipationRole.nature`

### 落库的形状，照抄进 `events/models.py`

```python
class ParticipationRole(ImmutableCodeMixin, ConstraintErrorFieldMixin, models.Model):

    class Nature(models.TextChoices):
        """来提供，还是来接受 —— 属于工种本身，不属于某一场活动对它的一次开设。

        判据是 D10 那条「换个人来做这条信息还成立的，属于编制」：「ESL 座位」
        不管谁来坐都是来接受的，「搬运」不管谁来干都是来提供的。所以它落在字典
        表上，`EventRole` 一个字不动 —— 于是它不可能在两场活动之间被设成不一致。
        """

        HELPING = "helping", "Helping — they give their time"
        ATTENDING = "attending", "Attending — they receive a service"

    GENERAL_CODE = "general"

    code = ...      # 一个字不动
    name = ...      # 一个字不动
    nature = models.CharField(
        max_length=20,
        choices=Nature.choices,
        default=Nature.HELPING,
        verbose_name="What somebody in this role is doing",
        # ⚠️ 第一句是[第九节那条缺口](participants.md)点名要写进界面的定义，
        #    不是客套话：在非营利行业里 participant 最常被读成「被服务的人」，
        #    而这两档第一次被并排命名就在这里。少了它，基金会会按行业习惯把
        #    「参与者」读窄成受助者，然后给来帮忙的人另找一个词。
        help_text="Everybody at an event is a participant — this says which "
                  "kind. Lifting, interpreting and the welcome desk are "
                  "helping; an ESL seat or a food parcel is attending.",
    )
    is_active = ...  # 一个字不动
```

### 为什么是 `TextChoices` 而不是字典表上的一列

[D5 的判定规则](decisions/D05-lookup-tables-not-enums.md)是「代码要不要 branch 它」。
`nature` 有五处代码 branch（默认身份、拒绝工时、报表两个分母、`people_served`），
所以它是枚举而不是给基金会自己加行的字典表。⚠️ 它长在一张字典表**上面**，
这两件事不矛盾：`ParticipationRole` 的行由基金会自由增删，每一行属于哪一档由代码决定。

先例是 `RelationshipType.usable_as_emergency_contact`（`contact/models.py`）——
字典表上一个被代码读的判断列，同一个形状，第二次出现。

### 为什么这里可以有默认值，而 `served_as` 不许有

两条规矩看起来打架，实际相反，注释里要把这一对写出来，否则下一个人会以为其中一条被违反了：

- `served_as` 的默认会**凭空造证据**（[D38 第九节](decisions/D38-served-as-volunteer-or-work.md)：
  给历史行 default 回填一个「志愿服务」，等于替没人说过的话背书）；
- `nature=helping` 的默认是**已经成立的历史事实**：今天库里每一个工种都是来提供的。

⚠️ 「今天库里全是 helping」这句话要在 dev 库上真看一眼，不是推理。
迁移不做任何回填，验收里有一条「打开 admin 过一遍现有的工种行」。

### `clean()` 加一条：已有报名的工种不许改 `nature`

```python
    def clean(self):
        super().clean()          # 原有的 code 检查，一个字不动
        if self.pk is None:
            return
        was = (type(self).objects.filter(pk=self.pk)
               .values_list("nature", flat=True).first())
        if was is None or was == self.nature:
            return
        if Participation.objects.filter(event_role__role_id=self.pk).exists():
            raise ValidationError({"nature": (
                "People have already signed up through this role, and their "
                "records were written under what it says now. Add a new role "
                "instead — a dictionary row is cheap."
            )})
```

它护的是两样东西：报表的两个分母，以及已经写进库里的 `not_applicable`。
翻档之后的状态是**看得见的**（一个 helping 的工种上挂着 `not_applicable` 的行），
不再是静默的，但看得见不等于可以发生。

⚠️ `Participation` 在本模块里定义在 `ParticipationRole` **下面**。
方法体里引用它没问题（调用时才解析），类体里不行。

它是**提示层，不是约束**，按 [D14](decisions/D14-constraint-is-the-only-rule.md) 的规矩说清楚：
`ParticipationRole.objects.filter(...).update(nature=...)` 从它旁边走过去，
`clean()` 一次都不会被调用。而它进不了 `CheckConstraint` 的理由和 L2×L3 那条一样 ——
判据在另一张表上（有没有 `Participation` 指向我），跨表条件表达不了。

⚠️ admin 这一格和 [D38 第四节](decisions/D38-served-as-volunteer-or-work.md) 那次**不一样**，
值得写出来免得照抄错：`served_as` 当时要靠 `readonly_fields` 挡住 admin，
是因为守卫 grep 不到「一个没人写代码的表单」。这里不需要 ——
admin 的 ModelForm 会调 `full_clean()`，所以 `clean()` 天然覆盖 admin 那条路。
`nature` 因此**保持可编辑**：没人报名之前把开错的档改回来，正是它该允许的事。

### 兜底工种 `general` 落在 `helping`，且本轮不建第二个

`ParticipationRole.seed_general()` 那一行（`code="general"`，
迁移 0015 改名成 "General participant"）拿的是默认值 `helping`，而那是对的：
今天挂在它下面的每一条报名都是来帮忙的。

⚠️ 于是「没有特定工种的**受助者**」暂时没有落点。**本轮不seed 第二个兜底行**，
理由是 [`goal.md` 零](goal.md)那句「这张表 / 这个字段，会出现在哪条需求的查询里？
答不上来就先别做」：它不需要预先建 —— ministry admin 在报名管理页上一次点击就能加一行
（2026-08-04 那条路，本步给它补上选档）。

⚠️ 顺带记一处措辞张力，不改，只记：这一行现在叫 "General participant"，
而 `participant` 在本轮的词汇里是**总称**（涵盖两档），所以一个只属于 `helping`
的行叫这个名字读起来偏宽。不改是因为它三周前刚由迁移 0015 改过一次名，
再改一次是纯churn；真要分开时，正确的动作是加一行 attending 的兜底，
而不是把这一行重命名。

> ### 2026-08-26 更正：上面这两句都推翻了，见 [L1.6](#l16-兜底工种拆成两个2026-08-26-追加)
>
> **「本轮不建第二个」推翻了。** 那个判断的依据是「管理员一次点击就能加」——
> 而它把一个**系统级的缺口**摊派给了每一个基金会：他们要先自己撞上
> 「原来受助者没地方落」，再自己想到解法。`goal.md` 零那条判据问的是
> 「这一行会出现在哪条需求的查询里」，而它出现在需求 2 里 —— 我当时答的是
> 「不需要预先建」，那答的是另一个问题。
>
> **「不重命名」也推翻了。** 只加不改的话，下拉框里并排的是
> `General participant` 和 `General participant (attending)`，
> 前者的档位**是隐形的** —— 要靠「不带括号就是 helping」这条没人说过的规则才读得懂，
> 正是 [D27](decisions/D27-ministry-report.md) 那条「没有和没算不能长得一样」。
>
> ⚠️ 而「三周前刚改过、再改是纯 churn」这条理由**本身没错，只是不适用**：
> 那次反对的是**不携带新信息**的改名。这一次的括号里装着档位，是新信息。
> 判断一次改名值不值，看的是它加了什么，不是距上次多久。

### 迁移

`events/migrations/0016_participationrole_nature.py` —— 纯 `AddField`，无回填。
docstring 里写明「默认值等于今天的事实，所以这一步不改任何一行数据的含义」。

### 跟着改的地方，逐个点名（漏了大多不报错）

| 文件 | 改什么 |
|---|---|
| `events/forms.py` · `EventRoleForm` | 加 `new_role_nature`（`ChoiceField`，`required=False`），`clean()` 里要求「填了 `new_role_name` 就必须选档」。⚠️ 还要进 `order_fields`，紧跟在 `new_role_name` 后面 —— 那个方法里已经有一段注释在讲为什么声明顺序不能当渲染顺序用 |
| `events/forms.py` · `EventRoleForm.clean()` 的重名错误 | 消息里带上已有那一行的档位。现在只说「已经有一个叫 X 的角色，去上面挑」，而如果那一行的档位正是他不想要的，这句话把人带进死胡同 |
| `events/services.py` · `create_participation_role(name)` | 多收一个 `nature`。⚠️ 这条是最容易漏的一格：报名管理页可以现场新建工种（2026-08-04 加的那条路），漏了它，每一个临时建的工种都是 helping，而 ESL 座位会静默计工时 |
| `events/services.py` · `matching_participation_role(name)` | 不改，但 docstring 补一句：重名检查不看 `nature`，两个同名不同档的工种仍然算重复 |
| `events/admin.py` · `ParticipationRoleAdmin` | `list_display` / `list_filter` 加 `nature`。⚠️ 不进 `get_readonly_fields` —— 见上面 admin 那一段 |
| `events/templates/events/_event_roles_panel.html` | 工种表加一列档位，管理员开角色时看得见自己开的是什么 |
| `events/templates/events/_event_detail_body.html` | 同上，报名的人也要看得见 |
| `events/management/commands/seed_demo.py` · `dictionaries()` / `events()` | 加一行 `attending` 的工种（ESL seat），并给它一场活动 |

⚠️ 一处从这一步**撤回**的改动，记下来免得下一个人以为漏了：
`RoleChoiceField.label_from_instance`（报名下拉）本来列在这张表里，
理由是「否则报名的人看不出 ESL 座位和搬运是两回事」。撤回有两条：
角色的名字本来就在说这件事；而在下拉里挂一个「— attending」是**没有后果的行话** ——
真正值得告诉报名者的后果（不记工时、不问身份）是 L1.3 才落地的，
两件事应该一起出现。所以那一格移到 L1.3。

### 测试（都放 `events/tests.py`）

裸名列在这里，实施时按现有分类塞进对应的 TestCase：

- `test_a_new_role_defaults_to_helping`
- `test_the_catch_all_role_is_a_helping_one`
- `test_adding_a_role_from_the_page_asks_which_kind_it_is`
- `test_a_role_added_from_the_page_records_the_kind_that_was_chosen`
- `test_a_duplicate_name_says_what_kind_the_existing_role_is`
- `test_changing_the_nature_of_a_role_with_signups_is_refused`
- `test_changing_the_nature_of_a_role_nobody_used_is_allowed`
- `test_the_roles_panel_says_which_kind_each_role_is`

---

## L1.2 `ServedAs` 加第三档 `not_applicable`，配一条约束

### 落库的形状

```python
    class ServedAs(models.TextChoices):
        VOLUNTEER = "volunteer", "Volunteering"
        WORK = "work", "Scheduled work"
        # ⚠️ 不是第三种身份，是「这个问题在这一行上不成立」。attending 的角色
        #    不记工时，于是身份不问、不存 —— 但空值已经有含义了（这一行早于
        #    D38，回填证不出来，见迁移 0014），两个事实不能挤进同一个空值。
        #
        # ⚠️ 它永远不出现在任何表单上，只由 services.set_served_as() 写入，
        #    且 declared_by 留空：没有人声明过它，是结构决定的。
        NOT_APPLICABLE = "not_applicable", "Not applicable"
```

`SERVED_AS_EXPLANATIONS` 仍然只有两条，并在上面补一句：这张表是「问人的时候怎么说」，
而 `not_applicable` 从不问人，所以它不在这里。

它旁边再加一个函数，让「哪几档可以给人选」**由构造决定，而不是靠三处各排除一次**：

```python
def askable_served_as():
    """人可以被问到的那几档 —— (value, label) 对。

    就是 SERVED_AS_EXPLANATIONS 的键：一个没有「问法」的值，就不是一个会被问到的值。
    NOT_APPLICABLE 因此进不了任何一个下拉框，不是因为三个地方各记得排除它一次。
    """
    return [(value, Participation.ServedAs(value).label)
            for value in SERVED_AS_EXPLANATIONS]
```

⚠️ 三处调用方全部改读它（见下面那张表）。这不是包装：把「可选集合」写成
`ServedAs.choices` 减去一档，等于让每一个新加的档默认可选，
而这一档的性质恰好相反 —— 默认不可选，除非有人给它写了问法。

### ⭐ 这一档换来一条真正的数据库约束

「`attending` 不记工时」原来是跨表条件（工时在 `Participation`、性质在
`ParticipationRole`），和 [D19](decisions/D19-event-role.md) 判掉 `Participation.event`
是同一格 —— `CheckConstraint` 表达不了。加了这一档之后判据落在本行上：

```python
            models.CheckConstraint(
                condition=(
                    ~models.Q(served_as="not_applicable")
                    | models.Q(hours__isnull=True)
                ),
                name="participation_no_hours_when_not_applicable",
                violation_error_message="A place somebody attends does not record "
                                        "hours — they were not giving time.",
                violation_error_code="participation_hours_when_not_applicable",
            ),
```

三点写死：

- 字面量 `"not_applicable"`，不写枚举 —— 和旁边 `models.Q(status="attended")` 同一种写法；
- 不放行 `hours=0`。零工时是一句声明（「他来了，干了零小时」），
  而这一行要说的是「这里根本不问工时」。旁边那条
  `participation_hours_only_when_attended` 放行 0，是因为那条讲的是另一件事；
- 按 [D14](decisions/D14-constraint-is-the-only-rule.md) 的规矩，加约束是三件事，
  第三件是 `core/constraints.py` 的 `CONSTRAINT_FIELD` 加一行
  `"participation_hours_when_not_applicable": "hours"`。
  忘了它 `ConstraintMappingGuardTests` 当场变红。

诚实的边界要写在 docstring 里：这条约束挡的是「已经写了 `not_applicable` 的行再被塞工时」，
挡不住「`bulk_create` 给一个 attending 的工种写了一行空 `served_as`」。
那一半仍然是服务层的事，[D14](decisions/D14-constraint-is-the-only-rule.md) 要求这句话写出来而不是省略。

⚠️ 还有一句要写出来：**这一步结束时，这条约束一行都拦不到** ——
写 `not_applicable` 的人是 L1.3。这不是「先加了个没用的东西」，
而是本项目一贯的顺序：先让词汇和保证落地、再接写入路径。
不写这句的后果是下一个人跑完 L1.2 的测试，发现约束从没在真实流程里触发过，
于是怀疑它是不是接错了。

> ### 2026-09-08 补：这条约束**到不了场次那一层**
>
> [L5.2](#l52-sessionattendance他哪几场来没来干了多久) 的 `SessionAttendance`
> 复制了这张表的五条规则，唯独这一条复制不了：它能成立**全靠行上存着**
> `not_applicable`，而那一档是身份轴的一部分，整期声明一次，不往场次上搬。
> 于是那张表上没有任何一列可供 `CheckConstraint` 检验，那条规则在下面一层
> 只能是 `clean()` 加服务层。
>
> 写在这里是因为「这一档换来一条真正的约束」这句话本身没错，
> 但它换来的是**这一张表上**的一条约束 —— 不是一条会跟着形状一起复制下去的保证。

### 迁移

`events/migrations/0017_served_as_not_applicable.py`：

- `AlterField` × 2（`Participation.served_as` 和 `HistoricalParticipation.served_as`，
  choices 变更，对 Postgres 是空操作）；
- `AddConstraint`。⚠️ 现有数据不可能违反它（没有任何一行是 `not_applicable`），
  但迁移 docstring 里要写明这句话，因为下一个人看到 `AddConstraint` 第一个念头就是「会不会炸」。

### 加这一档会当场打断三处，其中一处是 import 期就炸

核对时逐个 grep 出来的。第一处不改，`events` 这个 app 根本 import 不了：

| 位置 | 现在 | 会发生什么 |
|---|---|---|
| `events/forms.py` · `SignUpForm.served_as` 的 choices | 列表推导里写着 `SERVED_AS_EXPLANATIONS[value]`，遍历 `ServedAs.choices` | 第三档没有对应的注解 → `KeyError`，而且它在**类体**里求值，所以是 import 期就炸。改成只遍历 `SERVED_AS_EXPLANATIONS` 的键 —— 那张表本来就是「要问人的那两档」的定义 |
| `events/views.py` · `event_registrations` 的 `served_as_choices` | `Participation.ServedAs.choices` 整份传给模板 | 更正下拉里会多出一项 Not applicable，admin 点得到，而它一点就把一行本来正常的记录改成「不适用」 |
| `events/views.py` · `event_registrations` 的 POST 分支 | `if value in Participation.ServedAs.values` | 同上，只是从 POST 进来。⚠️ 不画控件是界面，界面挡不住任何人 —— 这两处要一起改，只改一处等于没改 |

三处全部改读 `askable_served_as()` / `SERVED_AS_EXPLANATIONS`（见上）。

⚠️ 模板 `event_registrations.html` 那个下拉**不用改**：它已经在遍历
`served_as_choices`，而那份名单在视图里换掉了。写下来是因为「改了三处、模板忘了」
和「模板本来就不用改」在 diff 上长得一样，而下一个人会去找第四处。

### 空值的含义因此恢复单一

迁移 0014 那段「空 = 这一行早于 D38」一个字不用改。这是选第三档而不是复用空值的全部收益。

### D38 的改口在这一步做，不留到批一末尾

改动落在两节，各自是那个事实的家，不合并成一处：

- [D38 第五节](decisions/D38-served-as-volunteer-or-work.md)（默认值那张表）加一行：
  `attending` 的角色 → 记 `not_applicable`、不问。它是一条**默认值规则**，
  而那一节就是默认值规则的家；
- [D38 第九节](decisions/D38-served-as-volunteer-or-work.md) 补一句确认：
  第三档**不动摇**那句「空值只可能来自那一次回填」——
  恰恰相反，它是为了保住那句话才加的。

⚠️ 第六节（措辞）一个字不动，而这是结论不是遗漏：那一节是「问人时怎么说」的唯一的家，
而这一档从不问人，所以它在那里没有位置。

### 测试（都放 `events/tests.py`）

- `test_the_database_refuses_hours_on_a_row_marked_not_applicable`
  —— 直接 `Participation.objects.create(...)`，绕开服务层，验的是约束本身
- `test_clearing_the_hours_lets_a_not_applicable_row_save`
  —— 另一半，否则上一条也可能只是因为别的约束在拦
- `test_the_signup_form_offers_only_the_two_identities_a_person_can_claim`
- `test_the_correction_dropdown_does_not_offer_not_applicable`
- `test_the_registrations_page_refuses_a_posted_not_applicable`
  —— ⚠️ 这一条不能省：上一条只证明控件里没有它，而控件挡不住任何人

---

## L1.3 三条工时写入路径，各自拒绝

### `default_served_as()` 改签名，仍然只有一处判断

```python
def default_served_as(contact, event_role, *, on_the_books=None):
    """(记什么, 问不问) —— 现在按角色答，不按活动答。

    ⚠️ 两列，不是一列。D38 第五节那张表有两列且它们互不同意，这一点没有变，
       只是多了一个更早的分支。
    """
    if event_role.role.nature == ParticipationRole.Nature.ATTENDING:
        return Participation.ServedAs.NOT_APPLICABLE, False
    if on_the_books is None:
        on_the_books = _on_the_books(event_role.event).filter(contact=contact).exists()
    return Participation.ServedAs.VOLUNTEER, on_the_books
```

`on_the_books` 是给表单用的**预算好的答案**，不是第二条规则：
表单先问一次在编（一次查询），再对每个角色调这个函数（零查询）。
判据本身仍然只有这一处。

### 「这一行记不记工时」只许有一种问法，而它问的是角色不是本行的列

⚠️ 本节初稿写的是「`record_hours()` 判 `participation.served_as == NOT_APPLICABLE`，
和约束同一个判据，两边不可能有分歧」。**那个判据挡不住它要挡的行**：
一行落在 attending 角色上、而 `served_as` 从没被写过（`bulk_create`、导入脚本、
或者任何早于本步的行），它既不等于 `not_applicable`，也就一路走到记工时那一步 ——
而新加的那条约束同样不管，因为那一行没说自己是 `not_applicable`。

真相在角色上，`not_applicable` 只是它被记下来的**后果**。所以两层各问各的，
而且**服务层问的那一层更宽**：

| 层 | 判据 | 挡的是 |
|---|---|---|
| 服务层 | 角色的 `nature` | 一切落在 attending 角色上的行 |
| 数据库约束 | 本行的 `served_as` | 已经写了 `not_applicable` 的行再被塞工时 |

这不是两份规则打架，是同一条规则的两个观察点，且窄的那个是兜底。写出来是因为
「服务层比约束宽」看起来像 bug，而它是有意的。

落成一个属性，全仓只有这一处拼写：

```python
    @property
    def records_hours(self):
        """False 表示这是一个「来参加」的位置 —— 活动侧不记工时。

        ⚠️ 走 event_role.role，所以按行渲染它的页面必须 select_related
           ("event_role__role")，否则每行一次查询。今天两处调用方都带了。
        """
        return self.event_role.role.nature != ParticipationRole.Nature.ATTENDING
```

### 表单：一个活动可能同时有两种角色

`SignUpForm.__init__` 现在按「这场活动的角色里**有没有** helping 的」决定画不画那道题。
代价如实写在注释里，并配一条测试：

> 一场同时开了 helping 和 attending 角色的活动，那道题会被画出来；
> 选了 attending 的角色提交时，服务层忽略答案、写 `not_applicable`。

这不是新规矩 —— `sign_up()` 里那句「caller 传来的是请求不是指令」本来就在做这件事，
现在多覆盖一个分支。想让题目跟着下拉框动，是 Alpine 的渐进增强（[D24](decisions/D24-htmx-alpine-tailwind.md)），
不在本轮。

### 三条路径

| 函数 | 改法 |
|---|---|
| `record_hours()` | 开头判 `not participation.records_hours` 就 raise 一个新的 `NoHoursHere(ValidationError)`，消息说得出为什么（「这个位置是来参加的，不记工时」） |
| `check_out()` | 仍然写 `checked_out_at`，但跳过「按时长算工时」那一段。attending 的人来了、走了，这两个时刻是真的，工时不是。⚠️ 加在那个 `if` 的最前面一个条件即可，后面「有工时才 `_mark_attended`」那一段因此自然不触发 |
| `check_in()` | 一个字不动。它不碰工时 |

⚠️ `clear_hours()` / `undo_attendance()` 把 `hours` 置 `None`，两条都不违反新约束，不动。

### 页面

| 文件 | 改什么 |
|---|---|
| `events/templates/events/_attendance_row.html` | attending 的行不画工时输入框、不画 Enter hours / Clear，签到签退照旧。⚠️ 原地要**留一句话说明为什么**：一个控件凭空消失读起来是页面坏了，人会去刷新、然后去别处找。⚠️ 这一份被 `_attendance_row_swap.html` include，所以只改这一处 |
| `events/views.py` · `event_attendance` | 不加判断（薄壳），但 queryset 已有的 `select_related("event_role__role")` 现在是 `records_hours` 的前提，注释里点明，别哪天被「优化」掉 |
| `events/templates/events/event_registrations.html` | 身份更正的控件改读新的按行集合（见下），模板里不写第二个条件 |
| `events/views.py` · `event_registrations` | 同上，POST 分支读同一个集合 |
| `events/templates/events/my_participations.html` | `{% if row.served_as %}` 会把 `Not applicable` 印出来。加上 `and row.records_hours` |
| `events/templates/events/event_report.html` | 第 76 行同一个问题，同一个改法 |

### `contacts_asked_about_serving()` 换成按行问，因为问题本身变成了按行的

「这个人在这一行上要不要被问身份」现在有两个条件（他在编 **且** 这个角色是 helping），
而它们分散在模板和 POST 两处的话，就是同一条规则的两份拷贝 —— 本项目判过多次的形状。

```python
def signups_asked_about_serving(event):
    """这场活动里、要问身份的那些**报名行**的 pk。default_served_as() 的集合形式。"""
```

⚠️ 返回的从 contact id 变成 participation pk，模板那一句 `row.contact_id in …`
跟着变成 `row.pk in …`。改名是有意的：留着旧名字而换掉语义，
是让下一个人读到一个说谎的名字。

### `RoleChoiceField`：L1.1 推到这一步，这一步**判它不做**

L1.1 把「报名下拉里标出档位」推到了这里，理由是「后果要和标签一起出现」。
现在后果落地了，重新看这一格，结论是**不做**：

- 详情页的角色表已经有 Kind 那一列（L1.1 加的），「这是什么」已经答过；
- 在下拉里挂一个「— attending」是分类学名词，而人在那一刻要的不是分类；
- 真正的后果对一个来占座位的人**不是他关心的事** —— 那是机构记账的事。

> ### 2026-09-08 更正：上面那句原来写的是「不记工时…不是他关心的事」
>
> 结论不变（下拉里仍然不挂分类学名词），但那半句话不成立了。
> [D43](decisions/D43-hours-given-and-hours-received.md) 之后，一个来占座位的人
> **恰恰关心自己被服务了多久** —— 成人教育那一行按 contact hours 报表，
> 而基金会走查时提的正是这句话。他不关心的是 `hours` **那一列**：
> 那一列装的是他给出去的时间，而他给出的是零。
> 两个数方向相反，所以「不记工时」从来不等于「他的时间不算数」。

真正需要说明的是另一件事，而它不在下拉里：一场同时开了两种角色的活动，
那道身份题会被画出来，但选了 attending 的角色时它被忽略。
所以补的是**那道题自己的一句 help text**（「只在你付出时间的角色上才问这一句」），
一句话，没有行话，正对着会让人困惑的那一格。

### 测试

- `test_recording_hours_on_a_place_somebody_attends_is_refused`
- `test_a_row_on_an_attending_role_with_no_identity_written_is_still_refused_hours`
  —— ⚠️ 这一条是判据从 `served_as` 换成 `nature` 的全部理由，缺了它那次换回去不会红
- `test_checking_out_of_an_attending_role_records_the_time_but_no_hours`
- `test_the_identity_question_is_not_asked_when_every_role_is_attending`
- `test_the_identity_question_still_appears_when_one_role_is_a_helping_one`
  —— 混合活动那一格，也就是上面主动接受的那条代价
- `test_signing_up_for_an_attending_role_records_not_applicable_and_credits_nobody`
- `test_choosing_an_attending_role_ignores_an_identity_sent_by_hand`
- `test_the_attendance_page_offers_no_hours_box_for_a_place_somebody_attends`
- `test_the_attendance_page_says_why_the_hours_box_is_absent`
  —— 实施时补的：只断言「框没了」的话，把那句说明删掉测试照样绿
- `test_the_signups_page_offers_no_identity_control_on_an_attending_row`
- `test_the_correction_control_is_not_offered_on_a_place_somebody_attends`
  —— 服务层那一侧，和上面那条页面级的成对
- `test_an_admin_cannot_correct_the_identity_on_an_attending_row`
- `test_my_signups_does_not_print_not_applicable`

（约束本身那两条在 L1.2，不在这里 —— 它是 L1.2 的交付物）

---

## L1.4 报表三处口径（单独一步，因为它改答案）

先把在编判据抽成三个层次，`events/services.py`：

```python
def on_the_books_q(on):
    """「这一天算基金会自己人」的 Q，over Assignment。判据的唯一一份。"""
    return (models.Q(position__kind=Position.Kind.STAFF)
            & models.Q(position__is_active=True)
            & in_effect_on(on=on))


def _on_the_books(event):
    """原来的那个，现在是上面那条的第一个调用方。行为一个字不变。"""
    return Assignment.objects.filter(on_the_books_q(local_date_of(event.start_time)))


def on_the_books_exists(*, contact_ref, day_ref):
    """同一条判据的关联子查询形式 —— 给「一批活动」用，判的是各自那一天。"""
    return models.Exists(Assignment.objects.filter(
        models.Q(contact_id=contact_ref) & on_the_books_q(day_ref)))
```

### ⚠️ 本节初稿那段代码跑不起来，实测过了

初稿写的是 `TruncDate(event_ref, tzinfo=foundation_timezone())`，其中 `event_ref`
是一个 `OuterRef`。**它当场抛异常**：

```
AttributeError: 'ResolvedOuterRef' object has no attribute 'output_field'
```

`TruncDate.resolve_expression()` 要读 lhs 的 `output_field` 来决定截断成什么类型，
而 `OuterRef` 在解析那一刻还没有类型。两条补救都实测通过、答案一致（dev 库上同为 17）：

| 写法 | 形状 | 取舍 |
|---|---|---|
| A | `TruncDate(ExpressionWrapper(OuterRef(...), output_field=DateTimeField()), tzinfo=…)` | 能跑，但那层 `ExpressionWrapper` 纯粹是绕 Django 的一个限制，读的人看不出它为什么在 |
| B ✅ | 外层先 `annotate(event_day=local_day(...))`，子查询只 `OuterRef("event_day")` | 时区转换出现在**调用点**、看得见；子查询退化成一个普通列引用 |

选 B，还有第三条理由：L2.2 的 `for_audience()` 外层是 `Event`、L1.4 这里外层是
`Participation`，B 对两者是同一个形状，A 要各写一遍字段路径的包装。

⚠️ B 的代价如实说：调用方必须先 annotate。忘了的表现是 `FieldError` ——
**吵，不是静默**，所以这个代价可以接受。

### `local_day()` 落在 `core/timeutils.py`，而不是新造一个 `foundation_timezone()`

```python
def local_day(field):
    """一个数据库表达式：这个时刻落在基金会时区的哪一天。local_date_of() 的 ORM 双胞胎。"""
    return TruncDate(field, tzinfo=timezone.get_current_timezone())
```

初稿要加的 `foundation_timezone()` 只是把 `get_current_timezone()` 换个名字，
调用方仍然要自己记得写 `TruncDate(..., tzinfo=...)` —— 而**忘掉 `tzinfo` 才是那个不报错的错**
（D16：下午 5 点之后的活动整个跳到第二天，R8 已经为它付过一次）。
包成 `local_day()` 之后 `tzinfo` 没有地方可忘。

⚠️ 它和 `local_date_of()` **紧挨着放**：同一个问题的两个实现（一个给一行、
一个给一批），这是 `core/querysets.py` 里 `active()` / `is_currently_active`
那条注释的规矩，本轮第三次用它。

⚠️ `in_effect_on()` 现在会收到一个数据库表达式而不是 `date`。
实测确认 `on or local_today()` 不会把它吃掉（表达式对象为真），
但 docstring 要补一句说明它有两种入参 —— 否则下一个人会以为那是 bug。

### 三处改动 → 实际是四处

⚠️ 不新造 `HELPING` 这个 Q 常量：L1.3 已经落了
`ParticipationQuerySet.recording_hours()`，而「这一行记不记工时」正是下面头两个
指标要问的那句话。新造一个常量就是同一条判据的第四种拼写。
只在 `events/models.py` 加一个模块级的 `ATTENDING` Q，让
`attending()` 和 `recording_hours()` 共用一份字面量、两个方向。

| 指标 | 现在 | 改成 | 为什么 |
|---|---|---|---|
| `hours_missing` | `signups − hours_records` | `parts.recording_hours().count() − hours_records` | 现在每一个不记工时的人都被算成「缺一条工时记录」，而那个数会一直涨。这是本轮修掉的第一个静默 bug |
| `hours_per_participant` | `hours / participants` | `hours / recording_hours() 里的 distinct contact` | L1 一上线，ESL 学员进了分母却永远不贡献分子，人均工时会被稀释，而且不报错 |
| `people_served` | 不存在 | `attending 的参与 × 活动当天没有在职 Assignment 的人` | [D38 第七节](decisions/D38-served-as-volunteer-or-work.md) 说的那两个问题，这是第二个第一次答得出来。乘号右边不能省：没有它，来听讲座的员工会被算进「我们服务了 N 位社区成员」 |
| `fully_staffed` / `staffable_events` | 数所有开了人数的角色 | 只数 **helping** 的角色 | 初稿漏了这一格，见下 |

> ### 2026-09-08 补：这四个口径全都只管一个方向
>
> 上面四格改的都是「别人给基金会的时间」那个账本。
> [D43](decisions/D43-hours-given-and-hours-received.md) 之后还有一个反方向的数
> ——「基金会花在他身上多少时间」—— 它**不进这张表的任何一格**，
> 而是并排加一个数，落点在 [L5.7](#l57-l14-那几个工时口径要改决定-20-的代价)。
>
> ⚠️ 尤其是 `hours_per_participant` 那一格：它把 ESL 学员从**分母**里拿掉，
> 是对的（他不贡献分子）。但那不等于这些人的时间无处可去 ——
> 它去了另一个数，而两个数永远不相加。

### ⚠️ 第四处：满员率，而 D27 自己已经写好了它的理由

[D27 那张「四个数字带着自己的注脚」的表](decisions/D27-ministry-report.md)里，
满员率那一行的注脚原文是：

> 分母混进没开工种的活动，比率会因为**量不出来**而变低，读起来像缺人

一门开了 12 个座位、来了 3 个学员的 ESL 课，对这个比率做的正是同一件事 ——
它会把「课没招满」算成「志愿者不够」。那不是同一个问题，
而这个数字是印在报表上给基金会看「我们缺不缺人手」的。

所以 `staffable` 和 `short_events` 两个集合都收窄到 helping 的角色：

- 只开座位的 ESL 课 → **整场不进分母**（它没有可量的人手需求）；
- 混合活动（12 座位 + 1 翻译）→ 因为翻译进分母，只有翻译缺人时才算没满。

⚠️ 这是本步的第四个会静默改答案的口径，所以它和前三个一样，配一条钉死的测试。

`people_served` 的写法：

```python
def _people_served(parts):
    return (parts.attending()
            .annotate(event_day=local_day("event_role__event__start_time"))
            .exclude(on_the_books_exists(
                contact_ref=models.OuterRef("contact_id"),
                day_ref=models.OuterRef("event_day"),
            ))
            .values("contact_id").distinct().count())
```

`participants` 一个字不动 —— 它是「所有参与过的人」，含带薪员工，
2026-08-20 那次改名已经把它的名字改对了，口径不再动第二次。

### 报表页

`events/templates/events/_report_body.html`（面板和完整版报表页共用这一份）：

- 「Hours per participant」下面补一行小字，写明分母是「帮忙的人」；
- 「Recorded hours」那行的 `hours_missing` 句子跟着改口（现在漏掉了「只数帮忙的人」）；
- 新增一格 `People served`，和 `Participants` **并排、不相加**，
  并在下面写一句「不含来参加的在编员工」。两个数并排不相加是
  [D36](decisions/D36-two-hour-ledgers.md) 的唯一不变量在报表上的第二次应用。

### 不动的那些，逐个写下来 —— 「顺手一起收窄」是这一步最容易犯的错

| 不动的 | 为什么 |
|---|---|
| `signups` | 它数的是报名人次，来参加的也是报了名的 |
| `participants` | 「所有参与过的人」，含带薪员工也含学员。2026-08-20 那次改名刚把它的名字改对，口径不再动第二次 |
| `repeat_rate` | 连着来两堂课的学员就是回头客，这个数答的是「这个时间段有没有养成习惯」，对两种人一样成立 |
| `minors_without_consent` | 一个没有同意记录的未成年学员，正是这个风险指标要抓的 |
| `_absence()` | 来参加的人也会不来，缺勤率对他们同样成立 |
| ~~`_top_participants()`~~ | ~~按工时排，attending 的工时是 `None`，现有的 `nulls_last` 已经把他们排在外面，而且它本来就叫 Most hours~~ 2026-08-27 推翻，见下 |
| ~~`hours_by_role` 图~~ | ~~它已经 `.exclude(hours__isnull=True)`，attending 的角色不可能有工时，所以一行都不会出现~~ 同上 |
| `_role_gap` 图 | 它是**按角色**画的，「ESL seat：要 12，来了 3」这一行准确且有用。混在一起会出错的是那个**比率**，不是这张图 |

> ### 🔴 2026-08-27 更正：上表最后两行的理由不成立，而分子分母原来取自两个集合
>
> 上表里那两行给的理由是同一句话 ——「attending 的行不可能有工时」。
> 而 [L1.3 自己](#这一行记不记工时只许有一种问法而它问的是角色不是本行的列)
> 花了一整节论证的正是这句话**靠不住**：一行落在 attending 角色上、`served_as`
> 从没被写过（`bulk_create`、导入脚本、任何早于本步的行），
> 那条约束抓不到它（它没说自己是 `not_applicable`），于是它带得动工时。
> `records_hours` 比约束宽，全部理由就在这里 —— 而报表这一侧当时没有跟上。
>
> 于是本步交付的状态是：**分母收窄了，分子没有。**
>
> | 数 | 原来取自 | 后果 |
> |---|---|---|
> | `hours`（Recorded hours） | `parts` | 那一行的工时进了总数 |
> | `hours_records` | `parts` | 同上 |
> | `hours_per_participant` | 分子 `parts` ÷ 分母 `recording_hours()` | **一个比率的上下取自两个集合** —— 按构造就是错的 |
> | `hours_missing` | `recording_hours().count() − parts 的 hours_records` | 减数比被减数多，**这个数会变成负的**，而页面照印不误 |
>
> 改法：`helped = parts.recording_hours()` 提到最前面，
> 所有和工时有关的数**全部**从它来，`totals` 只留 `signups` / `participants`。
> 「哪个数来自哪一个集合」于是从写法上看得出来，而不是要逐个参数去查有没有 `filter=`。
>
> ⚠️ 三张图必须跟着走，其中「Recorded hours by month」是**不能留下的那一张**：
> 它就印在工时总数下面，本来就是那个总数的分解。只收窄数字不收窄图，
> 屏幕上会同时出现一个读 4.00 的总数和一张加起来是 13 的图。
>
> ⚠️ 诚实的边界，照 [D14](decisions/D14-constraint-is-the-only-rule.md) 的规矩写出来：
> 这么改等于**把那一行藏起来**，而不是报出来。对一页比率来说这是对的取舍，
> 但代价是这一页从此没有任何一个数能让人发现那种行的存在。
> `event_summary()` 的 R6 仍然逐行数、并且把工时挂在它自己的角色名下 ——
> 那才是它看得见的地方。**本次不动它**：收窄 R6 是在改一条需求编号的口径，
> 属于另一步，而「顺手一起收窄」正是本节标题在警告的那件事。
>
> 补三条测试（`MinistryReportTests`）：
> `test_a_stray_attending_row_with_hours_does_not_inflate_the_total`、
> `test_hours_missing_cannot_go_negative`、
> `test_the_hours_charts_leave_the_stray_row_out_too`。
> 三条都用同一个 fixture 造那一行（直接落库、`served_as` 留空、
> `status=attended` 以免撞上另一条**真的**约束）。

### 测试

- `test_attending_signups_are_not_counted_as_missing_hours`
- `test_hours_per_participant_counts_only_people_who_helped`
- `test_people_served_counts_the_esl_class`
- `test_people_served_excludes_staff_who_came_to_the_lecture`
- `test_people_served_judges_staff_on_the_day_of_the_event_not_today`
- `test_people_served_counts_somebody_with_two_posts_once`
- `test_a_class_that_did_not_fill_is_not_counted_as_understaffed`
- `test_a_mixed_event_is_still_judged_on_its_helping_roles`
- `test_participants_still_counts_everybody`（钉住不动的那个）

---

## L1.5 演示数据与批一验收

工种和活动 L1.1 已经落了（`code="esl-seat"` + 一场 ESL 课，同时开座位和翻译两个角色）。
这一步只剩**报名**，而选谁不是随意的 —— 每一个人都要扛一条验收。

### 四个报名，逐个说明它为什么是这个人

| 谁 | 报哪个角色 | 它让哪一条验收走得了 |
|---|---|---|
| Li Si（`participant_adult`，有账号，外部） | ESL seat | 进 `people_served`；**且他登得进去**，所以「我的报名上不印身份文字」这条能真的走一遍 |
| Ada Okafor（`staff_unpaid`，有账号，在编） | ESL seat | `people_served` 要排除的正是她。同样有账号，所以两边都看得见 |
| Sam Noreach（无账号，外部） | ESL seat | 凑出「3 个报名 → People served = 2」这个**在页面上看得出来**的差 |
| Rafa Silva（`intern`，在编） | Interpreting | 见下一节 |

### ⭐ 第四个报名是初稿漏掉的，而它是本轮最值得看的一屏

初稿只写了三个报名，全在座位上。那样演示数据**演不出这一轮的核心不变量**。

Rafa 和 Ada **都在编**，都在同一场活动里，而签到页上：

| | Ada（ESL seat） | Rafa（Interpreting） |
|---|---|---|
| 工时框 | 没有，且写着为什么 | 有 |
| 身份题 | 不问 | 问 |
| 进 `people_served` | 否（她在编） | 否（他是来帮忙的） |

同一场活动、两个同样在编的人、**待遇相反，而区别只来自角色** ——
这正是 [`participants.md` 第四节](participants.md) 那条唯一的不变量
（「轴贴在角色上，永远不贴在人上」）在屏幕上的样子。
一屏看得见，比文档里那句话有用得多。

### ⚠️ 初稿那句关于 `AcceptanceWalkTests` 的警告不成立，收回

初稿写着「演示数据一动，断言总工时的那一条会跟着红」。**这一次不会**：
那条断言（`total_hours == 15.00`）问的是**上个月那场发放日**的 `event_summary()`，
而这一步加的报名全在 ESL 课上、且一条工时都不带。

⚠️ 收回归收回，那条警告背后的规矩仍然成立（演示数据是耦合点，动它要先想清楚谁在断言它）。
错的是「这一次会红」这个预测，不是那条规矩。以后照抄这句话之前先问一句：
新加的数据**落在哪场活动上、带不带工时**。

### 顺带结清批一欠 [`participants.md`](participants.md) 的两笔

[改口清单](#要改的文档)里列的那两条到期了，批一交付前做掉：
第十节加一段批一的执行记录，第十一节把批一交付的那几条打勾
（其余的属于批二 / 批三，留着）。

### 验收

代码这一侧：

- 全量测试绿，测试数只增不减（[口径见 `phase-c.md`](phase-c.md#测试数基线只增不减的新口径)）
- 给 ESL 座位记工时 → 被拒，且消息说得出为什么
- 直接 `Participation.objects.create(...)` 塞一个 `not_applicable` + 工时 → 数据库拒绝
- 一门只开座位的课**不进**满员率的分母；混合活动进，且只按 helping 的角色判

浏览器这一侧（dev 库真数据，照[第十节那五条](participants.md)的规矩）：

- admin 的工种列表：五行，只有 ESL seat 是 Attending，其余四行 Helping
- 活动详情页和管理页的角色表都有 **Kind** 那一列，ESL 课上两行分别写着 Attending / Helping
- ESL 课的签到页：Ada 那一行**没有**工时框、且写着为什么；Rafa 那一行**有** ——
  两个人都在编，区别只来自角色
- ESL 课的报名管理页：Rafa 那一行有身份下拉，Ada 那一行没有
- 报表页出现 **People served = 2**，而 ESL seat 那个角色显示 **3 人报名** ——
  差的那一个正是在编的 Ada
- 报表页的 Hours per participant 在加了这场 ESL 之后**没有**下降；
  `hours_missing` 和满员率的分母也没有动
- 用 `lisi@example.invalid` 登录 →「我的报名」上 ESL 那一行**不印任何身份文字**；
  用 `ada@example.invalid` 登录 → 同样不印（而她在别的活动上仍然印着
  `Scheduled work · Set by an admin`，两种状态在同一个人身上并排）

---

## L1.6 兜底工种拆成两个（2026-08-26 追加）

批一交付之后走查时点出来的一处：库里那行「没有特定工种」的兜底
（`code="general"`）是 **helping**，于是**说不清具体在接受什么服务的受助者没有位子可落**。
发放日那种「就是来领东西、没有具体名目」的场合会立刻撞上。

批一里我判过这一格，当时的结论是「不预先建，管理员一次点击就能加」。
那个判断在**只有一个兜底**的前提下成立；现在看它不成立 —— 让每个基金会自己去发现
「原来还要再建一行」，等于把一个系统级的缺口摊派给用户。

| code | 名字 | 档位 |
|---|---|---|
| `general`（一个字不动） | General participant (helping) | Helping |
| `general-attending`（新） | General participant (attending) | Attending |

⚠️ 老那行的 `code` 不许改：它是 `ImmutableCodeMixin` 的列，
`ParticipationRole.GENERAL_CODE` 被代码按名字引用，迁移 0003 的 `get_or_create`
也是按它匹配的。能改的只有显示名（[D5](decisions/D05-lookup-tables-not-enums.md)：
显示名归基金会，只有 `code` 是钉死的）。

### 跟着改的地方

| 位置 | 改什么 |
|---|---|
| `ParticipationRole` 的类 docstring | ⚠️ 复查时才发现的一处：它现在白纸黑字写着「No second catch-all is seeded, **deliberately**」—— 也就是模型文件自己在替这一步推翻的那个决定辩护。不改的话，代码和它旁边的迁移互相矛盾 |
| `GENERAL_CODE` + `seed_general()` | 兜底从一行变两行，「那一行」这句话有歧义了。⚠️ 查证：`GENERAL_CODE` **全仓只有 `models.py` 自己读**（迁移 0003 有自己的局部拷贝），所以影响面比看上去小。但迁移 0015 的 docstring 声称它「被代码按名字引用」，删掉它那句话就变假 —— 常量留着，API 改成按档位取 |
| 新迁移 `0018`（数据迁移） | 建 `general-attending`，并把老那行改名。⚠️ 只改**种子原文**那一个字符串，基金会自己改过名的不动 —— 照抄 0015 的规矩 |
| `core/management/commands/check_deployment.py` | 那里写着「工种表至少 2 行」，注释解释是「迁移送 1 行 + 基金会自己加了至少 1 行」。⚠️ 送 2 行之后门槛要提到 3，否则这条自检从此什么都不检查，**而且不报错** |
| `seed_demo.py` | 见下 —— 这一格比「给新那行一个用处」大 |

### ⚠️ 演示数据里没有一个纯粹的受助者，而这一轮讲的正是那批人

复查时数了一遍现在的 cast：三个管理员、四个外部志愿者（含两个未成年）、
一个联系不上的、一个离职的、两个在编。**没有一个人是只来接受服务的。**
ESL 课那三个座位坐的是「一个志愿者、一个联系不上的志愿者、一个在编员工」。

所以这一步要补的不是「给新那行找个用处」，是**补上那个人群**：
两位只来领取的社区成员，报上个月那场发放日的「General participant (attending)」，
签到、不记工时。

⚠️ 2026-09-08 补一句，因为它正是 [D43](decisions/D43-hours-given-and-hours-received.md)
第五节最容易被写错的那一格：那场是**发放日，没有讲次**，所以
「基金会花在他身上多久」这个问题在那两行上**不成立** —— 不是 0。
一个人来领了一箱食物，说他「接受了 0 小时服务」是错的，
而 `Sum` 的空值离被渲染成 `0` 只差一个 `or 0`。

三件事一次到位：新那行有了真读者；`people_served` 在演示库里第一次反映
**一门课 + 一场发放**的混合，而不是只有课；以及界面上第一次出现一个
「既不是志愿者、也不是员工」的人 —— 而 [第四节那条不变量](participants.md)
（轴贴在角色上、不贴在人上）说的就是这批人不该被贴标签。

### 测试

- `test_there_is_a_catch_all_role_for_each_kind`
- `test_the_helping_catch_all_keeps_its_code`
  —— ⚠️ 这条钉的是「`code="general"` 一个字没动」，而那正是最容易顺手改掉的一格
- `test_both_catch_all_names_say_which_kind_they_are`
  —— 这一步存在的**全部理由**：并排放着看得出区别。只测「有两行」测不到它
- `test_a_foundation_that_renamed_the_catch_all_keeps_its_own_name`
  —— 迁移只改种子原文那一格，照抄 0015 的规矩
- `test_the_deployment_check_still_asks_for_a_role_of_the_foundations_own`
  —— ⚠️ 门槛从 2 提到 3 那一格。查证过：**字典表那几个门槛现在一条测试都没有**，
  所以这不是补一条，是第一条

---

# 批二 · L3 可见性与 L2 资格

## ⚠️ 2026-08-26：三档枚举被推翻了，改成多选。这一节整个重写

批二一行代码都还没写，所以代价只有这份文档。但**推翻的理由要留下**，
否则下一个人会以为多选是随手定的。

原来锁定的是一个字段、三档枚举（所有人 / 全体在编 / 本 ministry 在编），
其中「本 ministry」是**相对活动自己的 ministry**，所以不需要额外的外键 —— 很省。

它表达不了一句话：**「食物银行 + 报税互助，两个 ministry 一起看，别人不行」。**

⚠️ 这个代价在调研时就写下来了，原话是「枚举的代价要写下来：表达不了『A 和 B 两个
ministry 一起看』……出现真实需求时的升级路径是把三档换成 `AudienceRule` 表」。
所以这不是判断错了，**是那个重启条件到了** —— 联合培训、跨 ministry 的团建，
基金会说这是真实场景。

同时暴露了原来那三档缺一档：**「只给外部人员看」**。
原设计里「所有人」是最宽的一档，而基金会要的是一档**只有外部人员看得见、
在编的人看不见**的（只给受助者的物资发放，不想让员工报名占位）。
三档枚举里没有这个位置。

## L2.1 可见性是一组勾选，不是一个枚举

### 选项，以及它们之间的包含关系

```
谁看得见这场活动：
  ☐ 所有人（外部 + 全体在编）        ← 界面便利勾，见下
  ☐ 外部人员（没有在职任职的人）
  ☐ 全体在编                          ← 勾它，下面各 ministry 置灰
  ☐ Food Pantry 的在编
  ☐ Tax Help 的在编
  ☐ …每个在用的 ministry 一行
```

🔴 **「所有人」不是一个存储值。** 它在意思上恰好等于「外部人员 + 全体在编」两个都勾，
所以存成第三个值就是同一个状态两种写法 —— 而这一节下面那条置灰规则正是为了避免这个。

处置：勾「所有人」= 表单**替你勾上另外两个**，库里存的是那两项。
按 [D24](decisions/D24-htmx-alpine-tailwind.md) 这是纯界面增强（没有 JS 时手动勾两个，
一样能用），而「两个都勾了要显示成『所有人』」这个推导写在 Python 里，不写在模板里。

> ### ⚠️ 2026-08-27 补：这一节的界面部分整整一天只存在于注释里
>
> 上面那两句 —— 勾「所有人」替你勾上另外两个、勾了「全体在编」之后各 ministry 置灰 ——
> **两样都没实现**，而 `events/models.py` 和 `events/tests.py` 里有**三处注释用现在时
> 写着它们已经有了**。服务端那一半（`refuse_redundant_audience` 真的拒绝冗余组合）
> 一直是在的，缺的只是界面。
>
> 🔴 缺「所有人」勾的代价不是少一次点击，是**一个不报错的错误**：
> 想发一场公开活动的人会去勾「外部人员」—— 它读起来就是「外面所有人」，
> 于是也就读成了「所有人」—— 而那一勾把活动**对全体员工藏了起来**。
> 决定 10 那一档反直觉的地方正在这里，而界面上没有任何东西提示它。
>
> 两样都补上了。落点分得很清楚：
>
> | | 落在哪 | 没有 JS 时 |
> |---|---|---|
> | 「所有人」勾 | 纯服务端（`AudienceFormMixin`） | 照常工作 —— 它本来就是一个普通勾选框，服务端负责展开 |
> | 置灰 | Alpine 组件 `audienceTicks` | 照常能点，冗余组合由服务端拒绝并说清楚。少的是一次往返，不是功能（D24） |
>
> 🔴 **写回 `cleaned_data` 那两行是整件事的关键。** 规则读的是 `Spec`，
> 而落库的是 `ModelForm` 从 `cleaned_data` 里拿到的东西。只展开给规则看、不写回去，
> 表单会**校验一场「所有人可见」的活动、然后存下一场「谁都看不见」的** ——
> 从那条专门防这件事的规则旁边走过去，因为给它看的是另一个值。配了独立测试。
>
> ⚠️ 「所有人」是**推导出来的，不是存的**：打开一场存着「外部 + 全体在编」的活动，
> 表单上勾着的是「所有人」。少了这一步，这个便利勾就是一次性的 —— 人勾一次、
> 回来看到两个勾，就学会了不用它。推导写在 Python 里（决定 11 的原话），不写在模板里。
>
> ⚠️ 冗余那条错误消息按**人勾的那个框**分两句（照抄 `EMPTY_AUDIENCE_MESSAGE` 的形状）：
> 对一个勾了「所有人」的人说「『全体在编』已经包含每一个 ministry」，
> 说的是一个他从没碰过的框。
>
> ⚠️ Alpine 组件从 `$root` 里**按 name 找控件**，照 `passwordReveal` 那条注释定的规矩 ——
> 不靠 x-ref、也不往 `forms.py` 的 widget attrs 里塞 `x-` 属性（那里只放语义属性）。
> 🔴 代价是 JS 和表单之间有一份**没有任何东西连着的契约**：改掉字段名，
> 这里每一条测试照样绿，而两个页面上的置灰静默停止。所以配了一条测试
> **从 `app.js` 里把它要找的 name 读出来**，跟表单渲染出来的对；两边任一改名都变红。
>
> ⚠️ 灰掉的是**连标签一起**，class 由组件加、样式在 `app.css` ——
> 一个满对比度的标签配一个点不动的框，读起来是页面坏了。而且是变淡不是隐藏：
> 那些选项没有坏，它们是已经被包含了，取消上面那个勾立刻回来。

### 落库的形状

```python
class Event(...):
    #: 外部人员（没有在职任职的人）看得见
    visible_to_outsiders = models.BooleanField(default=False)
    #: 全体在编看得见。⚠️ 它和下面那张多对多是包含关系，不是并列
    visible_to_all_staff = models.BooleanField(default=False)
    #: 具体哪几个 ministry 的在编看得见
    visible_to_ministries = models.ManyToManyField(
        Ministry, blank=True, related_name="+",
        limit_choices_to={"is_active": True},
    )

    history = HistoricalRecords(m2m_fields=["visible_to_ministries"])
```

`EventRole` 三个同名的字段。

⚠️ **两个布尔 + 一张多对多，而不是一张 `AudienceRule` 表。** 前者的三行读起来就是
界面上那三类勾选，后者要为「外部人员」和「全体在编」各造一行没有 ministry 的记录 ——
[D15](decisions/D15-relationship-carriers.md) 的载体判据里，那属于「为了让结构统一
而造数据」。真到了要给可见性加属性（比如「从哪天起可见」）的那天再升级成表。

### ⚠️ 三处实测出来的机制，写进形状里而不是等实现时撞

**`m2m_fields` 不是可选的。** `HistoricalRecords()` **默认不跟踪 M2M**
（实测 simple-history 3.13 支持 `m2m_fields`，但要显式传）。不传的话，两个布尔进历史、
那张多对多不进 —— **一半有一半没有，比全都没有更糟**：翻历史的人会以为自己看到了
完整的受众变更。而「谁看得见这场活动」正是 `Event` 挂历史的那条理由
（「改时间改地点必须事后答得出来」）所属的那一类。

**`limit_choices_to={"is_active": True}`。** 退休的 ministry 不该出现在勾选框里 ——
同 `Participation.consent_relationship` 那一处的做法。

⚠️ **删掉一个 ministry 会静默收窄活动。** M2M 的中间行没有 `on_delete`，
被引用的 ministry 一删，那几行直接消失，活动因此变窄而没有任何提示。
`Ministry` 今天靠 `Event.ministry` / `Position.ministry` 的 `PROTECT` 挡着，
但一个**只被受众引用**的 ministry 不在那道保护里。
主动接受：退休走 `is_active=False`（那是这张表本来的路），删除本来就不该发生。
写下来是因为它是这个形状唯一一处会静默丢信息的地方。

### 🔴 `Model.clean()` 验不了 M2M —— 实测，而它推翻了下面一句话

```
ValueError: 'Event' instance needs to have a primary key value
            before this relationship can be used.
```

M2M 在 `save()` **之后**才写，`full_clean()` 在**之前**跑。所以：

> 「至少勾一项」和「角色 ⊆ 活动」**进不了 `Model.clean()`**。

⚠️ 更坏的是改一个已存在的对象时它**不报错**：`self.visible_to_ministries.all()`
读得出来，读到的是**库里那份旧值**，而不是正在提交的新值。一条读着合理、
验的是过期数据的校验 —— 比直接抛异常危险得多。

于是 [L2.3](#l23-不变量角色勾的每一项活动都必须勾了) 那张表里
「`EventRole.clean()` · 模型层，所以 admin 的 inline 和表单两条路一起覆盖」
**这句话作废**。规则的落点改成：

| 层 | 管什么 |
|---|---|
| `EventForm` / `EventRoleForm` 的 `clean()` | 真正的把关。表单读得到 `cleaned_data` 里那份**新**的 M2M |
| `services` | 非表单路径（以后的导入、API）走这里 |
| admin | ⚠️ **要自己的 `form = `**，否则 admin 那条路一道校验都没有 |

⚠️ 按 [D14](decisions/D14-constraint-is-the-only-rule.md)：这里**一条数据库约束都没有**，
而且不是偷懒 —— 「至少勾一项」的第三个析取项（有没有 ministry 行）在另一张表上，
`CheckConstraint` 看不见；而更弱的版本（比如「两个布尔至少一个真」）是**错的**，
因为只勾了 ministry 的活动完全合法。所以这一整条规则只有提示层，`bulk_create` 走得过去。

### ⚠️ 角色那一组是「看得见」，不是只有「报得上」—— 这一格我原来写反了

本节初稿写的是「`EventRole` 三个同名的字段，**语义从看得见换成报得上**」。错。
两条原文各自都足够：

- 需求 8：「同一个 event，internal roles **只会显示给** internal 的人」
- [`participants.md` 第三节](participants.md)的 🔴：「在角色这一层，**看得见 = 报得上**」

所以角色要**按看的人过滤掉**，不是列出来带一句「你报不上」。

⚠️ 于是多出一种空状态：**别的 ministry 的在编成员打开活动，看到零个角色** ——
和「还没建完」长得一模一样，而这正是 [D27](decisions/D27-ministry-report.md) 那条
「没有和没算不能长得一样」。所以这两种要分成两句话。

> 本句原文还有第三种（「这是一条公告」），并因此写着「L2.5 要从两句话变三句」。
> 2026-08-31 [D41](decisions/D41-notices-are-not-events.md) 把公告移出了 `Event`，
> 第三种**自己消失了** —— 一个角色都没有的活动只剩「还没建完」一种含义。
> 两句话就够，[L2.5](#l25-公告) 整节作废。

⚠️ [`participants.md` 第六节](participants.md)那个示意框写的是「外部人看得见活动，
这个位置报不上」，和它自己第三节的 🔴 打架。需求原文 + 第三节的不变量，二比一，
按这两条走 —— 那份文档要补一句更正。

### 五条规则，全部落在服务层 / 表单，逐条写出来

| # | 规则 | 违反的表现 |
|---|---|---|
| 1 | 至少勾一项 | 已发布却谁都看不见 —— 和草稿长得一样，而草稿已经有自己的状态了 |
| 2 | 勾了「全体在编」，各 ministry 不许再勾 | 同一个可见性两种存法。⚠️ 而且它们**今天等价、明天不等价** —— 新建一个 ministry，「全体在编」自动覆盖它，勾齐的那份不会 |
| 3 | 角色勾的每一项，活动都必须勾了 | 需求 6 / 7：报得上却看不见 |
| 4 | 新建活动时**一项都不预勾**（除了发布者自己的 ministry，见下） | 见下面那条 🔴 |
| 5 | 新开角色时默认 = 活动勾了什么 | 「看得见就报得上」是需求 6 的默认情形；默认最窄会让需求 8 的常见情形每个角色都要改宽 |

🔴 **规则 4 是这五条里最贵的一条。** 默认「所有人」等于今天的行为、迁移也省事 ——
但它的失败方式是：发一场欢送会忘了改，**就把它公开给了每一个外部志愿者**，
而这件事不报错、没有任何人会发现。逼发布者选一次的代价是每场活动多一步，
包括那些本来就对外的；这个代价明说，并且接受。

⚠️ 规则 2 的置灰只是界面。**服务层必须自己拒绝手工造出来的冗余组合**，
否则那道置灰谁都拦不住 —— 同这个项目一直在说的「不画控件是界面，界面挡不住任何人」。

⚠️ **没有 `Contact` 的登录用户按定义就是外部人员**（他不可能有在职任职），
所以他落进「外部人员」那一支，不是单独一个分支。⚠️ 这一句要写进
`for_audience()` 的 docstring：超级管理员就是这种账号（[D12](decisions/D12-user-on-contact.md)：
`User.contact` 可空），而「超管看不见内部活动」第一次遇到会被当成 bug。

### 发布者自己的 ministry：可以不勾，但表单预勾上

「食物银行为报税志愿者办一场培训」是真实场景，所以**不强制包含自己**。
但新建时把发布者自己的 ministry 预勾上 —— 内部活动最常见的情形就是给自己人看。

⚠️ 「自己的 ministry」在一个人管两个 ministry 时是两个。预勾**他管的全部** ——
判据走 `ministry_ids_administered_by(user)`，也就是那份表单的 ministry 下拉框
已经在用的同一个集合。不另判一次，理由同这个项目一直在说的：
两处判断迟早会在某一格上走散。

⚠️ 于是规则 4 有一个例外：这一项是预勾的。它不违反规则 4 的用意 ——
规则 4 防的是「默认对外公开」，而预勾自己的 ministry 是默认**最窄**。

### 迁移：现有活动全部回填成「外部 + 全体在编」

也就是今天的行为（任何登录用户都看得见）。⚠️ 不回填的后果不是「变严格」，
是**把库里每一场活动都藏起来**，因为规则 1 说空集不合法。

⚠️ 这和规则 4「新建时空着」不矛盾：迁移在**保住既有行为**，规则 4 在**定新政策**。
两件事，写在一起是因为它们看起来像互相打架。

## L2.2 `EventQuerySet.for_audience()`

```python
    def for_audience(self, contact):
        """按人收窄。⚠️ 和 visible_to_participants() 是两个谓词，永远分开写。

        判的是**活动当天**在不在编，和 L2 的资格同一把尺 —— 两把尺会造出
        「看得见但当天报不上」和「当天报得上但今天看不见」两种没人解释得清的错位。
        """
```

三个分支，对着 L2.1 那三类勾：

| 勾了 | 这个人要满足 |
|---|---|
| 外部人员 | 活动当天**没有**任何合格的在职任职 |
| 全体在编 | 活动当天**有**任何一条合格的在职任职 |
| 某几个 ministry | 活动当天有一条合格的在职任职，且岗位的 ministry 在勾中的那几个里 |

⚠️ 三条都是**存在性**判断（`Exists`），不是「他的那条任职怎么样」——
一个人可以同时持有多条 `Assignment`
（[D32](decisions/D32-worker-axes-schedule-and-assignment.md) 那条不变量说的是
路径不是条数）。落到语义上：**张三在食物银行和报税互助各有一个岗位，
一场只勾了「报税互助在编」的活动，他看得见、也报得上。**

### 🔴 第三行**不能写成 join** —— 实测，最自然的那个写法是错的

「勾中的 ministry 里有他的岗位」读起来就是一句 `filter(visible_to_ministries__in=…)`。
它在 dev 上跑出来是这样：

```
张三在 Pantry 和 Tax 各有一个岗位
一场活动同时勾了 Pantry 和 Tax
→ 结果里那场活动出现了 2 次
```

多对多是一次 join，两边各命中一行就出两行。**分页、计数、报表全部跟着错**，
而页面上看起来只是「这场活动怎么列了两遍」。

正确形状是 `Exists`（子查询只问有没有，不产生行）：

```python
Exists(
    Assignment.objects
    .filter(on_the_books_q(OuterRef("event_day")), contact_id=contact.pk)
    .filter(position__ministry__event_audience=OuterRef("pk"))
)
```

⚠️ 最后那一行要求 M2M **有一个反向名字**。L2.1 里我写的是 `related_name="+"`
（禁用反向），实测直接报 `FieldError: Unsupported lookup 'event_audience'`。
改成 `related_name="%(class)s_audience"`，于是 `Ministry` 那头有两个入口：
`ministry.events`（它拥有的活动，早就有）和 `ministry.event_audience`
（它看得见的活动）。⚠️ 只改 `related_name` **不动数据库**，迁移是纯状态变更。

⚠️ 走 through 表也做得到（`Event.visible_to_ministries.through`），实测同样正确、
同样不重复，但它要**嵌套两层 `OuterRef`**。两个都能跑的时候选读得懂的那个。

### 三处实测记录，免得下一个人再验一遍

| 问的 | 答案 |
|---|---|
| `exclude(Exists(…))` 和 `filter(~Exists(…))` 一样吗 | 一样。⚠️ 第一次只拿一个在编的人试，两边都返回空 —— **那不叫验过**。换一个真外部人再跑，两边都返回那一场，才算 |
| 重复 `annotate(event_day=…)` 会不会冲突 | 不会。所以 `for_audience()` 可以自己 annotate，不必担心被链式调用两次 |
| `Model.clean()` 能读 M2M 吗 | 不能，见 [L2.1](#-modelclean-验不了-m2m--实测而它推翻了下面一句话) |

⚠️ 外层要先 `annotate(event_day=local_day("start_time"))` —— L1.4 已经建好的
`on_the_books_exists()` 收的是一个已经算好当地日期的 `OuterRef`，理由见那一步。

### ⚠️ `on_the_books_q()` 要从 `services.py` 搬到 `models.py`

`for_audience()` 是 `EventQuerySet` 上的方法（在 `models.py`），而那条判据现在在
`services.py` —— 而 `services.py` 已经 import 了 `models.py`。**循环 import。**

搬到 `models.py`（它只依赖 `org.models` 和 `core.querysets`，两个 `models.py` 都已经
import 了），`services.py` 反过来 import 它。判据仍然只有一份，而依赖方向回到
`services → models` 这一个方向。

⚠️ 不用「在方法里延迟 import」那条路。它能跑，仓库里也有先例，但那是给
`forms → services` 那个方向用的；让 `models` 反过来伸手进 `services`，
即使 Python 允许，读起来也是一处味道。

### 要改的读路径，逐个点名 —— 漏一处就是一次静默的泄露

| 位置 | 改法 |
|---|---|
| `views.py` · `_visible_events()` | `.visible_to_participants().for_audience(contact)`。⚠️ 它现在只收 `period`，要多收一个 contact |
| `views.py` · `_schedule()` | 同上。日程和列表共用筛选，不共用这道门就是「列表里没有、日程上画着」 |
| `views.py` · `_detail()` | 收窄之外还要 **404 而不是 403** —— 和草稿预览同一条理由：不该看见的活动不该暴露自己存在。`can_view_event_records` 仍然是那扇后门 |
| `views.py` · `event_detail_panel` | 走 `_detail()`，自动覆盖 |
| `views.py` · `event_signup` | ⚠️ 它今天只走 `open_for_signup()`，一道受众都没有 |

**不改的两处，而这是决定不是遗漏** —— 这段话要写进 docstring：

> 收窄的是**发现**，不是**你已经拥有的行**。受众事后改窄，不该让已经报了名的人
> 打不开自己的活动页。

| 不改 | 为什么 |
|---|---|
| `my_participations` | 他手上就有那一行。⚠️ 守卫一的白名单点名了它，理由就是这一条 |
| 扫码签到（`checkin_scan` / `checkin_confirm` / `apply_scan`） | 人已经站在现场了。一道受众判断只会让他签不了到 |
| 管理侧 `_scoped_events()` | 它答的是「我管哪些活动」，和受众正交 |

### ⭐ 守卫一：受众必问

`core/tests.py` 加 `AudienceIsAskedGuardTests`：任何**函数体**里出现
`visible_to_participants()` 的地方，同一个函数体里必须出现 `for_audience(`。
白名单点名两处（`my_participations`、以及模型里那个谓词自己的定义）。

⚠️ 它故意窄：扫全文件的版本会因为 docstring 里的讨论天天红，
然后被加白名单加到失效 —— [`participants.md` 第十节](participants.md)
对 `ReportFigureNamesGuardTests` 写过同一句话。

## L2.3 不变量：角色勾的每一项，活动都必须勾了

从「枚举比大小」变成**集合包含**。

### 先补一条 L2.1 漏掉的：角色也要「至少勾一项」

活动那一侧 L2.1 定了（[规则 1](#五条规则全部落在服务层--表单逐条写出来)）。
角色那一侧当时一个字没说 —— 于是一个手工 POST 造得出一个**谁都报不上的角色**，
而它在页面上和「满员」「还没建完」长得一模一样，正是
[D27](decisions/D27-ministry-report.md) 那条「没有和没算不能长得一样」。

`refuse_empty_audience()` 已经在 L2.1 建好了，**两侧共用同一个函数**，
只是消息要分开：活动那句说的是「已发布却谁都看不见」，
角色这句要说「这个位置谁都报不上」。

### 三条比较，逐条判

```python
def refuse_wider_than_event(*, event: Audience.Spec, role: Audience.Spec):
    """角色的可报范围不许超出活动的可见范围。"""
```

| 角色勾了 | 合法当且仅当 |
|---|---|
| 外部人员 | 活动也勾了外部人员 |
| 全体在编 | 活动也勾了全体在编 |
| 某几个 ministry | 活动勾了全体在编 **或** 活动勾的 ministry 是它的超集 |

⚠️ 第三行那个「或」是这条不变量真正的难点，也是枚举版本没有的 ——
「全体在编」在包含关系上位于所有 ministry 之上，但它是一个**布尔**不是一个集合，
所以比较不能只写成一次 `issubset`。

⚠️ 第二行反过来**不成立**：活动勾齐了所有 ministry，角色勾「全体在编」——
**拒绝**。理由和 L2.1 规则 2 是同一条：两者今天等价、明天新建一个 ministry 就不等价。
⚠️ 而这一条在今天几乎触发不了（库里只有两个 ministry，勾齐 = 全体），
所以它读起来像多余的严格。写下来是因为**「今天等价」正是它存在的全部理由**。

### 🔴 两边都收松散值，不收实例

```python
class Audience(models.Model):
    class Spec(NamedTuple):
        """一份受众，从任何来源取出来之后的样子。"""
        outsiders: bool
        all_staff: bool
        ministries: frozenset[int]
```

⚠️ 签名里**不许出现 `event` 实例**。改窄一场活动时，活动那一侧也是「正在提交的值」——
读实例就读到了库里那份旧值，而那正是 [L2.1 实测过的那个坑](#-modelclean-验不了-m2m--实测而它推翻了下面一句话)：
一条读着合理、验的是上周数据的校验。

⚠️ `AudienceFormMixin.audience()` 现在返回三元组，改成返回 `Spec`。
`NamedTuple` 向后兼容（照样解包），所以这不是一次改口，是给同一个东西一个名字。

> ### ⚠️ 2026-08-27 补：这三条比较原来全部报在同一格上
>
> L2.1 [规则那张表](#五条规则全部落在服务层--表单逐条写出来)定的是「错误落在**字段**上，
> 不落在整张表单上」，理由是「『说清楚这是给谁的』挂在一张长表单顶上，人只会满屏找是哪个框」。
> 而实现里三条比较**全部**挂在 `visible_to_outsiders` 上 ——
> 一个因为「勾了活动没勾的 Tax Help」被拒的角色，报在「没有在职任职的人」那一格下面。
>
> 🔴 **指到错的格子，是同一个毛病多绕一步**，不是它的轻量版：
> 那条规则的用意是让人不用找，而错的格子让人找**并且**找错。
>
> 改法：`refuse_wider_than_event()` 抛的 `ValidationError` 改成**按字段的 dict**，
> 三条比较各自说出自己在讲哪一格；表单侧 `add_error(None, error)`，
> 由 Django 按 dict 的键分发。这是仓库里已有的形状 ——
> `core/constraints.py` 的 `_reattach_to_fields()` 和 `ParticipationRole.clean()` 都这么写。
>
> ⚠️ 活动那一侧（改窄活动被角色挡住）跟着改成**按格分组**，不是拼成一句：
> 同一次提交里取消「外部人员」又取消一个 ministry，坏掉的角色是**两批不同原因**的，
> 合成一句挂在一格上，两批都没说清 —— 而人下一步要做的事正是「把某一个勾放回去」，
> 是哪一个就是这句话的全部内容。
>
> ⚠️ 「一项都不勾」仍然挂在第一格上，而这一次是**写明的决定**不是同一个偷懒：
> 那种错没有单独的责任格，三个勾一起错。落点起名叫 `AUDIENCE_GROUP_FIELD`，
> 旁边写清楚它只给「关于整组」的错用 —— 而「关于某一个勾」的错由规则自己指名。
>
> 🔴 **由此产生一条常驻要求**：三个勾必须出现在每一张编辑受众的表单上。
> `ModelForm._update_errors` 对表单上没有的字段直接抛 `ValueError` ——
> 于是一个只画了两个勾的 admin，会把一次校验失败变成 **500**。
> 这和 `core/constraints.py` 给 `CONSTRAINT_FIELD` 记的是同一个陷阱，第二次出现。
> 配一条测试遍历所有注册的 admin（**含 inline**）逐个查，
> 并且断言「至少查到了一个」—— 循环一个都没找到时静默通过，正是这种检查失效的方式。
>
> 测试：`test_narrowing_two_ways_at_once_reports_each_on_its_own_tick`、
> `test_every_admin_editing_an_audience_renders_all_three_ticks`，
> 外加原有那三条断言各自改指对的格子（它们原来钉的正是这个 bug）。

> ### ⚠️ 2026-08-27 补：`Spec.of()` 原来是每行一次查询，而 prefetch 救不了它
>
> `refuse_narrowing_below_the_roles()` 要逐个角色取出受众来比 ——
> 而 `Spec.of()` 读那张多对多用的是 `values_list("pk", flat=True)`。
>
> 🔴 `values_list` **另建一个 queryset，因此绕过 `prefetch_related`**。
> 所以这个循环是每角色一次查询，**而且在调用点加 prefetch 也没有用** ——
> 这是两种失败里更坏的那一种：修补看起来已经加上了，而它什么都没做。
> 实测：8 个角色 16 次查询，其中 8 次是这个。
>
> 改法两半，缺一半都不成立：`Spec.of()` 改读 `.all()`，
> 调用点加 `.prefetch_related("visible_to_ministries")`。
> 实测 16 → 9，且 2 个角色和 12 个角色**都是 9**。
>
> ⚠️ 测试断言的是「两个数相等」，不是某个绝对值。
> 钉死 9 的话，哪天这张表单加一个字段它就变红，而下一个人会去把 9 改成 10 ——
> 一条性能测试就是这样变成橡皮图章的。要钉的是「代价不跟角色数走」这个形状。
>
> ⚠️ 还剩一条：ministry 那一支被拒时，`refuse_wider_than_event()` 为了拼消息
> 要查一次部门名字，每个被挡的角色一次（实测 2 个角色 12 次、12 个角色 22 次）。
> 初判是「有界，不改」，**当天被推翻** —— 见下一条。
>
> ### ⚠️ 2026-08-27 再补：那次查询不该省，该让它有用
>
> 上面判「不改」的理由里，有一条是「那句话调用方根本不看，所以是白跑的」。
> 走查时被问了一句：**那为什么不干脆也给用户看？**
>
> 这一问把问题的性质挪了位置。它原来被我归成性能取舍，而它其实是
> **「用户看到什么」**的问题 —— 而那不该由实现的人顺手定。
>
> 定案：**给用户看。** 改窄活动被挡住时，提示里点名是哪一档人卡住的：
>
> ```
> 现在  改窄这场活动会让「翻译」、「搬运」变成谁都看不见却报得上。
> 改后  改窄这场活动会让「翻译」对报税互助的在编开放，而他们将看不见这场活动。
> ```
>
> ⚠️ **落点不是把整句正文塞进括号。** 角色页那一句是完整的句子，写给「一个角色」；
> 活动页这一句讲的是**一组角色**，而规则拼不出它 —— 规则收的是 `Spec`，
> 按 [L2.3 的 🔴](#-两边都收松散值不收实例)它永远看不到角色的名字。
> 所以两边共用的那一半是**那个人群短语**（`params["audience"]`），
> 句子各自组装。短语只有一份拼写，被插进两边的句子里。
>
> ⚠️ 分组的键是**（格子, 人群）**，不是格子。三个位子被同一个部门卡住是
> 「一个问题、三个名字」，不是三个问题 —— 逐个位子加括号会把「报税互助」印三遍，
> 而一句自我重复的提示读起来就是一张故障清单。走查时先看到的正是这个毛病。
>
> ⚠️ 顺带两处措辞，实际渲染出来才看得见：
> 部门名用 `and` 连（`staff in Tax Help and Youth Work`）—— 消息里的角色名各自带引号、
> 自己就分隔开了，部门名不带引号地嵌在短语里，逗号列表读着像话没说完。
> ⚠️ 这一句我手写了一遍并配了测试，走查时才发现 **Django 自带
> `django.utils.text.get_text_list`**，逐个用例比对完全一致，而且它还过 `gettext`。
> 已改成用它 —— 「先查框架有没有」这一步我漏了；
> 以及只挡住一个位子时说 `Narrow that role first` 而不是 `those roles` ——
> 那是最常见的情形，复数读起来像消息漏了一个。
>
> 于是那条性能尾巴**自己没了**：那次查询查出来的东西现在两条路都在用。

### 三个调用方 —— 而 `sign_up()` **不在**里面

| 调用方 | 挡的方向 |
|---|---|
| `EventRoleForm.clean()` | 建 / 改角色。⚠️ **不是** `EventRole.clean()`，模型层读不到还没保存的 M2M |
| `EventForm.clean()` | 改窄活动。⚠️ 要**点名是哪几个角色**挡住了它，否则人只知道被拒绝、不知道去改什么 |
| `AudienceAdminForm.clean()` | 两张表在 admin 里共用它，所以两个方向一起覆盖 |

🔴 **`services.sign_up()` 不是这条不变量的调用方**，而初版把它列在这里 ——
那会让实现的人在报名路径上调错函数。两件事不是一回事：

| | 判什么 | 谁 |
|---|---|---|
| `refuse_wider_than_event()` | 两个**配置**之间的关系 | 本节 |
| `eligible(contact, event_role)` | 一个**人**和一个角色的关系 | [L2.4](#l24-报名门) |

### 两处实现细节，各自会抛一次

**新建活动时没有角色可查。** `EventForm.clean()` 要判包含就得取
`self.instance.roles.all()`，而新建时 `instance.pk` 是 `None` —— 实测直接
`ValueError: 'Event' instance needs to have a primary key value`。
`if self.instance.pk is None: return`，因为新建的活动确实一个角色都没有。

**顺序：先判空集，空了就不判包含。** 活动一项都不勾时，**每一个**角色都比它宽 ——
不先返回的话，一个空受众会同时报出一堆「角色比活动宽」，
而真正的毛病只有一个，且不在那些角色上。

### 决定 15 的落点：新角色默认继承活动勾了什么

`EventRoleForm.__init__` 在 `instance.pk is None` 时，把活动那三项填进 `initial`。

⚠️ 它和这条不变量是同一件事的两面：**默认继承 = 默认合法**。
默认最窄的话，需求 8（一次发布同时招内外）的常见情形要每个角色手动改宽，
而忘了改的表现是「外部志愿者看得见活动却报不上任何位子」。

⚠️ 只在新建时。改角色时库里那份就是答案 —— 重新继承会把一个被刻意收窄过的
角色悄悄放宽，同 L2.1 那条编辑时不预勾。

> ### 🔴 2026-08-27 补：这条只给了表单，而不走表单的路径全部落在「谁都报不上」
>
> 上面那句「`EventRoleForm.__init__` 填进 `initial`」是对的，**但它是唯一的一份**。
> 受众是三列，缺省 `False` / `False` / 空 —— 于是**任何直接落库的角色都是空受众**，
> 也就是 [L2.3 第一节](#先补一条-l21-漏掉的角色也要至少勾一项)刚刚判为非法的那个状态。
>
> 实际中招的有两处，都是这一轮自己写的：
>
> | 位置 | 状态 |
> |---|---|
> | `events/tests.py` · `make_role()` | 2026-08-26 给 `make_event()` 加了缺省受众，**同一笔漏了角色这一半** |
> | `seed_demo.py` · `role()` | 同上。演示库里那 28 行角色全部谁都报不上（11 处调用，其中两处在填充活动的循环里）|
>
> ⚠️ **它今天一点症状都没有**，因为在 [L2.4](#l24-报名门) 之前没有任何查询读角色的受众。
> 症状会在 L2.4 落地的那一刻一次性出现：演示库每一页报名清空，本文件大半的测试同时变红 ——
> 而那个时刻最省事的出路是回头把 L2.4 放松。所以它在这一步钉死，不留给 L2.4 去发现。
>
> 落点是 `services.inherit_audience(row, source)`。
> ⚠️ 初稿写的是 `Audience.inherit_audience_from()`（在 `events/models.py` 上），
> 而[后来那一步](#-2026-08-27-补三条规则原来只装在表单上而注释说的是三处)
> 把写入收进了 `services.set_audience()` —— 模型侧再留一个写方法就是在门旁边又开一扇。
> 落到 services 之后三个理由一条不少（`models.py` 也 import 不了 `services.py`）。
> 三个理由决定了它**必须是一个函数**，而不是在那两处各写四行：
>
> 1. 守卫二盯的是 `Spec` 那三个属性名，只放行 `models.py` / `forms.py`。
>    在 seed 或 fixture 里读 `spec.outsiders` 当场变红 —— 而那是对的，
>    「同一条包含关系长出第二份实现」正是它要防的；
> 2. 决定 15 是一条规则，现在有**两个机制**（表单填 `initial`、直接落库的写这一列），
>    照 `records_hours` / `recording_hours()` 那条规矩，两个机制共用一份判据；
> 3. 批三的 session 生成器是第三个调用方，它同样没有表单可以继承。
>
> ⚠️ **继承，不是写死 `outsiders=True`。** 写死是第一反应，而它在唯一要紧的方向上是错的：
> 一场只给在编看的活动，它的角色会拿到一个**比活动还宽**的受众 ——
> `refuse_wider_than_event()` 要防的那个状态，由防它的那行代码亲手造出来。
> 列上的 `default=` 同理做不到：一个缺省值读不到另一行。
>
> ⚠️ 两处都先问 `audience_is_empty` 再继承，理由各不相同，都要写出来：
> seed 是 `get_or_create`，重跑不该覆盖别人在演示库里改过的受众；
> fixture 是让 `**fields` 里显式写了受众的调用方保住自己那份。
> 而「空受众」从来不是谁选出来的状态，所以填它是**修复**不是覆盖。
>
> 配三条测试（`AudienceContainmentTests`）+ 一条 seed 上的（`SeedDemoTests`）：
> `test_a_role_can_take_its_audience_from_its_event_without_a_form`（含多对多那一半）、
> `test_what_it_inherits_is_never_wider_than_the_event`（写死为什么不行）、
> `test_a_fixture_role_is_not_open_to_nobody`、
> `test_nothing_it_seeds_is_visible_to_nobody` ——
> 最后这条是[第十节那两个 seed bug](participants.md)那一类**第一次有东西在自动查**，
> 而那两个当初是靠浏览器撞出来的。

> ### ⚠️ 2026-08-27 补：`Spec.__str__` 删掉了 —— 没有调用方，而且它会查库
>
> L2.1 给 `Audience.Spec` 写了一个 `__str__`，docstring 说是「给错误消息用的」。
> 走查时数了一遍：**没有任何一处把 `Spec` 转成字符串**，
> 三条错误消息各自拼自己的话。
>
> 删掉的理由有三条，第三条才是最要紧的：
>
> 1. 照 `EventQuerySet` 删掉 `upcoming()` / `past()` 那条注释 ——
>    「一个没有调用方的东西没有任何东西在查它，而下一个人读到的是一种受支持的做法」；
> 2. 上面那次改动之后，它里面「people with no current post」「everybody on the books」
>    两句和 `refuse_wider_than_event()` 里**一字不差**地重复了 ——
>    而有读者的是后者。两份拼写、其中一份看不见，比原来更糟；
> 3. 🔴 **它会查库。** 一个 `__str__` 里的查询会在任何东西被打印时触发 ——
>    日志行、调试器、渲染这个值的模板、一个列表的 repr ——
>    每一处都是没人会去找查询的地方。而 `NamedTuple` 自带的 repr 打的是原始字段值，
>    那正是调试的人真正想看的东西。
>
> ⚠️ 测试钉的是**性质不是方法的缺席**：`assertNumQueries(0)` 包住
> `f"{spec}"`。断言「没有 `__str__`」会连一个写得好的也一起禁掉；
> 必须一直成立的是「把受众变成文字是免费的」，所以问的就是这一句。
>
> ⚠️ 将来 [L2.4](#l24-报名门) 的角色列表或 [L2.6](#l26-eventtype-上页面) 的活动页
> 真要在界面上**说出**一份受众时：从已经有读者的那份拼写里取措辞，不要在这里另写一份，
> 并且把数据库挡在外面。

> ### ⚠️ 2026-08-27 补：三条规则原来只装在表单上，而注释说的是三处
>
> `events/models.py` 那段模块注释从 L2.1 起就列着三个「规则生效的地方」，
> 其中一个是 `services`。**它是假的** —— `services.py` 一次都没调过这三条规则里的任何一条。
> 于是不经过屏幕的每一条写入（`bulk_create`、以后的导入、脚本、批三的场次生成器）
> 从三条规则旁边一并走过去。
>
> 🔴 一句承诺了锁的注释比一扇没上锁的门更糟，因为它让人**不去看**。
>
> 落点分三层，而分层是被守卫二逼出来的、不是选的：
>
> | 层 | 谁 | 为什么在这儿 |
> |---|---|---|
> | 规则本体 | `models.py` 里那三个 `refuse_*` | 判据要读 `Spec` 的字段，而守卫二只放行 `models.py` / `forms.py` |
> | 「哪几条适用于这一侧」 | `models.refuse_bad_audience(row, spec)` | 同上：它也要读 `Spec` |
> | 写 | `Audience.apply_audience(spec)` | 同上 |
> | 门 | `services.set_audience(row, spec)` | 先拒后写。**它一个 `Spec` 字段都不拆** —— 拆了当场触发守卫二，而 `services.py` 正是第二份实现最可能出现的地方 |
>
> ⚠️ 走查时确实被守卫二当场抓了一次 —— 抓的是我写在 `set_audience` docstring
> 里的那个字面量。守卫按**行**扫不按语法扫，这对一个漏报是静默的守卫来说是对的取舍。
>
> 🔴 **表单不走这扇门，这是决定不是遗漏。** `ModelForm` 自己从 `cleaned_data` 存实例，
> 走这扇门等于写两遍；而表单要的是**一次收齐所有毛病、分挂到各自的格子上**，
> 这扇门要的是**第一条就停**。两边调的是 `models.py` 里同一批规则体，
> 差的只是拿到失败之后怎么处理。
> ⚠️ 代价是「哪几条适用」被写了两遍 —— 所以配了一条
> `test_the_service_and_the_forms_refuse_the_same_three_things`，
> 把每一种拒绝同时递给两扇门，不许有一扇是松的。
>
> ⚠️ 服务层那一侧的消息要**点名是哪个位子**。规则本身说不出来（它收的是 `Spec`，
> 永远看不到名字），而「本活动不开放给外部人员」对一个正在改窄活动的人是真话也是废话 ——
> 他要的是「你哪个位子挡住了」。所以 `refuse_bad_audience` 在活动那一侧重新抛，
> 用的是表单那边同一句话。
>
> ⚠️ **调用方是真的，不是空转的锁**：`seed_demo.role()` 和 `events/tests.py` 的
> `make_role()` 都改走这扇门。前者照 `joins()` 对 `sign_up()` 的同一条规矩 ——
> 演示数据应当走站点自己的那些门；后者让这扇门每跑一次测试就被走 65 次。
> （我原来提醒过「装一把没人推的门」的风险，这是它的答案。）
>
> ⚠️ 一处如实记下的边界（D14 的规矩）：`set_audience` 是 `atomic` 的，
> 但**没有任何测试到得了它防的那个情形** —— 校验在写之前，所以校验失败永远不会写一半。
> `atomic` 防的是 `apply_audience` **内部**出错（布尔列写完了、ministry 没设上）。
> 把 `atomic` 拆掉一条测试都不红，这是**实测过的**不是推测的；留着是因为
> 那个状态没有主人也没有报警。原来那条测试的名字和 docstring 都在暗示它被覆盖了，
> 已经改名成它真正钉住的那件事（拒绝之后行一个字没动）。

### 守卫二

初版写的是「`AUDIENCE_WIDTH` 只有 `refuse_wider_than_event()` 一个读者」，
而 `AUDIENCE_WIDTH` 随枚举一起没了。改成盯**集合比较**：

```
issubset  /  <=  /  >=   出现在受众字段附近 → 只许在 events/models.py
```

⚠️ 它比原来窄也比原来必要：多选之后可比的东西变多了，而「在视图里顺手比一下」
正是这条不变量最可能长出第二份实现的地方。

⚠️ 按 [D14](decisions/D14-constraint-is-the-only-rule.md) 如实说：它**不是**
`CheckConstraint`。字段在两张表上，现在还多一张多对多，跨表条件表达不了 ——
和 [D19](decisions/D19-event-role.md) 判掉 `Participation.event` 是同一格。
`bulk_create` 走得过去，而它走过去之后的状态是「一个人报上了一个他看不见的活动」。
⚠️ 那个状态**不由 `sign_up()` 兜底**：报名路径判的是人和角色（L2.4），
不是替这条不变量补一道。写下来是因为「那让 sign_up 顺手也查一下」听起来很合理，
而它会让同一条规则有两处实现、且两处的入参不同。

### 测试

- `test_a_role_open_to_nobody_is_refused`（补上的那条）
- `test_a_role_wider_than_its_event_is_refused`
- `test_a_role_for_all_staff_is_refused_even_when_the_event_ticked_every_ministry`
  —— ⚠️ 今天几乎触发不了的那一格，正因如此才要钉住
- `test_a_role_for_one_ministry_is_fine_when_the_event_is_for_all_staff`
- `test_narrowing_an_event_below_its_roles_is_refused_and_names_them`
- `test_an_empty_event_audience_does_not_also_report_every_role`
  —— 顺序那一格
- `test_a_new_role_inherits_what_the_event_can_see`（决定 15）
- `test_editing_a_role_does_not_re_inherit`

## L2.4 报名门

`services.sign_up()` 在容量门**之前**加资格门：

```python
    if not eligible(contact, event_role):
        raise NotEligible({"event_role": "…"})
```

顺序是故意的：「你没资格报这个位置」比「这个位置满了」更准确，
而先答满员会让人去等一个永远等不到的空位。

`eligible(contact, event_role)` 和 `for_audience()` 共用同一条 `on_the_books_q()`，
并且**收在 `.exists()` 里**：问的是「他有没有一条合格的在职任职」，
不是「他的任职是哪一条」。详情页要一次问完一整页：
`eligible_role_ids(contact, event)` 返回一个集合，一次查询 ——
集合天然去重，所以一人多岗不会让同一个角色的 id 出现两次。

### 页面：角色**按人过滤掉**，不是列出来带一句「你报不上」

> ⚠️ 本节初稿写的是「照样列出来，带一句为什么」。**错**，理由和更正见
> [L2.1 末尾那一节](#-角色那一组是看得见不是只有报得上-这一格我原来写反了)：
> 需求 8 原文说 internal roles「只会显示给 internal 的人」，
> 而 `participants.md` 第三节的 🔴 说「在角色这一层，看得见 = 报得上」。

`_event_detail_body.html` 的角色表和 `SignUpForm` 的 `event_role` queryset
**收同一个集合**：这个人看得见的那些角色。手工构造的 POST 因此拿到一条真正的
校验错误，而不是 500。

需求 7（看得见 ≠ 报得上）在这个形状下仍然成立，只是落在**活动**这一层：
别的 ministry 的在编成员**打得开这场活动**，只是里面一个角色都没有给他的。

⚠️ 于是那一页要说得出「这里有角色，只是没有一个是给你的」——
见 [L2.5](#l25-公告)，那里现在有三种空状态要区分。

> ### ⚠️ 2026-08-29 补：动手前这一节被逐条核对了一遍，四处要改
>
> 本节和 L2.1～L2.3 的落地对得上（`on_the_books_q()` 已经在 `models.py`、
> 角色那头的反向名是 `eventrole_audience`、L2.3 已经让每个角色真的带着受众）。
> 四处不对的地方写在下面，都在动手之前改掉了。
>
> #### 一、「共用同一条 `on_the_books_q()`」的共用不够
>
> 那只共用了判据的一半。真正容易写错的是外面那个三支析取，尤其是
> `visible_to_outsiders & ~Exists(...)` ——「外部人员不是最宽的一档」这个坑
> 在这个仓库里已经咬过一次（seed 只勾了外部人员，把整个演示库对自己人藏了起来）。
> 照本节字面写，这个析取会有两份实现：`EventQuerySet.for_audience()` 一份、
> `eligible()` 一份。而守卫二盯的是 `Spec` 的三个属性名，按模型字段写的第二份
> 根本不碰那三个名字 —— 没有任何东西拦得住它。
>
> 落点：抽 `AudienceQuerySetMixin`，`EventQuerySet` 和 `EventRoleQuerySet` 各混入一次，
> 差别只有两处 —— 判哪一天（`AUDIENCE_DAY`），以及那张多对多的反向名
> （从 `model_name` 推，因为字段本来就是 `%(class)s_audience` 推出来的；
> 写死两份的代价是批三第三张带受众的表会**静默落进 event 那一支**）。
> `eligible()` 因此只剩一行 `.filter(pk=…).for_audience(contact).exists()`。
>
> #### 二、空状态不能推给 L2.5
>
> 本节写着「见 L2.5」，但 L2.4 正是**造出**这个状态的那一步。只做 L2.4 的话，
> 别的 ministry 的在编成员打开活动，读到的是 "No roles opened yet." —— 一句假话，
> 正是 D27 那条「没有和没算不能长得一样」。而批二的验收清单自己写着
> 「看得见，报不上，页面说得出为什么」。
>
> 所以第二种空状态（有角色、没有一个是给你的）归 L2.4，L2.5 在它之上再加公告那一种。
>
> #### 三、Sign up 按钮会指向一个空下拉框
>
> `can_sign_up` 原来只等于 `event.accepting_signups`，和「有没有位子是给他的」无关。
> 角色一被过滤掉，零资格的人照样看到按钮 → 点进去是一个必填却没有任何选项的下拉框
> → 提交得到 "Select a valid choice"，而他什么都没做错。
>
> 落点三处：按钮按 `to_join` 画；活动详情多一句「这场活动在收报名，只是没有一个
> 位子是给你的」（否则会掉进「本活动不收报名了」那一句，对他是假话）；
> 报名页直接进来时 404 —— 和这个视图上面那道门、和详情页同一个答案。
>
> #### 四、走后门看详情的人该看到什么（定案：全表）
>
> `_detail()` 有一扇后门（`can_view_event_records`）。按人过滤之后，
> 刚开完角色的 ministry admin 会看到一张缺行的表，而页面上没有任何东西说少了行。
>
> 定案：**这一类人看全表**，因为他们在报名页本来就看得见全部角色，
> 而「我刚开的角色去哪了」是一个查不出原因的问题。
> 代价照实写下来：角色表和报名下拉框对这一类人**不再是同一个集合**，
> 而本节原文要求的是同一个 —— 所以页面自己要说出来是哪一件（一句常驻文案，不查库）。
> 报名按钮仍然按他**自己**的资格画：全表是一种**读**的特权，不是报名的特权。
>
> #### 顺带：`eligible_role_ids()` 判它不建
>
> 详情页和 `SignUpForm` 要的是带 `with_signup_counts()` 的角色**对象**，
> 直接 `.for_audience(contact)` 一次查询就有；再取一遍 id 是第二次查询。
> 建一个没有调用方的函数，理由和删掉 `upcoming()`/`past()`、删掉 `Spec.__str__`
> 是同一条：没有调用方的东西没有任何东西在查它，而下一个人读到的是一种受支持的做法。
> 文件总表里那一行跟着改。

## L2.5 公告

❌ **本节整个作废（2026-08-31）。**

> 🔴 **原计划的 `Event.takes_signups` 是个伪需求，一行代码都没写过。**
> 完整论证在 [D41](decisions/D41-notices-are-not-events.md)，短版是它两头都不成立：
>
> - **有**固定时间的公告不需要它 —— 上面[决定 3](#七个已定的决定2026-08-21) 已经写死了
>   「需要被记住的一律开一个 `attending` 角色，`needed_count` 留空」；
> - **没有**固定时间的公告装不进 `Event` —— `start_time` / `end_time` 都是 NOT NULL，
>   编造时间之后它会进 R1 的活动条数、进日历、进 `.ics`。
>
> 落点改成 `notices` app 的 `Notice` 表（批四 N2 / N3，已落地）。
>
> ### ⚠️ 而「批四」没有自己的 roadmap，这是一处结构性欠账（2026-09-08 记）
>
> [`goal.md`](goal.md) 的约定 2 是「每个 Phase 开工前，把当时的实施细节写进一份新的
> `0N-roadmap.md`」，而批四（公告）**没有那一份** —— 步骤散在这一节的更正框里，
> 计划外记录没有落点，于是 `1a25ce5` 那三个坑当时无处可写（见文末补记的那一条）。
>
> **不补建 `07-roadmap.md`**，这是判断不是偷懒：批四已经交付完了，事后补一份
> 「实施步骤」是写一份没有人会照着做的文档，而这个仓库刚为「说不出谁读它」删掉
> 一整张表。真相分三处，各自有读者：形状在 [D41](decisions/D41-notices-are-not-events.md)、
> 代码在 `notices/`、这一节记它为什么不是活动。
>
> ⚠️ 欠的那一样是**计划外记录没有家**。下一批开工时如果仍然挂在某一节的更正框
> 底下，就必须建那份 roadmap —— 判据是「这一批会不会撞出值得记的坑」，
> 而答案从来是会。
>
> **原来那三句空状态因此变回两句。** 本节曾要求把「这是一条公告」和
> 「还没建完」区分开；公告不在 `Event` 那张表里之后，「一个角色都没有」只剩
> 「还没建完」一种含义，那个歧义**自己消失了**，不需要任何文案去消解它。
> `_event_roles_panel.html` 那句 empty 一个字不动。
>
> ⚠️ 本节引的 ChurchSuite 依据**过不来**：它的原文场景是
> *"an internal reminder in the Calendar module of the weekly staff meeting"*，
> 一条**日历条目** —— 而本仓库已经把例会判给了 `Shift`（[`phase-d.md`](phase-d.md)）。
> 三家真有这个需求的产品（Viva、Planning Center、Chatter）全都建了独立对象。

本节剩下的**唯一**一件事已经做完（2026-09-02，`8a8352a`），至此本节全部结清。

它原本排在批四 N4（仪表盘）之后，实际是在 N4 里一并收掉的 —— 那句引导指向
`/me/`，而 `/me/` 正是 N4 建出来的那一页，两件事分不开。

- ✅ `_event_detail_body.html` 角色空状态那一格，在 `No roles opened yet.` 之后补一句
  指向 `/me/` 的英文引导。
  ⚠️ 它是承重的：横幅那条被判不做（[D41 第六节](decisions/D41-notices-are-not-events.md)），
  所以这是活动侧通向公告的**唯一**线索。

## L2.6 `EventType` 上页面

❌ **本节整个作废（2026-09-04）。这张表没有上页面 —— 它被整张删掉了。**

> 原计划是给 `EventPeriodForm` 加一个 `event_type` 的下拉、`narrow()` 多一个
> filter、详情页和管理列表显示类型，目的写着「让这张字典表有真读者」。
>
> 🔴 **而它是一个必填、却没有任何人读的字段，这两半同时成立。**
> 单独一条都不足以推翻它，合起来足够：
>
> - **成本每天在付**：`Event.event_type` 是非空 FK，而且在 `EventForm.Meta.fields`
>   里 —— 每一个 ministry admin 发布活动都必须选一个类型，不选就发不出去；
> - **收益一次没有**：全仓 `*.html` 命中 **0** 次，唯一的读者是 admin 的 changelist；
> - **没有人要过**：[`participants.md` 第六节](participants.md) 自己查证过
>   「R1–R8 / P1–P6 里一条都没提到活动分类」。
>
> 于是只有三条路，而**继续保持现状是三条里最差的那一条**：给它上页面（本节原计划）、
> 把表删掉、或者让每个人接着填一个没人看的框。选了第二条 ——
> 迁移 `events/migrations/0021_drop_event_type.py`，代码 / 迁移 / 文档 / 图一处不留，
> 照 `3b5c059` 删通用关系表那次的规矩办。
>
> ⚠️ 本节原文把「没有一处代码 branch 它、前台模板命中 0 次」记在 **D5** 名下，
> 而那句话**不在 D5 里** —— 它在 [D41](decisions/D41-notices-are-not-events.md)
> 和 `participants.md` 第六节。而且「没有一处代码 branch 它」恰恰是 D5 判定它
> **该做成字典表的理由**，本来就该保持成立；只有「命中 0 次」那一半是缺陷。
>
> ⚠️ `participants.md` 那张「三件容易被塞进同一个字段的事」的表**仍然成立**，
> 只是第一行没有了：L3「这场给谁看」和 L1「这个角色是来给还是来受」还在，
> 而且正因为少了一个容易混进来的第三者，那条边界比原来更清楚。

## L2.7 批二的测试与验收

测试：

- `test_an_outside_account_does_not_see_a_staff_only_event`
- `test_a_staff_member_of_another_ministry_sees_but_cannot_sign_up`
- `test_visibility_is_judged_on_the_day_of_the_event`
- `test_the_schedule_narrows_by_audience_too`
- `test_signing_up_for_an_event_you_cannot_see_is_a_404`
- `test_cancelling_visibility_does_not_hide_an_event_you_already_signed_up_for`

多选带来的那几条（2026-08-26 加）：

- `test_an_event_can_be_visible_to_two_ministries_at_once`
  —— ⚠️ 这一条是推翻枚举的**唯一理由**，缺了它那次推翻就没有落点
- `test_an_outsiders_only_event_is_hidden_from_staff`
  —— 决定 10：「外部人员」不是最宽的一档
- `test_an_outsiders_only_event_is_hidden_from_the_admin_who_published_it`
  —— 同一条规则的刺耳后果，写成测试免得以后被当成 bug「修」掉
- `test_ticking_everyone_stores_outsiders_and_all_staff`
  —— 决定 11：「所有人」不落库
- `test_an_event_visible_to_nobody_is_refused`
  —— 决定 12
- `test_all_staff_together_with_a_named_ministry_is_refused_by_the_service`
  —— 决定 13 的**服务端**那一半。⚠️ 只测置灰等于没测，界面挡不住任何人
- `test_somebody_with_posts_in_two_ministries_sees_an_event_for_either`
  —— 一人多岗，决定表第 1 条在多选下的样子
- `test_the_executive_director_sees_all_staff_events_but_not_ministry_ones`
- `test_a_new_event_starts_with_only_the_publishers_own_ministry_ticked`
  —— 决定 14 + 那个例外
- `test_a_new_role_inherits_what_the_event_can_see`
  —— 决定 15
- `test_a_role_wider_than_its_event_is_refused`
- `test_a_role_for_all_staff_is_refused_even_when_the_event_ticked_every_ministry`
  —— ⚠️ 包含关系里最容易写错的一格：勾齐所有 ministry **不等于**「全体在编」
- `test_narrowing_an_event_below_its_roles_is_refused_and_names_them`
- `test_existing_events_stay_visible_after_the_migration`
  —— 回填那一条。⚠️ 漏了它，上线那天库里每一场活动都会消失

浏览器验收（dev 库真数据，照 [`participants.md` 第十节](participants.md) 那五条的规矩）：

- 外部账号看不到内部活动
- 别的 ministry 的在编成员看得见、报不上，且页面说得出为什么
- 一次发布同时招内外（一个对外的角色 + 一个仅在编的角色）
- 一场勾了两个 ministry 的活动，两边的在编都看得见，第三个 ministry 的看不见
- 勾「所有人」→ 存进去之后再打开，显示的仍然是「所有人」而不是两个分开的勾
- 把活动改窄 → 被角色挡住，错误里点了名
- 公告页面说得出它是故意不收报名的

⚠️ 这一批的洞全是静默的，测试绿不代表页面对 ——
[`participants.md` 第十节](participants.md) 那两个 seed bug 就是这么撞出来的。

---

# 批三 · L5 时间：一期、一场、还是各报各的

> ⚠️ **本批 2026-08-26 整个重写。** 初版只装了需求 4 的一半（「按规则生成多场 +
> 多场归成一组」），而基金会当天补了另一半：
>
> > 「recurring 的长期 event，可以让 admin 选**报名一次就代表后面都报过了**；
> > 也可以选是显示成**一个条目**还是每周一个。前者我这边叫 **Programs**，
> > 后者叫 **recurring events**。」
>
> 🔴 这一句正是 [`participants.md` 第九节](participants.md)**排在第一位**那条缺口
> 写死的重启条件（「他报一次之后，后面每一场都不用再报」这句话成立时）。
> 所以本批同时是那条缺口的出栏，而它是**行业主分界线**，不是本项目的特例。
>
> 初版那一节的内容没有删，见 [L5.9](#l59-初版那份-eventseries-哪些留下了哪些作废)。

## L5.0 六个决定，以及为什么是三档不是四格

基金会最初描述的是两个开关（显示成一个条目 / 报一次管全部）。四种组合里
**只有三种说得通**，而漏掉的那一种真实存在：

| 显示 | 报名 | |
|---|---|---|
| 一个条目 | 报一次管全部 | ✅ **Programs** |
| 每场一个条目 | 每场单独报 | ✅ **recurring events** |
| 一个条目 | 点进去挑哪几场 | ⚠️ 真实存在，[Planning Center 专门做了](https://www.planningcenter.com/blog/2021/04/split-registrations-signups-for-date-and-time-blocks)（「每周二的妈妈小组，但妈妈们只报得了其中几个周二」） |
| 每场一个条目 | 报一次管全部 | ❌ 骗人：点第 3 周却等于报了全部 |

所以落成**一个三档单选**，不是两个开关 —— 两个开关要额外一条规则去挡第四格。
第三种是 Programs 下面的一个副开关。

| # | 问题 | 定案 |
|---|---|---|
| 16 | Program 的形状 | **一个 `Event`**（起 3 月止 6 月）+ N 个 `Session`。不是「一个系列 + N 场活动」 |
| 17 | 「挑哪几场」 | 本轮一并做，是 Program 下的副开关 |
| 18 | 中途加入 | 从加入那天算起 —— 前几场对他**不存在**，不是缺席 |
| 19 | 报名人次 | 一个人上 12 堂课算 **1**（数 `Participation`，不动） |
| 20 | 每场工时 | 记在场次那一层 |
| 21 | 发布界面 | 一个三档单选，「挑哪几场」是第三档下的副开关 |

### ⚠️ 决定 19 改过一次，而它把整个形状换了一遍

先定的是「算 12」。那一条**单独**逼着「每场的记录必须是一行 `Participation`」——
于是要动 `participation_unique_per_event_role`（现在是 `(角色, 人)` 唯一）、
要把容量判断从数行改成数人、还要一张 `Enrolment` 装「整期一次」的同意书和身份。

改成「算 1」之后那一串全部消失：报名还是一行 `Participation`，
`signups` 天然是 1，同意书和身份本来就在它上面。

⚠️ 记下来是因为它是这一轮**最便宜的一次改主意** —— 一个报表口径的选择，
决定了要不要动一条已有的数据库约束。下一次遇到「这个数该怎么算」的问题时，
值得先问一句：它会不会反过来决定形状。

## 又七条（2026-09-05，页面安排）

[L5.3](#l53-三档单选落在哪以及报一次管全部) 写的是「现有 `/events/` 列表页排不排除 Program 这一格留白 ——
在设计出来之前替它决定，就是在猜」，[L5.8](#l58-页面与路由) 写的是「Programs 的页面本轮不设计」。
设计现在有了，两格一起填。

| # | 问题 | 定案 |
|---|---|---|
| 22 | Programs 出现在哪 | 自己的列表页 `/programs/`，**不进** `/events/` |
| 23 | 日程 | Programs 有自己的 schedule，画**每一讲**，不画那条 111 天的横条。⚠️ 后半句「站点日程不含 Programs」**2026-09-08 作废** —— 见下 |
| 24 | recurring events | 不需要任何额外代码 —— 它生成的是 N 个各自独立的 `single` 活动，天然就在 `/events/` 里 |
| 25 | 「挑哪几场」 | 不是第四种东西，是 Programs 下的副开关（决定 17），和「报一次管全部」同在 `/programs/` |
| 26 | 我报名的 programs | 单开 `/me/programs/`，且**从 `/me/participations/` 里拿走** —— 各管各的，`/me/` 上两个入口 |
| 27 | Program 的详情页 | **复用 `/events/<pk>/`**，按 `shape` 换一块（讲次表走独立 partial）。列表页各开各的，详情页只有一个 |
| 28 | 拼写 | `program`（美式）。原文的 `PROGRAMME = "programme"` 作废 |

> ### 2026-09-08：决定 23 的后半句改了 —— 站点日程**按讲次**画 Programs
>
> 原文是「站点日程不含 Programs」，而排除需要 `Event.shape`（L5.3 还没做），
> 于是它在落地之前就先被走查撞上了：一门跨三个月的课在两端之间的**每一列**
> 都画一个占满全天的方块，包括一次聚会都没有的那些天。实测 8/29 那天 ——
> 24 小时高，把其他活动全压在底下。不是一条长条，是一堵墙。
>
> 而「排除」本来就答错了问题：一门课的各讲**就是这个月要发生的事**，
> 站点日程不画它们，等于让一个志愿者看不到自己周二晚上有课。
> 所以改成：把活动摊成它实际占用的那些段（`schedule.occurrences`），
> 单场是它自己，有讲次的是它的各讲。卡片带「Session N」。
>
> ⚠️ 判据是「**有没有讲次**」而不是 `shape`，且这不和
> [L5.3](#l53-三档单选落在哪以及报一次管全部) 那条「不靠 `sessions.exists()` 判形状」冲突：
> 那条说的是**分类**（一个还没排期的 Program 也是 Program），
> 这里问的是**画什么**（有讲次就把它们画出来）。两个问题，两个判据。
>
> `/programs/schedule/` 仍然在 L5.8，届时复用同一段。

### 决定 27 是这次走查买来的，理由要写下来

列表页和详情页的答案不一样，而分界线是**持不持有不变量**：

- **列表页放心新开**。它只是一个筛过的查询集，多一个列表页 = 多一行 `filter(shape=program)`，
  没有第二处规则；
- **详情页只许有一个**。一个 Program 的详情页要重新持有受众（`for_audience`）、报名门
  （`eligible`）、草稿预览、监护人同意书、改期通知、L2×L3 含容不变量 —— 六样全是
  「漏一处就静默泄露」的类型。

而这不是推理，是本仓库正在为同一形状付的两笔账（2026-09-05 走查实测）：
`event_signup`（`events/views.py`）漏了受众门，且 `AudienceIsAskedGuardTests` 的信号
触发不到它；`event_create` / `event_update` 漏了 `form.save_m2m()`，而晚一个月写的
`notices/views.py` 有 —— 同一形状的两处代码，新写的那处对、老的那处错。**两扇门，一扇忘了上锁。**

还有一条本仓库自己的判据，在 `notices/models.py` 的 `Notice` docstring 上：
「它没有场合，所以它没有场合带来的一切：没有角色、没有报名、没有出勤、没有工时。
**这一行上要是哪天开始想要其中任何一样，那被描述的东西就是一个 Event，它就该是一个 Event。**」
Program **四样全要**。按这张表它就是 Event，就走 Event 那条路。

⚠️ 决定 23 让 [L5.1](#l51-session一期课的第几讲) 三条否定式里的第三条改了措辞：
原文「不进活动列表页」会读错 —— 讲次确实要出现在 Program 自己那页的 schedule 里。
准确说法是**不进站点级的活动列表和日程**。判据不变。

⚠️ 决定 28 现在改是免费的，值写进库再改就是一次数据迁移。三处不一致（库里 `programme`、
页面写 Programs、口头说 programs）的代价是以后 grep 不到彼此。

## L5.1 `Session`：一期课的第几讲

> ### 2026-09-05 落地。本节初稿有六处照字面敲会出问题，逐条改在下面
>
> 开工前的走查把这一节和仓库现状对了一遍。形状是对的 —— 那三条否定式是本节最值钱的
> 东西，它们把「什么时候该用 `Session`、什么时候该用 `Event`」写成了**可判定的判据**，
> 不是描述。问题全在「照着敲」这一层：初稿的列名和全仓冲突、漏了一条别的表都有的约束、
> 一条它自己的 docstring 隐含要求的规则没人挡，以及三处会让守卫当场变红的遗漏。

落库的形状（已实现，`events/models.py`）：

```python
class Source(models.TextChoices):
    """一份定义两处用：`Session.source`（本步）和 `Event.source`（L5.4）。"""

    MANUAL = "manual", "Added by hand"
    GENERATED = "generated", "Produced by a rule"


class Session(ConstraintErrorFieldMixin, TimeStampedModel):
    """一期活动里的一次聚会。⚠️ 它不是 `Event`。

    ESL 春季班是**一个** `Event`（3/1 起、6/20 止），十二次聚会是它下面的
    十二行 `Session`。这正是 participants.md 第九节说 `Event` 装不下的那句话
    ——「他从 3 月到 6 月在这个项目里」—— 而 `Event` 其实一直有起止两列，
    缺的只是中间那些时刻。

    ⚠️ 和 `Event` 的分工是硬的，三条都要成立：
       · `Session` **不能单独报名**（报名挂在 `Event` 的角色上，整期一次）
       · `Session` **没有自己的受众**（L2/L3 在 `Event` 和 `EventRole` 上）
       · `Session` **不进站点级的活动列表和日程**（`/events/`、`/events/schedule/`）
       任何一条要破，说明那个东西其实是 `Event`，该走 recurring events 那一档。

    ⚠️ 和 `Shift` 的分界线也没有变（participants.md 第六节）：有固定岗位 +
       按周重复 + 机构对他的时间有承诺 → `Shift`。一期课的学员不是在上班。
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="sessions")
    start_time / end_time
    source = ...                      # 默认 MANUAL
    history = HistoricalRecords()

    class Meta:
        ordering = ["start_time", "id"]          # 正序，和 Event 相反
        constraints = [唯一(event, start_time)、check(end_time >= start_time)]

    def clean(self): ...              # 一讲必须落在这一期之内
```

### 六处改动，逐条写明原文是什么、为什么改

| # | 初稿写的 | 改成 | 为什么 |
|---|---|---|---|
| 1 | `starts_at / ends_at` | `start_time / end_time` | 仓库里 `_at` 一律是**动作发生的时刻**（`registered_at`、`checked_in_at`、`sent_at`、`consent_at`、`created_at`），排定的时间窗从来不用它（`Event.start_time`、`Notice.starts_showing`、`Assignment.start_date`）。用 `_at` 会被读成「这一讲实际开始的时刻」。而同 `Event` 命名还有第二个好处：`clean()` 里两者要直接比较 |
| 2 | 只有唯一约束 | 加 `end_time >= start_time` 的 `CheckConstraint` | `Event` / `Assignment` / `MinistryRole` / `Notice` 各有一条。⚠️ 这一条是 L5.2 自己那句话的反面教材：「逐条抄，**不是**『大概同款』—— 抄漏一条的表现是那张表比它复制的那张松」，而初稿在 L5.1 上就先松了一条 |
| 3 | 没有任何窗口检查 | `clean()` + `services.add_session()` | 春季班 3/1–6/20 可以存一个 8/1 的聚会，而**本节 docstring 自己说**「`Event` 一直有起止两列，缺的只是中间那些时刻」—— 中间的时刻能跑到两列外面，那句话就不成立。跨表条件（判据在 `event` 的两列上），`CheckConstraint` 看不见，同 L2×L3 那条和 `nature` 冻结那条 |
| 4 | `source` 各写各的 | 枚举提到模块级 `Source`，L5.4 的 `Event.source` 复用同一份 | 初稿是「L5.1 给 Session 写一份、L5.4 给 Event 再写一份」，同一个概念两处定义 —— 这个项目判过三次的「第二份真相」 |
| 5 | 没提 `ConstraintErrorFieldMixin` / `core/constraints.py` | 两者都补 | `core/tests.py` 那条守卫双向查：约束缺 `violation_error_code`、code 缺 `CONSTRAINT_FIELD` 映射、或留下没有约束的映射，三种都当场红。⚠️ 而「本轮要动的文件总表」里 `core/constraints.py` 只标了「批一」，批三这一格是漏的 |
| 6 | 没有 `history` | `history = HistoricalRecords()` | 相邻的 `SessionAttendance` 初稿明写了它。「谁在什么时候把第 7 讲从周二挪到周四」会影响一批人的出勤记录，而 `Event` / `Participation` / `Notice` 全都有。两张相邻的表一张有一张没有，需要理由而不是默认 |

⚠️ 第 3 条按 [D14](decisions/D14-constraint-is-the-only-rule.md) 的规矩**把缺口写出来而不是暗示**：
`Session.objects.create()` 和 `bulk_create` 从它旁边走过去。这一条本身配了一条测试
（`test_a_bare_create_walks_past_the_containment_rule`），钉的就是这个代价 ——
免得下一个人把 docstring 读成承诺。

⚠️ **不加 `indexes`，而这是决定不是遗漏。** 唯一约束 `(event, start_time)` 自带的复合索引
正好服务那两个查询：「这一期的全部讲，按时间正序」和「下一讲是哪天」（`/me/programs/` 要用）。
`Event` 那三条索引各自标了 R1/R2 的理由，「不加」同样要写。

⚠️ `Event.duration`（R3）对 Program 会变成「111 天」。**不改它** ——
那两列说的就是这个，而报表上那一格对 Program 本来就没有意义。
真要显示「每次两小时」，那是 `Session` 的时长，属于**新页面**的事。
记在这里是因为它看起来像个 bug。

> ### 2026-09-08 更正：上面那句把它推给了「新页面」，而新页面被取消了
>
> 决定 27（晚三周）判 Program **不新建详情页**，复用 `/events/<pk>/`。
> 于是这一句寄存在「新页面」上的东西**没有人接** —— 两份文档各自把它推给了对方，
> 而中间那一格是空的。走查时基金会打开一个有讲次的活动，看到的是这一行：
>
> ```
> When: Aug. 9, 2026, 4:05 p.m. — Nov. 7, 2026, 3:05 p.m.
> ```
>
> 🔴 它不是「少了信息」，是**一句假话**：读起来像一场从八月某个下午一直开到十一月
> 某个下午的活动，而三讲一讲都没出现。落点和验收写在
> [L5.8](#l58-页面与路由)，`Event.duration` 本身仍然不改（那两列说的确实是这个）。

### ⚠️ 这一步**不兑现** participants.md 第九节那条缺口

第九节排第一位那条（「他报一次之后，后面每一场都不用再报」）的出栏要等
[L5.2](#l52-sessionattendance他哪几场来没来干了多久) + [L5.3](#l53-三档单选落在哪以及报一次管全部)。
L5.1 只提供承载 —— 记在这里是因为初稿没写这句，容易让人以为做完这一步就结清了。

> ✅ 2026-09-09 由 [L5.3](#l53-三档单选落在哪以及报一次管全部) 结清。

### 测试（`events/tests.py` · `SessionTests`）

- `test_a_session_belongs_to_one_event`
- `test_two_sessions_in_one_event_cannot_start_at_the_same_moment`
- `test_two_events_may_hold_meetings_at_the_same_moment`
  —— 唯一约束是**两列**而不是一列：两门课同一个晚上开是常事
- `test_a_session_cannot_end_before_it_starts`
- `test_a_session_outside_its_events_own_dates_is_refused`
- `test_a_session_before_its_event_starts_is_refused`
- `test_a_session_inside_its_events_dates_is_kept`
- `test_the_service_refuses_a_session_outside_the_events_dates`
- `test_the_service_keeps_a_session_inside_the_events_dates`
- `test_a_bare_create_walks_past_the_containment_rule` —— D14 那个缺口
- `test_deleting_an_event_takes_its_sessions_with_it`
- `test_a_session_starts_out_marked_as_added_by_hand`
- `test_moving_a_session_is_kept_in_its_history`
- `test_sessions_come_back_in_the_order_they_are_taught`

⚠️ 两条约束走**数据库**（裸 `create()` + `IntegrityError`），窗口那条走 `full_clean()`
和服务层 —— 两层分开验。一条只在 `full_clean()` 下失败的「约束」，是穿着约束外衣的 `clean()`。

### 迁移

`events/migrations/0022_session.py` —— 纯 `CreateModel`（`Session` + `HistoricalSession`），
无回填。docstring 写明：今天库里每一场活动都是没有聚会的单场，而「没有这些行」正是这个意思，
所以这一步不改任何一行现有数据的含义。

## L5.2 `SessionAttendance`：他哪几场、来没来、干了多久

> ### 2026-09-08 落地。本节初稿有八处照字面敲会出问题，逐条改在下面
>
> 开工前的走查把这一节和仓库现状对了一遍。形状是对的，而**决定 18 那一段是本节最值钱的
> 东西**：「中途加入不是一个字段，是哪几行存在」把两条需求（决定 17、决定 18）收敛成一个
> 实现。问题有两类：一类和 L5.1 那次一样，是「照着敲」这一层的漏抄；另一类更重 ——
> 本节有两条规则**照字面敲会落成一张比它复制的那张更松的表**，而其中一条是它自己要求的。

落库的形状（已实现，`events/models.py`）：

```python
class SessionAttendance(ConstraintErrorFieldMixin, TimeStampedModel):
    """一个人的一次聚会。行业里这一层各有各的名字，形状是同一个。

    Salesforce PMM 叫 `ServiceDelivery`（报名是 `ServiceParticipant`），
    Apricot 叫 Attendance Tracker（报名是 enrollment），
    ChurchSuite 开着「sign up to the sequence」时把出勤汇成一张「随时间」的表。
    ⚠️ 五个平台查下来没有例外：**报一次 ≠ 出勤一次**，报名一层、出勤一层。

    ⚠️ 它**不是**一行 `Participation`，而这是决定 19 换掉的那个形状。
       两者的字段确实很像，区别在语义：`Participation` 是「他报了这一期」，
       报表数它得到「多少人报名」；这张表是「他来了第几讲」，数它得到的是
       「出勤人次」——两个不同的数，而合成一个正是本项目判过三次的病。
    """

    participation = FK(Participation, CASCADE, related_name="attendances")
    session       = FK(Session,       CASCADE, related_name="attendances")
    status  = ...          # 复用 Participation.Status，只在这一场上成立
    hours   = ...          # 决定 20。⚠️ 只装「给出去的」那一半，见 D43
    checked_in_at / checked_out_at / checked_in_method
    history = HistoricalRecords()

    class Meta:
        constraints = [五条，见下]        # ⚠️ 无 ordering、无 indexes，两者都是决定

    @property
    def records_hours(self): ...          # 委托给 participation
    @property
    def hours_received(self): ...         # D43：这一讲有多长
    def clean(self): ...                  # 两条跨表规则
```

### 八处改动，逐条写明原文是什么、为什么改

| # | 初稿写的 | 改成 | 为什么 |
|---|---|---|---|
| 1 | 「三条从 `Participation` 搬过来的规则」，第一条是那条 `CheckConstraint` | 那条约束**搬不过来**，只能是 `clean()` + 服务层 | 🔴 初稿在同一行里写了两句互相否定的话：要求照搬那条约束，又注明「判据仍然读 `participation.event_role.role.nature`」。后者成立前者就不成立 —— `Participation` 上那条能落地，靠的是行上存着 `served_as=not_applicable` **把跨表判据搬到了本行**；而本节判了 `served_as` 不搬，这张表上就没有任何一列可供检验。⚠️ 附带一处：不搬的理由（「身份是整期一次的声明」）只覆盖 `volunteer`/`work` 两档，而 `not_applicable` 按 [D38 第五节](decisions/D38-served-as-volunteer-or-work.md) 根本不是身份，是结构性标记 —— 顺手带走的正是那个锚点 |
| 2 | 没有任何规则说两个外键要指向同一场活动 | `clean()` 第一条 | 🔴 `participation` 的活动在 `event_role.event` 上，`session` 的在 `session.event` 上。不一致时存下来的是「他来了一门他没报的课的第 7 讲」——读得出、印得出、页面正常。这正是 `Participation` docstring 自己点名的那个坑（当年的解法是删掉一列），而这里两个外键都删不掉，所以改成检查它们是否一致 |
| 3 | 「三条规则」 | 五条，两条都是同款 | ⚠️ 和这张表字段重合的约束 `Participation` 上有五条，初稿漏了 `checkout 不早于 checkin` 和 `checked in 的人不能标成缺席` —— 而这张表 `checked_in_at` / `checked_out_at` / `status` 三列都有。「抄漏一条的表现是那张表比它复制的那张松」这句话就写在本节自己那一行上，L5.1 落地时刚在它身上应验过一次 |
| 4 | `class SessionAttendance(TimeStampedModel)` | 加 `ConstraintErrorFieldMixin`，`core/constraints.py` 补五行 | 和 L5.1 初稿第 5 条一模一样的遗漏。`ConstraintMappingGuardTests` 四向查（缺 code、缺映射、留下没有约束的映射、映射到不存在的字段），少哪一样当场红 |
| 5 | 没说这张表的行是谁建的 | `services.add_attendance()`，单行 | L5.1 有 `add_session()`，本节没有对应的东西 —— 而两条跨表规则全靠它才被调用到。⚠️ 批量建行（报一次管全部 / 决定 17 / 决定 18）**要等 L5.3**：`sign_up()` 在 `Event.shape` 之前分不出一门课和一场周六发放，这句话写进 docstring，免得这张表读起来像没做完。✅ 2026-09-09 L5.3 落地时那句 docstring 已就地兑现 |
| 6 | `session` 用 `related_name="+"` | 两头都叫 `attendances` | 「第 7 讲今天谁来了」是点名页的第一个查询，`"+"` 把它从 `Session` 那头挡死了。而初稿担心的那件事不成立：`event.sessions` 和 `participation.sessions` 会是**一个词指两张表**，而这两个是**一个词指同一张表的两个方向** —— 那正是反向名字的用途 |
| 7 | 没提 `Meta.ordering` | 明写**不设**，改出两个 queryset 方法 | 两个方向要两种顺序（按讲次时间 / 按人），一个 `Meta` 服务不了两个；而跨关系的默认排序还有第二笔代价 —— Django 会把它塞进 `values().annotate()` 的 GROUP BY，而 [L5.7](#l57-l14-那几个工时口径要改决定-20-的代价) 正要写那种查询。L5.1 那条「不加索引是决定不是遗漏」在这里是同一种要写下来的「不加」 |
| 8 | 没有这个数 | `hours_received` + `services.hours_received()` | 走查时基金会提出：来接受服务的人也想知道自己被服务了多久。查证下来这不是可选项（成人教育按 contact hours 报，还有 12 小时门槛），但答案**不是放开 `hours`** —— 那是方向相反的第三个数，见 [D43](decisions/D43-hours-given-and-hours-received.md)。不加列，从 `Session` 的起止两列算 |

### 五条规则，逐条抄齐

| 规则 | 落成什么 |
|---|---|
| 唯一：一个人一场只有一行 | `UniqueConstraint(participation, session)` |
| 工时非负 | `CheckConstraint`，同款 |
| 只有出席过才有工时 | `CheckConstraint`，同款 |
| 签退不早于签到 | `CheckConstraint`。⚠️ 初稿漏的那两条之一 |
| 签到过的人不能是缺席 | `CheckConstraint`。⚠️ 另一条 |
| 「来参加的位置不记工时」 | 🔴 **不是约束** —— `clean()` + `record_session_hours()`，见上表第 1 条 |
| `hours` 是 `Decimal` 不是 `Float` | D 那条老规矩 |

⚠️ `served_as`（身份）不搬：它是整期一次的声明，留在 `Participation` 上。
[D38 第五节](decisions/D38-served-as-volunteer-or-work.md)那张表问的是「他这次参加算什么」，
而对一期课来说「这次」就是这一期。代价是上表第 1 条那一条，已就地写进
[D38](decisions/D38-served-as-volunteer-or-work.md) 第五节。

### ⚠️ 决定 18（中途加入）不是一个字段，是「哪几行存在」

第 5 周才报名的人，`SessionAttendance` 只为**第 5 讲起**的那些聚会建行。
于是他的出勤率分母是 8 不是 12 —— 前四讲对他**不存在**，不是缺席。

⚠️ 这也正好是决定 17（挑哪几场）的机制：只为选中的那几场建行。
两条需求一个实现，而不是两个字段。

⚠️ 而它同时是 D43 那个数的分母：他接受到的时数是那 8 讲里他到场的那几讲之和，
没有任何一处需要去减掉前四讲。

### D43：接受到的时数是第三个数，方向相反

`hours` 这一列装的是**他给出去的时间**（决定 20：一个助教在十二讲里帮了六次，
就是六个数）。**他被服务了多久**是另一个数，方向相反，
[D43](decisions/D43-hours-given-and-hours-received.md) 判它**不存、用算的**：

> 他接受到的时数 ＝ 他出勤过的那几讲的时长之和

⚠️ 两者永远并排、永远不相加 —— 加起来是「我们收到的时间 ＋ 我们发出的时间」，
一个没有定义而且看起来完全合理的量。这是 [D36](decisions/D36-two-hour-ledgers.md)
那条不变量的第四次应用，两处都已就地写了修订说明。

⚠️ 报表上怎么摆属于 [L5.7](#l57-l14-那几个工时口径要改决定-20-的代价)，本步只提供这个数。

### ⚠️ 这一步同样**不兑现** participants.md 第九节那条缺口

L5.1 那一节写了这句，本节初稿没写 —— 而本节更容易被读成做完了。
「他报一次之后，后面每一场都不用再报」要等 [L5.3](#l53-三档单选落在哪以及报一次管全部)：
本步只有单行的门，批量建行在 `Event.shape` 之前写不出来。

> ✅ 2026-09-09 由 [L5.3](#l53-三档单选落在哪以及报一次管全部) 结清 —— 批量建行落在
> `open_register()` 上，`sign_up()` 调它。⚠️ 而 L5.3 那一节当时**并没有写要做这件事**，
> 三处指着它的话里就有上面这一句；见[计划外那一条](#计划外--一步被三处指着而它自己那一节是空的2026-09-09l53-开工走查)。

### 测试（`events/tests.py`）

`SessionAttendanceTests` —— 五条约束走**裸 `create()` + `IntegrityError`**，
两条跨表规则走 `full_clean()` 和服务层，两层分开验（同 L5.1）：

- `test_one_person_at_one_meeting_is_one_row`
- `test_a_new_row_starts_out_expected_rather_than_present`
- `test_two_people_may_attend_the_same_meeting`
- `test_the_same_person_cannot_be_marked_twice_for_one_meeting`
- `test_one_person_may_be_on_the_register_for_every_meeting`
- `test_hours_cannot_be_negative`
- `test_somebody_who_did_not_attend_cannot_have_hours`
- `test_check_out_cannot_be_before_check_in` —— 初稿漏的那两条之一
- `test_somebody_who_checked_in_cannot_be_marked_absent` —— 另一条
- `test_a_meeting_from_another_run_is_refused`
- `test_the_service_refuses_a_meeting_from_another_run`
- `test_a_place_people_attend_records_no_hours`
- `test_the_service_refuses_hours_on_a_place_people_attend`
- `test_the_rule_is_read_off_the_role_through_the_signup`
- `test_a_bare_create_walks_past_the_same_run_rule`
- `test_a_bare_create_walks_past_the_no_hours_rule`
  —— ⚠️ D14 那两个缺口。第二条钉的是「不搬 `served_as`」的代价：
  同一行在 `Participation` 上被数据库拒绝，在这里存得下
- `test_an_assistant_records_hours_for_one_meeting`
- `test_who_first_recorded_the_row_is_not_rewritten_by_a_correction`
- `test_deleting_a_signup_takes_its_register_entries_with_it`
- `test_deleting_a_meeting_takes_its_register_with_it`
- `test_correcting_a_register_entry_is_kept_in_its_history`
- `test_one_persons_register_reads_in_teaching_order`
- `test_one_meetings_register_reads_by_person`

`SessionsThroughTheAdminTests` —— 两张表今天唯一的门。⚠️ 计划里这一格原本是
一次手工走查，改成测试是因为仓库自己已经有先例（`AudienceThroughTheAdminTests`
抓的正是「页面侧看不见的洞」），而一次点击留不下任何东西：

- `test_the_meetings_table_is_reachable_at_all` —— 钉住 L5.1 那三天的缺口
- `test_the_register_pages_render`
- `test_the_admin_refuses_a_meeting_from_another_run`
- `test_the_admin_refuses_hours_on_a_place_people_attend`
  —— ⚠️ 四条里最要紧的一条：这条规则背后**没有约束**，而 admin 是今天唯一
  一个人能往点名册里敲工时的地方
- `test_who_recorded_the_row_cannot_be_edited_here`

`HoursReceivedTests` —— D43：

- `test_a_meeting_knows_how_long_it_runs`
- `test_the_hours_column_stays_empty_for_somebody_being_served`
- `test_hours_received_add_up_the_meetings_they_attended`
- `test_a_meeting_they_missed_adds_nothing`
- `test_a_meeting_they_missed_has_no_length_of_its_own` —— None 不是 0
- `test_meetings_before_they_joined_are_not_missing_hours` —— 决定 18 在时长这一维
- `test_somebody_who_came_to_nothing_receives_nothing`
- `test_hours_given_and_hours_received_are_two_different_numbers`
- `test_an_event_with_no_meetings_has_no_register_to_read`
  —— ⚠️ 一场发放日没有讲次，这个数在那里**不是 0 是不适用**

### 迁移与 admin

`events/migrations/0024_session_attendance.py` —— 纯 `CreateModel`
（`SessionAttendance` + `HistoricalSessionAttendance`），无回填，同 0022 的形状。
⚠️ 编号是 **0024** 不是 0023：批二之后还落了一条 `0023_audience_help_text`，
而文件总表里两条都没有。

⚠️ 顺带补上 L5.1 欠的一笔：`Session` **当时没有注册进 admin**，于是那张表从
2026-09-05 起只有测试碰得到。批三的文件总表是承诺过的，而漏掉它没有任何症状 ——
一张没人打得开的表，和一张没人需要的表，长得一模一样。本步两张一起挂。


## L5.3 三档单选落在哪，以及「报一次管全部」

> ### 2026-09-09 落地。本节初稿有两处开工前的走查就撞上了，逐条改在下面
>
> 形状是对的，两条「为什么要一列」的理由也站得住。问题不在写错，在**写漏**：
> 这一节自己只写了列和谓词，而全仓有三处指着它说「报一次管全部要等 L5.3」。
> 中间那一格是空的 —— 和 L5.1 那条 2026-09-08 更正判过的是同一个病。

| # | 初稿写的 | 改成 | 为什么 |
|---|---|---|---|
| 1 | 只有枚举、两条理由、两个谓词。**批量建点名行一个字都没有** | 本节同时交付 `open_register()` / `close_future_register()` 和六个调用方 | 🔴 三处指着这一步：`events/services.py` 里 `add_attendance()` 的 docstring 明写「…until `Event.shape` lands in L5.3」、L5.1 和 L5.2 各有一条 ⚠️「要等 L5.3」、`participants.md` 第十一节验收「前半句要等 L5.3」。而 L5.10 那张批次级测试清单里的 `test_signing_up_for_a_program_covers_every_session` 等四条，没有任何一步描述怎么做。**两份文档各自把它推给了对方** |
| 2 | 2026-09-05 补框：「`/events/` 和 `/events/schedule/` 都只剩单场」 | 日程那半句**作废**；列表那半句**推到 L5.8** | 三天后的[又七条 2026-09-08 补框](#又七条2026-09-05页面安排)已经推翻了日程那一半（站点日程按讲次画 Programs），而代码早按新的落了地（`events/schedule.py` 的 `Occurrence`）。照初稿敲会把修好的东西改回去。列表那半推到 L5.8 和 `/programs/` 同批，中间就不会出现「一门课在站点上没有任何入口」的窗口；同时它也避开了 `events/views.py` 那条注释点名的坑 ——「列表里没有、日程上画着」是同一份筛选画出的两个答案 |

> ### 被这两处取代的原文，照本文件的规矩留在这里
>
> 2026-09-05 那一格写的是：
>
> > 原文是「基金会说 Programs 会有**自己的页面**，而页面设计要等后端定完。所以后端把
> > 两者分得开，而**现有 `/events/` 列表页排不排除 Program 这一格留白** —— 在设计
> > 出来之前替它决定，就是在猜」。
> >
> > 设计给出来了（决定 22–27），所以答案是**排除**：Programs 走 `/programs/`，
> > `/events/` 和 `/events/schedule/` 都只剩单场（含 recurring 生成的那些）。
> > 而它反过来证明了这一列该存在 —— 上面那两条理由（列表页要按它筛、一个还没排期的
> > Program 也是 Program）现在**各自都有了真实调用方**。
>
> 最后那句现在由 admin 的 `ShapeFilter` 兑现，而不是由列表页 —— 结论没变，
> 兑现它的是另一个调用方。日程那一句在三天后作废，列表那一句推到 L5.8。

### 落库的形状（已实现，`events/models.py`）

```python
class Event(...):
    class Shape(models.TextChoices):
        SINGLE = "single", "One occasion"
        PROGRAM = "program", "A course or program — sign up once"

    shape = ...                       # 默认 SINGLE
    people_pick_meetings = ...        # 决定 17 的副开关，默认 False
```

⚠️ **枚举只有两档，而界面上是三档。** 第二档（recurring events：每周一场、
各自报名）生成的是 **N 个独立的 `Event`，每个都是 `single`** ——
它是建活动时的一个**生成选项**，不是 `Event` 上的一个状态。
三档单选是发布表单上的一个 `ChoiceField`，其中两档写进这一列、
一档触发生成器（L5.4，届时加进同一个字段）。

⚠️ 那为什么不靠 `sessions.exists()` 判、非要一列？两条：
一是列表页要按它筛（`Exists` 子查询每次都要 join）；
二是**一个还没排期的 Program 也是 Program** —— 建的时候先定形状、再排日期，
是很自然的顺序，而 `sessions.exists()` 在那一刻会答错。

⚠️ 这一列问的是**分类**，日程和详情页问的是**画什么**（判据是「有没有讲次」）。
两个问题、两个判据，这一点 [L5.8](#l58-页面与路由) 和 2026-09-08 那条补框已经写死。

### 两个谓词，这一步就有调用方

```python
def programs(self): ...          # Shape.PROGRAM
def single_occasions(self): ...  # Shape.SINGLE
```

⚠️ `EventQuerySet` 自己那段注释写着「没有调用方的谓词不留」（`upcoming()` /
`past()` 就是这么删掉的）。所以两件事一起做：一是在那段注释旁写明**这两个是一个
完备划分的两半**（`test_the_two_predicates_do_not_overlap` 钉的是不重不漏，
而 `upcoming` / `past` 是两个各自独立的查询，两种东西）；二是给它们一个真读者 ——
`events/admin.py` 的 `ShapeFilter`。

⚠️ `list_filter = ["shape"]` 那条路**接不上**：Django 从字段的 `choices` 生成
`ChoicesFieldListFilter`，执行的是 `filter(shape__exact=…)`，永远碰不到
`EventQuerySet`。要调谓词必须是 `SimpleListFilter` —— 同一个文件里的
`UnderstaffedFilter` 就是先例，它的 docstring 第一句正是「每个分支一次 QuerySet
调用，这里不做算术」。而 L5.8 之前 admin 是 Program 唯一管得到的地方，
所以这个筛选不是为了满足规矩硬造的。

### 两个互为镜像的函数，四条规则全落在它们身上

```python
def open_register(participation, *, sessions=None, now=None): ...
def close_future_register(participation, *, now=None): ...
```

| 规则 | 落在哪 |
|---|---|
| 报一次管全部 | `sign_up()` → `open_register()`，和 `set_served_as()` 同一个事务 |
| 决定 17「挑哪几场」 | `sign_up(sessions=…)`，由 `people_pick_meetings` 放行。✅ 2026-09-10 学员端补齐：`SignUpForm.ask_sessions` + `MeetingChoiceField` |
| 决定 18「中途加入」 | 同一个机制：**哪几行存在**。切点 `end_time > now` |
| 后加的讲次要补建 | `add_session()` → `open_registers_for()`（admin 走 `SessionForm.save()`） |
| 重新报名只补缺的行 | `open_register()` 是补齐不是重建 |
| 取消/退出清掉未来的行 | `cancel()` → `close_future_register()` |
| 整门课停办 / 复办 | `set_status()`，只在进出 `cancelled` 这两个转换上动 |

**切点一律 `end_time > now`** —— 仓库那条硬规矩「是否结束一律读 `end_time`」
（`open_for_signup()` / `is_over` / `from_today()` 三处，2026-08-18 为此合并过一次）。
晚到半小时当场报名的人，仍在今晚这一讲的点名册上。

⚠️ **`sessions=` 在两个函数里只有一个意思：收窄看哪几讲。** 决定 17 那道
「他有没有资格挑」的门在 `sign_up()` 上，因为**挑**这件事发生在那里。初稿把两者
写成同一个参数，结果 `add_session()` 的补建被当成一次非法的挑选拒掉了 ——
测试当场抓到。

🔴 **`close_future_register()` 要两个条件，不是一个。** 讲次还没结束**且**那一行
还停在 `registered`。只按时钟筛的话，一堂课上到一半、已经签到的人中途退出，
会把他的签到和工时一起删掉 —— 在一个规矩是「已经发生的绝不动」的函数里。
同样测试抓到的。

### 整门课停办到一半

| 东西 | 怎么处理 | 为什么 |
|---|---|---|
| 已上的那几讲 | 一行不动 | 那几讲真的发生了。停办不能倒过来说它没发生 |
| 剩下几讲的 `Session` 行 | 保留 | 它们是当初的计划，也是「为什么只上了六讲」的唯一证据。同 L5.6「有出勤的讲次不许自动删」的手工路径版 |
| 那几讲上每个人的预期点名行 | 清掉 | 不清的话出勤率分母永远是 12，而只有 6 讲可能发生 |
| 报名行的状态 | 不动 | 🔴 `cancel()` 判的是「**这个人自己**不来了」。基金会停办不是他做的事 —— 写成 withdrew，报表会说「六个人退出了」，而事实是「我们停开了」。活动的 `status` 已经说了一遍，往人身上再写就是第二份真相 |

⚠️ **一条写下来的缺口（D14）**：`people_pick_meetings` 为真的那种课停办之后再复办，
**恢复不了谁当初挑了哪几讲** —— 那些选择就是那些行。今天可以接受，因为这个开关
在 L5.8 之前没有界面，还不存在真的选择可丢；重启条件就是那一页做出来的时候，
届时的答案多半是「留着行并标记」而不是删。

### `Session.clean()` 补一条：讲次只挂在 Program 上

跨表规则（判据在 `event.shape` 上），`CheckConstraint` 看不见 —— 同 L5.1 那条窗口
规则、L2×L3 那条含容规则。

⚠️ 它堵的正是这一列自己会开的口子：一个 `single` 活动挂上讲次之后，日程和详情页
都会把它们画出来（两者判据是「有没有讲次」，而那是对的），可 `sign_up()` 读的是
`shape`，于是谁的点名册都不会开 —— 讲次在、点名册永远空、什么都不报错。
反方向自由：**一门还没排期的 Program 仍然是 Program**。

### `Event.clean()` 补两条

1. **`shape` 冻结**：已有讲次或已有报名之后不许改，照 `ParticipationRole.clean()`
   那条「已有报名的工种不许改 `nature`」的形状。
   🔴 **只冻这一列。** 取消活动改的是 `status`、走 `set_status()`，那条路不读
   `shape` —— 冻结永远拦不住「这门课不办了」，且有一条测试专钉这一点。
2. 副开关只在 Program 上成立。

⚠️ 不新增任何 `CheckConstraint`，因此 **`core/constraints.py` 不动**。
写下来是因为 L5.1 和 L5.2 各在这一格上漏过一次（方向相反：那两次是该加没加）。

### 迁移

- `0026_event_shape.py` —— 纯 `AddField` × 2（连 `HistoricalEvent`）
- `0027_backfill_event_shape.py` —— **已经有讲次的活动回填成 `program`**

⚠️ 回填不是可选的。L5.1 / L5.2 是 9 月 5 日和 8 日落的，开发库从那天起就长出了
带讲次的活动，所以「库里每一场都是没有聚会的单场」这句话在这一列到来之前三天就
不成立了。不回填的话第一天就存在上面那种「讲次在、点名册空」的行 ——
而那正是本步要堵的东西，不能和它同一个提交一起发出去。

### 表单与 admin

- `EventForm` 加 `shape`（两档 `RadioSelect`）和 `people_pick_meetings`，
  位置在 `start_time` / `end_time` **之前** —— 它改变那两列的**含义**
  （对 Program 那是学期的两端），先填日期的人已经在答另一个问题了。
- 新 `SessionForm`，`save()` 调 `open_registers_for()`。
  ⚠️ 为什么是表单而不是 `admin.save_model()`：那个钩子被
  `AdminHasNoLogicGuardTests` 禁了，而 `form = ` 是这个文件里的现成做法。
  而这项目里没有任何信号（signal），本轮**不引入第一个**。

### 演示数据

`seed_demo` 里原来一个 `Session` 都没有。加一门 **ESL spring term**：跨期一个
`Event` + 12 讲（5 讲已上、7 讲未上）、学员与助教两个角色、三个人 ——
从第一讲就在的、今天才报名的（决定 18 在一屏上可见）、以及一个按讲记工时的助教
（决定 20 + D43 两个方向的数并排）。

⚠️ 匹配键只用活动名，**不含任何日期** —— 这个文件第 423 行专门有一条注释说明
含 `local_today() - N 天` 的匹配键会让今天跑和昨天跑造出两份。

### 测试

`EventShapeTests`、`ProgramSignUpTests`、`RunCalledOffTests` 三个新类，
另加 `SessionsThroughTheAdminTests` 与 `EventFormTests` 的补充。裸名如下：

- `test_a_new_event_is_one_occasion`
- `test_the_two_predicates_do_not_overlap`
- `test_a_course_with_no_dates_yet_is_still_a_course`
- `test_an_empty_run_can_still_change_its_mind`
- `test_a_run_with_meetings_cannot_be_turned_into_a_one_off`
- `test_a_run_with_signups_cannot_be_turned_into_a_one_off`
- `test_calling_off_a_run_is_not_blocked_by_the_freeze` —— 冻结只碰一列
- `test_a_bare_update_walks_past_the_freeze` —— D14
- `test_picking_meetings_is_refused_on_a_one_off`
- `test_a_meeting_cannot_be_added_to_a_one_off_occasion`
- `test_the_service_refuses_a_meeting_on_a_one_off_occasion`
- `test_signing_up_for_a_program_creates_one_participation` —— 决定 19
- `test_signing_up_for_a_program_covers_every_session`
- `test_joining_in_week_five_is_not_four_absences` —— 决定 18
- `test_a_meeting_still_running_is_put_on_their_register` —— 切点
- `test_a_meeting_already_over_is_not_put_on_their_register`
- `test_signing_up_for_a_one_off_occasion_opens_no_register`
- `test_a_program_with_no_meetings_yet_opens_an_empty_register`
- `test_picking_some_sessions_leaves_the_others_alone` —— 决定 17
- `test_picking_meetings_is_refused_when_the_run_does_not_allow_it`
- `test_signing_up_again_after_cancelling_tops_up_rather_than_duplicating`
- `test_a_meeting_added_later_reaches_everybody_already_signed_up`
- `test_a_meeting_added_later_skips_a_run_people_pick_from`
- `test_a_meeting_added_later_skips_somebody_who_pulled_out`
- `test_cancelling_clears_the_meetings_that_have_not_happened`
- `test_cancelling_leaves_the_meetings_they_already_attended`
- `test_cancelling_leaves_a_meeting_they_are_in_the_middle_of` —— 两个条件那条
- `test_a_bare_create_walks_past_the_register` —— D14
- `test_calling_off_a_run_clears_everybodys_future_register`
- `test_calling_off_a_run_leaves_what_was_already_taught`
- `test_calling_off_a_run_leaves_the_meetings_themselves`
- `test_calling_off_a_run_does_not_mark_anybody_as_withdrawn`
- `test_putting_a_called_off_run_back_on_rebuilds_the_register`
- `test_an_ordinary_status_change_leaves_the_register_alone`
- `test_the_attendance_rate_counts_only_the_meetings_that_happened`

### 守卫（本轮第七条，不在原来那张表里）

`RegisterDeleteGuardTests` —— 点名行的删除只许出现在 `close_future_register()`。

理由：这个删除现在有两个调用方（个人退出 / 整门课停办），而本轮已经为同一形状
付过一次账 ——「删一个角色会把整学期的点名册一起删掉」是 2026-09-08 修掉的 🔴，
当时的成因正是保护条件只看 `Participation.hours`，而一门课的工时全在点名册上。
它是[守卫 4](#本轮新增的守卫九条)（`GeneratedEventDeleteGuardTests`）在低一层上的同一条。

## L5.4 recurring events 那一档：`EventSeries` + 生成器

> ### 2026-09-10 落地，**和 L5.5 / L5.6 一起交付**。开工前的走查挑出九处，逐条改在下面
>
> 🔴 **第一处是范围，而它比后面八条加起来都重要。** 照原文只做 L5.4，交出去的是
> **一张没有任何人写、也没有任何人读的表** —— 生成器在 L5.5、那句删除在 L5.6、
> 页面在 L5.8。而 `events/models.py` 的 `Source` docstring 白纸黑字写着
> 「它唯一的读者是 `_drop_generated_after()`」，所以 `Event.source` 单独落地
> 就是一列**零读者**的字段。
>
> 这个项目为同一形状判过三次：`EventType` 整张表被删（说不出谁读它）、
> L5.3 被走查抓到「只有列和谓词，中间那格是空的」、`people_pick_meetings`
> 勾上会造出一门谁也上不了的课。而 [`participants.md` 第十节](participants.md)
> 2026-09-10 刚为最后那次补了一条判据：
>
> > **读者存在不够，还要有人兑现得了它承诺的事。**
>
> 按这条判据，L5.4 单独落地会**当场违反它**。⚠️ 而 [`participants.md` 第十一节](participants.md)
> 那条唯一还空着的验收（「按规则生成 N 场，N 场归成一组；改规则只动未来」）
> 本身就横跨这三步 —— 任何一步单独交付都勾不上。所以三步一次交付，页面仍归 L5.8。
>
> | # | 原文写的 | 改成 | 为什么 |
> |---|---|---|---|
> | 1 | `Event.series`「可空 FK」，`on_delete` 一个字没写 | 改成 **`PROTECT`** | [D40 第三节](decisions/D40-undo-a-pattern-batch.md)专门为同一形状改过一次决定（`Shift.generated_from`）：`SET_NULL` 造出的是「机器造的、在未来的、不知道自己从哪来」的**孤儿** —— 每个生成器都按 `series=` 过滤，所以再没有任何一个收得走它，它会一直站在列表页上直到有人一行行手删 |
> | 2 | `EventSeriesRole` 的唯一约束只写了约束本身 | 三样一起加，`core/constraints.py` 加映射 | 本仓规矩是「加一条约束 = 话术 + 错误码 + 映射」，少一样 `ConstraintMappingGuardTests` 当场红。全轮文件总表里 `core/constraints.py` 那一格也没提 L5.4 |
> | 3 | L2×L3 含容不变量**一个字都没提** | 模板那一对在**发布时**就验，复用 `refuse_wider_than_event()` | 那个函数自己的注释写着「`bulk_create` 走过去，另一头是一个人报上了他看不见的活动」，而它**没有数据库约束兜底**。模板上错一格不是一次泄露，是**每一场各一次** |
> | 4 | 没提 `org/permissions.py` | 加两行 `view_` | 注册进 admin **不等于**可达 —— L5.2 刚为这一格付过账，症状是「一页没做」 |
> | 5 | 规则只要求带 `UNTIL` 或 `COUNT` | 再加一个**场次上限 52**（决定 31） | 那条挡的是「一直下去」。`COUNT=5000` 完全合法、完全有限，一次点击五千个活动、五千批角色、五千行历史。两个不同的失败，各要一句话 |
> | 6 | 新函数叫 `occurrences()` | 改叫 **`occasions()`** | `events/schedule.py` 已经有一个 `occurrences()`，意思完全不同（把活动摊成日程上占的那些段），而视图层两边都 import |
> | 7 | 「一个展开器，**三个**调用方」 | docstring 如实写「今天只有一个」 | roadmap 里没有任何一步在做「Program 按规则排十二讲」—— 排讲次至今是 admin 一条一条敲。不写清楚就是 L5.1（`Event.duration`）、L5.3（批量建点名行）那个「两份文档互相推」的**第三次** |
> | 8 | `EventSeriesRole` 没写 `history` | 加上 | 它逐列照抄的 `EventRole` 有，理由是 `needed_count` 是对志愿者的承诺 —— 而在模板上那是**对每一场同时**做出的承诺 |
> | 9 | 模板里没有 `status` | 加 `status`，生成时跟模板走（决定 30） | 「先生成、检查一遍、再发布」和「生成即发布」是两种完全不同的产品行为，而这一格原来是空的 |

### 三个决定（2026-09-10，开工前）

| # | 问题 | 定案 |
|---|---|---|
| 29 | 交付范围 | L5.4 + L5.5 + L5.6 一次交付；页面留 L5.8，本轮的门是 admin 上**四个** action（生成 / 即日停止 / 改规则 / 撤销） |
| 30 | 生成出来的活动是什么状态 | **跟模板走**（`EventSeries.status`，默认 draft）。⚠️ 两个默认的失败方向相反，同 `requires_guardian_consent` 那段：默认发布，一个打错的规则是十二场错活动同时发给所有外部志愿者；默认草稿，它是一张有人翻一翻就改掉的列表 |
| 31 | 一条规则最多生成几场 | **52**（一年周更）。跨年的周会因此要重建一条规则，这是有意的代价 —— 活动本来就有结束，正是 D33 第三节那条分歧的整个前提。⚠️ 照 D40 第五节对「七天」的写法：这个数没有别的依据，只有一句常识，试点跑一轮之后回来看它 |

### 两条写下来的缺口（主动接受，各带重启条件）

| 缺口 | 什么时候再看 |
|---|---|
| **模板不带图片。** `services.purge_event_image()` 在活动结束后**删掉文件**，十二场共用一个路径意味着第一场结束的第二天早上，其余十一场全变成碎图标，而且不报错；复制十二份字节则是为一张一个月后按设计会被删掉的图付十二倍存储 | L5.8 做系列页的时候，届时答案多半是「每场复制一份文件」 |
| **撤销窗口沿用 [D40 第五节](decisions/D40-undo-a-pattern-batch.md)的七天**，判据一字不改：「我刚才建错了」→ 撤销，「我们改主意了」→ 改规则 / 即日停止 | 同 D40：试点跑一轮之后。⚠️ 窗口之外什么都没锁死 —— 规则照样能改、能停、场次能一条条删 |

### 落库的形状（已实现，`events/models.py`）

```python
class EventSeries(Audience, ConstraintErrorFieldMixin, TimeStampedModel):
    """一条规则 + 一份模板，生成 N 场**各自独立**的 Event。"""

    AUDIENCE_ON = "series"
    AUDIENCE_PARENT = None
    AUDIENCE_CHILDREN = "roles"
    AUDIENCE_DAY = None                 # 模板自己没有场合 —— 同 Notice

    name / ministry(PROTECT) / owner(PROTECT)
    rule = TextField(max_length=SHORT_TEXT)   # RFC 5545 的 RRULE，不含 DTSTART
    starts_on = DateField() / start_time = TimeField() / duration = DurationField()
    location / description
    status                              # 决定 30，默认 draft
    requires_guardian_consent
    ended_on                            # 「即日停止」
    undone_at / undone_by               # 整批撤销
    history = HistoricalRecords(m2m_fields=["visible_to_ministries"])
```

⚠️ 它和 Program 不是一回事，而这两个词在英文里几乎同义，所以写死在 docstring 上：

- `EventSeries` → N 个 `Event`，各自报名、各自受众、各自出现在列表页；
- Program → 一个 `Event` + N 个 `Session`，报一次管全部。

选哪一个是发布时的三档单选，选完不能互换（那是一次数据迁移）。

⚠️ **载体判定作废的记录**：[推迟清单](deferred.md)里 `Event.parent`（活动系列）写的是
「按 D15 三条件检验 → 自引用 FK 正是对的载体」，其中第二条是「关系自己没有属性」。
而生成规则就是属性 —— [D15](decisions/D15-relationship-carriers.md) 自己盯着这一格，
明写「条件破了就必须升级成表」。

⚠️ 为什么不是「第一场兼作母本」（Google / CiviCRM 的形状）：那让一行同时是
系列和一场，删它、改它各有两种读法，而[三方集成里反复出问题的正是这一点](https://community.zapier.com/troubleshooting-99/new-or-updated-google-calendar-event-triggered-for-old-copies-of-recurring-events-42518)。
独立成表还顺带解决了批次身份 —— 一个系列就是一次批量动作，
[D40](decisions/D40-undo-a-pattern-batch.md) 的 `PatternBatch` 在这里不用单独建表。

`Event` 加两列：`series`（可空 FK，**PROTECT**）和 `source`（`manual` / `generated`）。

### 🔴 那条 `ended_on >= starts_on` 的约束**不存在**，而初稿里有 —— 测试当场抓到

建一批下个月的场次、其中一场有人报名、当天下午撤销：留下来的那一场让系列活着，
`ended_on` 写成今天，于是约束拒绝了一次完全正常的动作。

错在**把这两列读成了一段任期**（`Assignment` / `MinistryRole` 那个形状，两端确实
夹着一件事）。这里不是：`starts_on` 是规则锚在哪一天，`ended_on` 说的是
**这天之后不再生成**。截止早于锚点是一个真实、说得出口的状态 ——
「它还没开始我们就停了」—— 而那正是撤销一批未来的场次的意思。

⚠️ 写下来是因为「两个日期得有先后」是任何人读到这张表时**第一个会伸手去加**的东西，
初稿就是这么加的。

### `EventSeriesRole`

```python
class EventSeriesRole(Audience, ConstraintErrorFieldMixin, TimeStampedModel):
    AUDIENCE_ON = "series_role"
    AUDIENCE_PARENT = "series"          # ⬅️ 走查 3 靠这一行接上含容规则
    AUDIENCE_CHILDREN = None
    series → EventSeries / role → ParticipationRole
    needed_count / stop_at_needed_count / notes    # 逐列照抄 EventRole
    history = HistoricalRecords(m2m_fields=["visible_to_ministries"])
```

生成第 N 场时，每条模板建一行真的 `EventRole`。唯一约束 `(series, role)`。

### 让含容不变量认得第三、第四张表 —— 而**不写第二份规则**

`AudienceContainmentGuardTests` 禁止那三条比较出现在 `refuse_wider_than_event()`
之外，而它本来就收两个 `Audience.Spec`、和表无关。缺的只是
`refuse_bad_audience()` 里那段**靠字符串猜表**的分支：它写的是
`AUDIENCE_ON == "role"` 加一句硬编码的 `row.event`，于是含容规则**只有把父表叫
`event` 的表够得着**，而 `EventSeriesRole` 把它叫 `series`。

所以 `Audience` 上加两个类属性 `AUDIENCE_PARENT` / `AUDIENCE_CHILDREN`，五张表各自
声明一次，`refuse_bad_audience()` 读它们。**规则体一行没动。**

⚠️ 漏掉这一格的表现是最坏的那种：模板那一对**根本不做含容检查**，
因为分支会掉进「走一遍子行」那一支，而那边也什么都找不到。一个字都不报，
另一头是一批角色开给了看不见它们的人。

⚠️ `AudienceIsWiredUpTests` 因此多一条：每张带受众的表都要声明这两个属性，
且声明的路径解析得动 —— 同它已有的那两条，写在 `Audience.__subclasses__()` 上，
第五张表加进来的当天就被盯住。


## L5.5 `events/recurrence.py` —— 纯函数

```python
MAX_OCCASIONS = 52

def has_an_ending(rule) -> bool
def occasions(rule, *, starts_on, start_time, limit=MAX_OCCASIONS + 1) -> list[datetime]
```

⚠️ **改名了**（走查 6）：原文写的 `occurrences()` 和 `events/schedule.py` 里已有的
`occurrences()` 撞名，而那个的意思完全不同（把一场活动摊成它在日程上占的那些段），
且视图层两边都 import。

⚠️ **一个展开器，写给三个调用方 —— 而今天只有一个**（走查 7）。三个是：
recurring events 生成 `Event`（唯一在跑的那个）、Program 按规则一次排完十二讲、
以及 D2a 的 `WorkPattern` 生成 `Shift`。后两个**没有任何一步在做** ——
排讲次至今是 admin 一条一条敲。写进 docstring，做那两步时调这里，别再写一个。

用 `dateutil.rrule.rrulestr()`。`python-dateutil` 已经在 `requirements.txt` 里，
**不引入任何新依赖**（核对过）。不用 `django-recurrence`：它多给的是一个字段类型
和一个 widget，而按 D18 的落点规矩，生成器本来就该是这里的纯函数。

🔴 **墙钟时间，不是绝对时刻。** 规则在 naive datetime 上展开，落出来的每一天再和
`start_time` 拼成当地时刻。所以按当地 19:00 每周重复的活动，跨过十一月第一个周日
之后仍然是 19:00 —— 而它对应的 UTC 时刻变了。反过来做（在 aware datetime 上加七天）
会让那一场变成 18:00 或 20:00，**并且不报错**：日历上仍然是每周二。
这正是 [D33 第二节](decisions/D33-work-schedule.md)给 `Shift` 存 date + time
而不存 aware datetime 的同一条理由。

⚠️ `limit` 默认是 `MAX_OCCASIONS + 1`，**多要一个**：少要一个的话「刚好 52 场」和
「5000 场砍到 52」长得一模一样，而调用方要靠这个区别决定放行还是拒绝。
用 `islice` 而不是先 `list()` —— 一条没有结束的规则是一个无穷迭代器，`list()` 会
在这里挂住，而挂住的表现是一个永远转圈的页面，读起来像故障不像拒绝。

⚠️ `MAX_OCCASIONS` **不放在 `core/limits.py`**：那个文件管的是「一个人手敲进来的值
可以多长」，而这是「一条规则可以造出多少行」。两件事、两种失败（一句超长的描述
vs 五千行数据），放一起会让那个文件自己的判据说不清楚。

必测夏令时切换那一天，且**两个方向都要断言**：当地时间三场都是 19:00，
而它们的 UTC 偏移**确实有两个值** —— 后半句不写，前半句在一个不跨越切换的
fixture 上会永远绿。

## L5.6 生成、改未来、整批撤销

⭐ 删除只许写在一处，照 [D40 第一节](decisions/D40-undo-a-pattern-batch.md) 逐字搬：

```python
def _drop_generated_after(series, after):
    """这三个条件全仓只在这里出现。⚠️ **两个**调用方：即日停止 / 整批撤销 —— D40 的第三个（重算未来）在这一侧不存在，因为规则冻结之后生成器没有该收的行了。"""
```

三个条件：`source = generated`、**还没开始**、**一行报名都没有**。

⚠️ 第三个条件 D40 没有，因为班次没有报名。`Event` 删除会两级级联到
`Participation`，所以漏掉它的后果是删掉工时记录 —— 和 2026-09-08 那次
「删一个角色带走整学期点名册」是同一种损失，高一层。

🔴 **切点是 `start_time`，不是 `end_time`** —— 这是对本仓 2026-08-18 合并出来那条
硬规矩的一次**有意例外**，所以写下来而不是让它看起来像手滑。那条规矩答的是
「它过去了没有」，全仓读 `end_time`（`is_over` / `open_for_signup()` /
`from_today()` / `_meetings_still_to_come()`）。这里问的是另一件事：**它开始了没有**。
一场正在进行的活动是有人站在里面的活动，把它从他们脚底下撤掉，正是
「已经发生的绝不动」要挡的那件事。两个问题、两个列 —— 同 L5.3 那条
「`shape` 判分类、有没有讲次判画什么」。

⚠️ 生成器**不用 `bulk_create`**，和 D40 第二节给 `Shift` 的选择相反，两条理由都
只在这一侧成立：`Event` 有 `history`（受众是发布出去的承诺），而 `bulk_create`
不触发信号，整批生成会在历史表上一个字都不留；受众本身是 M2M，`bulk_create`
根本写不了。D40 那边 `Shift` 没有 history、量是每周全量，那份沉默正是优点。

⚠️ 每一行都走 `full_clean()` 和 `set_audience()`。后者的 docstring 从 2026-08-27
起就把「批三的生成器」写成它存在的理由，本步是那句话变成真的。

```python
def generate_occasions(series, *, generated_by=None)   # 只补不删，见下
def stop_series_today(series)                   # 「即日停止」
def split_series(series, *, changed_by, **fields)   # → (successor, 撤掉几场)
def undo_preview(series) -> UndoPreview
def undo_series(series, *, undone_by)
```

「改规则只动未来」= 老系列 `ended_on = today` + 新建一个系列（Google 的 split）。
不做原地改规则重算：原地改会让「这一场当初是按哪条规则生成的」没有答案。
⚠️ 新系列从**今天**起，不是从老系列的 `starts_on` 起 —— 否则它会去重造已经发生过的
那几场，被「已经站着」的检查静静挡掉，什么都不发生。
⚠️ 角色模板要跟着搬过去：一个没有角色的后继系列是一批谁也报不上的活动，
而它看起来和「有人建了一半」一模一样。

整批撤销照 D40 三步，另加两条自保：
开头**先读一次 `undone_at`**，非空就拒绝而不是静静成功（第六节代价 3 —— 放过去会把
`undone_by` 覆盖成一个什么都没撤的人）；只对 `generated_at >= 今天 − 7 天` 的批次
开放（第五节）。

确认屏照 D40 那一屏：数字真算，**并且把留下来的那部分按原因分组写出来**。
`undo_preview()` 和按钮读的是同一份三条件 —— 一个自己数一遍的屏幕，可以和它下面
那个按钮不一致，而不一致只会在按下之后才显出来。

⚠️ Program 的 `Session` 同理：**有出勤记录的 `Session` 不许自动删**。
两条是同一条规矩在两个层级上。

### 本轮的门：admin 上四个 action，而生成不是保存的副作用

🔴 **生成必须是一个明确的动作**，两条各自都足够的理由：

1. Django 的 `ModelAdmin.save_related()` 先 `form.save_m2m()`、**再**存 inline，
   所以任何挂在保存上的动作跑的时候，系列**还一个角色都没有**（新建时连受众都还没有）。
   它会高高兴兴地生成十二场什么都没开的活动，一个字不报；
2. `save_related` 本来就是 `AdminHasNoLogicGuardTests` 禁的四个钩子之一（D18）。

而这正好落在 D40 想要的形状上：一批东西是有人按下去、看一眼、还能撤销的，
不是他在改描述的时候顺手发生在他身上的。

⚠️ `FOUNDATION_ADMIN_PERMISSIONS` 加两行 `view_`（**只给 view**，逐字沿用 L5.2 那段
理由：建系列是「某个 ministry 的活动」上的动作，按 D20 属于 ministry 那一层，
它的门是 L5.8）。在那之前唯一的写入者是超级用户，和 `Session` 同一个 footing。


## L5.7 L1.4 那几个工时口径要改（决定 20 的代价）

工时现在有两个落点：`Participation.hours`（单场活动）和
`SessionAttendance.hours`（Program 的每一讲）。所以报表要 union 两个来源：

| 口径 | 改法 |
|---|---|
| `hours` | 两个 `Sum` 相加 |
| `hours_records` | 两个 `Count` 相加 |
| `hours_missing` | 「该有工时却没有」的分母现在也有两种行 |
| `hours_per_participant` | 分子 union，分母（帮忙的人）不变 |

⚠️ `people_served` **不用改**：它数的是 distinct contact，一个人上了 12 讲仍然是 1。
⚠️ 满员率 **不用改**：它数角色和报名，和场次无关。

> ### 2026-09-08：上面那张表**漏了两个读者**，而两个今天都在印 0
>
> 「工时」在两张表上之后，要改的不只是 `ministry_report` 的四个口径。走查跑出来
> 两处已经在骗人的地方，都已随本轮修掉：
>
> | 漏掉的读者 | 症状 |
> |---|---|
> | `event_summary()`（R6 / R7） | 一门有五小时助教工时的课，活动报表印 **0**。⚠️ 而 [D38 第七节](decisions/D38-served-as-volunteer-or-work.md) 早就点名说它要和报表一起改 |
> | `/me/` 的 Volunteer hours 卡 | 整学期做了 24 小时的助教，自己的主页上写 **0 小时** —— 那是他最会相信的一页 |
>
> ⚠️ 改法**不是** `Sum` 跨 join（会按点名行数把报名的工时翻倍），
> 也**不是** `distinct=True`（那是对不同的**值**求和，两个 2.5 会折成一个）。
> 两者都静默地错，方向相反，而第二个更糟因为它看起来像是修好了。
> 落法是子查询 / 分开两次聚合。
>
> ⚠️ 还没做完的那半：`_top_participants()` 和 `_monthly_series()` 仍然只读一列。
> 它们排序和分月用，不像上面两处那样印一个绝对数，但同一句话适用 ——
> **「工时」这个词从此在两张表上**。

### 还要多一个数，而它不在上面那张表里

[D43](decisions/D43-hours-given-and-hours-received.md)（2026-09-08，随 L5.2 一起定）：
上面四格 union 的都是**给出去的**工时。「基金会花在他身上多久」是方向相反的第三个数，
`services.hours_received()` 已经算得出来，本步把它摆上报表。

| | 装什么 | 怎么来 |
|---|---|---|
| 上面那四格 | 人给基金会的时间 | 两列 `hours` 相加 |
| 新的这一个 | 基金会给人的时间 | 出勤过的讲次时长之和，不存 |

🔴 **并排，永远不相加。** 加起来是「我们收到的 ＋ 我们发出的」，一个没有定义的量，
而它看起来完全合理 —— [D36](decisions/D36-two-hour-ledgers.md) 那条不变量的第四次应用。

⚠️ 三件事这一步一起做，少一件这个数就会骗人：

1. 它要有自己的标题，**不许**和 Hours 那一组画在一起，更不许有合计；
2. 没有讲次的活动上它**不是 0 是不适用** —— `Sum` 的空值离 `or 0` 只差一个字符，
   而 [D27](decisions/D27-ministry-report.md) 那条「没有和没算不能长得一样」正管这一格；
3. 守卫：D43 第五节把「不许相加」记成了一个**没有守卫的缺口**，重启条件写的就是
   「同屏打印两个数的那一刻」—— 也就是本步。所以这条守卫在这一步补上，
   而不是继续记在缺口清单里。

🔴 这是决定 20（每场一个工时数）唯一的、也是全部的代价，
选的时候就摆出来了。⚠️ 而它带来一条必须写下来的话：
**「工时」这个词从此在两张表上**，任何新写的汇总都要问一句「另一半算了吗」。

## L5.8 页面与路由

> ### 2026-09-05：页面定了，见[又七条](#又七条2026-09-05页面安排)
>
> 原文是「Programs 的页面**本轮不设计**（基金会明说要等后端定完）。本轮只做到：
> 两个谓词、`Session` 和 `SessionAttendance` 两张表、以及 admin 能建能看」。

按决定 22–27，本轮要出三张列表页和**零张**新详情页：

| 路由 | 装什么 |
|---|---|
| `/programs/` | Programs 的列表（`shape=program`），两档共用一页 |
| `/programs/schedule/` | Programs 的日程，画**每一讲**，不画那条 111 天的横条 |
| `/me/programs/` | 我在上的课。⚠️ 同时要把 program 的报名**从 `/me/participations/` 里拿走**，并在 `/me/` 上挂第二个入口 |
| ~~`/programs/<pk>/`~~ | ❌ 不新建视图。详情复用 `/events/<pk>/`，按 `shape` 换一块（讲次表走独立 partial，同 `_event_roles_panel.html` 那一级）。理由见决定 27 |

> ### 2026-09-08：下面这三样**已经做了**，L5.8 剩下的是三张列表页
>
> 走查当天就落了地（详情页的讲次表、那行 When、以及那句摘要），
> 因为第 2 条是一句会发到监护人手机上的假话，不适合排队等一个步骤。
> 本节保留原文，是因为它记着这三样各自为什么不能省。

### 🔴 详情页那行 When 也要换，而决定 27 只说了「换一块」

走查（2026-09-08）打开一个有讲次的活动，页顶是：

```
When: Aug. 9, 2026, 4:05 p.m. — Nov. 7, 2026, 3:05 p.m.
```

三讲一讲都没出现。所以本步要动的是**三样**，不是一样 —— 决定 27 只写了第一样：

| # | 要什么 | 为什么它不能省 |
|---|---|---|
| 1 | 讲次表（独立 partial，按形状换进来） | 决定 27 已写。「这门课都哪几天」得有地方答 |
| 2 | 那行 When **换掉** | 🔴 对 Program，那两列是**学期的两端**，不是一个时段。照现在渲染成带时分的区间，说的是一句假话 —— 而 L5.1 那条注只预料到报表上的 `duration` 会难看，没预料到它会在详情页顶上变成一句错话 |
| 3 | 一句**摘要**（「每周二 19:00–21:00，共 12 讲」） | ⚠️ 十二行日期**不等于**一句摘要。要决定报不报名的人问的是「每周几次、什么时间」，而一张十二行的表把这个问题留给他自己数。这一条决定 22–27 一条都没写 |

⚠️ 第 3 条要从讲次行里**推**出来，不新增字段：规律就在那些行上（同 D43 不存那个数的理由）。
规律不成立时（有人挪了第 7 讲）**如实说**，不要硬凑一句整齐的话 ——
[D27](decisions/D27-ministry-report.md) 那条「没有和没算不能长得一样」在措辞上同样成立。

⚠️ 判据用「**这场活动有没有讲次**」而不是 `shape`，且这不和
[L5.3](#l53-三档单选落在哪以及报一次管全部) 那条「不靠 `sessions.exists()` 判形状」冲突：
那条说的是**分类**（一个还没排期的 Program 也是 Program），这里问的是**显示**
（有讲次就把它们画出来）。两个问题，两个判据。

验收：

- [ ] 打开一个跨期、有讲次的活动 → 页顶不出现那种带时分的长区间
- [ ] 同一页说得出「每周几次、什么时间」，而不用读者自己去数那张表
- [ ] 挪掉其中一讲之后，那句摘要**改口或退让**，不继续声称「每周二」

⚠️ 三张列表页各自都要过 `for_audience()` —— `AudienceIsAskedGuardTests` 和
`RolesAreNarrowedGuardTests` 会盯着，但**别指望守卫兜底**：本轮走查刚证实
`event_signup` 那条路守卫的信号根本触发不到（它用的是 `open_for_signup()`）。

recurring events 那一档的路由照初版：`events/series/new/`、
`events/series/<int:pk>/`、`events/series/<int:pk>/undo/`。
⚠️ `new` 排在 `<int:pk>` 前面，同这个文件里已有的两处。

> ### 2026-09-10：L5.8 拆成 a / b 两半，a 已交付
>
> 上面那句「recurring events 那一档的路由照初版」和整节的 Programs 内容**不是
> 同一件事**，而写在一节里读起来像一件。拆开的理由是它们一样东西都不共用：
> 不共用模型（`EventSeries` / `Event.shape`）、不共用查询、不共用模板、
> 不共用不变量。
>
> ⚠️ 决定 27 那条「详情页只许有一个」在这里**不成立**，别照搬：那条的理由是
> Program 和 single 共用 `Event` 的六样东西（受众、角色、报名、签到、工时、
> 通知），复制一份详情页就是复制六份判断。`EventSeries` 一样都不持有 ——
> 它不是人们报名的那个东西，它是造出那些东西的配方。所以系列页是**新的一页**，
> 而不是 `/events/<pk>/` 换一块。
>
> | | 装什么 | 状态 |
> |---|---|---|
> | L5.8a | recurring events 的发布者入口：发布页第三档 + 系列页 | ✅ 2026-09-10 |
> | L5.8b | Programs 的三张列表页（上面那张表） | 未做 |

### L5.8a 发布者那一端的门（2026-09-10 交付）

L5.4–L5.6 把 `EventSeries` 整条链路做完了，**而它只有超级用户进得来**。
这一步几乎不写业务逻辑 —— `generate_occasions` / `stop_series_today` /
`undo_series` 全都写好、测过、有守卫盯着，缺的只是门。

兑现的是 participants.md 第九节那条：需求 4 的后半句（基金会原话「可以让 admin
**选**……显示成一个条目还是每周一个」）—— 一个**给发布者的选择**，而在这一步
之前没有任何发布者被给到它。

| 路由 | 干什么 |
|---|---|
| `events/publish/when/` | 换「什么时候」那一块（POST，无副作用） |
| `events/series/preview/` | 这条规则会落在哪几天（POST，无副作用） |
| `events/series/<int:pk>/` | 系列页：改配方（GET 渲染，POST 保存） |
| `events/series/<int:pk>/roles/` | 加人手（镜像 `events/<pk>/roles/`） |
| `events/series/roles/<int:pk>/delete/` | 删人手 |
| `events/series/<int:pk>/generate/` | 生成 |
| `events/series/<int:pk>/stop/` | 即日停止 |

⚠️ 初版排的 `events/series/new/` **没有做，而且是故意的**：第三档长在
`/events/new/` 这一张发布页上（决定 32），单独一条 `new` 会变成「同一件事有
两个入口，而两边问的问题只差一块」。`undo/` 同样没做，理由在下面的缺口表。

#### 这一步谈定的六条

| # | 问题 | 定案 | 代价 / 为什么 |
|---|---|---|---|
| 32 | 选中第三档之后 | **同一张表单，只换「什么时候」那一块**。名字/地点/说明/图片/受众一个字不动 | 否掉的是「跳到另一页」：那一版会把填了一半的东西弄丢，而发布者答的本来就是同一个问题 |
| 33 | 人手在哪一屏填 | **和 event 一样**：存完跳到系列页，在那一页加 | 一致性优先于少一次跳转。选了第三档不该遇到一套不一样的东西 |
| 34 | 「生成」按下去做什么 | **让这一批对齐配方** —— 补缺的场次，也把后加的工种补到还没开始的场次上 | 代价见 35。换来的是这个按钮**按几次都安全**，这是它敢只有一个的原因 |
| 35 | 被人手工删掉的角色 | **不放回去**。判据是 `EventRole` 影子表里有没有一条删除记录 | ⚠️ 不新增任何列。`EventRole` 本来就带 `HistoricalRecords` |
| 36 | 一个人手都没有就按生成 | 放行 | 有了 34，留空生成不再是死胡同 —— 回头补工种再按一次就补上了。旧的那条拒绝正是为「没有退路」存在的 |
| 37 | 生成出来的状态 | 表单上每次选（草稿 / 开放报名，草稿是默认值） | 维持现状，不新发明 |

#### 🔴 决定 34 第一版借错了规矩，是被问出来的

第一版的第四条写的是「**这一场已经有人报名 → 整场都不碰**」，理由写的是
「和 `_collectable_occasions()` 同一条规矩：有人站在里面的东西不动」。

用户问：「如果我加的工种不影响已经报名的人，为什么不可以加工种」。

问得对，而这是一次**把一条规矩搬到它不成立的地方**：`_collectable_occasions()`
之所以躲开有人报名的场次，是因为那里的动作是**删除** —— 删除拿走东西。
开一个新工种什么都不拿走：报了名的人握着的是指向**另一个角色**的行，一动没动。
而且一个已经有志愿者的晚上，恰恰是最可能需要多一个门口接待的那个晚上。

所以真正该跳过的判据是**钟**，不是报名：在一个已经过去的晚上开工种，
会凭空写出一笔那天从来不存在的人手缺口（`understaffed()` 和 `_role_gap()`
都不按日期筛，已验证）。四条规则最后是：已经有了 → 跳过；被人手工删过 →
跳过；**这一场已经开始了 → 跳过**；否则 → 补上。

⚠️ 而这个修法当场暴露了我自己的一个 bug：`except ValidationError` 那一支会
留下一个**受众全空**的半成品角色 —— 正是 `refuse_empty_audience()` 存在要
防的那个状态。用 `transaction.atomic()` 存档点修掉。两条测试钉住：
`test_signing_up_does_not_stop_a_later_job_from_being_added`、
`test_an_evening_that_has_happened_gets_no_new_job`。

#### ⚠️ 一个字段画两遍是静默的

发布页把三档单选和「什么时候」那一块**摘出来自己画**（决定 32 要求它们成块），
而 `core/components/form_fields.html` 是逐个字段平铺的。两边都画 =
同一个 `name` 出现两次，浏览器提交的是**后一个**（空的）那份。不报错、
不变红，只是人填的东西没了。

所以 `PublishFormMixin.drawn_separately` 声明哪几格由页面自己画，
`form_fields.html` 跳过它们。⚠️ 两个方向都要命，而且互为一行之差：
多声明一格，那一格从页面上**消失**；少声明一格，它被画两遍。
`NoFieldIsDrawnTwiceTests` 三张页面各钉一条。

⚠️ 那段跳过写成 `{% if ... in ... %}{% else %}` 而不是更顺口的 `not in`：
绝大多数表单没有 `drawn_separately`，Django 的 `{% if %}` 把成员判断里抛出的
异常一律当 False，所以不带 `{% else %}` 的 `not in` 版本会让**全站每一张表单
渲染成空白**。已实测两版。

#### 🔴 走查抓到的那一条：回车键发布不了活动

这一条**测试全绿、页面看着完全正常**，而它是这一步唯一一个让人做不成事的缺陷。

在名称框里按回车，浏览器走的是「隐式提交」，用的是表单里**文档顺序上第一颗**
提交按钮 —— 而没有 JS 时那条换档路留下的「Switch」排在真正的提交按钮前面。
实测：填满一张完全合法的发布表单、按回车，**活动没有被发布**，页面只是原地
换了一次档。没有报错，看起来就像回车没反应。

⚠️ 「它被 JS 藏起来了」不解决这件事，这是当时最容易信的那句话：Chrome 找默认
按钮时跳过的是 `disabled`，不是看不见的。藏起来只让它更难被发现。

修法是在表单最前面放一颗剪到 1px 的**真**主提交按钮，让默认按钮是它。
钉住它的是 `test_pressing_return_in_a_text_box_publishes_rather_than_switches`，
而那条测试断言的是**顺序**，不是点击行为 —— 因为出问题的是顺序。

⚠️ 这一条也是「为什么浏览器走查不能省」的当期例子：D24 要求的那条无 JS 路径，
   它本身是对的（`curl` 走通了全程），代价却落在了有 JS 的那条路上。

#### 走查当天改掉的五样（2026-09-10，都不是逻辑问题）

| 看到的 | 改成 | 为什么记下来 |
|---|---|---|
| 三档单选画成三张大卡片 | `field.html` 新增可选参数 `plain_options`，这一处不画卡片 | 用户原话「跟下面 Who can see 一样」。⚠️ 没有改全局：另外两组单选（报名页的「以什么身份参加」、活动的 `shape`）今天要卡片，而 `checkin_confirm.html` **手写了同一套 class** —— 全局改会让那两处分叉，而分叉了没有任何东西会报错 |
| 「多久一次」是个八行高的文本域 | `EventSeriesForm` 给 `rule` 指定 `TextInput` | `EventSeries.rule` 是 `TextField`（为了 `max_length=SHORT_TEXT`），ModelForm 照列类型给了个文本域。列类型不该决定这一格长什么样 |
| 单选那一组和下面的间距比别处窄 | `#when-block` 手补 `mt-4` | 🔴 **这一条值钱**：字段间距来自 app.css 的 `.field + .field`，而那是一条**相邻兄弟**选择器。这一步把 `.field` 包进了一层 div，相邻关系断了，间距就静默地没了。任何一段「把几个字段包起来自己画」的代码都会踩到它 |
| 日期预览那两句是中文 | 改成英文 | 全站界面是英文，注释是中文。新写的片段里混进了两句面向用户的中文 |
| 「12 occasion(s) generated」 | `_occasions_worded()`，两条消息共用 | 斜杠 s 是网站在告诉用户「这句话是机器写的」。⚠️ `events/admin.py` 里还有四处没动，理由写在那个函数的 docstring 里（两条测试钉着那个字符串，而 admin 是另一拨读者）|

⚠️ 走查中途有一次「JS 没生效」（藏 Switch 那段没跑），当时**排错排错了地方**，
记在这里因为下一个人多半会犯同一个错：我当场断定是 dev server 在吃
`staticfiles/`（collectstatic 的产物）而不是 `static/`，跑了一次 collectstatic
就接着往下走了。

真正的原因是**浏览器缓存**，而那一次 collectstatic 什么也没修好 —— 之后是
`location.reload(true)` 让它变对的。事后拿一个只写进 `static/js/app.js` 的标记
验过：`curl` 到的那一份**带**这个标记，`staticfiles/` 里那份没有。DEBUG 下
staticfiles 走的是 finders，而 `static/` 就在 `STATICFILES_DIRS` 里。

⚠️ 所以规矩是老规矩：`npm run build:js` 之后硬刷新一次，别 collectstatic ——
   而更该记的是，那句「dev server 吃的是 staticfiles」当时听起来非常合理，
   它只是没有人去验。

#### 验收（浏览器，`seed_demo` + `zhangsan@example.invalid`）

- [ ] `/events/new/` 三档单选在页面上；选第三档，**只有中间那块变了**，填了的名字还在
- [ ] 填 `FREQ=WEEKLY;BYDAY=TU;COUNT=12` → 当场列出 12 个日期；打错成周四 → 日期跟着变
- [ ] 保存 → 落在 `/events/series/<pk>/`
- [ ] 加两个工种 → 「生成」→ 12 场出现在 `/events/` 上，各自报名
- [ ] 再加第三个工种，按一次生成 → 12 场都多了这个工种（决定 34）
- [ ] 从第 3 场手工删掉一个工种，再按生成 → **没有被放回去**（决定 35）
- [ ] 一个人手都没有按生成 → 放行；补工种再按一次 → 补上了（决定 36）
- [ ] 「即日停止」→ 未来那几场从 `/events/` 上消失，已发生的还在
- [ ] **关掉 JavaScript** 重走一遍 → 单选旁边那个 Switch 按钮整页重渲，照样能发布，
      而且填了一半的东西还在（图片除外，浏览器不允许回填文件框）

### L5.8c 「多久一次」从手打改成选（2026-09-11 交付）

L5.8a 把门做出来了，而门后第一格问的是
`FREQ=WEEKLY;BYDAY=TU;COUNT=12` —— 一种只有这个仓库里的人认得的语言。
用户原话：**「我不喜欢 How often 自己手打」**。

#### 先纠正一个前提：一周两次本来就做得到

谈的时候提出的问题是「一周两次是不是只能建两条规则」。**不是。**
`FREQ=WEEKLY;BYDAY=TU,TH;COUNT=8` 就是周二 + 周四各一场，一条规则八场，
`EventSeries.clean()` 也过 —— 当场实测过才这么答的。

⚠️ 真正做不到的是**另一件事**：两天共用同一个时刻和同一个时长。
「周二 19:00、周四 10:00」才是真的要两条规则。这句话现在写在
`start_time` 那一格的说明里（用户要求加的），因为勾了好几天之后，
页面看起来像是每天都能各自安排 —— 而这件事出问题的那天，是一个志愿者
按 19:00 去了一个 10:00 就开始的周四。

#### 谈定的六条

| # | 问题 | 定案 |
|---|---|---|
| 38 | 转轮的数字是什么 | **间隔**（每 N 周 / 每 N 个月），1～4。旁边那句「一周 2 次」是**读数**，跟着星期条走，不能点 |
| 39 | 一周几天 | 星期条随便点几天，点几天就是一周几次 |
| 40 | 按月怎么排 | 和「按周」并列的第二档：每 N 个月 + 第〔一/二/三/四/最后〕个 + 星期几 |
| 41 | 「第几个」能不能多选 | **能**。「每月第一个和第三个周六」是真实排法，单选画不出来 |
| 42 | 什么时候停 | 二选一，默认「共 N 场」。RFC 5545 里 `COUNT` 和 `UNTIL` 本来就不能同时出现 |
| 43 | 手打规则还留不留 | 留，收进折叠的「高级」里。选择器只画得出两档，`FREQ=DAILY`、`BYMONTHDAY=15` 得有地方去 |

⚠️ 「第一个周六 + 第三个**周日**」仍然表达不了 —— 星期几只有一个下拉。
   已知代价，走高级框。

⚠️ 「每月第三周」和「每月第三个周六」**不是一回事**，RRULE 支持的是后者。
   谈的时候专门确认过，因为中文这两句听起来一样。

#### 🔴 存储一列没加

九个控件写的是 `EventSeries.rule` 那**一个**字符串：`recurrence.compose()`
拼进去，`recurrence.decompose()` 拆出来。加 `repeat_mode` / `repeat_interval`
那几列会让「这条规则到底是什么」有两个答案 —— 而 D14 的整条规矩就是它只许有一个。
`RecurrenceRuleWordingTests` 钉的正是这一对互为逆运算：拆了再拼，必须逐字不变。
**这一步同样没有迁移。**

#### 走查抓到的四条（测试全绿，页面不对）

| 症状 | 真因 | 记它是因为 |
|---|---|---|
| 轮子怎么滚都停在两格之间，值永远是第一格 | 🔴 **`rotateX` 加在了滚动吸附的目标身上。** CSS 按**变换之后**的盒子算吸附点，所以把格子转一下，吸附点也跟着转走了（实测停在 90.5px） | 这是全场最不像 bug 的一行。修法是每格里再套一层 `.wheel-face`，转里面那层 |
| 填好了选择器，日期预览却一直说「填好上面几格」 | 🔴 `clean()` 写的是 `if not self.errors` 才拼规则 —— 而预览是**边填边跑**的，`ministry` 那时候还空着。一个不相干的字段把规则挡掉了，而且一句话都不说 | 「稳妥起见多判一点」在这里正好是错的。判据必须**只看选择器自己那几格** |
| 一个鼠标滚轮档位从 1 直接跳到 4 | 原生滚动一个档位是 ~100px ≈ 三格；第一版接管之后按累积量算，同样是三格 | 用户原话「电脑操作好像有点不 intuitive」。⚠️ 触摸、触控板、鼠标、键盘是**四种**输入，判据是一次事件的大小：≥40px 或 `deltaMode≠0` 是鼠标档位，走一格；小碎步是触控板，累积够一格才走 |
| Tab 之后焦点不见了，但还能改值 | 升级之后 `<select>` 被剪成 1px，而它仍在 Tab 顺序里 | 滚筒现在自己是控件（`role="spinbutton"`、可聚焦、认上下/Home/End），`<select>` 退成那个值的持有者 |

⚠️ 前两条有同一个形状，值得单独说一句：**它们都不是「写错了」，是「写对了一个
   不成立的地方」** —— 一个把变换加在了不能变换的盒子上，一个把「谨慎」加在了
   不该谨慎的判据上。本轮 L5.8a 那条「借错规矩」（决定 34）是第三个同类。

#### 验收（浏览器，已走）

- [x] 选「按周」，点亮周二 + 周四 → 读数变成「2 times a week」
- [x] 换「按月」→ 星期条整块换成「第几个 + 星期几」，**纯 CSS 换的，没有 JS**
- [x] 点亮第一个 + 第三个、星期六 → 当场出「The first and third Saturday of every month.」
- [x] 日期预览列出 19 Sep · 3 Oct · 17 Oct · 7 Nov · 21 Nov · 5 Dec —— 正是每月第一和第三个周六
- [x] 把「第几个」全取消 → 预览当场变成那句拒绝
- [x] 轮子：鼠标一档一格、触控板累积、键盘上下/Home/End、点一下选那格、触摸走原生惯性
- [x] `<select>` 和七个勾都在 HTML 里 —— 关掉 JS 照样选得动

### L5.8d 日期预览改成小月历（2026-09-11 交付）

L5.8c 之后，「这条规则会造出哪几天」仍旧是一行用「·」隔开的日期。
用户原话：**「只用文字有点不方便」**。

那一行**正确但要人自己在脑子里排**：`15 Sep · 17 Sep · 22 Sep · 24 Sep` 看不出
它其实是「每周二和周四」，而那正是这一块唯一想让人看出来的东西。现在每个涉及
的月份画一张小月历，规则落到的那几天高亮成实心圆 —— 一列竖着的高亮，
「每周二」三个字不用写就看出来了。

⚠️ **月历是服务端摆的**（`schedule.month_grids()`），和日期本身同一个来源。
让浏览器按日期串自己画会出现第二个答案，而这一块存在的全部理由就是
「按下去之前先看见」—— 那个答案必须和生成器的是同一个。

⚠️ 那一行文字**留着**，没有被替换：月历是给眼睛的，那一行是给读屏和
「一眼看总数」的。上面还多了一句「共 N 场，X 到 Y」。

⚠️ 默认铺开 6 个月，其余的收进一个原生 `<details>`（「Show all N months」，
没有 JS 也能展开）。`MAX_OCCASIONS` 是 52，而「每月第三个周六」的 52 场横跨
**四年多** —— 五十二张月历一上来就铺开不是预览，是另一个页面。

⚠️ **是折起来，不是丢掉**（2026-09-11 定）。这一块要答的是「按下生成会造出哪
几天」，而一个答一半的答案会让人回头去数下面那一行文字。实测：一学期那种
12 场横跨 4 个月，**什么都不用展开**；一年周更横跨 13 个月，铺 6 折 7。

⚠️ 两档单选和那个转轮**并排**：它们是同一句话的两半（「按月，每 2 个月」），
上下摆的时候转轮离那个由左边决定的单位词很远。

#### 🔴 测试抓到的那一条：同一天被高亮两次

每张月历的头尾都会带上邻月的几天（10 月那张第一行有 9 月 29 日）。第一版把
「这一天在不在规则里」直接问了那些格子，于是**跨月那一天在两张月历上各高亮
一次**。模板把邻月画成空格，所以**屏幕上完全看不出来** —— 数据里多一天，
画面上一切正常。

抓到它的是一条按「画出来的格子」反推日期、和 `moments` 逐一比对的断言。
如果那条测试只查了「有没有画月历」，这个就过去了。

### L5.8e 滚动生成：规则可以没有结束（2026-09-11 交付）

⚠️ 本节原打算叫 L5.9，但那个号已经被「初版那份 `EventSeries` 哪些留下了」占着，
L5.10 是测试 —— 所以跟着 a/c/d 排下来。

#### 它换掉的是什么

在这之前，一条重复规则**必须自带结束**，而且**超过 52 场直接拒绝保存**。
用户原话：「直接拒绝『超过 52 场』不是很好 prevent 的办法」。

他是对的，而且比「不好看」更实在：每周一次跑一年半是 78 场 —— 完全正常的排法，
会被拒，而那句拒绝给的建议是「build it in shorter runs」，也就是让人替系统
干体力活。

#### 🔴 谈的时候量出来的那张表，是这次设计的转折点

提出的替代方案是「可以有无数个 occasions，但结束必须在三年以内」。
听起来合理，而**三年这个窗口兜不住行数**：

| 排法 | 三年内场次 | |
|---|---:|---|
| 每天 | 1096 | 超过旧上限二十倍 |
| 每个工作日 | 784 | |
| 一周三次 | 470 | |
| 每周一次 | 157 | |
| 每月第三个周六 | 36 | |

一条 `FREQ=DAILY` 的三年规则完全满足「三年以内」，一次点击造出 1096 个活动 ——
比那条 52 挡住的东西大二十倍。**时间边界是时间边界，行数是频率决定的**，
两者不能互相替换。量出来之前，两个方案听起来一样有道理。

#### 定案：生成永远是滚动的

规则可以没有结束；有结束的也只是滚到那个结束为止。按一次「生成」排出
**从今天到一年后**，明年再按一次再往前。

⚠️ 这条路和系统**已有**的设计合拍，不是新发明：决定 34 早就把「生成」定义成
「让这一批对齐配方」，滚动只是同一句话在时间轴上的延伸；而「即日停止」本来就是
系列的出口，现在它成了结束一条**无限**规则的唯一正路。

| | 定案 | 为什么 |
|---|---|---|
| 窗口 | **12 个月**，从这条系列开始那天算起（`recurrence.horizon_for`） | 和「一年周更」那个直觉一致，每周一次正好 53 场 —— 和它替换掉的 52 几乎一样大 |
| 无限规则靠什么接上 | 页面写明「已排到 X」+ **人手再按** | 不做 cron：定时任务挂了没人会发现，而页面上那行字挂不了 |
| 还剩什么拒绝 | 只剩**密度**（`BATCH_CEILING = 750`） | 一年的每日规则 365 场要放行；高级框里一条 `FREQ=HOURLY` 是一年 8760 场，必须挡 |

⚠️ `MAX_OCCASIONS` 改名成 `BATCH_CEILING`，因为**意思变了**：从「一条拒绝」
变成「一张安全网」。名字不跟着意思走，下一个人就会照着旧名字读新行为。

⚠️ 措辞也跟着改：旧的是「more than 52 occasions … build it in shorter runs」，
新的是「repeats more often than this can build」。「分成几段短的」对一条每小时的
规则是完全没用的建议 —— 它再短也是这个密度。

#### 🔴 唯一一个不改就会坏的连带：图片会被误删

`series_with_images_to_purge()` 的判据是「这条系列**没有一场在未来**了」，
而它的 docstring 明写着「never on `ended_on`」，理由是「标记结束是人的动作，
人会忘」。

**那条理由在无限规则下不成立**：一条还活着的无限规则，只要有人忘了回来按
「生成」、最后一场过去，就符合那个判据 —— 图片被删，然后下次按生成，新造的
那一批全部指向一个不存在的文件。这正是 `Event.poster` 那一节警告过的碎图标，
只是触发路径换了一条。

判据改成「没有一场在未来，**而且这条规则不会再产出任何东西**」。一条 `COUNT=12`
跑完的系列照常被清理 —— 这不是把清理关掉，是把「还活着」和「真的完了」分开。
两条测试各钉一半。

⚠️ 这一条是**写计划的时候找出来的**，不是测试抓出来的 —— 它没有任何一条现有
测试会红。这是这一轮里计划模式唯一一次真正赚回成本的地方。

#### 走查抓到的两条

| 症状 | 真因 |
|---|---|
| 「Never」那一档下面多出一个日期框，提交上去的日期永远是空的 | `{% else %}` 里无条件画 `form.ends_on`，加第三档之后被画了**两遍**。`NoFieldIsDrawnTwiceTests` 当场抓到 —— 这条守卫这一轮已经救了两次 |
| 高级框里手写规则，被「This field is required」挡住 | `ends_kind` 是必填的，而勾了「高级」就是不用选择器 —— 一格那个人根本没看的控件把他挡下来了。选择器那几格改成 `required=False`，默认在 `clean()` 里 |

#### 2026-09-11 代码评审之后的八条修正

评审在这一批上抓到八条，全部复现过才动手。**三条值得单独记**：

| | 症状 | 真因 |
|---|---|---|
| 🔴 | 日期预览那两颗翻页键**一页都翻不动**，而页面看起来完全正常 | 模板问 `months.earlier_vals` / `at_the_start`，而 `MonthPage` 给的是 `earlier_page` / `has_earlier` —— 四个名字一个不对。Django 取不到属性时交回空串、不报错，于是按钮身上一个 `hx-vals` 都没有。实测：整段 HTML 里 `hx-vals` 出现 **0** 次 |
| 🔴 | 「快排完了，再按一次生成」对一条**产不出东西**的规则也说 | `is_running_low()` 只问「最后一场近不近」，不问「再按一次还有东西可排吗」。一条跑完的 `COUNT=12` 会在最后一场前六周开始劝人按一颗按不出东西的按钮，而那句话永远不会消失 |
| 🔴 | 明年秋天才开的课，保存合法、预览一场不显示、生成说「什么都没生成」 | 窗口从**今天**算。一条 14 个月后开始的系列整条落在窗口外 —— 而唯一的线索指向两个没有问题的格子 |

第三条改掉了一个口径，**这里改口**：窗口不是「从今天起的一年」，是
**「从这条系列开始那天起的一年」**（`recurrence.horizon_for`）。提前半年排明年的课，
排出来的是它自己的头一年。

⚠️ 它顺带修掉了另一件当时被我当成「窗口的正常表现」写进测试的事：`COUNT=52`
原来只生成 51 场（五十二个周二从下周算起会越过今天+12 个月一点点）。
现在 52 就是 52。

另外五条：`_rule_for` 的往返比较忽略 `BYDAY` 顺序（`BYDAY=TH,TU` 会被判成
「改了规则」而锁死整次保存）、无 JS 那颗 Switch 手写的 `class="btn"` 在 CSS 里
没有任何规则（改走组件）、`bootRecurrenceWords` 每次 HTMX settle 都多挂一个监听、
`_top_up_roles` 用 `.values_list()` 绕开了自己那份 prefetch（实测 4 场 12 条查询、
16 场 24 条，一场一条）、换档时把 `start_time` 跨形状带过去（两档一个是
datetime-local 一个是 time，塞过去浏览器直接丢）。

⚠️ 新增一条守卫 `MonthPageMatchesItsTemplateGuardTests`：模板里每一个
`months.X` 都必须在 `MonthPage` 上存在。它防的是**那一整类**失败 ——
Django 模板取不到属性不报错，所以拼错或重构改名的表现是「那一块安静地什么都
不做」。两个方向都验过：把 `at_the_start` 改个名，它当场红。

#### 顺带：「开多久」拆成三格

用户走查时说「Runs for 很融合有格式错误，分成三个格子中间用：隔开」。

🔴 **它换掉的那一格有一个不报错的陷阱，而 `EventSeries.clean()` 里早就用一条
   🔴 注释记着**：`DurationField` 把光秃秃的数字读成**秒**，所以想写「两小时」
   敲了个 `2` 的人，得到的是五十二个**两秒**的活动 —— 而约束只挡得住零和负数，
   所以一句话都没有。三个格子（时 : 分 : 秒）让那个歧义根本不存在。

⚠️ 底下仍然是同一个 `DurationField`：`DurationBoxes.value_from_datadict()`
   把三格拼回 `"H:MM:SS"` 交给它解析。校验、约束、admin 那一侧一个字没动 ——
   admin 走的是 `EventSeriesAdminForm`，它没有这个部件。

三条写下来的坑：

| | |
|---|---|
| 循环变量必须叫 `widget` | Django 的部件模板读上下文里那个叫 `widget` 的东西。叫 `part` 的那一版不报错，只是三格全渲染成**外层**部件 —— 三个一模一样的 `id_duration`，`NoFieldIsDrawnTwiceTests` 抓到 |
| `use_required_attribute()` 交回 False | `required` 会被发到每一个子部件上，于是「两小时」必须写成 `2` `0` `0`。留空分和秒正是这个控件想让人能做的事 |
| 宽度写在 `.duration-part` 上，不写在 input 上 | `.field input:not(...)` 那条是 `width:100%` 且特指度更高。而且列宽本来就该由列定：不写的话三格被底下的单位字（hours 比 min 长）撑成不一样宽 |

#### 验收（浏览器，已走）

- [x] 「Runs for」是三格 `1 : 30 : `，各 72px，底下标着 hours / min / sec
- [x] 选「Never」→ 预览说「52 occasions over the next year … it repeats until you stop it」
- [x] 发布 → 系列页说「Booked through 7 Sep 2027」
- [x] 按生成 → 52 场；再按一次 → 「Nothing new to make」，一场都不多
- [x] `COUNT=500` → **不再被拒**，预览出一年那 52 场
- [x] 「到 3026 年」→ **不再被拒**，同上
- [x] 高级框 `FREQ=HOURLY;COUNT=9000` → 被拒，而且那句话说的是**太密**不是太长
- [x] 「即日停止」→ 未来的撤掉、已发生的留着（回归）

### L5.8f 撤销一批 / 改规则，交到 ministry admin 手里（2026-09-11 交付）

两个服务 L5.4–L5.6 就写好、测过、有守卫盯着，**而只有超级用户按得动**。
ministry admin 的退路一直只有「即日停止」。

而这不只是少两个功能 —— 冻结那句拒绝写着「Stop this series instead, and
**build the next one**」，**而没有任何控件做得了这件事**。一句指着不存在的控件
的话，正是这个仓库反复付账的那一种。

#### ⚠️ 即日停止和撤销到底差在哪（会被反复问到，所以记在这里）

谈的时候问到这个，而答案不是「一个有记录一个没有」——`EventSeries` 带影子表，
两个动作都进历史。真正的分界只有一条：

| | 即日停止 | 撤销 |
|---|---|---|
| 收未来那些场次 | `_drop_generated_after()` | **同一句**（D40 唯一的那条不变量） |
| 系列那一行 | 永远留着，写 `ended_on` | 一场都不剩 → 整行删掉，图一起清 |
| 时限 | 随时 | 建成后 7 天（`UNDO_WINDOW`） |
| 做过一次 | 可以再做 | 拒绝（`AlreadyUndone`） |

它们答的是两个问题：**撤销说「这批东西本来就不该存在」，停止说「它存在过，
但到此为止」**。停止那一行必须留着 —— 它是「为什么只有六场」的唯一答案。

⚠️ 而一旦有东西留下来，撤销**连行也删不掉**，代码里就退化成一次停止加一条
「谁撤的」。两者只在「什么都没留下」那 95% 的情况下真正分开 —— 而那正是 D40
说这个功能值得做的理由。

#### 改规则：就地改，保存时拦一屏确认

否掉的是 admin 那一版（按一下**先**停掉旧的、给你一份副本去改）：世界在人想好
新规则**之前**就变了。新的顺序是 **改 → 保存 → 确认屏 → 才动**，而人填的新规则
直接用在新系列上，不必再填一遍。

⚠️ `UNDO_WINDOW` 那个七天**一个字没动**（谈定）。D40 第五节自己写着它是
「本条最可能定错的一个数…试点跑一轮之后回来看它」—— 这一步只开门，不改数：
把一个没有依据的数在开门的同时一起改，就没有任何一轮数据说得清是哪一个改动
起的作用。

#### 🔴 做的时候踩到的三条，全都是「手里这个对象不等于那一行」

| 症状 | 真因 |
|---|---|
| 确认之后整件事**无声地什么都没做**（系列数不变、`ended_on` 仍是 None、页面退回原处） | 传给 `split_series()` 的 `series` **就是 `form.instance`**，而表单在 `_post_clean()` 里已经把新值写进了这个内存对象 —— 它最后 `stop_series_today()` 时的 `full_clean()` 于是拿着一个「规则和库里不一样」的对象**再撞一次冻结**。改成重新取一行 |
| 新系列建出来了，但带的是**旧**规则 —— 而人刚在上一屏读过新规则 | 🔴 Django 的 `add_error()` 会把出错字段从 `cleaned_data` 里**删掉**（`forms/forms.py`：`if field in self.cleaned_data: del …`），而冻结那条错误正挂在 `rule` 上。于是 `cleaned_data["rule"]` **不存在**，`split_series()` 静静回退到旧规则 |
| 新系列被自己的 `clean()` 拒掉（「周二不在它重复的日子里」），而且是 500 | 我把 `starts_on` 也传了过去，它盖掉了算好的 `resumes_on` —— 而表单上那个「第一场」说的是**旧**系列什么时候开始的 |

⚠️ 三条都不报错、或者报得离题，而**第二条尤其**：页面照常说成功。钉住它的是
`test_confirming_starts_a_new_series_with_the_rule_i_typed`。

⚠️ 顺带把 `split_series()` 的拒绝接住落成一句提示，不再冒成 500。

#### 验收（浏览器，已走）

- [x] 把每周二改成每周四 → 保存 → 出确认屏（12 场撤回、新系列从 9 月 17 日起、明说不自动生成）
- [x] 确认 → 落在新系列页，旧的停在今天、场次归零、工种照搬
- [x] 只改说明 → 照常就地保存，不出确认屏

### L5.8g 「撤销」和「即日停止」合并成一颗键（2026-09-11 交付）

L5.8f 刚把两颗键交到 ministry admin 手里，用户当场提出：

> 并不是每个 admin 都会区分即日停止和撤销，education 成本不小，而且如果要选
> 撤销，但是不清楚意思选了即日停止，很难 undone。

#### 判断成立，而真正的论据比「教育成本」更硬

**那个分支代码里一直是自动的。** `undo_series()` 合并之前就长这样：
有东西剩下写 `ended_on`（= 即日停止），什么都没剩就 `delete()`（= 真的撤销）。
两颗键之间那道选择题，系统反正会自己重答一遍 —— 而让人在两个「系统反正会自己
决定」的选项之间选，是最难辩护的一种界面。

实测过的三件事：

| | |
|---|---|
| 按错的代价**不对称** | 误按撤销结果完全一样；误按停止要再按一次撤销才补得回来，而**那条补救路径需要的正是那个人没有的知识** |
| 影子表记得「是谁」 | `HistoryRequestMiddleware` 装着，所以 `undone_by` 那一列记的东西是第二份 |
| 那两列在这一侧从没有过理由 | D40 要它是因为「`bulk_create` 不触发信号」，而 D40 §8 自己写着这条在活动侧是反过来的 —— 当时没把线索跟到底 |

#### 定案

一颗键 `Stop this series`，按「有没有人报名」自动分支。**七天窗口取消**
（「有没有人报名」是对同一件事的直接测量，窗口只是代理指标），
**`undone_at` / `undone_by` 两列删掉**，admin 四个 action 变三个。

⚠️ 那两列是在**未合并的** 0028 里建的，所以直接从那条迁移里拿掉 ——
当初就别建，而不是建完再删。**没有新增迁移文件。**

⚠️ `split_series()` 用的是拆出来的 `_stop_from_today()`，**不删行**：
改规则的结果是「这一条停掉、那一条接上」，旧的那一行就是新那条的来历。
用会删行的那个，一条没人报名的系列改个规则旧行会凭空消失 —— 从外面看就成了
「就地改了规则」，而那正是整个 split 存在要避免的事。

#### 🔴 同一个 bug 一天之内出了两次

共用片段 `_withdrawal_summary.html` 要一个叫 `taken` 的变量：

| 哪一处 | 传进去的叫什么 | 后果 |
|---|---|---|
| 站点确认屏（L5.8f） | `preview` | 「occasion still to come withdrawn」—— 数字整个不见 |
| admin 确认屏（本步） | 循环变量还是 `preview` | 「 个场次会被移除」 |

两次都**不报错**，页面照常渲染。第一次之后我加了一条读 HTML 的测试，第二次是
那条测试的同族（`test_the_confirmation_screen_names_what_it_will_keep`）抓到的 ——
它断言的是「留下来的原因」那句话，而数字不见时那句话也跟着不见了。

⚠️ 教训写下来：**断言 context 的测试对「模板取不到变量」这一类失败是瞎的。**
Django 取不到就当空串，不抛任何东西。

#### 顺手修掉的一个死胡同

admin 确认屏那颗提交键是 `{% if offerable %}`，而 **`offerable` 从来没有被传进
上下文过** —— 于是整张确认屏看得见数字、按不下去。合并时才发现。

#### code review 抓到的四件事（2026-09-11，同日）

| | |
|---|---|
| 🔴 只改开始时间／只改时长，改规则那条路**静默地用回旧值** | `add_error()` 把出错字段从 `cleaned_data` 里删掉，而冻结那条错误挂在 `GENERATION_FIELDS` 里第一个变了的字段上 —— 规则没动的时候那就是 `start_time` 或 `duration`。`rule` 那个出口早就补过了，另外两个没有。实测：POST 20:00 → 新系列 19:00 |
| 🔴 `series_form.html` 少一个 `</div>` | 「Roles on this rule」和整个工种面板渲染在按钮那一排**里面**，当 flex item。浏览器自己补上闭合标签，所以什么都不报，2211 条测试全绿 |
| ⚠️ 那句成功提示报的是**新**名字 | `series` 就是 `form.instance`，表单已经把新名字写进去了。同一次保存里改了名，那句话就用新名字说旧系列停了 |
| ⚠️ 系列页每渲染一次白算一遍 `withdrawal_preview()` | 注释写着「确认屏的依据」，而确认屏是另外三个视图、各自显式传。这一页的模板一个字都没读过它，而它要走遍这条系列的每一场 |

⚠️ 另外两条是文档和代码对不上：D40 §9 写着确认屏「站点和 admin 两处共用」，
而 admin 那一页其实还是自己写的一套说法；`stop_series` 叠了两个
`@transaction.atomic`（改名时留下的）。都已改正。

#### 🔴 新守卫：`TemplateDivsAreBalancedGuardTests`

那个少掉的 `</div>` 是**这个仓库没有任何东西在看的一类失败**：浏览器替你补上，
补在错的地方，页面照常出，测试照常绿 —— 因为每一条断言钉的要么是 context、
要么是 body 里的一个字符串，而这两样在页面被重新拼装之后都还在。

守卫读模板源码（不是渲染结果，理由和 `TemplateCommentsAreClosedGuardTests`
一样：没有测试渲染过的模板照样要管），逐个数 `<div>` 和 `</div>`。
⚠️ 写完先在**旧代码**上跑了一遍确认它会红 —— 全项目只有 `series_form.html`
一个不平衡，所以这条守卫今天就是真的。

⚠️ 一份「自己开了框、留给父模板关」的片段会被它误伤，这是它明写的取舍：
今天项目里没有这种东西，而一份只有从对的父模板包进去才闭合的片段，比它抓的
这个问题更糟。

#### 验收（浏览器，待走）

- [ ] 系列页上**只有一颗**「Stop this series」
- [ ] 干净的一批 → 确认屏说「系列会被整个删掉」→ 确认 → 落在 `/events/manage/`
- [ ] 有人报名的 → 确认屏改口说「系列留着」→ 确认 → 行还在、报了名那场留下
- [ ] `generated_at` 改到 100 天前 → 照常能按（窗口没了）
- [ ] admin 里选一条 → **先出确认页**，键按得下去 —— ⚠️ code review 之后那一页改成包站点的两份片段了，要重走
- [ ] 已经停过的系列 → 页面上不再显示那颗键

## L5.9 初版那份 `EventSeries` 哪些留下了、哪些作废

| 初版写的 | 现在 |
|---|---|
| `EventSeries` 一条规则生成 N 场 | ✅ 留下，但只服务 recurring events 那一档 |
| `EventSeriesRole` 模板表 | ✅ 留下，同上 |
| 纯函数生成器 | ✅ 留下。⚠️ 「三个调用方」这句话 2026-09-10 改口了：**今天只有一个**（recurring events），另外两个还没有任何一步在做，见 L5.4 走查第 7 条 |
| 一句删除、整批撤销、改未来 = split | ✅ 留下，多一条「有出勤的 Session 不许删」 |
| 「例会是 `Shift`」那条边界 | ✅ 留下，写进 `Session` 的 docstring |
| ⚠️ 「一门课十二讲 = 十二场 `Event`」 | ❌ **作废**。那是 recurring events，而一门课是 Program：一个 `Event` + 十二个 `Session` |
| ⚠️ 「`EventSeries` 装 Program」 | ❌ **作废** —— 两者现在是两个东西，见 L5.4 的 docstring |

## L5.10 测试

生成与规则：

- `test_a_weekly_rule_without_an_end_is_refused`
- `test_the_local_time_survives_a_daylight_saving_change`
- `test_one_expander_serves_both_shapes`

Program（决定 16–20）：

- `test_a_program_is_one_event_with_many_sessions`
- `test_signing_up_for_a_program_creates_one_participation`
  —— ⚠️ 决定 19。它同时钉住 `signups` 不会因为一期课暴涨
- `test_signing_up_for_a_program_covers_every_session`
- `test_picking_some_sessions_leaves_the_others_alone`（决定 17）
- `test_joining_in_week_five_is_not_four_absences`（决定 18）
  —— ⚠️ 出勤率的分母是 8 不是 12
- `test_hours_on_a_session_are_counted_by_the_report`（决定 20 的 union）
- `test_a_seat_in_a_program_still_records_no_hours`
  —— L1/L4 那条规则在新表上同样成立
- `test_a_session_somebody_attended_is_never_deleted_by_the_generator`

recurring events（2026-09-10 落地，**九个类 90 条** —— ⚠️ 这个数被审查抓到过一次「写着 34」， 下面是原文那五条，
✅ 的实际落在哪个类写在后面）：

- `test_twelve_occasions_are_generated_with_their_roles` —— ✅ `EventSeriesTests`
- `test_changing_the_rule_leaves_past_occasions_alone` —— ✅ `SeriesChangesTests`
- `test_an_occasion_somebody_signed_up_for_is_never_deleted` —— ✅ 同上
- `test_undoing_a_batch_says_what_it_will_leave_behind` —— ✅ `UndoSeriesTests`
- `test_undoing_right_after_creating_leaves_nothing_behind` —— ✅ 同上

⚠️ 原文这五条**漏了三类**，各自都是落地时才发现有话要说的：

| 漏的那类 | 补了什么 | 为什么它不能省 |
|---|---|---|
| 规则本身的**第二种**拒绝 | `test_a_rule_that_would_run_past_a_year_is_refused`、`test_a_rule_nobody_could_parse_says_so_in_its_own_words` | 「带 UNTIL 或 COUNT」只挡住一种失败（走查 5） |
| 那三个条件的**另外两个** | `test_an_occasion_added_by_hand_is_never_deleted`、`test_an_occasion_already_under_way_is_left_alone` | 原文只测了「有人报名」那一个。人手加的那一条是 `source` 这一列存在的全部理由；正在进行的那一条钉的是切点用 `start_time` 不用 `end_time` |
| 模板上的**含容不变量** | `test_a_template_role_wider_than_its_series_is_refused`、`test_a_generated_occasion_inherits_the_series_audience` | 走查 3。模板上错一格是每一场各一次泄露，而它没有数据库约束兜底 |

⚠️ 另外三条是这一轮**测试自己抓出来的设计问题**，各配了一条：
`test_a_batch_with_survivors_is_stopped_rather_than_deleted`（`ended_on` 那条约束是错的，见 L5.4）、
`test_undoing_twice_says_it_is_already_undone`（D40 第六节代价 3）、
`test_the_preview_and_the_button_agree`（确认屏和按钮读同一份条件）。

admin（本轮唯一的门，所以是唯一测得到的门）：`SeriesThroughTheAdminTests`，
其中 `test_generating_from_the_admin_reaches_the_service` 钉的正是
「生成不能是保存的副作用」那条 🔴。

两个谓词：

- `test_the_two_predicates_do_not_overlap`
  —— ⚠️ 页面怎么用还没定，但「一场活动只属于其中一个」现在就要钉住

---

# 本轮新增的守卫（九条）

| # | 名字 | 盯什么 |
|---|---|---|
| 1 | `AudienceIsAskedGuardTests` | 调用 `visible_to_participants()` 的函数体里必须同时调 `for_audience(` |
| 1b | `RolesAreNarrowedGuardTests` | 2026-08-29 加的第六条：函数体里出现 `select_related("role")`（也就是在**列角色行给人看**）的，必须同时调 `for_audience(`。点名三处管理侧例外 |
| 2 | `AudienceContainmentGuardTests` | 「角色的范围 ⊆ 活动的范围」那三条比较只许出现在 `refuse_wider_than_event()` 里。⚠️ 改成多选之后可比的东西变多了，这条比枚举时代更必要 |
| 3 | `HoursWriteGuardTests` | `.hours =` 只出现在 `events/services.py`（现在就成立，这一条是把现状钉住） |
| 4 | `GeneratedEventDeleteGuardTests` | 2026-09-10 随 L5.4–L5.6 落地：生成场次的那三个删除条件只出现在 `_drop_generated_after()`。信号是「函数体里同时出现 `Source.GENERATED` 和 `.delete(`」，白名单只有那一个名字 —— 照 D40 第一节那条不变量的写法，它盯的是**条件**而不是「这几个文件可以删」，所以 D36 代价 4 警告的那种「白名单越放越宽」在这里没有入口。守卫 6 在低一层上的同一条 |
| 5 | `LocalDayInSqlGuardTests` | `TruncDate(` 只出现在 `on_the_books_exists()` 所在的文件，且那一行带 `tzinfo=` |
| 6 | `RegisterDeleteGuardTests` | 2026-09-09 随 L5.3 加的第七条：点名行的删除只许出现在 `close_future_register()`。守卫 4 在低一层上的同一条，而这一层已经出过一次事故（删角色带走整学期的点名册） |

每一条都要做双向验证：故意写错一处，确认它真的红 —— 这是本项目对守卫的既有要求，
而守卫一和守卫五都属于「不做反向验证就等于没写」的那一类。

⚠️ 第六条的双向验证当场抓到了它自己：初版**没红**，因为
`SignUpForm.__init__` 里那行调用**上面的注释**写着 `for_audience()`，
守卫把注释读成了调用。所以它先剥掉 docstring 和 `#` 注释再找 ——
一个注释就能满足的守卫比没有守卫更糟，它报的是一份它从没检查过的安全。
（信号选 `select_related("role")` 而不是 `with_signup_counts(`：后者每一处容量
判断都在调，白名单会长到比被保护的地方还多，而那正是守卫失效的方式。）

# 本轮要动的文件总表

清点用。批次列写「一/二/三」。

| 文件 | 批 | 干什么 |
|---|---|---|
| `events/models.py` | 一二三 | `nature`、`NOT_APPLICABLE`、新约束、第二个兜底工种、可见性的两个布尔 + 一张多对多（`Event` / `EventRole` 各一套）、`refuse_wider_than_event()`（⚠️ `Audience` 和 `AudienceQuerySetMixin` **2026-08-31 搬去了 `org/audience.py`**，留在这里的只有事件×角色那条含容规则，见 [D41 第四节](decisions/D41-notices-are-not-events.md)）、`Event.shape` + 两个谓词、`Session`（+ `duration`）、`SessionAttendance`（+ `records_hours` / `hours_received`）、`EventSeries`、`EventSeriesRole`、`Event.series` / `Event.source` |
| `events/services.py` | 一二三 | `add_session()`（L5.1）、`add_attendance()` / `record_session_hours()` / `hours_received()`（L5.2）；`on_the_books_q()` / `on_the_books_exists()`、`default_served_as()`、`record_hours()`、`check_out()`、`create_participation_role()`、`ministry_report()`、`_people_served()`、`eligible()`（⚠️ `eligible_role_ids()` 判它不建，见 L2.4 那个补框）、`sign_up()`、系列的生成与撤销、⚠️ L5.7：工时的四个口径要 union `SessionAttendance` |
| `events/forms.py` | 一二三 | `RoleChoiceField`、`SignUpForm`、`EventRoleForm`、`EventForm`、`EventPeriodForm`。⚠️ **「`EventForm` 加三档单选」和「新的 `EventSeriesForm`」这一轮都没做** —— 第三档是 L5.8 的事，进了缺口表 |
| `events/views.py` | 一二三 | `_visible_events()`、`_schedule()`、`_detail()`、`event_signup`、`event_registrations`、`event_attendance`、系列的三个视图 |
| `events/urls.py` | 三 | 系列的三条路由 |
| `org/permissions.py` | 三 | ⚠️ 原计划列在「不动」里，L5.2 推翻了 —— `FOUNDATION_ADMIN_PERMISSIONS` 加 `events.view_session` / `events.view_sessionattendance`，否则注册了也在 admin 首页上看不见。⚠️ L5.4 再加两行（`view_eventseries` / `view_eventseriesrole`），同一条理由第二次（走查 4） |
| `events/tokens.py` | 三 | ⚠️ 同样原计划列在「不动」里（理由是「收窄的是发现，不是已经拥有的行」—— 那句话对受众成立，对讲次不成立）。码从按活动改成按讲次，取消 `WINDOW_BEFORE`，见 [D28](decisions/D28-qr-checkin.md) |
| `events/schedule.py` | 三 | 新的 `Occurrence` / `occurrences()` / `meeting_summary()` / `when_line()` —— 日程按讲次画，详情页那行 When |
| `dashboard/services.py` | 三 | L5.7 漏列的读者之一：`/me/` 的工时卡只读一列，一个整学期的助教在自己主页上看到 0 |
| `events/admin.py` | 三 | ⚠️ 原计划整张表都没列它。L5.2 挂了 `Session` / `SessionAttendance` 两张表，L5.3 加 `ShapeFilter`（两个谓词今天唯一的读者）和 `SessionAdmin.form` |
| `events/management/commands/seed_demo.py` | 三 | ⚠️ 同样没列。批一有整整一步（L1.5）在做演示数据，批三一步都没有 —— 而 L5.3 是「一门课报一次」第一次能在浏览器里走通的时刻 |
| `events/admin.py` | 一二三 | ⚠️ L5.4 加 `EventSeriesAdmin` + `EventSeriesRoleInline` + **四个 action**（生成 / 即日停止 / 改规则 / 撤销），另加 `EventAdmin` 的 `series` 列和筛选，撤销那个渲染一张确认页 `templates/admin/events/eventseries/undo_confirm.html`。🔴 生成是 **action 不是保存的副作用**，两条理由见 L5.6。⚠️ 两个 action 都要 `permissions=["change"]` —— 少了这一行，`org/permissions.py` 里那个只给 `view_` 的授权**一点都不成立**（审查第 1 条）。`EventAdmin` 另加 `readonly_fields = ["series", "source"]` —— 那一列决定一条规则能不能把这一行收回去，不是一个偏好，见计划外记录第 2 条。`ParticipationRoleAdmin` 加 `nature`；`EventAdmin` 和 `EventRoleAdmin` 各加三个可见性字段；`Session` / `SessionAttendance` / `EventSeries` 注册。⚠️ `Session` 那一笔 L5.1 落地时漏了，L5.2 一起补 —— 在那之前那张表只有测试碰得到 |
| `events/recurrence.py` | 三 | 新文件，纯函数。⚠️ 对外是 `occasions()`、`has_an_ending()` 和 `looks_like_a_rule()`，**不叫 `occurrences()`** —— 那个名字 `events/schedule.py` 已经占了，意思完全不同（走查 6） |
| `org/audience.py` | 三 | ⚠️ 原计划整张表都没列它。`Audience` 加 `AUDIENCE_PARENT` / `AUDIENCE_CHILDREN` 两个类属性，`AUDIENCE_HEADING` / `EMPTY_AUDIENCE_MESSAGE` 各加两条 —— 含容不变量原来**只有把父表叫 `event` 的表够得着**（走查 3） |
| `notices/models.py` | 三 | ⚠️ 同上：`Notice` 声明那两个属性各为 `None`。抽象类给第二个的默认值是 `"roles"`（五张表里三张的形状），而这张表正是当年为「猜而不是声明」付过账的那一张 |
| `events/migrations/0016_participationrole_nature.py` | 一 | 新 |
| `events/migrations/0017_served_as_not_applicable.py` | 一 | 新 |
| ~~`events/migrations/0018_audience_and_signups.py`~~ | 二 | ❌ **没有这个文件**，2026-09-05 划掉。批二实际拆成了四条：`0018_second_catch_all_role`（L1.6，下面单独列着）、`0019_event_audience`（含回填）、`0020_audience_reverse_name`、`0021_drop_event_type`。后三条各自在正文里有说明，唯独这一行从没跟着改 —— 于是总表里一度同时存在两个 0018 |
| `events/migrations/0022_session.py` | 三 | 新（L5.1）。⚠️ 编号：批二实际拆成了 0018–0021，所以批三从 0022 起 |
| `events/migrations/0023_audience_help_text.py` | 二 | ⚠️ 2026-09-08 补进总表 —— 它 2026-09-04 就落了地，而这张表从没记过它。于是 L5.2 的编号是 **0024** 不是 0023 |
| `events/migrations/0024_session_attendance.py` | 三 | 新（L5.2）。纯 `CreateModel`，无回填，同 0022 的形状 |
| `events/migrations/0028_event_series.py` | 三 | 新（L5.4–L5.6，2026-09-10）。两张表 + 两张历史表 + `Event` / `HistoricalEvent` 各两列。**无回填**，而这一句是核对过的：两列的默认值对库里现存的每一行都成立，因为这条迁移之前没有规则造得出行来。⚠️ 对照 0027 —— 那一条**必须**回填，L5.1/L5.2 已经长了三天带讲次的活动 |
| `core/constraints.py` | 一三 | ⚠️ L5.4 再加三行（`EventSeries` 一条 + `EventSeriesRole` 两条）—— 全轮这张表原来没提 L5.4，而少一行 `ConstraintMappingGuardTests` 当场红（走查 2）。`CONSTRAINT_FIELD` 加一行；⚠️ 批三 L5.1 又加两行（`Session` 的两条约束）—— 这一格 2026-09-05 之前写的是「一」，而批三加约束不改它，`core/tests.py` 那条守卫会当场红。L5.2 再加**五行**（`SessionAttendance` 那一组） |
| `core/timeutils.py` | 一 | `local_day()` —— `local_date_of()` 的 ORM 双胞胎，`tzinfo` 包在里面 |
| `core/querysets.py` | 一 | `in_effect_on()` 的 docstring：`on` 现在也可以是数据库表达式 |
| `core/tests.py` | 一二三 | 五条新守卫；⚠️ L5.4 再加三条：`GeneratedEventDeleteGuardTests`、`PosterIsAskedGuardTests`、`AdminActionsDeclarePermissionsGuardTests`，**每一条都做过双向验证** |
| `events/tests.py` | 一二三 | 上面列的全部测试 |
| `events/templates/events/_report_body.html` | 一 | 分母说明、`hours_missing` 措辞、People served |
| `events/templates/events/_attendance_row.html` | 一 | attending 不画工时 |
| `events/templates/events/event_registrations.html` | 一 | 身份下拉按角色档位收窄 |
| `events/templates/events/my_participations.html` | 一 | 不印 Not applicable |
| `events/templates/events/event_report.html` | 一 | 同上 |
| `events/templates/events/_event_roles_panel.html` | 一二 | 档位列；公告的空状态 |
| `events/templates/events/_event_detail_body.html` | 一二 | 档位列；三种空状态（「还没开」/「没有一个是给你的」/ 公告）；看全表的人那一句常驻文案 |
| `events/templates/events/_period_filter.html` | 二 | 多一个 kind 下拉 |
| `events/templates/events/event_form.html` | 二三 | `audience`；系列入口 |
| `events/management/commands/seed_demo.py` | 一二三 | ESL 工种与活动；一场内部活动；一个系列 |
| `docs/planning/diagrams/src/page.html` | 三 | ⚠️ 2026-09-08 起**不重画**，改为在 `README.md` 的已知不准清单里逐张记（`Session`、`SessionAttendance` 都已在册）。原计划：ERD 加三个字段和两张表，DFD 加一条生成的路，表册加两行。⚠️ 改完要按 `docs/planning/diagrams/README.md` 重新生成 `data-and-flow.html`，那一步要 `npm i mermaid puppeteer-core` |

| `core/management/commands/check_deployment.py` | 一 | L1.6：工种表的门槛从 2 提到 3 |
| `events/migrations/0018_second_catch_all_role.py` | 一 | 新（L1.6），数据迁移 |

⚠️ 不动的文件，写下来是因为它们看起来该动
（⚠️ 这一段 2026-09-08 划掉了三行中的两行 —— `org/permissions.py` 和
`events/tokens.py`，各自的理由见上表。留下来的那条判据本身没问题，
错在它假设「本轮不碰这些东西」，而本轮碰了）：
~~`org/permissions.py`~~（受众不是授权，是可见性；授权仍然只有 MinistryRole 那一套）
—— 🔴 **2026-09-08 划掉，这句话在 L5.2 上不成立**，理由和受众无关：
把模型注册进 `admin.py` **不等于**它可达。持有零权限时 Django 会把模型整个从
admin 首页藏掉，于是两张新表对除超级用户外的每一个账号都不存在，
而症状是「一页没做」——`add_ministry` 当年就是这么丢的，理由就写在那个文件里。
所以 `FOUNDATION_ADMIN_PERMISSIONS` 加两行 `view_`（⚠️ **只给 view**：
排讲次是「某个 ministry 的活动」上的动作，按 D20 的分层判据属于 ministry 那一层，
它的门是 L5.8 的 Programs 页面）、
~~`events/tokens.py` 与扫码那几个视图~~（原理由：收窄的是发现，不是已经拥有的行
—— 对受众成立，而讲次是另一件事，见上表）、
`render.yaml`（决定 4 之后不需要第三条 cron，`RenderBlueprintGuardTests` 因此不用改）、
`gallery/`（Memories 墙没有指向 `Event` 的外键，核对过，所以 L3 不会从那边漏出去）。

⚠️ `check_deployment.py` 2026-08-26 之前列在上面这一段里，理由写的是
「本轮没有任何一档是上线前必须先有行的」。那句话本身没错，
错在它没预料到 L1.6 会让**迁移多送一行** —— 门槛不跟着提，
那条自检从此什么都不检查，而且不报错。

### ⚠️ 那张 ERD 已经落后三轮了，这是核对时撞出来的

`docs/planning/diagrams/src/page.html` 停在 2026-08-03。核对本轮要改哪些文件时
grep 了一遍，它里面搜不到 `served_as`、`stop_at_needed_count`、`compensation`
任何一个，`Event.status` 还写着 `confirmed`（0011 已经改成 `full`），
谓词还叫 `visible_to_volunteers`（2026-08-20 已改名）。

而它的 README 声称自己画的是「16 张业务表的**全部字段**」。
一份声称完整、实际落后三轮的图，正是本项目反复判过的那个形状
（[D27](decisions/D27-ministry-report.md)：没有和没算不能长得一样）。

处置分两步，不要混：本轮新加的东西照上表补进去；
**之前三轮欠的账单独补一次**，或者在 README 里写明它停在哪一天、
哪几处已知不准 —— 两条都行，但不许继续假装它是完整的。

# 要改的文档

| 文档 | 改什么 |
|---|---|
| [`participants.md`](participants.md) | 第八节改口清单里 D38 那一行从「不改口」改成「加一档 `not_applicable`」；第十节加三批的执行记录；第十一节的验收逐条打勾 |
| [D38](decisions/D38-served-as-volunteer-or-work.md) | 加 `not_applicable` 一档，写明它不是身份、永远不出现在表单上、且它换来了一条真正的约束。⚠️ L5.2 又就地补了一条：那条约束**到不了场次那一层**，因为这一档不往下搬 |
| [D43](decisions/D43-hours-given-and-hours-received.md) | 新开（2026-09-08，随 L5.2）：给出去的时间和接受到的时间是两个方向相反的数，后者不存、用算的 |
| [D36](decisions/D36-two-hour-ledgers.md) | 就地补：有第三个数，方向相反，同样不许加进那两个账本 |
| [D27](decisions/D27-ministry-report.md) | 指标拆成两组并排不相加；`hours_per_participant` 的分母改口；新增 People served |
| [D19](decisions/D19-event-role.md) | `EventRole` 长出「谁报得上」那一组勾选（两个布尔 + 一张多对多）；并写明 L1 为什么落在 `ParticipationRole` 而不是这里 |
| [D5](decisions/D05-lookup-tables-not-enums.md) | `EventType` 从字典表清单里**删掉**（说不出谁读它，L2.6）；`nature` 作为「字典表上的枚举列」的第二个例子 |
| [`deferred.md`](deferred.md) | `Event.parent` 出栏，并注明载体判定作废的理由 |
| [`phase-b.md`](phase-b.md) | 可见性那一节补 L3 这一维 |
| [D32](decisions/D32-worker-axes-schedule-and-assignment.md) | ✅ 2026-08-21 已改：那条不变量的标题原来写的是「一个人在基金会里只有一条在编路径」，会被读成「一个人只能有一行任职」。改成「在编只有一套结构」，并补一小节写明一人多岗是常态、判据一律写成存在性判断 |
| [D33](decisions/D33-work-schedule.md) | 第三节旁边补一句：活动的系列选了「必须有结束条件、一次生成完」，和班次的滚动窗口不同，理由在本文件 L5.1 |
| [`05-roadmap.md`](05-roadmap.md) | D1.4 与本轮的关系；D2a 的生成器要调 `events/recurrence.py` |
| [`phase-d.md`](phase-d.md) | 同上 |
| [D14](decisions/D14-constraint-is-the-only-rule.md) | 就地补 L5.3 的三处缺口：`Event.objects.update(shape=…)` 绕过冻结、`Participation.objects.create()` 不开点名册、以及「挑讲次的课停办再复办恢复不了谁挑了什么」 |
| [`goal.md`](goal.md) | ⚠️ 核对时发现的一处欠账：goal.md 自称是「唯一入口」，而它开头那张「去哪找」的表里列了 01–05 和 phase-b/c/d，**没有 `participants.md`** —— 那份文档从 2026-08-20 起就一直不在索引里。本轮把它和本文件一起补进去 |

⚠️ [`revisions.md`](revisions.md) 不在上表里，而这是判断不是遗漏：那份文档记的是
「用户报来一批问题 → 底下各有一条看不见的规则」，按批次编号。
本轮的九条需求原文已经一字不改地记在 [`participants.md` 第二节](participants.md) 里了，
再抄一份到 revisions.md 就是同一件事的第二处真相。
⚠️ 例外：实施中如果撞出「用户报的现象和真正的成因不是一回事」那种事，那一条属于 revisions.md。

# 全轮

- `python manage.py check` / `makemigrations --check` / `ruff` 干净
- 测试数只增不减
- 八条新守卫全部做过双向验证
- `python manage.py test core.tests.MarkdownLinkGuardTests core.tests.EmphasisGuardTests core.tests.DecisionSectionReferenceGuardTests` 绿。
  本轮改十份文档，三条都要跑：链接那条挡指不到的文件和锚点，
  节号那条挡正文里的假引用，强调那条挡「星号和加粗越写越多」
- `core.tests.DocTestReferenceGuardTests` 绿。⚠️ 本文件里的测试名一律写成裸名
  （`test_xxx`），**不带类名前缀** —— 「类名点测试名」是「指向一个已存在的测试」的
  写法，而这些测试还不存在，那样写会让这条守卫当场红。
  这一条是实测出来的：本文件初稿在这一行上举了一个反例，写的时候带了类名前缀，
  守卫立刻把它连同行号一起报了出来
- 迁移的真验收在**有旧数据的库**上做，不是在按迁移跑出来的空库上

# 验收

批一：

- [ ] 现有工种行全部是 `helping`，且是打开 admin 看过的，不是推理的
- [ ] 给一个 `attending` 的角色记工时 → 被拒，消息说得出为什么
- [ ] 绕开服务层直接写 `not_applicable` + 工时 → 数据库拒绝
- [ ] `attending` 的参与不进 `hours_missing` 的分母
- [ ] 加一场 ESL 之后，Hours per participant 没有下降
- [ ] 「我们服务了多少人」这个数不含来听讲座的在编员工
- [ ] 「我的报名」上 ESL 那一行不印任何身份文字

批二：

- [ ] 一场活动同时开「所有人可报」和「仅在编可报」两个角色 → 外部账号看得见活动、看得见前者、报不上后者
- [ ] 给「仅本 ministry 可见」的活动加一个「所有人可报」的角色 → 被拦住
- [ ] 把已经有「所有人可报」角色的活动改成「仅本 ministry 可见」 → 被拦住，且点了名
- [ ] 别的 ministry 的在编成员打开「仅本 ministry 在编可报」的活动 → 看得见，报不上，页面说得出为什么
- [ ] 执行主任（岗位没有 ministry）看得见「全体在编」的活动，报不上「仅本 ministry」的角色
- [ ] 受众改窄之后，已经报了名的人仍然打得开那一页
- [ ] 一场没有角色的公告 → 页面说得出它是故意不收报名，不是没建完
- [x] ~~活动列表可以按类型筛选~~ —— ❌ **2026-09-04 随 `EventType` 一起作废**（L2.6）。那张表说不出谁读它，整表删掉，这一条也就没有了要验的东西。⚠️ 它在验收表里又挂了四天，而验收表上一条**已经不成立的条目**比缺一条更糟：它会让走查的人去找一个不存在的功能。

批三：

L5.3（Programs）—— ⚠️ 下面这些是**浏览器**验收，一条都没走。
自动化那半已经绿了：全量 1959 条测试、`ruff`、`makemigrations --check`、四条文档守卫，
以及 `RegisterDeleteGuardTests` 的双向验证。回填迁移在一个**跑到 0025、塞进 L5.2 期
数据、再往前迁**的临时库上验过（有讲次的活动变成 `program`，普通活动不变）；
开发库里今天一个 `Session` 都没有，所以 0027 在那上面是空操作。


- [ ] 发布页出现两档单选；建一门 Program，排讲次**之前**改回 One occasion → 可以；排了之后 → 被拦住，且话里说得出为什么
- [ ] 把一门排了讲次的课取消掉 → **没有被冻结规则拦住**
- [ ] 学员报名一门课 → 报名只有一行，而点名册上他出现在**还没结束的每一讲**上
- [ ] 今天才报名的那位，出勤率分母比从第一讲就在的那位小，且没有任何一处在减
- [ ] 给一个 single 活动加讲次 → 被拒，话里说得出它是一场而不是一门课
- [ ] admin 加一讲 → 已报名的人自动出现在那一讲的点名册上
- [ ] 停办这门课 → 已上那几讲一行没少、未来那几讲的预期行没了、**没有人被标成 withdrew**
- [ ] 复办 → 未来那几讲的点名行回来了
- [ ] admin 活动列表的 Kind of event 筛选真的筛得动
- [ ] 报表上「接受到的时数」和「工时」并排，没有合计
- [ ] 勾上「让学员自己挑讲次」→ 报名页真的问他哪几讲、只列还赶得上的、不选被拒
- [ ] 照片墙灯箱：喂一个坏图 URL → 停在缩略图，不出碎图标

L5.4–L5.6（recurring events）—— ⚠️ 2026-09-10 落地。下面同样是**浏览器**验收，
一条都没走；自动化那半已经绿了（全量测试、`ruff`、`makemigrations --check`、
四条文档守卫，以及 `GeneratedEventDeleteGuardTests` 的双向验证 ——
故意在别处种一句同形状的删除，确认它真的红，再撤掉）。

- [ ] 按规则生成 N 场，N 场归成一组，每一场都带着角色，且状态跟模板走
- [ ] 改规则只动未来的场次，且有人报名的那一场一行没动
- [ ] 人手加进这一批的那一场，再按一次生成之后还在（⚠️ 现在生成只补不删，所以这一条比原来弱；真正该走的是下面两条新的）
- [ ] 传一张图到系列上 → 每一场都显示它；换一张 → 每一场跟着换；最后一场结束之后图才消失
- [ ] 「即日停止」之后未来那几场真的从 `/events/` 上没了（不是只停了生成）
- [ ] 跨过夏令时切换，当地时间不变
- [ ] 刚建完就撤销 → 库里干净得像没发生过
- [ ] 三周后再撤销 → 确认屏说得出会删几场、会留几场、每一场为什么留
- [ ] 再撤一次 → 说「已经撤销过」，不报错
- [ ] 模板角色勾得比系列宽 → **在发布那一刻被拦住**，不是等到生成时才炸
- [ ] `COUNT=500` → 被拒，且话里说得出上限是多少
- [ ] 报一个星期二 → 只报上那一场，下个星期二一行没有

---

# 计划外记录

> 实施时才发现的坑写在这里。这一节是这个项目最贵的资产之一，
> 每个 roadmap 都留着它，不要因为「这次很顺」就不写。

开工前那一轮核对已经先记了六条，那是**文档自己的坑**
（[`participants.md`](participants.md) 第六节没走到底的地方）：
`Position.ministry` 可空让「本 ministry」对执行主任无解；
L2×L3 的不变量进不了 `CheckConstraint`，而第六节写的是「进约束」；
可见性要问「哪一天在编」而那不是一个常量；
公告和半成品在结构上无法区分，于是第十一节那条验收做不到；
`attending` 的 `served_as` 和「早于 D38」抢同一个空值；
`nature` 一翻，历史行的含义跟着变。六条各自的处置写在上面对应的步骤里。

动手之后发现的写在下面。

## L1.1 · 标签自带说明，到了表格里就得再切一刀（2026-08-21）

`Nature` 的标签初版写成 "Helping — they give their time"。在建角色那张表单上很好读，
到了工种表的「Kind」那一格就太长，于是模板里出现了
`|cut:" — they give their time"|cut:" — they receive a service"` ——
**一句会静默失效的代码**：谁把标签重写一遍，那两刀就切不动了，
页面照常渲染，只是每一格都长出一条尾巴。

仓库里早就有正确形状：`SERVED_AS_EXPLANATIONS`（D38 那一轮建的）——
标签只留一个词，说明放在旁边一张 dict 里，**只在「问人」的时候拼起来**。
照抄成 `NATURE_EXPLANATIONS`，模板回到一个 `get_nature_display`。

⚠️ 教训不是「别在标签里写说明」，是：**一个值有两种读者（正在选的人 / 在看结果的人）时，
它就有两种写法**，而把长的那种切短是最容易想到、也最容易烂掉的一种。
这是同一个形状在本项目里第二次出现，所以下一次应该在写标签的那一刻就想起它。

## L1.2 · 在真库上验约束，第一次验到的是**另一条**约束（2026-08-21）

`participation_no_hours_when_not_applicable` 落库之后，在 dev 库上随手挑了一行
真数据，改成 `not_applicable` + 工时，库拒绝了 —— 差一点就当成验过了。
拒绝它的其实是旁边那条 `participation_hours_only_when_attended`：
那一行的状态不是 `attended`，所以在轮到新约束之前就已经被挡下了。

换一行「`attended` 且已经有工时」的再试，报出来的才是新约束的名字。
另一半（没有工时的行写得进 `not_applicable`）也走了一遍，然后回滚。

⚠️ 教训可以直接复用：**一张表上有多条约束时，「它拒绝了」不等于「我这条生效了」。**
验收要读报错里那个 `constraint` 的名字，不是只看有没有抛异常。
本项目的表普遍带三到五条 CheckConstraint，所以下一条约束落地时还会遇到。

## L1.3 · 「同一条规则的两个实现」，仓库里第二次给出同一个答案（2026-08-21）

`signups_asked_about_serving()` 初版是这么写的：取出这场活动的报名行，
在 Python 里逐行判「他在编 **且** 这个角色记工时」。能跑，测试也绿 ——
但那个 `and` 右边是 `records_hours` 的**第二份拷贝**，而且是一份会走散的拷贝。

仓库里已经有过这一格的答案，而且写在注释里：`core/querysets.py` 的
`DateRangeQuerySet.active()` 和 `DateRangeMixin.is_currently_active`
是同一条规则的两个实现（一个给一批行、一个给一行），
它们**故意挨着放**，理由原话是「让『改一个就要改另一个』是一眼可见的事，而不是一句承诺」。

于是照办：`Participation.records_hours`（一行）+ `ParticipationQuerySet.recording_hours()`
（一批），紧挨着，各自注释指向对方。

⚠️ 这是本轮**第二次**在同一类问题上发现仓库已有先例 ——
第一次是 L1.1 那个标签切短（答案是 `SERVED_AS_EXPLANATIONS`）。
两次都是「我先写了一个能跑的版本，然后发现三个月前的自己已经解过」。
教训不是「要多读仓库」这种空话，而是一条可执行的：**写下第二份判据之前**，
先 grep 一下这条规则的第一份在哪、它旁边有没有注释。

## L1.4 · 文档里那段关联子查询根本跑不起来（2026-08-21）

`on_the_books_exists()` 初稿写的是 `TruncDate(OuterRef(...), tzinfo=...)`。
读起来完全合理，**一跑就抛**：

```
AttributeError: 'ResolvedOuterRef' object has no attribute 'output_field'
```

`TruncDate` 在 resolve 的时候要读操作数的 `output_field` 决定截断成什么类型，
而 `OuterRef` 那一刻还没有类型。改法和取舍写在 L1.4 正文里（选了「外层先 annotate」）。

⚠️ 这一条和 D1.3 那条 `-> str | None`（[`05-roadmap.md`](05-roadmap.md)）是**同一类**错，
第二次了：文档里的代码片段读起来合理、评审过好几遍，
而它是**照着敲一遍才会发现**的那种错。第一次的教训写的是
「照着这个签名写一遍，能不能把表里每一格都表达出来」，
这一次要补的是更低的一条：**文档里的每一段 ORM 代码，在写进文档之前先在
`shell` 里跑一次**。这两次都不是设计错，是「没运行过」。

## L1.5 · 演示数据必须是系统真的会造出来的数据（2026-08-21）

seed 里那个 `signup()` 助手是直接 `get_or_create` 一行 `Participation`，
不走 `services.sign_up()`。照它加 ESL 的报名，`served_as` 会留**空** ——
而空在这个字段上有一个写死的含义：这一行早于 D38、回填证不出任何东西。

也就是说：演示数据会展示一个**只有导入脚本才造得出来的状态**，
而真实报名走那条路写的是 `not_applicable`。演示的是错的东西。

处置：这四行走 `services.sign_up()`，并给它一个自己的名字（`joins()`），
docstring 写明和 `signup()` 的区别、以及它不幂等（只许在 `if made:` 里调）。

⚠️ 一句可以直接复用的判据：**演示数据里任何一个「有规则决定它取什么值」的字段，
都必须由那条规则写进去。** 手工塞值的助手只能用在没有规则的字段上。

## L1.5 · 一个宽松的 fixture 选择器，等到数据长出来那天才发作（2026-08-21）

加完 ESL 报名，`AcceptanceWalkTests` 里三条测试同时红。它们都写着：

```python
Participation.objects.filter(contact__legal_last_name="Okafor").first()
```

意思是「Ada 的那条报名」—— 而 Ada 从今天起有两条，`.first()` 按默认排序
拿回了新的那条（ESL 座位，`not_applicable`）。三条测试报的错都是
「身份不对」，**没有一条指向真正的原因**（fixture 选歧义了）。

处置：抽成一个具名的 `adas_helping_signup()`，用 `get()` 而不是 `first()`，
并写明她现在有两条、这条是哪一条。

⚠️ 教训不是「别用 `.first()`」，是：**一个 fixture 选择器要么唯一，
要么就得说出自己指的是哪一个。** `.first()` 在只有一行时是「那一行」，
在有两行时是「随便哪一行」，而这两件事在代码上长得一模一样。
⚠️ 顺带印证了那条老规矩的另一半：演示数据是耦合点 ——
这一次它没有让断言变错，而是让**测试的失败信息变得没有指向性**，
那比直接红更贵。

## L1.6 · 一条断言太松，它是「靠别的东西错」才通过的（2026-08-26）

给「工种表门槛从 2 提到 3」补第一条测试时，第一版是这么写的：

```python
self.assertIn("empty, so the form that needs it", self.report())   # 只有送的行 → 该报警
...
self.assertNotIn("empty, so the form that needs it", text)          # 加了一行 → 不该报警
```

第二行当场红。原因不是门槛错了，是**那句话是所有字典表共用的** ——
`Ministry` / `Position` / `EmploymentType` / `EventType`（当时还在）在测试库里全是 0 行，
每一个都在输出里印同一句。于是第一行的「通过」是靠别的表在报警混过去的，
和 `ParticipationRole` 一点关系都没有。

改成只读 `ParticipationRole` 自己那一行，判它的状态标记。

⚠️ 教训和 L1.2 那条（「它拒绝了」不等于「我这条生效了」）是**同一个形状的第二次**：
一个断言通过，不代表它测的是你以为的那件事。上一次是多条约束共用一个异常，
这一次是多张表共用一句文案。**共用的输出 = 断言要收窄到那一行**。

## L1.6 · 演示数据里那两位邻居，在已有的库上不会出现（2026-08-26）

新加的两位「只来领取的社区成员」写在事件 4 的 `if made:` 块里 ——
而 dev 库上那场活动早就存在，所以重跑 `seed_demo` 时整块被跳过，一行都没加。

这是 seed 里**每一个** `if made:` 块的固有行为，不是这一步引入的：
`services.sign_up()` 按设计拒绝重复报名，所以那些块只能在活动新建那一次跑。

验证时的做法：删掉那场活动再 seed 一次。写下来是因为下一个往 seed 里加东西的人
会先遇到「我加了，可是库里没有」，而那三十秒的困惑完全没有必要。

⚠️ 顺带一句判断，不改：**要让它幂等，就得让 `joins()` 先查一次「报过没有」** ——
而那正是 `sign_up()` 故意不做的事（「你已经报过这个角色了」是一条要说给人听的话，
不是一个静默跳过）。演示脚本的方便，不值得让那条规矩多一个例外。

## L2.1 · 一条新规则红了 13 条老测试，而那 13 条都在替我说同一句话（2026-08-26）

「至少勾一项」落地之后，全量跑下来 13 条红的，全是**往建/改活动的表单 POST、
但 payload 里没有受众**的测试。

它们不是被误伤 —— 它们精确地演示了这条规则要防的那件事：
一个手工拼出来的 POST，**可以造出一场谁都看不见的已发布活动**。
真实浏览器不会（表单会把勾选框渲染出来、一起提交），而脚本、导入、
以及以后的 API 会。

处置不是逐条加一个字段了事，是给那几个 payload 助手补上，
并在每一处写明为什么补：`visible_to_outsiders=True` ——
因为这些活动在这个字段存在之前**本来就是所有人可见的**，
而这些测试没有一条是在测受众。

⚠️ 记下来是因为它是这个项目一直在讲的那件事的一次正面例子：
新规则打红老测试，第一反应应该是**「它抓到什么了」而不是「怎么让它绿」**。
这一次答案是「什么都没抓到，那 13 处本来就该带这个字段」——
但那句话得先问出来才知道。

## L2.1 · admin 那条路差点一道校验都没有（2026-08-26）

规则进不了 `Model.clean()`（M2M 在 `save()` 之后才写，实测见 L2.1）。
而**管理后台自己建表单** —— 它不会用站点的 `EventForm`。

也就是说：如果只把规则写进 `EventForm`，那么**从 admin 建一场活动，
可以一项都不勾**，而页面上什么都不会说。

处置：`AudienceAdminForm` 复用同一个 mixin（不是抄一份规则），
放在 `forms.py` 而不是 `admin.py` —— 后者按 D18 不许持有逻辑，
在那边它只剩一行 `form = `。

⚠️ 这一格是「模型层校验覆盖所有入口」这个直觉的反例，而那个直觉在这个项目里
一直是对的（`ParticipationRole.nature` 那条就是靠它覆盖 admin 的）。
M2M 是它唯一不成立的地方，所以值得单独记住。

## L2.2 · 最自然的那个写法会让活动列两遍（2026-08-26）

「勾中的 ministry 里有他的岗位」读起来就是一句
`filter(visible_to_ministries__in=…)`。跑出来：

```
张三在 Pantry 和 Tax 各有一个岗位
一场活动同时勾了这两个 ministry
→ 它在结果里出现 2 次
```

多对多是 join，两边各命中一行就出两行。表现不是报错，是**分页、计数、
报表全部跟着错**，而页面上只是「这场活动怎么列了两遍」。

⚠️ 这一条和 L1.4 那条（`TruncDate(OuterRef(...))` 跑不起来）是**同一类的第三次**：
文档里的 ORM 代码读起来合理，而它要么跑不起来、要么跑出错答案。
区别是这一次**跑得起来** —— 所以它比前两次更危险：没有异常，只有一个多出来的行。

教训因此要收紧一格：**不只是「先跑一遍」，是「跑一遍并数一下行数」。**
一个返回了结果的查询，不等于返回了对的结果。

## L2.2 · `related_name="+"` 挡住了唯一读得懂的写法（2026-08-26）

正确形状是 `Exists`。而写 `Exists` 有两条路：

| 路 | 形状 |
|---|---|
| 从 `Assignment` 反查回 `Event` | 一层 `OuterRef`，读起来就是那句话 |
| 走 through 表 | **两层嵌套 `OuterRef`**，同样正确，没人读得懂 |

第一条需要 M2M 有反向名字，而 L2.1 我写的是 `related_name="+"`（禁用反向），
实测直接 `FieldError`。

改成 `%(class)s_audience`。⚠️ 这个改动**不产生任何 SQL** ——
`related_name` 只活在 Django 的状态里。但它仍然要一个迁移（0020），
而不是去改已经提交的 0019：那条规矩（迁移一旦应用就不再修改）在这里
「这次无害」，而「这次无害」正是让一条值得保留的规矩慢慢失效的说法。

## L2.2 · 一个 fixture 缺省值，80 条测试（2026-08-26）

`for_audience()` 接进视图之后，全量跑下来 80 条红的。原因只有一个：
`make_event()` 建出来的活动**没有任何受众**，于是每一页 404。

处置是给那个助手一个缺省值 —— 和迁移给存量行的是同一个值（对外可见），
理由也是同一条：那是这些活动在这个字段存在之前的意思。

⚠️ 但 `ForAudienceTests` 自己那个建活动的助手**必须显式写全三档**，
不能继承这个缺省。它是**关于受众**的测试类，一个悄悄带着「对外可见」的
fixture 会让它一半的用例因为错误的原因通过。
这是「共用 fixture 省事」和「测试要说得出自己在测什么」之间的一次真实取舍。

## L2.3 · 一条规则挂错了类，而测试是唯一发现它的东西（2026-08-26）

「改窄活动时挡住比它宽的角色」这一半，我第一次写落在了 `AudienceAdminForm` 上 ——
而 `EventForm` 自己还留着上一步那个只调 `clean_audience()` 的 `clean()`，
把它**覆盖掉了**。

站点的发布表单因此对改窄**一点检查都没有**，而 `manage.py check`、`ruff`、
以及 L2.1 那批测试**全部是绿的**。抓到它的只有一条：
`test_narrowing_an_event_below_its_roles_is_refused_and_names_them`。

⚠️ 更值得记的是排查过程：直接调 `refuse_wider_than_event()` 是拒绝的，
表单却放行 —— 也就是说**规则本身对，接线错了**。
这一类错（规则写对了，但没有接到那条路上）和「规则写错了」在测试报错上
长得一模一样，而修法完全不同。

处置：两半都搬到 `AudienceFormMixin` 上，三张表单（`EventForm`、`EventRoleForm`、
`AudienceAdminForm`）各自继承。⚠️ 于是每张表单都拿到两半，
而它没有的那一半自然什么都不做（角色表单没有 `roles`，活动表单没有 `event`）——
比「哪张表单接哪一半」这种要记住的接线可靠。

## L2.3 · `Spec.of(instance)` 什么时候安全，什么时候是那个老坑（2026-08-26）

L2.1 实测过：验证时读实例的 M2M，读到的是库里那份旧值。
但这一步有两处**必须**读实例：

| 读谁 | 安全吗 |
|---|---|
| 加角色时读**活动**的受众 | ✅ 安全 —— 角色总是加在一个已经存在的活动上，库里那份就是它现在的样子 |
| 改窄活动时读**角色**的受众 | ✅ 安全 —— 变的是活动，角色没动 |
| 改窄活动时读**活动自己**的受众 | 🔴 **就是那个坑** —— 它正是被改的那个 |

所以 `Spec.of()` 的 docstring 写死一句：**只用于「不是正在被编辑的那一行」**。
⚠️ 这三格看起来很像，而第三格和前两格的区别只有一个词：
被验证的那一行，不能从库里读。

## L2.4 · 一个「特意收窄」的写法，其实是最宽的那一个（2026-08-29）

给测试造一个只给某几个 ministry 看的角色，第一版写的是：

```python
role = make_role(event, code, visible_to_outsiders=False, visible_to_all_staff=False)
role.visible_to_ministries.set(ministries)
```

读起来是「两个都关掉，只留 ministry」。实际上 `False / False / 空` 正是
**空受众**，而 `make_role()` 看到空受众就会按决定 15 去继承活动那一份 ——
于是这个角色拿到的是活动的「谁都看得见」，比原来还宽。
测试因此**通过了，但通过的是另一件事**：跨 ministry 的那条断言之所以绿，
是因为角色对所有人开放。

⚠️ 值得记的是这两件事在代码上长得一模一样：
「我要一个窄受众」和「我还没设受众」写出来是同一个字节。
L2.3 那条 `audience_is_empty` 的注释早就写了「空受众从来不是谁选出来的状态」——
这一节是同一句话反过来咬人的样子：**当它真的被谁选出来的时候，系统读不出区别。**

处置：造窄受众一律走 `set_audience()`（它会拒绝非法的，也不会替你继承）。
seed 里那个只给在编看的角色同样如此，注释里写了原因。

## L2.4 · fixture 的缺省受众，把一步无关的改动变成一片红（2026-08-29）

`make_event()` 的缺省受众 2026-08-28 特意从「外部 + 全体在编」收窄成了
「只给外部人员」，理由写在原地：「这些测试都在扮演外部人员」。那句话当时对。

L2.4 落地那一刻它不再对了：角色的受众从活动继承，而这一步是第一次真的去读它。
于是每一个让**在编人员**走 `SignUpForm` / `sign_up()` 的测试都被拒 ——
`ServedAsTests` 那一整类，而它们一条都不是在测受众。

⚠️ 这和 L2.3 替 seed 挡下的是同一个坑低一层：那次补的是「角色要继承活动」，
没有人回头问一句「**那活动本身的缺省装得下谁**」。

处置：缺省改回「外部 + 全体在编」（迁移 0019 给真实行回填的那个值，也就是今天的行为），
而真正关于受众的三个类各自把 flag 写全。⚠️ 其中两个类原来只写了一半 ——
`ForAudienceTests` 的注释写着「每一个 flag 都写出来」，实际只写了一个，
另一个一直在吃缺省。缺省一变，它们**当场变红**，而这是它们该有的样子：
一个只写了一半的「写全」，和没写是一回事。

## 计划外 · 报表那两条测试，红在日历上而不是代码上（2026-08-30 / 31）

L2.4 收尾时全量测试红了一条 `MinistryReportTests`，而**把本轮改动整个收起来、
回到改动之前那份代码，它一样红** —— 也就是说它和这一轮无关，是日期滚过去撞出来的。

两条，同一个病：

| 测试 | 它想摆的场景 | 实际摆出来的 |
|---|---|---|
| `test_months_with_nothing_in_them_are_still_drawn` | 两场活动隔三个月，中间那个空月份必须画成 0 | 活动放在「今天减 30 天」和「今天减 90 天」。60 天跨几个**日历月**取决于今天几号 —— 2026-08-30 那天落成 6/1 和 7/31，两个相邻月份，中间根本没有空月 |
| `test_an_ordered_queryset_does_not_split_every_group` | 同一个月三场活动，柱子上应该写「3 events」 | 报表覆盖整个 ministry，而 `setUp` 那场活动也放在「今天减 30 天」—— 2026-08-31 它飘进了同一个八月，柱子写的是「4 events」 |

⚠️ 算了一遍：第一条一年里**有 12 天会红**，差不多每个月底一天。
所以它不是坏了，是**每个月有一天诈红** —— 写它的人当天跑过、绿的，之后几十次也都绿。
（同一类东西这个仓库已经记过一次：`ff06105`「一条跨午夜就会红的测试」，那次的窗口是十几分钟。）

🔴 顺手那个改法是错的：把 90 改成 120（拉得更开）。那只是把窗口挪到别的日子，
一年照样有几天红，而下一个人还得再查一遍。

处置两半，缺一半都不成立：

1. **时刻钉死**（`day_start(datetime.date(2026, 1, 10))`，照 D16 的写法，
   不用 `make_aware` —— ruff 的 DTZ 本来也挡着）；
2. **报表收窄到这条测试自己造的那几场**。只做第一半不够：报表默认覆盖整个
   ministry，而 `setUp` 那场活动仍然跟着今天飘 —— 日历会从后门再走回答案里。

⚠️ 断言也跟着从「至少三根柱子」改成**逐根点名**（`["Jan 2026", "Feb 2026", "Mar 2026"]`
以及 `["1 event", "0 events", "1 event"]`）。原来那句「至少三根」在场景摆错的时候
才会红；点名之后，摆出来的是什么一眼就看得见。


## 计划外 · 三个坑全是「测试绿、页面不对」（2026-08-31，补记于 2026-09-08）

`1a25ce5`「公告有了自己的三个页面」那一批，提交标题自己写着「这一批里三个坑全是
『测试绿、页面不对』」，而计划外记录里一条都没有 —— 补记在这里，因为
[L2.7](#l27-批二的测试与验收) 早就预告过同一件事（「这一批的洞全是静默的，
测试绿不代表页面对」），撞上了却没落到纸上。

⚠️ 而这一条本身就是这一节存在的理由的例证：**最容易不写的，正是「这次很顺」
之外的第二种情况 —— 顺到觉得不值得写**。一次交付三个页面、测试全绿，坑就显得像
过程噪音。它们不是：它们是「哪一类东西测试盯不住」的样本，而那正是这份文件
最贵的部分。

## 计划外 · 一条决定晚于它的实现三个提交（2026-09-01，补记于 2026-09-08）

`7eee471` 的标题是「D41：公告不是活动 —— 而这一条欠了三个提交，代码跑着、
索引里一条都没有」。也就是说：`Notice` 表建了、三个页面上了、测试绿了，而
**说明它为什么该这样的那份决策文件还不存在**，`decisions/README.md` 里也搜不到它。

⚠️ 这和「先写文档再写代码」无关 —— 顺序本来就可以颠倒。问题在那三个提交之间，
仓库处于一个特定状态：一个新 app、一张新表、一套新页面，而**任何人都查不到它凭
什么存在**。这个项目对「说不出谁读它」判过死刑（`EventType`），对「清单和现实分了
家」记过三次，而这一次是第三种形状：**现实跑在前面，索引里没有它**。

⚠️ 处置不是「以后先写 D」，是**把决策文件和它的第一个提交绑在一起**：一个新表
或一条推翻旧决定的改动，D 文件和代码进同一个提交，或者代码那个提交的正文里
写明「D 还欠着，编号 DNN」。后者是本文件这一次采取的做法。

## 计划外 · 一步被三处指着，而它自己那一节是空的（2026-09-09，L5.3 开工走查）

L5.3 那一节写完的时候只有三样东西：一个枚举、两条「为什么要一列」的理由、
两个谓词。而全仓有三处指着它说别的：

- `events/services.py` 里 `add_attendance()` 的 docstring —— *"…none of them can
  be written yet: `sign_up()` cannot tell a course from a Saturday distribution
  until `Event.shape` lands in L5.3"*；
- 本文件 L5.1 和 L5.2 各有一条 ⚠️「这一步不兑现第九节那条缺口……要等 L5.3」；
- [`participants.md`](participants.md) 第十一节验收：「前半句要等 L5.3」。

三处都在说「报一次管全部归 L5.3」，而 L5.3 自己一个字都没写。
[L5.10](#l510-测试) 那张批次级测试清单里倒是有四条它的测试
（`test_signing_up_for_a_program_covers_every_session` 等），但没有任何一步描述
怎么做 —— 测试名挂在那里，实现无人认领。

⚠️ 这不是新病。L5.1 那条 2026-09-08 更正写的是同一句话：「两份文档各自把它推给了
对方，而中间那一格是空的」，说的是 `Event.duration` 在详情页上的落点。相隔一天，
同一个形状又出现一次 —— 而两次的成因也一样：**一段话被写在「谁不做它」那一侧，
从来没有人回来写「谁做它」**。

⚠️ 处置不是「以后写全一点」，那是决心不是机制。可用的判据是：**一句「这件事要等
X」写进代码或文档的那一刻，X 那一节就欠一条对应的正文** —— 而它是可查的，
`grep -rn "等 L5\." docs/ events/` 找得到每一处。本轮顺手查了一遍，剩下的两处
（L5.7 的报表 union、L5.8 的三张列表页）在它们自己那一节里都有正文，没有第三处。

## 计划外 · 同一个类里，学过的教训隔一个方法就没传到（2026-09-10）

跑全量时 `SessionScheduleTests.test_a_meeting_is_drawn_at_its_own_length` 红了，
而**把本轮改动整个收起来、回到干净的 main，它一样红** —— 又一条和改动无关、
撞在时钟上的测试。

成因：`setUp` 里的讲次是 `NOW + 2 * DAY`，长两小时。`NOW` 是真实时钟，
所以晚上十点之后跑，这一讲跨过午夜 —— 日程**正确地**把卡片切在日界上，
于是那条断言要的整段高度只在一天里的前 22 小时成立。
实测：22:00 之前绿，22:00 之后红。

🔴 **而这个类自己已经知道**。隔一个方法的
`test_a_single_occasion_is_drawn_exactly_as_before` 上写着：

> ⚠️ Pinned to mid-morning rather than "NOW + 3 days": NOW is the real clock,
> so a run of this test late in the evening puts a three-hour event across
> midnight and the card is legitimately clipped.

有人撞到过、想明白了、写下来了、**修好了自己那一个** —— 而两行之上那个
共用的 `setUp` 带着同一个毛病留在原地。教训传到了一个方法，没传到它旁边的夹具。

⚠️ 顺带查出那句注释本身也不准：它写的是 pin 到「mid-morning」，而
`local_now()` 是 UTC，`.replace(hour=9)` 钉的是**九点 UTC**，也就是本地凌晨两点。
它离午夜够远所以一直是对的 —— 那种「碰巧对」在有人改动那个数字的当天失效。

处置照[报表那两条](#计划外--报表那两条测试红在日历上而不是代码上2026-08-30--31)
定下的规矩：**时刻钉死**，且走 `day_start()`（D16：日界一律在基金会的时区里取），
不用 `.replace(hour=…)`。两处都改成同一个 `morning()` 助手。

⚠️ 这是这个仓库第三次记同一类（前两次：报表那两条、`ff06105` 跨午夜那条）。
值得记的是**它的传播方式**：前两次都留下了正确的判词，而判词没有走到隔壁的夹具。
下次遇到「我在这个测试里修好了一个时钟依赖」，同一句话要问一遍：
**同一个类里还有谁是这么摆的？**

## 计划外 · 一个有读者、却兑现不了的开关（2026-09-10）

L5.3 把决定 17 的副开关 `people_pick_meetings` 落了库，也落了发布表单。服务层有
**三个**读者：`sign_up()` 在没勾时拒绝 `sessions=`、`open_register()` 在勾了且没选
时建零行、`open_registers_for()` 整个跳过这种课。三条规则都对，都有测试。

🔴 **而学员端一个字都没有。** `SignUpForm` 没有讲次字段，没有任何视图传 `sessions`。
于是勾上它的后果是：一门课报了名 → 点名册一行都没有 → 而且他**没有任何地方能选**。
不报错、不提示，报了等于没报。而那个复选框在发布页上是可见、可点的。

⚠️ 值得记的是**它绕过了本项目已有的判据**。这个仓库判过「说不出谁读它」的死刑
（`EventType` 整表删掉），`services.py` 里也写着「一个没人读的开关正是这个项目反复
删掉的东西」。这个开关**有读者**，所以那条判据一次都没响。

判据因此要补一句：**读者存在不够，还要有人兑现得了它承诺的事**。一个只被「拒绝」
读到的开关，和一个没人读的开关，区别只在于前者更难发现 —— 它看起来是做完的。

⚠️ 它也是同一轮里第三次「测试绿、页面错」：`SessionForm` 从来没执行过（#33）、
这个开关造出空点名册、以及灯箱那个碎图标。三次的共同点是**没有人打开过那一页**，
而这一轮的浏览器验收清单从 L5.3 起就一直挂着没走。

## 计划外 · 一个「性能 vs 安全」的取舍，量了之后发现是假的（2026-09-09，L5.3）

`open_register()` 一次报名要建十二行点名记录，每一行走一次 `add_attendance()`，
而那里面是一次 `full_clean()`。实测：**一次报名 208 条查询**，一门课复办
（15 人 × 12 讲）**3087 条**。

第一反应是那个熟悉的取舍：「要快就得拆掉逐行校验，可那张网刚刚接住过一个真 bug」。
于是它被记成一条需要人来定的设计题。

🔴 **量一下就发现这个取舍不存在。** 把 `full_clean()` 拆开数：

| | 查询数 |
|---|---|
| `full_clean()` 全套 | 15 |
| `full_clean(validate_constraints=False)` | 2 |
| 只跑 `clean()`（两条跨表规则） | 0 |

十五条里有**十三条**是 Django 在插入之前，用 SQL 把五条 `CheckConstraint` 又问了
一遍 —— 而那五条就在数据库里，插入时一定会执行。而真正接住那个 bug 的
`clean()`，一条查询都不花。

所以改法是 `full_clean(validate_constraints=False)`：字段校验、两条跨表规则、
唯一性全部保留，只把「替数据库预演一遍它自己的约束」这件事去掉。
报名 208 → 52，复办 3087 → 807，安全性没有任何一处下降 ——
[D14](decisions/D14-constraint-is-the-only-rule.md) 本来就是这么判的：
**约束是唯一的规则**，在 Python 里提前问一遍只改变调用方看到的错误长什么样。
（admin 不受影响：它的 ModelForm 自己校验实例，含约束，
所以 `ConstraintErrorFieldMixin` 照旧把违规挂到对应的框上。）

⚠️ 值得记的不是这个数，是**「先量再选」**。那条取舍写下来的时候听起来完全合理，
而它之所以是假的，只因为没有人问过「十五条里，哪一条贵」。
本项目已经为「听起来合理的量」判过一次（[D36](decisions/D36-two-hour-ledgers.md)
那条「两个账本永远不相加」），这是同一种错误的另一面：
一个**没被拆开**的总数，和一个没有定义的总数，一样会把人引到错的地方。

## 计划外 · 两条设计缺陷是测试抓的，不是走查（2026-09-09，L5.3）

L5.3 落地时红了两条，各自都是**照字面读需求会写出来的那个写法**：

1. **`sessions=` 一个参数装了两个意思。** 决定 17 说「他可以挑哪几讲」，
   于是 `open_register(sessions=…)` 被写成「他挑的这几讲」，并在里面检查
   `people_pick_meetings`。而 `add_session()` 给已报名的人补建时，用的是同一个参数
   表达「只看这一讲」—— 于是一次正常的补建被当成一次非法的挑选拒掉了。
   改法是把「他有没有资格挑」那道门搬到 `sign_up()`：**挑**这件事发生在那里。
2. **清理未来的点名行只按时钟筛。** 一堂课两小时，有人签到之后中途退出 ——
   那一讲还没结束，于是他的签到和工时被一起删掉了，而这发生在一个规矩是
   「已经发生的绝不动」的函数里。改成两个条件：讲次还没结束**且**那一行还停在
   `registered`。

⚠️ 两条的共同点是**它们都不会在浏览器里被撞见**：第一条要有人在补讲次的同时
已经有人报了名，第二条要有人在一堂正在进行的课上中途退出。走查走不到，
而写测试的时候会自然而然地写出这两种情形。

⚠️ 记下来是因为本轮前几步的坑清一色是「照字面敲会漏抄一条」（L5.1 六处、
L5.2 八处），那类靠对着仓库现状走查就能挑出来。这一次两条都是**设计本身**的
歧义，走查挑不出来 —— 它们要跑起来才现形。两种坑，两种工具。

## L5.4–L5.6 · 五条实施时才现形的，其中一条是浏览器抓到的（2026-09-10）

本轮开工前的走查挑出九条（写在 L5.4 那张表里），那些是文档自己的坑。
下面五条是**跑起来才现形**的，和 L5.3 那两条属于同一类。

1. **`EventSeries` 上那条 `ended_on >= starts_on` 的约束是错的。** 初稿有它，
   照 `Assignment` / `MinistryRole` 抄的。第一次跑撤销的测试就红了：建一批
   **下个月**的场次、其中一场有人报名、当天下午撤销 —— 留下来的那一场让系列活着，
   `ended_on` 写成今天，于是约束拒绝了一次完全正常的动作。

   错在**把这两列读成了一段任期**。`Assignment` 那对确实夹着一件事；这一对不是：
   `starts_on` 是规则锚在哪一天，`ended_on` 说的是「这天之后不再生成」，
   截止早于锚点是一个真实、说得出口的状态 ——「它还没开始我们就停了」。
   约束整条去掉，理由写在原地，因为「两个日期得有先后」是任何人读到这张表时
   **第一个会伸手去加**的东西。

2. **`Event.source` 让 admin 的建活动页整个不能用了，而抓到它的是一条和它毫无
   关系的测试**（`AudienceThroughTheAdminTests`，L2 那一批的）。这一列有默认值
   但没有 `blank=True`，于是 ModelForm 把它渲染成必填，而没有任何一个 payload
   会带它。表现是「建活动被拒，说 source 是必填」——
   而**新加的那五个测试类一条都不会红**，因为它们谁也不走建活动那条路。

   ⚠️ 改法不只是让它别必填：`series` 和 `source` 一起进 `readonly_fields`。
   🔴 `source` 不是一个偏好，它决定**一条规则能不能把这一行收回去**。
   手工建的活动被人设成 `generated`，下一次重算就会连同上面的报名一起把它撤掉；
   手工改 `series` 则是把一行塞给一个从没造过它的系列。两件事都只能走
   `generate_occasions()`，或者根本不发生。

3. ⚠️ **两条守卫式的收尾都是「反过来也要成立」才算数的。** 守卫四做了双向验证
   （故意在别处种一句同形状的删除，确认它真的红，再撤掉）；
   而夏令时那条测试断言的是**两件事**：三场当地时间都是 19:00，
   **并且**它们的 UTC 偏移确实有两个值。后半句不写，前半句在一个不跨越切换的
   fixture 上会永远绿 —— 那正是本项目定义的「没写的守卫」。

4. 🔴 **含容不变量在表单那一侧还有一份，而我只修了服务那一侧。**
   走查第 3 条的改法是让 `refuse_bad_audience()` 读 `AUDIENCE_PARENT` 而不是
   猜一个叫 `event` 的属性。改完、测试全绿、浏览器点得通 —— 而
   `events/forms.py` 的 `EventAudienceFormMixin.submitted_event()` 里**一模一样
   的那个字面量**还在。

   后果：admin 的 inline 上，给一个「仅在编可见」的系列加一个「对外可见」的
   模板角色，**存得下去、返回 302、一个字不报**，而那条规则接下来生成的每一场
   都带着这个缺口。而 inline 正是本轮唯一的门。

   ⚠️ 抓到它的是一条**为这一页新写的**测试（`test_a_template_role_wider_than_
   its_series_is_refused_on_the_page`）。规则那一层的测试是绿的，服务那一层的
   测试是绿的 —— 因为它们测的是我修过的那一份。**一条规则有两份实现时，
   修好一份会让另一份看起来也修好了**，这正是本仓一直在判的那个病，
   而这一次它就发生在修那个病的同一个提交里。

   ⚠️ 顺手把镜像的那一半（`refuse_narrowing_below_the_roles` 里的 `roles`）
   也改成读 `AUDIENCE_CHILDREN`。那一处**本来就是对的** —— 三张有子行的表
   恰好都叫 `roles` —— 但「碰巧对」不是值得留着的性质，尤其当那个声明就在旁边。

5. **含容拒绝那句话在系列页上写着一个不存在的东西。** 浏览器上一屏读到的是
   「This **event** is not open to people with no current post…」——
   而那一页上没有活动，只有系列。

   ⚠️ 值得记的不是这个字错了，是**这个仓库提前一年零几个月写下过它**：
   `EventAudienceFormMixin` 的 docstring 里明写「含容那点算术搬得动，
   **它的句子搬不动** —— `TOO_WIDE_STEM` 读作『This event is not open to…』，
   而没有第三张表让这句话成立」。现在有第三张表了，而那句预言就在改动的文件里，
   隔着二十行。

   改法照本仓已有的做法：那个名词变成参数，按 `AUDIENCE_ON` 查一张
   `PARENT_NOUN` 表 —— 和 `AUDIENCE_HEADING` / `EMPTY_AUDIENCE_MESSAGE`
   同一个形状，那两张表自己的注释就写着「第四张表加一条就完事」。
   ⚠️ 「改窄会落下角色」那句（`NARROWING_MESSAGE`）同一处病，一起改。

⚠️ 五条的共同点值得单独说一句：**新功能的测试只盯着新功能**。第 2 条是全量
测试里唯一红的一条，来自三周前写的、和这一轮无关的一个类。所以
「新加的都绿了」不是可以提前收工的理由，跑全量才是。
而第 4 条把这句话又推进一步：**新功能的测试也只盯着新功能里我刚碰过的那一份实现**。
第 5 条再推一步：**它一个字都读不出来**。那句话对每一条测试都是「一个非空的错误」，
只有把页面打开的人才看得见它说的是一个不存在的东西 —— 浏览器验收买到的就是这个。

## L5.4–L5.6 · 一轮代码审查跑出八条，八条全部复现（2026-09-10，同日）

上面那五条是实施时撞上的。这八条是**交付之后**一轮专门的代码审查跑出来的 ——
而那次交付是绿的：2030 条测试、`ruff`、`makemigrations --check`、八条守卫、
外加一遍浏览器走查。八条逐条复现过，没有一条是误报。

> ⚠️ **复现的时候自己先错了一次，值得记下来。** 第 4 条第一次跑「没复现」，
> 因为那条一次性脚本用 `start_time__date=` 筛当天 —— 那是 **UTC 的那一天**，
> 正是 `TimeSourceGuardTests` 存在的全部理由。改成 `local_date_of()` 之后当场
> 复现。**一个「没复现」和一个「复现了」一样需要被审视**，而这次的教训是：
> 验证别人报的问题时，最先该怀疑的是自己那段验证代码。

| # | 是什么 | 为什么绿着的测试看不见它 |
|---|---|---|
| 1 🔴 | **两个 action 都没写 `permissions=`，于是只有 `view_` 的 foundation admin 能生成、能整批撤销。** 实测：一个只持 `view_eventseries` 的账号建出 4 场活动、撤掉整批 | Django 的 `_filter_actions_by_permissions()` 放行任何**没有** `allowed_permissions` 的 action，而 changelist 对任何打得开这一页的人都渲染那个下拉。🔴 而唯一声称这里锁着的东西**是我自己写的那段 docstring** —— 一句承诺了锁的注释比一扇没锁的门更糟，它让下一个人不再去看 |
| 2 | **「即日停止」之后再点一次生成，今晚那场回来了。** 停止按的是**时刻**（「开始了没有」），而重算的过滤按的是**日期**（「在停止那天或之前」）。一年里正好有一天两者不一致 —— 就是有人按下停止的那一天 | 那条测试（`test_a_stopped_series_does_not_grow_new_occasions`）**在大多数日子是绿的**，只在周二 19:00 之前会红。它不是漏了，是**按日期漂移的** |
| 3 | 同一个边界，撤销那一侧：撤过的批次（因为有人报名而留了下来）离「被重新生成回来」只差一次点击，而那个按钮就在同一屏上 | 同上。撤销的部分分支也把 `ended_on` 写成今天 |
| 4 | **原地改时间会把一场聚会变成两个活动。** 19:00 改 20:00 再生成：没人报名的那几场被收走重建在 20:00，**有人报名的那场不许删、留在 19:00**，于是同一晚上出现两个活动，志愿者占着其中一个 | 🔴 L5.6 正文早就写了「不做原地改规则重算」，`split_series()` 就是那条路 —— **而没有任何东西挡着另一条路**。又一次「决定写下来了，没有人兑现」，本轮第二次 |
| 5 | 撤销确认屏对一个八天前的批次照样印「将删除 N 场」，按下去一场都不删 | `undo_preview()` 只算那三个删除条件，`undo_series()` 另外还判七天窗口。**这一屏存在的全部理由就是不说假话** |
| 6 | 生成 action 没有接 `ValidationError`，于是一个受众为空的系列会把 admin 打成 500，而服务层的 `atomic` 已经把写回滚掉了 —— 人看到的是崩溃，不知道写没写进去 | 旁边的 `undo_batch` **接了**。同一个文件里两个 action，一个对一个错，和 2026-09-08 那次「两扇门，一扇忘了上锁」同形 |
| 7 | `COUNT=100` 的规则静静生成 53 场，并且告诉你「53 occasion(s) generated」 | 上限只在 `clean()` 里判，而 `objects.create` / 导入 / seed 都不走它。D14 的老问题：规则只写在人打字的那道门上 |
| 8 | `EventSeriesRoleInline.show_change_link = True` 什么都不做（`EventSeriesRole` 没注册进 admin） | Django 静默地不渲染它。一个**看起来像被谁弄坏了的链接**，而它从来就不存在 |

### 八条的共同点，而这一条比八条本身值钱

每一条都长在**两块各自正确的东西中间**。

- 权限那一行是对的，action 没去要它；
- 停止是对的，生成器不认它；
- `split_series()` 是对的，没有任何东西把人送过去；
- 那句拒绝是对的，屏幕没等它。

一块一块地测，八条一条都测不出来 —— 这正是它们活过 2030 条绿测试和一遍浏览器
走查的原因。⚠️ 本轮已经记过一次「新功能的测试只盯着新功能」，又记过一次
「只盯着我刚碰过的那一份实现」。这八条是第三层：**测试盯着的是零件，而这些
问题住在接缝上。**

⚠️ 处置：八条全部修掉，各配一条测试，集中在 `SeriesReviewFindingsTests` ——
**不打散到那四个类里**，因为它们的共同点是「怎么被发现的」，而下一个人在判断
该信哪些测试的时候，应该能一次看见这一整张单子。

## L5.4–L5.6 · 七个角度的独立审查，三十余条（2026-09-10，第三轮）

第一轮是开工前走查（九条，文档自己的坑）。第二轮是实施时撞上的（五条）。
第三轮是交付之后**七个各自只问一个问题**的独立审查：对不对得上前面、专不专业、
漏没漏、直不直观和理不理性、完不完整、有没有兑现需求，外加一个「资深工程师」
视角问这做法在业界靠不靠谱。

### 🔴 第一条：`generate_occasions()` **根本不幂等**，而它的 docstring 说它是

先 `_drop_generated_after()`，再读 `standing` —— 于是那个「已经站着的就跳过」的
集合，对每一个刚刚被收走的行都是**空的**。跳过只保护了收不走的那些（有人报名的、
已经开始的、人手加的），而**每一个没人碰过的未来场次都被删掉重建**。

实测按两次生成：

```
第一次 pks [9, 10, 11, 12]
第二次 pks [13, 14, 15, 16]   ← 同样四个晚上，四个新行
提示语「4 occasion(s) generated. …nothing was removed」
```

跟着一起没的：

| 没的东西 | 后果 |
|---|---|
| 一场被 admin **取消**的活动 | 回到 `open`，而且记录「已经通知过谁」的 `EventNotification` 跟着级联删掉 |
| 手工改过的地点 / 说明 | 还原，一个字不说 |
| 主键 | 每一条已经发出去的 `/events/<pk>/` 链接、`.ics` UID、签到二维码全部指向不存在的行 |
| 历史 | 三次按下之后，四个场次留下 32 行历史 |

⚠️ **而这一格的修法比 bug 本身简单**：自从「规则一旦生成过就冻结」落地，
生成器已经**没有任何该收的行**了 —— 规则不能变，时刻集合就不会变。
所以 `generate_occasions()` 现在**一行都不删**，只补缺的那些
（`open_register()` 一张表之外早就写着的那句「补齐不是重建」）。
删除仍然只有一处，三个调用方：停止、split、撤销 —— 生成不是其中之一。

### 第二条：需求 4 的后半句**从来没有兑现**

发布表单至今只有两档。`events/forms.py` 那条注释写着「第三档等生成器存在就加进
这个字段」，生成器 2026-09-10 就存在了，没有人回来加。于是基金会原话
「可以让 admin **选**」—— 一个给发布者的选择 —— 今天**没有任何发布者被给到**：
建一条规则只有超级用户在 admin 里做得到。

⚠️ 而代码和文档里有**五处**声称相反（`Event.Shape` 的 docstring、`EventSeries` 的
docstring、那条注释、participants.md 第十节、L5.3 那句「届时加进同一个字段」）。
五处全部改口，缺口进 participants.md 第九节，重启条件 L5.8 ——
**不在这一轮补做**：第三档要在发布表单上多出规则和时长两栏、走另一条保存路径
（建的是 `EventSeries` 不是 `Event`）、还要有一个「建完去哪儿」的答案，那是页面的事。

### 其余（逐条修掉，各配测试）

| # | 是什么 |
|---|---|
| 3 | **`stop_series_today()` 和 `split_series()` 都没有入口**，而两处注释写着「这是人按的按钮」，还有两句拒绝把人指向它们。`ended_on` 手填只停生成、**不收回任何东西** —— 填完之后四个已发布的晚上仍然站在日历上可报名。补了两个 action，并把 `ended_on` 设成只读：唯一的入口是那个两件事一起做的按钮 |
| 4 | 撤销确认屏的 `within_window` 只包住了「将删除 N 场」那一行，**另一半还在说**「库里干净得像没发生过」—— 同一个假承诺，往下一段，而上面那条注释还写着它已经修好了 |
| 5 | `has_an_ending()` 在**解析之前**问，于是「every tuesday」被判成「这条规则永远不停，加个结束」—— 对一个根本不是规则的输入给了「除了结束哪儿都对」的建议。⚠️ 而 `recurrence.py` 自己的 docstring 反方向写着这条危险 |
| 6 | `duration` 填 `2` 是**两秒**（`DurationField` 把裸数字读成秒），五十二场两秒的活动，一个字不报 |
| 7 | `starts_on` 不检查星期几。标签写着「First one on」，而周三配周二的规则被收下，第一场悄悄落在六天后 —— 撤销确认屏那句「first on 17 Sept」印的是一个什么都不发生的日子 |
| 8 | `status` 给了全部五档，于是可以把一个月后的四场活动生成成「已结束」。决定 30 只论证了 draft / open 两档 —— **实现比它的理由宽** |
| 9 | 上限那句话在非周更的规则上算错账：90 天的每日晨祷被一句「这超过一年的周更」拒绝，而月度规则能排到 2030 |
| 10 | `COUNT=0` 得到的建议是「检查起始日和重复的星期几」，而那条规则里根本没有星期几 |
| 11 | `EventAdmin` 上没有 `series` 列也没有筛选：一条规则放 52 行同名活动到那一页，而那是员工真正天天看的页面 |
| 12 | 撤销的拒绝不报批次名（旁边那个 action 报），多选时分不清是哪一条 |
| 13 | `split_series(**fields)` 把拼错的字段名**静静吞掉** —— 而它是改规则唯一被认可的路 |
| 14 | 一场活动跨夏令时切换时，`moment + duration` 是**墙钟**加法：01:30 的两小时活动在春天那个早上只开了一个真实小时 |
| 15 | 四个 `now=None` 参数、`_occasion_moments(upto=)`、`live()` 没有任何调用方 |

### 图片：这一格没有进缺口表，当场做掉了

原方案是「模板不带图片」，理由是 purge 会在第一场结束后删掉文件。
走查时判定**不能接受** —— 「传一次、每场都有图」是一个合理到不该被拒绝的要求。
三条路：

| | 结果 |
|---|---|
| 复制 52 份字节 | 能用。换一张图要改 52 处，而且**一处都追不到已经生成的** —— 正是下面那条缺口的病 |
| 52 行共用同一个**路径** | 🔴 最坏。第一场结束、purge 一跑，其余 51 场全变碎图标，而 `purge_event_image()` 自己的注释就写着「指向不存在的文件比没有图更糟」 |
| **图放系列上，场次引用它**（`Event.poster`） | ✅ 一个文件、改一处全改、某一场想用别的图还可以单独传 |

⚠️ **purge 那条规矩一个字都没改**，改的只是「那个东西」指谁：单场活动是它自己，
系列那张图是**最后一场结束之后**。判据仍然是 `end_time` 不是 `status` ——
现有那条 queryset 的理由（「靠人记得去标完成的图就是永远不会消失的图」）一字照抄。

⚠️ 唯一复制字节的地方是 `split_series()`：两条系列**生命周期独立**，
旧的最后一场先结束、purge 先删它的图，共用路径会把新的打碎。
一次复制，因为有两个**所有者**；不是 52 次，因为场次不是所有者。

⚠️ 守卫 `PosterIsAskedGuardTests`：模板只许问 `poster`，不许直接读 `.image` ——
直接读的表现是 52 场全显示默认 logo，而那看起来和「这个系列没人传过图」一模一样。
双向验证过。

⚠️ **另一条写下来的缺口**：模板上的名称 / 地点 / 说明 / 受众是在生成那一刻**复制**
过去的，之后各是各的 —— 改窄系列的受众**不会**改窄已经生成的场次。
真正的答案是每个日历都会问的那三选一（「这一场 / 这一场及以后 / 全部」），
而那需要一个页面去问。今天在 help text 上如实写出来，缺口进第九节。

### 三轮加起来的那句话

**「一句承诺了锁的注释比一扇没锁的门更糟」在这一个功能里出现了三次**，
三次都是我自己写的注释：
权限那一次（docstring 说只有超级用户，实际只持 `view_` 就能按）、
幂等这一次（docstring 说手工改过的会活下来，实际全删）、
以及「`stop_series_today()` / `split_series()` 是人按的按钮」那一次（两个都没有按钮）。

⚠️ 第一轮靠对着仓库走查、第二轮靠跑起来、第三轮靠**七个只问一个问题的人**。
三种工具找出三类完全不同的问题，而第三类（「你说它做得到，它做不到」）
是前两种都找不出来的 —— 因为代码和测试是一致的，**不一致的是代码和它自己的注释**。

## L5.4–L5.6 · 第四轮：修完之后再查一遍，又是十一条（2026-09-10）

前三轮：开工前走查（九条）、实施时撞上（五条）、七个角度的独立审查（三十余条）。
这一轮是**把修完的树重新交给同样那几个视角**，其中「Google 资深工程师」那一份
给的结论是 **still not approved**，四个 block 点。四条全部修掉，另加七条。

⚠️ **这一轮每一条都是一个真人在平常日子里会做的事** —— 双击一个慢按钮、
把上学期的开始日期打进去、从 Google 日历粘一条规则。没有一条是刁难，
而在这之前没有一条会报错。

| # | 是什么 | 后果 |
|---|---|---|
| 1 🔴 | **双击「生成」会把整批建两遍。** 读「哪些时刻已经站着」和写之间没有锁，两次点击在 READ COMMITTED 下各自读到空集 | 实测 4 场变 8 场，**一个字不报**，两条绿色提示都说「4 occasion(s) generated」。而那个按钮慢到会让人想再点一次：52 场 × 2 个角色 ≈ 2500 条查询。修法两半：`(series, start_time)` 的**部分**唯一约束（同一时刻两场手工活动仍然合法 —— 一个周六早上两辆车）+ 生成时 `select_for_update` |
| 2 🔴 | **起始日填在过去，会造出撤销够不着的活动。** 往回十二周的规则生成十二场，其中九场已经过去 —— 已发布、带角色、进 ministry 报表当成「开过但没人来」的会 | 而 `_collectable_occasions()` 按设计不碰任何已经开始的东西，所以撤销撤不掉，只能一行一行手删。⚠️ 改成**在门口拒绝**而不是让生成器跳过：跳过更省事也更糟，因为「为什么只有三场」在页面上没有任何地方答得出来 |
| 3 🔴 | **`UNTIL=…Z` 被拒，而那是每个日历导出的标准写法。** 拒绝的话是 dateutil 的原文：「UNTIL values must be specified in UTC」—— 而他填的**就是** UTC，是我们的 `DTSTART` 是 naive 的 | 一个出不去的循环，踩在最可能被粘进来的那种输入上。⚠️ 修法是**换算**不是**去掉 Z**：`20270101T000000Z` 在加州是 12 月 31 日下午四点，去掉 Z 会把截止日挪掉一天 |
| 4 🔴 | 规则里**夹带 `DTSTART:`** 会静静盖掉上面两个框。页面写着「19:00」，生成出来是 09:00 | 日期那一半被新加的「第一场必须等于 starts_on」抓到了，**时刻那一半没有**，而且不会被抓到 —— 没有任何一处比较它 |
| 5 | 「改规则」是这组里**唯一一个不报数字的破坏性动作**。它撤掉四个已发布的晚上，只说「副本建好了 (#17)」 | 两个兄弟 action 都报了数 |
| 6 | 撤销一整批会**遗弃系列那张图**：行删了，文件还在，而 `series_with_images_to_purge()` 查的是 `EventSeries` —— 再没有任何东西会去找它 |
| 7 🔴 | **`SessionAdmin` 的删除保护问的是「工时」，而来参加的位置按设计不记工时**（决定 20）。一个满员的学员点名册求和是 0，删除按钮是亮的 —— 而 `SessionAttendance` 从 `session` 级联 | 那道守卫问的是**志愿那一半的问题**，问在一张专门为装下两半而建的表上。⚠️ 改成问「有没有证据」，而且是**证据不是预期**：`add_session()` 会给已报名的人自动开点名行，按行数算会让一个刚排错的讲次删不掉 |
| 8 | 撤销窗口从 `series.created_at` 算，而**决定 30 保证这两个不一样** —— 系列生来是 draft，就是为了让人建完、看一眼、过几天再发布 | 实测：一个月前起草、三十秒前生成，撤销被拒，理由是「这批是七天前建的」。加 `generated_at` / `generated_by` 两列 |
| 9 | 没有任何守卫盯着「admin action 必须声明 `permissions=`」—— 而那是本轮**最重的一条**学到的规矩，且这个类当天又长了两个 action | 补 `AdminActionsDeclarePermissionsGuardTests`：走 `admin.site._registry`，读每个 callable 的 `allowed_permissions`，所以它盯得住这个文件从没点过名的 action。双向验证过 |
| 10 | `split_series()` 的后继从**今天**起算，而「第一场」必须落在规则重复的那一天 —— 于是七天里有六天，改规则这条唯一被认可的路会被一个它自己没碰过的字段拒掉 | 被一条为**图片**写的测试抓到 |
| 11 | 一条「从来没生成过」的系列，撤销**应该**能把它整个删掉（D40 §1 的 95%），而我第一版的窗口判它「没有批次可撤」 | 被我自己写的那条测试抓到 —— 而我在它的 docstring 里把理由写反了 |

### 图片：不接受那条缺口，当场做掉了

原方案「模板不带图片」被判**不可接受**。三条路里选了**引用**而不是复制：
图放系列上，场次通过 `Event.poster` 读它。一个文件、改一处全改、某一场想用
别的图还可以单独传。purge 那条规矩一个字没改，只是「那个东西结束了」问的是
**最后一场**。⚠️ 唯一复制字节的地方是 split —— 两条系列生命周期独立，
共用路径会让旧的 purge 把新的打碎。守卫 `PosterIsAskedGuardTests`，双向验证过。

### 四轮下来，那句话出现了四次

**「一句承诺了锁的注释比一扇没锁的门更糟」** —— 权限那次、幂等那次、
「stop / split 是人按的按钮」那次，以及这一轮的第 7 条（守卫的 docstring 说它
挡住了删除，而它对半张表是瞎的）。四次都是我自己写的注释。

⚠️ 而这一轮多出一条新的：**我的两条判断被我自己的测试推翻**（第 10、11 条）。
第 11 条尤其值得记 —— 我在 docstring 里把「没生成过的系列不该能撤销」论证得很顺，
测试一跑就发现论证是反的。**写得顺不等于是对的**，而测试是唯一不看文笔的读者。

## L5.4–L5.6 · 第五轮：三个验证 agent 把修复本身又查了一遍（2026-09-10）

修完第四轮之后，把**同一棵树**交给三个只问一件事的验证者：修复是不是真的、
文档和代码现在对不对得上、以及「Google 资深工程师」再看一遍。
又是九条，其中一条是这一整轮里**最值得记的一条**。

### 🔴 最重的一条：我自己的重构把守卫拆废了，而拆的理由是「让它更硬」

`GeneratedEventDeleteGuardTests` 的信号是「同一个函数体里既出现 `Source.GENERATED`
又出现 `.delete(`」。第四轮我把那三个条件抽进 `_collectable_occasions()`，
好让确认屏**数同一个查询**而不是自己复述一遍 —— 抽完之后：

| | `Source.GENERATED` | `.delete(` |
|---|---|---|
| `_collectable_occasions()` | ✅ | ❌ |
| `_drop_generated_after()` | ❌ | ✅ |

**两边各有一半，于是守卫一个都匹配不到，继续绿。** 验证者种了一句
`_collectable_occasions(series, after).delete()` —— 一个「最自然的写法」的第二个
删除者 —— 守卫**没红**。

⚠️ 这是「一个注释就能满足的守卫」那条教训**从反方向来的版本**：
**一次读起来像收紧的重构，可以把信号拆散**，而没有任何东西会告诉你 ——
一个什么都匹配不到的守卫，和一个没东西可抓的守卫，长得一模一样。
改法是两个信号任取其一（点名 `generated` 那个源，或者调那个持有条件的函数），
并且用那句「最自然的写法」重新做了双向验证。

### 其余八条

| # | 是什么 |
|---|---|
| 1 🔴 | **今天上传的图，今晚就被 purge 删掉。** 我写的「没有任何未来场次的系列要被收走」对一个「建完还没按生成」的系列同样成立 —— 上传、下班、第二天图没了。判据改成「**有没有跑过**」（`generated_at`）而不是「有没有未来」。代价写下来：建完就永远放着不管的系列会留一个小文件，而这比删掉别人刚传的图好得多 |
| 2 🔴 | **系列那张图绕过了 EXIF 剥离。** `Event.image` 从落地起就走 `normalise_event_image()`，理由写在它自己的注释里：手机照片带 GPS，一张在别人家里拍的活动照会把住址发给每个登录用户。系列是这个功能**唯一新增的上传口**，而它走了后门 —— 而它的图还会显示在每一场上 |
| 3 | `Source` 的 docstring 说「唯一的读者是 `_drop_generated_after()`」—— 抽完之后那个函数**什么都不读**了，它只是对 helper 交回的东西动手 |
| 4 | 那句「三个调用方」在这一侧是**两个**：D40 的第三个（重算未来）在活动这边不存在，因为规则冻结之后生成器没有该收的行 |
| 5 | 图片那条 🔴「没有图片列，这是一条写下来的缺口」—— 而那一列就在它下面四十行 |
| 6 | `split_series()` 那条「没有入口」的缺口注 —— 入口第四轮就建好了 |
| 7 | 确认屏印 `created_at`，而按钮判的是 `generated_at`；同一屏还写着「已经发生的永远不会被重新生成」，而生成器会填**任何**没有场次站着的时刻，包括过去的 |
| 8 | `Event.poster` 在志愿者列表页是 **N+1** —— 每一个生成的行一次查询，而那是全站被打得最多的一页。`EventAdmin` 为这件事加过 join，公开那一页没有。实测 8 场 8 次查询 → 1 次 |
| 9 | `PosterIsAskedGuardTests` 的正则只认 `event|occasion|e`，而 Django `DetailView` 的默认上下文名是 `object` —— **最可能的下一种写法正是它看不见的那一种** |

⚠️ 另外补了一条守卫：`AdminActionsDeclarePermissionsGuardTests`。
本轮最重的那条（只持 `view_` 也能按）原来只由一个「遍历这一个类的 actions」的
循环钉着，而**同一天这个类又长了两个 action**。守卫改成走 `admin.site._registry`，
读每个 callable 的 `allowed_permissions` —— 它盯得住这个文件从没点过名的 action。

### 第五轮验证之后又补了四条，其中两条是**我刚修好的那两条守卫仍然绕得过去**

| # | 是什么 |
|---|---|
| 1 🔴 | `GeneratedEventDeleteGuardTests` 两个信号仍然**都躲得开**：把过滤放一个 helper、`.delete()` 放调用方 —— 而那**正是这个功能刚引入的那种两函数形状**。所以下一个照着这个模式写的人，会直接走过为拦他而写的那道守卫。改法不是再加信号，是换一个问题：`DeletesInServicesAreEnumeratedGuardTests` 问的是「这个模块里**哪些函数会删东西**」，一个拆分答不过去 —— 整个服务层只有五个删除，逐个点名，加一个名字这件事本身就是「你得说清楚删的是什么、为什么可以删」的那一刻 |
| 2 🔴 | `PosterIsAskedGuardTests` 被**任何含 `form` / `field` 的一行**解除武装 —— 而 `class="form-row"` 是最普通不过的 admin 样式类。一个样式类就能关掉的豁免不叫豁免。改成匹配**被渲染的那个变量**本身，而不是在整行里搜。另外它对注释是红的，而同文件的兄弟守卫早就有 `_blank_out_comments()` |
| 3 | 「即日停止」那个 action 和 `ended_on` 的只读**一条测试都没有** —— 删掉任何一个，全量测试照样绿，而那一轮的记录把两者都写成「修复」 |
| 4 | 确认屏那半边的修复同样没有测试兜住：`shipped` 那条只断言「关闭那句在」和「按钮不在」，而漏掉的那句就在下一段 |

⚠️ 第 3、4 条合起来是一条：**「我修了 X」和「X 有测试盯着」是两件事**，
而本轮的记录把前者写成了后者。

### 五轮之后，那句话的最终形态

「一句承诺了锁的注释比一扇没锁的门更糟」这一轮又添两次（第 3、5、6 条），
但真正新的是这一条：

一次读起来像收紧的重构，**可以把守卫的信号拆散**，而它会安静地继续绿。

⚠️ 而修那条守卫的第一版**还是绕得过去** —— 我加了第二个信号，验证者用同一种
两函数拆分又绕过去一次。真正的修法是换一个**拆分答不过去的问题**：
不问「这个函数提到了什么」，问「这个模块里哪些函数会删东西」。
信号可以被拆散，而**一份名单不能**。

⚠️ 所以「守卫要双向验证」这条既有要求不够 —— 它只保证**写下来的那一刻**是红的。
**动过被守卫盯着的那段代码之后，要重新验一遍**，用「下一个人最自然会写的那种
写法」去验，而不是用当初那句。本轮为此把两条守卫各重验了一次。
