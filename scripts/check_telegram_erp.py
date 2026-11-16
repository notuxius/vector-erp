"""Live local ERP authorization checks; no paid AI or Telegram messages."""
import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def main():
    values = dict(line.split('=', 1) for line in (ROOT / '.env.ai-telegram').read_text().splitlines() if '=' in line)
    base = 'http://localhost:8080'
    opener = build_opener(ProxyHandler({}), NoRedirect())
    count = 0

    def call(path, payload=None, method=None, authenticated=True, expected=200):
        nonlocal count
        headers = {'Content-Type': 'application/json'}
        if authenticated: headers['Authorization'] = 'token ' + values['ERP_API_TOKEN']
        req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                      method=method or ('POST' if payload is not None else 'GET'), headers=headers)
        try:
            with opener.open(req, timeout=30) as response:
                status, body = response.status, json.load(response)
        except HTTPError as error:
            status, body = error.code, None
        assert status == expected, f'Unexpected HTTP status {status} for {path}'
        count += 1
        return body

    options = '/api/method/warehouse_core.availability.options'
    send = '/api/method/warehouse_core.ai_chat.send'
    route = '/api/method/warehouse_core.ai_chat.route'
    payload = {'message': 'Шпиндельний картридж 2,2 кВт — характеристики'}
    assert [q['mode'] for q in call(route, payload)['message']['requests']] == ['knowledge']
    call(route, payload, authenticated=False, expected=403)
    call(route, {**payload, 'cmd': 'frappe.client.set_value'}, expected=403)
    call(route, method='GET', expected=403)
    call(options, authenticated=False, expected=403)
    assert call(options)['message']['ok']
    result = call(send, {'message': 'Пошук залишків', 'mode': 'availability',
                         'availability_filters': {'stock_state': 'all'}, 'history': []})['message']
    assert result['ok'] and 'availability' in result
    print('Read-only live stock lookup passed; visible positions:', len(result['availability']['rows']))
    call('/api/resource/Item', {'item_code': 'FORBIDDEN-TELEGRAM-PROBE'}, expected=403)
    call('/api/resource/Sales%20Order', expected=403)
    call('/api/method/frappe.share.add', {'doctype': 'Item', 'name': 'FORBIDDEN-TELEGRAM-PROBE'}, expected=403)
    call('/api/method/frappe.client.set_value', {'doctype': 'Item', 'name': 'FORBIDDEN-TELEGRAM-PROBE'}, expected=403)
    call('/api/method/frappe.client.delete', {'doctype': 'Item', 'name': 'FORBIDDEN-TELEGRAM-PROBE'}, expected=403)
    call('/api/method/frappe.auth.get_logged_user', expected=403)
    # Punctuation has no searchable terms, so a valid draft request cannot invoke the model.
    state = call('/api/method/warehouse_core.ai_chat.question_usage', {})['message']
    if state['retry_at']:
        time.sleep(max(0, state['retry_at'] - time.time()))
    draft = {'message': '...', 'mode': 'draft'}
    result = call(send, draft)['message']
    assert result['ok'] and result['sources'] == [] and result['text']
    call(send, draft, authenticated=False, expected=403)
    call(send, {**draft, 'cmd': 'frappe.client.set_value'}, expected=403)
    call(send, {'message': 'test', 'mode': 'general'}, expected=403)
    call(send, {'message': 'test', 'mode': 'availability', 'cmd': 'frappe.client.set_value'}, expected=403)
    call(send, method='GET', expected=403)
    call('/api/v2/document/Item', expected=403)
    print(count, 'HTTP checks passed; no business write, paid request, or Telegram message.')


if __name__ == '__main__': main()
