// Verify that translated settings captions cannot overwrite saved identifiers.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname,
    "../apps/warehouse_core/warehouse_core/public/js/settings_ui.js"), "utf8");
const dictionary = { Projects: "Проєкти", About: "Про програму", Fields: "Поля", "Item Group": "Група товарів",
    "Stock Settings": "Налаштування складу", "<caption>": "<назва>" };
function setup(lang) {
    class Sidebar { refresh() { return "original"; } }
    class Control { make() {} }
    class ControlData {
        format_for_input(value) { return value || ""; }
        get_input_value() { return this.input; }
    }
    const context = { frappe: { boot: { lang, translated_doctypes: [] },
        get_meta: () => ({ fields: [{ fieldname: "item_group", label: "Item Group" }] }),
        ui: { form: { Sidebar, Control, ControlData, on: () => {} } },
        form: { formatters: { Data: value => value } },
        utils: { escape_html: s => s.replaceAll("<", "&lt;").replaceAll(">", "&gt;"),
            copy_to_clipboard: value => { context.copied = value; } },
    }, __: value => dictionary[value] || value };
    vm.runInNewContext(source, context);
    return context;
}
const ctx = setup("uk");
for (const [parent, fieldname] of [["Portal Menu Item", "title"], ["Navbar Item", "item_label"]]) {
    const control = new ctx.frappe.ui.form.ControlData();
    control.df = { parent, fieldname };
    control.value = "Projects";
    control.input = control.format_for_input(control.value);
    assert.equal(control.input, "Проєкти");
    assert.equal(control.get_input_value(), "Projects");
    control.input = "Мій портал";
    assert.equal(control.get_input_value(), "Мій портал");
    assert.equal(ctx.frappe.form.formatters.Data("<caption>", control.df), "&lt;назва&gt;");
}
for (const df of [{ parent: "Customer", fieldname: "customer_name" },
    { parent: "Navbar Item", fieldname: "route" }, { parent: "POS Field", fieldname: "default_value" }]) {
    const control = new ctx.frappe.ui.form.ControlData();
    control.df = df;
    control.value = control.input = "Projects";
    assert.equal(control.format_for_input(control.value), "Projects");
    assert.equal(control.get_input_value(), "Projects");
    assert.equal(ctx.frappe.form.formatters.Data("Projects", df), "Projects");
}
const control = new ctx.frappe.ui.form.Control();
control.frm = { meta: { issingle: 1 } };
control.df = { label: "Fields", fieldname: "fields" };
control.tooltip = { text(v) { this.caption = v; return this; }, off() { return this; },
    on(event, handler) { this.click = handler; return this; } };
control.make();
assert.equal(control.tooltip.caption, "Поля");
control.tooltip.click();
assert.equal(ctx.copied, "fields");
const wrapped = ctx.frappe.ui.form.ControlData.prototype.get_input_value;
const fieldId = new ctx.frappe.ui.form.ControlData();
fieldId.df = { parent: "POS Search Fields", fieldname: "fieldname" };
fieldId.value = "item_group";
fieldId.input = fieldId.format_for_input(fieldId.value);
assert.equal(fieldId.input, "Група товарів");
assert.equal(fieldId.get_input_value(), "item_group");
vm.runInNewContext(source, ctx);
assert.equal(ctx.frappe.ui.form.ControlData.prototype.get_input_value, wrapped);
assert.equal(setup("en").frappe.form.formatters.Data("Projects", { parent: "Portal Menu Item", fieldname: "title" }), "Projects");
console.log("PASS: Ukrainian captions, unchanged saved keys, editable custom labels, escaped HTML, technical fields and English preserved, idempotent adapter.");
