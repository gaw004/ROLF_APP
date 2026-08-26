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
| 2 | 公告怎么和「还没建完」区分 | 加 `Event.takes_signups` 显式开关 |
| 3 | 想参加公告的人怎么「记住」它 | 不做新东西。需要被记住的一律开一个 `attending` 角色（`needed_count` 留空） |
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
| 批二 · L3 + L2 | 可见性 + 资格 + 公告 + `EventType` 上页面 | 一个整体：不变量横跨两层，拆开交付会留一个「角色比活动宽」的窗口期。本轮权限面最大的一批，必须配浏览器验收 |
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
- 真正的后果（不记工时）对一个来占座位的人**不是他关心的事** ——
  那是机构记账的事。

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
| `_top_participants()` | 按工时排，attending 的工时是 `None`，现有的 `nulls_last` 已经把他们排在外面，而且它本来就叫 Most hours |
| `hours_by_role` 图 | 它已经 `.exclude(hours__isnull=True)`，attending 的角色不可能有工时，所以一行都不会出现 |
| `_role_gap` 图 | 它是**按角色**画的，「ESL seat：要 12，来了 3」这一行准确且有用。混在一起会出错的是那个**比率**，不是这张图 |

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
和「还没建完」「这是一条公告」长得一模一样。[L2.5](#l25-公告) 因此要从两句话变三句，
而这正是 [D27](decisions/D27-ministry-report.md) 那条「没有和没算不能长得一样」。

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

## L2.5 公告

`takes_signups=False` 时：

- `Event.accepting_signups` 属性把它 AND 进去（那是 `can_sign_up` 的唯一来源）；
- 不许有角色。同 L2.3 一样是跨表的，落在 `EventRole.clean()` 和 `EventForm.clean()`；
- 两处空状态分成两句话，这一格是[验收](#验收)里点名的一条：

| 情况 | 文案 |
|---|---|
| `takes_signups=False` | This is an announcement — there is nothing to sign up for. |
| `takes_signups=True` 且零角色 | 保持现在那句 No roles opened yet. |

⚠️ 现在这两种情况长得一模一样，正是 [D27](decisions/D27-ministry-report.md) 那条
「没有和没算不能长得一样」。要改的文件是
`_event_roles_panel.html`（第 55 行那句 empty）和 `_event_detail_body.html`（第 158 行那格）。

行业依据写进 [D27](decisions/D27-ministry-report.md) 或 `participants.md`：
ChurchSuite 从零设计就把报名做成每场活动的显式开关（开关关着时，
Sign-Ups / Tickets 这些页签根本不出现），可见性是另一组设置；
Planning Center 是被「有人以为在 Groups 里 RSVP 了就等于报名了」这个 bug
逼着补上同一个开关的。两家最终落在同一个形状上。

## L2.6 `EventType` 上页面

`EventPeriodForm` 加一个 `event_type` 的 `ModelChoiceField`
（`is_active=True`，`empty_label="All kinds"`），`narrow()` 里多一个 filter，
`order_fields` 里排在 ministry 后面，`description()` 跟着补一句。
详情页和管理列表显示类型。

目的只有一个：让这张字典表有真读者 ——
[D5](decisions/D05-lookup-tables-not-enums.md) 那一行从「没有一处代码 branch 它、
前台模板命中 0 次」变成「有页面」。

## L2.7 批二的测试与验收

测试：

- `test_an_outside_account_does_not_see_a_staff_only_event`
- `test_a_staff_member_of_another_ministry_sees_but_cannot_sign_up`
- `test_visibility_is_judged_on_the_day_of_the_event`
- `test_the_schedule_narrows_by_audience_too`
- `test_signing_up_for_an_event_you_cannot_see_is_a_404`
- `test_cancelling_visibility_does_not_hide_an_event_you_already_signed_up_for`
- `test_an_announcement_says_it_takes_no_signups`
- `test_an_announcement_cannot_have_roles`
- `test_the_event_list_filters_by_kind`

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

## L5.1 `Session`：一期课的第几讲

```python
class Session(TimeStampedModel):
    """一期活动里的一次聚会。⚠️ 它不是 `Event`。

    ESL 春季班是**一个** `Event`（3/1 起、6/20 止），十二次聚会是它下面的
    十二行 `Session`。这正是 participants.md 第九节说 `Event` 装不下的那句话
    ——「他从 3 月到 6 月在这个项目里」—— 而 `Event` 其实一直有起止两列，
    缺的只是中间那些时刻。

    ⚠️ 和 `Event` 的分工是硬的，三条都要成立：
       · `Session` **不能单独报名**（报名挂在 `Event` 的角色上，整期一次）
       · `Session` **没有自己的受众**（L2/L3 在 `Event` 和 `EventRole` 上）
       · `Session` **不进活动列表页**（它不是一场活动，是一场活动的一次聚会）
       任何一条要破，说明那个东西其实是 `Event`，该走 recurring events 那一档。

    ⚠️ 和 `Shift` 的分界线也没有变（participants.md 第六节）：有固定岗位 +
       按周重复 + 机构对他的时间有承诺 → `Shift`。一期课的学员不是在上班。
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="sessions")
    starts_at / ends_at
    # 由生成器造的还是人手加的 —— 同 Event.source 的用途，见 L5.4 那句删除
    source = ...
```

⚠️ 唯一约束 `(event, starts_at)`：同一期课不可能有两次同时开始的聚会。

⚠️ `Event.duration`（R3）对 Program 会变成「111 天」。**不改它** ——
那两列说的就是这个，而报表上那一格对 Program 本来就没有意义。
真要显示「每次两小时」，那是 `Session` 的时长，属于**新页面**的事。
记在这里是因为它看起来像个 bug。

## L5.2 `SessionAttendance`：他哪几场、来没来、干了多久

```python
class SessionAttendance(TimeStampedModel):
    """一个人的一次聚会。行业里这一层各有各的名字，形状是同一个。

    Salesforce PMM 叫 `ServiceDelivery`（报名是 `ServiceParticipant`），
    Apricot 叫 Attendance Tracker（报名是 enrollment），
    ChurchSuite 开着「sign up to the sequence」时把出勤汇成一张「随时间」的表。
    ⚠️ 五个平台查下来没有例外：**报一次 ≠ 出勤一次**，报名一层、出勤一层。

    ⚠️ 它**不是**一行 `Participation`，而这是决定 19 换掉的那个形状。
       两者的字段确实很像，区别在语义：`Participation` 是「他报了这一期」，
       报表数它得到「多少人报名」；这张表是「他来了第几讲」，数它得到的是
       「课时人次」——两个不同的数，而合成一个正是本项目判过三次的病。
    """

    participation = models.ForeignKey(
        Participation, on_delete=models.CASCADE, related_name="sessions")
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="+")
    status = ...          # 同 Participation.Status，但只在这一场上成立
    hours = ...           # 决定 20
    checked_in_at / checked_out_at / checked_in_method
    history = HistoricalRecords()
```

⚠️ 唯一约束 `(participation, session)`。

### 三条从 `Participation` 搬过来的规则，一条都不能漏

| 规则 | 为什么在这里同样成立 |
|---|---|
| 「来参加的位置不记工时」的那条 `CheckConstraint` | 角色的档位没变。⚠️ 判据仍然读 `participation.event_role.role.nature`，所以 `records_hours` 那个属性要能从这一层问出来 |
| 工时非负 / 只有出席过才有工时 | 同款，逐条抄，**不是**「大概同款」—— 抄漏一条的表现是那张表比它复制的那张松 |
| `hours` 是 `Decimal` 不是 `Float` | D 那条老规矩 |

⚠️ `served_as`（身份）**不搬**：它是整期一次的声明，留在 `Participation` 上。
D38 第五节那张表问的是「他这次参加算什么」，而对一期课来说「这次」就是这一期。

### ⚠️ 决定 18（中途加入）不是一个字段，是「哪几行存在」

第 5 周才报名的人，`SessionAttendance` 只为**第 5 讲起**的那些聚会建行。
于是他的出勤率分母是 8 不是 12 —— 前四讲对他**不存在**，不是缺席。

⚠️ 这也正好是决定 17（挑哪几场）的机制：只为选中的那几场建行。
两条需求一个实现，而不是两个字段。

## L5.3 三档单选落在哪

```python
class Event(...):
    class Shape(models.TextChoices):
        SINGLE = "single", "One occasion"
        PROGRAMME = "programme", "A course or programme — sign up once"
```

⚠️ **枚举只有两档，而界面上是三档。** 第二档（recurring events：每周一场、
各自报名）生成的是 **N 个独立的 `Event`，每个都是 `single`** ——
它是建活动时的一个**生成选项**，不是 `Event` 上的一个状态。
三档单选是发布表单上的一个 `ChoiceField`，其中两档写进这一列、
一档触发生成器。

⚠️ 那为什么不靠 `sessions.exists()` 判、非要一列？两条：
一是列表页要按它筛（`Exists` 子查询每次都要 join）；
二是**一个还没排期的 Program 也是 Program** —— 建的时候先定形状、再排日期，
是很自然的顺序，而 `sessions.exists()` 在那一刻会答错。

### 两个谓词，页面怎么用**本轮不定**

```python
    def programmes(self): ...      # Shape.PROGRAMME
    def single_occasions(self): ...  # Shape.SINGLE
```

⚠️ 基金会说 Programs 会有**自己的页面**，而页面设计要等后端定完。
所以后端把两者分得开，而**现有 `/events/` 列表页排不排除 Program 这一格留白** ——
在设计出来之前替它决定，就是在猜。

⚠️ 留白的是「用哪个」，不是「有没有」：两个谓词都要写、都要有测试。

## L5.4 recurring events 那一档：`EventSeries` + 生成器

第二档要的仍然是初版那两件事（按规则生成多场 + 多场归成一组），
所以 `EventSeries` 留下来，但它的角色收窄了：**只服务第二档**。

```python
class EventSeries(TimeStampedModel):
    """一条规则 + 一份模板，生成 N 场**各自独立**的 Event。

    ⚠️ 它和 Program 不是一回事，而这两个词在英文里几乎同义，所以写死：
       · `EventSeries` → N 个 `Event`，各自报名、各自受众、各自出现在列表页
       · Program       → 一个 `Event` + N 个 `Session`，报一次管全部
       选哪一个是发布时的三档单选，选完不能互换（那是一次数据迁移）。

    ⚠️ 载体判定作废的记录：推迟清单里 `Event.parent`（活动系列）写的是
       「按 D15 三条件检验 → 自引用 FK 正是对的载体」，其中第二条是
       「关系自己没有属性」。而生成规则就是属性 —— D15 自己盯着这一格，
       明写「条件破了就必须升级成表」。
    """

    name / ministry / event_type / owner
    rule = models.TextField()          # RFC 5545 的 RRULE，不含 DTSTART
    starts_on / start_time / duration
    location / description / image
    可见性三件套 / takes_signups / requires_guardian_consent   # 模板
    ended_on                            # 「即日停止」
    undone_at / undone_by               # 整批撤销
    history = HistoricalRecords(m2m_fields=["visible_to_ministries"])
```

`Event` 加两列：`series`（可空 FK）和 `source`（`manual` / `generated`）。

⚠️ 为什么不是「第一场兼作母本」（Google / CiviCRM 的形状）：那让一行同时是
系列和一场，删它、改它各有两种读法，而[三方集成里反复出问题的正是这一点](https://community.zapier.com/troubleshooting-99/new-or-updated-google-calendar-event-triggered-for-old-copies-of-recurring-events-42518)。
独立成表还顺带解决了批次身份 —— 一个系列就是一次批量动作，
[D40](decisions/D40-undo-a-pattern-batch.md) 的 `PatternBatch` 在这里不用单独建表。

`rule` 的校验（`EventSeries.clean()`）：`rrulestr()` 解析得通，且必须带
`UNTIL` 或 `COUNT`。⚠️ 这是不做滚动物化、不加第三条 cron 的代价：
「每周一直下去」必须填一个截止日。[D33 第三节](decisions/D33-work-schedule.md)
给班次选的是滚动窗口 + cron，这里选了另一条 —— 活动本来就有结束（一门课十二讲），
班次没有。两处不同不是矛盾，但要在两边都写一句。

### `EventSeriesRole`

```python
class EventSeriesRole(TimeStampedModel):
    series → EventSeries
    role → ParticipationRole
    needed_count / stop_at_needed_count / notes
    可见性三件套（和 EventRole 同一套字段）
```

生成第 N 场时，每条模板建一行真的 `EventRole`。唯一约束 `(series, role)`。
改模板时只同步「未来的、且那一行角色上没有人报名的」场次。

## L5.5 `events/recurrence.py` —— 纯函数，两档共用

```python
def occurrences(rule, *, starts_on, start_time, window_start, window_end) -> list[datetime]
```

⚠️ **一个展开器，三个调用方**：Program 排 `Session`、recurring events 生成 `Event`、
以及 D2a 的 `WorkPattern` 生成 `Shift`（那一步还没做，写进 docstring 免得再写一个）。

用 `dateutil.rrule.rrulestr()`。`python-dateutil` 已经在 `requirements.txt` 里，
**不引入任何新依赖**。不用 `django-recurrence`：它多给的是一个字段类型和一个 widget，
而按 D18 的落点规矩，生成器本来就该是这里的纯函数。

必测夏令时切换那一天：按当地 19:00 重复的课，跨过 DST 之后 UTC 时刻会变，
测试要钉住「当地时间不变」。⚠️ 不写就会在十一月的第一个周日撞上。

## L5.6 生成、改未来、整批撤销

⭐ 删除只许写在一处，照 [D40 第一节](decisions/D40-undo-a-pattern-batch.md) 逐字搬：

```python
def _drop_generated_after(series, after):
    """这三个条件全仓只在这里出现。三个调用方：重算未来 / 即日停止 / 整批撤销。"""
```

比 D40 多一个条件：**有报名的场次一行都不许自动删**。
`Event` 删除会两级级联到 `Participation`，所以漏掉这个条件的后果是删掉工时记录。

⚠️ Program 的 `Session` 同理：**有出勤记录的 `Session` 不许自动删**。
两条是同一条规矩在两个层级上，所以那句删除也要覆盖它。

「改规则只动未来」= 老系列 `ended_on = today` + 新建一个系列（Google 的 split）。
不做原地改规则重算：原地改会让「这一场当初是按哪条规则生成的」没有答案。

整批撤销的确认屏照 D40 那一屏：数字真算，**并且把留下来的那部分写出来**。

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

🔴 这是决定 20（每场一个工时数）唯一的、也是全部的代价，
选的时候就摆出来了。⚠️ 而它带来一条必须写下来的话：
**「工时」这个词从此在两张表上**，任何新写的汇总都要问一句「另一半算了吗」。

## L5.8 页面与路由

⚠️ Programs 的页面**本轮不设计**（基金会明说要等后端定完）。本轮只做到：
两个谓词、`Session` 和 `SessionAttendance` 两张表、以及 admin 能建能看。

recurring events 那一档的路由照初版：`events/series/new/`、
`events/series/<int:pk>/`、`events/series/<int:pk>/undo/`。
⚠️ `new` 排在 `<int:pk>` 前面，同这个文件里已有的两处。

## L5.9 初版那份 `EventSeries` 哪些留下了、哪些作废

| 初版写的 | 现在 |
|---|---|
| `EventSeries` 一条规则生成 N 场 | ✅ 留下，但只服务 recurring events 那一档 |
| `EventSeriesRole` 模板表 | ✅ 留下，同上 |
| 纯函数生成器 | ✅ 留下，而且现在有三个调用方 |
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

- `test_a_programme_is_one_event_with_many_sessions`
- `test_signing_up_for_a_programme_creates_one_participation`
  —— ⚠️ 决定 19。它同时钉住 `signups` 不会因为一期课暴涨
- `test_signing_up_for_a_programme_covers_every_session`
- `test_picking_some_sessions_leaves_the_others_alone`（决定 17）
- `test_joining_in_week_five_is_not_four_absences`（决定 18）
  —— ⚠️ 出勤率的分母是 8 不是 12
- `test_hours_on_a_session_are_counted_by_the_report`（决定 20 的 union）
- `test_a_seat_in_a_programme_still_records_no_hours`
  —— L1/L4 那条规则在新表上同样成立
- `test_a_session_somebody_attended_is_never_deleted_by_the_generator`

recurring events：

- `test_twelve_occasions_are_generated_with_their_roles`
- `test_changing_the_rule_leaves_past_occasions_alone`
- `test_an_occasion_somebody_signed_up_for_is_never_deleted`
- `test_undoing_a_batch_says_what_it_will_leave_behind`
- `test_undoing_right_after_creating_leaves_nothing_behind`

两个谓词：

- `test_the_two_predicates_do_not_overlap`
  —— ⚠️ 页面怎么用还没定，但「一场活动只属于其中一个」现在就要钉住

---

# 本轮新增的守卫（五条）

| # | 名字 | 盯什么 |
|---|---|---|
| 1 | `AudienceIsAskedGuardTests` | 调用 `visible_to_participants()` 的函数体里必须同时调 `for_audience(` |
| 2 | `AudienceContainmentGuardTests` | 「角色的范围 ⊆ 活动的范围」那三条比较只许出现在 `refuse_wider_than_event()` 里。⚠️ 改成多选之后可比的东西变多了，这条比枚举时代更必要 |
| 3 | `HoursWriteGuardTests` | `.hours =` 只出现在 `events/services.py`（现在就成立，这一条是把现状钉住） |
| 4 | `GeneratedEventDeleteGuardTests` | 生成场次的那三个删除条件只出现在 `_drop_generated_after()` |
| 5 | `LocalDayInSqlGuardTests` | `TruncDate(` 只出现在 `on_the_books_exists()` 所在的文件，且那一行带 `tzinfo=` |

每一条都要做双向验证：故意写错一处，确认它真的红 —— 这是本项目对守卫的既有要求，
而守卫一和守卫五都属于「不做反向验证就等于没写」的那一类。

# 本轮要动的文件总表

清点用。批次列写「一/二/三」。

| 文件 | 批 | 干什么 |
|---|---|---|
| `events/models.py` | 一二三 | `nature`、`NOT_APPLICABLE`、新约束、第二个兜底工种、可见性的两个布尔 + 一张多对多（`Event` / `EventRole` 各一套）、`takes_signups`、`refuse_wider_than_event()`、`for_audience()`、`Event.shape` + 两个谓词、`Session`、`SessionAttendance`、`EventSeries`、`EventSeriesRole`、`Event.series` / `Event.source` |
| `events/services.py` | 一二三 | `on_the_books_q()` / `on_the_books_exists()`、`default_served_as()`、`record_hours()`、`check_out()`、`create_participation_role()`、`ministry_report()`、`_people_served()`、`eligible()` / `eligible_role_ids()`、`sign_up()`、系列的生成与撤销、⚠️ L5.7：工时的四个口径要 union `SessionAttendance` |
| `events/forms.py` | 一二三 | `RoleChoiceField`、`SignUpForm`、`EventRoleForm`、`EventForm`（加三档单选）、`EventPeriodForm`、新的 `EventSeriesForm` |
| `events/views.py` | 一二三 | `_visible_events()`、`_schedule()`、`_detail()`、`event_signup`、`event_registrations`、`event_attendance`、系列的三个视图 |
| `events/urls.py` | 三 | 系列的三条路由 |
| `events/admin.py` | 一二三 | `ParticipationRoleAdmin` 加 `nature`；`EventAdmin` 和 `EventRoleAdmin` 各加三个可见性字段；`Session` / `SessionAttendance` / `EventSeries` 注册 |
| `events/recurrence.py` | 三 | 新文件，纯函数 |
| `events/migrations/0016_participationrole_nature.py` | 一 | 新 |
| `events/migrations/0017_served_as_not_applicable.py` | 一 | 新 |
| `events/migrations/0018_audience_and_signups.py` | 二 | 新 |
| `events/migrations/0019_event_series.py` | 三 | 新 |
| `core/constraints.py` | 一 | `CONSTRAINT_FIELD` 加一行 |
| `core/timeutils.py` | 一 | `local_day()` —— `local_date_of()` 的 ORM 双胞胎，`tzinfo` 包在里面 |
| `core/querysets.py` | 一 | `in_effect_on()` 的 docstring：`on` 现在也可以是数据库表达式 |
| `core/tests.py` | 一二三 | 五条新守卫 |
| `events/tests.py` | 一二三 | 上面列的全部测试 |
| `events/templates/events/_report_body.html` | 一 | 分母说明、`hours_missing` 措辞、People served |
| `events/templates/events/_attendance_row.html` | 一 | attending 不画工时 |
| `events/templates/events/event_registrations.html` | 一 | 身份下拉按角色档位收窄 |
| `events/templates/events/my_participations.html` | 一 | 不印 Not applicable |
| `events/templates/events/event_report.html` | 一 | 同上 |
| `events/templates/events/_event_roles_panel.html` | 一二 | 档位列；公告的空状态 |
| `events/templates/events/_event_detail_body.html` | 一二 | 档位列；公告的空状态；报不上的角色带原因 |
| `events/templates/events/_period_filter.html` | 二 | 多一个 kind 下拉 |
| `events/templates/events/event_form.html` | 二三 | `audience` / `takes_signups`；系列入口 |
| `events/management/commands/seed_demo.py` | 一二三 | ESL 工种与活动；一场内部活动；一个系列 |
| `docs/planning/diagrams/src/page.html` | 三 | ERD 加三个字段和两张表，DFD 加一条生成的路，表册加两行。⚠️ 改完要按 `docs/planning/diagrams/README.md` 重新生成 `data-and-flow.html`，那一步要 `npm i mermaid puppeteer-core` |

| `core/management/commands/check_deployment.py` | 一 | L1.6：工种表的门槛从 2 提到 3 |
| `events/migrations/0018_second_catch_all_role.py` | 一 | 新（L1.6），数据迁移 |

⚠️ 不动的文件，写下来是因为它们看起来该动：
`org/permissions.py`（受众不是授权，是可见性；授权仍然只有 MinistryRole 那一套）、
`events/tokens.py` 与扫码那几个视图（收窄的是发现，不是已经拥有的行）、
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
| [D38](decisions/D38-served-as-volunteer-or-work.md) | 加 `not_applicable` 一档，写明它不是身份、永远不出现在表单上、且它换来了一条真正的约束 |
| [D27](decisions/D27-ministry-report.md) | 指标拆成两组并排不相加；`hours_per_participant` 的分母改口；新增 People served |
| [D19](decisions/D19-event-role.md) | `EventRole` 长出「谁报得上」那一组勾选（两个布尔 + 一张多对多）；并写明 L1 为什么落在 `ParticipationRole` 而不是这里 |
| [D5](decisions/D05-lookup-tables-not-enums.md) | `EventType` 从「没有 branch」变成「有页面」；`nature` 作为「字典表上的枚举列」的第二个例子 |
| [`deferred.md`](deferred.md) | `Event.parent` 出栏，并注明载体判定作废的理由 |
| [`phase-b.md`](phase-b.md) | 可见性那一节补 L3 这一维 |
| [D32](decisions/D32-worker-axes-schedule-and-assignment.md) | ✅ 2026-08-21 已改：那条不变量的标题原来写的是「一个人在基金会里只有一条在编路径」，会被读成「一个人只能有一行任职」。改成「在编只有一套结构」，并补一小节写明一人多岗是常态、判据一律写成存在性判断 |
| [D33](decisions/D33-work-schedule.md) | 第三节旁边补一句：活动的系列选了「必须有结束条件、一次生成完」，和班次的滚动窗口不同，理由在本文件 L5.1 |
| [`05-roadmap.md`](05-roadmap.md) | D1.4 与本轮的关系；D2a 的生成器要调 `events/recurrence.py` |
| [`phase-d.md`](phase-d.md) | 同上 |
| [`goal.md`](goal.md) | ⚠️ 核对时发现的一处欠账：goal.md 自称是「唯一入口」，而它开头那张「去哪找」的表里列了 01–05 和 phase-b/c/d，**没有 `participants.md`** —— 那份文档从 2026-08-20 起就一直不在索引里。本轮把它和本文件一起补进去 |

⚠️ [`revisions.md`](revisions.md) 不在上表里，而这是判断不是遗漏：那份文档记的是
「用户报来一批问题 → 底下各有一条看不见的规则」，按批次编号。
本轮的九条需求原文已经一字不改地记在 [`participants.md` 第二节](participants.md) 里了，
再抄一份到 revisions.md 就是同一件事的第二处真相。
⚠️ 例外：实施中如果撞出「用户报的现象和真正的成因不是一回事」那种事，那一条属于 revisions.md。

# 全轮

- `python manage.py check` / `makemigrations --check` / `ruff` 干净
- 测试数只增不减
- 五条新守卫全部做过双向验证
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
- [ ] 活动列表可以按类型筛选

批三：

- [ ] 一门课按规则生成 N 场，N 场归成一组，每一场都带着角色
- [ ] 改规则只动未来的场次，且有人报名的那一场一行没动
- [ ] 跨过夏令时切换，当地时间不变
- [ ] 刚建完就撤销 → 库里干净得像没发生过
- [ ] 三周后再撤销 → 确认屏说得出会留下几场、为什么留

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
`Ministry` / `Position` / `EmploymentType` / `EventType` 在测试库里全是 0 行，
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
