"""Minimal Telegram Bot API adapter. Never log URLs, tokens or customer payloads."""

import json
import logging
import urllib.error
import urllib.request


class ApiError(Exception):
    def __init__(self, code=0, retry_after=None):
        self.code, self.retry_after = code, retry_after
        # Telegram error descriptions and urllib exceptions may contain secrets.
        super().__init__(f"Telegram API error {code or 'network'}")


class Telegram:
    def __init__(self, token):
        self._base = "https://api.telegram.org/bot" + token + "/"

    def call(self, method, payload=None):
        payload = payload or {}
        request = urllib.request.Request(self._base + method,
            data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=int(payload.get("timeout", 0)) + 15) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            try:
                result = json.loads(error.read())
            except (ValueError, OSError):
                result = {}
            raise ApiError(error.code, result.get("parameters", {}).get("retry_after")) from None
        except (OSError, ValueError):
            raise ApiError() from None
        if not result.get("ok"):
            raise ApiError(result.get("error_code", 0), result.get("parameters", {}).get("retry_after"))
        return result["result"]


def preflight(api, staff_chat_id):
    identity = api.call("getMe")
    webhook = api.call("getWebhookInfo")
    if webhook.get("url"):
        raise ValueError("This bot already has a webhook. Use a dedicated bot or remove its webhook deliberately first.")
    chat = api.call("getChat", {"chat_id": staff_chat_id})
    if chat.get("type") not in {"private", "group", "supergroup"}:
        raise ValueError("Staff destination must be a private chat or a restricted staff group")
    if chat.get("type") in {"group", "supergroup"}:
        member = api.call("getChatMember", {"chat_id": staff_chat_id, "user_id": identity["id"]})
        if member.get("status") in {"left", "kicked"} or member.get("can_send_messages") is False:
            raise ValueError("Bot cannot send messages to the staff chat")
        if member.get("status") not in {"administrator", "creator"}:
            permissions = chat.get("permissions", {})
            if permissions.get("can_send_messages") is False or permissions.get("can_send_photos") is False:
                raise ValueError("Staff group must allow the bot to send messages and photos")
    return identity


def dispatch(store, api, budget=100):
    """At-least-once outbound delivery; lead creation is exactly once per draft."""
    sent = 0
    while sent < budget:
        rows = store.due(min(50, budget - sent))
        if not rows:
            break
        for row in rows:
            try:
                api.call(row["method"], json.loads(row["payload"]))
            except ApiError as error:
                if error.code == 401:
                    raise
                permanent = error.code in {400, 403, 404} or row["method"] == "answerCallbackQuery"
                terminal = store.failed(row, str(error), error.retry_after, permanent)
                logging.log(logging.ERROR if terminal else logging.WARNING,
                            "Outbound item %s: %s; %s", row["id"], error,
                            "operator retry required" if terminal else "retry queued")
                if error.code == 429:
                    return max(1, error.retry_after or 5)
            else:
                store.delivered(row["id"])
            sent += 1
    return 0
