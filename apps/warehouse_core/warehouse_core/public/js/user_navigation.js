// Both navigation surfaces can own the user menu. Also update existing controls
// on refresh, since the sidebar/dock can be created before app assets run.
(() => {
    // Dock rows have their own captions, including freshly fetched arrangements.
    // Change only the default Setup caption, preserving user-defined titles.
    const dockEntry = frappe.ui.Sidebar.prototype.dock_entry;
    frappe.ui.Sidebar.prototype.dock_entry = function (row) {
        const entry = dockEntry.call(this, row);
        if (entry?.module === "Setup" && row.link_type === "Sidebar"
            && (!row.title || row.title === "Setup")) {
            entry.label = __("Administration");
        }
        return entry;
    };
    const returnIcon = "/assets/warehouse_core/images/mtech-mark.svg";
    function addReturnArrow($logo) {
        if (!$logo.children(".vector-return-arrow").length) {
            $("<span>", { class: "vector-return-arrow", "aria-hidden": "true" })
                .html(frappe.utils.icon("arrow-left", "xs")).appendTo($logo);
        }
    }
    // Resolve visibility and destination on open for both existing and new menus.
    const dropdown = frappe.ui.Dropdown?.prototype;
    const openMenu = dropdown?.open;
    if (openMenu && !openMenu.vectorFrameworkLink) {
        dropdown.open = function (...args) {
            const sidebarMenu = this.$trigger?.is(".body-sidebar .sidebar-header, .body-sidebar .dropdown-navbar-user");
            if (sidebarMenu) {
                this.side = "top";
            }
            if (this.$trigger?.is(".dock-user")) {
                // Account controls at the foot of either left panel open upward.
                this.side = document.body.classList.contains("vector-navigation-top") ? "right" : "top";
            }
            if (this.$trigger?.is(".dropdown-navbar-user, .dock-user") && Array.isArray(this.options)) {
                // Resolve page-specific and site help on every open, using the
                // native links/actions and visibility rules in both user menus.
                const help = frappe.app?.sidebar?.sidebar_header?.get_help_siblings() || [];
                const helpItem = help.some(section => section.options.length) ? {
                    name: "help", label: __("Help"), icon: "info", submenu: help,
                } : null;
                const returnToERP = frappe.app?.sidebar?.get_sidebar_app()?.app_name === "frappe";
                const targetApp = returnToERP ? "erpnext" : "frappe";
                const app = (frappe.boot.app_data || []).find(app =>
                    app.app_name === targetApp && app.on_apps_screen);
                const route = app && frappe.app?.sidebar?.app_landing_route(app);
                const insertLink = items => items.filter(item => !["vector-framework", "help"].includes(item.name))
                    .flatMap(item => {
                        if (Array.isArray(item.options)) return [{ ...item, options: insertLink(item.options) }];
                        if (item.name === "reload" && helpItem) return [item, helpItem];
                        if (item.name !== "settings" || !route) return [item];
                        return [{ name: "vector-framework",
                            label: returnToERP ? __("Return to MTech ERP") : __("Platform"),
                            icon: returnToERP ? undefined : "box",
                            image: returnToERP ? returnIcon : undefined,
                            css_class: returnToERP ? "vector-return-menu-item" : "", href: route }, item];
                    });
                this.options = insertLink(this.options);
            }
            const result = openMenu.apply(this, args);
            if (sidebarMenu) this.menu?.panels[0]?.panel.classList.add("vector-sidebar-dropdown");
            $(".es-menu__item.vector-return-menu-item .es-menu__image").each(function () {
                const $image = $(this);
                if (!$image.parent().hasClass("vector-return-logo")) {
                    $image.wrap('<span class="vector-return-logo" aria-hidden="true"></span>');
                }
                addReturnArrow($image.parent());
            });
            return result;
        };
        dropdown.open.vectorFrameworkLink = true;
    }

    // Filter nested touch/dock-less menus too, removing empty groups so no
    // orphan separator remains. Sidebar editing and module switching stay native.
    const header = frappe.ui.SidebarHeader?.prototype;
    // Initial header templates render this value directly; refresh translates it.
    // Translate the display result in both paths without changing module identity.
    const displayTitle = header?.get_display_title;
    if (displayTitle && !displayTitle.vectorFrameworkTitle) {
        header.get_display_title = function (...args) {
            const title = displayTitle.apply(this, args);
            return title === "Framework" || title === "Frappe Framework" ? __("Framework") : title;
        };
        header.get_display_title.vectorFrameworkTitle = true;
        frappe.app?.sidebar?.sidebar_header?.refresh();
    }
    const menuItems = header?.menu_items;
    if (menuItems && !menuItems.vectorApplicationLinks) {
        const withoutUserLinks = items => items.flatMap(item => {
            if (item.name === "all-apps" || item.name === "help") return [];
            const children = Array.isArray(item.options) ? "options"
                : Array.isArray(item.submenu) ? "submenu" : null;
            if (!children) return [item];
            const filtered = withoutUserLinks(item[children]);
            return filtered.length ? [{ ...item, [children]: filtered }] : [];
        });
        header.menu_items = function (...args) {
            return withoutUserLinks(menuItems.apply(this, args));
        };
        header.menu_items.vectorApplicationLinks = true;
    }

    function userName() {
        const user = frappe.boot.user || {};
        const language = (frappe.boot.lang || "en").replace("_", "-");
        const clean = value => (value || "").trim().replace(/\s+/g, " ");
        const first = clean(user.first_name);
        const middle = clean(user.middle_name);
        const last = clean(user.last_name);
        const fallback = frappe.session.user_fullname || frappe.session.user;
        // Do not infer a surname by splitting an unstructured display name.
        if (!last) return { short: fallback, full: fallback };
        const segmenter = new Intl.Segmenter(language, { granularity: "grapheme" });
        const initial = part => [...segmenter.segment(part)][0]?.segment.toLocaleUpperCase(language) || "";
        const initials = value => value.split(/\s+/).filter(Boolean)
            .map(part => part.split("-").filter(Boolean).map(piece => `${initial(piece)}.`).join("-"))
            .join(" ");
        const surnameFirst = language.split("-")[0] === "uk";
        return {
            short: (surnameFirst ? [last, initials(first), initials(middle)]
                : [initials(first), initials(middle), last]).filter(Boolean).join(" "),
            full: (surnameFirst ? [last, first, middle] : [first, middle, last])
                .filter(Boolean).join(" "),
        };
    }

    function setupHeaderLogo() {
        const sidebar = frappe.app?.sidebar;
        const panel = document.querySelector(".body-sidebar");
        const moduleMenu = panel?.querySelector(".sidebar-header");
        const footer = panel?.querySelector(".body-sidebar-bottom");
        // Move the existing trigger so its menu bindings and keyboard order follow
        // the visual layout. Keep both controls outside the scrolling module list.
        if (moduleMenu && footer && moduleMenu.nextElementSibling !== footer) {
            footer.before(moduleMenu);
        }
        const sidebarApp = sidebar?.get_sidebar_app()?.app_name || "";
        $(".body-sidebar").attr("data-vector-app", sidebarApp);
        const isERP = sidebarApp === "erpnext";
        const erp = (frappe.boot.app_data || []).find(app => app.app_name === "erpnext" && app.on_apps_screen);
        const returnRoute = sidebarApp === "frappe" && erp && sidebar.app_landing_route(erp);
        const route = returnRoute || (isERP ? "/desk/operations-overview" : "/desk");
        const label = returnRoute ? __("Return", null, "Navigation") : isERP ? __("Dashboard") : __("All apps");
        // Use a real link in the dock, retaining native keyboard and new-tab behavior.
        const $home = $("#desk-dock .dock-logo .shell-header");
        const active = isERP && frappe.get_route()?.[0] === "operations-overview";
        $home.attr({ href: route, "aria-label": label })
            .toggleClass("dock-item vector-dashboard-link", isERP || !!returnRoute)
            .toggleClass("vector-return-link", !!returnRoute)
            .toggleClass("active", active);
        if (active) $home.attr("aria-current", "page");
        else $home.removeAttr("aria-current");
        if (returnRoute) {
            const $logo = $home.find(".header-logo");
            if ($logo.find("img").attr("src") !== returnIcon) {
                $logo.empty().append($("<img>", { src: returnIcon, alt: "" }));
            }
            addReturnArrow($logo);
        } else if ($home.find(".header-logo img").attr("src") === returnIcon) {
            const app = (frappe.boot.app_data || []).find(app => app.app_name === sidebarApp);
            $home.find(".header-logo img").attr("src", app?.app_logo_url || "");
        }
        if (!returnRoute) $home.find(".vector-return-arrow").remove();
        if (isERP || returnRoute) {
            if (!$home.children(".vector-dashboard-label").length) {
                $("<span>", { class: "dock-item-label vector-dashboard-label", text: label }).appendTo($home);
            }
            $home.children(".vector-dashboard-label").text(label);
            $home.find(".header-logo").attr("aria-hidden", "true");
        } else {
            $home.children(".vector-dashboard-label").remove();
            $home.find(".header-logo").removeAttr("aria-hidden");
        }
        frappe.vector_navigation_logo.sync($home[0]);
        if (sidebar?.dock?.header_tooltip) sidebar.dock.header_tooltip.text = label;
        // The sidebar title still owns the module menu. Stop logo activation
        // before it reaches that trigger.
        $(".body-sidebar .sidebar-header .header-logo")
            .attr({ role: "link", tabindex: "0", "aria-label": label, title: label })
            .off(".vectorHeaderHome")
            .on("click.vectorHeaderHome keydown.vectorHeaderHome", function (event) {
                if (event.type === "keydown" && event.key !== "Enter") return;
                event.preventDefault();
                event.stopPropagation();
                frappe.set_route(route);
            });
    }

    function labelUserButtons() {
        const name = userName();
        if (!name.short) return;
        $("#desk-dock .dock-user-button, .body-sidebar .sidebar-user-button").each(function () {
            const $button = $(this);
            let $label = $button.children(".vector-user-label");
            if (!$label.length) {
                $label = $("<span>", { class: "vector-user-label" }).appendTo($button);
            }
            $label.text(name.short).attr("data-full-name", name.full);
            $button.attr("aria-label", `${__("User Menu")}: ${name.full}`);
            if ($button.is(".sidebar-user-button")) {
                let tooltip = $label.data("es-tooltip");
                if (!tooltip) {
                    frappe.ui.tooltip($label, { text: name.full, side: "right" });
                    tooltip = $label.data("es-tooltip");
                }
                tooltip.set_text(name.full);
                tooltip.only_on_overflow = true;
            }
            $button.addClass("vector-user-button");
        });
    }

    for (const [prototype, method] of [
        [frappe.ui.Dock?.prototype, "setup_user_menu"],
        [frappe.ui.Dock?.prototype, "refresh"],
        [frappe.ui.Sidebar?.prototype, "setup_user_menu"],
        [frappe.ui.SidebarHeader?.prototype, "make"],
        [frappe.ui.SidebarHeader?.prototype, "refresh"],
    ]) {
        const original = prototype?.[method];
        if (!original || original.vectorUserLabel) continue;
        prototype[method] = function (...args) {
            const result = original.apply(this, args);
            labelUserButtons();
            setupHeaderLogo();
            return result;
        };
        prototype[method].vectorUserLabel = true;
    }
    $(labelUserButtons);
    $(setupHeaderLogo);
    $(document).on("app_ready.vectorUserLabel", labelUserButtons);
    $(document).on("app_ready.vectorHeaderHome", setupHeaderLogo);
    frappe.router.on("change", setupHeaderLogo);
})();
