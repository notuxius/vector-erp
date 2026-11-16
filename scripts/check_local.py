"""Verify the local login page and generated administrator credentials."""
import http.cookiejar
import html as html_module
from hashlib import sha256
import json
import os
import re
from pathlib import Path
import urllib.parse
import urllib.request


def check_realtime(opener, base):
    """Exercise Socket.IO authentication through the actual reverse proxy."""
    port = urllib.parse.urlsplit(base).port
    endpoint = base + "/socket.io/?EIO=4&transport=polling"
    for host, origin, fetch_site, accepted in [
        (f"127.0.0.1:{port}", f"http://127.0.0.1:{port}", None, True),
        (f"localhost:{port}", f"http://localhost:{port}", None, True),
        (f"localhost:{port}", None, "same-origin", True),
        (f"localhost:{port}", None, "cross-site", False),
        (f"localhost:{port}", None, None, False),
        (f"localhost:{port}", "http://untrusted.example.test", "same-origin", False),
    ]:
        headers = {"Host": host, "Content-Type": "text/plain;charset=UTF-8"}
        if origin:
            headers["Origin"] = origin
        if fetch_site:
            headers["Sec-Fetch-Site"] = fetch_site

        def request(url, data=None):
            with opener.open(urllib.request.Request(url, data=data, headers=headers), timeout=15) as response:
                return response.read().decode()

        handshake = request(endpoint)
        assert handshake.startswith("0"), "Missing Engine.IO handshake"
        session_url = endpoint + "&sid=" + urllib.parse.quote(json.loads(handshake[1:])["sid"])
        try:
            request(session_url, b"40/frontend,")
            packets = request(session_url).split("\x1e")
            if accepted:
                assert any(packet.startswith("40/frontend,") for packet in packets), \
                    f"Local realtime authentication failed for {host}: {packets}"
            else:
                assert any(packet.startswith("44/frontend,") and "Invalid origin" in packet for packet in packets), \
                    "Realtime must reject mismatched origins"
        finally:
            request(session_url, b"1")


def check_banking(opener, base):
    for route in ("/banking", "/banking/statement-importer"):
        with opener.open(base + route, timeout=30) as response:
            page = response.read().decode()
        encoded, _ = json.JSONDecoder().raw_decode(page.split("frappe.boot = JSON.parse(", 1)[1])
        boot = json.loads(encoded)
        assert "frappe._messages = frappe.boot.__messages || {};" in page
        assert "frappe._translations_loaded = Promise.resolve();" in page
        assert "frappe.translate.get_boot_translations" not in page, "Banking still fetches translations too late"
        if boot["lang"] == "uk":
            assert "<title>Банківські операції |" in page
            for source, translated in {
                "Banking": "Банківські операції",
                "This Month": "Цього місяця",
                "No bank accounts found": "Банківських рахунків не знайдено",
                "Configure Bank Accounts": "Налаштувати банківські рахунки",
            }.items():
                assert boot["__messages"].get(source) == translated, source


