"""HTTP authentication/CSRF checks for the Desk widget. No paid AI calls."""
import http.cookiejar
import json
import time
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler, Request


def main():
    root = Path(__file__).resolve().parents[1]
    env = dict(line.split("=", 1) for line in (root / ".env").read_text().splitlines()
               if line and not line.startswith("#"))
    base = f"http://localhost:{int(env.get('HTTP_PORT', '8080'))}"
    guest = build_opener(ProxyHandler({}))
    session = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
    endpoint = base + "/api/method/warehouse_core.ai_chat."

    def blocked(opener, request, expected):
        try:
            opener.open(request, timeout=15)
        except HTTPError as error:
            assert error.code in expected, f"Unexpected HTTP status: {error.code}"
        else:
            raise AssertionError("Unauthorized request was accepted")

    blocked(guest, endpoint + "status", (403,))
    blocked(guest, Request(endpoint + "send", data=urlencode({"message": "guest test"}).encode()), (403,))
    credentials = urlencode({"usr": "Administrator", "pwd": env["ADMIN_PASSWORD"]}).encode()
    with session.open(base + "/api/method/login", data=credentials, timeout=15) as response:
        assert json.load(response)["message"] == "Logged In"
    try:
        with session.open(base + "/desk", timeout=30) as response:
            html = response.read().decode()
        csrf = re.search(r'frappe\.csrf_token = "([^"]+)"', html).group(1)
        assert "/js/ai_chat.js?v=" in html and "/css/ai_chat.css?v=" in html
        for kind in ("js", "css"):
            with session.open(base + f"/assets/warehouse_core/{kind}/ai_chat.{kind}", timeout=15) as response:
                assert response.read() == (root / f"apps/warehouse_core/warehouse_core/public/{kind}/ai_chat.{kind}").read_bytes()
        with session.open(endpoint + "status", timeout=15) as response:
            status = json.load(response)["message"]
        assert set(status) == {"available", "message"}, "Billing or internal details leaked"
        blocked(session, endpoint + "send?message=method-test", (403, 405))
        data = urlencode({"message": "csrf-test", "history": "[]"}).encode()
        blocked(session, Request(endpoint + "send", data=data), (400, 403))
        data = urlencode({"message": "validation-test", "history": '[{"role":"system","content":"bad"}]'}).encode()
        with session.open(Request(endpoint + "send", data=data, headers={"X-Frappe-CSRF-Token": csrf}), timeout=15) as response:
            assert json.load(response)["message"]["ok"] is False
        data = urlencode({"message": "general-mode-test", "mode": "general"}).encode()
        with session.open(Request(endpoint + "send", data=data, headers={"X-Frappe-CSRF-Token": csrf}), timeout=15) as response:
            assert json.load(response)["message"]["ok"] is False, "General AI mode remains accessible"
        def wait_for_question():
            with session.open(Request(endpoint + "question_usage", data=b"", headers={"X-Frappe-CSRF-Token": csrf}), timeout=15) as response:
                state = json.load(response)["message"]
            assert state["remaining"] and not state["busy"], "Admin allowance is in use"
            if state["retry_at"]:
                time.sleep(max(0, state["retry_at"] - time.time()))

        token = "0" * 32
        blocked(guest, Request(endpoint + "reset", data=urlencode({"conversation_id": token}).encode()), (403,))
        blocked(session, endpoint + "reset?conversation_id=" + token, (403, 405))
        blocked(session, Request(endpoint + "reset", data=urlencode({"conversation_id": token}).encode()), (400, 403))
        wait_for_question()
        data = urlencode({"message": "missing-context-test", "conversation_id": token}).encode()
        with session.open(Request(endpoint + "send", data=data, headers={"X-Frappe-CSRF-Token": csrf}), timeout=15) as response:
            result = json.load(response)["message"]
            assert result["ok"] is False and result["reset_conversation"] is True
        availability = base + "/api/method/warehouse_core.availability.options"
        blocked(guest, availability, (403,))
        with session.open(availability, timeout=15) as response:
            options = json.load(response)["message"]
        assert options["ok"] and set(options) == {"ok", "companies", "warehouses", "groups"}
        data = urlencode({"message": "Пошук залишків", "mode": "availability", "availability_filters": "{}"}).encode()
        blocked(session, Request(endpoint + "send", data=data), (400, 403))
        wait_for_question()
        with session.open(Request(endpoint + "send", data=data, headers={"X-Frappe-CSRF-Token": csrf}), timeout=30) as response:
            result = json.load(response)["message"]
        assert result["ok"] and "availability" in result
        stock = result["availability"]
        assert len(stock["rows"]) <= 20 and stock["checked_at"] and stock["timezone"]
        assert "_memory" not in result and "next_after" in stock
        for row in stock["rows"]:
            assert row["free_qty"] == max(0, row["actual_qty"] - max(0, row["reserved_stock"]))
            assert not {"stock_value", "valuation_rate", "customer"}.intersection(row)
        data = urlencode({"message": "Оновити", "mode": "availability", "conversation_id": result["conversation_id"]}).encode()
        wait_for_question()
        with session.open(Request(endpoint + "send", data=data, headers={"X-Frappe-CSRF-Token": csrf}), timeout=30) as response:
            refreshed = json.load(response)["message"]
        assert refreshed["ok"] and refreshed["availability"]["filters"] == stock["filters"]
        assert refreshed["availability"]["checked_at"] >= stock["checked_at"]
        print(json.dumps({"availability_passed": ["guest options denied", "scoped options", "CSRF enforced",
            "live stock response", "bounded rows and no valuation", "server-owned follow-up refresh"], "paid_calls": 0}))
        print(json.dumps({"passed": ["guest access denied", "authenticated status redacted", "POST-only chat", "CSRF enforced", "invalid context rejected", "general mode rejected", "versioned assets deployed", "reset guest denied", "reset POST-only", "reset CSRF enforced", "missing context rejected"], "available": status["available"], "paid_calls": 0}))
    finally:
        session.open(Request(base + "/api/method/logout", data=b"",
                             headers={"X-Frappe-CSRF-Token": csrf}), timeout=15).close()


if __name__ == "__main__":
    main()
