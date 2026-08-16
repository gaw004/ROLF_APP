## 五、明确推迟的事

记下来是为了不反复纠结。

> **2026-07-29 新增三行（列在最前）** —— 它们是按[零的判定规则](goal.md#零当前优先级2026-07-29-定)
> （"是不是那 14 条需求的前置条件"）从 Phase B 移出来的，不是新想出来的推迟项。

| 事情 | 为什么现在不做 | 什么时候再看 |
|------|--------------|------------|
| `VolunteerProfile`（原 Phase B B7） | 14 条需求一条都没提到技能、可服务时段。 表的形状早就想清楚了（OneToOne → `Contact` + `availability_notes`），建它是十分钟的事，但它不在任何一条需求的查询路径上 | 真的要按"谁星期六有空"排班时。**注意它不是 `Participation` 的前置条件** —— 志愿者报名只需要 `Contact` 和一个 account，不需要志愿者档案 |
| `BackgroundCheck`（原 Phase B B7）<br>2026-08-14 补：Phase D 建了通用的 `Credential`（CPR / 食品安全 / 按立 / 驾照），**它不吃掉这一张**。塞进去等于让所有能看证照的人都能看背景审查结果，而那正是 D18 拆表要防的事。见 [D37 第三节](decisions/D37-hris-fields-and-credentials.md) | 同上。⚠️ **但 [D18](decisions/D18-admin-boundary.md#-权限的形状会倒推模型敏感字段必须独立成模型) 那条"必须独立成 model、不能是 `VolunteerProfile` 上的字段"的决定不撤销** —— 建的时候仍然是两个模型。**推迟的是建表，不是推翻拆表** | 基金会答复审查有效期、且真的开始做审查时。形状已定：OneToOne → `Contact`（不是挂 `VolunteerProfile`）/ `status` / `completed_on` / simple-history / `BACKGROUND_CHECK_VALID_DAYS` 放 settings |
| `Event.parent`（活动系列）/ 奖励规则 | 都要等基金会说清楚，且都是"加字段不改结构"。**关键是区分它们所需的维度已经全部存下来了**（哪场 `Event`、哪个 `EventRole`、多少 `hours`） | 见下面各自那一行 |
| 一个 Contact 多个 email / 电话 / 地址 | 见 D13，单字段够用且可逆 | 出现"两个邮箱分别用"的真实需求时 |
| 薪酬 / 工资数据 | 见 D11，敏感度最高且 MVP 无功能需要 | 要算人力成本或做预算报表时，连同权限方案一起设计 |
| 一个编制多个上级（矩阵式实线/虚线汇报） | 见 D11，小基金会极少有；`Position.reports_to` 现在是单个外键 | 真的出现双线汇报时，把 `reports_to` 改成多对多 |
| 带日期的编制层级（组织架构的历史） | D11 第二次修订解决了"**换人**"，没解决"**重组**"。`Position.reports_to` 是无日期的可变字段，改了旧架构就只剩 simple-history。这是 D15「载体二」第二个条件（关系自己没有属性）的边界 —— 一旦汇报线需要起止日期，条件就破了，按规则必须升级成表 | 需要回答"**2025 年 3 月的组织架构长什么样**"时。做法是给编制层级单独一张带 `start_date` / `end_date` 的表（`PositionReportingLine`），`Position.reports_to` 降级为"当前值"的缓存或直接删掉。**这是双时态建模，成本不低，别顺手做** |
| ~~请假 / 停职的历史（`Leave` 表）~~ | ✅ **2026-08-14 出栏，进 Phase D** —— 重启条件真的到了，而且是被班表逼到的：有了稠密的 `Shift` 之后，「两周假手工去翻 10 行班次」没有人会真的执行。形状和这一行当初写的一字不差。见 [D34](decisions/D34-leave.md) | — |
| ~~`Position.headcount`（编制人数）~~ | ✅ **2026-08-14 出栏，进 Phase D** —— 基金会问「每个职位多少人」时包含了"**应该**有几人"。加一个整数字段，不改结构。⚠️ **但不是照这一行当初写的那样改 `vacant()`** —— 那会打破 `PositionQuerySet` 的三态划分（半满的岗位同时是 vacant 和 occupied）。改成**新增 `understaffed()`**，`vacant()` 一个字不动（[D37 代价 1](decisions/D37-hris-fields-and-credentials.md)） | — |
| Membership / 会员制 | 基金会未必有会员概念 | 需求出现时 |
| REST API、前后端分离（React / Vue） | **2026-07-31 重新问过一遍，结论不变**（起因是"页面要好看"—— 而颜值来自 CSS，**Tailwind 不是 React**）。分离想买的东西是"换前端时后端不用动"，这个项目**已经靠 [D18](decisions/D18-admin-boundary.md) 的落点规矩 + 12 条 grep 守卫买到了"**：逻辑在 `services.py`、权限只在 `org/permissions.py`、统计在 queryset、视图是薄壳，真要换只需加一层 serializer。<br>⚠️ **而分离最贵的代价正好打在 [D20](decisions/D20-ministry-role.md) 上：权限判断会变成两份** —— 前端够不到 `permissions.py`，却要自己决定显不显示按钮，而漏掉的权限检查是静默的。其余代价：12 个页面各要 (endpoint + serializer + 前端状态 + loading + error)，约 3–4 倍代码；现有 334 个端到端测试会退化成只覆盖 API 那一半。<br>**2026-08-03 第三次确认，结论仍然不变** —— 这一次是在「前端要现代、要丝滑」的前提下重问的，正面方案定成了 **Tailwind + HTMX + Alpine**，见 [D24](decisions/D24-htmx-alpine-tailwind.md)。颜值来自 CSS 和设计系统，和渲染发生在哪一端无关 | 真的要做**手机 App**（多客户端喂同一套后端），**或者**真的有第二个人专职做前端时。那时加 DRF 是几天的事，**因为业务逻辑早就不在模板里** |
| 自建云（AWS / GCP）部署 | **2026-07-31 定：Phase C 走 Render。** 同规模下自建云**更贵也更累** —— Render 约 $14–20/月、半天搭好；AWS App Runner + RDS 约 $50–90/月、2–3 天，走 ECS Fargate 还要自己搭 VPC / 子网 / ALB / 安全组，单 NAT Gateway 一项就 ~$32/月。撞[终极目标](goal.md#一终极目标)那张表的两行：「起步月成本几十美元内」和「一个人能读完全部代码」。<br>**这不是锁定** —— [D3](decisions/D03-portable-postgres.md) 一直在保护这件事：一个 `pg_dump` 带走全库，应用是标准 Django 镜像，没用任何厂商专有服务 | 月账单真的超过自建云的等价配置时，**或者**出现平台顶不住的需求（多区域、私有网络合规、要跑 Render 没有的托管服务）。搬家成本约一天 |
| 软删除 | 现有 `is_active` 已覆盖"停用"语义，不同时上两套 | 出现"误删要恢复"的真实事故时 |
| `Guardianship` 法定监护专用表 | 2026-07-28 移出 Phase B，**2026-07-29 确认继续推迟**。P3 说的是"注册参加 event 时的 guardian consent" —— 那是**一次活动的一条同意记录**（已落在 `Participation` 的同意字段上），不是"谁是小明的法定监护人"这段**长期关系**。需求要的是前者，先做前者。<br>⚠️ 判据是 [D15](decisions/D15-relationship-carriers.md#d15--关系用什么载体承载四条判据--选择规则) 的老问题换了个场合：**同一个词（"监护"）指向两个不同形状的东西时，先看需求里的那个句子在问什么** —— "这次活动家长同意了吗"和"谁是他的监护人"是两个问题 | 出现"每次活动都要重填一遍家长信息、烦"的真实抱怨时 —— 那时 `Guardianship` 变成同意字段的**默认值来源**，而不是取代它。形状：`minor` / `guardian` → Contact（都 `PROTECT`，不同 `related_name`）/ 监护类型 / 同意书签署日期 / 能否代签 / 起止日期；建的时候**必须**同期带上 `UniqueConstraint(minor, guardian, start_date, nulls_distinct=False)`、`end_date >= start_date`、"监护人不能是自己"、simple-history，以及**两侧的 admin inline**（一段关系只存一行，另一头看不见就等于没记）。放 `contact` app（D17） |
| 把 `EmergencyContact.name` / `.phone` 升级成 FK → `Contact` | 现在存文本（D15 第三次修订，为了不让第三方进 `Contact`）。代价是重复存储、无反查、且当紧急联系人本来就是系统里的人时数据会分裂 —— **这些是主动接受的，不是遗漏**。⚠️ **这是文档里标注为「痛」的那个迁移方向**：要去重、判断同一人、建 `Contact`、连关系，且不可逆 | 重复存储真的造成过一次事故时（打了过期的号码）。**升级前必须先想清楚 D15 第四条判据怎么办** —— 那正是当初选文本的唯一理由，不能因为嫌重复就把它推翻。折中方案是加一个**可空**的 `contact` FK：能关联的关联，关联不上的留文本 |
| 逐字段合并的交互界面（"保留哪个邮箱、哪个地址"） | 合并功能本身**暂列 Phase B**（理由换过一次，见那一节）。MVP 阶段规则简单够用：保留方字段优先，被合并方只在保留方为空时补进来 | 真出现"两条都有值且都不想丢"的实际争议时 |
| `Skill` 字典表 + `VolunteerProfile.skills` | 需求不紧急，且**没有任何东西依赖它**（ministry 视图不需要技能）。设计已想清楚：字典表带 `code`（D5 通则）+ M2M 挂 `VolunteerProfile`，要加时照 D5 直接建，无需重新设计 | 真的要按技能找志愿者时（"谁会西班牙语翻译"）。注意语言偏好已有 `Contact.preferred_language`，别和技能混为一谈。**2026-08-15 补它挡着谁**：[D33 第十节](decisions/D33-work-schedule.md) 推迟的「开放班次（先建班次再找人认领）」**以它为前置** —— 让人认领一个班次，前提是系统说得出「这个班次要什么本事」。两条要一起重启，否则会做出一个谁都能认领任何班次的入口 |
| 换掉邻接表：递归 CTE 或 Postgres `LTREE` | `Position` 是几十行，一次查询取全表在内存建树（`build_org_tree()`）已经是最优解 —— 一次查询、零 N+1。LTREE 另外要付三笔账（依赖 `save()` 维护 path 违反 D9、丢外键完整性和 `PROTECT`、或者 FK + path 并存违反 D11），理由全文见「为什么不上 LTREE」 | `Position` 涨到 ~1000 行以上，**或者**"查某个编制的全部下属子树"变成高频查询时。**那时第一选择也不是 LTREE，是 `WITH RECURSIVE`** —— 不装任何东西、不丢外键、`build_org_tree()` 换个实现，调用方一个字不改。LTREE 要等到"递归 CTE 实测跑不动"才轮得上 |
| Ministry 的层级（子 ministry） | ERPNext 的 Department 是树形，但小基金会大概率是平的。真要加就是一个可空的 `parent` 自引用 FK —— 按 D15 的三条件检验：最多一个父、无独立属性、只有一种类型，**自引用 FK 正是对的载体** | 真出现"报税志愿下面还分几个小组"时 |
| `PositionRole` 字典表（取代 `Position.is_leader` 布尔） | 现在只需要区分 leader / 非 leader，一个布尔够了 | 角色长到第三种时（如 leader / 副手 / 培训中），按 D5 升级成带 `code` 的字典表 |
| 活动的班次（`Shift`）<br>⚠️ **和 Phase D 建的那个 `Shift` 不是一回事** —— 这一行说的是**一场活动内部**的班次，[D33](decisions/D33-work-schedule.md) 建的是**岗位的排班**。同名，两件事，**本行的否决不撤销** | 2026-07-28 决定：多班次一律拆成多个 `Event`。行业标准结构确实是三层（Salesforce V4S 的 Job → Shift → Hours），但拆 Event 之后时段差异由 Event 表达、做的事差异由 `Participation.role` 表达、时长差异由各行 `hours` 表达，三个维度一个不少，而少一张表少一层 admin 嵌套 | 一场活动的班次多到"拆成十个 Event"开始碍事时 |
| 活动的分组 / 系列（`Event.parent`） | 拆成多个 Event 的代价是"上午场 / 下午场"在统计里算两场。按 D15 三条件检验：最多一个父、无独立属性、只有一种类型 → **自引用 FK 正是对的载体**，和 Ministry 层级同理 | 真的需要"这一整天总共来了多少人次"时 |
| 一个人一次活动的奖励规则 | 规则还不明确（按班次算？按角色算？按工时算？）。猜错就是白写字段。关键是**区分它所需的三个维度已经全部存下来了**（哪个 Event、什么 role、多少 hours），结构撑得住 | 基金会说清楚奖励怎么算时 —— 那时只是加字段，不用改结构 |
| 邮件**群发 / 营销**（简报、募捐信、全员公告） | 和内部管理是两回事，且要处理退订、名单管理、合规。<br>**注意它和 P6 不是一回事**：P6 是**事务性**通知（这场活动改时间了，通知**这场活动的报名者**），范围由 `Participation` 天然界定，触发条件明确。群发是"给所有人发点什么"，没有边界 | 基金会真的要做简报时 |
| `ParticipationConfirmation`（改动后重新确认报名） | 2026-07-29 定：改时间后**报名照旧**，通知里请人自行取消（D22 末尾）。加一档 `needs_reconfirmation` 是把"这个人和某次改动的关系"塞进"这个人怎么样了"那个字段，两个维度 —— 本项目已经为这种事付过两次代价 | 真出现"改完时间之后没人取消也没人来"的实际问题时。**形状不是加一档 status，是一张表**：`ParticipationConfirmation(participation, notification, confirmed_at)`，一次改动一行 |
| 通知的送达状态 / 退信处理 / 重试队列 | MVP 阶段 `EventNotification` 只记"发出去了、覆盖到谁、几个联系不上"。逐个收件人的送达/退信要接 provider 的 webhook，是一整套东西 | 真的出现"他说他没收到"且查不清时。⚠️ **`unreachable` 那一组现在就要做**，那是本系统自己算得出来的，和送达状态是两回事（D22 ②） |
| 匿名（无账号）活动报名页 | 2026-07-29 拆开说清楚，这一行原来和"邮件群发"写在一起，会被误读成"P3 也推迟了"。<br>P3 要的是「登录用户」能报名，那个进当前阶段。 推迟的是**不登录**就能报名的公开页面 —— 它需要另一套东西（防刷、验证码、匿名记录事后认领），而需求原话是"每个普通 **account** 可以看到发布的 event" | 基金会真的要做面向公众的活动招募时 |
| 一个 Contact 多个 account / 一个 account 多个 Contact | `User.contact` 是 OneToOne，够用（D12）。开放注册之后可能出现"同一个人注册了两次" —— 那是**重复 `Contact`**，`merge_contacts()` 已经能处理，不是基数问题 | 出现"一家人共用一个邮箱、想各自登录"的真实需求时 |
| 报名的等候名单 / 审批 | `EventRole.needed_count` 只提醒不阻止（同 `Contact` 重名的口径），所以报满了照样能报，不需要排队机制 | 基金会说"超了的人要排队 / 要审批"时 —— 那时 `Participation.status` 加一档 `waitlisted`，不改结构 |
| 中英双语界面（`gettext` / `.po` / `compilemessages`） | **2026-07-31 定，当天推翻了早上的相反结论**，见 [D23](decisions/D23-i18n-interface-only.md)。界面统一写英文。双语的成本全是确定的（`.po` 进仓库、`compilemessages` 进构建、文案两处维护、⚠️ **漏包一处 `{% trans %}` 不报任何错**），而收益那一侧「有人需要中文界面」这个假设**没有人验证过** | 基金会明确要中文界面，或真的出现读不懂英文的志愿者报不上名时。**方案已经写好了，在 D23 折叠的那一节里**（`gettext` + `set_language` + cookie，不上 `i18n_patterns` —— 后者会逼你改 334 个测试里写死的 URL）。⚠️ 重启时**不要改成中文单语**，那只是把同一个问题换个人承受 |

## Phase D 定案时推迟的（2026-08-14）

> 这一批和上面那张表的成因不同：它们**不是"以后再说"，是当天被一条判据当场筛掉的** ——
> [`goal.md` 零](goal.md#零当前优先级2026-07-29-定)末尾那句
> 「这张表 / 这个字段，会出现在哪条需求的查询里？答不上来就先别做」，
> 2026-08-14 第一次被真的执行了一遍。经过见
> [`phase-d.md` 第六节](phase-d.md#六自查这一轮砍掉和补上的东西)。

| 事情 | 为什么现在不做 | 什么时候再看 |
|------|--------------|------------|
| `Position.location` | 说不出谁读它 —— 一个 ministry 一个地点，而 ministry 已经有了 | 真的出现"同一个 ministry 在两个堂点"时。一个 `CharField` |
| `Assignment.work_arrangement`（onsite / hybrid / remote） | 说不出谁读它 —— 现场服务机构，`remote` 这一档现在没有对应的人 | 真的有人常态远程时 |
| `Assignment.end_reason` + `EndReason` 字典表 | 说不出谁读它，而它要付一张字典表 + 一次种子迁移（⚠️ [C0.2.1 的教训](03-roadmap.md#c021--给字典表加种子迁移打红了-40-个测试)：会打红一批既有测试） | 真的要统计"人为什么离开"时 |
| `Assignment.is_rehirable` | 说不出谁读它，⚠️ **而且它是最敏感的那一类**（「此人不可再雇」会引起纠纷），而 `MinistryRole` 现在只有 admin 一档 —— 做了就等于所有 ministry admin 都看得见 | 真的要跟踪"离开又回来"的人时。⚠️ **出栏时必须同期建 `StaffProfile` 或等价的权限边界**（[D37](decisions/D37-hris-fields-and-credentials.md)），不能顺手加个字段 |
| `Participation.source`（`self_signup` / `assigned`） | 它是[身份轴](decisions/D38-served-as-volunteer-or-work.md)的**代理变量**，真事实一记下来就没有读者了；而它和 `served_as` 长得太像，留着一定会被合并成一个 | ⚠️ **大概率永不重启** —— 「这一行是谁造的」simple-history 答得更细 |
| 请假的余额 / 累计 / 额度 · 审批工作流 | [D34](decisions/D34-leave.md)：本轮只做"请了几天"，不做"还剩几天" | 真的要回答"他还剩几天假"时。形状照 ERPNext：`LeaveAllocation` |
| `Shift.status` 的 `cancelled` 档 | 说不出谁写它 —— 四条写入路径（生成 / 例会确认 / 请假 / 删行）没有一条会用到它。留着只是给口径函数多一个答不出来的分支 | 真的出现"这个班次取消了但要留痕"时。⚠️ 那时先问一句：它和"删掉这一行"差在哪 |
| `LeaveType.is_sensitive` | 说不出谁读它 —— [D34 第四节](decisions/D34-leave.md) 把**整个类型**对 ministry admin 藏了，没有判断会去问这一列。⚠️ 而它会让人以为"没标敏感的就可以给人看" | 真的要分级显示请假类型时 |
| 「下周班表已生成」这条周期性通知 | 每周发给每个人，而绝大多数周它什么都没变。⚠️ 它会训练所有人忽略这个发件人，连"你的班次改了"一起忽略掉 | ⚠️ **大概率永不重启** —— 班表有两个"自己去看"的入口，其中 `.ics` 是自动更新的 |
| `Assignment.status` 的 `on_leave` 档 | 被 `Leave` 表取代，本轮**退休并迁移**（不是推迟）。没有代码会再写它，而 `serving()` 在读它 —— 一个没人维护的缓存 | — |
| 换班（swap）· 开放班次（先建班次再找人认领）· 轮换模式（双周 / 每月第一三周）· blockout | [D33 第十节](decisions/D33-work-schedule.md)，各自带重启条件。⚠️ blockout 和 `Leave` **不是一回事**：请假是已经在编的人不上班，blockout 是排班之前的可用性 | 各见 D33 那张表 |
| `self_logged` 工时日志（`HoursLog` + admin 审核） | [D36](decisions/D36-two-hour-ledgers.md)：`hours_tracking` 另外三档已经让那一格不显示 0 了，而自助申报要一张表加一个审核流程，且填的人正是"干活时也顺手干点别的"的人 —— 数字质量不见得比一个谈好的 `agreed` 值高 | 真的有人抱怨「我投入了但系统上看不出来」时 |
| ~~批量建例会模板的整批撤销~~ | ✅ **2026-08-15 当天出栏，进 [D40](decisions/D40-undo-a-pattern-batch.md)** —— 推迟了不到一天。⚠️ 推迟的理由（「只是缺一个入口」）只算了实现成本，**没算这个入口本身的当量**：一次点击 30 条模板、约 390 行班次，是本轮单次影响面最大的动作。⚠️ 而真做起来它不是「加一个删除」—— 撤销是**停止并收回未来**，过去的班次一行都不许动 | — |
| 别的批量动作的撤销（例会批量确认出勤） | [D40 第七节](decisions/D40-undo-a-pattern-batch.md)：形状不一样 —— 那一件改的是既有行的**状态**，撤销要存旧值而不是删行 | 出现「一屏勾错了一整批」的实际问题时 |
| 撤销的重做（redo） | ⚠️ **大概率永不做** —— 重建一次就是批量入口本身，而 redo 要存下「撤销前长什么样」 | — |
| ~~同一人两个岗位的排班冲突检测~~ | ✅ **2026-08-15 出栏，进 [D39](decisions/D39-scheduling-conflicts.md)** —— ⚠️ 不是重启条件到了，是**当初推错了东西**：主冲突不是「两个班表互撞」（确实罕见），是 `Shift` × `Participation`（有班表的人报名了落在他班次里的活动），而那是 Phase D 自己造出来的。D39 收的是 `contact` 不是 `assignment`，所以这一行说的那一类**自动被覆盖** | — |
| 「总投入工时」这一个数 | ⚠️ **两个账本会重叠，加出来就是错的**（[D36 ⭐](decisions/D36-two-hour-ledgers.md)）。有人一定会问 | ⚠️ **永远不做**，除非先想清楚重叠怎么去。⭐ 注意「**志愿服务小时数**」不在此列 —— 它有定义、可以印，见 [D38 第七节](decisions/D38-served-as-volunteer-or-work.md) |

---
