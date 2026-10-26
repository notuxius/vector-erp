// Exercise the installed Frappe section implementation with the app adapter.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { execFileSync } = require("node:child_process");
const upstream = execFileSync("docker", ["compose", "exec", "-T", "backend", "cat",
    "apps/frappe/frappe/public/js/frappe/ui/sidebar/sidebar_item.js"], { encoding: "utf8" });
const adapter = fs.readFileSync(path.join(__dirname,
    "../apps/warehouse_core/warehouse_core/public/js/navigation_sections.js"), "utf8");
const values = new Map();
const handlers = [];
const context = {
    document: {},
    $: () => ({ on: (_event, handler) => handlers.push(handler) }),
    localStorage: {
        getItem: key => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, value),
    },
    frappe: {
        provide() {},
        app: { sidebar: { current_module: "Stock" } },
        ui: { sidebar_item: {}, Sidebar: class {
            expand_parent_section() { this.activeSection.open(); }
            get_item() { return this.activeSection; }
        } },
    },
};
vm.createContext(context);
vm.runInContext(upstream, context);
vm.runInContext(adapter, context);
const Section = context.frappe.ui.sidebar_item.TypeSectionBreak;
// Replace rendering only; prepare, open, close, restoration and persistence are real.
Section.prototype.make = function () {};
Section.prototype.toggle = function () { this.renderedCollapsed = this.collapsed; };
function section(label, module = "Stock", indent = false) {
    context.frappe.app.sidebar.current_module = module;
    return new Section({ item: { label, indent, nested_items: [] } });
}
function choose(item, collapsed) {
    item.collapsed = collapsed;
    item.save_section_break_state();
}
function restored(label, module = "Stock") {
    const item = section(label, module);
    if (item.section_breaks_state[module]) item.apply_section_break_state();
    return item.collapsed;
}
const a = section("Inventory");
const b = section("Reports");
const c = section("Reports", "Selling");
choose(a, true);
choose(b, true);
choose(c, false);
assert.equal(restored("Inventory"), true);
assert.equal(restored("Reports"), true);
assert.equal(restored("Reports", "Selling"), false);
choose(a, false);
assert.equal(restored("Inventory"), false);
assert.equal(restored("Reports"), true);

// A separate page writes after these instances were constructed.
const external = JSON.parse(values.get("section-breaks-state"));
external.Accounting = { Reports: true };
values.set("section-breaks-state", JSON.stringify(external));
choose(b, false);
assert.equal(restored("Reports", "Accounting"), true);

// Expanded and collapsed, indented and ordinary sections survive rail redraws.
for (const indent of [false, true]) {
    for (const collapsed of [false, true]) {
        const item = section("Rail", "Stock", indent);
        item.collapsed = collapsed;
        item.toggle_on_collapse();
        const handler = handlers.at(-1);
        handler({}, { sidebar_expand: false });
        handler({}, { sidebar_expand: false });
        handler({}, { sidebar_expand: true });
        assert.equal(item.collapsed, collapsed);
        item.collapsed = !collapsed;
        handler({}, { sidebar_expand: true });
        assert.equal(item.collapsed, !collapsed);
    }
}

// Route highlighting must retain a deliberate collapse, but reveal unsaved groups.
const sidebar = new context.frappe.ui.Sidebar();
sidebar.activeSection = a;
sidebar.active_item = { closest: () => ({ attr: () => "Inventory" }) };
choose(a, true);
sidebar.expand_parent_section();
assert.equal(a.collapsed, true);
choose(a, false);
a.collapsed = true;
sidebar.expand_parent_section();
assert.equal(a.collapsed, false);

const save = Section.prototype.save_section_break_state;
vm.runInContext(adapter, context);
assert.equal(Section.prototype.save_section_break_state, save);
values.set("section-breaks-state", "invalid json");
assert.doesNotThrow(() => section("Malformed"));
values.set("section-breaks-state", "null");
assert.doesNotThrow(() => choose(section("Empty"), true));
context.localStorage.getItem = context.localStorage.setItem = () => { throw Error("blocked"); };
assert.doesNotThrow(() => choose(section("Unavailable"), true));
assert.equal(restored("Unavailable"), true);
console.log("PASS: independent sections/modules, fresh-page restoration, concurrent-page merge, rail transitions, active-page collapse, malformed/unavailable storage, idempotent adapter.");
