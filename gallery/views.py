"""Two pages: the wall, and the page the admins put photos on it from.

Same three rules as events/views.py — visibility decided in the query, the
permission check first and delegated to org.permissions, no arithmetic here.
The layout arithmetic for the wall is all in services.strips_for.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from org.permissions import (
    can_delete_gallery_photo,
    can_reach_gallery_manage,
    can_upload_gallery_photo,
    in_foundation_tier,
    ministry_ids_administered_by,
)

from .forms import GalleryPhotoForm, describe_skipped
from .models import GalleryPhoto
from .services import (
    MAX_PHOTOS_PER_REMOVAL,
    remove_photo,
    repeats_for,
    resolve_removal,
    strips_for,
)

#: The one sentence both photo-admin pages refuse with. ⚠️ Written once: two
#: doors onto the same room that disagree about why it is locked is how a
#: refusal starts sounding like a bug.
NOT_A_PHOTO_ADMIN = ("Photos on the Memories wall are put there by a ministry's "
                     "admins and by the foundation.")


def _strip(photos, position):
    """One moving strip, as the template needs it.

    ⚠️ `span` — the strip's total width in strip-height units — is computed here
       because only the server knows it. The stylesheet turns it into the
       animation's duration, which is what keeps the drift at one speed whether
       a strip holds 6 photos or 20: a fixed duration would make a fuller strip
       move faster, so the page would get more hurried every time the foundation
       added photographs.

    ⚠️ `repeats` is what keeps a short strip from showing a hole. See
       services.MIN_STRIP_SPAN — this is the case a new foundation is in.

    ⚠️ `position` exists only to stagger the three strips' starting phase. All
       of them drift left at the same speed, so without it every strip begins
       with a photo flush to the left edge and the three read as one block
       sliding rather than as three strips. The stylesheet turns it into a
       negative `animation-delay`, which starts an animation part-way through
       rather than delaying it.
    """
    span = sum(p.width_ratio for p in photos)
    return {
        "photos": photos,
        "span": span,
        "repeats": range(repeats_for(span)),
        "position": position,
    }


@login_required
def wall(request):
    """The Memories page: one strip of equal-height photos, drifting left.

    ⚠️ `@login_required` is half of the gate and the smaller half. The other
       half is that the photos live in a private bucket and are served through
       signed URLs (config/settings/prod.py) — without that, this decorator
       would be protecting the *page* while every photo on it stayed a public,
       permanent link. Decided 2026-08-06: these are pictures of volunteers,
       some of them minors.
    """
    strips = strips_for()
    return render(request, "gallery/wall.html", {
        "strips": [_strip(photos, n) for n, photos in enumerate(strips)],
        # The sequence the lightbox arrows walk — the whole page, top strip
        # first and left to right within each.
        #
        # ⚠️ The **large** image's URL, which appears nowhere else on the page —
        #    the strips carry thumbnails. In production both are signed and
        #    temporary (see config/settings/prod.py), so this block is the one
        #    place the full-size links live, and it is why the lightbox opens
        #    without a round trip.
        "sequence": [
            {"src": item.photo.image.url, "caption": item.photo.caption}
            for photos in strips for item in photos
        ],
    })


def _added_message(added, skipped):
    """What to say after a batch upload.

    ⚠️ The skipped ones are named, and the wording comes from
       `forms.describe_skipped` rather than from here. That function also writes
       the "nothing could be added" error, and those two sentences answer the
       same question — a second phrasing in this module is a second thing to
       keep in step, with the error branch being the one nobody re-reads.

    ⚠️ Counted **and** named. "Skipped 2" leaves somebody hunting through their
       own file picker for which two; the count is only there so the number
       added and the number missing are both in the first clause.
    """
    said = f"Added {len(added)} to Memories."
    dropped = sum(len(names) for names in skipped.values())
    if dropped:
        said += f" Skipped {dropped} — {describe_skipped(skipped)}."
    return said


@login_required
def manage(request):
    """Upload a photo, and see the ones you may take down.

    ⚠️ The list is narrowed in the queryset, not in the template. A ministry
       admin is shown their own ministries' photos because those are the only
       rows fetched — not because a template hid the rest, which would still
       have sent them to the browser.
    """
    foundation = in_foundation_tier(request.user)
    administered = ministry_ids_administered_by(request.user)
    if not foundation and not administered:
        raise PermissionDenied(NOT_A_PHOTO_ADMIN)

    if request.method == "POST":
        form = GalleryPhotoForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            # ⚠️ Asked about the **submitted** ministry, not about the dropdown
            #    the form drew. The dropdown stops a slip; this stops a POST.
            if not can_upload_gallery_photo(request.user, form.cleaned_data.get("ministry")):
                raise PermissionDenied(
                    "A photo with no ministry speaks for the whole foundation, "
                    "and only the foundation tier may publish one.")
            added = form.save()
            messages.success(request, _added_message(added, form.skipped))
            return redirect("gallery:manage")
    else:
        form = GalleryPhotoForm(user=request.user)

    photos = GalleryPhoto.objects.on_the_wall()
    if not foundation:
        photos = photos.filter(ministry_id__in=administered)

    return render(request, "gallery/manage.html", {
        "form": form,
        "photos": photos,
        # ⚠️ Handed to the template so the page can stop somebody ticking a
        #    61st box, rather than letting them pick 200 and refusing the lot
        #    afterwards. The number is **not** written in the template — one
        #    source of truth, same rule as MAX_PHOTOS_PER_UPLOAD's help text.
        "removal_cap": MAX_PHOTOS_PER_REMOVAL,
    })


@login_required
def delete(request, pk):
    """Take one photo off the wall. POST only.

    ⚠️ POST, never GET. A link that deletes on GET is followed by every
       link-prefetcher and every crawler that ever sees the page — and these
       files are the one thing here that no backup can bring back.
    """
    photo = get_object_or_404(GalleryPhoto, pk=pk)
    if not can_delete_gallery_photo(request.user, photo):
        raise PermissionDenied(
            "This photo belongs to another ministry.")
    if request.method != "POST":
        return redirect("gallery:manage")

    # ⚠️ The files go with the row, and that is `services.remove_photo`'s job
    #    rather than four lines here — the batch path below has to do exactly
    #    the same thing, and a second copy of it is where the thumbnail gets
    #    forgotten.
    caption = remove_photo(photo)
    messages.success(request, f"Removed from Memories — {caption}.")
    return redirect("gallery:manage")


def _skipped_note(removal):
    """What to add about the ids that will not be acted on. "" when there are none.

    ⚠️ Counts, no names. The two reasons are told apart because they mean
       different things to the person reading — one is "somebody beat you to
       it", the other is "that was never yours" — but neither says *which*
       photo, because an id that came back named would make this form into a
       way of asking whether photo 412 exists and whose it is.
    """
    parts = []
    if removal.missing:
        parts.append(f"{removal.missing} already gone")
    if removal.forbidden:
        parts.append(f"{removal.forbidden} not yours to take down")
    return f" Left alone: {', '.join(parts)}." if parts else ""


@login_required
def remove_selected(request):
    """Take several photos off the wall — confirmation page, then the deletion.

    Two POSTs, and the first one deletes nothing. It renders the photographs
    that are actually about to go, at a size somebody can recognise them at.

    ⚠️ **The second POST re-resolves from the ids rather than trusting the page
       it came from.** The confirmation page is a picture of a decision, not the
       authority to carry it out: it holds no token, no signature and no
       server-side state, so a stale one — rendered before a role was revoked,
       or before somebody else deleted half of it — is simply resolved again and
       comes out smaller. This is the whole reason it is safe to put ids in
       hidden inputs.

    ⚠️ POST for **both** steps, including the one that only shows the list. A
       GET that carried the ids would be a URL somebody could bookmark, share,
       or have prefetched — and the page it renders has a one-click delete on
       it.
    """
    if not can_reach_gallery_manage(request.user):
        # ⚠️ The gate is here as well as on `manage`, even though a volunteer
        #    would get an empty removal anyway. "It happens to resolve to
        #    nothing" is not a refusal — it is a refusal by accident, one
        #    permission change away from not being one.
        raise PermissionDenied(NOT_A_PHOTO_ADMIN)
    if request.method != "POST":
        return redirect("gallery:manage")

    removal = resolve_removal(request.user, request.POST.getlist("photo"))

    if removal.over_cap:
        messages.error(request, (
            f"That is more than {MAX_PHOTOS_PER_REMOVAL} photos at once, so "
            f"nothing was removed — send them in smaller batches."))
        return redirect("gallery:manage")

    if not removal.photos:
        # ⚠️ Not an error page. Ticking nothing is a slip, and everything else
        #    that lands here (ids that are gone, ids that are not theirs) is
        #    already accounted for in the note.
        messages.error(
            request,
            "Nothing was removed — no photos you can take down were selected."
            + _skipped_note(removal))
        return redirect("gallery:manage")

    if not request.POST.get("confirmed"):
        return render(request, "gallery/remove_selected.html", {
            "removal": removal,
            "skipped_note": _skipped_note(removal),
        })

    for photo in removal.photos:
        remove_photo(photo)
    messages.success(
        request,
        f"Removed {len(removal.photos)} from Memories. The files are gone."
        + _skipped_note(removal))
    return redirect("gallery:manage")
