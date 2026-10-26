// Read-only acceptance check for translated price lists and immutable history.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const env = Object.fromEntries(fs.readFileSync(path.join(root, '.env'), 'utf8')
    .split(/\r?\n/).filter(line => line && !line.startsWith('#')).map(line => {
        const i = line.indexOf('='); return [line.slice(0, i), line.slice(i + 1)];
    }));
const base = `http://localhost:${env.HTTP_PORT || 8080}`;

(async () => {
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext();
    try {
        const login = await context.request.post(`${base}/api/method/login`, {
            form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
        });
        assert.equal(login.status(), 200);
        const page = await context.newPage();
        await page.goto(`${base}/desk/selling-settings`);
        await page.waitForFunction(() => window.cur_frm?.doctype === 'Selling Settings' && cur_frm.timeline);
        const result = await page.evaluate(() => {
            const timeline = cur_frm.timeline;
            const before = JSON.stringify(cur_frm.get_docinfo().versions);
            const items = timeline.get_version_timeline_contents();
            const html = items.map(item => item.content).join('\n');
            timeline.refresh();
            return {
                html, unchanged: before === JSON.stringify(cur_frm.get_docinfo().versions),
                value: cur_frm.doc.selling_price_list,
                label: __('Standard Selling', null, 'Price List'),
                rendered: timeline.wrapper.text(),
            };
        });
        assert(result.html.includes(result.label), result.html);
        assert(!result.html.includes('Standard Selling'), result.html);
        assert(result.html.includes('/desk/version/kfthqo7d82'), 'Original version link retained');
        assert(result.rendered.includes(result.label));
        assert.equal(result.value, 'Standard Selling');
        assert(result.unchanged, 'Audit history must remain unchanged');

        // A free-text field containing the same words must not be translated.
        const freeText = await page.evaluate(() => {
            const timeline = cur_frm.timeline;
            const df = frappe.meta.get_docfield(cur_frm.doctype, 'selling_price_list', cur_frm.docname);
            const version = { ...timeline.doc_info.versions[0], data: JSON.stringify({ changed: [[df.fieldname, '', 'Standard Selling']] }) };
            const context = Object.create(timeline);
            context.doc_info = { ...timeline.doc_info, versions: [version] };
            const fieldtype = df.fieldtype;
            try {
                df.fieldtype = 'Data';
                return context.get_version_timeline_contents().map(item => item.content).join('');
            } finally {
                df.fieldtype = fieldtype;
            }
        });
        assert(freeText.includes('Standard Selling'), freeText);
        await page.evaluate(() => frappe.set_route('List', 'Price List', 'List'));
        await page.waitForFunction(() => window.cur_list?.doctype === 'Price List' && cur_list.data?.length);
        const prices = await page.evaluate(() => ({
            text: cur_list.$result.text(), names: cur_list.data.map(row => row.name),
            buying: __('Standard Buying', null, 'Price List'),
        }));
        assert(prices.text.includes(result.label), prices.text);
        assert(prices.text.includes(prices.buying), prices.text);
        assert(prices.names.includes('Standard Selling'));
        assert(prices.names.includes('Standard Buying'));
        const version = await context.request.get(`${base}/api/resource/Version/kfthqo7d82`);
        assert.equal(version.status(), 200);
        assert.deepEqual(JSON.parse((await version.json()).data.data).changed,
            [['selling_price_list', '', 'Standard Selling']]);
        console.log('PASS: Ukrainian timeline and price lists; original version link, audit payload, price-list IDs and free text preserved.');
    } finally {
        await context.request.post(`${base}/api/method/logout`).catch(() => {});
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
