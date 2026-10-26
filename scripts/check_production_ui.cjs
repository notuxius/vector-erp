// Read-only browser checks; new forms are opened without saving business records.
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
fs.mkdirSync(path.join(root, '.artifacts/production-review'), {recursive: true});

(async () => {
    const browser = await chromium.launch({headless: true});
    try {
        const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
        assert.equal((await context.request.post(`${base}/api/method/login`, {
            form: {usr: 'Administrator', pwd: env.ADMIN_PASSWORD},
        })).status(), 200);
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(base + '/desk/stock');
        await page.waitForFunction(() => window.frappe?.boot && window.frappe?.set_route);
        await page.evaluate(() => frappe.set_route('query-report', 'Expected Production'));
        await page.waitForFunction(() => frappe.query_report?.report_name === 'Expected Production' && frappe.query_report.filters?.length);
        await page.evaluate(async () => {
            await frappe.query_report.set_filter_value('company', 'MTech');
            await frappe.query_report.refresh();
        });
        assert.ok(await page.getByText('Очікуваний випуск виробництва', {exact: true}).count());
        const filters = await page.evaluate(() => frappe.query_report.get_filter_values());
        assert.equal(filters.company, 'MTech');
        await page.evaluate(async () => {
            await frappe.query_report.set_filter_value('view', 'Виробничі замовлення');
            await frappe.query_report.refresh();
        });
        await page.screenshot({path: path.join(root, '.artifacts/production-review/report.png'), fullPage: true});
        const serialResponse = await context.request.get(base + '/api/resource/Serial%20No', {
            params: {fields: JSON.stringify(['name']), filters: JSON.stringify({name: 'CNC-SN-001'}), limit_page_length: '1'},
        });
        assert.equal(serialResponse.status(), 200);
        const serial = (await serialResponse.json()).data[0]?.name;
        assert.ok(serial, 'The configured demo must contain a serial number');
        await page.evaluate(serial => frappe.set_route('Form', 'Serial No', serial), serial);
        await page.waitForFunction(serial => window.cur_frm?.doc?.name === serial, serial);
        await page.getByRole('button', {name: 'Історія вузлів', exact: true}).waitFor();
        assert.ok(await page.getByText('Чинних встановлень немає.', {exact: true}).count());
        const fields = await page.evaluate(() => Object.keys(cur_frm.fields_dict));
        for (const name of ['custom_manufacturer_serial_no', 'custom_technical_specifications', 'custom_installation_details', 'warranty_expiry_date']) {
            assert.ok(fields.includes(name), name);
        }
        await page.screenshot({path: path.join(root, '.artifacts/production-review/serial.png'), fullPage: true});
        await page.getByRole('button', {name: 'Встановити вузол', exact: true}).click();
        await page.waitForFunction(() => window.cur_frm?.doctype === 'Machine Assembly Installation' && cur_frm.is_new());
        const doc = await page.evaluate(() => ({company: cur_frm.doc.company, machine: cur_frm.doc.machine_serial, date: cur_frm.doc.installed_at}));
        assert.equal(doc.company, 'MTech');
        assert.equal(doc.machine, serial);
        assert.ok(doc.date);
        await page.screenshot({path: path.join(root, '.artifacts/production-review/installation.png'), fullPage: true});
        assert.deepEqual(errors, []);
        console.log(JSON.stringify({ok: true, checks: ['Ukrainian report and both views', 'Serial fields and current installations', 'Installation form prefills machine/company/date', 'No JavaScript errors or saved records']}));
    } finally {
        await browser.close();
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
