"""Admin pieces shared by more than one app.

Disposable configuration, like every admin.py (goal.md D18) — it is here rather
than in one of the apps only because two apps now want the same filter, and a
copy of it in each would be a copy of a business rule.
"""

from django import forms
from django.conf import settings
from django.contrib import admin
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse

from .images import is_new_upload, too_many_pixels
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


class HomePageForm(forms.ModelForm):
    """Refuses a picture with more pixels in it than the instance can decode.

    🔴 **The front page is the one upload with no re-encoding step**, so until
       2026-09-01 nothing between the file picker and the bucket ever looked at
       how large the picture was in pixels. The byte limit the other two uploads
       carry does not answer that question: a 0.25 MB PNG can hold 81
       megapixels, which is 243 MB decoded on a 512 MB instance. It would not
       have raised — it would have taken the site down while somebody changed
       the front page.

    ⚠️ **The outer of two guards, not the only one.** `core.renditions.
       render_ladder` refuses the same picture on its own, which is what covers
       the shell and `rebuild_hero_renditions`. This one exists because a
       complaint belongs next to the field somebody just used — `save()` has no
       field to put one next to, and an admin who gets a traceback instead of a
       sentence has learned nothing about what to do differently.

    ⚠️ Nothing else is validated here and nothing is re-encoded. Stripping the
       camera's metadata happens in `HomePage.save()` instead, because it has to
       apply to the shell and to `rebuild_hero_renditions` as well as to this
       screen.
    """

    class Meta:
        model = HomePage
        fields = "__all__"

    def clean_hero_image(self):
        uploaded = self.cleaned_data.get("hero_image")
        # An unchanged field hands back the stored FieldFile, which is already
        # in the bucket and has already been through this.
        if not is_new_upload(uploaded):
            return uploaded
        if too_many_pixels(uploaded):
            raise forms.ValidationError(
                f"That picture is larger than "
                f"{settings.IMAGE_MAX_PIXELS // 1_000_000} megapixels. Its file "
                f"size is fine — it is the number of pixels in it, and decoding "
                f"one that large is what the server cannot afford. Scaling it "
                f"down before uploading will fix it.")
        return uploaded


@admin.register(HomePage)
class HomePageAdmin(admin.ModelAdmin):
    """The one row, edited in place. No list to choose from, no add, no delete.

    ⚠️ `has_add_permission` returns False and the changelist redirects straight
       into the single object. A model with one row still gets Django's full
       list-add-delete furniture otherwise, and every piece of it is a way to
       end up with zero rows or two.
    """

    form = HomePageForm

    fieldsets = [
        ("Background", {
            "fields": ["hero_video", "hero_image"],
            "description": "A video is used instead of the image when both are "
                           "set. It plays muted and looped — browsers refuse to "
                           "autoplay sound.",
        }),
        ("Framing", {
            "fields": ["focus_picker", "hero_focus_x", "hero_focus_y"],
            "description": "The picture fills the whole screen, so some of it is "
                           "always cut off — and how much differs between a "
                           "phone and a laptop. Click the part that has to stay "
                           "visible, or drag the ring onto it.",
        }),
        ("Words over the picture", {
            "fields": ["verse_text", "verse_reference"],
        }),
    ]

    readonly_fields = ["focus_picker"]

    class Media:
        # Collected as plain files, like the two contact-admin scripts. ⚠️ Not
        # part of assets/ — that bundle is built by CI and downloaded by every
        # visitor, and this is one staff-only screen.
        js = ["core/admin/hero_focus_picker.js"]
        css = {"all": ["core/admin/hero_focus_picker.css"]}

    @admin.display(description="Point to keep")
    def focus_picker(self, obj):
        """The picture, whole, with the focal point drawn on it.

        ⚠️ Markup lives in a template, not in this method. admin.py is
           disposable configuration (D18) and a block of HTML built with string
           formatting here would be neither reviewable nor replaceable with the
           admin it is attached to.

        ⚠️ It renders **nothing that is submitted**. The two number fields are
           the form; this is a way of typing into them. So the framing is still
           editable when the script does not run, which is the whole reason the
           numbers were not simply replaced.

        ⚠️ These two URLs are what is **saved**, and the widget is rendered even
           when both are empty (2026-08-13). Emptiness is a `hidden` attribute
           in the template rather than an absent element, because the script
           puts the file somebody has *just chosen* into it — before this, the
           framing tool did not exist in the document until the upload had been
           saved, so choosing a picture and scrolling down to frame it found the
           sentence "add a picture and save" instead. Framing then took two
           trips through the form.
        """
        return render_to_string("admin/core/homepage/focus_picker.html", {
            "image_url": obj.hero_image.url if obj.hero_image else "",
            "video_url": obj.hero_video.url if obj.hero_video else "",
            "focus_x": obj.hero_focus_x,
            "focus_y": obj.hero_focus_y,
        })

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # Straight to the only object there is.
        return redirect(reverse("admin:core_homepage_change",
                                args=[HomePage.load().pk]))
