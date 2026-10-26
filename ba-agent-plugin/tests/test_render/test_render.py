"""docsync: the enriched copy is the source bytes + a header + BA blocks, regenerated from state, never overwriting a hand edit."""
import hashlib
import unittest
from pathlib import Path

from tests.helpers import (BATest, acs, changemod, checks, docmodel, docsync, gapmod, key, projmod, reqindex, util, Project)


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class RenderTests(BATest):
    def source_hashes(self):
        return {f.name: sha(f) for f in self.src.glob("*.md")}

    def assert_invariant(self, p):
        for f in self.src.glob("*.md"):
            if (p.enriched_root / f.name).exists():
                out = (p.enriched_root / f.name).read_text(encoding="utf-8")
                self.assertEqual(docmodel.strip_blocks(out).encode("utf-8"), f.read_bytes(), f.name)

    def test_fresh_project_has_a_plain_copy_with_a_header(self):
        p = self.new_project()
        for name in ("membership.md", "lending.md", "reporting.md"):
            out = (p.enriched_root / name).read_bytes()
            self.assertTrue(out.startswith(b"<!-- ba:generated"))
            self.assertEqual(docmodel.strip_blocks(out.decode("utf-8")).encode("utf-8"), (self.src / name).read_bytes())
        self.assertFalse((p.enriched_root / "README.md").exists())  # skipped as not a requirements file
        self.assertTrue(docsync.check(p)["in_sync"])

    def test_blocks_follow_gaps_and_approved_ac(self):
        p = self.new_project()
        before = self.source_hashes()
        gapmod.add_gaps(p, self.gap_items())
        t = self.enriched(p, "lending.md")
        self.assertIn(f'<!-- ba:begin key="{key("LIB-006")}"', t)
        self.assertIn("*Acceptance criteria withheld until blocking gaps are resolved.*", t)
        self.assertIn("| GAP-001 | HIGH | OPEN |", t)
        self.assertIn("**Readiness:** NOT_READY", t)
        self.assert_invariant(p)
        self.resolve_all(p)
        gapmod.commit_analysis(p, True)
        t = self.enriched(p, "lending.md")
        self.assertNotIn("Open Gaps", t)  # resolved gaps leave the document
        acs.add_acs(p, [self.ac_item("LIB-006")])
        self.assertNotIn("ba:begin", self.enriched(p, "lending.md"))  # a candidate is never rendered
        self.approve(p, "LIB-006")
        t = self.enriched(p, "lending.md")
        self.assertIn('ac="v1"', t)
        self.assertIn("*Approved by Priya Nair (Product Owner)", t)
        self.assertIn("*Decisions applied: DEC-001*", t)
        self.assertIn("1. Given a valid case for LIB-006", t)
        self.assertIn("**Readiness:** READY_FOR_DEVELOPMENT", t)
        self.assert_invariant(p)
        self.assertEqual(before, self.source_hashes(), "the supplied files must never change")

    def test_table_rows_get_blocks_after_the_table(self):
        p = self.to_ac_stage()
        acs.add_acs(p, [self.ac_item("LIB-008"), self.ac_item("LIB-009")])
        self.approve(p, "LIB-008")
        self.approve(p, "LIB-009")
        t = self.enriched(p, "reporting.md")
        self.assertLess(t.index("| LIB-009 |"), t.index("ba:begin"))
        self.assertEqual(t.count("ba:begin"), 2)
        self.assert_invariant(p)

    def test_idempotent(self):
        p = self.to_acs(2)
        self.approve(p, "LIB-001")
        first = {f.name: f.read_bytes() for f in p.enriched_root.glob("*.md")}
        r = docsync.refresh(p)
        self.assertEqual(r["written"], [])
        self.assertEqual(first, {f.name: f.read_bytes() for f in p.enriched_root.glob("*.md")})
        self.assertEqual(docsync.sync(p)["written"], [])

    def test_many_states_keep_the_invariant(self):
        p = self.to_ac_stage()
        acs.add_acs(p, [self.ac_item(r) for r in ("LIB-001", "LIB-002", "LIB-003", "LIB-005", "LIB-006", "LIB-007", "LIB-008")])
        self.assert_invariant(p)
        for r in ("LIB-001", "LIB-003", "LIB-006", "LIB-008"):
            self.approve(p, r)
            self.assert_invariant(p)
        acs.edit(p, key("LIB-001"), {"acceptance_criteria": ["Given a changed case, when it runs, then something else happens."]}, "reword")
        self.assert_invariant(p)
        self.assertIn("Given a valid case for LIB-001", self.enriched(p))  # old version stays in force
        gapmod.reopen(p, "GAP-003", "needs rework", "Priya Nair", "Product Owner")
        t = self.enriched(p)
        self.assertIn("Blocking gap(s) open", t)
        self.assertIn("provisional", t)
        self.assert_invariant(p)

    def test_source_files_are_never_written(self):
        p = self.new_project()
        before = self.source_hashes()
        gapmod.add_gaps(p, self.gap_items())
        self.resolve_all(p)
        gapmod.commit_analysis(p, True)
        acs.add_acs(p, [self.ac_item("LIB-001")])
        self.approve(p, "LIB-001")
        docsync.sync(p, force=True)
        self.assertEqual(before, self.source_hashes())

    def test_guard_refuses_writing_into_the_source(self):
        p = self.new_project()
        with self.assertRaises(util.BAError) as cm:
            p.write_text("../inputs/reqs/x.md", "x")
        self.assertIn(cm.exception.code, ("PATH_ESCAPE", "SOURCE_PROTECTED"))
        with self.assertRaises(util.BAError) as cm:
            p._guard_source(self.src / "lending.md")
        self.assertEqual(cm.exception.code, "SOURCE_PROTECTED")

    # ------------------------------------------------------------ tampering
    def test_hand_edit_is_not_overwritten_and_is_reported_as_drift(self):
        p = self.new_project()
        f = p.enriched_root / "membership.md"
        edited = f.read_text(encoding="utf-8") + "\nA stray note.\n"
        f.write_text(edited, encoding="utf-8", newline="")
        gapmod.add_gaps(p, self.gap_items())  # any state change triggers a sync
        self.assertEqual(f.read_text(encoding="utf-8"), edited)
        self.assertEqual(docsync.refresh(p)["drift"], ["membership.md"])
        row = [r for r in docsync.check(p)["files"] if r["file"] == "membership.md"][0]
        self.assertEqual(row["state"], "DRIFT")
        with self.assertRaises(util.BAError) as cm:
            docsync.sync(p)
        self.assertEqual(cm.exception.code, "ENRICHED_DRIFT")
        v = checks.validate_project(p)
        self.assertTrue(any("changed by hand" in w for w in v["warnings"]))
        # `changes check` records the edit, keeps it and restores the generated content
        out = changemod.check(p, True)
        self.assertEqual(out["unreviewed_ids"], [])
        self.assertEqual(len(out["formatting_only_accepted"]), 1)
        self.assertNotIn("A stray note", f.read_text(encoding="utf-8"))
        kept = list((p.root / "state" / "backups").glob("enriched__membership.md.observed-*"))
        self.assertEqual(len(kept), 1)
        self.assertIn("A stray note", kept[0].read_text(encoding="utf-8"))
        self.assertTrue(docsync.check(p)["in_sync"])

    def test_force_overwrites_a_drifted_copy(self):
        p = self.new_project()
        f = p.enriched_root / "membership.md"
        f.write_text("garbage", encoding="utf-8")
        docsync.sync(p, force=True)
        self.assertTrue(docsync.check(p)["in_sync"])

    def test_damaged_markers_are_restored_not_trusted(self):
        p = self.to_acs(1)
        self.approve(p, "LIB-001")
        f = p.enriched_root / "membership.md"
        good = f.read_text(encoding="utf-8")
        for broken in (good.replace("<!-- ba:end", "<!-- ba:endx"),                      # unbalanced
                       good.replace("<!-- ba:begin", "<!-- ba:begin key=\"nested\" -->\n<!-- ba:begin", 1),  # nested begin
                       good + good[good.index("<!-- ba:begin"):good.index("<!-- ba:end")] + '<!-- ba:end key="x" -->\n'):  # duplicated block
            f.write_text(broken, encoding="utf-8", newline="")
            out = changemod.check(p, True)
            self.assertEqual(out["unreviewed_ids"], [], broken[:80])
            self.assertEqual(f.read_text(encoding="utf-8"), good)
            self.assertTrue(docsync.check(p)["in_sync"])

    def test_deleted_copy_is_regenerated(self):
        p = self.new_project()
        f = p.enriched_root / "lending.md"
        f.unlink()
        self.assertIn("MISSING", {r["state"] for r in docsync.check(p)["files"]})
        out = changemod.check(p, True)
        self.assertTrue(f.exists())
        self.assertEqual(len(out["formatting_only_accepted"]), 1)

    def test_text_outside_blocks_is_restored(self):
        p = self.new_project()
        f = p.enriched_root / "lending.md"
        f.write_text(f.read_text(encoding="utf-8").replace("Late return fine", "Late return penalty"), encoding="utf-8", newline="")
        out = changemod.check(p, True)
        self.assertEqual(out["unreviewed_ids"], [])
        self.assertIn("Late return fine", f.read_text(encoding="utf-8"))
        rec = changemod.records(p)[-1]
        self.assertEqual(rec["subject"]["change_type"], "ENRICHED_TEXT_EDITED")
        self.assertEqual(rec["status"], "RECORDED")

    # --------------------------------------------------------------- crashes
    def test_failed_write_keeps_the_previous_copy_and_does_not_fail_the_command(self):
        p = self.to_acs(1)
        f = p.enriched_root / "membership.md"
        before = f.read_bytes()
        real = projmod.atomic_write_bytes

        def boom(path, data):
            if "enriched" in str(path):
                raise OSError("disk full")
            return real(path, data)
        projmod.atomic_write_bytes = boom
        self.addCleanup(setattr, projmod, "atomic_write_bytes", real)
        self.approve(p, "LIB-001")  # the approval itself succeeds
        self.assertEqual(f.read_bytes(), before)
        self.assertTrue(p.doc_warnings and "WRITE_FAILED" in p.doc_warnings[0])
        self.assertTrue(p.exists("acceptance/approved/" + projmod.ac_fname(key("LIB-001")) + ".json"))
        projmod.atomic_write_bytes = real
        r = docsync.refresh(p)  # recover: the next render catches up
        self.assertEqual(r["written"], ["membership.md"])
        self.assertIn("AC v1", f.read_text(encoding="utf-8"))
        self.assertTrue(docsync.check(p)["in_sync"])

    def test_leftover_temp_file_does_not_matter(self):
        p = self.new_project()
        (p.enriched_root / ".membership.md.crash.tmp").write_bytes(b"half")
        self.assertTrue(docsync.check(p)["in_sync"])

    def test_render_invariant_failure_keeps_old_copy(self):
        p = self.new_project()
        old = (p.enriched_root / "lending.md").read_bytes()
        real = docmodel.strip_blocks
        docmodel.strip_blocks = lambda t: t  # a broken strip: invariant cannot hold
        self.addCleanup(setattr, docmodel, "strip_blocks", real)
        gapmod.add_gaps(p, self.gap_items())
        self.assertEqual((p.enriched_root / "lending.md").read_bytes(), old)
        self.assertTrue(any("RENDER_INVARIANT_FAILED" in w for w in p.doc_warnings) or docsync.refresh(p)["errors"])

    # ------------------------------------------------------------ creation
    def test_output_inside_source_is_refused(self):
        src = self.reqs_copy()
        with self.assertRaises(util.BAError) as cm:
            Project.create(src / "ba-output", src)
        self.assertEqual(cm.exception.code, "OUTPUT_INSIDE_SOURCE")
        self.assertFalse((src / "ba-output").exists())

    def test_enriched_dir_inside_source_is_refused(self):
        src = self.reqs_copy()
        with self.assertRaises(util.BAError) as cm:
            Project.create(self.tmp / "out", src, enriched_dir=str(src / "enriched"))
        self.assertEqual(cm.exception.code, "OUTPUT_INSIDE_SOURCE")

    def test_enriched_dir_elsewhere(self):
        shared = self.tmp / "shared-docs"
        p = self.new_project(enriched_dir=str(shared))
        self.assertTrue((shared / "membership.md").exists())
        self.assertFalse((p.root / "enriched" / "membership.md").exists())
        gapmod.add_gaps(p, self.gap_items())
        self.assertIn("ba:begin", (shared / "lending.md").read_text(encoding="utf-8"))
        reopened = Project.open(p.root)
        self.assertEqual(reopened.enriched_root, shared.resolve())
        self.assertTrue(docsync.check(reopened)["in_sync"])

    def test_enriched_file_as_source_is_refused(self):
        p = self.to_acs(1)
        self.approve(p, "LIB-001")
        fed_back = self.tmp / "fed-back"
        fed_back.mkdir()
        (fed_back / "membership.md").write_bytes((p.enriched_root / "membership.md").read_bytes())
        with self.assertRaises(util.BAError) as cm:
            Project.create(self.tmp / "out2", fed_back)
        self.assertEqual(cm.exception.code, "ENRICHED_FILE_AS_SOURCE")
        self.assertIn("membership.md", cm.exception.details["files"])

    def test_header_only_file_when_nothing_to_show_and_removed_source_removes_copy(self):
        p = self.new_project()
        (self.src / "reporting.md").unlink()
        out = changemod.check(p, True)
        cid = out["unreviewed_ids"][0]
        changemod.apply(p, cid, "Priya Nair", "Product Owner", "Apply it.")
        self.assertFalse((p.enriched_root / "reporting.md").exists())
        self.assertIn("enriched/reporting.md", p.vacated)


if __name__ == "__main__":
    unittest.main()
