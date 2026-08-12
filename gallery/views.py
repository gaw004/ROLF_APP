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
    can_upload_gallery_photo,
    in_foundation_tier,
    ministry_ids_administered_by,
)

from .forms import GalleryPhotoForm
from .models import GalleryPhoto
from .services import repeats_for, strips_for


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


def _added_message(added, repeats):
    """What to say after a batch upload.

    ⚠️ The skipped ones are named. A batch of ten that quietly becomes eight is
       the shape of problem where somebody re-uploads the missing two, watches
       nothing happen, and concludes the page is broken — when in fact it did
       exactly what it was asked to.
    """
    said = f"Added {len(added)} to Memories."
    if repeats:
        said += (f" Skipped {len(repeats)} already on the wall: "
                 f"{', '.join(repeats)}.")
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
        raise PermissionDenied(
            "Photos on the Memories wall are put there by a ministry's admins "
            "and by the foundation.")

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
            messages.success(request, _added_message(added, form.repeats))
            return redirect("gallery:manage")
    else:
        form = GalleryPhotoForm(user=request.user)

    photos = GalleryPhoto.objects.on_the_wall()
    if not foundation:
        photos = photos.filter(ministry_id__in=administered)

    return render(request, "gallery/manage.html", {
        "form": form,
        "photos": photos,
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

    caption = photo.caption
    # ⚠️ The files go too, and before the row does. Deleting only the row
    #    leaves two objects in the bucket that nothing points at any more —
    #    invisible, unbilled to anyone's attention, and holding a photograph
    #    somebody asked to have taken down.
    photo.image.delete(save=False)
    photo.thumb.delete(save=False)
    photo.delete()
    messages.success(request, f"Removed from Memories — {caption}.")
    return redirect("gallery:manage")
