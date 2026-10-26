// Desktop navigation stays attached. Mobile uses a left overlay drawer
// beneath the title row, reached by the menu button or a deliberate edge swipe.
(() => {
    const prototype = frappe.ui.Dock?.prototype;
    if (!prototype || prototype.refresh.vectorWorkspaceChip) return;
    const mobile = window.matchMedia("(max-width: 767.98px)");
    const buttons = new Set();
    let frame;
    let scrim;
    const currentDock = () => frappe.app?.sidebar?.dock;
    const visibleButton = () => [...buttons].find(button => !button.hidden && button.getClientRects().length);

    // Desktop workspaces keep their folded icon rail. Mobile
    // still uses the native off-screen drawer through its responsive styles.
    frappe.ui.Sidebar.prototype.hides_when_collapsed = () => false;

    function focusWorkspace(dock) {
        // Opening changes inherited visibility. Focus after the menu has painted,
        // so keyboard activation also works when the floating tray was hidden.
        const origin = document.activeElement;
        requestAnimationFrame(() => requestAnimationFrame(() => {
            if (!dock.is_open || dock.holds_focus() || document.activeElement !== origin) return;
            dock.$items.find(".dock-item").first().trigger("focus");
        }));
    }

    function syncMobileDrawer(dock) {
        const open = mobile.matches && dock?.enabled && dock.is_open && !dock.is_pinned;
        document.body.classList.toggle("vector-mobile-menu-open", !!open);
        if (scrim) scrim.hidden = !open;
        if (!dock?.$dock?.length) return;
        dock.$dock.attr("role", mobile.matches ? "dialog" : "navigation");
        dock.$dock.attr("aria-label", __("Workspace"));
        if (open) dock.$dock.attr("aria-modal", "true");
        else dock.$dock.removeAttr("aria-modal");
    }

    function syncSidebarIcons() {
        const expanded = !!frappe.app?.sidebar?.sidebar_expanded;
        document.querySelectorAll(".sidebar-collapse-arrow").forEach(button => button.remove());
        document.querySelectorAll(".sidebar-toggle-btn").forEach(button => {
            button.setAttribute("aria-expanded", String(expanded));
            button.setAttribute("aria-label", expanded ? __("Collapse sidebar") : __("Expand sidebar"));
            const icon = button.querySelector(".sidebar-toggle-icon use");
            if (icon) icon.setAttribute("href", `#icon-panel-left-${expanded ? "close" : "open"}`);
        });
    }

    function position(dock = currentDock()) {
        if (!mobile.matches || !dock?.enabled || dock.is_pinned) return;
        const button = [...buttons].find(button => !button.hidden && button.getClientRects().length);
        if (!button) return;
        const rect = button.getBoundingClientRect();
        // Leave the current title and its menu button visible above the drawer.
        document.documentElement.style.setProperty("--vector-mobile-menu-top",
            `${Math.max(0, Math.ceil(rect.bottom + 8))}px`);
    }
    function schedulePosition() {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => position());
    }

    function install(page) {
        if (!page.$title_area?.length || page.vector_workspace_chip) return;
        const button = document.createElement("button");
        button.type = "button";
        button.hidden = true;
        button.className = "btn-reset vector-workspace-chip";
        button.setAttribute("aria-controls", "desk-dock");
        const actionGlyph = document.createElement("span");
        actionGlyph.className = "vector-workspace-chip-action";
        actionGlyph.innerHTML = frappe.utils.icon("panel-top-open", "md");
        actionGlyph.setAttribute("aria-hidden", "true");
        button.append(actionGlyph);
        const label = document.createElement("span");
        label.className = "vector-workspace-chip-label";
        label.textContent = __("Workspace");
        const arrow = document.createElement("span");
        arrow.className = "vector-workspace-chip-chevron";
        arrow.innerHTML = frappe.utils.icon("chevron-down", "xs");
        button.append(label, arrow);
        page.$title_area[0].before(button);
        page.vector_workspace_chip = button;
        buttons.add(button);
        button.addEventListener("click", event => {
            event.stopPropagation();
            const dock = currentDock();
            if (!dock?.enabled) return;
            if (dock.is_open) { dock.close(); return; }
            dock.opener = button;
            position(dock);
            dock.open();
            if (event.detail === 0 || mobile.matches) {
                focusWorkspace(dock);
            }
        });
        page.wrapper.on("show.vectorWorkspaceChip", () => sync(currentDock()));
        page.wrapper.on("hide.vectorWorkspaceChip", () => currentDock()?.close());
        new ResizeObserver(schedulePosition).observe(page.$title_area[0].parentElement);
    }

    function sync(dock) {
        syncSidebarIcons();
        if (!dock) return;
        Object.values(frappe.ui.pages || {}).forEach(install);
        for (const button of buttons) {
            button.hidden = !mobile.matches || !dock.enabled || dock.is_pinned;
            button.setAttribute("aria-expanded", String(dock.is_open));
            button.setAttribute("aria-pressed", String(dock.is_open));
            button.querySelector(".vector-workspace-chip-action use")?.setAttribute(
                "href", `#icon-panel-top-${dock.is_open ? "close" : "open"}`);
            const action = dock.is_open ? __("Close app dock") : __("Open the app dock");
            button.setAttribute("aria-label", `${__("Workspace")}: ${action}`);
        }
        syncMobileDrawer(dock);
        position(dock);
        schedulePosition();
    }

    function setup(dock) {
        if (!dock.$dock?.length || dock.vector_workspace_ready) return;
        dock.vector_workspace_ready = true;
        const element = dock.$dock[0];
        const close = document.createElement("button");
        close.type = "button";
        close.className = "btn-reset vector-mobile-menu-close";
        close.setAttribute("aria-label", __("Close"));
        close.innerHTML = frappe.utils.icon("panel-top-close", "md");
        close.addEventListener("click", () => dock.close());
        element.querySelector(".dock-logo").append(close);
        if (!scrim) {
            scrim = document.createElement("div");
            scrim.className = "vector-mobile-menu-scrim";
            scrim.hidden = true;
            scrim.setAttribute("aria-hidden", "true");
            scrim.addEventListener("click", () => currentDock()?.close());
            document.body.append(scrim);
        }
        // Disable native invisible edge reveal; only explicit activation opens it.
        $(document).off(".dock-edge").off(".dock-reveal");
        dock.$dock.off("focusout.dock-reveal");
        $(document).on("click.dock-reveal", event => {
            // The account dropdown is portalled outside the workspace. Keep its
            // trigger available while the user interacts with that menu.
            if (event.target.closest(".es-menu") || element.querySelector('.dock-user[aria-expanded="true"]')) return;
            if (!element.contains(event.target) && !event.target.closest(".vector-workspace-chip")) dock.close();
        });
        document.addEventListener("keydown", event => {
            // The native user dropdown is portalled outside the drawer. Let it
            // handle Escape and Tab before restoring the drawer's focus boundary.
            if (dock.is_open
                && element.querySelector('.dock-user[aria-expanded="true"]')) {
                if (event.key === "Tab" && mobile.matches) requestAnimationFrame(() => {
                    if (dock.is_open && !element.contains(document.activeElement)) {
                        element.querySelector(".dock-user-button")?.focus();
                    }
                });
                return;
            }
            if (event.key === "Tab" && mobile.matches && dock.is_open && !dock.is_pinned) {
                const targets = [...element.querySelectorAll("button, a[href], [tabindex='0']")]
                    .filter(el => !el.disabled && el.getClientRects().length && !el.closest("[inert]"));
                const first = targets[0], last = targets.at(-1);
                if (first && ((event.shiftKey ? document.activeElement === first
                    : document.activeElement === last) || !element.contains(document.activeElement))) {
                    event.preventDefault();
                    (event.shiftKey ? last : first)?.focus();
                }
                return;
            }
            if (event.key !== "Escape" || !dock.is_open || dock.is_pinned) return;
            event.preventDefault();
            event.stopPropagation();
            dock.close();
        }, true);
    }

    prototype.setup_reveal = function () { setup(this); };
    const toggleFromKeyboard = prototype.toggle_from_keyboard;
    prototype.toggle_from_keyboard = function (...args) {
        const result = toggleFromKeyboard.apply(this, args);
        if (this.is_open) focusWorkspace(this);
        return result;
    };
    const open = prototype.open;
    prototype.open = function (...args) {
        if (mobile.matches && this.enabled && !this.is_open) {
            frappe.app?.sidebar?.close();
            this.opener ||= visibleButton();
            position(this);
        }
        const result = open.apply(this, args);
        if (mobile.matches && this.is_open && !this.holds_focus()) {
            focusWorkspace(this);
        }
        return result;
    };
    const shouldPin = prototype.should_pin;
    prototype.should_pin = function (...args) {
        return mobile.matches ? false : shouldPin.apply(this, args);
    };
    for (const method of ["refresh", "apply_open_state", "apply_pin"]) {
        const original = prototype[method];
        prototype[method] = function (...args) {
            const result = original.apply(this, args);
            setup(this);
            sync(this);
            return result;
        };
        prototype[method].vectorWorkspaceChip = true;
    }
    for (const method of ["setup_page", "render_breadcrumbs"]) {
        const original = frappe.ui.Page.prototype[method];
        frappe.ui.Page.prototype[method] = function (...args) {
            const result = original.apply(this, args);
            install(this);
            sync(currentDock());
            return result;
        };
    }
    function refit() {
        const dock = currentDock();
        if (!dock) return;
        setup(dock);
        dock.apply_pin();
        if (!dock.is_pinned) dock.close();
        sync(dock);
    }
    mobile.addEventListener("change", refit);
    // Touch only: mouse hover and swipes starting inside page controls keep their
    // native behavior. Vertical scrolling and multi-touch never reveal the drawer.
    let gesture;
    let suppressClickUntil = 0;
    document.addEventListener("touchstart", event => {
        gesture = null;
        // A fresh touch is intentional; only suppress the swipe's own ghost click.
        suppressClickUntil = 0;
        const dock = currentDock();
        if (!mobile.matches || !dock?.enabled || event.touches.length !== 1
            || document.querySelector(".modal.show, .modal.in, .es-menu[data-state='open']")
            || frappe.app?.sidebar?.sidebar_expanded) return;
        const touch = event.touches[0];
        if (!dock.is_open && (touch.clientX > 32 || event.target.closest(
            "input, textarea, select, [contenteditable], .datatable, .grid-body"))) return;
        gesture = { x: touch.clientX, y: touch.clientY, time: performance.now(),
            open: dock.is_open, horizontal: false };
    }, { passive: true });
    document.addEventListener("touchmove", event => {
        if (!gesture) return;
        if (event.touches.length !== 1) { gesture = null; return; }
        const dx = event.touches[0].clientX - gesture.x;
        const dy = event.touches[0].clientY - gesture.y;
        if (!gesture.horizontal && Math.abs(dy) > 10 && Math.abs(dy) >= Math.abs(dx)) {
            gesture = null;
            return;
        }
        if (Math.abs(dx) > 12 && Math.abs(dx) > Math.abs(dy) * 1.5
            && (gesture.open ? dx < 0 : dx > 0)) gesture.horizontal = true;
        if (gesture.horizontal && event.cancelable) event.preventDefault();
    }, { passive: false });
    document.addEventListener("touchend", event => {
        const start = gesture;
        gesture = null;
        const touch = event.changedTouches[0];
        const dock = currentDock();
        if (!start || !touch || !mobile.matches || !dock?.enabled
            || event.touches.length || performance.now() - start.time > 900) return;
        const dx = touch.clientX - start.x, dy = touch.clientY - start.y;
        if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5
            || (start.open ? dx >= 0 : dx <= 0)) return;
        suppressClickUntil = performance.now() + 400;
        start.open ? dock.close() : dock.open();
    }, { passive: true });
    document.addEventListener("touchcancel", () => { gesture = null; }, { passive: true });
    document.addEventListener("click", event => {
        if (event.detail !== 0 && performance.now() < suppressClickUntil) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }, true);
    window.addEventListener("resize", schedulePosition);
    window.visualViewport?.addEventListener("resize", schedulePosition);
    window.visualViewport?.addEventListener("scroll", schedulePosition);
    $(refit);
    $(document).on("app_ready.vectorWorkspaceChip", refit);
    $(document).on("sidebar-expand.vectorWorkspaceChip", syncSidebarIcons);
})();
