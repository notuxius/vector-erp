// Exercise quick entry and list subscriptions on the local demo site.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const { chromium } = require("playwright");

const env = Object.fromEntries(fs.readFileSync(path.join(__dirname, "../.env"), "utf8")
    .split(/\r?\n/).filter(line => line && !line.startsWith("#")).map(line => {
        const index = line.indexOf("=");
        return [line.slice(0, index), line.slice(index + 1)];
    }));
const base = `http://localhost:${process.env.HTTP_PORT || env.HTTP_PORT || 8080}`;

(async () => {
    const browser = await chromium.launch();
    try {
        const page = await browser.newPage();
        const login = await page.request.post(base + "/api/method/login", {
            form: { usr: "Administrator", pwd: env.ADMIN_PASSWORD },
        });
        assert.equal((await login.json()).message, "Logged In");
        await page.goto(base + "/desk/selling/customer");
        await page.waitForFunction(() => window.cur_list?.realtime_events_setup &&
            frappe.realtime.socket?.connected);
        const loadedAt = await page.evaluate(() => performance.timeOrigin);
        for (const [doctype, field] of [
            ["Customer", "customer_name"], ["Supplier", "supplier_name"], ["Customer", "customer_name"],
        ]) {
            await page.evaluate(dt => frappe.set_route("List", dt, "List"), doctype);
            await page.waitForFunction(dt => cur_list?.doctype === dt && cur_list.realtime_events_setup, doctype);
            const label = "List update check " + randomUUID();
            await page.locator(".page-head:visible .primary-action").click();
            const dialog = page.locator(".modal.show");
            await dialog.locator(`[data-fieldname="${field}"] input`).fill(label);
            const saved = page.waitForResponse(r => r.url().endsWith("/frappe.client.save"));
            await dialog.locator(".btn-modal-primary").click();
            const doc = (await (await saved).json()).message;
            assert.equal(doc?.[field], label, `${doctype} quick entry save`);
            const row = page.locator(".frappe-list:visible .list-row-container").filter({ hasText: label });
            try {
                await row.waitFor({ timeout: 10000 });
                console.log(`PASS ${doctype} appeared after quick entry`);
                if (doctype === "Customer") {
                    const customerType = doc.customer_type === "Company" ? "Individual" : "Company";
                    await page.evaluate(({ name, customerType }) =>
                        frappe.db.set_value("Customer", name, "customer_type", customerType),
                    { name: doc.name, customerType });
                    const translated = await page.evaluate(value => __(value), customerType);
                    await row.getByText(translated, { exact: true }).waitFor({ timeout: 10000 });
                    console.log("PASS saved change appeared in the Customer row");
                }
            } finally {
                await page.evaluate(({ doctype, name }) =>
                    frappe.call("frappe.client.delete", { doctype, name }), { doctype, name: doc.name });
            }
            await row.waitFor({ state: "detached", timeout: 10000 });
            assert.equal(await page.evaluate(() => performance.timeOrigin), loadedAt);
            console.log(`PASS ${doctype} disappeared after deletion without a reload`);
        }
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
