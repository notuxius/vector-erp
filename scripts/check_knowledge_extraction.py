"""Upload a synthetic document to an explicitly named empty test draft. No paid API calls."""
import argparse
import base64
import hashlib
import http.cookiejar
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlencode, quote
from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler, Request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--article", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--format", choices=("docx", "xlsx", "txt"), default="docx")
    args = parser.parse_args()
    assert args.title.startswith("Тест витягування "), "Only synthetic test drafts are allowed"
    root = Path(__file__).resolve().parents[1]
    env = dict(line.split("=", 1) for line in (root / ".env").read_text().splitlines()
               if line and not line.startswith("#"))
    base = f"http://localhost:{int(env.get('HTTP_PORT', '8080'))}"
    session = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
    session.open(base + "/api/method/login", data=urlencode({"usr": "Administrator", "pwd": env["ADMIN_PASSWORD"]}).encode(), timeout=20).close()
    csrf = None
    try:
        with session.open(base + "/desk/knowledge-base", timeout=30) as response:
            csrf = re.search(r'frappe\.csrf_token = "([^"]+)"', response.read().decode()).group(1)
        article_url = base + "/api/resource/Knowledge%20Article/" + quote(args.article, safe="")
        with session.open(article_url, timeout=20) as response:
            article = json.load(response)["data"]
        assert article["title"] == args.title and article["status"] == "Draft"
        assert not article.get("content") and not article.get("allowed_users") and not article.get("allowed_roles")
        assert not article.get("cloud_allowed")
        sys.path.insert(0, str(root / "apps/warehouse_core"))
        from warehouse_core.verify_knowledge_extraction import make_docx, make_xlsx
        if args.format == "xlsx":
            raw, marker = make_xlsx(), "XlsxMarker"
        elif args.format == "txt":
            marker = "TxtMarker Українська інструкція"
            raw = ("\ufeff" + marker).encode("utf-8")
        else:
            marker = "Синтетична перевірка"
            raw = make_docx(marker)
        data = urlencode({"article": args.article, "filename": "extraction-synthetic." + args.format, "data": base64.b64encode(raw).decode()}).encode()
        with session.open(Request(base + "/api/method/warehouse_core.knowledge.upload", data=data,
                                 headers={"X-Frappe-CSRF-Token": csrf}), timeout=30) as response:
            result = json.load(response)["message"]
        assert result["extraction_status"] == "Extracted" and result["extracted_characters"] > 0
        with session.open(article_url, timeout=20) as response:
            article = json.load(response)["data"]
        assert marker in article["content"]
        if args.format == "xlsx":
            assert "A1: Назва товару" in article["content"] and "D2: 2024-01-01" in article["content"]
        assert article["status"] == "Draft" and not article.get("approved_by") and not article["cloud_allowed"]
        with session.open(base + "/api/method/warehouse_core.knowledge.download?name=" + quote(result["name"]), timeout=20) as response:
            assert "no-store" in response.headers["Cache-Control"]
            assert hashlib.sha256(response.read()).digest() == hashlib.sha256(raw).digest()
        print(json.dumps({"format": args.format, "passed": ["authenticated upload", "expected text appended", "draft and local-only preserved", "protected download matches original"], "paid_calls": 0}))
    finally:
        if csrf:
            session.open(Request(base + "/api/method/logout", data=b"", headers={"X-Frappe-CSRF-Token": csrf}), timeout=20).close()


if __name__ == "__main__":
    main()
