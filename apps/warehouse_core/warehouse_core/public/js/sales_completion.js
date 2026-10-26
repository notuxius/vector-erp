/* Informational only: preserve native permissions, stock and accounting. */
frappe.ui.form.on("Sales Order", {
  refresh(frm) {
    const field = frm.fields_dict.custom_completion_check;
    if (!field) return;
    const request = (frm.__completion_request || 0) + 1;
    frm.__completion_request = request;
    const name = frm.doc.name;
    const wrapper = field.$wrapper;
    if (frm.is_new() || frm.is_dirty()) {
      wrapper.text("Збережіть замовлення, щоб перевірити оплату та відвантаження.");
      return;
    }
    wrapper.text("Перевірка оплати, відвантаження та повернень…");
    frappe.call({
      method: "warehouse_core.sales_completion.get_completion_check",
      args: { sales_order: name },
      callback(response) {
        if (frm.doc.name !== name || frm.__completion_request !== request || frm.is_dirty()) return;
        const state = response.message;
        if (!state) return;
        const escape = value => frappe.utils.escape_html(String(value ?? ""));
        wrapper.html(`
          <div class="alert alert-info">
            <strong>Контроль виконання замовлення</strong>
            <p>${escape(state.conclusion)}</p>
            <dl>
              <dt>Відвантаження</dt><dd>${escape(state.shipment)} (${escape(state.delivered_percent)}%)</dd>
              <dt>Розрахунки</dt><dd>${escape(state.payment)}</dd>
              <dt>Повернення</dt><dd>${escape(state.returns)}</dd>
            </dl>
            <p>${escape(state.note)}</p>
            ${state.native_completed ? "<p>Стандартний статус цього документа означає відвантаження та виставлення рахунків; він не підтверджує отримання коштів.</p>" : ""}
            <button type="button" class="btn btn-default btn-sm">Оновити перевірку</button>
          </div>`);
        wrapper.find("button").on("click", () => frm.reload_doc());
        if (state.native_completed) {
          frm.page.set_indicator("Відвантажено та виставлено рахунки", "blue");
        }
      },
      error() {
        if (frm.doc.name === name && frm.__completion_request === request) {
          wrapper.text("Не вдалося перевірити виконання. Оновіть замовлення.");
        }
      }
    });
  }
});
