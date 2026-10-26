// Keep each navigation list independently scrollable without a visible scrollbar.
(() => {
    const mounted = new WeakSet();

    function mount(panel, viewport, contentSelector) {
        if (!panel || !viewport || mounted.has(viewport)) return;
        mounted.add(viewport);
        if (panel.matches(".body-sidebar")) {
            // Read overflow and text at hover time, including newly rendered
            // sections and labels affected by sidebar width changes.
            frappe.ui.Tooltip.delegate(panel,
                ".sidebar-item-label, .header-title",
                { only_on_overflow: true, side: "right", text_align: "start" });
        }
        const horizontal = () => panel.id === "desk-dock"
            && document.body.classList.contains("vector-navigation-top");
        const frame = document.createElement("div");
        frame.className = "vector-navigation-scroll";
        viewport.before(frame);
        frame.append(viewport);
        viewport.classList.add("vector-navigation-scroll-viewport");

        const buttons = [-1, 1].map(direction => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "btn-reset vector-navigation-scroll-arrow";
            button.hidden = true;
            const label = __(direction < 0 ? "Scroll up" : "Scroll down");
            frappe.vector_tooltip(button, label);
            button.setAttribute("aria-label", label);
            button.innerHTML = frappe.utils.icon(direction < 0 ? "chevron-up" : "chevron-down", "sm");
            button.addEventListener("click", () => {
                if (button.getAttribute("aria-disabled") === "true") return;
                viewport.scrollBy({
                    [horizontal() ? "left" : "top"]: direction * Math.max(40,
                        (horizontal() ? viewport.clientWidth : viewport.clientHeight) * 0.75),
                    behavior: frappe.vector_animations.enabled() ? "smooth" : "instant",
                });
            });
            direction < 0 ? frame.prepend(button) : frame.append(button);
            return button;
        });

        let pending = false;
        function schedule() {
            if (pending) return;
            pending = true;
            requestAnimationFrame(update);
        }
        function update() {
            pending = false;
            // Compare against the space WITHOUT arrows, so adding them cannot
            // make a formerly overflowing list permanently keep its arrows.
            const sideways = horizontal();
            const size = sideways ? frame.clientWidth : frame.clientHeight;
            const extent = sideways ? viewport.scrollWidth : viewport.scrollHeight;
            const offset = sideways ? viewport.scrollLeft : viewport.scrollTop;
            const visible = sideways ? viewport.clientWidth : viewport.clientHeight;
            const overflow = size > 0 && extent > size + 1;
            for (const [index, button] of buttons.entries()) {
                button.hidden = !overflow;
                const label = sideways
                    ? (frappe.boot.lang === "uk" ? ["Прокрутити ліворуч", "Прокрутити праворуч"][index]
                        : ["Scroll left", "Scroll right"][index])
                    : __(index === 0 ? "Scroll up" : "Scroll down");
                frappe.vector_tooltip(button, label);
                button.setAttribute("aria-label", label);
            }
            // Native disabled blurs a focused button, triggering the floating
            // dock's focusout dismissal. Keep focus at either scroll boundary.
            buttons[0].setAttribute("aria-disabled", String(offset <= 1));
            buttons[1].setAttribute("aria-disabled",
                String(offset + visible >= extent - 1));

            // Hidden labels still name compact links and section controls.
            viewport.querySelectorAll(".item-anchor").forEach(anchor => {
                const labelElement = anchor.querySelector(".sidebar-item-label");
                const label = labelElement?.textContent.trim();
                if (!label) return;
                const hidden = !labelElement.getClientRects().length
                    || getComputedStyle(labelElement).visibility === "hidden";
                for (const element of [anchor, ...anchor.querySelectorAll("button")]) {
                    frappe.vector_tooltip(element, hidden ? label : "");
                    if (element.getAttribute("aria-label") !== label) element.setAttribute("aria-label", label);
                }
            });
        }

        viewport.addEventListener("scroll", schedule, { passive: true });
        window.addEventListener("vector-navigation-position", schedule);
        // Wheel anywhere over the panel, including its fixed header and arrows.
        // Within the list keep native wheel/trackpad scrolling and touch behavior.
        panel.addEventListener("wheel", event => {
            if (horizontal()) {
                if (event.ctrlKey || event.deltaX || !event.deltaY
                    || viewport.scrollWidth <= viewport.clientWidth + 1) return;
                event.preventDefault();
                const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientWidth : 1;
                viewport.scrollBy({ left: event.deltaY * unit, behavior: "instant" });
                return;
            }
            if (event.ctrlKey || !event.deltaY || viewport.scrollHeight <= viewport.clientHeight + 1) return;
            if (viewport.contains(event.target)) return;
            event.preventDefault();
            const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientHeight : 1;
            viewport.scrollBy({ top: event.deltaY * unit, behavior: "instant" });
        }, { passive: false });

        const resize = new ResizeObserver(schedule);
        resize.observe(frame);
        resize.observe(viewport);
        let observedContent = [];
        function observeContent() {
            for (const element of observedContent) resize.unobserve(element);
            observedContent = [...viewport.querySelectorAll(contentSelector)];
            for (const element of observedContent) resize.observe(element);
            schedule();
        }
        new MutationObserver(observeContent).observe(viewport, {
            childList: true, subtree: true, attributes: true, attributeFilter: ["class", "style"],
        });
        observeContent();
    }

    function setup() {
        const dock = document.querySelector("#desk-dock");
        const items = dock?.querySelector(".dock-items");
        let dockViewport = dock?.querySelector(".vector-dock-viewport");
        if (items && !dockViewport) {
            // Return / Dashboard is the first scrolling destination. Keep the
            // native items container intact because render_entries empties it.
            dockViewport = document.createElement("div");
            dockViewport.className = "vector-dock-viewport";
            const logo = dock.querySelector(".dock-logo");
            logo.before(dockViewport);
            dockViewport.append(logo, items);
        }
        mount(dock, dockViewport, ".dock-logo, .dock-items, .dock-item");
        const sidebar = document.querySelector(".body-sidebar");
        mount(sidebar, sidebar?.querySelector(".body-sidebar-top"), ".sidebar-items");
    }
    for (const [prototype, method] of [
        [frappe.ui.Dock?.prototype, "make"],
        [frappe.ui.Sidebar?.prototype, "make_dom"],
    ]) {
        const original = prototype?.[method];
        if (!original || original.vectorNavigationScroll) continue;
        prototype[method] = function (...args) {
            const result = original.apply(this, args);
            setup();
            return result;
        };
        prototype[method].vectorNavigationScroll = true;
    }
    $(setup);
    $(document).on("app_ready.vectorNavigationScroll", setup);
})();
