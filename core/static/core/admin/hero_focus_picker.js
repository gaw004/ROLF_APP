/*
 * Home page admin: choose the focal point by clicking the picture.
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

        var media = picker.querySelector(".hero-focus__media");
        var marker = picker.querySelector(".hero-focus__marker");

        function draw() {
            // Read from the fields, not from a variable of our own: typing in
            // the boxes has to move the marker too, or the page is showing two
            // answers to one question.
            marker.style.left = clamp(xField.value || 50) + "%";
            marker.style.top = clamp(yField.value || 50) + "%";
        }

        function pick(event) {
            // ⚠️ Measured against the **media element**, not the container. The
            //    picture is letterboxed inside its box (object-fit: contain),
            //    so the container is wider or taller than the picture itself
            //    and a click in the empty margin would otherwise read as a
            //    point inside the photograph.
            var box = media.getBoundingClientRect();
            if (!box.width || !box.height) {
                return;
            }
            xField.value = clamp(((event.clientX - box.left) / box.width) * 100);
            yField.value = clamp(((event.clientY - box.top) / box.height) * 100);
            draw();
        }

        picker.addEventListener("click", pick);
        xField.addEventListener("input", draw);
        yField.addEventListener("input", draw);

        // Demote the two rows to a compact read-out under the picture. Done
        // last, so a failure above leaves them exactly as the server rendered
        // them.
        //
        // ⚠️ The flag goes on the **fieldset**, which contains both the picture
        //    and the two rows. The first version put it on the picture's own
        //    row — an element the number fields are not inside — so the class
        //    was set, the selectors never matched, and the only symptom was
        //    that nothing looked any different.
        (xField.closest("fieldset") || document.body).classList.add("hero-focus-on");
        [xField, yField].forEach(function (field) {
            var row = field.closest(".form-row");
            if (row) {
                row.classList.add("hero-focus__readout");
            }
        });

        draw();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