def main():
    env_path = Path(__file__).resolve().parents[1] / ".env"
    values = dict(
        line.split("=", 1)
        for line in env_path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    port = int(os.environ.get("HTTP_PORT") or values.get("HTTP_PORT", "8080"))
    base = f"http://127.0.0.1:{port}"
    logo_bytes = (env_path.parent / "apps/warehouse_core/warehouse_core/public/images/vector-mark.svg").read_bytes()
    logo = "/assets/warehouse_core/images/vector-mark.svg?v=" + sha256(logo_bytes).hexdigest()[:12]
    favicon_bytes = (env_path.parent / "apps/warehouse_core/warehouse_core/public/images/mtech-mark.svg").read_bytes()
    favicon = "/assets/warehouse_core/images/mtech-mark.svg?v=" + sha256(favicon_bytes).hexdigest()[:12]
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )
    with opener.open(base + "/login", timeout=30) as response:
        assert response.status == 200, "Login page did not return HTTP 200"
        login_html = response.read().decode()
        assert f'src="{logo}"' in login_html, "Login logo is stale"
        assert f'href="{favicon}"' in login_html, "Login favicon is stale"
    with opener.open(base + logo, timeout=30) as response:
        assert response.read() == logo_bytes, "Vector logo asset is stale or unavailable"
    with opener.open(base + favicon, timeout=30) as response:
        assert response.read() == favicon_bytes, "MTech favicon asset is stale or unavailable"
    # Fresh guests and browsers retaining an English cookie must both see the
    # Ukrainian site entry page. An explicit language URL still takes precedence.
    public = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for path, headers, language in [
        ("/login?redirect-to=%2Fdesk%2Fsetup%2Fhome", {"Accept-Language": "en-US,en;q=0.9"}, "uk"),
        ("/login", {"Accept-Language": "en-US", "Cookie": "preferred_language=en"}, "uk"),
        ("/login?_lang=en", {"Accept-Language": "uk-UA,uk;q=0.9"}, "en"),
    ]:
        with public.open(urllib.request.Request(base + path, headers=headers), timeout=30) as response:
            page = response.read().decode()
        assert re.search(r'<html[^>]*lang="' + language + '"', page), "Wrong guest login language"
        expected = ("Увійти", "Вітаємо! Увійдіть, щоб продовжити.", "Електронна пошта",
                    "Пароль", "Забули пароль?", "Продовжити", "Надіслати посилання") if language == "uk" else (
                    "Sign In", "Welcome! Please sign in to continue.", "Email", "Password", "Forgot password?")
        for label in expected:
            assert re.search(r">\s*" + re.escape(label) + r"\s*<", page), f"Missing login label: {label}"
    with opener.open(base + "/assets/warehouse_core/js/uk_ui.js", timeout=30) as response:
        assert response.status == 200 and "translateDuringRender" in response.read().decode(), \
            "Ukrainian UI asset is unavailable from the frontend"
    with opener.open(base + "/assets/warehouse_core/js/nova_poshta.js", timeout=30) as response:
        assert response.status == 200 and '"Nova Poshta Shipment"' in response.read().decode(), \
            "Nova Poshta UI asset is unavailable from the frontend"
    with opener.open(base + "/assets/warehouse_core/js/onboarding_labels.js", timeout=30) as response:
        assert response.status == 200 and "vectorBranding" in response.read().decode(), \
            "Product branding asset is unavailable from the frontend"
    with opener.open(base + "/assets/warehouse_core/js/user_navigation.js", timeout=30) as response:
        assert response.status == 200 and "vectorUserLabel" in response.read().decode(), \
            "User navigation asset is unavailable from the frontend"
    data = urllib.parse.urlencode(
        {"usr": "Administrator", "pwd": values["ADMIN_PASSWORD"]}
    ).encode()
    with opener.open(base + "/api/method/login", data=data, timeout=30) as response:
        result = json.load(response)
        assert result.get("message") == "Logged In", "Administrator login failed"
    try:
        with opener.open(base + "/api/method/frappe.auth.get_logged_user", timeout=30) as response:
            assert json.load(response)["message"] == "Administrator", "Session verification failed"
        check_realtime(opener, base)
        check_banking(opener, base)
        with opener.open(base + "/desk", timeout=30) as response:
            html = response.read().decode()
        csrf = re.search(r'frappe\.csrf_token = "([^"]+)"', html)
        assert csrf, "Desk CSRF token is missing"
        opener.addheaders.append(("X-Frappe-CSRF-Token", csrf[1]))
        match = re.search(r"frappe\.boot = (.*?);\s*frappe\._messages", html, re.S)
        assert match, "Desk boot data is missing"
        boot = json.loads(match[1])
        initial_html = html.split("<script type=\"text/javascript\">")[0]
        assert re.search(r'class="centered splash">\s*<img src="' + re.escape(logo) + '"', initial_html), \
            "Vector splash logo must be present before Desk JavaScript runs"
        assert f'href="{favicon}"' in initial_html, "Desk favicon is stale"
        assert "/assets/erpnext/images/" not in initial_html, "Upstream logo remains in initial HTML"
        assert boot["app_logo_url"] == logo, "Desk app logo is stale"
        language = boot["lang"]
        company = "MTech"
        assert boot["sysdefaults"]["company"] == company, "Default company is stale"
        with opener.open(base + "/api/resource/Company/" + urllib.parse.quote(company), timeout=30) as response:
            company_doc = json.load(response)["data"]
        assert company_doc["company_name"] == company and company_doc["abbr"] == "MTech"
        for doctype, name, print_format in [
            ("Sales Order", "SO-001", "Vector Payment Request"),
            ("Delivery Note", "DISPATCH-DN-001", "Vector Delivery Note"),
        ]:
            url = base + "/printview?" + urllib.parse.urlencode(
                {"doctype": doctype, "name": name, "format": print_format, "no_letterhead": 1})
            with opener.open(url, timeout=30) as response:
                rendered = html_module.unescape(response.read().decode())
            assert company in rendered, f"Company name missing in {print_format}"
        if language in ("en", "uk"):
            product = "Вектор ERP" if language == "uk" else "Vector ERP"
            assert boot["__messages"]["ERPNext"] == product, "Desk product branding is stale"
            assert boot["__messages"]["Get to know ERPNext"] == (
                "Get to know Vector ERP" if language == "en" else "Знайомство з Вектор ERP"
            ), "Onboarding branding is stale"
        assert next(app for app in boot["app_data"] if app["app_name"] == "erpnext")["app_title"] == ("Вектор ERP" if language == "uk" else "Vector ERP")
        app_icons = {app["app_name"]: app["app_logo_url"] for app in boot["app_data"]}
        for path, app_name in [
            ("/desk/stock", "erpnext"),
            ("/desk/stock/item", "erpnext"),
            ("/desk/item", "erpnext"),
            ("/desk/setup/home", "erpnext"),
            ("/desk/query-report/Order%20Readiness", "erpnext"),
            ("/desk/build", "frappe"),
            ("/desk/build/doctype", "frappe"),
            ("/desk/user", "frappe"),
            # An invalid shell prefix must follow the entity's actual app.
            ("/desk/geo/item", "erpnext"),
        ]:
            for _ in range(2):  # Direct visit and refresh, using the same session.
                with opener.open(base + path, timeout=30) as response:
                    page = response.read().decode().split('<script type="text/javascript">')[0]
                assert re.search(r'class="centered splash">\s*<img src="'
                                 + re.escape(app_icons[app_name]) + '"', page), \
                    f"Wrong app splash in initial HTML for {path}"
        for app_name in ("erpnext", "frappe"):
            with opener.open(base + app_icons[app_name], timeout=30) as response:
                assert response.status == 200 and b"<svg" in response.read(), \
                    f"Loading icon is unavailable for {app_name}"
        assert "/assets/warehouse_core/js/branding.js" not in html, "Blocked legacy script is still included"
        for asset in ("onboarding_labels", "user_navigation"):
            assert f"/assets/warehouse_core/js/{asset}.js?v=" in html, f"Missing script: {asset}"
        report_url = base + "/api/method/frappe.desk.query_report.run?" + urllib.parse.urlencode(
            {"report_name": "Review", "filters": "{}", "ignore_prepared_report": "1"}
        )
        with opener.open(report_url, timeout=30) as response:
            report = json.load(response)["message"]
            assert len(report["columns"]) == 5 and isinstance(report["result"], list), \
                "Quality Review report did not return columns and results"
        for name, filters, columns in [
            ("Order Readiness", {"company": "MTech"}, 16),
            ("Daily Dispatch List", {"company": "MTech", "include_overdue": 1}, 8),
            ("Machine Service History", {"company": "MTech", "serial_no": "CNC-SN-001"}, 6),
        ]:
            url = base + "/api/method/frappe.desk.query_report.run?" + urllib.parse.urlencode(
                {"report_name": name, "filters": json.dumps(filters), "ignore_prepared_report": "1"})
            with opener.open(url, timeout=30) as response:
                report = json.load(response)["message"]
                assert len(report["columns"]) == columns and isinstance(report["result"], list), name
    finally:
        opener.open(base + "/api/method/logout", data=b"", timeout=30).close()
    print("PASS: login, UI assets, Desk branding, Vector ERP tile, company identity/default and printed documents, session, realtime origin checks, Quality Review, Order Readiness, Daily Dispatch List, Machine Service History and logout.")


if __name__ == "__main__":
    main()
