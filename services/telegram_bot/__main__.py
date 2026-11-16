"""Run: python -m services.telegram_bot --help"""

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from .content import DEFAULT_CONTENT, load_content
from .commands import menu_commands
from .storage import Store
from .transport import ApiError, Telegram, dispatch, preflight


def load_env(path):
    """Read a simple KEY=VALUE file; existing environment values take precedence."""
    if not Path(path).exists():
        return
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("Invalid environment-file entry")
        value = value.strip()
        if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def credentials():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    staff = os.environ.get("TELEGRAM_STAFF_CHAT_ID", "")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
        raise ValueError("Set TELEGRAM_BOT_TOKEN in .env.telegram (do not paste it into chat)")
    if not re.fullmatch(r"-?[1-9][0-9]*", staff):
        raise ValueError("Set TELEGRAM_STAFF_CHAT_ID to the numeric ID of the private staff destination")
    return token, int(staff)


@contextmanager
def worker_lock(database):
    """One polling/outbox worker per database, including on Windows."""
    path = Path(str(database) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        if not os.fstat(handle.fileno()).st_size:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("A bot worker already owns this database") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def bind_identity(store, bot_id):
    row = store.db.execute("SELECT value FROM meta WHERE key='bot_id'").fetchone()
    if row and row[0] != str(bot_id):
        raise ValueError("Database belongs to another bot; choose a separate database")
    with store.db:
        store.db.execute("INSERT OR REPLACE INTO meta VALUES('bot_id',?)", (str(bot_id),))


def run(database, content):
    token, staff = credentials()
    api = Telegram(token)
    with worker_lock(database):
        store = Store(database)
        try:
            identity = preflight(api, staff)
            bind_identity(store, identity["id"])
            # Default commands also serve clients whose Telegram language is not Ukrainian.
            for language in ("", "uk"):
                api.call("setMyCommands", {"commands": menu_commands(), "language_code": language})
            api.call("setChatMenuButton", {"menu_button": {"type": "commands"}})
            store.reconcile_command_menus()
            logging.info("MTech Telegram worker started; content reviewed %s", content["reviewed_at"])
            maintenance_at, cooldown_until = 0, 0
            while True:
                if time.time() >= maintenance_at:
                    store.maintenance()
                    maintenance_at = time.time() + 3600
                if time.time() < cooldown_until:
                    time.sleep(min(5, cooldown_until - time.time()))
                    continue
                cooldown = dispatch(store, api)
                if cooldown:
                    cooldown_until = time.time() + cooldown
                    continue
                try:
                    updates = api.call("getUpdates", {"offset": store.offset(), "timeout": 5, "limit": 50,
                                                       "allowed_updates": ["message", "callback_query"]})
                except ApiError as error:
                    if error.code in {401, 409}:
                        raise  # Invalid token / another poller: do not silently loop.
                    logging.warning("Polling: %s", error)
                    cooldown_until = time.time() + max(5, error.retry_after or 0)
                    continue
                for update in updates:
                    store.process(update, content, staff)
                cooldown_until = time.time() + dispatch(store, api)
        finally:
            store.close()


def simulate(content):
    """Use the real engine/store with an in-memory DB and no network or credentials."""
    store = Store(":memory:")
    chat_id, update_id, choices = 100, 0, []
    print("Локальний симулятор. /1, /2, … — кнопки; /photo — тестове фото; /quit — вихід.\n")
    value = "/start"
    try:
        while True:
            update_id += 1
            message = {"chat": {"id": chat_id, "type": "private"}, "from": {"id": chat_id}}
            update = {"update_id": update_id, "message": message}
            if value.startswith("/") and value[1:].isdigit() and 0 < int(value[1:]) <= len(choices):
                button = choices[int(value[1:]) - 1]
                if button.get("url"):
                    print("Відкрити:", button["url"])
                    value = input("\nВи: ").strip()
                    continue
                if button.get("callback_data"):
                    update = {"update_id": update_id, "callback_query": {
                        "id": str(update_id), "from": {"id": chat_id}, "message": message,
                        "data": button["callback_data"]}}
                elif button.get("request_contact"):
                    message["contact"] = {"user_id": chat_id, "phone_number": "+380000000000"}
                else:
                    message["text"] = button["text"]
            elif value == "/photo":
                message["photo"] = [{"file_id": "demo-" + str(update_id), "file_unique_id": str(update_id)}]
            else:
                message["text"] = value
            store.process(update, content, None)
            # Simulator is not throttled; the production handler remains unchanged.
            with store.db:
                state = store.state(chat_id)
                state.pop("throttle", None)
                store.db.execute("UPDATE sessions SET state=? WHERE chat_id=?", (json.dumps(state), chat_id))
            while True:
                pending = store.due()
                if not pending:
                    break
                for row in pending:
                    payload = json.loads(row["payload"])
                    if row["method"] == "sendMessage":
                        print("\nMTech:", payload["text"])
                        markup = payload.get("reply_markup", {})
                        if "inline_keyboard" in markup or "keyboard" in markup:
                            choices = [b if isinstance(b, dict) else {"text": b}
                                       for r in markup.get("inline_keyboard", markup.get("keyboard", [])) for b in r]
                            for i, button in enumerate(choices, 1):
                                print(f"  /{i} {button['text']}")
                    store.delivered(row["id"])
            value = input("\nВи: ").strip()
            if value == "/quit":
                break
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        print("\nЛокально створено заявок:", len(store.requests()))
        store.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="MTech Telegram customer bot")
    parser.add_argument("--env-file", default=".env.telegram")
    parser.add_argument("--database", help="SQLite path (default: TELEGRAM_DATABASE or data/telegram/bot.sqlite3)")
    parser.add_argument("--content", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run")
    check = sub.add_parser("check-config")
    check.add_argument("--online", action="store_true", help="Read-only Telegram account checks")
    sub.add_parser("simulate")
    sub.add_parser("list-requests")
    show = sub.add_parser("show-request")
    show.add_argument("number", type=int)
    status = sub.add_parser("set-status")
    status.add_argument("number", type=int)
    status.add_argument("status", choices=["new", "in_progress", "closed"])
    sub.add_parser("health")
    sub.add_parser("retry-failed")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        load_env(args.env_file)
        database = args.database or os.environ.get("TELEGRAM_DATABASE", "data/telegram/bot.sqlite3")
        content = load_content(args.content or os.environ.get("TELEGRAM_CONTENT", str(DEFAULT_CONTENT)))
        if args.command == "simulate":
            simulate(content)
        elif args.command == "check-config":
            print("Content OK; reviewed", content["reviewed_at"])
            token, staff = credentials()
            print("Required credentials are configured (values hidden).")
            if args.online:
                preflight(Telegram(token), staff)
                print("Telegram account and staff destination checks passed.")
        elif args.command == "run":
            run(database, content)
        else:
            store = Store(database)
            try:
                if args.command == "list-requests":
                    print(json.dumps(store.requests(), ensure_ascii=False, indent=2))
                elif args.command == "show-request":
                    print(json.dumps(store.request(args.number), ensure_ascii=False, indent=2))
                elif args.command == "set-status":
                    store.set_status(args.number, args.status)
                    print("Request status updated.")
                elif args.command == "health":
                    print(json.dumps(store.health(), indent=2))
                elif args.command == "retry-failed":
                    print("Queued for retry:", store.retry_failed())
            finally:
                store.close()
    except KeyboardInterrupt:
        logging.info("Stopped")
    except (ApiError, ValueError, OSError, sqlite3.Error) as error:
        # Do not dump tracebacks or environment values to operational logs.
        logging.error("%s", str(error) if isinstance(error, (ApiError, ValueError)) else type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
