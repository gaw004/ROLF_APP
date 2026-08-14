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

#: Why a photo in a batch did not go up, and how to say so — in the order the
#: reasons are read out.
#:
#: ⭐ **Every one of these skips one photo and keeps the rest** (2026-08-13).
#:    Before that only `repeats` did; the other three refused the whole
#:    submission, so one 12 MB photo in a pick of ten put none of the ten on the
#:    wall. And because a rejected POST empties the file input, it also meant
#:    choosing all ten again — the failure cost more than the mistake did.
#:
#: ⚠️ The order is the order somebody should act on them: the two they can do
#:    something about (shrink it, convert it) come before the two that are
#:    already fine (it is up there / send the rest in a second batch).
#:
#: ⚠️ A table rather than four `if`s spread over two modules. The form fills
#:    these lists, the view prints them, and the wording lives in neither —
#:    `describe_skipped()` below is the only place a reason is put into English,
#:    so the "why" in a success message and the "why" in an error cannot drift.
SKIP_REASONS = {
    "too_big": "over {limit_mb} MB",
    "unreadable": "not usable images",
    "repeats": "already on the wall",
    "past_cap": f"past the {MAX_PHOTOS_PER_UPLOAD}-at-once limit",
}


def describe_skipped(skipped):
    """"over 10 MB: a.jpg; already on the wall: b.jpg" — or "" if none.

    ⚠️ **Names every file**, and that is the point of the whole feature rather
       than a nicety. A pick of ten that quietly becomes eight is the shape of
       problem where somebody re-uploads the missing two, watches nothing
       happen, and concludes the page is broken — when it did exactly what it
       was asked to. Counting them without naming them is the same trap one step
       along: "skipped 2" does not tell you *which* two to go and fix.
    """
    limit_mb = settings.EVENT_IMAGE_MAX_UPLOAD_BYTES // (1024 * 1024)
    parts = [
        f"{phrase.format(limit_mb=limit_mb)}: {', '.join(skipped[key])}"
        for key, phrase in SKIP_REASONS.items()
        if skipped.get(key)
    ]
    return "; ".join(parts)


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
    # ⚠️ `help_text` is filled in by `__init__`, not here. Reading a setting at
    #    class-definition time freezes it at import, so the sentence would keep
    #    quoting whatever the limit was when the process started — invisible in
    #    production, where it never changes, and wrong under `override_settings`
    #    the moment a test asks what the form tells people.
    images = MultipleFileField(label="Photos")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # Filled in by clean_images() so the view does not re-encode a second
        # time to find out what it is about to save.
        self.prepared = []
        # ⚠️ Set up here as well as there, so that reading it is safe on a form
        #    whose `clean_images()` never ran — an unbound form, or one that
        #    failed on the ministry field. The view asks for it right after
        #    `is_valid()`, and an AttributeError on the success path would be a
        #    500 on the one page this whole change exists to make friendlier.
        self.skipped = {key: [] for key in SKIP_REASONS}
        administered = ministry_ids_administered_by(user)
        field = self.fields["ministry"]
        # `accept` is a semantic attribute rather than styling — it tells the
        # file picker what to offer. It is a convenience and never a check;
        # Pillow opening the file is the check.
        self.fields["images"].widget.attrs["accept"] = "image/*"
        # ⚠️ The last sentence states the behaviour **before** it happens, not
        #    only in the message afterwards. Somebody picking twelve holiday
        #    photos should know in advance that the two extra and the huge one
        #    will be left behind; otherwise the first they hear of it is a
        #    number in a green bar they have already clicked past.
        limit_mb = settings.EVENT_IMAGE_MAX_UPLOAD_BYTES // (1024 * 1024)
        self.fields["images"].help_text = (
            f"Up to {MAX_PHOTOS_PER_UPLOAD} at once, each under {limit_mb} MB. "
            f"JPEG, PNG and WebP; SVG and PDF do not work. Anything too big, "
            f"unreadable or already on the wall is skipped and named — the rest "
            f"still go up."
        )

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
        """Take everything that can go up, and name what could not.

        Everything expensive happens here rather than in the view, so that a
        photo which is going to be refused is refused before anything is
        written.

        ⭐ **One unusable file no longer stops the other nine** (2026-08-13).
           Every reason a photo cannot be used — too big, unreadable, already on
           the wall, past the ten-at-once cap — drops that one photo and keeps
           the rest; the message afterwards names what was dropped and why. Only
           a batch where **nothing** survives is an error.

           What made it worth changing: a rejected POST empties the file input,
           so one 12 MB photo in a pick of ten meant choosing all ten again. The
           repeats path already worked this way and was the model for the rest.

        ⚠️ Skipping is **not** "decode it and throw it away". The size check
           stays in front of `normalise_gallery_image`, because decoding is
           where the memory goes: a 6000×4000 photograph is 72 MB of pixels out
           of a 3 MB file, and that is what took production down earlier the
           same day. An oversized file is never opened.

        ⚠️ The size check is about **storage and bandwidth, not safety**.
           Django's `ImageField.to_python()` has already opened the file with
           Pillow by the time this runs, so what stops a decompression bomb is
           Pillow's own MAX_IMAGE_PIXELS. That correction was paid for once
           already over in events/forms.py; the full note is there.
        """
        from .services import digest_of, normalise_gallery_image, source_digest_of

        uploads = [f for f in self.cleaned_data.get("images") or [] if f]
        if not uploads:
            raise forms.ValidationError("Choose at least one photo.")

        skipped = {key: [] for key in SKIP_REASONS}

        # ⚠️ Trimmed to the cap **before** the loop rather than filtered down to
        #    ten survivors afterwards. The cap is there to bound the work in one
        #    request — at most ten decodes and ten re-encodes — and "keep going
        #    until ten of them are good" would put no bound on it at all: a pick
        #    of two hundred unreadable files would open two hundred files.
        skipped["past_cap"] = [f.name for f in uploads[MAX_PHOTOS_PER_UPLOAD:]]
        uploads = uploads[:MAX_PHOTOS_PER_UPLOAD]

        limit = settings.EVENT_IMAGE_MAX_UPLOAD_BYTES
        prepared, digests, source_digests = [], set(), set()
        for upload in uploads:
            if upload.size > limit:
                skipped["too_big"].append(upload.name)
                continue
            # ⭐ **The same file again is caught here, before it is decoded**
            #    (2026-08-13). This is the cheap check and the common case —
            #    somebody picks the same folder twice, or re-sends a photo they
            #    already sent — and catching it up here means the most likely
            #    duplicate never costs a decode at all.
            #
            # ⚠️ It is also the half of duplicate detection that **survives a
            #    change to the pipeline**, which is the whole reason the column
            #    exists; see GalleryPhoto.source_digest. The `image_digest`
            #    check further down is the other half and catches what this one
            #    cannot: the same photograph re-saved as a different format.
            #    Neither replaces the other.
            source_digest = source_digest_of(upload)
            if source_digest in source_digests or GalleryPhoto.objects.filter(
                    source_digest=source_digest).exists():
                skipped["repeats"].append(upload.name)
                continue
            try:
                image, thumb, taken_year = normalise_gallery_image(upload)
            except forms.ValidationError:
                # ⚠️ Caught, not propagated — and this is the only thing that may
                #    be swallowed here. `normalise_gallery_image` raises
                #    ValidationError for one situation, a file Pillow cannot
                #    open, and re-raises everything else as itself. A bare
                #    `except Exception` would turn a genuine fault into "that
                #    photo was skipped" and nobody would ever hear about it.
                skipped["unreadable"].append(upload.name)
                continue
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
                skipped["repeats"].append(upload.name)
                continue
            digests.add(digest)
            source_digests.add(source_digest)
            prepared.append({
                "image": image, "thumb": thumb,
                "taken_year": taken_year, "image_digest": digest,
                "source_digest": source_digest,
            })

        if not prepared:
            # ⚠️ The same sentence the success message uses, out of the same
            #    function. Two ways of saying "here is why these were skipped"
            #    are two that drift apart, and the one nobody re-reads is this
            #    branch.
            raise forms.ValidationError(
                f"Nothing could be added — {describe_skipped(skipped)}.")

        self.prepared = prepared
        self.skipped = skipped
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
