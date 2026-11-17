"""Internal text-only AI endpoint; administration is local CLI only."""
import argparse
import errno
import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from http.client import HTTPSConnection
from urllib.error import HTTPError
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler

from guard import Blocked, Ledger, MODEL
from notifications import Notifier

MAX_BODY = 140_000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class RequestNotSent(Exception):
    pass


class ProviderConnection(HTTPSConnection):
    def connect(self):
        try:
            super().connect()
        except OSError as error:
            # TCP/TLS setup finishes before http.client writes the API request.
            raise RequestNotSent() from error


class ProviderHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(ProviderConnection, request, context=self._context)


def openai_request(body, observe_limits, request_id, evidence):
    key = os.environ.get('OPENAI_API_KEY', '')
    if not key:
        raise Blocked('api_key_missing')
    request = Request('https://api.openai.com/v1/responses',
                      data=json.dumps(body).encode(), method='POST',
                      headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                               'X-Client-Request-Id': request_id})
    opener = build_opener(NoRedirect(), ProviderHTTPSHandler())
    try:
        try:
            response = opener.open(request, timeout=90)
        except RequestNotSent as error:
            if error.__cause__.errno not in (errno.ENETUNREACH, errno.EHOSTUNREACH):
                raise
            response = opener.open(request, timeout=90)
    except HTTPError as error:
        response = error
    with response:
        evidence['http_status'] = response.status
        trace = response.headers.get('x-request-id', '')
        if re.fullmatch(r'[A-Za-z0-9_-]{1,160}', trace):
            evidence['provider_request_id'] = trace
        observe_limits(response.headers)
        if isinstance(response, HTTPError):
            raise response
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('Provider response too large')
        return json.loads(raw)


def generate(ledger, payload, transport=None):
    if not isinstance(payload, dict) or set(payload) - {'prompt', 'max_output_tokens'}:
        raise ValueError('Only prompt and max_output_tokens are supported')
    prompt = payload.get('prompt')
    if not isinstance(prompt, str) or not 1 <= len(prompt) <= 32_000:
        raise ValueError('Prompt must contain 1 to 32000 characters')
    limit = payload.get('max_output_tokens', 512)
    request_id = ledger.reserve(limit)
    evidence = {}
    try:
        body = {'model': MODEL, 'input': prompt, 'max_output_tokens': limit,
                              'reasoning': {'effort': 'none'}, 'service_tier': 'default', 'store': False,
                              'prompt_cache_options': {'mode': 'implicit', 'ttl': '30m'}}
        response = transport(body) if transport else openai_request(body, ledger.observe_api_limits, request_id, evidence)
        if response.get('model') != MODEL or response.get('service_tier') != 'default':
            raise ValueError('Unexpected provider model or service tier')
        if response.get('status') not in ('completed', 'incomplete'):
            raise ValueError('Unknown provider completion state')
        usage = response['usage']
        texts = []
        for item in response['output']:
            if item.get('type') != 'message':
                raise ValueError('Unexpected non-text provider output')
            for content in item['content']:
                if content.get('type') == 'output_text':
                    texts.append(content['text'])
                elif content.get('type') != 'refusal':
                    raise ValueError('Unexpected provider content')
        text = '\n'.join(texts)
        ledger.settle(request_id, usage['input_tokens'], usage['output_tokens'], limit,
                      usage.get('input_tokens_details'))
        return {'text': text, 'request_id': request_id}
    except RequestNotSent:
        ledger.settle(request_id, 0, 0, limit)
        raise Blocked('provider_connection_unavailable') from None
    except Exception as error:
        # Even an HTTP error/timeout can follow billable work. Keep the full hold.
        evidence['error_type'] = type(error).__name__
        ledger.uncertain(request_id, evidence)
        raise Blocked('provider_outcome_uncertain_ai_disabled') from None


