/*
 * Contact admin: the state field is stored as free text so non-US addresses keep
 * their province/region. For United States addresses we swap in a dropdown of the
 * 50 states (plus DC, territories and armed forces codes), whose options are handed
 * to us on the text input's data-us-states attribute (see forms.ContactAdminForm).
 *
 * The text input stays the single source of truth: the dropdown writes into it.
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
