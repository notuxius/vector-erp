/* Let users leave POS setup without submitting an opening entry. */
(() => {
    const prototype = frappe.ui.Dialog.prototype;
    const show = prototype.show;
    if (show.vectorPosNavigation) return;

    const isPOS = () => frappe.get_route()?.[0] === "point-of-sale";
    let openingDialog;

    function isOpeningDialog(dialog) {
        return dialog.static && dialog.title === __("Create POS Opening Entry") &&
            dialog.fields_dict.company?.df.options === "Company" &&
            dialog.fields_dict.pos_profile?.df.options === "POS Profile" &&
            dialog.fields_dict.balance_details?.df.fieldtype === "Table";
    }

    prototype.show = function () {
        if (!isOpeningDialog(this)) return show.apply(this, arguments);

        // The opening-entry request can finish after the user has already left POS.
        if (!isPOS()) {
            this.hide();
            return this;
        }
        openingDialog = this;
        if (!this.vectorPosNavigation) {
            this.vectorPosNavigation = true;
            const previous = [...(frappe.get_prev_route() || [])];
            const destination = previous.length && previous[0] !== "point-of-sale"
                ? previous : [];
            let leaving = false;
            const leave = () => {
                // Submission owns the frozen UI until its request finishes.
                if (leaving || this.get_primary_btn().attr("aria-busy") === "true") return;
                leaving = true;
                this.hide();
                if (isPOS()) {
                    // Replace the unfinished POS route so Back does not reopen it.
                    frappe.route_flags.replace_route = true;
                    frappe.set_route(destination);
                }
            };
            this.set_secondary_action_label(__("Back"));
            this.set_secondary_action(leave);
            this.get_secondary_btn().addClass("vector-pos-back");
            this.get_close_btn().show().attr("aria-label", __("Close"));
            // Handle explicit cancellation only: native hide() also runs after Submit
            // and on browser navigation, neither of which should change the route.
            this.get_close_btn()[0].addEventListener("click", (event) => {
                event.preventDefault();
                event.stopImmediatePropagation();
                leave();
            }, true);
            this.$wrapper[0].addEventListener("keydown", (event) => {
                if (event.key !== "Escape" || window.cur_dialog !== this ||
                    this.$wrapper.find(".awesomplete input[aria-expanded='true']").length ||
                    frappe.ui.form.get_open_grid_form?.()) return;
                event.preventDefault();
                event.stopImmediatePropagation();
                leave();
            }, true);
        }
        return show.apply(this, arguments);
    };
    prototype.show.vectorPosNavigation = true;

    frappe.router.on("change", () => {
        if (!openingDialog) return;
        if (!isPOS()) {
            if (openingDialog.display || openingDialog.is_visible) openingDialog.hide();
        } else if (!openingDialog.display && window.cur_pos && !cur_pos.pos_opening) {
            // Upstream refresh only retries after scanner setup. Cancelling the
            // opening dialog happens before that setup, so retry on a later visit.
            cur_pos.check_opening_entry();
        }
    });
})();
