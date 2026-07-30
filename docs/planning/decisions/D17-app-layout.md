# D17 · app 划分：一个 app 一个业务领域，敏感数据单独成 app（2026-07-28）

> 本文件是 `../goal.md` 拆出来的一条决策记录（2026-07-30 拆分，内容一字未改）。
> **`goal.md` 仍是唯一入口**：决策一览表和「去哪找」都在那里，
> 代码注释里写的 `goal.md D17` 指的就是本文件。

> Phase A 已经为这件事付过一次学费（`TimeStampedModel` 从 `contact` 搬到 `core`）。
> Phase B 一次加 6 个模型，同一个问题会再来一遍，而模型跨 app 搬家的痛苦程度
> 和换 `AUTH_USER_MODEL` 是一个量级 —— 正好符合"现在改成本≈0，以后改很痛"。

```
core        TimeStampedModel、core/timeutils.py、共享的 .active() QuerySet mixin
            core/notifications/  投递适配器（D22 —— 不是表，唯一和外部服务打交道的地方）
contact     Contact / RelationshipType / Relationship / Language / EmergencyContact
accounts    User
org         Ministry / Position / Assignment / EmploymentType / MinistryRole
events      EventType / Event / EventRole / ParticipationRole / Participation
            EventNotification
volunteer   （已推迟）VolunteerProfile / BackgroundCheck
finance     （Phase D 预留）Contribution / FinancialType / PaymentMethod
payroll     （更靠后，薪酬真要入库时）薪酬
```

> **2026-07-29 晚补齐**：本清单曾漏掉 `EventRole` / `EventNotification` / `MinistryRole` /
> `EmergencyContact` / `core/notifications/`，而它自称是"新模型放哪"的唯一依据；
> `finance` / `payroll` 的 Phase 编号也没跟着 C / D 对调改。

几个取舍：

- **`org` 而不是 `hr` / `staff`** —— 它装的是 ministry + **所有** kind 的编制与任职
  （员工、志愿者、理事），不只是员工。名字取 `org` 而不是 `positions`，
  是因为这个 app 的主题是**组织结构本身**，`Position` 只是它的骨架。
- **`Guardianship` 将来放 `contact`** —— 它是 Contact ↔ Contact 的关系，
  和 `Relationship` 同层，且未来非志愿者场景也会用到。
- `payroll` 必须是独立 app，不能塞进 `org` 或 `finance`。
  薪酬是本系统里敏感度最高的数据（D11 把它排除在 MVP 之外就是这个原因）。
  独立成 app，将来可以整个 app 级别地做权限隔离 ——
  Django 的权限是按 `app_label.model` 授予的，一个 Group 直接不给 `payroll.*`，
  比逐个字段配权限简单得多，也更不容易配漏。这是现在就要占的位，不是以后再拆。
- **依赖方向**：`INSTALLED_APPS` 按
  `core` → `contact` → **`accounts`** → `org` → `events` → `volunteer` → `finance`
  的顺序列，读的时候依赖方向一目了然。谁也不许反向 import。
  > **2026-07-29 晚补上 `accounts`** —— 它原来在上面的清单里、却不在这条依赖链里，
  > 而它恰好是链中间最容易搞反的一环：`accounts.User` → `contact.Contact`（D12），
  > 而 `org.MinistryRole.granted_by` → `accounts.User`（D20）、
  > `events.EventNotification.sent_by` → `accounts.User`（D22）。
  > **`accounts` 只依赖 `contact`，`org` / `events` 可以依赖 `accounts`，反向绝对不行** ——
  > 一旦 `accounts` 去 import `org`（比如想在 `User` 上加一个 `is_ministry_admin` 便利属性），
  > 就是循环依赖，而且那个属性本来就该在 `org/permissions.py` 里。
