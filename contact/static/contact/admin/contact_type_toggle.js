/*
 * Contact admin: show only the name fields that apply to the selected contact type.
 * Individuals get the legal/preferred person names; organizations get the
 * organization name. Fields hidden here are also cleared server-side on save
 * (Contact.save), so nothing stale is kept behind the scenes.
 */
(function () {
    "use strict";

    var FIELDS_BY_TYPE = {
        individual: ["legal_first_name", "legal_last_name", "preferred_name"],
        organization: ["organization_name"]
    };

    function rowFor(fieldName) {
        var field = document.getElementById("id_" + fieldName);
        if (!field) {
            return null;
        }
        // The whole admin row (label + input + help text) carries this class.
        return field.closest(".form-row") || field.parentElement;
    }

    function init() {
        var contactType = document.getElementById("id_contact_type");
        if (!contactType) {
            return;
        }

        var rows = {};
        Object.keys(FIELDS_BY_TYPE).forEach(function (type) {
            rows[type] = FIELDS_BY_TYPE[type].map(rowFor).filter(Boolean);
        });

        function sync() {
            Object.keys(rows).forEach(function (type) {
                var show = type === contactType.value;
                rows[type].forEach(function (row) {
                    row.style.display = show ? "" : "none";
                });
            });
        }

        contactType.addEventListener("change", sync);
        sync();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
