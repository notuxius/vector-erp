frappe.ui.form.on("Machine Assembly Installation", {
    setup(frm) {
        for (const field of ["machine_serial", "assembly_serial"]) {
            frm.set_query(field, () => ({
                query: "warehouse_core.assemblies.serial_query",
                filters: {company: frm.doc.company, kind: field === "machine_serial" ? "machine" : "assembly"},
            }));
        }
    },
    refresh(frm) {
        if (frm.is_new() && !frm.doc.installed_at) frm.set_value("installed_at", frappe.datetime.now_datetime());
        if (frm.doc.docstatus === 1 && !frm.doc.removed_at && frm.perm[0]?.write) {
            frm.add_custom_button("Зняти вузол", () => {
                frappe.prompt([{fieldname: "reason", label: "Причина зняття", fieldtype: "Small Text", reqd: 1}],
                    async ({reason}) => {
                        await frappe.call({
                            method: "warehouse_core.warehouse_core.doctype.machine_assembly_installation.machine_assembly_installation.remove_assembly",
                            args: {name: frm.doc.name, reason}, freeze: true,
                        });
                        await frm.reload_doc();
                    }, "Зняття вузла", "Зафіксувати зняття");
            });
        }
        frm.set_intro("Запис пов’язує серійні номери. Витрату комплектуючих і випуск верстата оформлюйте складськими документами.", "blue");
    },
});
