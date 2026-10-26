/* English onboarding bypasses __ upstream. Translate known display fields
 * during rendering only; saved onboarding keys and routes stay unchanged.
 * Ukrainian rendering is handled by uk_ui.js.
 */
(() => {
    if (frappe.boot.lang !== "en") return;
    const onboarding = frappe.widget?.widget_factory?.onboarding?.prototype;
    for (const method of ["make_step", "make_step_content", "set_title", "update_progress"]) {
        const original = onboarding?.[method];
        if (!original || original.vectorBranding) continue;
        const fields = method.startsWith("make_")
            ? ["title", "description", "action_label"] : ["title"];
        onboarding[method] = function (...args) {
            const object = method.startsWith("make_") ? args[0] : this;
            if (!object) return original.apply(this, args);
            const saved = fields.map((field) => [field, object[field]]);
            try {
                for (const [field, value] of saved) {
                    if (typeof value === "string") object[field] = __(value);
                }
                return original.apply(this, args);
            } finally {
                for (const [field, value] of saved) object[field] = value;
            }
        };
        onboarding[method].vectorBranding = true;
    }
})();
