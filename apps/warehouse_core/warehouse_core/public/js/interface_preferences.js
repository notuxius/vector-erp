// Shared Desk preferences cover framework and ERP pages, including lazy dialogs.
(() => {
    if (frappe.vector_animations) return;
    const root = document.documentElement;
    const user = frappe.session.user || frappe.boot.user?.name;
    const key = `vector-animations:${user}`;
    let preference;
    function read() {
        try {
            const saved = localStorage.getItem(key);
            preference = saved === "enabled" ? true : saved === "disabled" ? false : undefined;
        } catch (_) { /* Animations stay off when storage is unavailable. */ }
    }
    const enabled = () => preference ?? false;
    function apply() {
        const off = !enabled();
        root.classList.toggle("vector-animations-disabled", off);
        // Frappe still uses jQuery effects for some form and navigation controls.
        if ($.fx) $.fx.off = off;
        if (off) {
            $(":animated").stop(true, true);
            for (const animation of document.getAnimations()) {
                if (Number.isFinite(animation.effect?.getComputedTiming().endTime)) {
                    try { animation.finish(); } catch (_) { /* Detached effects may have no timeline. */ }
                } else animation.cancel();
            }
        }
    }
    frappe.vector_animations = {
        enabled,
        set(value) {
            preference = Boolean(value);
            try { localStorage.setItem(key, preference ? "enabled" : "disabled"); } catch (_) {}
            apply();
        },
    };
    // Native Web Animations are independent of CSS and jQuery effects.
    const animate = Element.prototype.animate;
    if (animate) Element.prototype.animate = function (frames, options) {
        if (!enabled()) options = { ...(typeof options === "object" ? options : {}),
            duration: 0, delay: 0, endDelay: 0, iterations: 1 };
        return animate.call(this, frames, options);
    };
    read();
    apply();
    window.addEventListener("storage", event => {
        if (event.key === key || event.key === null) { read(); apply(); }
    });

    // SettingsDialog is lazy loaded, but its base Dialog is already available.
    // Extend only the user Appearance tab before SettingsDialog constructs it.
    const make = frappe.ui.Dialog.prototype.make;
    frappe.ui.Dialog.prototype.make = function (...args) {
        const items = this.tabs?.flatMap(group => group.items || []);
        const appearance = items?.find(item => item.id === "appearance");
        if (items?.some(item => item.id === "profile") && appearance && !appearance.vectorPreferences) {
            appearance.vectorPreferences = true;
            const render = appearance.render;
            appearance.render = function (panel) {
                render.call(this, panel);
                const uk = frappe.boot.lang === "uk";
                // Desktop navigation is always pinned at the top.
                panel.body.find(".dock-preview-rail, .dock-preview-tray")
                    .closest(".theme-card-wrapper").parent().remove();
                panel.body.find(".settings-dialog-section-title").filter((_, el) => el.textContent === __("Dock"))
                    .closest(".settings-dialog-section-heading").remove();
                const fields = panel.add_fields([
                    {
                        fieldtype: "Switch", fieldname: "vector_animations",
                        label: uk ? "Анімації інтерфейсу" : "Interface animations",
                        description: uk
                            ? "Анімації інтерфейсу у Frappe і Вектор ERP вимкнено за замовчуванням. Ефекти наведення на кнопки залишаються активними. Застосовується одразу для вас у цьому браузері."
                            : "Interface animations in Frappe and Vector ERP are off by default. Button hover effects remain active. Applies immediately for you in this browser.",
                        default: enabled() ? 1 : 0,
                    },
                ]);
                fields.fields_dict.vector_animations.$input.on("change", () =>
                    frappe.vector_animations.set(Boolean(fields.get_value("vector_animations"))));
            };
        }
        return make.apply(this, args);
    };
})();
