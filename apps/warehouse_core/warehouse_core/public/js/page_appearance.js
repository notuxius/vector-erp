/* Page-level access to the same appearance preferences used by User Settings. */
(() => {
    const prototype = frappe.ui.Page.prototype;
    if (prototype.setup_page.vectorAppearance) return;

    let themeSwitcher;
    const currentTheme = () => document.documentElement.getAttribute("data-theme-mode") || "light";
    const fullWidth = () => JSON.parse(localStorage.container_fullwidth || "false");

    function setTheme(theme) {
        if (currentTheme() === theme) return;
        if (!themeSwitcher) {
            themeSwitcher = new frappe.ui.ThemeSwitcher();
            // Only the native preference setter is needed; don't retain an unused modal.
            themeSwitcher.dialog.$wrapper.remove();
        }
        themeSwitcher.toggle_theme(theme);
    }

    function options() {
        const theme = currentTheme();
        const full = fullWidth();
        const choice = (label, icon, selected, onclick) => ({
            label: __(label), icon, selected, onclick,
            icon_right: selected ? "check" : undefined,
            css_class: "vector-appearance-choice",
        });
        return [
            {
                group: __("Theme"),
                options: [
                    ["Light", "sun", "light"],
                    ["Dark", "moon", "dark"],
                    ["Automatic", "monitor", "automatic"],
                ].map(([label, icon, value]) => choice(label, icon, theme === value, () => setTheme(value))),
            },
            {
                group: __("Layout"),
                options: [
                    ["Compact", "panel-left", false],
                    ["Full Width", "maximize", true],
                ].map(([label, icon, value]) => choice(label, icon, full === value, () => {
                    // Re-read at activation; selecting the current option must never toggle it.
                    if (fullWidth() !== value) frappe.ui.toolbar.toggle_full_width();
                })),
            },
        ];
    }

    function install(page) {
        if (!page.standard_actions?.length || page.vector_appearance) return;
        const dropdown = new frappe.ui.Dropdown({
            button: {
                label: frappe.boot.lang === "uk" ? "Вигляд" : __("Appearance"),
                icon: "sliders-horizontal",
                css_class: "vector-appearance-button",
            },
            align: "end",
            options,
            on_open() {
                // The native menu's selected flag is visual; expose radio state to AT too.
                document.querySelectorAll(".es-menu .vector-appearance-choice").forEach((row) => {
                    row.setAttribute("role", "menuitemradio");
                    row.setAttribute("aria-checked", String(row.getAttribute("data-state") === "checked"));
                });
            },
        });
        dropdown.$trigger.prependTo(page.standard_actions);
        page.vector_appearance = dropdown;
        page.wrapper.on("hide.vectorAppearance", () => dropdown.close("owner"));

        if (page.wrapper.attr("data-page-route") === "desktop") {
            // All apps hides the standard header and rebuilds its own navbar on each visit.
            // Observe just this cached page so both desktop grid variants keep one trigger.
            const placeInDesktopHeader = () => {
                const actions = page.wrapper.find(".desktop-navbar .desktop-avatar").parent()[0];
                if (actions && dropdown.trigger_el.parentElement !== actions) {
                    dropdown.close("owner");
                    actions.prepend(dropdown.trigger_el);
                }
            };
            page.vector_appearance_observer = new MutationObserver(placeInDesktopHeader);
            page.vector_appearance_observer.observe(page.wrapper[0], { childList: true, subtree: true });
            placeInDesktopHeader();
        }
    }

    const setup = prototype.setup_page;
    prototype.setup_page = function (...args) {
        const result = setup.apply(this, args);
        install(this);
        return result;
    };
    prototype.setup_page.vectorAppearance = true;

    // Cover pages constructed before app assets, as well as newly created/cached routes.
    const installExisting = () => Object.values(frappe.ui.pages || {}).forEach(install);
    installExisting();
    $(document).on("app_ready.vectorAppearance", installExisting);
})();
