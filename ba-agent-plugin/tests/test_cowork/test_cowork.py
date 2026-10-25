"""Frictionless start and visibility: `find` locates the BRD, START-HERE.md shows where everything is, PDFs are handled by Claude."""
import json
import subprocess
import sys

from tests.helpers import BATest, FIXTURE, PLUGIN, gapmod
from ba_lib import discover, ingest, overview
from ba_lib.util import BAError

BA = str(PLUGIN / "scripts" / "ba.py")


def ba(root, *args):
    r = subprocess.run([sys.executable, BA, "--root", str(root), *args], capture_output=True, text=True, encoding="utf-8")
    return json.loads(r.stdout)


class FindTests(BATest):
    def test_one_brd_in_the_folder_is_proposed_without_asking_for_a_path(self):
        d = self.tmp / "attached"
        d.mkdir()
        (d / "Library-BRD.md").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        (d / "README.md").write_text("hi", encoding="utf-8")
        (d / "~$lock.docx").write_bytes(b"x")
        out = ba(d, "find")
        self.assertTrue(out["ok"], out)
        self.assertEqual([b["name"] for b in out["brd_candidates"]], ["Library-BRD.md"])
        self.assertTrue(out["brd_candidates"][0]["readable_directly"])
        self.assertNotIn("projects", out, "empty values are omitted from CLI output")

    def test_a_pdf_is_listed_and_flagged_as_not_directly_readable(self):
        d = self.tmp / "pdfs"
        d.mkdir()
        (d / "Scope document.pdf").write_bytes(b"%PDF-1.4")
        (d / "notes.txt").write_text("n", encoding="utf-8")
        got = discover.find(str(d))["brd_candidates"]
        by = {b["name"]: b for b in got}
        self.assertFalse(by["Scope document.pdf"]["readable_directly"])
        self.assertTrue(by["notes.txt"]["readable_directly"])

    def test_an_existing_project_is_found_and_not_confused_with_a_brd(self):
        p = self.new_project()
        out = discover.find(str(p.root))
        self.assertEqual(out["projects"][0]["project_id"], p.state["project_id"])
        self.assertEqual(out["brd_candidates"], [])
        parent = discover.find(str(p.root.parent))
        self.assertTrue(any(x["folder"] == str(p.root) for x in parent["projects"]))

    def test_missing_folder_is_a_clear_error(self):
        with self.assertRaises(BAError):
            discover.find(str(self.tmp / "nope"))


class PdfHintTests(BATest):
    def test_pdf_error_tells_claude_to_convert_it_instead_of_sending_the_user_away(self):
        pdf = self.tmp / "b.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        with self.assertRaises(BAError) as cm:
            ingest.extract(pdf)
        self.assertEqual(cm.exception.code, "UNSUPPORTED_FORMAT")
        self.assertIn("source/", cm.exception.message)
        self.assertIn("Do not ask the user", cm.exception.message)


class StartHereTests(BATest):
    def test_start_here_is_written_on_init_and_tracks_the_gaps_as_they_move(self):
        brd = self.brd_copy()
        out = self.tmp / "cli-out"
        r = subprocess.run([sys.executable, BA, "--root", str(out), "init", "--brd", str(brd)], capture_output=True, text=True)
        self.assertTrue(json.loads(r.stdout)["ok"], r.stdout)
        text = (out / "START-HERE.md").read_text(encoding="utf-8")
        self.assertIn("Atomic requirements", text)
        self.assertIn("not named yet", text)

    def test_counts_follow_the_files(self):
        p = self.with_gaps()
        overview.refresh(p)
        text = (p.root / "START-HERE.md").read_text(encoding="utf-8")
        self.assertIn("| Open gaps (waiting for an answer, including carried forward) | 3 |", text)
        self.assertIn("| Resolved gaps | 0 |", text)
        self.set_owner(p)
        gapmod.next_gaps(p, 10)
        self.answer_and_resolve(p, "GAP-003", "Block the borrow.", "A 6th borrow is rejected.")
        overview.refresh(p)
        text = (p.root / "START-HERE.md").read_text(encoding="utf-8")
        self.assertIn("| Open gaps (waiting for an answer, including carried forward) | 2 |", text)
        self.assertIn("| Resolved gaps | 1 |", text)
        self.assertIn("Priya Nair — Product Owner", text)

    def test_every_writing_command_refreshes_it_and_reading_does_not_rewrite_it(self):
        p = self.with_gaps()
        ba(p.root, "owner", "set", "--name", "Priya Nair", "--role", "Product Owner")
        self.assertIn("Priya Nair — Product Owner", (p.root / "START-HERE.md").read_text(encoding="utf-8"))
        before = (p.root / "START-HERE.md").stat().st_mtime_ns
        ba(p.root, "gaps", "list")
        self.assertEqual((p.root / "START-HERE.md").stat().st_mtime_ns, before)


class BaselineApproverTests(BATest):
    def _proposed(self):
        from tests.helpers import basemod
        p = self.with_gaps()
        self.resolve_all(p)
        gapmod.commit_analysis(p, True)
        basemod.create(p, "initial")
        return p, basemod

    def test_confirm_defaults_to_the_decision_owner_and_says_so_in_the_audit(self):
        p, basemod = self._proposed()
        basemod.confirm(p, 1, None, None, "Yes, confirmed.")
        ev = [e for e in p.audit_events() if e["event_type"] == "BASELINE_APPROVED"][-1]
        self.assertEqual((ev["by_name"], ev["approver_basis"]), ("Priya Nair", "DECISION_OWNER"))

    def test_a_named_approver_is_still_honoured(self):
        p, basemod = self._proposed()
        basemod.confirm(p, 1, "Lee Chan", "Delivery Lead", "Confirmed.")
        ev = [e for e in p.audit_events() if e["event_type"] == "BASELINE_APPROVED"][-1]
        self.assertEqual((ev["by_name"], ev["approver_basis"]), ("Lee Chan", "NAMED_APPROVER"))
