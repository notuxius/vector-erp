"""Standalone parser: stdin bytes -> bounded JSON. Never import Frappe or execute documents."""
import io
import json
import logging
import re
import sys
import zipfile

MAX_FILE = 5 * 1024 * 1024
MAX_TEXT = 100_000
MAX_PAGES = 300
MAX_XML = 8 * 1024 * 1024
MAX_UNPACKED = 25 * 1024 * 1024
MAX_SHEETS = 50
MAX_CELLS = 50_000
MAX_GRID = 250_000
W = {"http://schemas.openxmlformats.org/wordprocessingml/2006/main",
     "http://purl.oclc.org/ooxml/wordprocessingml/main"}


class LimitExceeded(Exception):
    pass


def clean(text):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text).replace("\r\n", "\n").strip()


def bounded(parts):
    result = "\n\n".join(parts)
    if len(result) > MAX_TEXT:
        raise LimitExceeded
    return result


def pdf(raw):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(raw), strict=True)
    if reader.is_encrypted:
        return {"status": "Encrypted", "text": "", "message": "Захищений паролем PDF. Завантажте дозволену незашифровану копію."}
    if len(reader.pages) > MAX_PAGES:
        raise LimitExceeded
    parts, empty = [], []
    for number, page in enumerate(reader.pages, 1):
        text = clean(page.extract_text() or "")
        if text:
            parts.append(f"[Сторінка {number}]\n{text}")
            bounded(parts)
        else:
            empty.append(number)
    text = bounded(parts)
    if not text:
        return {"status": "No Text", "text": "", "message": "PDF не містить доступного тексту. Для сканів потрібне OCR; воно поки не виконується."}
    message = "Текст додано до чернетки. Перевірте порядок читання, таблиці та одиниці виміру."
    if empty:
        message += " Без тексту (можливі скани або порожні сторінки): " + ", ".join(map(str, empty)) + ". OCR не виконувався."
    return {"status": "Partial" if empty else "Extracted", "text": text, "message": message}


def tag(element):
    namespace, _, name = element.tag[1:].partition("}") if element.tag.startswith("{") else ("", "", element.tag)
    return name if namespace in W else ""


def inline(element):
    kind = tag(element)
    if kind in ("del", "instrText", "delText"):
        return ""
    if kind == "t":
        return element.text or ""
    if kind in ("br", "cr"):
        return "\n"
    if kind == "tab":
        return "\t"
    return "".join(inline(child) for child in element)


def blocks(element):
    for child in element:
        kind = tag(child)
        if kind == "del":
            continue
        if kind == "p":
            text = clean(inline(child))
            if text:
                yield text
        elif kind == "tbl":
            for row in child:
                if tag(row) == "tr":
                    cells = [" / ".join(blocks(cell)) for cell in row if tag(cell) == "tc"]
                    if any(cells):
                        yield " | ".join(cells)
        else:
            yield from blocks(child)


def validate_archive(archive):
    entries = archive.infolist()
    names = [entry.filename for entry in entries]
    if len(entries) > 1000 or len(names) != len(set(names)) or sum(e.file_size for e in entries) > MAX_UNPACKED:
        raise LimitExceeded
    for entry in entries:
        name = entry.filename
        if (name.startswith(("/", "\\")) or ".." in name.split("/") or "\\" in name
                or entry.flag_bits & 1 or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
            raise ValueError("unsupported archive entry")
        lower = name.lower()
        if "vbaproject" in lower or any(part in lower for part in ("/activex/", "/embeddings/", "/macrosheets/")):
            raise ValueError("active content is not supported")
    return names


def read_xml(archive, name):
    from defusedxml.ElementTree import fromstring
    info = archive.getinfo(name)
    if info.file_size > MAX_XML:
        raise LimitExceeded
    with archive.open(info) as stream:
        data = stream.read(MAX_XML + 1)
    if len(data) > MAX_XML:
        raise LimitExceeded
    return fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)


