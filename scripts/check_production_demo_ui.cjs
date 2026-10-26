// Reads the seeded training scenario; never submits or changes training documents.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const env = Object.fromEntries(fs.readFileSync(path.join(root, '.env'), 'utf8').split(/\r?\n/)
    .filter(line => line && !line.startsWith('#')).map(line => {
        const index = line.indexOf('='); return [line.slice(0, index), line.slice(index + 1)];
    }));
const base = `http://localhost:${env.HTTP_PORT || 8080}`;
const out = path.join(root, '.artifacts/production-review');
fs.mkdirSync(out, {recursive: true});

(async () => {
    const browser = await chromium.launch({headless: true});
    try {
        const context = await browser.newContext({viewport: {width: 1920, height: 1080}});
        assert.equal((await context.request.post(`${base}/api/method/login`, {
            form: {usr: 'Administrator', pwd: env.ADMIN_PASSWORD},
        })).status(), 200);
        const page = await context.newPage();
        const errors = [];
        const urls = {};
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(base + '/desk/stock');
        await page.waitForFunction(() => window.frappe?.boot && window.frappe?.set_route);
        await page.evaluate(() => frappe.set_route('query-report', 'Expected Production'));
        await page.waitForFunction(() => frappe.query_report?.report_name === 'Expected Production' && frappe.query_report.filters?.length);
        await page.evaluate(async () => {
            await frappe.query_report.set_filter_value({company: 'MTech', view: 'Товари та склади'});
            await frappe.query_report.refresh();
        });
        const quantities = await page.evaluate(() => Object.fromEntries(frappe.query_report.data
            .filter(row => row.item_code?.startsWith('TRAIN-'))
            .map(row => [row.item_code, [row.actual_qty, row.expected_qty, row.ordered_qty]])));
        assert.deepEqual(quantities, {
            'TRAIN-FRAME-1325': [1, 1, 0], 'TRAIN-SPINDLE': [1, 1, 2],
            'TRAIN-CONTROL': [0, 1, 0], 'TRAIN-CNC-1325': [1, 1, 0],
        });
        urls.report = page.url();
        await page.screenshot({path: path.join(out, 'demo-expected-production.png'), fullPage: true});
        await page.evaluate(async () => {
            await frappe.query_report.set_filter_value('view', 'Виробничі замовлення');
            await frappe.query_report.refresh();
        });
        await page.waitForFunction(() => frappe.query_report.data?.some(row => row.work_order?.startsWith('TRAIN-')));
        const orders = await page.evaluate(() => frappe.query_report.data.filter(row => row.work_order?.startsWith('TRAIN-')));
        assert.equal(orders.length, 4);
        assert.ok(orders.every(row => row.expected_qty === 1 && row.planned_end_date));
        await page.screenshot({path: path.join(out, 'demo-work-orders.png'), fullPage: true});
        await page.evaluate(() => frappe.set_route('Form', 'Serial No', 'TRAIN-CNC-1325-001'));
        await page.waitForFunction(() => window.cur_frm?.doc?.name === 'TRAIN-CNC-1325-001');
        const table = page.locator('[data-fieldname="custom_assembly_details"]');
        await table.getByRole('link', {name: 'TRAIN-SPINDLE-002', exact: true}).waitFor();
        assert.equal(await table.locator('tbody tr').count(), 3);
        assert.equal(await table.getByRole('link', {name: 'TRAIN-SPINDLE-001', exact: true}).count(), 0);
        urls.machine = page.url();
        await table.scrollIntoViewIfNeeded();
        await page.screenshot({path: path.join(out, 'demo-machine-links.png'), fullPage: false});
        await table.getByRole('link', {name: 'TRAIN-SPINDLE-002', exact: true}).click();
        await page.waitForFunction(() => cur_frm.doc.name === 'TRAIN-SPINDLE-002');
        await table.getByRole('link', {name: 'TRAIN-CNC-1325-001', exact: true}).waitFor();
        await table.getByRole('link', {name: 'TRAIN-CNC-1325-001', exact: true}).click();
        await page.waitForFunction(() => cur_frm.doc.name === 'TRAIN-CNC-1325-001');
        await page.getByRole('button', {name: 'Історія вузлів', exact: true}).click();
        await page.waitForFunction(() => window.cur_list?.doctype === 'Machine Assembly Installation' && cur_list.data?.length === 4);
        urls.history = page.url();
        await page.screenshot({path: path.join(out, 'demo-replacement-history.png'), fullPage: true});
        await page.evaluate(() => frappe.set_route('Form', 'Machine Assembly Installation', 'TRAIN-INSTALL-SPINDLE-OLD'));
        await page.waitForFunction(() => window.cur_frm?.doc?.name === 'TRAIN-INSTALL-SPINDLE-OLD');
        const old = await page.evaluate(() => ({removed: cur_frm.doc.removed_at, reason: cur_frm.doc.removal_reason,
            assembly: cur_frm.doc.assembly_serial, installed: cur_frm.doc.installed_at}));
        assert.ok(old.removed && old.reason.includes('шум') && old.installed);
        assert.equal(old.assembly, 'TRAIN-SPINDLE-001');
        assert.deepEqual(errors, []);
        console.log(JSON.stringify({ok: true, quantities, urls, checks: ['Populated production report and dated orders',
            'Machine → assembly → machine links', 'Four installation records with completed replacement', 'No browser errors or record changes']}));
    } finally {
        await browser.close();
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
