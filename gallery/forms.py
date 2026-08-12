"""The one form: putting photos on the wall, up to ten at a time."""

from django import forms
from django.conf import settings

from org.models import Ministry
from org.permissions import in_foundation_tier, ministry_ids_administered_by

from .models import GalleryPhoto

#: How many photos one submission may carry.
#:
#: ⚠️ A cap on the **request**, not on the collection. Ten phone photos is
#:    30–80 MB arriving in one POST and ten re-encodes done before the response
#:    goes out; a hundred would time out the request and leave the admin looking
#:    at a browser error with no idea which ones went in. Somebody with two
#:    hundred photos does twenty submissions, which is slower for them and is
#:    the trade being made deliberately.
MAX_PHOTOS_PER_UPLOAD = 10


class MultipleFileInput(forms.ClearableFileInput):
    """The widget half of "pick more than one file".

    ⚠️ Django ships no multi-file field, and this two-class pair is the recipe
       from its own documentation rather than something invented here. The
       widget's `allow_multiple_selected` is what makes the browser offer more
       than one file; without it Django raises rather than silently taking one.
    """

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """The field half. Cleans every file, and returns a list.

    ⚠️ `clean()` is given a **list** by the widget above, and the base class
       knows nothing about that — hence the loop. Skipping it would validate the
       first file and store all of them, which is exactly the sort of gap that
       shows up as a corrupt row months later.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(item, initial) for item in data]
        return [single(data, initial)]


class GalleryPhotoForm(forms.Form):
    """Upload up to ten photos at once, all for one ministry.

    ⚠️ A plain Form, not a ModelForm (changed 2026-08-07 when this went
       multi-file). One submission makes N rows, and a ModelForm is built around
       making exactly one — keeping it would have meant a `save()` that ignores
       `self.instance`, which is a lie about what the class is.

    ⚠️ The ministry dropdown lists only the ones this person may use, but that
       is there to stop a slip rather than to stop an attack — a POST can name
       any id, or none. The view asks can_upload_gallery_photo() about the
       submitted value as well. Two different jobs, both needed; same split as
       EventForm.
    """

    ministry = forms.ModelChoiceField(
        queryset=Ministry.objects.none(), required=False, label="Ministry")
    images = MultipleFileField(
        label="Photos",
        help_text=f"Up to {MAX_PHOTOS_PER_UPLOAD} at once. "
                  f"JPEG, PNG and WebP; SVG and PDF do not work.",
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # Filled in by clean_images() so the view does not re-encode a second
        # time to find out what it is about to save.
        self.prepared = []
        administered = ministry_ids_administered_by(user)
        field = self.fields["ministry"]
        # `accept` is a semantic attribute rather than styling — it tells the
        # file picker what to offer. It is a convenience and never a check;
        # Pillow opening the file is the check.
        self.fields["images"].widget.attrs["accept"] = "image/*"

        if in_foundation_tier(user):
            # The foundation tier may attribute photos to any live ministry, or
            # to nobody — that last one is what "foundation-wide" is.
            field.queryset = Ministry.objects.filter(is_active=True)
            field.required = False
            field.empty_label = "River of Life Foundation"
            field.help_text = ("Leave this as the foundation for photos that "
                               "belong to no one ministry.")
        else:
            # ⚠️ Required for a ministry admin, and the blank option is taken
            #    away rather than merely unselected. Left in, an admin who
            #    submitted without touching it would be publishing a
            #    foundation-wide photo — something they are not allowed to do,
            #    arrived at by doing nothing.
            field.queryset = Ministry.objects.filter(
                id__in=administered, is_active=True)
            field.required = True
            field.empty_label = None

    def clean_images(self):
        """Count them, size them, re-encode them, and refuse the repeats.

        Everything expensive happens here rather than in the view, so that a
        submission which is going to be refused is refused before anything is
        written.

        ⚠️ The size check is about **storage and bandwidth, not safety**.
           Django's `ImageField.to_python()` has already opened the file with
           Pillow by the time this runs, so what stops a decompression bomb is
           Pillow's own MAX_IMAGE_PIXELS. That correction was paid for once
           already over in events/forms.py; the full note is there.
        """
        from .services import digest_of, normalise_gallery_image

        uploads = [f for f in self.cleaned_data.get("images") or [] if f]
        if not uploads:
            raise forms.ValidationError("Choose at least one photo.")
        if len(uploads) > MAX_PHOTOS_PER_UPLOAD:
            raise forms.ValidationError(
                f"That is {len(uploads)} photos. "
                f"{MAX_PHOTOS_PER_UPLOAD} at a time is the limit — the rest "
                f"can go in a second batch.")

        limit = settings.EVENT_IMAGE_MAX_UPLOAD_BYTES
        prepared, digests, repeats = [], set(), []
        for upload in uploads:
            if upload.size > limit:
                raise forms.ValidationError(
                    f"“{upload.name}” is larger than "
                    f"{limit // (1024 * 1024)} MB. Most phone photos are well "
                    f"under it.")
            image, thumb, taken_year = normalise_gallery_image(upload)
            digest = digest_of(image)
            # ⚠️ Two checks, not one. `digests` catches the same photo twice in
            #    **this** submission — which is what happens when somebody
            #    ctrl-clicks a file twice in the picker — and the query catches
            #    one that is already on the wall. Only the second is enforced by
            #    the unique column, and relying on that alone turns a duplicate
            #    into an IntegrityError 500 rather than a sentence under the
            #    field.
            if digest in digests or GalleryPhoto.objects.filter(
                    image_digest=digest).exists():
                repeats.append(upload.name)
                continue
            digests.add(digest)
            prepared.append({
                "image": image, "thumb": thumb,
                "taken_year": taken_year, "image_digest": digest,
            })

        if repeats and not prepared:
            raise forms.ValidationError(
                f"Already on the wall: {', '.join(repeats)}. Nothing to add.")

        self.prepared = prepared
        self.repeats = repeats
        return uploads

    def save(self):
        """Create one row per accepted photo. Returns them.

        ⚠️ The re-encode already happened in clean_images(); this only writes.
           The other order means a submission of ten photos whose ninth is a PDF
           has already stored eight of them before it finds out.

        ⚠️ A loop rather than `bulk_create`. bulk_create does not call `save()`
           on the file fields, so nothing would ever be written to storage —
           the rows would land with file names pointing at objects that do not
           exist, and the wall would render broken images. This is the one place
           in the project where the usual "prefer bulk_create" advice is wrong.
        """
        ministry = self.cleaned_data.get("ministry")
        created = []
        for fields in self.prepared:
            created.append(GalleryPhoto.objects.create(
                ministry=ministry, uploaded_by=self.user, **fields))
        return created
