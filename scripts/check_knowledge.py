"""HTTP knowledge access/method/CSRF checks. No changes to business data or paid calls."""
import http.cookiejar
import json
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
    endpoint = base + "/api/method/warehouse_core.knowledge."
    checks = []

    def blocked(opener, request, expected=(403,)):
        try:
            opener.open(request, timeout=20)
        except HTTPError as error:
            assert error.code in expected, f"Unexpected HTTP status: {error.code}"
        else:
            raise AssertionError("Unauthorized request accepted")

    for action in ("search?query=test", "attachments?article=KB-00001", "download?name=missing"):
        blocked(guest, endpoint + action)
    checks.append("guest search, attachment listing and download denied")
    credentials = urlencode({"usr": "Administrator", "pwd": env["ADMIN_PASSWORD"]}).encode()
    session.open(base + "/api/method/login", data=credentials, timeout=20).close()
    csrf = None
    try:
        with session.open(base + "/desk/knowledge-base", timeout=30) as response:
            html = response.read().decode()
        csrf = re.search(r'frappe\.csrf_token = "([^"]+)"', html).group(1)
        with session.open(endpoint + "search", timeout=20) as response:
            result = json.load(response)["message"]
        assert result["can_edit"] and result["can_publish"]
        assert any(a["name"] == "KB-00001" for a in result["articles"])
        checks.append("authenticated search and starter draft")
        blocked(session, endpoint + "upload?article=KB-00001&filename=x.txt&data=YWJj", (403, 405))
        blocked(session, Request(endpoint + "upload", data=urlencode({"article": "KB-00001", "filename": "x.txt", "data": "YWJj"}).encode()), (400, 403))
        checks.append("upload requires POST and CSRF")
        data = urlencode({"message": "zzzzknowledgehttptest", "mode": "knowledge", "history": "[]"}).encode()
        with session.open(Request(base + "/api/method/warehouse_core.ai_chat.send", data=data,
                                 headers={"X-Frappe-CSRF-Token": csrf}), timeout=20) as response:
            result = json.load(response)["message"]
        assert result["ok"] and result["sources"] == []
        checks.append("no-source answer without provider request")
        print(json.dumps({"passed": checks, "paid_calls": 0}))
    finally:
        if csrf:
            session.open(Request(base + "/api/method/logout", data=b"",
                                 headers={"X-Frappe-CSRF-Token": csrf}), timeout=20).close()


if __name__ == "__main__":
    main()
