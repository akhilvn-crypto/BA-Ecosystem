"""Ingestion formats, hostile documents, path containment, original-BRD protection."""
import json
import os
import subprocess
import sys
import unittest
import zipfile

from tests.helpers import BATest, FIXTURE, PLUGIN, checks, gapmod, ingest, reqs, stories, util
from ba_lib.project import Project
from ba_lib.util import BAError


def code_of(fn, *a, **k):
    try:
        fn(*a, **k)
    except BAError as e:
        return e.code
    return None


def make_docx(path, paragraphs, table=None):
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = []
    for style, text in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body.append(f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>")
    if table:
        rows = "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>" for row in table)
        body.append(f"<w:tbl>{rows}</w:tbl>")
    xml = f'<?xml version="1.0"?><w:document {W}><w:body>{"".join(body)}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)
        z.writestr("[Content_Types].xml", "<Types/>")


class FormatTests(BATest):
    def test_docx_headings_lists_tables(self):
        d = self.tmp / "inputs" / "brd.docx"
        d.parent.mkdir(exist_ok=True)
        make_docx(d, [("Heading1", "Orders"), (None, "The system shall let a customer place an order."),
                      ("Heading2", "Payment"), ("ListParagraph", "Card payments are supported by the system.")],
                  table=[["Field", "Rule"], ["Quantity", "Between 1 and 10"]])
        meta = ingest.extract(d)
        self.assertEqual(meta["format"], "docx")
        self.assertEqual([s["title"] for s in meta["sections"]], ["Orders", "Payment"])
        self.assertIn("| Quantity | Between 1 and 10 |", meta["text"])
        self.assertTrue(meta["sections"][1]["has_table"])
        p = Project.create(self.tmp / "out", d)
        out = reqs.add_requirements(p, [dict(title="Place an order", statement="A customer can place an order.", type="FUNCTIONAL",
                                             source_section_ids=["S1"], source_excerpt="The system shall let a customer place an order.")])
        self.assertEqual(out["created"], ["REQ-001"])

    def test_pdf_is_reported_not_silently_dropped_and_creates_nothing(self):
        pdf = self.tmp / "inputs" / "brd.pdf"
        pdf.parent.mkdir(exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4 fake")
        with self.assertRaises(BAError) as c:
            Project.create(self.tmp / "out", pdf)
        self.assertEqual(c.exception.code, "UNSUPPORTED_FORMAT")
        self.assertIn("read the pdf yourself", c.exception.message.lower())
        self.assertFalse((self.tmp / "out").exists(), "nothing is created for an unsupported source")

    def test_corrupt_docx_and_missing_and_empty_sources(self):
        bad = self.tmp / "inputs" / "bad.docx"
        bad.parent.mkdir(exist_ok=True)
        bad.write_bytes(b"not a zip")
        self.assertEqual(code_of(ingest.extract, bad), "UNREADABLE_SOURCE")
        self.assertEqual(code_of(ingest.extract, self.tmp / "nope.md"), "SOURCE_NOT_FOUND")
        empty = self.brd_copy("empty.md", "# T\n")
        self.assertEqual(code_of(ingest.extract, empty), "EMPTY_SOURCE")

    def test_plain_text_numbered_headings_and_cp1252(self):
        t = self.tmp / "inputs" / "brd.txt"
        t.parent.mkdir(exist_ok=True)
        t.write_bytes("1. Scope\nThe system covers orders – and refunds.\n\n1.1 Orders\nCustomers place orders.\n\n2. Reports\nStaff run reports.\n".encode("cp1252"))
        meta = ingest.extract(t)
        self.assertEqual([s["section_id"] for s in meta["sections"]], ["S1", "S1.1", "S2"])
        self.assertTrue(any("Windows-1252" in w for w in meta["warnings"]))

    def test_section_fetch_returns_only_that_section(self):
        p = self.new_project()
        sid = self.sec(p, "2.2")
        out = reqs.source_section(p, sid)
        self.assertIn("at most 5 books", out["text"])
        self.assertNotIn("confirmation email", out["text"], "context efficiency: only the requested section is loaded")


class HostileDocumentTests(BATest):
    def test_embedded_instructions_are_flagged_as_data_and_change_nothing(self):
        """Test 13."""
        p = self.with_requirements()
        meta = p.source_metadata()
        self.assertGreaterEqual(len(meta["injection_suspects"]), 1)
        self.assertIn("IGNORE ALL PREVIOUS", meta["injection_suspects"][0]["excerpt"])
        self.assertIn("SOURCE_INSTRUCTION_LIKE_CONTENT", [e["event_type"] for e in p.audit_events()])
        self.assertIn("flagged as instruction-like", (p.root / "extracted/md/brd-outline.md").read_text(encoding="utf-8"))
        self.assertTrue(any("instruction-like" in w for w in checks.validate_project(p)["warnings"]))
        # the code-level rules a hostile BRD cannot bypass:
        gapmod.add_gaps(p, self.gap_items())
        self.assertEqual(code_of(gapmod.next_gaps, p, 3), "OWNER_REQUIRED")
        self.assertTrue(all(g["status"] == "OPEN" for g in p.gaps()))
        import shutil
        shutil.rmtree(self.tmp / "out")
        p2 = self.to_stories()
        self.assertEqual(code_of(stories.approve, p2, "US-001", 1, ""), "APPROVAL_EVIDENCE_REQUIRED")
        self.assertEqual(list((p2.root / "stories/approved/json").glob("*.json")), [])

    def test_hallucinated_quote_or_section_is_rejected(self):
        p = self.new_project()
        sec = self.sec(p, "2.1")
        item = dict(title="Invented", statement="The system supports biometric login for members.", type="SECURITY",
                    source_section_ids=[sec], source_excerpt="Members may log in with a fingerprint.")
        self.assertEqual(code_of(reqs.add_requirements, p, [item]), "REQUIREMENTS_REJECTED")
        self.assertEqual(p.req_ids(), [])
        self.assertEqual(code_of(reqs.add_requirements, p, [dict(item, source_section_ids=["S9.9"])]), "REQUIREMENTS_REJECTED")

    def test_duplicate_and_conflicting_requirements_flagged_not_silently_resolved(self):
        """Test 7."""
        p = self.with_requirements()
        out = reqs.add_requirements(p, [
            dict(title="Return period (alt)", statement="Borrowed books must be returned within 21 days.", type="BUSINESS_RULE",
                 source_section_ids=[self.sec(p, "2.2")], source_excerpt="Borrowed books must be returned within 14 days."),
            dict(title="Borrow limit again", statement="A member may borrow at most 5 books at the same time.", type="BUSINESS_RULE",
                 source_section_ids=[self.sec(p, "2.2")], source_excerpt="A member may borrow at most 5 books at the same time.")])
        kinds = {a["requirement_id"]: a for a in out["advisory"]}
        self.assertEqual(kinds["REQ-009"]["possible_conflict_with"], "REQ-004")
        self.assertEqual(kinds["REQ-010"]["duplicate_of"], "REQ-003")
        r9 = p.requirement("REQ-009")
        self.assertEqual(r9["status"], "ACTIVE", "flagged, never auto-resolved or dropped")
        self.assertEqual(r9["flags"][0]["kind"], "POSSIBLE_CONFLICT")
        pairs = reqs.pairs_report(p)
        self.assertTrue(any({c["a"], c["b"]} == {"REQ-004", "REQ-009"} for c in pairs["possible_conflicts"]))
        self.assertTrue(any({d["a"], d["b"]} == {"REQ-003", "REQ-010"} for d in pairs["possible_duplicates"]))


class PathSafetyTests(BATest):
    def test_safe_join_rejects_escape(self):
        root = self.tmp / "root"
        root.mkdir()
        for bad in ("../x.txt", "a/../../x", "/etc/passwd", "C:\\Windows\\x", "C:x", ""):
            self.assertEqual(code_of(util.safe_join, root, bad), "PATH_ESCAPE", bad)
        self.assertEqual(util.safe_join(root, "a/b.json"), root / "a" / "b.json")

    def test_symlink_escape_rejected(self):
        root = self.tmp / "root"
        outside = self.tmp / "outside"
        root.mkdir()
        outside.mkdir()
        try:
            os.symlink(outside, root / "link", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted in this environment")
        self.assertEqual(code_of(util.safe_join, root, "link/x.txt"), "PATH_ESCAPE")

    def test_original_brd_is_never_modified_or_writable(self):
        brd = self.brd_copy()
        before = util.sha256_file(brd)
        p = Project.create(self.tmp / "out", brd)
        reqs.add_requirements(p, self.req_items(p))
        self.assertEqual(util.sha256_file(brd), before)
        # BRD chosen in place inside the project root: still protected from any write
        import shutil
        root = self.tmp / "inplace"
        root.mkdir()
        inplace = root / "BRD.md"
        shutil.copyfile(FIXTURE, inplace)
        q = Project.create(root, inplace)
        self.assertEqual(q.state["source"]["relative_path"], "BRD.md")
        self.assertEqual(code_of(q.write_text, "BRD.md", "overwritten"), "SOURCE_PROTECTED")
        self.assertEqual(code_of(q.write_text, "../escape.txt", "x"), "PATH_ESCAPE")
        self.assertEqual(util.sha256_file(inplace), util.sha256_file(FIXTURE))

    def test_copy_source_option_copies_but_never_moves(self):
        brd = self.brd_copy()
        p = Project.create(self.tmp / "out", brd, copy_source=True)
        self.assertTrue(brd.exists())
        self.assertTrue((p.root / "source" / brd.name).exists())
        self.assertEqual(p.state["source"]["relative_path"], f"source/{brd.name}")
        self.assertEqual(code_of(p.write_text, f"source/{brd.name}", "x"), "SOURCE_PROTECTED")

    def test_cannot_init_twice(self):
        p = self.new_project()
        self.assertEqual(code_of(Project.create, p.root, self.brd_copy("again.md")), "ALREADY_INITIALIZED")

    def test_not_a_project(self):
        self.assertEqual(code_of(Project.open, self.tmp), "NOT_A_PROJECT")

    def test_safe_write_script_only_writes_allowed_locations(self):
        p = self.new_project()
        sw = str(PLUGIN / "scripts" / "safe_write.py")
        f = self.tmp / "payload.json"
        f.write_text("[1,2,3]", encoding="utf-8")
        ok = subprocess.run([sys.executable, sw, "--root", str(p.root), "--path", "state/inbox/x.json", "--content-file", str(f)],
                            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertEqual((p.root / "state/inbox/x.json").read_text(encoding="utf-8"), "[1,2,3]")
        for bad in ("state/workflow.json", "../evil.json", "atomic-requirements/json/REQ-001.json", "state/audit.jsonl"):
            r = subprocess.run([sys.executable, sw, "--root", str(p.root), "--path", bad, "--content-file", str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, bad)
            self.assertFalse(json.loads(r.stdout)["ok"])
        self.assertEqual(p.state["stage"], "BRD_PARSED")


if __name__ == "__main__":
    unittest.main()
