"""Outbound-only Telegram pilot relay. No Telegram polling and no OpenAI key."""
import json
import os
import sqlite3
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, build_opener, HTTPRedirectHandler
from erp import ERP_SEND, ERP_ROUTE, ERP_OPTIONS, ERP_COMBINED, ERP_USAGE, HELP, knowledge_text, render, render_auto

WORKER = 'https://mtech-telegram-bot.notuxius.workers.dev/ai-bridge'
GATEWAY = 'http://ai-gateway:8091'
UNAVAILABLE = 'З’єднання з Вектор ERP зараз недоступне. Спробуйте ще раз, коли зв’язок із сервером ERP відновиться. Звичайне меню бота доступне.'


def blocked_reply(reason):
    explanations = {
        'credit_snapshot_in_future': 'Час сервера не узгоджується з даними API-коштів.',
        'credit_expired': 'Строк підтверджених API-коштів закінчився.',
        'cutoff_95_percent': 'ШІ зупинено захисним лімітом витрат 95%.',
        'pricing_needs_verification': 'Потрібна перевірка актуальних тарифів ШІ.',
        'ai_disabled': 'Виклики ШІ вимкнені в ERP.',
    }
    # This is a reply to an owner's question, never a proactive usage alert.
    explanation = explanations.get(reason, 'Виклики ШІ тимчасово заблоковані захистом витрат.')
    return explanation + '\n\nПрямий пошук працює: напишіть «Покажи залишки» або артикул, наприклад SERVO-1.'


