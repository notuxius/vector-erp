// Exercise the real Desk request handlers without creating a test notification.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");
const env = Object.fromEntries(fs.readFileSync(path.join(__dirname, "../.env"), "utf8")
    .split(/\r?\n/).filter(line => line.includes("=")).map(line => {
        const i = line.indexOf("="); return [line.slice(0, i), line.slice(i + 1)];
    }));

(async () => {
    const browser = await chromium.launch();
    try {
        const page = await browser.newPage({baseURL: "http://localhost:" + (env.HTTP_PORT || 8080)});
        page.setDefaultTimeout(15000);
        assert.equal((await page.request.post("/api/method/login", {
            form: {usr: "Administrator", pwd: env.ADMIN_PASSWORD},
        })).status(), 200);
        await page.goto("/desk/operations-overview");
        await page.waitForFunction(() => window.frappe?.app && window.frappe?.get_route_str()?.includes("operations-overview"));
        await page.route("**/api/method/vector.test_failure", route => route.fulfill({
            status: 502, contentType: "text/plain", body: "Bad Gateway", headers: {"X-Frappe-Request-Id": "test-request"},
        }));
        const reports = [];
        await page.route("**/api/method/**.vectoria_feedback.error_received", route => {
            const lookup = new URLSearchParams(route.request().postData());
            return route.fulfill({json: {message: reports.length >= 3 && lookup.get("request") === "HTTP 502 · vector.test_failure"}});
        });
        await page.route("**/api/method/**.vectoria_feedback.submit", route => {
            reports.push(Object.fromEntries(new URLSearchParams(route.request().postData())));
            if (reports.length === 1) return route.fulfill({status: 502, body: "Bad Gateway"});
            if (reports.length === 2) return route.abort("connectionreset");
            return route.fulfill({json: {message: "MTECH-1"}});
        });
        await page.evaluate(() => Promise.allSettled(Array.from({length: 4}, () =>
            frappe.call({method: "vector.test_failure", args: {private_value: "never-report-this"}}))).then(() => null));
        const area = page.locator(".msgprint:visible");
        const serverError = await page.evaluate(() => __("Internal Server Error"));
        const button = area.getByRole("button");
        await button.waitFor();
        assert.equal((await area.textContent()).split(serverError).length - 1, 1);
        assert.equal(await area.locator("hr").count(), 0);
        assert.equal(reports.length, 0);
        await button.click();
        await area.getByRole("status").filter({hasText: /автоматично|automatically/i}).waitFor();
        assert.equal(await button.isDisabled(), true);
        await area.getByText(/MTECH-1/).waitFor();
        assert.match(await area.getByRole("status").textContent(), /✓/);
        const reload = area.getByRole("button", {name: /^(Оновити|Reload)$/});
        assert.equal(await reload.isEnabled(), true);
        assert.equal(reports.length, 3);
        assert.deepEqual(reports[1], reports[0]);
        assert.deepEqual(reports[2], reports[0]);
        for (const value of ["HTTP 502", "vector.test_failure", "test-request", "× 4", "/desk/operations-overview"])
            assert.ok(reports[0].context.includes(value), value);
        assert.equal(reports[0].message, serverError);
        assert.ok(!JSON.stringify(reports[0]).includes("never-report-this"));
        await Promise.all([page.waitForEvent("domcontentloaded"), reload.click()]);
        await page.waitForFunction(() => window.frappe?.app && window.frappe?.get_route_str()?.includes("operations-overview"));
        await page.evaluate(() => Promise.allSettled([frappe.call("vector.test_failure")]).then(() => null));
        await area.getByRole("status").filter({hasText: /✓.*(уже отримав|already received)/i}).waitFor();
        assert.equal(await button.count(), 1);
        assert.equal(await reload.isEnabled(), true);
        assert.equal(reports.length, 3);
        await page.evaluate(() => {
            const err = JSON.stringify({indicator: "red", message: "Different error"});
            frappe.msgprint([err, err]);
            frappe.msgprint("Information");
        });
        const newReport = area.getByRole("button", {name: /Повідомити адміністратора|Report to administrator/});
        await newReport.waitFor();
        assert.equal(await area.getByRole("button").count(), 2);
        assert.equal((await area.textContent()).split("Different error").length - 1, 1);
        assert.ok((await area.textContent()).includes("Information"));
        await page.route("**/api/method/**.vectoria_feedback.submit", route => route.fulfill({status: 403}));
        await newReport.click();
        await area.getByText(/відхилила|declined/i).waitFor();
        assert.equal(await newReport.isEnabled(), true);
        console.log("PASS: automatic retries, confirmed checkmark, working reload, already-received state and distinct errors.");
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
