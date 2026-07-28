"""Seed the Language table with the full ISO 639-3 list from pycountry.

Also pins the three languages ROLF works in most (English, Mandarin, Cantonese)
to the top of the dropdown, and gives a handful of languages the names people
actually search for ("Cantonese" rather than "Yue Chinese").
"""

from django.db import migrations

# code -> pin_rank. Higher sorts first.
PINNED = {
    "eng": 3,
    "cmn": 2,
    "yue": 1,
}

# code -> (display_name override or None, alt names searched but not displayed)
NAME_OVERRIDES = {
    "cmn": ("Mandarin Chinese", "Mandarin, Putonghua, Guoyu, 普通话, 國語, 官話"),
    "yue": ("Cantonese", "Yue Chinese, 粤语, 廣東話"),
    "zho": (None, "Zhongwen, 中文, Chinese macrolanguage"),
    "hat": ("Haitian Creole", "Haitian, Kreyol, Kreyòl ayisyen"),
    "tgl": (None, "Filipino, Pilipino"),
    "fas": (None, "Farsi"),
    "pes": (None, "Farsi, Iranian Persian"),
    "prs": (None, "Afghan Persian, Eastern Farsi"),
    "ksw": (None, "Karen, Sgaw Karen"),
    "mya": (None, "Myanmar"),
    "ase": (None, "ASL, Sign language"),
    "spa": (None, "Castellano, Espanol, Español"),
    "ben": (None, "Bangla"),
}


def seed_languages(apps, schema_editor):
    import pycountry

    Language = apps.get_model("contact", "Language")

    rows = []
    for language in pycountry.languages:
        code = language.alpha_3
        override, alt_names = NAME_OVERRIDES.get(code, (None, ""))
        rows.append(Language(
            code=code,
            name=language.name,
            display_name=override or getattr(language, "common_name", None) or language.name,
            alt_names=alt_names,
            language_type=getattr(language, "type", "L"),
            pin_rank=PINNED.get(code, 0),
        ))

    Language.objects.bulk_create(rows, batch_size=1000, ignore_conflicts=True)


def unseed_languages(apps, schema_editor):
    Language = apps.get_model("contact", "Language")
    Language.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("contact", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_languages, unseed_languages),
    ]
