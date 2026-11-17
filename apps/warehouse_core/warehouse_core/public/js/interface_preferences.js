// Shared Desk preferences cover framework and ERP pages, including lazy dialogs.
(() => {
    if (frappe.vector_animations) return;
    const root = document.documentElement;
    const user = frappe.session.user || frappe.boot.user?.name;
    const key = `vector-animations:${user}`;
    const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
    let preference;
    function read() {
        try {
            const saved = localStorage.getItem(key);
            preference = saved === "enabled" ? true : saved === "disabled" ? false : undefined;
        } catch (_) { /* Keep the in-memory preference when storage is unavailable. */ }
    }
    const enabled = () => preference ?? !reducedMotion.matches;
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
    reducedMotion.addEventListener("change", apply);
    window.addEventListener("storage", event => {
        if (event.key === key || event.key === null) { read(); apply(); }
    });

    // SettingsDialog is lazy loaded, but its base Dialog is already available.
    const make = frappe.ui.Dialog.prototype.make;
    let settingsHeadersInstalled = false;
    frappe.ui.Dialog.prototype.make = function (...args) {
        const items = this.tabs?.flatMap(group => group.items || []);
        if (items && frappe.ui.SettingsDialogPanel && !settingsHeadersInstalled) {
            // Settings hides the modal's close button and rebuilds its panel header on view changes.
            const panel = frappe.ui.SettingsDialogPanel.prototype;
            const setHeader = panel.set_header;
            panel.set_header = function (options = {}) {
                const actions = options.actions || [];
                const label = __("Close");
                const close = actions.find(action => action.label === label);
                if (this.dialog.static && !close) return setHeader.call(this, options);
                setHeader.call(this, { ...options, actions: actions.filter(action => action !== close) });
                frappe.ui.button({
                    icon: "x", variant: "ghost", tooltip: label,
                    onclick: close ? () => close.click?.call(this, this) : () => this.dialog.hide(),
                }).appendTo(this.$header.removeClass("hide").find(".settings-dialog-panel-actions"));
                return this;
            };
            settingsHeadersInstalled = true;
        }
        const appearance = items?.find(item => item.id === "appearance");
        if (items?.some(item => item.id === "profile") && appearance && !appearance.vectorPreferences) {
            appearance.vectorPreferences = true;
            for (const tab of items.filter(item => ["appearance", "preferences", "lists", "forms", "reports"].includes(item.id))) {
                const changed = new Set();
                const renderTab = tab.render;
                tab.render = function (panel) {
                    renderTab.call(this, panel);
                    for (const field of tab.fields || []) {
                        if (field.fieldtype === "Switch") {
                            panel.get_field(field.fieldname).$input.on("change", () => changed.add(field.fieldname));
                        }
                    }
                };
                tab.actions = [...(tab.actions || []), {
                    label: __("Save"), variant: "solid",
                    async click(panel) {
                        try {
                            // Let the native autosaves finish before submitting the explicit save.
                            await frappe.after_ajax();
                            if (tab === appearance) {
                                // Layout is already stored by the native picker. Keep the OS animation default until explicitly set.
                                if (preference !== undefined) localStorage.setItem(key, preference ? "enabled" : "disabled");
                                await frappe.xcall("frappe.core.doctype.user.user.switch_theme", {
                                    theme: toTitle(root.getAttribute("data-theme-mode") || "light"),
                                });
                            } else if (changed.size) {
                                const values = Object.fromEntries([...changed].map(name => [name, panel.get_value(name)]));
                                await frappe.db.set_value("User", frappe.session.user, values);
                                for (const [name, value] of Object.entries(values)) {
                                    if (panel.get_value(name) === value) changed.delete(name);
                                    if (Object.hasOwn(frappe.boot.user, name)) frappe.boot.user[name] = value;
                                }
                            }
                            frappe.ui.toast({ message: __("Saved"), type: "success" });
                        } catch (error) {
                            frappe.ui.toast({ message: __("Failed to save"), type: "error" });
                            console.error(error);
                        }
                    },
                }];
            }
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
                            ? "Анімації типово ввімкнено, якщо в системі не вибрано зменшення руху. Ваш вибір діє одразу в цьому браузері. Ефекти наведення на кнопки залишаються активними."
                            : "Animations are on by default unless your system requests reduced motion. Your choice applies immediately in this browser. Button hover effects stay active.",
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
