"""Synthetic parser fixtures and security checks; no real manuals or paid APIs."""
import io
import subprocess
import unittest
import zipfile
from unittest.mock import patch
from xml.sax.saxutils import escape

from warehouse_core.knowledge_extraction import extract


def make_docx(text="Інструкція Соняшник", *, xml=None, extra=None):
    out = io.BytesIO()
    body = xml or ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>' + escape(text) + '</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Товар</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>Кількість 7</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        '</w:body></w:document>')
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("word/document.xml", body)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return out.getvalue()


def make_pdf(text="Manual ExtractionMarker", *, blank_pages=0, encrypted=False):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    if text:
        page = writer.add_blank_page(width=400, height=300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
            DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(("BT /F1 12 Tf 20 200 Td (" + text + ") Tj ET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    for _ in range(blank_pages):
        writer.add_blank_page(width=400, height=300)
    if encrypted:
        writer.encrypt("synthetic-password")
    out = io.BytesIO(); writer.write(out)
    return out.getvalue()


def make_xlsx(*, formula=False, hidden=False, sheet_xml=None, extra=None):
    """Minimal OOXML byte fixture, including cached results that workbook writers omit."""
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    hidden_row = '<row r="3" hidden="1"><c r="A3" t="inlineStr"><is><t>HiddenRowSecret</t></is></c></row>' if hidden else ''
    columns = '<cols><col min="5" max="5" hidden="1"/></cols>' if hidden else ''
    formula_cells = '<c r="B2"><f>1+6</f><v>7</v></c><c r="C2"><f>WEBSERVICE("http://127.0.0.1:1/private")</f></c>' if formula else ''
    xml = sheet_xml or (f'<worksheet xmlns="{ns}">{columns}<sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>Кількість</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>XlsxMarker</t></is></c>'
        '<c r="D2" s="1"><v>45292</v></c>' + formula_cells +
        ('<c r="E2" t="inlineStr"><is><t>HiddenColumnSecret</t></is></c>' if hidden else '') +
        '</row>' + hidden_row + '</sheetData></worksheet>')
    parts = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>',
        '_rels/.rels': f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets><sheet name="Товари" sheetId="1" r:id="rId1"/>' +
            ('<sheet name="Прихований" sheetId="2" state="hidden" r:id="rId2"/>' if hidden else '') + '</sheets></workbook>',
        'xl/_rels/workbook.xml.rels': f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/>' +
            (f'<Relationship Id="rId2" Type="{rel}/worksheet" Target="worksheets/sheet2.xml"/>' if hidden else '') + '</Relationships>',
        'xl/worksheets/sheet1.xml': xml,
        'xl/sharedStrings.xml': f'<sst xmlns="{ns}" count="1" uniqueCount="1"><si><r><t>Назва </t></r><r><t>товару</t></r></si></sst>',
        'xl/styles.xml': f'<styleSheet xmlns="{ns}"><fonts count="1"><font/></fonts><fills count="1"><fill><patternFill/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>',
    }
    if hidden:
        parts['xl/worksheets/sheet2.xml'] = f'<worksheet xmlns="{ns}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>HiddenSheetSecret</t></is></c></row></sheetData></worksheet>'
    parts.update(extra or {})
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items(): archive.writestr(name, data)
    return out.getvalue()


class ExtractionTests(unittest.TestCase):
    def test_txt_utf8_bom_and_empty(self):
        result = extract('\ufeffІнструкція\r\nДругий рядок'.encode('utf-8'), '.txt')
        self.assertEqual(result['status'], 'Extracted')
        self.assertEqual(result['text'], 'Інструкція\nДругий рядок')
        self.assertEqual(extract(b' \r\n', '.txt')['status'], 'No Text')

    def test_txt_invalid_binary_and_limit(self):
        for raw in (b'\xff\xfeA', b'hello\x00binary'):
            self.assertEqual(extract(raw, '.txt')['status'], 'Failed')
        self.assertEqual(extract(b'x' * 100001, '.txt')['status'], 'Limit Exceeded')

    def test_xlsx_unicode_coordinates_and_dates(self):
        result = extract(make_xlsx(), '.xlsx')
        self.assertEqual(result['status'], 'Extracted', result)
        for text in ('[Аркуш: Товари]', 'A1: Назва товару', 'B1: Кількість', 'A2: XlsxMarker', 'D2: 2024-01-01'):
            self.assertIn(text, result['text'])

    def test_xlsx_formulas_are_not_executed(self):
        result = extract(make_xlsx(formula=True), '.xlsx')
        self.assertEqual(result['status'], 'Partial', result)
        self.assertIn('збережений результат формули: 7', result['text'])
        self.assertIn('формула без збереженого результату', result['text'])
        self.assertNotIn('WEBSERVICE', result['text'])

    def test_xlsx_hidden_content_is_skipped(self):
        result = extract(make_xlsx(hidden=True), '.xlsx')
        self.assertEqual(result['status'], 'Partial', result)
        self.assertIn('XlsxMarker', result['text'])
        for text in ('HiddenRowSecret', 'HiddenColumnSecret', 'HiddenSheetSecret'):
            self.assertNotIn(text, result['text'])

    def test_xlsx_xml_active_content_and_bad_zip_rejected(self):
        self.assertEqual(extract(b'PK\x03\x04broken', '.xlsx')['status'], 'Failed')
        for name, data in (('xl/vbaProject.bin', b'bad'), ('../escape', b'bad'),
                           ('xl/evil.xml', '<!DOCTYPE a [<!ENTITY secret SYSTEM "file:///etc/passwd">]><a>&secret;</a>')):
            self.assertEqual(extract(make_xlsx(extra={name: data}), '.xlsx')['status'], 'Failed')

    def test_xlsx_sparse_grid_and_expansion_limits(self):
        ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
        xml = f'<worksheet xmlns="{ns}"><sheetData><row r="10001"><c r="A10001"><v>1</v></c></row></sheetData></worksheet>'
        self.assertEqual(extract(make_xlsx(sheet_xml=xml), '.xlsx')['status'], 'Limit Exceeded')
        self.assertEqual(extract(make_xlsx(extra={'xl/media/big.bin': b'x' * (26 * 1024 * 1024)}), '.xlsx')['status'], 'Limit Exceeded')

    def test_xlsx_empty_and_non_workbook(self):
        self.assertEqual(extract(make_xlsx(sheet_xml='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>'), '.xlsx')['status'], 'No Text')
        self.assertEqual(extract(make_docx(), '.xlsx')['status'], 'Failed')

    def test_pdf_text_and_page_marker(self):
        result = extract(make_pdf(), ".pdf")
        self.assertEqual(result["status"], "Extracted", result)
        self.assertIn("ExtractionMarker", result["text"])
        self.assertIn("[Сторінка 1]", result["text"])

    def test_docx_unicode_and_tables(self):
        result = extract(make_docx(), ".docx")
        self.assertEqual(result["status"], "Extracted", result)
        self.assertIn("Інструкція Соняшник", result["text"])
        self.assertIn("Товар | Кількість 7", result["text"])

    def test_pdf_missing_text_is_explicit(self):
        self.assertEqual(extract(make_pdf(text="", blank_pages=1), ".pdf")["status"], "No Text")
        result = extract(make_pdf(blank_pages=1), ".pdf")
        self.assertEqual(result["status"], "Partial")
        self.assertIn("2", result["message"])

    def test_encrypted_pdf(self):
        result = extract(make_pdf(encrypted=True), ".pdf")
        self.assertEqual(result["status"], "Encrypted")
        self.assertEqual(result["text"], "")

    def test_page_and_text_limits(self):
        self.assertEqual(extract(make_pdf(text="", blank_pages=301), ".pdf")["status"], "Limit Exceeded")
        result = extract(make_docx("x" * 100001), ".docx")
        self.assertEqual(result["status"], "Limit Exceeded")
        self.assertEqual(result["text"], "")

    def test_malformed_documents(self):
        for raw, suffix in ((b"%PDF-not-real", ".pdf"), (b"PK\x03\x04broken", ".docx")):
            self.assertEqual(extract(raw, suffix)["status"], "Failed")

    def test_xml_entities_and_active_content_rejected(self):
        xml = '<!DOCTYPE a [<!ENTITY secret SYSTEM "file:///etc/passwd">]><a>&secret;</a>'
        self.assertEqual(extract(make_docx(xml=xml), ".docx")["status"], "Failed")
        for name in ("../escape", "word/vbaProject.bin", "word/embeddings/object.bin"):
            self.assertEqual(extract(make_docx(extra={name: "bad"}), ".docx")["status"], "Failed")

    def test_compressed_expansion_limit(self):
        self.assertEqual(extract(make_docx(extra={"word/media/big.bin": b"x" * (26 * 1024 * 1024)}), ".docx")["status"], "Limit Exceeded")

    def test_external_relationships_never_resolved(self):
        result = extract(make_docx(extra={"word/_rels/document.xml.rels":
            '<Relationships><Relationship TargetMode="External" Target="http://127.0.0.1:1/private"/></Relationships>'}), ".docx")
        self.assertEqual(result["status"], "Extracted")

    def test_timeout_and_no_inherited_secrets(self):
        with patch("warehouse_core.knowledge_extraction.subprocess.run", side_effect=subprocess.TimeoutExpired("parser", 15)) as run:
            self.assertEqual(extract(b"%PDF-synthetic", ".pdf")["status"], "Failed")
            self.assertEqual(set(run.call_args.kwargs["env"]), {"LANG", "LC_ALL"})
            self.assertEqual(run.call_args.kwargs["timeout"], 15)
            self.assertIn("-I", run.call_args.args[0])


def run():
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream).run(unittest.defaultTestLoader.loadTestsFromTestCase(ExtractionTests))
    if not result.wasSuccessful():
        raise AssertionError(stream.getvalue())
    return {"passed": result.testsRun, "paid_calls": 0}
