"""T1: `convert` turns PDF / DOC / RTF / XLSX into Markdown with local tools so the model need not transcribe by hand."""
import contextlib
import io
import json
import shutil
import sys
import zipfile
from unittest import mock

from tests.helpers import BATest
from ba_lib import cli, convert as convmod

SHEET = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
         '<row r="1"><c r="A1" t="s"><v>ID</v></c><c r="B1" t="inlineStr"><is><t>Rule</t></is></c></row>'
         '<row r="2"><c r="A2"><v>1</v></c><c r="B2" t="inlineStr"><is><t>A member may borrow up to 5 books | max</t></is></c></row>'
         '</sheetData></worksheet>')


def make_pdf(path, lines):
    """A one-page PDF with a text layer, built by hand (no library)."""
    stream = "BT /F1 12 Tf 72 720 Td 16 TL " + " ".join(f"({l}) Tj T*" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)


class ConvertTests(BATest):
    def runTest(self):
        pass

    def xlsx(self):
        f = self.tmp / "rules.xlsx"
        with zipfile.ZipFile(f, "w") as z:
            z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                       '<sheet name="Rules" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>ID</t></si></sst>')
            z.writestr("xl/worksheets/sheet1.xml", SHEET.replace('<v>ID</v>', '<v>0</v>'))
        return f

    def test_xlsx_becomes_a_markdown_table_inside_source(self):
        out = convmod.convert(str(self.tmp / "out"), str(self.xlsx()))
        self.assertEqual(out["method"], "xlsx")
        text = (self.tmp / "out" / "source" / "rules.md").read_text(encoding="utf-8")
        self.assertIn("## Rules", text)
        self.assertIn("| ID | Rule |", text)
        self.assertIn("5 books / max", text, "a pipe inside a cell must not break the table")

    def test_pdf_with_pdftotext_gives_text_and_inferred_headings(self):
        if not shutil.which("pdftotext"):
            self.skipTest("pdftotext not installed")
        f = self.tmp / "brd.pdf"
        make_pdf(f, ["1 Borrowing", "A member shall borrow up to five books at a time", "2 Returns", "Books are returned within 14 days"])
        out = convmod.convert(str(self.tmp / "out"), str(f))
        self.assertEqual(out["method"], "pdftotext")
        self.assertTrue(out["warnings"], "layout loss is reported, not hidden")
        text = (self.tmp / "out" / "source" / "brd.md").read_text(encoding="utf-8")
        self.assertIn("# 1 Borrowing", text)
        self.assertIn("up to five books", text)
        self.assertGreaterEqual(out["headings"], 2)

    def test_no_converter_is_reported_so_the_model_can_fall_back(self):
        f = self.tmp / "x.pdf"
        f.write_bytes(b"%PDF-1.4")
        with mock.patch.object(convmod.shutil, "which", return_value=None), mock.patch.dict(sys.modules, {"pypdf": None}):
            with self.assertRaises(convmod.BAError) as cm:
                convmod.convert(str(self.tmp / "out"), str(f))
        self.assertEqual(cm.exception.code, "CONVERTER_UNAVAILABLE")
        g = self.tmp / "x.rtf"
        g.write_text("{\rtf1 hi}")
        with mock.patch.object(convmod.shutil, "which", return_value=None):
            with self.assertRaises(convmod.BAError) as cm:
                convmod.convert(str(self.tmp / "out"), str(g))
        self.assertEqual(cm.exception.code, "CONVERTER_UNAVAILABLE")

    def test_output_cannot_leave_the_source_folder_and_text_formats_are_not_converted(self):
        for bad in ("../evil.md", "state/workflow.md", "source/x.txt"):
            with self.assertRaises(convmod.BAError) as cm:
                convmod.convert(str(self.tmp / "out"), str(self.xlsx()), bad)
            self.assertEqual(cm.exception.code, "BAD_INPUT", bad)
        with self.assertRaises(convmod.BAError) as cm:
            convmod.convert(str(self.tmp / "out"), str(self.brd_copy()))
        self.assertEqual(cm.exception.code, "UNSUPPORTED_FORMAT")

    def test_cli_convert_then_init_works_and_quotes_are_checked_against_the_copy(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["--root", str(self.tmp / "out"), "convert", "--in", str(self.xlsx())])
        js = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertTrue(js["ok"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["--root", str(self.tmp / "out"), "init", "--brd", js["out"]])
        self.assertTrue(json.loads(buf.getvalue())["ok"], buf.getvalue())
