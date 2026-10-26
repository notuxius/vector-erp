/* Add the route back through the permitted module/sidebar to the native page trail.
 * Keep stored page titles and the original document/list crumbs untouched.
 */
(() => {
    const page = frappe.ui.Page.prototype;
    const sidebar = frappe.ui.Sidebar.prototype;
    const original = page.get_breadcrumbs;
    if (original.vectorNavigation) return;

    page.get_breadcrumbs = function () {
        const items = original.call(this);
        const navigation = frappe.app?.sidebar;
        // Cached pages and dialog headers must never inherit the active route.
        if (!items.length || this !== frappe.get_current_page() || !navigation || this.hide_dock) {
            return items;
        }
        const route = frappe.get_route();
        if (route[0] === "operations-overview") return [{ label: __("Dashboard") }];
        const entity = navigation.entity_from_route(route);
        const kind = navigation.link_type_from_route(route);
        let shell = navigation.shell_for_route(route);
        // Frappe can keep Stock open while following a link to a sales order.
        // Teach the menu path that actually lists the destination in that case.
        const modules = navigation.get_modules_linking(entity, kind);
        if (modules.length && !modules.includes(shell)) shell = modules[0];
        const definition = frappe.boot.module_sidebars?.[shell];
        if (!definition) return items;

        const parents = [];

        const moduleTitle = __(definition.title || definition.label || shell);
        const landing = navigation.module_landing_route(shell);
        // The workspace already names the module in its own final crumb.
        if (!(route[0] === "Workspaces" && items[0]?.label === moduleTitle)) {
            parents.push({ label: moduleTitle, href: landing });
        }

        let section = null;
        for (const row of definition.items || []) {
            if (row.type === "Section Break") {
                section = row.label;
                continue;
            }
            if (!row.child) section = null;
            if (row.link_to === entity && row.link_type === kind) {
                // A sidebar group is a location cue, not a destination page.
                if (section) parents.push({ label: __(section) });
                break;
            }
        }
        return [...parents, ...items];
    };
    page.get_breadcrumbs.vectorNavigation = true;

    const render = page.render_breadcrumbs;
    page.render_breadcrumbs = function () {
        const result = render.apply(this, arguments);
        this.$breadcrumbs?.closest("nav").addClass("vector-navigation-breadcrumbs");
        // Keep full text available; the shared tooltip checks overflow on display.
        this.$breadcrumbs?.find(".es-breadcrumbs__item").each(function () {
            frappe.vector_tooltip(this, this.getAttribute("title")
                || this.querySelector(".es-breadcrumbs__label")?.textContent);
        });
        return result;
    };

    // Repaint on same-page navigation too (reports share a cached Page instance).
    const highlight = sidebar.highlight_active_item;
    sidebar.highlight_active_item = function () {
        const result = highlight.apply(this, arguments);
        frappe.get_current_page()?.render_breadcrumbs();
        return result;
    };
})();
