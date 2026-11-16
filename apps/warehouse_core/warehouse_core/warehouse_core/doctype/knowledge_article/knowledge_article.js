frappe.ui.form.on("Knowledge Article", {
    refresh(frm) {
        const publish = frappe.user.has_role("Knowledge Publisher") || frappe.user.has_role("System Manager") || frappe.session.user === "Administrator";
        for (const field of ["allowed_roles", "allowed_users", "cloud_allowed"]) frm.set_df_property(field, "read_only", !publish);
        // The server enforces these restrictions for API writes as well.
        frm.set_df_property("status", "options", publish ? "Draft\nReview\nPublished\nArchived" : "Draft\nReview");
        frm.fields_dict.status.$input.find('option[value="Review"]').text("На перевірці");
        frm.set_df_property("status", "read_only", !publish && ["Published", "Archived"].includes(frm.doc.status));
        if (frm.doc.status === "Published") frm.dashboard.set_headline("Зміна тексту, доступу або файлів зніме публікацію. Після збереження статтю потрібно опублікувати повторно.");
        // Standard File attachments have different ownership rules; use the protected endpoint.
        frm.$wrapper.find(".form-attachments, .form-shared").hide();
        if (frm.is_new()) {
            frm.dashboard.set_headline("Вкажіть назву та збережіть чернетку. Потім завантажте PDF/DOCX/XLSX/TXT — текст буде додано автоматично для перевірки.");
            return;
        }
        const area = frm.fields_dict.protected_files.$wrapper[0]; area.replaceChildren();
        frappe.call({method: "warehouse_core.knowledge.attachments", type: "GET", args: {article: frm.doc.name}}).then(response => {
            area.replaceChildren();
            for (const file of response.message || []) {
                const row = document.createElement("p"), link = document.createElement("a");
                link.textContent = `${file.file_name} (${Math.ceil(file.byte_size / 1024)} КБ)`;
                link.href = file.url; row.append(link);
                const detail = document.createElement("div"); detail.className = "text-muted";
                detail.textContent = file.extraction_message || "Файл додано раніше; текст не витягувався автоматично.";
                if (file.extracted_characters) detail.textContent += ` Додано символів: ${file.extracted_characters}.`;
                row.append(detail); area.append(row);
            }
            if (!response.message?.length) { const p = document.createElement("p"); p.textContent = "Захищених файлів ще немає."; area.append(p); }
        });
        if (frm.perm[0]?.write) frm.add_custom_button("Додати захищений файл", () => {
            if (frm.is_dirty()) { frappe.msgprint("Спочатку збережіть зміни статті."); return; }
            const input = document.createElement("input"); input.type = "file"; input.accept = ".pdf,.docx,.xlsx,.txt,.md";
            input.onchange = async () => {
                const file = input.files?.[0]; if (!file) return;
                if (file.size > 5 * 1024 * 1024) { frappe.msgprint("Максимальний розмір файлу — 5 МБ."); return; }
                const reader = new FileReader();
                reader.onload = async () => {
                    const response = await frappe.call({method: "warehouse_core.knowledge.upload", type: "POST", freeze: true,
                        freeze_message: "Зберігаємо файл і витягуємо текст…", args: {article: frm.doc.name, filename: file.name, data: reader.result.split(",")[1]}});
                    await frm.reload_doc();
                    const result = response.message;
                    frappe.msgprint({title: "Файл збережено. Стаття — чернетка", message: frappe.utils.escape_html(result.extraction_message),
                        indicator: ["Extracted", "Not Applicable"].includes(result.extraction_status) ? "green" : "orange"});
                };
                reader.onerror = () => frappe.msgprint("Не вдалося прочитати файл. Спробуйте ще раз.");
                reader.readAsDataURL(file);
            }; input.click();
        });
    }
});
