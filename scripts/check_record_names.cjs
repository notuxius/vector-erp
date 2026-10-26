// Read-only browser acceptance check after the explicit record-name migration.
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
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    try {
        const login = await context.request.post(`${base}/api/method/login`, {
            form: { usr: 'Administrator', pwd: env.ADMIN_PASSWORD },
        });
        assert.equal(login.status(), 200);
        const page = await context.newPage();
        await page.goto(`${base}/desk/department`);
        await page.waitForFunction(() => window.frappe?.set_route);
        await page.evaluate(() => frappe.set_route('List', 'Department', 'List'));
        await page.waitForFunction(() => window.cur_list?.doctype === 'Department' && cur_list.data?.length === 14);
        const departments = await page.evaluate(() => ({
            text: cur_list.$result.text(),
            names: cur_list.data.map(row => row.name),
            tree: frappe.get_meta('Department').is_tree,
        }));
        assert(!departments.text.includes('DEMO'), departments.text);
        assert(departments.text.includes('Бухгалтерія'));
        assert(departments.text.includes('Юридичний відділ'));
        assert(departments.names.includes('Accounts - MTech'));
        assert(departments.names.every(name => !name.includes('DEMO')));
        assert(departments.tree, 'Department hierarchy remains enabled');
        fs.mkdirSync(path.join(root, '.artifacts'), { recursive: true });
        await page.screenshot({ path: path.join(root, '.artifacts/departments-clean.png'), animations: 'disabled' });
        await page.getByRole('link', { name: 'Бухгалтерія', exact: true }).first().click();
        await page.waitForFunction(() => window.cur_frm?.doctype === 'Department' && cur_frm.doc?.name === 'Accounts - MTech');
        const department = await page.evaluate(() => ({ name: cur_frm.doc.name, parent: cur_frm.doc.parent_department }));
        assert.equal(department.parent, 'All Departments');
        for (const [doctype, name] of [['Item', 'ITEM-001'], ['Serial No', 'CNC-SN-001'],
            ['Sales Order', 'DISPATCH-SO-001'], ['Delivery Note', 'DISPATCH-DN-001']]) {
            const response = await context.request.get(`${base}/api/resource/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}`);
            assert.equal(response.status(), 200, `${doctype} ${name}`);
            assert.equal((await response.json()).data.name, name);
        }
        console.log('PASS: all 14 departments render without DEMO; Ukrainian names, clickable records, hierarchy and renamed transaction endpoints verified.');
    } finally {
        await context.request.post(`${base}/api/method/logout`).catch(() => {});
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
