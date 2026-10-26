"""Developer helper: read source-index|Ukrainian rows from stdin (UTF-8).

Source indexes refer to the pinned supplemental_sources.json audit. An equals
sign as the translation explicitly preserves an identifier in English.
"""
import csv
import json
import sys
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "apps/warehouse_core/warehouse_core/translation_sources"
navigation = "--navigation" in sys.argv
if navigation:
    sources = [{"source": source} for source in json.loads((root / "navigation_sources.json").read_text(encoding="utf-8"))]
else:
    sources = json.loads((root / "supplemental_sources.json").read_text(encoding="utf-8"))
path = root / ("navigation.uk.csv" if navigation else "supplemental.uk.csv")
catalog = {row[0]: row[1] for row in csv.reader((root.parent / "translations/uk.csv").open(encoding="utf-8", newline=""))}
rows = list(csv.reader(path.open(encoding="utf-8", newline=""))) if path.exists() else []
translations = dict(rows)
preserved = set(json.loads((root / "keep_english.json").read_text(encoding="utf-8")))
for line in sys.stdin:
    if not line.strip():
        continue
    index, translated = line.rstrip("\r\n").split("|", 1)
    source = sources[int(index)]["source"]
    if translated == "=":
        preserved.add(source)
    else:
        assert translated and source not in translations, f"Duplicate or empty: {index}"
        if navigation:
            links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", source)
            if links:
                translated += " Довідка: " + ", ".join(f"[{catalog.get(label, label)}]({url})" for label, url in links) + "."
        translations[source] = translated
with path.open("w", encoding="utf-8", newline="") as stream:
    csv.writer(stream, lineterminator="\n").writerows(sorted(translations.items()))
(root / "keep_english.json").write_text(json.dumps(sorted(preserved), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Supplemental translations: {len(translations)}")
