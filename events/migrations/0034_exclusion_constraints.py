"""D51：`EventGrant` 的重复拦法换成区间不相交。org 那一半在 `org/migrations/0013`。

⚠️ 依赖 `org.0013` 而不是自己再装一遍 `btree_gist` —— 扩展是**每个数据库**
   一份，装两次的第二次是 no-op，但把它写成依赖才说得清顺序。

🔴 这条迁移和「删掉 `grant_event_admin()` 的恢复分支」是同一个 commit：
   只加约束而留着那个分支，它照样会抹掉 `end_date`（一行变一行，约束一声不吭），
   而那一段从未存在过的连续授权就还在。
"""

import core.querysets
import django.contrib.postgres.constraints
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('org', '0013_exclusion_constraints'),
        ('contact', '0009_contact_contact_individual_has_a_first_name'),
        ('events', '0033_half_open_date_ranges'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='eventgrant',
            name='eventgrant_unique_grant',
        ),
        migrations.AddConstraint(
            model_name='eventgrant',
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(condition=models.Q(('end_date__isnull', True), ('end_date__gte', models.F('start_date')), _connector='OR'), expressions=[('contact', '='), ('event', '='), (core.querysets.DateRange(), '&&')], name='eventgrant_no_overlapping_grant', violation_error_code='eventgrant_overlapping_grant', violation_error_message='They already manage this event over part of that period.'),
        ),
    ]
