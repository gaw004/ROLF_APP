"""D51：授权/任职的重复拦法换成**区间不相交**，由数据库说。

三条 `UNIQUE(..., start_date)` 换成三条排他约束（这里两条，`EventGrant` 在
`events/migrations/0034`）。原来那条键在 `start_date` 上，而那一列对这些表是
**偶然量**（表单默认留空），于是它同时过松和过紧 —— 整段病历在 D51 第三节。

⚠️ **`btree_gist` 是前提，不是可选项**：排他约束里那几个 `=` 比的是整数外键，
   而 GiST 默认不认识整数的 `=`。少了它，建约束会直接报
   "data type integer has no default operator class for access method gist"。
   `events` 那一条迁移依赖本条，所以扩展只装一次。

⚠️ 装扩展需要建库者权限。本项目的 Postgres 在 Render 上（`CREATE EXTENSION`
   在 Render 的托管库里是允许的），本地和 CI 都是自己建的库。

🔴 **建约束会在已有数据上失败**，如果现存行里已经有重叠 —— 那正是旧约束
   过松放行的那一种。**写这条迁移时当场撞到了一次**（本地库里两段压着的任职）：

     could not create exclusion constraint "assignment_no_overlapping_tenure"
     DETAIL: Key (contact_id, position_id, daterange(start_date, end_date))
             =(1, 1, [2026-02-01,)) conflicts with ... [2026-01-01,2026-03-16)

   两行的 `start_date` 不同，所以旧的 `UNIQUE(contact, position, start_date)`
   一声不吭地放行了它们。失败是对的：这是一次要人看一眼的数据冲突，
   不是可以自动合并的东西 —— 所以这条迁移**不带数据清理**。
   上线前先跑一遍那条查询找出重叠行，人工定夺。
"""

import core.querysets
import django.contrib.postgres.constraints
from django.conf import settings
from django.contrib.postgres.operations import BtreeGistExtension
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contact', '0009_contact_contact_individual_has_a_first_name'),
        ('org', '0012_half_open_date_ranges'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        BtreeGistExtension(),
        migrations.RemoveConstraint(
            model_name='assignment',
            name='assignment_unique_tenure',
        ),
        migrations.RemoveConstraint(
            model_name='ministryrole',
            name='ministryrole_unique_grant',
        ),
        migrations.AddConstraint(
            model_name='assignment',
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(condition=models.Q(('end_date__isnull', True), ('end_date__gte', models.F('start_date')), _connector='OR'), expressions=[('contact', '='), ('position', '='), (core.querysets.DateRange(), '&&')], name='assignment_no_overlapping_tenure', violation_error_code='assignment_overlapping_tenure', violation_error_message='This person already holds this post over part of that period. End that tenure first — two live tenures on one post count them twice.'),
        ),
        migrations.AddConstraint(
            model_name='ministryrole',
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(condition=models.Q(('end_date__isnull', True), ('end_date__gte', models.F('start_date')), _connector='OR'), expressions=[('contact', '='), ('ministry', '='), ('role', '='), (core.querysets.DateRange(), '&&')], name='ministryrole_no_overlapping_grant', violation_error_code='ministryrole_overlapping_grant', violation_error_message='They already have that role in this ministry over part of that period.'),
        ),
    ]
