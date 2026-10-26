"""Display branding; upstream identifiers and stored records remain unchanged."""

from hashlib import sha256
from pathlib import Path
import re


# Operate only on catalog prose, never DOM text or user-entered values. Preserve
# HTML attributes, URLs, code spans and example parameter values verbatim.
_PROTECTED = re.compile(r"(<[^>]+>|`[^`]*`|https?://[^\s<>]+)")
_PRODUCT = re.compile(r"(?<![\w/=.@-])ERPNext(?![\w/-]|\.[\w])")


def brand_message(message, language):
    name = "Вектор ERP" if language == "uk" else "Vector ERP"
    return "".join(part if index % 2 else _PRODUCT.sub(name, part)
                   for index, part in enumerate(_PROTECTED.split(message)))


def brand_catalog(translations, current):
    """Apply the same branding on every catalog rebuild; return English entries."""
    sources = dict(current)
    sources.update({identifier: (source, context)
                    for identifier, (source, _, context) in translations.items()})
    sources["ERPNext"] = ("ERPNext", None)
    english = {}
    # App captions use one name in both navigation and the apps screen.
    english["Frappe Framework"] = ("Frappe Framework", "Framework", "")
    for identifier, (source, context) in sources.items():
        branded = brand_message(source, "en")
        if branded != source:
            english[identifier] = (source, branded, context or "")
            original = translations.get(identifier)
            if original:
                translations[identifier] = (source, brand_message(original[1], "uk"), context or "")
            elif source == "ERPNext":
                translations[identifier] = (source, "Вектор ERP", context or "")
    return english


_logo_version = sha256(
    (Path(__file__).parent / "public/images/mtech-mark.svg").read_bytes()
).hexdigest()[:12]
MTECH_LOGO = f"/assets/warehouse_core/images/mtech-mark.svg?v={_logo_version}"

_vector_version = sha256(
    (Path(__file__).parent / "public/images/vector-mark.svg").read_bytes()
).hexdigest()[:12]
VECTOR_LOGO = f"/assets/warehouse_core/images/vector-mark.svg?v={_vector_version}"


def boot_session(bootinfo):
    # Rename only the administration shell's caption, retaining its route and
    # persistent identity. Personal Settings and other Setup sections stay intact.
    setup = bootinfo.get("module_sidebars", {}).get("Setup")
    if setup and setup.get("app") == "erpnext":
        setup["label"] = "Адміністрування" if bootinfo.get("lang") == "uk" else "Administration"
    # Keep the app identifier, routes, permission filtering and dock entries intact.
    for app in bootinfo.get("app_data", []):
        if app.get("app_name") == "erpnext":
            app["app_title"] = "Вектор ERP" if bootinfo.get("lang") == "uk" else "Vector ERP"
            app["app_logo_url"] = MTECH_LOGO
        elif app.get("app_name") == "frappe":
            app["app_title"] = "Адміністрування" if bootinfo.get("lang") == "uk" else "Framework"


def verify():
    """Exercise actual server dictionaries, including English and contextual text."""
    import csv
    import frappe
    from frappe.translate import get_all_translations

    installed = frappe.get_installed_apps()
    assert {"frappe", "erpnext", "warehouse_core"}.issubset(installed), installed
    from frappe.core.doctype.navbar_settings.navbar_settings import get_app_logo

    settings = frappe.get_single("Website Settings")
    for field in ("app_logo", "splash_image"):
        assert settings.get(field) == VECTOR_LOGO, field
    assert settings.favicon == MTECH_LOGO
    assert frappe.get_hooks("website_context")["favicon"][-1] == MTECH_LOGO
    assert get_app_logo() == VECTOR_LOGO
    assert frappe.get_hooks("website_context")["splash_image"][-1] == VECTOR_LOGO
    counts = {}
    for language, product in (("en", "Vector ERP"), ("uk", "Вектор ERP")):
        boot = {"lang": language, "app_data": [{"app_name": "erpnext"}]}
        boot_session(boot)
        assert boot["app_data"][0]["app_title"] == product
        assert boot["app_data"][0]["app_logo_url"] == MTECH_LOGO
        actual = get_all_translations(language)
        assert actual.get("ERPNext") == product
        assert actual.get("ERPNext Integrations") == (
            "Vector ERP Integrations" if language == "en" else "Інтеграції Вектор ERP")
        counts[language] = 0
        with (Path(__file__).parent / "translations" / f"{language}.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            for row in csv.reader(stream):
                if not row:
                    continue
                if brand_message(row[0], "en") == row[0]:
                    continue
                identifier = row[0] + (":" + row[2] if len(row) == 3 and row[2] else "")
                assert actual.get(identifier) == row[1].strip(), identifier
                assert brand_message(row[1], language) == row[1], identifier
                counts[language] += 1
        assert frappe._("ERPNext", lang=language) == product
    sample = '<a href="https://example.test/ERPNext">ERPNext</a> `ERPNext` sender=ERPNext /ERPNext ERPNext.com'
    assert brand_message(sample, "en") == sample.replace(">ERPNext<", ">Vector ERP<")
    return {"installed_apps": installed, "verified_branding_entries": counts}
