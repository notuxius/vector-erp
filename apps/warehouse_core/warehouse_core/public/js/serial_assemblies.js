frappe.ui.form.on("Serial No", {
    async refresh(frm) {
        const field = frm.fields_dict.custom_assembly_details;
        if (!field) return;
        field.$wrapper.empty();
        if (frm.is_new() || !frappe.model.can_read("Machine Assembly Installation")) return;
        const serial = frm.doc.name;
        const {message: data} = await frappe.call({method: "warehouse_core.assemblies.get_assemblies", args: {serial_no: serial}});
        if (frm.doc.name !== serial || !data) return;
        const escape = frappe.utils.escape_html;
        const link = (type, name) => `<a href="${escape(frappe.utils.get_form_link(type, name))}">${escape(name)}</a>`;
        const rows = data.rows.map(row => `<tr>
            <td>${link("Machine Assembly Installation", row.name)}</td>
            <td>${link("Serial No", row.machine_serial)}</td><td>${link("Serial No", row.assembly_serial)}</td>
            <td>${escape(row.position || "")}</td><td>${escape(frappe.datetime.str_to_user(row.installed_at))}</td></tr>`).join("");
        field.$wrapper.html(rows ? `<div class="table-responsive"><table class="table table-bordered">
            <thead><tr><th>Встановлення</th><th>Верстат</th><th>Вузол</th><th>Місце</th><th>Встановлено</th></tr></thead>
            <tbody>${rows}</tbody></table></div>` : '<p class="text-muted">Чинних встановлень немає.</p>');
        frm.add_custom_button("Історія вузлів", () => {
            frappe.route_options = {[data.filter_field]: serial};
            frappe.set_route("List", "Machine Assembly Installation");
        });
        if (frappe.model.can_create("Machine Assembly Installation")) {
            frm.add_custom_button("Встановити вузол", () => frappe.new_doc("Machine Assembly Installation", {
                company: frm.doc.company, [data.filter_field]: serial, installed_at: frappe.datetime.now_datetime(),
            }));
        }
    },
});
