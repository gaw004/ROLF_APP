"""Admin pieces shared by more than one app.

Disposable configuration, like every admin.py (goal.md D18) — it is here rather
than in one of the apps only because two apps now want the same filter, and a
copy of it in each would be a copy of a business rule.
"""

from django.contrib import admin


class InEffectFilter(admin.SimpleListFilter):
    """In effect / not, for any model whose manager has .active().

    Used by contact.Relationship and org.Assignment. Every branch is one call
    to a QuerySet method: no date arithmetic here, so the definition of "in
    effect" stays in core/querysets.py where Phase C reads it too (D18).

    list_filter = ["is_active"] is exactly what made that old field dangerous —
    it could be filtered independently of the dates and would confidently
    return the wrong rows.
    """

    title = "生效中"
    parameter_name = "in_effect"

    def lookups(self, request, model_admin):
        return [("yes", "生效中"), ("no", "已结束或未开始")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.active()
        if self.value() == "no":
            return queryset.exclude(pk__in=queryset.model.objects.active())
        return queryset
