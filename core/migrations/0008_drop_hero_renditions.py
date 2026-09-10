"""The front page's srcset ladder, removed. Five columns, all derived.

The ladder cut three WebP renditions from the hero picture on every change of
it, inside the request that changed it. Measured 2026-09-09 on the real code
path: 105–191 MB of peak memory for one change, on a 512 MB instance already
holding two workers at ~100 MB apiece — and on that day it took the instance
down while somebody was changing the picture. Without the ladder the same
operation is 6–75 MB. The full accounting, and why removing it costs no screen
a single pixel, is in revisions.md 六十一.

🔴 **Nothing here is recoverable and nothing here needs to be.** Every one of
   these five columns was derived from `hero_image`, which is untouched. The
   three WebP files they pointed at are still in the public bucket and are now
   orphans by definition — `core.services.orphaned_home_media` reports them and
   `manage.py purge_orphaned_home_media` deletes them once it is told twice.
   Run it after this is deployed; there is no rush and no other cleanup.

⚠️ **This drop is not backwards compatible with the code before it**, and on
   Render that matters for a minute or two. `preDeployCommand` runs `migrate`
   while the previous release is still serving, and that release selects these
   columns on every page (`core.context_processors` asks for the front page's
   picture on all of them) — so between the migration and the traffic switch, a
   visitor gets an error page. `/healthz` touches no database, so the platform
   will not notice and the deploy will report success. Accepted knowingly by
   the person who asked for the change, on 2026-09-09, over the alternative of
   splitting it across two deploys. If a future column drop here is on a busier
   day, split it: stop reading the column in one release, drop it in the next.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_homepage_hero_image_1280_homepage_hero_image_1920_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='homepage',
            name='hero_image_1280',
        ),
        migrations.RemoveField(
            model_name='homepage',
            name='hero_image_1920',
        ),
        migrations.RemoveField(
            model_name='homepage',
            name='hero_image_2560',
        ),
        migrations.RemoveField(
            model_name='homepage',
            name='hero_image_height',
        ),
        migrations.RemoveField(
            model_name='homepage',
            name='hero_image_width',
        ),
    ]
