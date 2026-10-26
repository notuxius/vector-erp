// Read-only form check against existing sandbox records.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const root = path.resolve(__dirname, "..");
const env = Object.fromEntries(fs.readFileSync(path.join(root, ".env"), "utf8").split(/\r?\n/)
  .filter(line => line && !line.startsWith("#")).map(line => {
    const index = line.indexOf("=");
    return [line.slice(0, index), line.slice(index + 1)];
  }));
const base = `http://localhost:${env.HTTP_PORT || 8080}`;

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    assert.equal((await context.request.post(`${base}/api/method/login`, {
      form: { usr: "Administrator", pwd: env.ADMIN_PASSWORD }
    })).status(), 200);
    const response = await context.request.get(base + "/api/resource/Sales%20Order", {
      params: { fields: JSON.stringify(["name"]), filters: JSON.stringify({ docstatus: 1 }), limit_page_length: "1" }
    });
    assert.equal(response.status(), 200);
    const name = (await response.json()).data[0]?.name;
    assert.ok(name, "An existing submitted sandbox sales order is required");
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(base + "/desk/selling");
    await page.waitForFunction(() => window.frappe?.set_route);
    await page.evaluate(name => frappe.set_route("Form", "Sales Order", name), name);
    const panel = page.locator('[data-fieldname="custom_completion_check"]');
    await panel.getByRole("button", { name: "Оновити перевірку", exact: true }).waitFor();
    assert.match(await panel.innerText(), /Виконано — усі необхідні умови виконані/);
    assert.match(await panel.innerText(), /Ця перевірка не завершує замовлення автоматично/);
    await panel.getByRole("button", { name: "Оновити перевірку", exact: true }).click();
    await panel.getByRole("button", { name: "Оновити перевірку", exact: true }).waitFor();
    assert.equal(await page.evaluate(() => !!cur_frm.is_dirty()), false);
    fs.mkdirSync(path.join(root, ".artifacts/production-review"), { recursive: true });
    await panel.screenshot({ path: path.join(root, ".artifacts/production-review/sales-completion.png") });
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ ok: true, checks: ["Completion panel renders in Ukrainian",
      "Refresh remains read-only", "No JavaScript errors"] }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