def usage_text(status):
    budget = status['allowance_usd']
    spent, held = status['accounted_usage_usd'], status['reserved_usd']
    lines = ['Бюджет ШІ · Вектор ERP']
    if budget:
        room = max(0, status['cutoff_usd'] - spent - held)
        lines.extend([f'Витрачено ${spent:.6f} з ${budget:g} ({spent / budget:.1%}).',
                      f'У резерві ${held:.6f}. До зупинки на {status["cutoff_percent"]}% доступно ${room:.6f}.'])
    else:
        lines.append('Бюджет не налаштовано.')
    lines.append('Шлюз дозволяє запити.' if status['can_request'] else 'Шлюз блокує запити до моделі.')
    lines.append(f'\nOpenAI API · {MODEL}')
    if status['api_limits']:
        stamp = datetime.fromtimestamp(status['api_limits_observed_at'], timezone.utc)
        lines.append(f'За відповіддю API від {stamp:%Y-%m-%d %H:%M:%S} UTC')
        for key, label in [('requests', 'Запити'), ('tokens', 'Токени'), ('project-tokens', 'Токени проєкту')]:
            if key in status['api_limits']:
                values = status['api_limits'][key]
                fields = [f'{name} {values[field]}' for field, name in
                          [('remaining', 'залишок'), ('limit', 'ліміт'), ('reset', 'тоді до скидання')]
                          if field in values]
                lines.append(label + ' — ' + ', '.join(fields))
    else:
        lines.append('Шлюз ще не отримав даних про ліміти запитів і токенів від API.')
    return '\n'.join(lines)


def handler(ledger, notifier=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Do not log business prompts or provider responses.

        def reply(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/health':
                self.reply(200, {'status': 'ok'})
            elif self.path == '/usage':
                self.reply(200, {'text': usage_text(ledger.status(include_events=False))})
            elif self.path == '/status':
                status = ledger.status()
                if notifier:
                    status['notifications'] = notifier.status()
                self.reply(200, status)
            else:
                self.reply(404, {'error': 'not_found'})

        def do_POST(self):
            if self.path != '/generate':
                self.reply(404, {'error': 'not_found'})
                return
            try:
                self.connection.settimeout(10)
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY:
                    raise ValueError('Invalid request size')
                payload = json.loads(self.rfile.read(length))
                self.reply(200, generate(ledger, payload))
            except Blocked as error:
                self.reply(403, {'error': str(error)})
            except (ValueError, TimeoutError):
                self.reply(400, {'error': 'invalid_request'})
            except Exception:
                # Database corruption/unavailability must never fall back to OpenAI.
                self.reply(503, {'error': 'spending_guard_unavailable'})
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['serve', 'status', 'disable', 'enable', 'budget', 'snapshot', 'test-notifications'])
    parser.add_argument('--file', help='Local budget JSON (period and allowance), or legacy snapshot')
    parser.add_argument('--test-id', help='Stable identifier to avoid repeating a delivery test')
    parser.add_argument('--keep-reserved', action='append', default=[], metavar='REQUEST_ID',
                        help='Enable with this unresolved request still fully reserved')
    args = parser.parse_args()
    if args.keep_reserved and args.command != 'enable':
        parser.error('--keep-reserved requires enable')
    path = Path(os.environ.get('AI_LEDGER_PATH', '/data/ledger.sqlite3'))
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(str(path))
    notifier = Notifier(ledger, os.environ)
    if args.command == 'serve':
        ledger.recover()
        threading.Thread(target=notifier.run, daemon=True, name='telegram-alerts').start()
        try:
            ThreadingHTTPServer(('0.0.0.0', 8091), handler(ledger, notifier)).serve_forever()
        finally:
            notifier.stop.set()
    else:
        try:
            if args.command in ('budget', 'snapshot'):
                raw = sys.stdin.read() if args.file == '-' else Path(args.file).read_text(encoding='utf-8')
                ledger.snapshot(json.loads(raw))
            elif args.command == 'enable':
                ledger.enable(args.keep_reserved)
            elif args.command == 'disable':
                ledger.disable()
            elif args.command == 'test-notifications':
                notifier.queue_test(args.test_id)
            status = ledger.status()
            status['notifications'] = notifier.status()
            print(json.dumps(status, indent=2))
        except (Blocked, ValueError) as error:
            parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
