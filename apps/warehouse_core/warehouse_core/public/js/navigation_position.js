// Desktop navigation stays attached at the top or left; mobile uses its drawer.
(() => {
    const prototype = frappe.ui.Dock?.prototype;
    if (!prototype || prototype.should_pin.vectorNavigationPosition) return;
    const desktop = window.matchMedia("(min-width: 768px)");
    const key = `vector-navigation-position:${frappe.session.user || frappe.boot.user?.name}`;
    let position = "top";
    try {
        if ((localStorage.getItem(key) ?? localStorage.getItem("vector-navigation-position:undefined")) === "left") {
            position = "left";
        }
    } catch (_) {}
    const atTop = () => desktop.matches && position === "top";
    prototype.should_pin = function () {
        // Ignore legacy Floating preferences without rewriting user records.
        return desktop.matches && this.enabled;
    };
    prototype.should_pin.vectorNavigationPosition = true;
    const setPinned = prototype.set_pinned;
    prototype.set_pinned = function () {
        return setPinned.call(this, true);
    };

    let actions;
    let actionHost;
    let labelFrame;
    const labelObserver = new ResizeObserver(scheduleLabels);
    const actionObserver = new MutationObserver(records => {
        if (records.some(record => !actions.contains(record.target))) scheduleLabels();
    });
    const preparedActions = new WeakSet();
    function scheduleLabels() {
        cancelAnimationFrame(labelFrame);
        labelFrame = requestAnimationFrame(fitLabels);
    }
    function fitLabels() {
        if (!actions?.isConnected || !actionHost?.getClientRects().length) return;
        const dock = actionHost.id === "desk-dock" && atTop() ? actionHost : null;
        // Probe the complete name first on each resize; the attribute is not
        // observed by actionObserver, so our own fitting cannot reschedule it.
        dock?.setAttribute("data-vector-user-name", "");
        // Measure with labels present, then collapse both together if any peer
        // would overflow or lose text. This follows translations and page actions,
        // as well as viewport, sidebar and compact/full-width changes.
        actions.classList.add("vector-actions-labeled");
        function fits() {
            const style = getComputedStyle(actionHost);
            const peers = [...actionHost.children].filter(el => el.getClientRects().length
                && getComputedStyle(el).position !== "absolute"
                && getComputedStyle(el).position !== "fixed");
            const needed = peers.reduce((total, el) => {
                const css = getComputedStyle(el);
                let width = Math.max(el.getBoundingClientRect().width, el.scrollWidth);
                if (dock && el.matches(".vector-navigation-scroll")) {
                    // Ignore temporary scroll arrows. Their visibility follows
                    // the final layout, not the name-width probe in this frame.
                    const items = el.querySelector(".dock-items");
                    const row = getComputedStyle(items);
                    const children = [...items.children].filter(child => child.getClientRects().length);
                    width = children.reduce((sum, child) => {
                        const style = getComputedStyle(child);
                        return sum + Math.max(child.getBoundingClientRect().width, child.scrollWidth)
                            + (parseFloat(style.marginLeft) || 0) + (parseFloat(style.marginRight) || 0);
                    }, 0) + (parseFloat(row.columnGap) || 0) * Math.max(0, children.length - 1)
                        + (parseFloat(row.paddingLeft) || 0) + (parseFloat(row.paddingRight) || 0);
                    const viewport = el.querySelector(".vector-dock-viewport");
                    const logo = viewport?.querySelector(".dock-logo");
                    if (logo) {
                        const css = getComputedStyle(logo);
                        width += Math.max(logo.getBoundingClientRect().width, logo.scrollWidth)
                            + (parseFloat(css.marginLeft) || 0) + (parseFloat(css.marginRight) || 0)
                            + (parseFloat(getComputedStyle(viewport).columnGap) || 0);
                    }
                }
                return total + width
                    + (parseFloat(css.marginLeft) || 0) + (parseFloat(css.marginRight) || 0);
            }, 0) + (parseFloat(style.columnGap) || 0) * Math.max(0, peers.length - 1);
            const available = actionHost.clientWidth
                - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0);
            const clipped = peers.some(el => [...el.querySelectorAll(
                ".es-button__label, .dock-item-label"
            )].some(label => label.getClientRects().length
                && label.scrollWidth > label.getBoundingClientRect().width + 1)
                || [...el.querySelectorAll(dock ? ".standard-actions > *" : ".standard-actions > *, .dock-items")].some(control =>
                    control.getClientRects().length && (control.getBoundingClientRect().left < el.getBoundingClientRect().left - 1
                        || control.scrollWidth > control.getBoundingClientRect().width + 1)));
            return needed <= available + 1 && !clipped;
        }
        if (!fits()) actions.classList.remove("vector-actions-labeled");
        // Prefer the user name over optional utility captions, but never make
        // module navigation scroll just to show it.
        if (dock && !fits()) {
            dock.removeAttribute("data-vector-user-name");
            actions.classList.add("vector-actions-labeled");
            if (!fits()) actions.classList.remove("vector-actions-labeled");
        }
        positionNotifications();
    }
    function positionNotifications() {
        if (!actions?.isConnected) return;
        const bottom = actions.getBoundingClientRect().bottom;
        document.documentElement.style.setProperty("--vector-notifications-top", `${bottom + 8}px`);
    }
    function placeActions() {
        const dock = document.querySelector("#desk-dock");
        const band = document.querySelector(".body-sidebar .standard-items-band");
        if (!band) return;
        if (!actions) {
            actions = document.createElement("div");
            actions.className = "vector-top-actions";
            new ResizeObserver(positionNotifications).observe(actions);
        }
        const showInDock = atTop() && dock && !dock.classList.contains("hidden");
        const page = frappe.container?.page;
        const desktopHeader = page?.querySelector(".desktop-navbar .desktop-avatar")?.parentElement;
        const host = showInDock ? dock : desktopHeader
            || page?.querySelector(".page-head .standard-items-section");
        if (host && actions.parentElement !== host) {
            if (showInDock) dock.querySelector(".dock-user").before(actions);
            else host.prepend(actions);
        }
        if (host && actionHost !== host) {
            labelObserver.disconnect();
            actionObserver.disconnect();
            actionHost = host;
            labelObserver.observe(host);
            for (const child of host.children) labelObserver.observe(child);
            actionObserver.observe(host, {
                childList: true, subtree: true, characterData: true,
                attributes: true, attributeFilter: ["class", "style"],
            });
        }
        for (const selector of [".navbar-modal-search-mobile", ".sidebar-notification"]) {
            const item = band.querySelector(selector) || actions.querySelector(selector);
            if (!item) continue;
            if (!preparedActions.has(item)) {
                preparedActions.add(item);
                // Native rows are anchors without hrefs; make them keyboard buttons.
                const anchor = item.querySelector(".item-anchor");
                if (anchor) {
                    anchor.setAttribute("role", "button");
                    anchor.tabIndex = 0;
                    anchor.addEventListener("keydown", event => {
                        if (event.key !== "Enter" && event.key !== " ") return;
                        event.preventDefault();
                        anchor.click();
                    });
                }
            }
            const anchor = item.querySelector(".item-anchor");
            const label = item.getAttribute("item-name")
                || __(selector === ".sidebar-notification" ? "Notification" : "Search");
            anchor?.setAttribute("aria-label", label);
            // These top-panel actions have no hover tooltip; keep accessible names.
            if (anchor) frappe.vector_tooltip(anchor, "");
            if (item.parentElement !== actions) actions.append(item);
        }
        // All apps hides the sidebar mount; the top-panel bell must still work there.
        const notifications = document.querySelector(".sidebar-panel-notifications");
        if (notifications && notifications.parentElement !== document.body) {
            document.body.append(notifications);
        }
        positionNotifications();
        scheduleLabels();
    }

    // Move the existing controls after their native handlers and notification host
    // have been created. Reparenting preserves search, unread counts and permissions.
    for (const [owner, method] of [
        [frappe.ui.Sidebar.prototype, "add_standard_items"],
        [prototype, "refresh"],
    ]) {
        const original = owner[method];
        owner[method] = function (...args) {
            const result = original.apply(this, args);
            placeActions();
            return result;
        };
    }
    function watchPage(page) {
        if (page.vector_top_actions_ready) return;
        page.vector_top_actions_ready = true;
        page.wrapper.on("show.vectorTopActions", placeActions);
        if (page.wrapper.attr("data-page-route") === "desktop") {
            // All apps rebuilds its own top panel when revisited.
            new MutationObserver(placeActions).observe(page.wrapper[0], { childList: true, subtree: true });
        }
    }
    const setupPage = frappe.ui.Page.prototype.setup_page;
    frappe.ui.Page.prototype.setup_page = function (...args) {
        const result = setupPage.apply(this, args);
        watchPage(this);
        return result;
    };
    function sync() {
        document.body.classList.toggle("vector-navigation-top", atTop());
        placeActions();
        window.dispatchEvent(new Event("vector-navigation-position"));
    }
    frappe.vector_navigation_position = {
        get: () => position,
        set(value) {
            if (!["left", "top"].includes(value)) return;
            position = value;
            try { localStorage.setItem(key, value); } catch (_) {}
            sync();
            frappe.app?.sidebar?.dock?.apply_pin();
        },
    };
    desktop.addEventListener("change", () => {
        sync();
        frappe.app?.sidebar?.dock?.apply_pin();
    });
    sync();
    Object.values(frappe.ui.pages || {}).forEach(watchPage);
    window.addEventListener("resize", positionNotifications);
    document.addEventListener("scroll", positionNotifications, true);
    $(document).on("app_ready.vectorNavigationPosition page-change.vectorNavigationPosition", () => {
        sync();
        // Page containers finish their show/hide cycle after page-change.
        requestAnimationFrame(placeActions);
    });
})();
