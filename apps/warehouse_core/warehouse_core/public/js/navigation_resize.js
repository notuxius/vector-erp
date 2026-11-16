// Sidebar width is edited only in Edit Sidebar; the module dock has a fixed width.
(() => {
    const root = document.documentElement;
    const panels = {
        // Keep labeled module tiles at least 72px wide, plus 16px padding.
        dock: {
            min: 88,
            max: 88, initial: 88,
        },
        sidebar: { min: 100, max: 400, initial: 400 },
    };
    let storageKey;
    let fitSidebarText = true;
    let textWidth;
    let observedSidebar;
    let measureFrame;
    let measuredRoute;
    let fittedWidths = {};
    root.classList.add("vector-navigation-restoring");
    for (const panel of Object.values(panels)) {
        panel.preferred = panel.initial;
    }

    function loadPreferences() {
        const user = frappe.session.user || frappe.boot.user?.name;
        if (storageKey || !user) return;
        storageKey = `vector-navigation-widths:${user}`;
        let saved = {};
        try {
            // Older assets ran before session setup and used the undefined-user key.
            saved = JSON.parse(localStorage.getItem(storageKey)
                ?? localStorage.getItem("vector-navigation-widths:undefined")) || {};
        } catch (_) { /* Optional. */ }
        try {
            fittedWidths = JSON.parse(localStorage.getItem(`${storageKey}:fitted`)) || {};
        } catch (_) { /* Optional. */ }
        measuredRoute = undefined;
        // Apply the 400px maximum with text fitting to existing preferences once.
        // Choices made after this update continue to be remembered.
        const applyDefaults = saved.sidebarDefaultsVersion !== 2;
        fitSidebarText = applyDefaults || saved.sidebarFitText !== false;
        for (const [name, panel] of Object.entries(panels)) {
            panel.preferred = Number.isFinite(saved[name]) ? saved[name] : panel.initial;
            // Ignore legacy custom dock widths; sidebar preferences remain adjustable.
            panel.preferred = Math.max(panel.min,
                Math.min(panel.max, panel.preferred));
        }
        if (applyDefaults) {
            panels.sidebar.preferred = panels.sidebar.initial;
            persist();
        }
    }

    function maximum(name) {
        if (name === "sidebar") {
            // A mobile drawer overlays the page, so it does not reserve content width.
            // Keep the settings range unchanged even when the screen is narrower.
            if (window.innerWidth < 768) return panels.sidebar.max;
            return Math.max(panels.sidebar.min,
                Math.min(panels.sidebar.max, window.innerWidth - 320));
        }
        const other = panels[name === "dock" ? "sidebar" : "dock"];
        return Math.max(panels[name].min, Math.min(panels[name].max,
            window.innerWidth - 320 - (other.width || other.preferred)));
    }

    function apply(name, value) {
        const panel = panels[name];
        const limit = name === "sidebar" && window.innerWidth < 768
            // Leave space for the 44px close button and its 8px side margins.
            ? Math.min(maximum(name), window.innerWidth - 60) : maximum(name);
        panel.width = Math.round(Math.max(panel.min, Math.min(limit, value)));
        root.style.setProperty(`--vector-${name}-width`, `${panel.width}px`);
    }

    function restore() {
        loadPreferences();
        const route = `${frappe.boot.lang}:${location.pathname}`;
        if (measuredRoute !== route) {
            measuredRoute = route;
            textWidth = Number.isFinite(fittedWidths[route]) ? fittedWidths[route] : undefined;
        }
        apply("dock", panels.dock.preferred);
        applySidebar();
    }

    function applySidebar() {
        const limit = Math.min(panels.sidebar.preferred, maximum("sidebar"));
        apply("sidebar", fitSidebarText && textWidth ? Math.min(limit, textWidth) : limit);
        document.querySelectorAll(".vector-sidebar-width-controls").forEach(controls => {
            const slider = controls.querySelector('input[type="range"]');
            const number = controls.querySelector('input[type="number"]');
            slider.max = number.max = maximum("sidebar");
            slider.value = limit;
            if (document.activeElement !== number) number.value = limit;
            controls.querySelector("output").textContent = __("Maximum: {0} px · Current: {1} px",
                [limit, panels.sidebar.width]);
        });
    }

    function persist() {
        try {
            if (!storageKey) return;
            localStorage.setItem(storageKey, JSON.stringify({
                sidebar: panels.sidebar.preferred,
                sidebarFitText: fitSidebarText,
                sidebarDefaultsVersion: 2,
            }));
        } catch (_) { /* Resizing still works when storage is unavailable. */ }
    }

    function measureSidebarText() {
        if (!observedSidebar) return;
        if (!fitSidebarText) {
            root.classList.remove("vector-navigation-restoring");
            return;
        }
        // Incomplete navigation and fallback fonts must not replace the last fitted width.
        if (!observedSidebar.querySelector(".sidebar-item-label") || document.fonts?.status === "loading") return;
        // Measure an expanded copy at a stable width. Include nested entries so opening
        // a group does not keep changing the width, and include icons/padding/indentation.
        const shell = document.createElement("div");
        shell.className = "body-sidebar-container expanded vector-sidebar-measure";
        shell.setAttribute("aria-hidden", "true");
        shell.inert = true;
        const copy = observedSidebar.cloneNode(true);
        copy.removeAttribute("id");
        copy.querySelectorAll("[id]").forEach(element => element.removeAttribute("id"));
        copy.querySelectorAll(".nested-container.hidden").forEach(element => element.classList.remove("hidden"));
        shell.append(copy);
        document.body.append(shell);
        try {
            let widest = panels.sidebar.min;
            const width = copy.getBoundingClientRect().width;
            for (const label of copy.querySelectorAll(".sidebar-item-label, .header-title")) {
                if (!label.getClientRects().length) continue;
                const box = label.matches(".header-title") ? label.closest(".title-container") || label : label;
                const available = box.getBoundingClientRect().width;
                const range = document.createRange();
                range.selectNodeContents(label);
                widest = Math.max(widest, width - available + range.getBoundingClientRect().width);
            }
            textWidth = Math.max(panels.sidebar.min, Math.ceil(widest + 2));
        } finally {
            shell.remove();
        }
        applySidebar();
        if (storageKey && measuredRoute && fittedWidths[measuredRoute] !== textWidth) {
            delete fittedWidths[measuredRoute];
            fittedWidths[measuredRoute] = textWidth;
            fittedWidths = Object.fromEntries(Object.entries(fittedWidths).slice(-50));
            try { localStorage.setItem(`${storageKey}:fitted`, JSON.stringify(fittedWidths)); }
            catch (_) { /* The measured width still works without storage. */ }
        }
        requestAnimationFrame(() => root.classList.remove("vector-navigation-restoring"));
    }

    function scheduleMeasurement() {
        // Keep the current fitted width while a new measurement is pending.
        cancelAnimationFrame(measureFrame);
        measureFrame = requestAnimationFrame(measureSidebarText);
    }

    const sidebarObserver = new MutationObserver(scheduleMeasurement);

    function setup() {
        restore();
        const sidebar = document.querySelector(".body-sidebar");
        if (sidebar && sidebar !== observedSidebar) {
            sidebarObserver.disconnect();
            observedSidebar = sidebar;
            sidebarObserver.observe(sidebar, { childList: true, subtree: true, characterData: true });
            scheduleMeasurement();
        }

    }

    // Generated page sidebars share appearance preferences without a saved arrangement.
    function showSidebarWidthSettings() {
        const dialog = new frappe.ui.Dialog({
            title: __("Edit Sidebar"),
            fields: [{ fieldtype: "HTML", fieldname: "vector_sidebar_width" }],
        });
        dialog.show();
        addSidebarWidthControls(dialog);
        dialog.$wrapper.one("hidden.bs.modal", () => dialog.$wrapper.remove());
    }

    function addSidebarWidthControls(dialog) {
        const escape = frappe.utils.escape_html;
        const controls = document.createElement("div");
        controls.className = "vector-sidebar-width-controls";
        controls.innerHTML = `
            <div class="vector-sidebar-width-label">
                <span>${escape(__("Maximum sidebar width (px)"))}</span>
                <div class="vector-sidebar-width-row">
                    <input class="vector-sidebar-width-slider" type="range"
                        aria-label="${escape(__("Maximum sidebar width (px)"))}"
                        min="${panels.sidebar.min}" max="${maximum("sidebar")}" step="1">
                    <input class="vector-sidebar-width-number form-control" type="number"
                        aria-label="${escape(__("Maximum sidebar width (px)"))}"
                        min="${panels.sidebar.min}" max="${maximum("sidebar")}" step="1">
                </div>
            </div>
            <output aria-live="polite"></output>
            <label class="vector-sidebar-fit-label">
                <input class="vector-sidebar-fit-text" type="checkbox">
                <span>${escape(__("Fit width to the widest text, up to the selected maximum"))}</span>
            </label>
            <p class="help-box">${escape(__("Applies immediately for you in this browser."))}</p>`;
        dialog.fields_dict.vector_sidebar_width.$wrapper.append(controls);
        const slider = controls.querySelector('input[type="range"]');
        const number = controls.querySelector('input[type="number"]');
        const checkbox = controls.querySelector('input[type="checkbox"]');
        slider.value = panels.sidebar.preferred;
        number.value = panels.sidebar.preferred;
        checkbox.checked = fitSidebarText;
        const setMaximum = value => {
            panels.sidebar.preferred = Math.round(Math.max(panels.sidebar.min,
                Math.min(maximum("sidebar"), value)));
            slider.value = panels.sidebar.preferred;
            number.value = panels.sidebar.preferred;
            applySidebar();
            persist();
        };
        slider.addEventListener("input", () => setMaximum(Number(slider.value)));
        number.addEventListener("input", () => {
            const value = number.valueAsNumber;
            // Let a partially typed value remain until the user finishes editing.
            if (Number.isInteger(value) && value >= panels.sidebar.min && value <= maximum("sidebar")) {
                setMaximum(value);
            }
        });
        number.addEventListener("change", () => {
            setMaximum(Number.isFinite(number.valueAsNumber)
                ? number.valueAsNumber : panels.sidebar.preferred);
        });
        checkbox.addEventListener("change", () => {
            fitSidebarText = checkbox.checked;
            if (fitSidebarText) measureSidebarText();
            applySidebar();
            persist();
        });
        // Leave the live sidebar visible beside the editor on desktop. Reserve
        // a fixed gutter so dragging never moves the slider under the pointer.
        dialog.$wrapper.addClass("vector-sidebar-editor");
        document.body.classList.add("vector-sidebar-width-preview");
        dialog.$wrapper.one("hidden.bs.modal", () =>
            document.body.classList.remove("vector-sidebar-width-preview"));
        frappe.app?.sidebar?.open();
        measureSidebarText();
        applySidebar();
    }

    function setupSidebarEditor() {
        const prototype = frappe.ui.SidebarManager?.prototype;
        const original = prototype?.extra_actions;
        if (!original || original.vectorNavigationWidth) return;
        prototype.extra_actions = function (...args) {
            return {
                ...original.apply(this, args),
                fields: [
                    { fieldtype: "HTML", fieldname: "vector_sidebar_width" },
                    { fieldtype: "HTML", fieldname: "picker" },
                ],
            };
        };
        prototype.extra_actions.vectorNavigationWidth = true;
        const make = prototype.make;
        prototype.make = function (...args) {
            const result = make.apply(this, args);
            addSidebarWidthControls(this.dialog);
            return result;
        };
    }

    // The native editor is lazy-loaded. Add the preference before its constructor runs.
    const headerPrototype = frappe.ui.SidebarHeader?.prototype;
    const menuItems = headerPrototype?.menu_items;
    if (menuItems) {
        headerPrototype.menu_items = function (...args) {
            const groups = menuItems.apply(this, args);
            for (const group of groups) {
                const item = group.options?.find(option => option.name === "edit-sidebar");
                if (!item) continue;
                if (this.sidebar.sidebar_data?.vector_width_settings_only) {
                    item.onclick = showSidebarWidthSettings;
                    continue;
                }
                const open = item.onclick;
                item.onclick = () => frappe.require("arrangement_editor.bundle.js").then(() => {
                    setupSidebarEditor();
                    return open();
                }).catch(() => frappe.msgprint(
                    __("Could not open the sidebar editor. Please refresh the page.")));
            }
            return groups;
        };
    }

    for (const [prototype, method] of [
        [frappe.ui.Dock?.prototype, "make"],
        [frappe.ui.Sidebar?.prototype, "make_dom"],
    ]) {
        const original = prototype?.[method];
        if (!original || original.vectorNavigationResize) continue;
        prototype[method] = function (...args) {
            const result = original.apply(this, args);
            setup();
            return result;
        };
        prototype[method].vectorNavigationResize = true;
    }
    // Refit widths when Appearance settings change the dock mode.
    const dockPrototype = frappe.ui.Dock?.prototype;
    const applyPin = dockPrototype?.apply_pin;
    if (applyPin && !applyPin.vectorNavigationResize) {
        dockPrototype.apply_pin = function (...args) {
            const result = applyPin.apply(this, args);
            restore();
            return result;
        };
        dockPrototype.apply_pin.vectorNavigationResize = true;
    }
    $(setup);
    $(document).on("app_ready.vectorNavigationResize", setup);
    $(document).on("page-change.vectorNavigationResize", () => {
        restore();
        scheduleMeasurement();
    });
    window.addEventListener("resize", restore);
    window.addEventListener("vector-navigation-position", restore);
    window.addEventListener("resize", scheduleMeasurement);
    document.fonts?.ready.then(scheduleMeasurement);
    document.fonts?.addEventListener("loadingdone", scheduleMeasurement);
    restore();
})();
