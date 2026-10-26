// Preserve each section's choice across sidebar rebuilds and panel mode changes.
(() => {
    const section = frappe.ui.sidebar_item.TypeSectionBreak.prototype;
    const sidebar = frappe.ui.Sidebar.prototype;
    if (section.save_section_break_state.vectorSections) return;
    const storageKey = "section-breaks-state";
    let memory = {};

    function readState() {
        try {
            const value = JSON.parse(localStorage.getItem(storageKey));
            memory = value && typeof value === "object" && !Array.isArray(value) ? value : {};
        } catch (_) { /* Keep navigation usable when browser storage is unavailable. */ }
        return memory;
    }

    section.prepare = function (opts) {
        this.collapsed = false;
        this.nested_items = opts.item.nested_items || this.nested_items;
        this.items = [];
        this.$items = [];
        this.section_breaks_state = readState();
    };

    section.save_section_break_state = function () {
        // Every section was constructed with a snapshot. Merge into the latest
        // state so saving one group cannot discard another group's changes.
        const state = readState();
        state[this.current_module] = {
            ...state[this.current_module],
            [this.item.label]: this.collapsed,
        };
        this.section_breaks_state = state;
        try { localStorage.setItem(storageKey, JSON.stringify(state)); }
        catch (_) { /* The in-memory choice still works for this page. */ }
    };
    section.save_section_break_state.vectorSections = true;

    section.toggle_on_collapse = function () {
        $(document).on("sidebar-expand", (_event, expand) => {
            if (expand.sidebar_expand) {
                // false is an explicit expanded choice too. Clear the temporary
                // value so a later redraw cannot restore an older choice.
                if (typeof this.old_state === "boolean") {
                    this.collapsed = this.old_state;
                    this.old_state = undefined;
                    this.toggle();
                }
            } else {
                // Repeated rail redraws must not overwrite the original choice.
                if (typeof this.old_state !== "boolean") this.old_state = this.collapsed;
                this.item.indent ? this.close() : this.open();
            }
        });
    };

    const setupSectionClick = section.setup_event_listner;
    section.setup_event_listner = function () {
        setupSectionClick.apply(this, arguments);
        const heading = this.wrapper.find(".standard-sidebar-item")[0];
        // Read the rail state before the native click handler opens the panel.
        // Expanding labels and other groups can push this group's children
        // below the viewport, making its icon look like an inactive link.
        heading.addEventListener("click", event => {
            if (frappe.app.sidebar.sidebar_expanded) return;
            const before = heading.getBoundingClientRect();
            // Keyboard activation uses the same anchor as a centered click.
            const anchorY = event.detail ? event.clientY : before.top + before.height / 2;
            const fraction = Math.max(0, Math.min(1, (anchorY - before.top) / before.height));
            const align = () => {
                if (!heading.isConnected || this.collapsed || !frappe.app.sidebar.sidebar_expanded) return;
                const viewport = heading.closest(".vector-navigation-scroll-viewport");
                if (!viewport) return;
                const bounds = viewport.getBoundingClientRect();
                const after = heading.getBoundingClientRect();
                const firstEntry = this.$nested_items[0].querySelector(".standard-sidebar-item");
                const entryBottom = firstEntry?.getBoundingClientRect().bottom || after.bottom;
                // Keep the group under the pointer instead of sending it to
                // the panel's top. Reserve room for its first destination.
                const targetTop = Math.max(bounds.top, Math.min(
                    anchorY - after.height * fraction,
                    bounds.bottom - (entryBottom - after.top) - 4,
                ));
                viewport.scrollBy({
                    top: after.top - targetTop,
                    behavior: "instant",
                });
                // Rows shrink from rail tiles to text links during expansion.
                // Follow finite transitions so their final heights do not move
                // the selected group away from its activation position again.
                if (heading.closest(".body-sidebar-container").getAnimations({ subtree: true }).some(animation =>
                    animation.playState === "running"
                    && Number.isFinite(animation.effect.getComputedTiming().endTime))) {
                    requestAnimationFrame(align);
                }
            };
            requestAnimationFrame(align);
        }, true);
    };

    const expandParent = sidebar.expand_parent_section;
    sidebar.expand_parent_section = function () {
        const name = this.active_item?.closest(".section-item").attr("data-id");
        const activeSection = name && this.get_item(name);
        // Route highlighting normally opens its parent. Respect an explicit
        // collapsed choice, including when reloading a page inside that group.
        if (activeSection && readState()[activeSection.current_module]?.[name] === true) return;
        return expandParent.apply(this, arguments);
    };
})();
