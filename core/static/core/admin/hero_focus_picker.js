/*
 * Home page admin: choose the focal point on the picture — by clicking it or by
 * dragging the ring — and see the file you just chose without saving first.
 *
 * The two number fields (hero_focus_x / hero_focus_y) remain the only thing
 * that is submitted. This script writes into them; it never posts anything of
 * its own, so the server side has one way in whether or not any of this ran.
 *
 * ⚠️ With JavaScript off the two fields are simply two ordinary inputs and the
 *    framing is still editable. That is why they are demoted **here** rather
 *    than hidden in the stylesheet: a stylesheet hides them in the one case
 *    they are needed.
 */
(function () {
    "use strict";

    function clamp(value) {
        return Math.max(0, Math.min(100, Math.round(value)));
    }

    function init() {
        var picker = document.querySelector(".hero-focus");
        var xField = document.getElementById("id_hero_focus_x");
        var yField = document.getElementById("id_hero_focus_y");
        if (!picker || !xField || !yField) {
            return;
        }

        var video = picker.querySelector(".hero-focus__video");
        var image = picker.querySelector(".hero-focus__image");
        var marker = picker.querySelector(".hero-focus__marker");
        var help = document.querySelector(".hero-focus__help");
        var empty = document.querySelector(".hero-focus__empty");
        var fieldset = xField.closest("fieldset") || document.body;

        // What is **stored** (handed over by the template) as against what has
        // merely been **chosen** in a file input and not submitted yet. Kept
        // apart on purpose: cancelling an upload has to put the saved picture
        // back, and it cannot if the two have been mixed into one variable.
        var saved = {
            image: picker.getAttribute("data-image-url") || "",
            video: picker.getAttribute("data-video-url") || ""
        };
        var chosen = {image: "", video: ""};   // blob: URLs, this page load only
        var clears = {image: null, video: null};

        function urlFor(kind) {
            if (chosen[kind]) {
                return chosen[kind];
            }
            // ⚠️ Read, never written. Ticking Clear *and* choosing a file is a
            //    contradiction Django rejects on submit with a message of its
            //    own; sorting it out here would be picking one of the two on
            //    somebody's behalf.
            if (clears[kind] && clears[kind].checked) {
                return "";
            }
            return saved[kind];
        }

        function shown() {
            // ⚠️ **The rule the front page uses** — video wins when both are set
            //    (HomePage.hero). A framing tool that previewed the picture
            //    while the site played the video would be showing the wrong
            //    thing to aim at, and it would look perfectly reasonable.
            return video.hidden ? image : video;
        }

        function draw() {
            // Read from the fields, not from a variable of our own: typing in
            // the boxes has to move the marker too, or the page is showing two
            // answers to one question.
            marker.style.left = clamp(xField.value || 50) + "%";
            marker.style.top = clamp(yField.value || 50) + "%";
        }

        function refresh() {
            var videoUrl = urlFor("video");
            var imageUrl = urlFor("image");

            if (videoUrl) {
                if (video.getAttribute("src") !== videoUrl) {
                    video.src = videoUrl;
                    // Setting src does not reliably restart resource selection
                    // on an element that already had one; the single frame this
                    // widget is here for is what load() goes and gets.
                    video.load();
                }
                video.hidden = false;
                image.hidden = true;
            } else if (imageUrl) {
                if (image.getAttribute("src") !== imageUrl) {
                    image.src = imageUrl;
                }
                image.hidden = false;
                video.hidden = true;
            } else {
                video.hidden = true;
                image.hidden = true;
            }

            var anything = Boolean(videoUrl || imageUrl);
            picker.hidden = !anything;
            if (help) {
                help.hidden = !anything;
            }
            if (empty) {
                empty.hidden = anything;
            }
            // ⚠️ The demotion is tied to there being something to frame. On an
            //    empty form those two numbers are the only way to set the
            //    framing at all, so shrinking them to a read-out under a blank
            //    space would take the control away and leave nothing in its
            //    place.
            fieldset.classList.toggle("hero-focus-on", anything);
            draw();
        }

        function watch(kind, inputId, clearId) {
            var input = document.getElementById(inputId);
            if (!input) {
                return;
            }
            clears[kind] = document.getElementById(clearId);
            input.addEventListener("change", function () {
                // ⚠️ Revoked before it is replaced. A blob: URL pins the whole
                //    file in memory until it is let go, so somebody trying four
                //    photographs in a row would otherwise be holding all four.
                if (chosen[kind]) {
                    window.URL.revokeObjectURL(chosen[kind]);
                }
                chosen[kind] = input.files && input.files[0]
                    ? window.URL.createObjectURL(input.files[0])
                    : "";
                refresh();
            });
            if (clears[kind]) {
                clears[kind].addEventListener("change", refresh);
            }
        }

        function pick(event) {
            // ⚠️ Measured against the **media element**, not the container. The
            //    picture is letterboxed inside its box (object-fit: contain),
            //    so the container is wider or taller than the picture itself
            //    and a click in the empty margin would otherwise read as a
            //    point inside the photograph.
            var box = shown().getBoundingClientRect();
            if (!box.width || !box.height) {
                return false;
            }
            xField.value = clamp(((event.clientX - box.left) / box.width) * 100);
            yField.value = clamp(((event.clientY - box.top) / box.height) * 100);
            draw();
            return true;
        }

        var dragging = false;

        // ⚠️ Pointer events, not mouse events: one pair of handlers covers a
        //    mouse, a trackpad and a finger. The alternative is a mousedown set
        //    plus a touchstart set that have to agree with each other about the
        //    same piece of state.
        //
        // ⚠️ Clicking still works, and works unchanged, because a click **is** a
        //    pointerdown: the point is set on the way down and a drag only
        //    keeps setting it. There is deliberately no second `click` handler
        //    that could come to disagree with this one.
        picker.addEventListener("pointerdown", function (event) {
            if (event.pointerType === "mouse" && event.button !== 0) {
                return;   // a right-click opens a menu; it is not a framing gesture
            }
            if (!pick(event)) {
                return;
            }
            dragging = true;
            picker.classList.add("hero-focus--dragging");
            // ⚠️ Capture, so a drag that wanders off the picture — or out of the
            //    window — keeps arriving here. Without it the pointer is lost at
            //    the edge and the ring stops following, which reads as the
            //    widget freezing rather than as having hit a boundary. The
            //    numbers are clamped to 0–100 either way.
            if (picker.setPointerCapture) {
                picker.setPointerCapture(event.pointerId);
            }
            // Stops the browser starting its own drag of the <img>, which would
            // take over with a ghost image and swallow every move after it.
            event.preventDefault();
        });

        picker.addEventListener("pointermove", function (event) {
            if (dragging) {
                pick(event);
            }
        });

        function release(event) {
            if (!dragging) {
                return;
            }
            dragging = false;
            picker.classList.remove("hero-focus--dragging");
            if (picker.hasPointerCapture && picker.hasPointerCapture(event.pointerId)) {
                picker.releasePointerCapture(event.pointerId);
            }
        }

        picker.addEventListener("pointerup", release);
        picker.addEventListener("pointercancel", release);

        xField.addEventListener("input", draw);
        yField.addEventListener("input", draw);

        watch("image", "id_hero_image", "hero_image-clear_id");
        watch("video", "id_hero_video", "hero_video-clear_id");

        // Demote the two rows to a compact read-out under the picture. Done
        // last, so a failure above leaves them exactly as the server rendered
        // them.
        //
        // ⚠️ The flag goes on the **fieldset**, which contains both the picture
        //    and the two rows. The first version put it on the picture's own
        //    row — an element the number fields are not inside — so the class
        //    was set, the selectors never matched, and the only symptom was
        //    that nothing looked any different.
        [xField, yField].forEach(function (field) {
            var row = field.closest(".form-row");
            if (row) {
                row.classList.add("hero-focus__readout");
            }
        });

        refresh();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
