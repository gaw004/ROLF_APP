"""D51：日期区间收成一条右开规则。events 这一半 —— 只有 `EventGrant`。

两件事：

1. **回填** `start_date IS NULL` —— 用 `created_at` 落在基金会时区的那一天
   （`core.timeutils.local_day()`，D16 的口径）。必须在第 2 步之前。
2. **改列**：`start_date` 不可为空。

⚠️ **`end_date` 一天都不挪**，和 org 那一半的 `Assignment` 相反：授权那一列
   2026-09-15 起（D47 落地那天）就已经按右开读了 —— 撤销填今天 = 今天起失效。
   要挪的只有原来右闭的**事实**表。

⚠️ 回填那一步的反向是 noop，理由同 `org/migrations/0012`。
"""

import core.timeutils
from django.db import migrations, models

from core.timeutils import local_day

NEEDS_A_START = ["EventGrant", "HistoricalEventGrant"]


def fill_missing_start_dates(apps, schema_editor):
    for label in NEEDS_A_START:
        model = apps.get_model("events", label)
        model.objects.filter(start_date__isnull=True).update(
            start_date=local_day("created_at"))


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0032_historicaleventgrant_eventgrant'),
    ]

    operations = [
        migrations.RunPython(fill_missing_start_dates, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='eventgrant',
            name='start_date',
            field=models.DateField(blank=True, default=core.timeutils.local_today),
        ),
        migrations.AlterField(
            model_name='historicaleventgrant',
            name='start_date',
            field=models.DateField(blank=True, default=core.timeutils.local_today),
        ),
    ]
