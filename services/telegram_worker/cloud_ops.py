"""Deployment checks without printing credentials or customer records.

Use from the repository root with `python -m services.telegram_worker.cloud_ops ...`.
Cloudflare authentication is read from Wrangler's existing credential store.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import socket
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from services.telegram_bot.__main__ import credentials, load_env
from services.telegram_bot.transport import Telegram, ApiError
from services.telegram_bot.commands import menu_commands
from .export_sqlite import TABLES

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = '3a9bd3cbf25c195f1635a9576379f117'
DATABASE = 'a3521b98-1965-49ac-b02d-f34daa87567c'
WORKER = 'https://mtech-telegram-bot.notuxius.workers.dev'

def cf(path, payload=None):
    config = Path(os.environ['APPDATA']) / 'xdg.config/.wrangler/config/default.toml'
    token = tomllib.loads(config.read_text(encoding='utf-8'))['oauth_token']
    request = urllib.request.Request('https://api.cloudflare.com/client/v4/accounts/' + ACCOUNT + '/' + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'Cloudflare HTTP {error.code}') from None
    if not result.get('success'):
        raise RuntimeError('Cloudflare operation failed')
    return result['result']

def query(sql, params=None):
    return cf(f'd1/database/{DATABASE}/query', {'sql': sql, 'params': params or []})[0]['results']

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['verify-import', 'status', 'webhook', 'database-info',
                                          'list-requests', 'show-request', 'set-status', 'retry-failed', 'configure-menu'])
    parser.add_argument('--snapshot')
    parser.add_argument('--ip-address', help='Optional verified IPv4 for temporary Telegram DNS propagation failures')
    parser.add_argument('--number', type=int)
    parser.add_argument('--status', choices=['new', 'in_progress', 'closed'])
    args = parser.parse_args()
    load_env(ROOT / '.env.telegram')
    api = Telegram(credentials()[0])
    if args.command == 'verify-import':
        if not args.snapshot: raise ValueError('Supply --snapshot')
        db = sqlite3.connect(f'file:{Path(args.snapshot).resolve().as_posix()}?mode=ro', uri=True)
        for table in TABLES:
            if table in ('subscriptions', 'customer_profiles') and not db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name=? AND type='table'", (table,)).fetchone():
                assert not query(f'SELECT chat_id FROM {table} LIMIT 1'), f'Remote {table} absent from legacy snapshot'
                continue
            columns = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
            local = db.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
            remote = query(f'SELECT * FROM "{table}" ORDER BY 1')
            # D1 JSON represents whole-valued REALs as ints; Python equality handles this.
            assert [tuple(row[col] for col in columns) for row in remote] == local, f'{table} mismatch'
            print(table, len(local), 'rows match exactly')
        identity = api.call('getMe')
        assert query("SELECT value FROM meta WHERE key='bot_id'")[0]['value'] == str(identity['id'])
        print('Bot identity matches migrated database.')
        db.close()
    elif args.command == 'configure-menu':
        identity = api.call('getMe')
        assert query("SELECT value FROM meta WHERE key='bot_id'")[0]['value'] == str(identity['id'])
        for language in ('', 'uk'):
            api.call('setMyCommands', {'commands': menu_commands(), 'language_code': language})
            assert api.call('getMyCommands', {'language_code': language}) == menu_commands()
        api.call('setChatMenuButton', {'menu_button': {'type': 'commands'}})
        print('Default command menus verified; existing chat menus refresh on their next interaction.')
    elif args.command == 'list-requests':
        print(json.dumps(query('SELECT id,flow,status,created_at FROM requests ORDER BY id DESC LIMIT 50'), ensure_ascii=False, indent=2))
    elif args.command == 'show-request':
        if not args.number: raise ValueError('Supply --number')
        print(json.dumps(query('SELECT * FROM requests WHERE id=?', [args.number]), ensure_ascii=False, indent=2))
    elif args.command == 'set-status':
        if not args.number or not args.status: raise ValueError('Supply --number and --status')
        if not query('SELECT id FROM requests WHERE id=?', [args.number]): raise ValueError('Request not found')
        query('UPDATE requests SET status=? WHERE id=?', [args.status, args.number])
        print('Request status updated.')
    elif args.command == 'retry-failed':
        rows = query("UPDATE outbox SET status='pending',attempts=0,available_at=0,error=NULL WHERE status='failed' AND method!='answerCallbackQuery' RETURNING id")
        print('Queued for retry:', len(rows))
    elif args.command == 'database-info':
        result = cf(f'd1/database/{DATABASE}')
        print(json.dumps({k:v for k,v in result.items() if k in ('name','uuid','file_size','version','num_tables','running_in_region','read_replication','created_at','max_size')}, indent=2))
    elif args.command == 'status':
        hook = api.call('getWebhookInfo')
        print(json.dumps({k:hook.get(k) for k in ('url','pending_update_count','last_error_date','last_error_message','max_connections','ip_address')}, indent=2))
        print('Requests:', query('SELECT COUNT(*) AS n FROM requests')[0]['n'])
        print('Outbox:', json.dumps(query('SELECT status,COUNT(*) AS n FROM outbox GROUP BY status')))
    elif args.command == 'webhook':
        # No pending updates are dropped. Stop the Docker poller before invoking this.
        secrets = json.loads((ROOT / '.artifacts/telegram-worker-secrets.json').read_text())
        identity = api.call('getMe')
        assert query("SELECT value FROM meta WHERE key='bot_id'")[0]['value'] == str(identity['id'])
        request = urllib.request.Request(WORKER + '/health', headers={'User-Agent': 'MTech-Deployment/1.0'})
        with urllib.request.urlopen(request, timeout=20) as response:
            health = json.load(response)
            assert health['ready'] and health['enabled']
        current = api.call('getWebhookInfo')
        if current['url'] and current['url'] != WORKER + '/webhook': raise ValueError('Unexpected existing webhook; unchanged')
        payload = {'url': WORKER + '/webhook', 'secret_token': secrets['WEBHOOK_SECRET'],
            'allowed_updates': ['message','callback_query'], 'max_connections': 1, 'drop_pending_updates': False}
        if args.ip_address:
            addresses = {row[4][0] for row in socket.getaddrinfo('mtech-telegram-bot.notuxius.workers.dev', 443, socket.AF_INET)}
            assert args.ip_address in addresses, 'IP does not match current Worker DNS'
            payload['ip_address'] = args.ip_address
        try:
            api.call('setWebhook', payload)
        except ApiError:
            # Telegram may clear the old webhook even when a replacement fails DNS.
            # Restore its known-good HTTPS endpoint/IP before propagating an error.
            if current['url'] == WORKER + '/webhook' and current.get('ip_address'):
                payload['ip_address'] = current['ip_address']
                api.call('setWebhook', payload)
                print('DNS-based reconfiguration failed; existing verified endpoint/IP restored.')
            else:
                raise
        assert api.call('getWebhookInfo')['url'] == WORKER + '/webhook'
        print('Telegram webhook is active; pending updates retained.')

if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except (ApiError, RuntimeError, ValueError, AssertionError, OSError) as error:
        # Avoid urllib tracebacks, which can embed Telegram credentials.
        print(type(error).__name__ + ': ' + (str(error) if isinstance(error, (ApiError, RuntimeError, AssertionError, ValueError)) else 'operation failed'))
        sys.exit(1)
