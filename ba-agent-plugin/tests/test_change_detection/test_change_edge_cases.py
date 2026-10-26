"""Edge cases of change detection: layout-destroying edits, reflowed text, bytes-only changes, dismissals, and the supplied files staying untouched."""
import hashlib
import unittest

from tests.helpers import BATest, acs, changemod, checks, docmodel, docsync, gapmod, projmod, reqindex, util, key
from tests.test_change_detection.test_change_detection import Base, K1, fn


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


class EdgeCaseTests(Base):
    def test_enriched_copy_with_a_destroyed_layout_is_kept_and_restored_not_trusted(self):
        p = self.approved3()
        f = p.enriched_root / "membership.md"
        f.write_text("everything was replaced by hand\n", encoding="utf-8", newline="")
        out = changemod.check(p, True)
        self.assertEqual(out["unreviewed_ids"], [])  # no approved criterion can be read from it: nothing material
        self.assertEqual(docmodel.strip_blocks(f.read_text(encoding="utf-8")).encode("utf-8"), (self.src / "membership.md").read_bytes())
        kept = list((p.root / "state" / "backups").glob("enriched__membership.md.observed-*"))
        self.assertEqual(len(kept), 1)
        self.assertIn("replaced by hand", kept[0].read_text(encoding="utf-8"))
        self.assertEqual(p.read_json(f"acceptance/approved/{fn('LIB-001')}.json")["version"], 1)

    def test_enriched_block_removed_by_hand_is_regenerated(self):
        p = self.approved3()
        f = p.enriched_root / "membership.md"
        t = f.read_text(encoding="utf-8")
        a, b = t.index("\n\n<!-- ba:begin key=\"membership.md::LIB-002\""), t.index("<!-- ba:end key=\"membership.md::LIB-002\" -->") + len("<!-- ba:end key=\"membership.md::LIB-002\" -->")
        f.write_text(t[:a] + t[b:], encoding="utf-8", newline="")
        out = changemod.check(p, True)
        self.assertEqual(out["unreviewed_ids"], [])
        self.assertIn('key="membership.md::LIB-002"', f.read_text(encoding="utf-8"))

    def test_hand_edit_of_the_record_with_an_unknown_requirement_is_captured_not_crashed(self):
        p = self.approved3()
        self.edit_json(p, "LIB-001", lambda d: d["open_gap_refs"].append({"gap_id": "GAP-099", "potential_impact": "Made up impact.", "readiness_effect": "NEEDS_CLARIFICATION"}))
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        res = self.apply(p, cid)
        self.assertEqual(p.read_json(f"acceptance/approved/{fn('LIB-001')}.json")["open_gap_refs"], [])  # restored
        self.assertTrue(res["effects"]["edit_preserved_as"])
        checks.status_report(p)  # must not crash

    def test_ac_edit_reopens_a_completed_workflow(self):
        p = self.to_ac_stage()
        acs.add_acs(p, [self.ac_item(r) for r in ("LIB-001", "LIB-002", "LIB-003", "LIB-004", "LIB-005", "LIB-006", "LIB-007", "LIB-008", "LIB-009")])
        for r in ("LIB-001", "LIB-002", "LIB-003", "LIB-004", "LIB-005", "LIB-006", "LIB-007", "LIB-008", "LIB-009"):
            self.approve(p, r)
        checks.complete_workflow(p)
        self.assertEqual(p.state["stage"], "COMPLETED")
        acs.edit(p, K1, {"acceptance_criteria": ["Given a visitor, when they register, then something new happens here."]}, "change")
        self.assertEqual(p.state["stage"], "WAITING_FOR_AC_REVIEW")

    def test_only_new_requirements_and_ac_removals_can_be_dismissed(self):
        p = self.approved3()
        f = self.src / "reporting.md"
        f.write_bytes(f.read_bytes() + b"| LIB-010 | Staff can print the report. | Could |\n")
        res = self.apply(p, changemod.check(p, True)["unreviewed_ids"][0])
        self.assertEqual([i["kind"] for i in res["open_items"]], ["new_requirement"])
        cid = res["change_id"]
        self.assertEqual(self.err(changemod.dismiss, p, cid, "new_requirement:nope", "r", "Priya Nair", "Product Owner").code, "UNKNOWN_ITEM")
        self.assertEqual(self.err(changemod.dismiss, p, cid, "new_requirement:reporting.md::LIB-010", "r", "", "").code, "IDENTITY_REQUIRED")
        changemod.dismiss(p, cid, "new_requirement:reporting.md::LIB-010", "Out of scope.", "Priya Nair", "Product Owner")
        self.assertEqual(self.err(changemod.dismiss, p, cid, "new_requirement:reporting.md::LIB-010", "again", "Priya Nair", "Product Owner").code, "NOT_APPLICABLE")
        # a requirement/gap/ac item is never dismissable
        self.edit_src("membership.md", "at most 5 books", "at most 4 books")
        res = self.apply(p, changemod.check(p, True)["unreviewed_ids"][0])
        kinds = {i["kind"]: i["item_id"] for i in res["open_items"]}
        self.assertEqual(self.err(changemod.dismiss, p, res["change_id"], kinds["requirement"], "r", "Priya Nair", "Product Owner").code, "NOT_DISMISSABLE")
        self.assertEqual(self.err(changemod.dismiss, p, res["change_id"], kinds["ac"], "r", "Priya Nair", "Product Owner").code, "NOT_DISMISSABLE")

    def edit_src(self, name, old, new):
        f = self.src / name
        f.write_bytes(f.read_bytes().replace(old.encode("utf-8"), new.encode("utf-8")))

    def test_bytes_only_change_is_formatting_and_leaves_a_valid_project(self):
        p = self.approved3()
        f = self.src / "membership.md"
        raw = f.read_bytes()
        f.write_bytes(b"\xef\xbb\xbf" + raw.replace(b"\n", b"\r\n"))  # BOM + CRLF: the file hash changes, the text does not
        out = changemod.check(p, True)
        self.assertEqual((out["source"], out["unreviewed_ids"]), ("formatting_only", []))
        self.assertEqual(checks.validate_project(p)["errors"], [])
        t = (p.enriched_root / "membership.md").read_bytes().decode("utf-8")  # bytes: read_text would translate CRLF
        self.assertEqual(docmodel.strip_blocks(t).encode("utf-8"), f.read_bytes())
        self.assertTrue(t.startswith("\ufeff<!-- ba:generated"))
        self.assertIn("\r\n<!-- ba:begin", t)  # blocks use the file's own line endings
        self.assertEqual(changemod.check(p, True)["source"], "unchanged")

    def test_reflowed_text_is_formatting_and_an_edited_sentence_is_material(self):
        p = self.approved3()
        f = self.src / "membership.md"
        raw = f.read_bytes()
        f.write_bytes(raw.replace(b"A member may borrow at most 5 books at the same time.", b"A member may borrow\nat most 5   books at the\nsame time."))
        self.assertEqual(changemod.check(p, True)["source"], "formatting_only")
        f.write_bytes(f.read_bytes().replace(b"at most 5   books", b"at most 6   books"))
        out = changemod.check(p, True)
        self.assertEqual(out["source"], "changed")
        self.assertEqual(len(out["unreviewed_ids"]), 1)

    def test_a_changed_number_is_material_even_inside_formatting(self):
        p = self.approved3()
        f = self.src / "membership.md"
        f.write_bytes(f.read_bytes().replace(b"within 14 days", b"within **15** days"))
        self.assertEqual(changemod.check(p, True)["source"], "changed")

    def test_heading_rename_is_a_material_change_of_the_title_only_when_the_text_differs(self):
        p = self.approved3()
        self.edit_src("membership.md", "### LIB-001 Member registration", "### LIB-001 Member sign-up")
        out = changemod.check(p, True)
        self.assertEqual(out["source"], "changed")  # the heading text is part of the requirement

    def test_the_supplied_files_are_never_written_to(self):
        p = self.approved3()
        before = {f.name: sha(f) for f in self.src.glob("*.md")}
        self.edit_block(p, "then the expected outcome is produced.", "then A happens.")
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        self.apply(p, cid)
        changemod.revert_edit(p, K1, "undo", "Priya Nair", "Product Owner")
        gapmod.reopen(p, "GAP-001", "again", "Priya Nair", "Product Owner")
        checks.recover(p)
        checks.complete_workflow(p)
        docsync.sync(p, force=True)
        self.assertEqual(before, {f.name: sha(f) for f in self.src.glob("*.md")})
        e = self.err(p.write_text, "../inputs/reqs/membership.md", "x")
        self.assertIn(e.code, ("PATH_ESCAPE", "SOURCE_PROTECTED"))

    def test_an_enriched_copy_fed_back_as_the_source_is_noticed_by_the_check(self):
        p = self.approved3()
        (self.src / "membership.md").write_bytes((p.enriched_root / "membership.md").read_bytes())
        out = changemod.check(p, True)
        self.assertEqual(out["source"], "invalid")
        self.assertEqual(out["unreviewed_ids"], [])
        self.assertTrue(any("enriched copies" in w for w in out["warnings"]))

    def test_requirement_dropped_and_re_added_resumes_its_numbering(self):
        p = self.approved3()
        # an AC that is archived leaves its versions behind: a re-added requirement continues the numbering
        t = (self.src / "membership.md").read_text(encoding="utf-8")
        original = t.encode("utf-8")
        s, e = t.index("### LIB-003"), t.index("### LIB-004")
        (self.src / "membership.md").write_bytes((t[:s] + t[e:]).encode("utf-8"))
        self.apply(p, changemod.check(p, True)["unreviewed_ids"][0])
        acs.archive(p, key("LIB-003"), "dropped", "Priya Nair", "Product Owner")
        (self.src / "membership.md").write_bytes(original)
        self.apply(p, changemod.check(p, True)["unreviewed_ids"][0])
        gapmod.commit_analysis(p, True)
        r = acs.add_acs(p, [self.ac_item("LIB-003")])
        self.assertEqual(r["created"], [key("LIB-003")])
        cand = p.read_json(f"acceptance/candidates/{fn('LIB-003')}.json")
        self.assertEqual(cand["version"], 2)  # v1 is in the archive and in the approval history


if __name__ == "__main__":
    unittest.main()
