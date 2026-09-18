"""D51：日期区间收成一条右开规则。org 这一半。

三件事，顺序不能换：

1. **回填** `start_date IS NULL` —— 用那一行的 `created_at` 落在基金会时区的
   那一天（`core.timeutils.local_day()`，D16 的口径；直接 `::date` 取的是 UTC 的
   那一天，边界日会差一天）。得在第 2 步之前，否则 NOT NULL 加不上去。
2. **改列**：`start_date` 不可为空（D51 / SQL:2011 对 `PERIOD` 起止列的要求）。
3. **`Assignment.end_date` 整体 +1 天** —— 这张表原来是**右闭**的（「做到 15 号」
   存 15 号），改成右开之后同一句话要存 16 号。⚠️ `MinistryRole` **不移**：
   授权那一列 2026-09-15 起就已经按右开读了。

⚠️ **影子表一起动。** 不动的话，翻历史会看到一条「结束日期从 3-15 变成 3-16」的
   假变更 —— 而一份可被改写的审计记录在合规上等于没有，这张表服务的又恰好是
   「谁在什么时候能看未成年人的紧急联系电话」。

⚠️ **回填那一步的反向是 noop**，如实记：哪几行原来是空的，改完就无从知道了。
   反向之后列重新可空，但那些日期会留着。+1 那一步的反向是 -1，那一步是精确的。
"""

import core.timeutils
from django.db import migrations, models

from core.timeutils import local_day

#: 起始日期可能为空的四张表（两张主表 + 各自的影子表）。
NEEDS_A_START = ["Assignment", "MinistryRole",
                 "HistoricalAssignment", "HistoricalMinistryRole"]

#: 原来右闭、需要整体 +1 的那一张（和它的影子表）。授权表不在其中。
WAS_RIGHT_CLOSED = ["Assignment", "HistoricalAssignment"]


def fill_missing_start_dates(apps, schema_editor):
    for label in NEEDS_A_START:
        model = apps.get_model("org", label)
        model.objects.filter(start_date__isnull=True).update(
            start_date=local_day("created_at"))


def shift_end_dates(apps, schema_editor, days):
    """`end_date` 整体挪 `days` 天。空值不动 —— 空的意思是「还没有结束」。

    走 SQL 而不是 `F("end_date") + timedelta`：Postgres 里 `date + interval`
    出来的是 timestamp，而 `date + integer` 出来的就是 date。少一次隐式转换。
    """
    for label in WAS_RIGHT_CLOSED:
        table = apps.get_model("org", label)._meta.db_table
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                f'UPDATE "{table}" SET end_date = end_date + %s '
                f"WHERE end_date IS NOT NULL", [days])


def to_half_open(apps, schema_editor):
    shift_end_dates(apps, schema_editor, 1)


def back_to_right_closed(apps, schema_editor):
    shift_end_dates(apps, schema_editor, -1)


class Migration(migrations.Migration):

    dependencies = [
        ('org', '0011_alter_historicalposition_needs_foundation_review_and_more'),
    ]

    operations = [
        migrations.RunPython(fill_missing_start_dates, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='assignment',
            name='start_date',
            field=models.DateField(blank=True, default=core.timeutils.local_today),
        ),
        migrations.AlterField(
            model_name='historicalassignment',
            name='start_date',
            field=models.DateField(blank=True, default=core.timeutils.local_today),
        ),
        migrations.AlterField(
            model_name='historicalministryrole',
            name='start_date',
            field=models.DateField(blank=True, default=core.timeutils.local_today),
        ),
        migrations.AlterField(
            model_name='ministryrole',
            name='start_date',
            field=models.DateField(blank=True, default=core.timeutils.local_today),
        ),
        migrations.RunPython(to_half_open, back_to_right_closed),
    ]
