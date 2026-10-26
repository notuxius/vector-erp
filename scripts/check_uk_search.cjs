// Regression coverage for command search labels and unchanged navigation/data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../apps/warehouse_core/warehouse_core/public/js/uk_ui.js"), "utf8");
const dictionary = {
    Assets: "Активи", Asset: "Актив", List: "Список", Workspace: "Робочий простір",
    Dashboard: "Огляд", Tree: "Дерево", Location: "Розташування",
    "Asset Movement": "Рух активів", "Asset Depreciation Schedule": "Графік амортизації активу",
    "Fixed Asset Register": "Реєстр фіксованих активів", Report: "Звіт",
};
const routes = [["Workspaces", "Assets"], ["List", "Asset"], ["List", "Asset Depreciation Schedule"],
    ["List", "Asset Movement"], ["query-report", "Fixed Asset Register"], ["Tree", "Location"],
    ["dashboard-view", "Asset"], ["Form", "Asset", "ASSET-0001"], ["List", "<custom>"]];
const entries = routes.map(route => ({route, label: route[1], value: route[1], index: 80, recent: true}));
const original = JSON.stringify(entries);
function setup(lang) {
    const utils = { get_recent_pages: () => entries, get_frequent_links: () => entries.map(e => ({...e, route:e.route.join("/")})) };
    const context = { frappe: { boot: {lang}, search: {utils}, ui: {form: {}},
        utils: {escape_html: s => s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")} },
        $: {fn: {}}, __: s => dictionary[s] || s };
    vm.runInNewContext(source, context);
    return {utils, context};
}
const {utils, context} = setup("uk");
for (const method of ["get_recent_pages", "get_frequent_links"]) {
    const results = utils[method]("");
    assert.equal(results[0].label, "<b>Активи</b> Робочий простір");
    assert.equal(results[2].label, "<b>Графік амортизації активу</b> Список");
    assert.equal(results[4].label, "<b>Реєстр фіксованих активів</b> Звіт");
    assert.equal(results[5].label, "<b>Розташування</b> Дерево");
    assert.equal(results[6].value, "Огляд: Актив");
    assert.equal(results[7].label, entries[7].label);
    assert.equal(results[8].label, "<b>&lt;custom&gt;</b> Список");
    results.forEach((result, i) => {
        assert.equal(JSON.stringify(result.route), JSON.stringify(method === "get_recent_pages" ? routes[i] : routes[i].join("/")));
        assert.equal(result.index, 80);
        assert.equal(result.recent, true);
    });
}
assert.equal(JSON.stringify(entries), original);
const wrapped = utils.get_recent_pages;
vm.runInNewContext(source, context);
assert.equal(utils.get_recent_pages, wrapped);
assert.equal(setup("en").utils.get_recent_pages(), entries);
console.log("PASS: Ukrainian recent/frequent search labels, escaped markup, preserved routes and records, English unchanged.");
