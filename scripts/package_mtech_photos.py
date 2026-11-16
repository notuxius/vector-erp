"""Package original product photos and explicit external-editor return paths."""
import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "data/mtech-product-archive"


def csv_bytes(rows, fields):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "'" + str(value) if str(value).startswith(("=", "+", "-", "@")) else value
                         for key, value in row.items()})
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def main():
    products = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["products"]
    assets = {}
    for product in products:
        for image in product["gallery"] + product["description_images"]:
            entry = assets.setdefault(image["path"], {"image": image, "products": {}, "roles": set()})
            entry["products"][product["source_product_id"]] = product
            entry["roles"].add(image["role"])
    handoff = ROOT / "handoff"
    handoff.mkdir(exist_ok=True)
    version = 1
    while (handoff / f"mtech-original-photos-external-v{version}.zip").exists() or (ROOT / "edited" / f"external-v{version}").exists():
        version += 1
    batch = f"external-v{version}"
    destination = handoff / f"mtech-original-photos-{batch}.zip"
    rows, entries = [], []
    for original_path, entry in sorted(assets.items()):
        image = entry["image"]
        source = ROOT / original_path
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != image["sha256"]:
            raise ValueError("Original hash mismatch: " + original_path)
        relative = Path(original_path).relative_to("originals").with_suffix(".png")
        return_path = (Path("edited") / batch / relative).as_posix()
        related = list(entry["products"].values())
        rows.append({"original_path": original_path, "return_path": return_path,
                     "original_sha256": image["sha256"], "width": image["width"], "height": image["height"],
                     "roles": " | ".join(sorted(entry["roles"])),
                     "product_ids": " | ".join(p["source_product_id"] for p in related),
                     "product_codes": " | ".join(p["code"] for p in related),
                     "product_names": " | ".join(p["name"] for p in related)})
        entries.append({"original_path": original_path, "return_path": return_path,
                        "original_sha256": image["sha256"], "product_ids": sorted(entry["products"])})
    instructions = f"""MTech — оригінальні фото для зовнішнього редагування
Пакет: {batch}
Кількість оригіналів: {len(assets)}; пов'язаних товарів: {len(products)}.

1. Розпакуйте ZIP. Папка originals/ містить незмінені вихідні файли.
2. Результати зберігайте у edited/{batch}/, повторюючи підпапки originals/.
   Залишайте ім'я файлу без змін; розширення результату — .png.
   Точний шлях для кожного результату наведено в photo-map.csv, колонка return_path.
3. Оригінали не змінюйте та не перейменовуйте. Не нумеруйте файли заново.
4. Одна фотографія може використовуватися кількома товарами. Редагуйте кожен
   вихідний файл лише один раз. ID папки не визначає всі товари, які використовують фото.
5. Основні фото: акуратно видаліть стороннє тло, вирівняйте баланс білого,
   освітлення й контраст, за потреби застосуйте помірне усунення шуму та різкість.
   PNG має справжню прозорість, чисті краї без ореолу; сам товар залишається непрозорим.
6. Збережіть точну форму, пропорції, колір, матеріали, комплектність, написи,
   логотипи, екран, кріплення й конструктивні деталі. Не домальовуйте обладнання,
   не змінюйте модель і не відновлюйте нерозбірливі деталі вигадуванням.
   Не обрізайте товар; залишайте рівномірне вільне поле навколо нього.
7. Не зменшуйте роздільну здатність без потреби; не маскуйте поганий вихідник
   штучно вигаданими деталями. Проблемні фото позначайте в журналі.
8. Частина файлів — схеми, таблиці, колажі, логотипи та ілюстрації в описах.
   Не застосовуйте до них бездумно видалення тла; збережіть усі технічні позначення
   та важливий текст. За потреби пропустіть файл і поясніть причину.
9. Заповніть editing-log.csv: status = edited / unchanged / needs-review / skipped,
   notes = виконані зміни або причина пропуску. Поля original_path і return_path не змінюйте.
10. Поверніть ZIP із папкою edited/{batch}/ та заповненим editing-log.csv.
    Повторно надсилати originals/ не потрібно. Файли PSD/TIFF із шарами, якщо є,
    можна додати окремо, але фінальний PNG має залишатися за погодженим шляхом.

photo-map.csv — перелік оригіналів, точних шляхів повернення та пов'язаних товарів.
package-manifest.json — машинозчитувані ID та хеші оригіналів для перевірки сумісності.
Ціни, залишки, приватні дані ERP та попередні AI-чернетки до пакета не включені.
"""
    manifest = {"batch": batch, "created_at": datetime.now(timezone.utc).isoformat(),
                "product_count": len(products), "image_count": len(assets), "images": entries}
    temp = destination.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("EDITING-INSTRUCTIONS.uk.txt", instructions.encode("utf-8"))
        archive.writestr("photo-map.csv", csv_bytes(rows, list(rows[0])))
        archive.writestr("editing-log.csv", csv_bytes(
            [{"original_path": r["original_path"], "return_path": r["return_path"], "status": "", "notes": ""} for r in rows],
            ["original_path", "return_path", "status", "notes"]))
        archive.writestr("package-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        archive.writestr(f"edited/{batch}/", b"")
        for original_path in sorted(assets):
            archive.write(ROOT / original_path, original_path)
    with zipfile.ZipFile(temp) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError("ZIP checksum failed: " + bad)
        for entry in entries:
            if hashlib.sha256(archive.read(entry["original_path"])).hexdigest() != entry["original_sha256"]:
                raise ValueError("Packaged original mismatch: " + entry["original_path"])
    temp.replace(destination)
    (ROOT / "edited" / batch).mkdir(parents=True)
    (handoff / f"{batch}-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"zip": str(destination), "images": len(assets), "products": len(products),
                      "size_mb": round(destination.stat().st_size / 1024 / 1024, 1),
                      "return_folder": str(ROOT / "edited" / batch), "verification": "ZIP CRC and all original SHA-256 checks passed"}, indent=2))


if __name__ == "__main__":
    main()
