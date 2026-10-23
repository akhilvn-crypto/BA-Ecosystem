"""Change detection: damaged inputs, tampered snapshots, reverted/changed-again BRDs, reopened workflows and a real
subprocess run of the CLI."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests.test_change_detection.test_change_detection import PO, ChangeTest, code_of

from ba_lib import changes, checks, reqs, stories, storytrack, util  # noqa: E402


class EdgeCaseChangeTests(ChangeTest):
    def test_markdown_with_a_destroyed_layout_is_kept_and_flagged_not_trusted(self):
        p = self.two_approved()
        md = p.root / "stories" / "approved" / "md" / "US-001.md"
        md.write_text("I rewrote this whole file as a note to self.\n", encoding="utf-8")
        rec = changes.check(p)["changes"][0]
        self.assertIn("markdown_structure", rec["changes"][0]["fields"])
        out = changes.apply(p, rec["change_id"], *PO, "apply")
        self.assertIsNone(out["effects"]["new_candidate_version"])
        self.assertIn("layout", out["effects"]["candidate_error"])
        s = self.approved_json(p)
        self.assertEqual(s["version"], 1)
        self.assertTrue(s["impact_review"]["pending"], "the story cannot silently stay approved after an unreadable edit")
        kept_md = [k for k in out["effects"]["edit_preserved_as"] if k.endswith(".md")]
        self.assertEqual((p.root / kept_md[0]).read_text(encoding="utf-8"), "I rewrote this whole file as a note to self.\n")
        self.assertIn("US-001", checks.status_report(p)["stories"]["flagged_for_impact_review"])

    def test_corrupt_story_json_is_preserved_and_the_approval_restored(self):
        p = self.two_approved()
        (p.root / "stories" / "approved" / "json" / "US-001.json").write_text("{ this is not json", encoding="utf-8")
        rec = changes.check(p)["changes"][0]
        self.assertEqual(rec["subject"]["change_type"], "UNREADABLE")
        out = changes.apply(p, rec["change_id"], *PO, "apply")
        self.assertEqual(self.approved_json(p)["approval"]["statement"], "I approve US-001 v1.")
        kept_json = [k for k in out["effects"]["edit_preserved_as"] if k.endswith(".json")]
        self.assertEqual((p.root / kept_json[0]).read_text(encoding="utf-8"), "{ this is not json")

    def test_damaged_approved_snapshot_is_reported_and_never_trusted(self):
        p = self.two_approved()
        snap = p.root / storytrack.current(storytrack.entry(p, "US-001"))["snapshot"]
        snap.write_text(snap.read_text(encoding="utf-8").replace("Register as a member", "Tampered"), encoding="utf-8")
        w = checks.validate_project(p)["warnings"]
        self.assertTrue(any("US-001 v1" in x and "snapshot" in x for x in w), w)
        s = self.approved_json(p)
        s["title"] = "Edited too"
        self.write_approved_json(p, s)
        rec = changes.check(p)["changes"][0]
        out = changes.apply(p, rec["change_id"], *PO, "apply")
        self.assertIn("warning", out["effects"])
        self.assertEqual(self.approved_json(p)["title"], "Edited too", "nothing is overwritten from an untrustworthy snapshot")
        self.assertTrue(self.approved_json(p)["impact_review"]["pending"])
        self.assertTrue((p.root / "stories" / "candidates" / "json" / "US-001.json").exists(), "the edit is still captured for review")

    def test_hand_edit_that_links_an_unknown_requirement_is_captured_not_crashed(self):
        p = self.two_approved()
        s = self.approved_json(p)
        s["requirement_ids"] = ["REQ-001", "REQ-999"]
        self.write_approved_json(p, s)
        rec = changes.check(p)["changes"][0]
        self.assertEqual(rec["impact"]["requirements"]["unknown_to_project"], ["REQ-999"])
        changes.apply(p, rec["change_id"], *PO, "apply")
        cand = json.loads((p.root / "stories" / "candidates" / "json" / "US-001.json").read_text(encoding="utf-8"))
        self.assertEqual(cand["requirement_ids"], ["REQ-001", "REQ-999"])
        self.assertEqual(code_of(stories.approve, p, "US-001", 2, "approve", *PO), "UNLINKED_REQUIREMENT",
                         "the normal rules still gate approval of the captured edit")

    def test_story_edit_reopens_a_completed_workflow(self):
        p = self.two_approved()
        p.state["stage"] = "COMPLETED"
        p.save_state("test-completed")
        s = self.approved_json(p)
        s["title"] = "Register as a library member"
        self.write_approved_json(p, s)
        changes.apply(p, changes.check(p)["changes"][0]["change_id"], *PO, "apply")
        self.assertEqual(p.state["stage"], "WAITING_FOR_STORY_REVIEW")
        self.assertIn("WORKFLOW_REOPENED", [e["event_type"] for e in p.audit_events()])

    def test_brd_reverted_before_accepting_is_withdrawn_not_forced_through(self):
        p = self.with_requirements()
        original = self.brd_path().read_bytes()
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        changes.apply(p, changes.check(p)["changes"][0]["change_id"], *PO, "apply")
        self.brd_path().write_bytes(original)
        reqs.confirm_requirement(p, "REQ-003", "BRD put back; unaffected.")
        reqs.confirm_requirement(p, "REQ-004", "BRD put back; unaffected.")
        fp = p.state["source"]["fingerprint"]
        out = reqs.source_accept(p, *PO, "The BRD was reverted.")
        self.assertTrue(out["withdrawn"])
        self.assertEqual(p.state["source"]["fingerprint"], fp)
        self.assertFalse(p.state["impact_review"]["pending"])
        self.assertEqual(len(self.events(p, "SOURCE_CHANGE_WITHDRAWN")), 1)

    def test_accepting_after_a_further_edit_is_refused(self):
        p = self.with_requirements()
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        changes.apply(p, changes.check(p)["changes"][0]["change_id"], *PO, "apply")
        reqs.confirm_requirement(p, "REQ-003", "ok")
        reqs.confirm_requirement(p, "REQ-004", "ok")
        self.edit_brd(lambda t: t.replace("within 21 days", "within 99 days"))
        self.assertEqual(code_of(reqs.source_accept, p, *PO, "accept"), "SOURCE_CHANGED_AGAIN")
        self.assertTrue(p.state["impact_review"]["pending"])

    def test_only_new_sections_and_story_removal_can_be_dismissed(self):
        p = self.with_requirements()
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        cid = changes.check(p)["changes"][0]["change_id"]
        changes.apply(p, cid, *PO, "apply")
        self.assertEqual(code_of(changes.dismiss, p, cid, "requirement:REQ-004", "skip it", *PO), "NOT_DISMISSABLE")
        self.assertEqual(code_of(changes.dismiss, p, cid, "source_acceptance:source", "skip it", *PO), "NOT_DISMISSABLE")

    def test_unknown_change_and_ambiguous_apply_are_refused(self):
        p = self.to_baseline()
        self.assertEqual(code_of(changes.get, p, "CHG-999"), "UNKNOWN_CHANGE")
        self.assertEqual(code_of(changes.apply, p, None, *PO, "x"), "CHANGE_ID_REQUIRED")

    def test_bytes_only_refresh_leaves_a_valid_project(self):
        p = self.to_baseline()
        self.brd_path().write_bytes(self.brd_path().read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(changes.check(p)["source"], "formatting_only")
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])
        self.assertEqual({r["source_fingerprint"] for r in p.requirements()}, {p.state["source"]["fingerprint"]})

    def test_the_original_brd_is_never_written_to(self):
        p = self.two_approved()
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        h = util.sha256_file(self.brd_path())
        changes.apply(p, changes.check(p)["changes"][0]["change_id"], *PO, "apply")
        reqs.update_requirement(p, "REQ-004", {"statement": "Borrowed books must be returned within 21 days.",
                                               "source_excerpt": "Borrowed books must be returned within 21 days."}, "BRD", "source-change")
        reqs.confirm_requirement(p, "REQ-003", "ok")
        reqs.source_accept(p, *PO, "ok")
        self.assertEqual(util.sha256_file(self.brd_path()), h)


class DocxChangeTests(ChangeTest):
    """DOCX BRDs are compared on their extracted text, so a Word re-save is formatting and an edited sentence is not."""

    def docx(self, name, paras, bold=False, extra=None):
        import zipfile
        W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        body = []
        for style, text in paras:
            ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
            rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
            body.append(f"<w:p>{ppr}<w:r>{rpr}<w:t>{text}</w:t></w:r></w:p>")
        path = self.tmp / "inputs" / name
        path.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {W}><w:body>{"".join(body)}</w:body></w:document>')
            z.writestr("[Content_Types].xml", "<Types/>")
            if extra:
                z.writestr("docProps/core.xml", extra)  # metadata only: changes the file bytes, not the text
        return path

    PARAS = [("Heading1", "Orders"), (None, "The system shall let a customer place an order."),
             (None, "An order must be confirmed within 30 minutes."), ("Heading1", "Returns"),
             (None, "A customer may return an item within 14 days.")]

    def test_word_resave_is_formatting_and_an_edited_sentence_is_material(self):
        brd = self.docx("brd.docx", self.PARAS)
        p = self.new_project(brd=brd)
        sid = next(s["section_id"] for s in p.source_metadata()["sections"] if s["title"] == "Orders")
        reqs.add_requirements(p, [dict(title="Order confirmation", statement="An order is confirmed within 30 minutes.", type="BUSINESS_RULE",
                                       source_section_ids=[sid], source_excerpt="An order must be confirmed within 30 minutes.")])
        reqs.commit_requirements(p)
        self.assertEqual(snapshots_text(p).count("30 minutes"), 1)
        # Word re-saves the file: new bytes, same words, bold runs
        self.docx("brd.docx", self.PARAS, bold=True, extra="<cp:coreProperties>saved again</cp:coreProperties>")
        out = changes.check(p)
        self.assertEqual((out["source"], out["unreviewed_ids"]), ("formatting_only", []))
        self.assertEqual(p.requirement("REQ-001")["status"], "ACTIVE")
        # someone edits a sentence
        edited = [(s, t.replace("30 minutes", "45 minutes")) for s, t in self.PARAS]
        self.docx("brd.docx", edited)
        rec = changes.check(p)["changes"][0]
        self.assertEqual(rec["classification"], "MATERIAL")
        self.assertIn("30 minutes", " ".join(rec["changes"][0]["hunks"][0]["removed"]))
        self.assertIn("45 minutes", " ".join(rec["changes"][0]["hunks"][0]["added"]))
        self.assertEqual([a["requirement_id"] for a in rec["impact"]["affected_requirements"]], ["REQ-001"])
        self.assertEqual(rec["impact"]["affected_requirements"][0]["impact"], "QUOTED_TEXT_REMOVED_OR_REWORDED")


def snapshots_text(p) -> str:
    from ba_lib import snapshots
    return snapshots.text_of(p, snapshots.latest(p)) or ""


class RealCliTests(ChangeTest):
    """The same flow through real subprocesses, as the plugin's commands and skills drive it."""

    def run_ba(self, p, *argv, expect=0):
        ba = Path(__file__).resolve().parents[2] / "scripts" / "ba.py"
        r = subprocess.run([sys.executable, str(ba), "--root", str(p.root), *argv], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, expect, r.stdout + r.stderr)
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            return {"raw": r.stdout}

    def test_detect_explain_apply_resume_through_subprocesses(self):
        p = self.two_approved()
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        out = self.run_ba(p, "changes", "check")
        self.assertTrue(out["ok"] and out["requires_review"])
        self.assertEqual(out["changes"][0]["impact"]["affected_requirements"][1]["requirement_id"], "REQ-004")
        blocked = self.run_ba(p, "baseline", "create", expect=1)
        self.assertEqual(blocked["error"]["code"], "CHANGES_UNREVIEWED")
        bad = self.run_ba(p, "changes", "apply", "--id", "CHG-001", "--by-name", "AI", "--by-role", "model", "--statement", "ok", expect=1)
        self.assertEqual(bad["error"]["code"], "IDENTITY_REQUIRED")
        ok = self.run_ba(p, "changes", "apply", "--id", "CHG-001", "--by-name", "Priya Nair", "--by-role", "Product Owner",
                         "--statement", "Apply it.")
        self.assertEqual(ok["status"], "APPLIED")
        st = self.run_ba(p, "status", "--json")
        self.assertEqual(st["changes"]["awaiting_reconciliation"][0]["change_id"], "CHG-001")
        self.assertEqual(self.run_ba(p, "changes", "list")["changes"][0]["status"], "APPLIED")
        self.assertEqual(self.run_ba(p, "changes", "show", "CHG-001")["applied"]["by_name"], "Priya Nair")
        self.assertTrue(self.run_ba(p, "validate")["ok"])
        self.assertEqual(self.run_ba(p, "source", "history")["versions"][0]["version"], 1)


if __name__ == "__main__":
    unittest.main()
