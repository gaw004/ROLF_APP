"""Admin pieces shared by more than one app.

Disposable configuration, like every admin.py (goal.md D18) — it is here rather
than in one of the apps only because two apps now want the same filter, and a
copy of it in each would be a copy of a business rule.
"""

from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse

from .models import HomePage


class InEffectFilter(admin.SimpleListFilter):
    """In effect / not, for any model whose manager has .active().

    Used by org.Assignment and org.MinistryRole. Every branch is one call to a
    QuerySet method: no date arithmetic here, so the definition of "in effect"
    stays in core/querysets.py where Phase C reads it too (D18).

    list_filter = ["is_active"] is exactly what made a boolean-beside-the-dates
    dangerous — it could be filtered independently of the dates and would
    confidently return the wrong rows.
    """

    title = "In effect"
    parameter_name = "in_effect"

    def lookups(self, request, model_admin):
        return [("yes", "In effect"), ("no", "Ended or not started")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.active()
        if self.value() == "no":
            return queryset.exclude(pk__in=queryset.model.objects.active())
        return queryset


@admin.register(HomePage)
class HomePageAdmin(admin.ModelAdmin):
    """The one row, edited in place. No list to choose from, no add, no delete.

    ⚠️ `has_add_permission` returns False and the changelist redirects straight
       into the single object. A model with one row still gets Django's full
       list-add-delete furniture otherwise, and every piece of it is a way to
       end up with zero rows or two.
    """

    fieldsets = [
        ("Background", {
            "fields": ["hero_video", "hero_image"],
            "description": "A video is used instead of the image when both are "
                           "set. It plays muted and looped — browsers refuse to "
                           "autoplay sound.",
        }),
        ("Framing", {
            "fields": ["hero_focus_x", "hero_focus_y"],
            "description": "The picture fills the whole screen, so some of it is "
                           "always cut off — and how much differs between a "
                           "phone and a laptop. These two numbers say which part "
                           "has to stay visible. Left at 50 and 50 the middle is "
                           "kept, which is what a browser does anyway.",
        }),
        ("Words over the picture", {
            "fields": ["verse_text", "verse_reference"],
        }),
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # Straight to the only object there is.
        return redirect(reverse("admin:core_homepage_change",
                                args=[HomePage.load().pk]))
