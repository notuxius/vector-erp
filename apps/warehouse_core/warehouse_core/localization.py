"""Build and audit the Ukrainian UI catalog against the installed Frappe version.

Run explicitly with bench execute warehouse_core.localization.build, then copy
translations/uk.csv back into source control and rebuild the application image.
No network calls, business-data changes, or runtime translation services.
"""
import csv
import json
import re
from collections import Counter
from pathlib import Path

import frappe
from babel.messages.pofile import read_po

ROOT = Path(__file__).parent
SOURCES = ROOT / "translation_sources"
PLACEHOLDER = re.compile(r"\{(?:\d+|[A-Za-z_]\w*)?(?:![rsa])?(?::[^{}]+)?\}|%(?:\([\w]+\))?[-+#0]*\d*(?:\.\d+)?[sdif](?![A-Za-z])")
HTML_TAG = re.compile(r"<(/?)(a|b|strong|em|i|u|br|p|div|span|code|pre|ul|li|ol|table|tr|td|th|h[1-6]|script|iframe|img|input|button)\b[^>]*>", re.I)


def placeholders(text):
    return Counter(PLACEHOLDER.findall(text))


def markup(text):
    return Counter((match[0] if match[1].lower() not in {"br", "img", "input"} else "", match[1].lower())
                   for match in HTML_TAG.findall(text))


def normalize(text):
    """Match JS escaped literals and Frappe's CSV newline decoding at runtime."""
    text = text.replace("\r\n", "\n").replace(r"\n", "\n").replace(r"\t", "\t")
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda match: chr(int(match[1], 16)), text)


def read_catalog(path):
    with path.open("rb") as stream:
        return read_po(stream)


def key(source, context=None):
    source = normalize(source)
    return source + ":" + context if context else source


def build():
    from warehouse_core.branding import brand_catalog

    translations = {}
    current = {}
    rejected = []
    catalogs = [("community", read_catalog(SOURCES / "community.uk.po"))]
    for app in ("frappe", "erpnext"):
        locale = Path(frappe.get_app_path(app, "locale"))
        for message in read_catalog(locale / "main.pot"):
            if message.id:
                assert isinstance(message.id, str), "Plural messages need explicit handling"
                current[key(message.id, message.context)] = (message.id, message.context)
        catalogs.append((app, read_catalog(locale / "uk.po")))
    for origin, catalog in catalogs:
        for message in catalog:
            if not message.id or not message.string or message.fuzzy:
                continue
            assert isinstance(message.id, str) and isinstance(message.string, str)
            if placeholders(message.id) != placeholders(message.string):
                rejected.append({"origin": origin, "source": message.id, "translation": message.string})
                continue
            translations[key(message.id, message.context)] = (
                message.id, message.string, message.context or "")
    seen = set()
    for filename in ("overrides.uk.csv", "supplemental.uk.csv", "navigation.uk.csv", "configuration.uk.csv"):
        with (SOURCES / filename).open(encoding="utf-8", newline="") as stream:
            for row in csv.reader(stream):
                if not row:
                    continue
                assert len(row) in (2, 3) and row[0] and row[1], row
                source, translated = normalize(row[0]), row[1]
                context = row[2] if len(row) == 3 else ""
                identifier = key(source, context)
                assert identifier not in seen, f"Duplicate override: {identifier}"
                assert placeholders(source) == placeholders(translated), f"Placeholder mismatch: {source}"
                seen.add(identifier)
                translations[identifier] = (source, translated, context)
                if filename == "configuration.uk.csv":
                    current[identifier] = (source, context or None)
    preserved = set(json.loads((SOURCES / "keep_english.json").read_text(encoding="utf-8")))
    supplemental = json.loads((SOURCES / "supplemental_sources.json").read_text(encoding="utf-8"))
    for entry in supplemental:
        current[key(entry["source"])] = (normalize(entry["source"]), None)
    for source in json.loads((SOURCES / "navigation_sources.json").read_text(encoding="utf-8")):
        current[key(source)] = (normalize(source), None)
    # These JSON descriptions are translated dynamically by ChartWidget and
    # are absent from the upstream gettext catalogs.
    for app in ("frappe", "erpnext"):
        for path in Path(frappe.get_app_path(app)).glob("*/dashboard_chart/*/*.json"):
            chart = json.loads(path.read_text(encoding="utf-8"))
            source = chart.get("empty_state_message")
            if source:
                current[key(source)] = (normalize(source), None)
    english = brand_catalog(translations, current)
    with (ROOT / "translations" / "en.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        for identifier in sorted(english):
            source, translated, context = english[identifier]
            assert placeholders(source) == placeholders(translated), source
            assert markup(source) == markup(translated), source
            writer.writerow((source, translated, context) if context else (source, translated))
    missing = [source for identifier, (source, context) in current.items()
               if identifier not in translations and source not in preserved]
    report = {
        "current_messages": len(current),
        "translated_current_messages": sum(identifier in translations for identifier in current),
        "preserved_current_messages": sum(identifier not in translations and source in preserved
                                          for identifier, (source, _) in current.items()),
        "catalog_entries": len(translations),
        "missing": sorted(set(missing)),
        "rejected_source_entries": rejected,
        "frappe_version": frappe.__version__,
    }
    for source, translated, context in translations.values():
        assert markup(source) == markup(translated), f"HTML tag mismatch: {source}"
    with (ROOT / "translations" / "uk.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        for identifier in sorted(translations):
            source, translated, context = translations[identifier]
            source = normalize(source)
            writer.writerow((source, translated, context) if context else (source, translated))
    (SOURCES / "coverage.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {k: len(v) if isinstance(v, list) else v for k, v in report.items()}


def verify():
    """Check the actual site dictionary, including context and cached values."""
    from frappe.translate import get_all_translations

    actual = get_all_translations("uk")
    seen = set()
    with (ROOT / "translations" / "uk.csv").open(encoding="utf-8", newline="") as stream:
        for row in csv.reader(stream):
            if not row:
                continue
            source, translated = row[:2]
            identifier = key(source, row[2] if len(row) == 3 else None)
            assert identifier not in seen, f"Duplicate translation: {identifier}"
            assert placeholders(source) == placeholders(translated), source
            assert markup(source) == markup(translated), source
            assert actual.get(identifier) == translated.strip(), f"Site translation mismatch: {identifier}"
            seen.add(identifier)
    report = json.loads((SOURCES / "coverage.json").read_text(encoding="utf-8"))
    assert not report["missing"], "Untranslated messages remain in the audited source set"
    assert not report["rejected_source_entries"], "Source entries require review"
    return {"verified_translations": len(seen), "audited_messages": report["current_messages"],
            "language": "uk", "missing_audited_messages": 0}