def docx(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = validate_archive(archive)
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise ValueError("not a DOCX document")

        types = read_xml(archive, "[Content_Types].xml")
        content_types = [node.get("ContentType", "") for node in types]
        if any("macroEnabled" in value or "vbaProject" in value for value in content_types):
            raise ValueError("macro-enabled file")
        if not any(node.get("PartName") == "/word/document.xml" and node.get("ContentType") ==
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml" for node in types):
            raise ValueError("not a Word document")
        sections = [("word/document.xml", "Основний текст")]
        sections += [(name, "Колонтитул" if "header" in name or "footer" in name else "Примітки")
                     for name in sorted(names) if re.fullmatch(r"word/(?:header\d+|footer\d+|footnotes|endnotes)\.xml", name)]
        parts = []
        for name, label in sections:
            document = read_xml(archive, name)
            lines = []
            for text in blocks(document):
                lines.append(text)
                bounded(parts + lines)
            if lines:
                parts.append(f"[{label}]\n" + "\n".join(lines))
                bounded(parts)
    text = clean(bounded(parts))
    return {"status": "Extracted" if text else "No Text", "text": text,
            "message": "Текст додано до чернетки. Перевірте таблиці та форматування; зображення не розпізнавалися." if text else
                       "DOCX не містить доступного тексту. Текст у зображеннях не розпізнається."}


def txt(raw):
    text = raw.decode("utf-8-sig")
    if "\x00" in text:
        raise ValueError("binary text file")
    text = bounded([clean(text)])
    return {"status": "Extracted" if text else "No Text", "text": text,
            "message": "Текст UTF-8 додано до чернетки. Перевірте зміст перед публікацією." if text else
                       "TXT не містить доступного тексту."}


def xlsx(raw):
    from datetime import date, datetime, time
    from openpyxl import load_workbook
    # Validate every XML part before openpyxl sees it, including relationships/styles.
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = validate_archive(archive)
        if "xl/workbook.xml" not in names:
            raise ValueError("not an XLSX workbook")
        cells = 0
        for name in names:
            if name.lower().endswith((".xml", ".rels")):
                root = read_xml(archive, name)
                for node in root.iter():
                    kind = node.tag.rsplit("}", 1)[-1]
                    if kind == "Override" and any(x in node.get("ContentType", "").lower()
                                                  for x in ("macroenabled", "vbaproject", "macrosheet")):
                        raise ValueError("macro-enabled workbook")
                    if kind == "c":
                        cells += 1
                        if cells > MAX_CELLS:
                            raise LimitExceeded
    books = []
    try:
        for data_only in (False, True):
            books.append(load_workbook(io.BytesIO(raw), read_only=False, data_only=data_only,
                                       keep_vba=False, keep_links=False))
        formulas, values = books
        if len(formulas.worksheets) > MAX_SHEETS:
            raise LimitExceeded
        parts, hidden, missing, formula_count, grid = [], False, 0, 0, 0
        total = 0
        def append(text):
            nonlocal total
            total += len(text) + (2 if parts else 0)
            if total > MAX_TEXT:
                raise LimitExceeded
            parts.append(text)
        for sheet in formulas.worksheets:
            if sheet.sheet_state != "visible":
                hidden = True
                continue
            grid += sheet.max_row * sheet.max_column
            if sheet.max_row > 10_000 or sheet.max_column > 256 or grid > MAX_GRID:
                raise LimitExceeded
            hidden_columns = set()
            for column in sheet.column_dimensions.values():
                if column.hidden:
                    hidden = True
                    hidden_columns.update(range(column.min, min(column.max, 256) + 1))
            lines = []
            for row in sheet.iter_rows():
                if sheet.row_dimensions[row[0].row].hidden:
                    hidden = True
                    continue
                fields = []
                for cell in row:
                    if cell.column in hidden_columns:
                        continue
                    value = values[sheet.title][cell.coordinate].value
                    if cell.data_type == "f":
                        formula_count += 1
                        if value is None:
                            missing += 1
                            rendered = "[формула без збереженого результату]"
                        else:
                            rendered = f"[збережений результат формули: {value}]"
                    elif value is None:
                        continue
                    elif isinstance(value, (datetime, date, time)):
                        rendered = value.isoformat()
                    else:
                        rendered = str(value)
                    fields.append(f"{cell.coordinate}: {clean(rendered)}")
                if fields:
                    if not lines:
                        append(f"[Аркуш: {clean(sheet.title)}]")
                    append(" | ".join(fields))
                    lines.append(True)
        message = "Текст XLSX додано до чернетки. Перевірте числа, одиниці виміру й форматування: значення клітинок можуть відрізнятися від їхнього вигляду в Excel. Зображення та діаграми не читаються."
        if formula_count:
            message += " Формули не виконувалися; збережені результати можуть бути застарілими."
        if missing:
            message += f" Без збереженого результату: {missing}. Перерахуйте й збережіть книгу в Excel перед повторним завантаженням."
        if hidden:
            message += " Приховані аркуші, рядки та стовпці пропущено."
        text = bounded(parts)
        if not text:
            message = "XLSX не містить доступного тексту у видимих клітинках. Приховані дані та зображення не витягуються."
        return {"status": ("Partial" if missing or hidden else "Extracted") if text else "No Text",
                "text": text, "message": message}
    finally:
        for book in books:
            book.close()


def extract(raw, suffix):
    try:
        if not raw or len(raw) > MAX_FILE:
            raise LimitExceeded
        if suffix == ".pdf":
            return pdf(raw)
        if suffix == ".docx":
            return docx(raw)
        if suffix == ".xlsx":
            return xlsx(raw)
        if suffix == ".txt":
            return txt(raw)
        raise ValueError("unsupported format")
    except (LimitExceeded, MemoryError, RecursionError):
        return {"status": "Limit Exceeded", "text": "", "message": "Перевищено ліміт обробки: 100 000 символів; PDF — 300 сторінок; DOCX/XLSX — 25 МБ розпакованих даних; XLSX — 50 аркушів, 50 000 клітинок, 10 000 рядків, 256 стовпців і 250 000 позицій сітки. Розділіть документ."}
    except Exception:
        # Parser messages can contain document text/paths; never return or log them.
        return {"status": "Failed", "text": "", "message": "Не вдалося безпечно прочитати документ. Збережіть нову копію PDF/DOCX/XLSX або TXT у UTF-8."}


def main():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    logging.disable(logging.CRITICAL)
    raw = sys.stdin.buffer.read(MAX_FILE + 1)
    result = extract(raw, sys.argv[1])
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
