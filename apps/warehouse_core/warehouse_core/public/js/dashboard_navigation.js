// Dashboard belongs to the whole app, so it has no module prefix or selected module.
(() => {
    const isDashboard = () => frappe.get_route()?.[0] === "operations-overview";
    const router = frappe.router;
    const writeShell = router.write_shell_into_url;
    router.write_shell_into_url = function (...args) {
        if (!isDashboard()) return writeShell.apply(this, args);
        this.current_shell = null;
        const path = "/desk/operations-overview" + location.search + location.hash;
        if (location.pathname !== "/desk/operations-overview") {
            history.replaceState(history.state, "", path);
        }
    };
    const sidebar = frappe.ui.Sidebar.prototype;
    // A page-owned shell uses the native panel, sizing, dock and mobile drawer.
    // It exists only in this session; it is not a customizable database Sidebar.
    const shell = "Operations Overview";
    const definition = { name: shell, label: "Огляд", app: "erpnext",
        header_icon: "layout-dashboard", items: [], workspaces: [], vector_width_settings_only: true };
    frappe.boot.module_sidebars[shell] = definition;
    let root = null;
    let sections = [];
    let hasOverviewData = false;
    let active = null;
    let selected = null;
    let frame = 0;
    const observer = new ResizeObserver(schedule);
    const currentShell = sidebar.shell_for_current_route;
    sidebar.shell_for_current_route = function (route) {
        return route[0] === "operations-overview" ? shell : currentShell.call(this, route);
    };
    const canShow = sidebar.shell_can_show;
    sidebar.shell_can_show = function (name, route) {
        return name === shell ? route[0] === "operations-overview" : canShow.call(this, name, route);
    };
    const landing = sidebar.module_landing_route;
    sidebar.module_landing_route = function (name) {
        return name === shell ? "/desk/operations-overview" : landing.call(this, name);
    };
    const highlight = sidebar.highlight_active_item;
    sidebar.highlight_active_item = function (...args) {
        if (this.current_module !== shell) return highlight.apply(this, args);
        paint();
    };
    const make = sidebar.make_sidebar;
    sidebar.make_sidebar = function (...args) {
        if (this.current_module === shell) this.items = this.items.filter(item => item.item.standard);
        const result = make.apply(this, args);
        if (this.current_module === shell) {
            this.$items_container.attr("aria-label", "Розділи огляду");
            if (!definition.items.length) {
                this.$items_container.empty();
                if (hasOverviewData) {
                    this.$items_container.append($("<p>", { class: "vo-empty", text: "Немає доступних розділів." }));
                }
            }
            this.$items_container.find("a.item-anchor").each((index, anchor) => {
                const key = definition.items[index].section_key;
                anchor.removeAttribute("target");
                anchor.dataset.overviewSection = key;
                if (key === "attention") {
                    const suffix = anchor.querySelector(".sidebar-item-suffix");
                    suffix.id = "vo-attention-count";
                    suffix.classList.add("vo-attention-count");
                    suffix.setAttribute("aria-label", `Прострочених замовлень: ${suffix.textContent}`);
                    anchor.setAttribute("aria-describedby", suffix.id);
                }
                anchor.addEventListener("click", event => {
                    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
                    event.preventDefault();
                    event.stopPropagation();
                    navigate(key);
                });
            });
            paint();
        } else this.$items_container.removeAttr("aria-label");
        return result;
    };
    function paint() {
        if (!isDashboard() || frappe.app?.sidebar?.current_module !== shell) return;
        document.querySelectorAll(".body-sidebar [data-overview-section]").forEach(anchor => {
            const selected = anchor.dataset.overviewSection === active;
            anchor.classList.toggle("active-sidebar", selected);
            if (selected) anchor.setAttribute("aria-current", "location");
            else anchor.removeAttribute("aria-current");
        });
    }
    function offset() {
        const header = $(".page-head:visible")[0];
        return Math.max(0, header?.getBoundingClientRect().bottom || 0) + 16;
    }
    function scrollContainer(target) {
        // Desk temporarily hides this pane's overflow during startup and drawer
        // transitions, but it remains the content's scroll container.
        return target.closest(".main-section") || document.scrollingElement;
    }
    function navigate(key) {
        const target = root?.querySelector(`#vo-${key}`);
        if (!isDashboard() || !target) return;
        active = key;
        selected = key;
        if (frappe.is_mobile()) frappe.app.sidebar.close();
        const scroller = scrollContainer(target);
        // Scroll only the content pane. scrollIntoView can also scroll its outer
        // document and move the sticky page header behind the fixed module bar.
        scroller.scrollBy({ top: target.getBoundingClientRect().top - offset(), behavior: "instant" });
        target.focus({ preventScroll: true });
        history.replaceState(history.state, "", `${location.pathname}${location.search}#vo-${key}`);
        paint();
    }
    function schedule() {
        if (frame || !root || !isDashboard()) return;
        frame = requestAnimationFrame(() => { frame = 0; track(); });
    }
    function track() {
        if (!root?.isConnected || !isDashboard()) return;
        const positions = sections.map(section => ({ key: section.key,
            rect: root.querySelector(`#vo-${section.key}`)?.getBoundingClientRect() })).filter(s => s.rect);
        const boundary = offset() + Math.min(120, (innerHeight - offset()) * 0.2);
        const passed = positions.filter(s => s.rect.top <= boundary);
        const rowTop = passed.length ? Math.max(...passed.map(s => s.rect.top)) : positions[0]?.rect.top;
        const row = positions.filter(s => Math.abs(s.rect.top - rowTop) < 3);
        // Two domain cards can share a row. Preserve the clicked one while that
        // row is current; otherwise its first card is the reading-order default.
        active = row.find(s => s.key === active)?.key || row[0]?.key || null;
        const last = positions.at(-1);
        if (last && last.rect.bottom <= innerHeight && last.rect.top >= offset()) {
            // At the end of the page there may not be enough scroll range to
            // align Attention with the header. Keep an explicitly chosen section
            // while visible; otherwise scrolling to the end selects Shortcuts.
            active = positions.find(s => s.key === selected && s.rect.top >= offset() - 3
                && s.rect.top < innerHeight)?.key || last.key;
        }
        paint();
    }
    function setItems(data) {
        hasOverviewData = data != null;
        const count = data?.cards.filter(card => ["overdue", "late_purchases"].includes(card.key))
            .reduce((total, card) => total + Number(card.value), 0) || 0;
        sections = [
            ...(data?.tables.length ? [{ key: "attention", label: "Потребує уваги", icon: "bell", count }] : []),
            ...(data?.sections || []).map(section => ({ ...section, icon: {
                sales: "shopping-cart", accounting: "wallet", purchasing: "shopping-bag", stock: "package",
            }[section.key] })),
            ...(data?.links.length ? [{ key: "shortcuts", label: "Швидкий доступ", icon: "arrow-up-right" }] : [])];
        definition.items = sections.map(section => ({ type: "Link", link_type: "URL",
            url: `#vo-${section.key}`, label: section.label, icon: section.icon,
            section_key: section.key, suffix: section.count === undefined ? "" : String(section.count) }));
        const navigation = frappe.app?.sidebar;
        if (isDashboard() && navigation?.current_module === shell) {
            navigation.load_sidebar_data();
            navigation.make_sidebar();
        }
    }
    frappe.vector_overview_navigation = {
        capture() {
            const target = root?.querySelector(`#vo-${active}`);
            return target ? { key: active, top: target.getBoundingClientRect().top } : null;
        },
        mount(element, data, view = null) {
            observer.disconnect();
            const firstMount = root !== element;
            root = element;
            setItems(data);
            observer.observe(root);
            const requested = location.hash.replace(/^#vo-/, "");
            if (firstMount && sections.some(section => section.key === requested)) navigate(requested);
            else {
                const target = view && root.querySelector(`#vo-${view.key}`);
                if (target) {
                    active = view.key;
                    scrollContainer(target).scrollBy({ top: target.getBoundingClientRect().top - view.top, behavior: "instant" });
                }
                schedule();
            }
        },
        detach() {
            observer.disconnect();
            cancelAnimationFrame(frame);
            frame = 0;
            root = null;
            hasOverviewData = false;
            selected = null;
        },
    };
    setItems(null);
    document.addEventListener("scroll", schedule, { capture: true, passive: true });
    // User scrolling releases the clicked section's end-of-page preference.
    for (const event of ["wheel", "touchmove"]) document.addEventListener(event, () => { selected = null; }, { passive: true });
    document.addEventListener("keydown", event => {
        if (["ArrowDown", "ArrowUp", "PageDown", "PageUp", "Home", "End", " "].includes(event.key)) selected = null;
    });
    window.addEventListener("resize", schedule);
    const activeEntry = sidebar.is_active_entry;
    sidebar.is_active_entry = function (...args) {
        return isDashboard() ? false : activeEntry.apply(this, args);
    };
})();
