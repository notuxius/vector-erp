// Navigation editors load on demand and render raw labels in their list/preview.
// Translate only the rendered text so saving preserves labels and route identities.
(() => {
    if (frappe.boot.lang !== "uk") return;

    // List breadcrumbs use the DocType description as an untranslated hover title.
    const list = frappe.views.BaseList.prototype;
    const breadcrumbs = list.get_breadcrumbs;
    list.get_breadcrumbs = function (...args) {
        return breadcrumbs.apply(this, args).map(item => item.title === this.meta?.description
            ? { ...item, title: __(item.title) } : item);
    };

    function setupEditors() {
        for (const [prototype, method] of [
            [frappe.ui.ArrangementEditor?.prototype, "item"],
            [frappe.ui.ArrangementEditor?.prototype, "preview_item"],
            [frappe.ui.SidebarManager?.prototype, "preview_item"],
        ]) {
            const original = prototype?.[method];
            if (!original || original.vectorNavigationTranslation) continue;
            prototype[method] = function (...args) {
                const result = original.apply(this, args);
                result.find(".ws-item-label, .ws-preview-section")
                    .addBack(".ws-item-label, .ws-preview-section").each(function () {
                        $(this).text(__($(this).text()));
                    });
                return result;
            };
            prototype[method].vectorNavigationTranslation = true;
        }
    }

    // Install before either callback-style or promise-style callers open an editor.
    const require = frappe.require;
    frappe.require = function (items, callback) {
        const assets = Array.isArray(items) ? items : [items];
        if (!assets.some(item => /(?:^|\/)arrangement_editor\.bundle(?:\.[^/]+)?\.js(?:\?.*)?$/.test(item))) {
            return require.apply(this, arguments);
        }
        return require.call(this, items, function (...args) {
            setupEditors();
            return callback?.apply(this, args);
        });
    };
    setupEditors();
})();
