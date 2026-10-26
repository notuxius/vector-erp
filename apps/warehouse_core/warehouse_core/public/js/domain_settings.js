// MultiCheck renders labels verbatim. Keep the stored domain values intact.
frappe.ui.form.on("Domain Settings", {
    refresh(frm) {
        if (frappe.boot.lang !== "uk") return;
        const control = frm.domains_multicheck;
        const getData = control?.df.get_data;
        if (!getData || getData.vectorUkrainian) return;
        control.df.get_data = function (...args) {
            return getData.apply(this, args).map(option => ({...option, label: __(option.label)}));
        };
        control.df.get_data.vectorUkrainian = true;
        control.refresh_input();
    },
});
