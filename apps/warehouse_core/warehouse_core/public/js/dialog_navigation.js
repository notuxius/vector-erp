// Keep the Desk shell visible behind every desktop popup, including pages such
// as POS that normally hide it. Do not rewrite page flags or saved preferences.
(() => {
    const prototype = frappe.ui.Sidebar?.prototype;
    if (!prototype || prototype.page_allows_sidebar.vectorDialogNavigation) return;

    const desktop = window.matchMedia("(min-width: 768px)");
    const dialogs = new Set();
    const hasDialog = () => [...dialogs].some(dialog => dialog.isConnected &&
        !dialog.classList.contains("modal-minimize"));

    for (const name of ["page_allows_sidebar", "page_allows_dock"]) {
        const original = prototype[name];
        prototype[name] = function () {
            if (desktop.matches && this.current_page() && hasDialog()) return true;
            return original.apply(this, arguments);
        };
        prototype[name].vectorDialogNavigation = true;
    }

    const refresh = () => frappe.app?.sidebar?.refresh();
    // Bootstrap events cover standard, static, settings and nested dialogs.
    // Retain the shell until the final closing animation completes.
    // A first-time Bootstrap modal is detached during `show`; `shown` bubbles
    // after it has been attached. Reopened modals can refresh before animating.
    $(document).on("show.bs.modal.vectorDialogNavigation shown.bs.modal.vectorDialogNavigation", ".modal", function (event) {
        if (event.target !== this || event.isDefaultPrevented()) return;
        dialogs.add(this);
        refresh();
    }).on("hidden.bs.modal.vectorDialogNavigation", ".modal", function (event) {
        if (event.target !== this) return;
        dialogs.delete(this);
        refresh();
    });
    const toggleMinimize = frappe.ui.Dialog.prototype.toggle_minimize;
    frappe.ui.Dialog.prototype.toggle_minimize = function () {
        const result = toggleMinimize.apply(this, arguments);
        refresh();
        return result;
    };
    desktop.addEventListener("change", refresh);
})();
