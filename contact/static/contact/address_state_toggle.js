/*
 * Country first, then state: for a United States address the free-text state box
 * is swapped for a dropdown of the 50 states (plus DC, territories and armed
 * forces codes). The column stays free text so a non-US address keeps its own
 * province or region — "Ontario", "Jalisco" — which a dropdown of US states
 * cannot express.
 *
 * The text input stays the single source of truth: the dropdown writes into it,
 * and the dropdown itself has no `name`, so it never submits anything.
 *
 * ⚠️ **Two pages load this file, deliberately one copy** (2026-08-19): the
 *    Contact admin (via ContactAdminForm.Media) and the volunteer's own
 *    "My profile" page (a <script> tag on the page). It used to live under
 *    contact/static/contact/admin/ and only the admin had the behaviour — which
 *    is exactly the report this move answers ("my profile should work like the
 *    django admin contact"). Copying it into the front-end bundle instead would
 *    have left two implementations of one rule, and the one nobody is looking at
 *    is the one that drifts.
 *
 * ⚠️ Both pages feed it the same way: the option list arrives as JSON on the
 *    text input's `data-us-states` attribute, built by
 *    contact.forms.us_state_choices_json(). Neither form spells the list out.
 *
 * ⚠️ Progressive enhancement, and it has to stay that way: with no JavaScript
 *    the text box is a perfectly good state field. Nothing here is a validation
 *    rule — the column accepts anything either way.
 */
(function () {
    "use strict";

    function init() {
        var country = document.getElementById("id_address_country");
        var input = document.getElementById("id_address_state");
        if (!country || !input) {
            return;
        }

        var choices;
        try {
            choices = JSON.parse(input.getAttribute("data-us-states") || "[]");
        } catch (e) {
            choices = [];
        }
        if (!choices.length) {
            return;
        }

        var select = document.createElement("select");
        select.id = "id_address_state_us";
        // No name attribute: this control is a helper, it never submits on its own.
        select.appendChild(new Option("---------", ""));
        choices.forEach(function (choice) {
            select.appendChild(new Option(choice[1], choice[0]));
        });
        input.parentNode.insertBefore(select, input.nextSibling);

        function selectHasValue(value) {
            return Array.prototype.some.call(select.options, function (option) {
                return option.value === value;
            });
        }

        function sync() {
            var isUS = country.value === "US";
            select.style.display = isUS ? "" : "none";
            input.style.display = isUS ? "none" : "";

            if (isUS) {
                // A value typed for another country (e.g. "Ontario") is not in the
                // state list; keep it as an option rather than silently dropping it.
                if (input.value && !selectHasValue(input.value)) {
                    select.appendChild(new Option(input.value, input.value));
                }
                select.value = input.value;
            }
        }

        select.addEventListener("change", function () {
            input.value = select.value;
        });
        country.addEventListener("change", sync);
        sync();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
