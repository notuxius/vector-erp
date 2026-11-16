(() => {
    const list = frappe.views.ListView.prototype;
    const setup = list.setup_realtime_updates;
    let subscribedList;

    // Frappe installs one list_update listener for the whole Desk, but caches
    // the "already subscribed" flag on each list when navigating between them.
    list.setup_realtime_updates = function () {
        if (this !== window.cur_list || frappe.get_route()[0] !== "List") return;
        if (subscribedList !== this) {
            if (subscribedList?.realtime_events_setup) subscribedList.disable_realtime_updates();
            subscribedList = this;
        }
        // Re-running setup on every render also clears pending document updates.
        if (!this.realtime_events_setup) return setup.apply(this, arguments);
    };

    const quickEntry = frappe.ui.form.QuickEntryForm.prototype;
    const openForm = quickEntry.open_form_if_not_list;
    quickEntry.open_form_if_not_list = function () {
        const [page, doctype] = frappe.get_route();
        const current = window.cur_list;
        if (page === "List" && doctype === this.doctype && current?.doctype === doctype) {
            // A committed save invalidates even an identical, very recent list query.
            // Use the existing list's filters, sorting and pagination.
            current.last_args = null;
            current.refresh();
        }
        return openForm.apply(this, arguments);
    };
})();