class Clarification(str):
    """A routing question awaiting a choice."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(url, payload=None, token=None):
    headers = {'Content-Type': 'application/json', 'User-Agent': 'VectorERP-TelegramPilot/1.0'}
    if token:
        headers['Authorization'] = ('token ' if url in (ERP_SEND, ERP_OPTIONS, ERP_ROUTE, ERP_COMBINED, ERP_USAGE) else 'Bearer ') + token
    req = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                  headers=headers, method='POST' if payload is not None else 'GET')
    with build_opener(NoRedirect()).open(req, timeout=230 if url == ERP_COMBINED else 105 if url == ERP_SEND else 15) as response:
        maximum = 200_000 if url == ERP_COMBINED else 100_000
        raw = response.read(maximum + 1)
        if len(raw) > maximum:
            raise ValueError('Response too large')
        return json.loads(raw)


class Bridge:
    def __init__(self, path, token, transport=request, erp_token=None):
        self.token, self.transport, self.erp_token = token, transport, erp_token
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY, claim_id TEXT NOT NULL, text TEXT, state TEXT NOT NULL)''')
        # A crash between submission and persistence has unknown billing outcome.
        # Deliver a failure response; never repeat the model call.
        self.db.execute("UPDATE results SET text=?,state='ready' WHERE state='started'", (UNAVAILABLE,))
        self.db.commit()

    def cloud(self, payload):
        return self.transport(WORKER, payload, self.token)

    def flush(self):
        for job_id, claim, text, state in self.db.execute("SELECT id,claim_id,text,state FROM results WHERE state IN ('ready','clarify')").fetchall():
            result = self.cloud({'action': 'complete', 'id': job_id, 'claim_id': claim, 'text': text, 'clarify': state == 'clarify'})
            if result.get('ok') is not True:
                raise ValueError('Completion not acknowledged')
            self.db.execute("UPDATE results SET text=NULL,state='sent' WHERE id=?", (job_id,))
            self.db.commit()

    def answer(self, prompt):
        message = prompt.removeprefix('auto: ').strip()
        if message.casefold() == '/usage':
            sections = []
            for url, token, label in [(ERP_USAGE, self.erp_token, 'Ліміт запитань'), (GATEWAY + '/usage', None, 'Шлюз ШІ')]:
                try:
                    data = self.transport(url, {} if url == ERP_USAGE else None, token)
                    sections.append((data['message'] if url == ERP_USAGE else data)['text'])
                except (OSError, KeyError, ValueError):
                    sections.append(label + ' · дані недоступні.')
            try:
                sections.append(self.cloud({'action': 'cloud_usage'})['text'])
            except (OSError, KeyError, ValueError):
                sections.append('Cloudflare · дані недоступні.')
            return '\n\n'.join(sections)
        if message.casefold().strip('!? .') in ('/help', 'help', 'допомога', 'привіт', 'hello', 'що ти вмієш', 'що ти можеш'):
            return HELP
        if message.startswith('чернетка: '):
            requests = [{'mode': 'draft', 'message': message.removeprefix('чернетка: ')}]
        else:
            plan = self.transport(ERP_ROUTE, {'message': message, 'count_clarification': 1}, self.erp_token)['message']
            requests = plan['requests']
            if plan.get('message'):
                return plan['message']
            if not requests:
                return Clarification(plan['clarification'])
            if any(q['mode'] not in ('availability', 'knowledge') for q in requests) or len(requests) > 2:
                raise ValueError('Invalid assistant route')
        needs_ai = any('availability_filters' not in q for q in requests)
        if needs_ai:
            try:
                status = self.transport(GATEWAY + '/status')
            except Exception:
                return blocked_reply(None)
            if status.get('can_request') is not True:
                return blocked_reply(status.get('block_reason'))
        if len(requests) == 2:
            answer = self.transport(ERP_COMBINED, {'message': message}, self.erp_token)['message']
            if not answer['ok']:
                return answer['message']
            return render_auto(*answer['results'])
        question, = requests
        result = self.transport(ERP_SEND, question, self.erp_token)['message']
        results = {question['mode']: result}
        if 'knowledge' in results:
            return '📚 База знань\n' + knowledge_text(results['knowledge'], 3300)
        if 'draft' in results:
            return 'Чернетка відповіді клієнту\n' + knowledge_text(results['draft'], 3300)
        return render(result, conversational=needs_ai)

    def tick(self):
        self.flush()
        ready = False
        try:
            # Readiness describes ERP connectivity, not permission to spend on AI.
            ready = bool(self.erp_token) and self.transport(ERP_OPTIONS, token=self.erp_token).get('message', {}).get('ok') is True
        except Exception:
            ready = False
        claim = {'action': 'claim', 'ready': ready}
        job = self.cloud(claim).get('job')
        if not job or not ready:
            return
        if (type(job.get('id')) is not int or not isinstance(job.get('claim_id'), str)
                or not isinstance(job.get('prompt'), str) or not 1 <= len(job['prompt']) <= 2000):
            raise ValueError('Invalid job')
        # Persist before any paid request. Duplicate or replayed jobs are inert.
        if self.db.execute('SELECT id FROM results WHERE id=?', (job['id'],)).fetchone():
            return
        self.db.execute("INSERT INTO results(id,claim_id,state) VALUES(?,?,'started')", (job['id'], job['claim_id']))
        self.db.commit()
        text, state = UNAVAILABLE, "ready"
        try:
            answer = self.answer(job['prompt'])
            if isinstance(answer, str) and answer.strip():
                if isinstance(answer, Clarification):
                    state = "clarify"
                # JS/Telegram count UTF-16 units; leave room for non-BMP text.
                text = answer.encode('utf-16-le')[:6800].decode('utf-16-le', errors='ignore')
        except Exception:
            pass  # Guard handles uncertain billing; do not repeat or reveal errors.
        self.db.execute("UPDATE results SET text=?,state=? WHERE id=?", (text, state, job['id']))
        self.db.commit()
        self.flush()


def cloud_usage_handler(token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path != '/cloud-usage':
                self.send_error(404)
                return
            try:
                report = request(WORKER, {'action': 'cloud_usage'}, token)['text']
                if not isinstance(report, str) or len(report) > 1200:
                    raise ValueError('Invalid quota report')
            except (OSError, ValueError, KeyError):
                self.send_error(503)
                return
            body = json.dumps({'text': report}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return Handler


def main():
    token = os.environ.get('AI_BRIDGE_TOKEN', '')
    if len(token) < 32:
        raise SystemExit('AI bridge credential is missing')
    bridge = Bridge(os.environ.get('AI_BRIDGE_DB', '/data/bridge.sqlite3'), token,
                    erp_token=os.environ.get('ERP_API_TOKEN'))
    # Docker network only. ERP never receives the cloud bridge credential.
    server = ThreadingHTTPServer(('0.0.0.0', 8092), cloud_usage_handler(token))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        try:
            bridge.tick()
        except Exception:
            print('AI bridge temporarily unavailable; no paid request is retried.', flush=True)
        time.sleep(15)


if __name__ == '__main__':
    main()
