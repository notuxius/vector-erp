"""Provision a dedicated read-only ERP identity; never print its credentials."""
import json
from pathlib import Path
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]
USER = 'telegram.bot@vector-erp.invalid'
ROLE = 'Telegram ERP Reader'
PROVISION = r'''
import os,sys,json
os.chdir('/home/frappe/frappe-bench/sites')
import frappe
from frappe.permissions import add_permission
frappe.init(site='frontend');frappe.connect();frappe.set_user('Administrator')
payload=json.load(sys.stdin)
user,role=payload['user'],payload['role']
doctypes=('Company','Warehouse','Item','Bin','Item Group')
def roles():
    return sorted((r.parent,r.role) for r in frappe.get_all('Has Role',fields=['parent','role']) if r.parent!=user)
before=roles()
try:
    assert 'warehouse_core.telegram_access.restrict_api' in frappe.get_hooks('auth_hooks'), 'Deploy API restriction before provisioning'
    if not frappe.db.exists('Role',role):
        frappe.get_doc({'doctype':'Role','role_name':role,'desk_access':1}).insert()
    for doctype in doctypes:
        rule=frappe.db.get_value('Custom DocPerm',{'parent':doctype,'role':role,'permlevel':0})
        if not rule: add_permission(doctype,role,ptype='read')
        frappe.clear_cache(doctype=doctype)
    if frappe.db.exists('User',user):
        doc=frappe.get_doc('User',user)
        assert doc.api_key==payload['api_key'] and doc.get_password('api_secret')==payload['api_secret'], 'Existing service identity has different credentials'
        assert doc.enabled and doc.user_type=='System User', 'Service identity is disabled'
    else:
        doc=frappe.get_doc({'doctype':'User','email':user,'first_name':'Telegram ERP Bot',
            'enabled':1,'user_type':'System User','send_welcome_email':0,
            'roles':[{'role':role}], 'api_key':payload['api_key'],'api_secret':payload['api_secret']})
        doc.insert()
    assert set(frappe.get_roles(user)) <= {role,'All','Guest','Desk User'}, 'Unexpected service roles'
    frappe.set_user(user)
    from warehouse_core.availability import require_access
    require_access()
    for dt in (*doctypes,'Sales Order','Purchase Order','Payment Entry','Stock Entry','Knowledge Article'):
        for permission in ('create','write','delete','submit','cancel','amend'):
            assert not frappe.has_permission(dt,permission), f'Unexpected {permission} access to {dt}'
    assert not frappe.has_permission('User','create'), 'Unexpected user-administration access'
    assert not frappe.has_permission('User','write',doc='Administrator'), 'Unexpected administrator access'
    for dt in ('Sales Order','Purchase Order','Payment Entry','Sales Invoice','Purchase Invoice'):
        assert not frappe.has_permission(dt,'read'), 'Unexpected financial/order access'
    frappe.set_user('Administrator')
    assert roles()==before, 'Existing role assignments changed'
    frappe.db.commit()
    print(json.dumps({'ok':True,'user':user,'role':role,'business_access':'read-only stock','existing_roles_preserved':True}))
except Exception as error:
    frappe.db.rollback()
    import traceback
    print('PROVISION_ERROR:'+json.dumps({'type':type(error).__name__,
        'check':str(error) if isinstance(error,AssertionError) else None,
        'locations':[(f.name,f.lineno) for f in traceback.extract_tb(error.__traceback__)[-4:]]}))
    sys.exit(1)
finally: frappe.destroy()
'''


def main():
    target = ROOT / '.env.ai-telegram'
    original = target.read_text(encoding='utf-8')
    values = dict(line.split('=', 1) for line in original.splitlines() if '=' in line and not line.startswith('#'))
    existing = values.get('ERP_API_TOKEN', '')
    key, secret = existing.split(':', 1) if existing else (secrets.token_hex(16), secrets.token_hex(24))
    payload = {'user': USER, 'role': ROLE, 'api_key': key, 'api_secret': secret}
    result = subprocess.run(['docker', 'compose', 'exec', '-T', 'backend',
        '/home/frappe/frappe-bench/env/bin/python', '-c', PROVISION], cwd=ROOT,
        input=json.dumps(payload), capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        # Errors may contain user documents or credential-bearing stack locals.
        for line in result.stdout.splitlines():
            if line.startswith('PROVISION_ERROR:'): print(line)
        raise SystemExit('ERP bot provisioning failed; transaction rolled back, secret output suppressed.')
    if not existing:
        target.write_text(original.rstrip() + '\nERP_API_TOKEN=' + key + ':' + secret + '\n', encoding='utf-8')
    print(result.stdout.strip())
    print('ERP credential stored only in ignored .env.ai-telegram; existing OpenAI key unchanged.')


if __name__ == '__main__': main()
