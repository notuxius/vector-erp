/* Ukrainian settings presentation. Stored configuration keys never change. */
(() => {
    if (frappe.boot.lang !== "uk" || frappe.ui.form.vectorSettingsUkrainian) return;
    frappe.ui.form.vectorSettingsUkrainian = true;

    frappe.boot.translated_doctypes = [...new Set([
        ...(frappe.boot.translated_doctypes || []), "Domain",
    ])];

    // Domain Settings builds a MultiCheck without translating its captions.
    // Repaint only labels; preserve checkbox data-unit and checked values.
    frappe.ui.form.on("Domain Settings", {
        refresh(frm) {
            const domains = frm.domains_multicheck;
            if (!domains) return;
            for (const option of domains.options || []) {
                option.$checkbox.find(".label-area").text(__(option.label));
            }
            domains.tooltip.text(__("Domains")).off("click").on("click", () => {
                frappe.utils.copy_to_clipboard(domains.df.fieldname);
            });
        },
    });

    const sidebar = frappe.ui.form.Sidebar.prototype;
    const refreshSidebar = sidebar.refresh;
    sidebar.refresh = function (...args) {
        const result = refreshSidebar.apply(this, args);
        if (this.frm.meta.issingle && this.frm.doc.name === this.frm.doctype) {
            // Keep data-copy and document identity intact, replacing only text.
            this.sidebar.find(".form-title-text > span").text(__(this.frm.doctype));
        }
        if (this.frm.doctype === "Sales Taxes and Charges Template") {
            this.sidebar.find(".form-title-text > span").text(__(this.frm.get_title()));
            this.sidebar.find(".form-name-copy > span").text(__(this.frm.doc.name));
            this.sidebar.find(".form-name-copy").attr("title", __(this.frm.doc.name));
        }
        return result;
    };

    const control = frappe.ui.form.Control.prototype;
    const makeControl = control.make;
    control.make = function (...args) {
        const result = makeControl.apply(this, args);
        if (this.frm?.meta?.issingle && this.df.label && this.tooltip) {
            this.tooltip.text(__(this.df.label, null, this.df.parent));
            // The upstream click handler copies visible tooltip text. Keep its
            // technical copy action useful even when the tooltip is localized.
            this.tooltip.off("click").on("click", () => {
                frappe.utils.copy_to_clipboard(this.df.fieldname);
            });
        }
        return result;
    };

    // These configuration values are themselves UI captions. Their editors
    // display translations too, but an untouched caption round-trips its key.
    // URLs, actions, defaults, custom code and business records are excluded.
    const captionFields = {
        "Navbar Item": ["item_label"],
        "Portal Menu Item": ["title"],
        "POS Field": ["label", "fieldtype"],
        "POS Search Fields": ["fieldname"],
        "Sales Taxes and Charges Template": ["title"],
    };
    const isCaption = (df) => captionFields[df?.parent]?.includes(df.fieldname);
    const captionFor = (value, df) => {
        if (df?.parent === "POS Search Fields" && df.fieldname === "fieldname") {
            const field = frappe.get_meta("Item")?.fields.find((item) => item.fieldname === value);
            return field?.label ? __(field.label, null, "Item") : value || "";
        }
        return __(value || "", null, df?.fieldname === "fieldtype" ? "DocField" : null);
    };
    const formatData = frappe.form.formatters.Data;
    frappe.form.formatters.Data = function (value, df, ...args) {
        const formatted = formatData.call(this, value, df, ...args);
        if (!isCaption(df) || typeof value !== "string" || captionFor(value, df) === value) return formatted;
        return frappe.utils.escape_html(captionFor(value, df));
    };
    const data = frappe.ui.form.ControlData.prototype;
    const formatInput = data.format_for_input;
    data.format_for_input = function (value) {
        return isCaption(this.df) ? captionFor(value, this.df) : formatInput.call(this, value);
    };
    const getInput = data.get_input_value;
    data.get_input_value = function () {
        const value = getInput.call(this);
        if (isCaption(this.df) && value === captionFor(this.value, this.df)) return this.value || "";
        return value;
    };
})();
