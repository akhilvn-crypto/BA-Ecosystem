"""The supplied files change upstream: detection with impact, formatting-only acceptance, apply, reassessment, enriched re-render."""
import unittest

from tests.helpers import (BATest, acs, changemod, checks, docmodel, docsync, gapmod, projmod, reqindex, snapshots, util, key)


class SourceChangeTests(BATest):
    def flow(self):
        """LIB-001/003/006 approved; LIB-007 approved with the carried-forward GAP-002 open."""
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 10)
        self.answer_and_resolve(p, "GAP-001", "R10 per day.", "Fine is R10 per day late, maximum R200.")
        self.answer_and_resolve(p, "GAP-003", "Block it.", "A 6th concurrent borrow is rejected.")
        gapmod.carry_forward(p, "GAP-002", "Priya Nair", "Product Owner", "later")
        gapmod.commit_analysis(p, True)
        ref = dict(gap_id="GAP-002", potential_impact="The page-load target is unknown.", readiness_effect="NEEDS_CLARIFICATION")
        acs.add_acs(p, [self.ac_item("LIB-001"), self.ac_item("LIB-003"), self.ac_item("LIB-006"), self.ac_item("LIB-007", open_gap_refs=[ref])])
        for r in ("LIB-001", "LIB-003", "LIB-006", "LIB-007"):
            self.approve(p, r)
        return p

    def edit_src(self, name, old, new):
        f = self.src / name
        t = f.read_text(encoding="utf-8")
        self.assertIn(old, t)
        f.write_bytes(t.replace(old, new).encode("utf-8"))

    def apply(self, p, cid=None):
        return changemod.apply(p, cid, "Priya Nair", "Product Owner", "I have seen the impact; apply it.")

    # ------------------------------------------------------------ detection
    def test_unchanged_source_reports_nothing(self):
        p = self.flow()
        out = changemod.check(p, True)
        self.assertEqual(out["source"], "unchanged")
        self.assertEqual(out["changes"], [])

    def test_material_change_is_detected_explained_and_touches_nothing(self):
        p = self.flow()
        self.edit_src("membership.md", "at most 5 books", "at most 3 books")
        stage, complete = p.state["stage"], p.state["gap_analysis_complete"]
        out = changemod.check(p, True)
        self.assertEqual(out["source"], "changed")
        rec = out["changes"][0]
        self.assertEqual(rec["status"], "DETECTED")
        self.assertEqual(rec["classification"], "MATERIAL")
        mod = [c for c in rec["changes"] if c["change"] == "MODIFIED"][0]
        self.assertEqual(mod["req_key"], key("LIB-003"))
        self.assertTrue(any("at most 3 books" in " ".join(h["added"]) for h in mod["hunks"]))
        self.assertTrue(any("at most 5 books" in " ".join(h["removed"]) for h in mod["hunks"]))
        imp = rec["impact"]
        self.assertEqual([a["req_key"] for a in imp["affected_requirements"]], [key("LIB-003")])
        self.assertEqual(imp["approved_acs_at_risk"], [key("LIB-003")])
        self.assertEqual(imp["workflow"]["will_reopen_to"], "GAP_ANALYSIS")
        self.assertTrue(rec["required_actions"])
        # nothing downstream changed yet
        self.assertEqual(p.state["stage"], stage)
        self.assertEqual(p.state["gap_analysis_complete"], complete)
        self.assertFalse(any((a.get("impact_review") or {}).get("pending") for _, a in p.all_acs()))
        self.assertEqual(reqindex.text(p, key("LIB-003")).count("5 books"), 1)  # the accepted text is still the old one
        self.assertTrue(p.exists(f"changes/{rec['change_id']}.md"))
        # recording is idempotent
        again = changemod.check(p, True)
        self.assertEqual(len(changemod.records(p)), 1)
        self.assertEqual(again["unreviewed_ids"], [rec["change_id"]])

    def test_preview_does_not_record(self):
        p = self.flow()
        self.edit_src("membership.md", "at most 5 books", "at most 3 books")
        out = changemod.check(p, False)
        self.assertEqual(out["changes"][0]["status"], "PREVIEW")
        self.assertEqual(changemod.records(p), [])

    def test_guard_refuses_writing_commands_until_applied(self):
        p = self.flow()
        self.edit_src("membership.md", "at most 5 books", "at most 3 books")
        with self.assertRaises(util.BAError) as cm:
            changemod.guard(p, "ac approve")
        self.assertEqual(cm.exception.code, "CHANGES_UNREVIEWED")
        self.assertIn("CHG-001", cm.exception.message)
        self.assertIn("await the user's decision", checks.next_action(p))
        c = checks.completion(p)
        self.assertFalse(c["complete"])

    def test_formatting_only_is_accepted_and_recorded(self):
        p = self.flow()
        before_v = len(snapshots.versions(p, "membership.md"))
        self.edit_src("membership.md", "at most 5 books", "at most **5** books  ")
        self.edit_src("membership.md", "# Membership requirements\n", "# Membership requirements\n\nA new intro paragraph outside any requirement.\n")
        out = changemod.check(p, True)
        self.assertEqual(out["source"], "formatting_only")
        self.assertEqual(out["unreviewed_ids"], [])
        self.assertEqual(len(out["formatting_only_accepted"]), 1)
        rec = changemod.records(p)[0]
        self.assertEqual((rec["status"], rec["classification"]), ("RECORDED", "FORMATTING_ONLY"))
        self.assertEqual(len(snapshots.versions(p, "membership.md")), before_v + 1)
        # accepted: the approved AC is untouched and still valid; the copy follows the new bytes
        a = p.read_json("acceptance/approved/" + projmod.ac_fname(key("LIB-003")) + ".json")
        self.assertIsNone(a["impact_review"])
        self.assertEqual(checks.requirement_rows(p)[2]["ac_approved"], 1)
        enr = self.enriched(p)
        self.assertIn("A new intro paragraph", enr)
        self.assertEqual(docmodel.strip_blocks(enr).encode("utf-8"), (self.src / "membership.md").read_bytes())
        self.assertTrue(docsync.check(p)["in_sync"])
        self.assertEqual(changemod.check(p, True)["source"], "unchanged")
        self.assertEqual(checks.validate_project(p)["errors"], [])

    def test_mixed_material_and_formatting_is_material(self):
        p = self.flow()
        self.edit_src("membership.md", "at most 5 books", "at most **4** books")
        out = changemod.check(p, True)
        rec = out["changes"][0]
        self.assertEqual(rec["classification"], "MATERIAL")

    # ----------------------------------------------------------------- apply
    def test_apply_accepts_flags_reopens_and_rerenders(self):
        p = self.flow()
        self.edit_src("membership.md", "at most 5 books", "at most 3 books")
        self.edit_src("lending.md", "Pages must load quickly", "Pages must load in two seconds")
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        with self.assertRaises(util.BAError) as cm:
            changemod.apply(p, cid, "Priya Nair", "Product Owner", "")
        self.assertEqual(cm.exception.code, "BAD_INPUT")
        with self.assertRaises(util.BAError) as cm:
            changemod.apply(p, cid, "", "", "ok")
        self.assertEqual(cm.exception.code, "IDENTITY_REQUIRED")
        res = self.apply(p, cid)
        eff = res["effects"]
        self.assertEqual(sorted(eff["requirements_changed"]), sorted([key("LIB-003"), key("LIB-007")]))
        self.assertEqual(eff["gaps_flagged"], ["GAP-002"])  # the carried-forward gap on the changed requirement
        self.assertEqual(p.state["stage"], "GAP_ANALYSIS")
        self.assertFalse(p.state["gap_analysis_complete"])
        self.assertTrue(p.gap("GAP-002")["needs_impact_review"])
        flagged = {a["req_key"] for _, a in p.all_acs() if (a.get("impact_review") or {}).get("pending")}
        self.assertEqual(flagged, {key("LIB-003"), key("LIB-007")})
        # the new text is the accepted text, the copy shows it and says the criteria are under review
        self.assertIn("at most 3 books", reqindex.text(p, key("LIB-003")))
        enr = self.enriched(p)
        self.assertIn("at most 3 books", enr)
        self.assertIn("*Under review:", enr)
        self.assertEqual(docmodel.strip_blocks(enr).encode("utf-8"), (self.src / "membership.md").read_bytes())
        # items: one per changed requirement, the gap and the two criteria records
        kinds = sorted(i["kind"] for i in res["open_items"])
        self.assertEqual(kinds, ["ac", "ac", "gap", "requirement", "requirement"])
        self.assertEqual(changemod.get(p, cid)["status"], "APPLIED")
        self.assertEqual(checks.validate_project(p)["errors"], [])
        # the approved criteria cannot be approved again while the text changed
        self.assertTrue(checks.next_action(p).startswith("Finish gap analysis") or "gap" in checks.next_action(p))

    def test_apply_twice_and_stale_record(self):
        p = self.flow()
        self.edit_src("membership.md", "at most 5 books", "at most 3 books")
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        self.edit_src("membership.md", "at most 3 books", "at most 2 books")  # changed again before it was applied
        with self.assertRaises(util.BAError) as cm:
            self.apply(p, cid)
        self.assertEqual(cm.exception.code, "CHANGE_RECORD_STALE")
        out = changemod.check(p, True)
        self.assertEqual(changemod.get(p, cid)["status"], "SUPERSEDED")
        self.assertEqual(changemod.get(p, cid)["superseded_by"], out["unreviewed_ids"][0])
        self.apply(p, out["unreviewed_ids"][0])
        with self.assertRaises(util.BAError) as cm:
            self.apply(p, out["unreviewed_ids"][0])
        self.assertEqual(cm.exception.code, "NOT_APPLICABLE")

    def test_reverting_the_file_closes_the_detected_record(self):
        p = self.flow()
        self.edit_src("membership.md", "at most 5 books", "at most 3 books")
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        self.edit_src("membership.md", "at most 3 books", "at most 5 books")
        out = changemod.check(p, True)
        self.assertEqual(out["unreviewed_ids"], [])
        self.assertEqual(changemod.get(p, cid)["status"], "REVERTED")

    def test_confirm_unaffected_repins_and_reconciles(self):
        p = self.flow()
        self.edit_src("membership.md", "Borrowed books must be returned within 14 days.", "Borrowed books must be returned within 14 days.\nLate returns are handled elsewhere.")
        self.edit_src("membership.md", "at most 5 books", "at most 5 books in total")
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        self.apply(p, cid)
        k = key("LIB-003")
        with self.assertRaises(util.BAError) as cm:  # flagged: cannot be approved as it stands
            acs.approve(p, k, 1, "ok", "Priya Nair", "Product Owner")
        self.assertEqual(cm.exception.code, "UNKNOWN_AC")  # approved criteria change through `ac edit` / `confirm-unaffected`, not `approve`
        r = acs.confirm_unaffected(p, k, "The limit is the same; criteria still hold.", "Priya Nair", "Product Owner")
        self.assertTrue(r["requirement_text_confirmed_unaffected"])
        a = p.read_json("acceptance/approved/" + projmod.ac_fname(k) + ".json")
        self.assertIsNone(a["impact_review"])
        self.assertEqual(a["requirement_hash"], reqindex.by_key(p)[k]["material_hash"])
        self.assertEqual(a["version"], 1)  # same approval, no new version
        self.assertNotIn("*Under review", self.enriched(p))
        # the requirement item closes with the gap analysis; then the record is RECONCILED
        gapmod.commit_analysis(p, True)
        changemod.reconcile(p)
        self.assertEqual(changemod.get(p, cid)["status"], "RECONCILED")
        # the approved criteria were not flagged as manually edited by the re-pin
        self.assertEqual(changemod.check(p, True)["unreviewed_ids"], [])

    def test_edit_after_change_creates_a_new_version_and_old_stays_in_force(self):
        p = self.flow()
        self.edit_src("membership.md", "at most 5 books", "at most 3 books")
        self.apply(p, changemod.check(p, True)["unreviewed_ids"][0])
        k = key("LIB-003")
        r = acs.edit(p, k, {"acceptance_criteria": ["Given a member with 3 books, when they borrow a 4th, then the borrow is refused."]},
                     "limit lowered to 3")
        self.assertEqual(r["new_version"], 2)
        self.assertTrue(r["approved_version_still_in_force"])
        self.assertIn("Given a valid case for LIB-003", self.enriched(p))  # v1 stays in the document until v2 is approved
        gapmod.commit_analysis(p, True)
        cand = p.read_json("acceptance/candidates/" + projmod.ac_fname(k) + ".json")
        self.assertEqual(cand["requirement_hash"], reqindex.by_key(p)[k]["material_hash"])
        acs.approve(p, k, 2, "Approve v2.", "Priya Nair", "Product Owner")
        t = self.enriched(p)
        self.assertIn("4th", t)
        self.assertNotIn("Given a valid case for LIB-003", t)
        self.assertIn('ac="v2"', t)
        # the agent edit created a reapproval item that is now done
        changemod.reconcile(p)
        self.assertEqual(changemod.get(p, changemod.records(p)[0]["change_id"])["status"], "RECONCILED")

    def test_new_requirement_item_can_be_dismissed(self):
        p = self.flow()
        f = self.src / "reporting.md"
        f.write_bytes(f.read_bytes() + b"| LIB-010 | Staff can print the report. | Could |\n")
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        rec = changemod.get(p, cid)
        self.assertEqual([x["req_key"] for x in rec["impact"]["new_requirements"]], ["reporting.md::LIB-010"])
        res = self.apply(p, cid)
        item = [i for i in res["open_items"] if i["kind"] == "new_requirement"][0]
        with self.assertRaises(util.BAError) as cm:
            changemod.dismiss(p, cid, item["item_id"], "", "Priya Nair", "Product Owner")
        self.assertEqual(cm.exception.code, "REASON_REQUIRED")
        with self.assertRaises(util.BAError) as cm:  # work items are closed by doing the work
            changemod.dismiss(p, cid, [i["item_id"] for i in res["open_items"] if i["kind"] == "gap"][0] if any(i["kind"] == "gap" for i in res["open_items"]) else "ac:x",
                              "no", "Priya Nair", "Product Owner")
        self.assertIn(cm.exception.code, ("NOT_DISMISSABLE", "UNKNOWN_ITEM"))
        out = changemod.dismiss(p, cid, item["item_id"], "Printing is out of scope for now.", "Priya Nair", "Product Owner")
        self.assertEqual(out["state"], "DISMISSED")
        self.assertIn("reporting.md::LIB-010", [e["req_key"] for e in p.requirements()])
        self.assertIn("| LIB-010 |", self.enriched(p, "reporting.md"))

    def test_removed_requirement_archives_its_criteria(self):
        p = self.flow()
        t = (self.src / "membership.md").read_text(encoding="utf-8")
        start = t.index("### LIB-003")
        end = t.index("### LIB-004")
        (self.src / "membership.md").write_bytes((t[:start] + t[end:]).encode("utf-8"))
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        res = self.apply(p, cid)
        k = key("LIB-003")
        self.assertEqual(p.requirement(k)["status"], "REMOVED")
        self.assertNotIn(k, [e["req_key"] for e in p.requirements()])
        self.assertNotIn("LIB-003", self.enriched(p))
        self.assertTrue(any(i["kind"] == "ac" and i["ref"] == k for i in res["open_items"]))
        with self.assertRaises(util.BAError) as cm:  # a removed requirement cannot be "confirmed unaffected"
            acs.confirm_unaffected(p, k, "still fine", "Priya Nair", "Product Owner")
        self.assertEqual(cm.exception.code, "REQUIREMENT_REMOVED")
        with self.assertRaises(util.BAError) as cm:
            acs.archive(p, key("LIB-001"), "no", "Priya Nair", "Product Owner")
        self.assertEqual(cm.exception.code, "REQUIREMENT_STILL_PRESENT")
        out = acs.archive(p, k, "The requirement was dropped upstream.", "Priya Nair", "Product Owner")
        self.assertEqual(out["archived"], ["approved:v1"])
        self.assertFalse(p.exists("acceptance/approved/" + projmod.ac_fname(k) + ".json"))
        self.assertTrue(p.exists("acceptance/archive/" + projmod.ac_fname(k) + ".v1.json"))
        self.assertEqual(checks.validate_project(p)["errors"], [])
        # the removed key still resolves (gaps and history that name it keep working)
        self.assertEqual(p.req_key(k), k)
        with self.assertRaises(util.BAError):
            p.req_key(k, active_only=True)
        self.assertNotIn(k, checks.traceability(p)["coverage"])

    def test_gap_on_a_removed_requirement_is_flagged(self):
        p = self.flow()
        self.edit_src("lending.md", "Pages must load quickly for members on mobile devices.", "Pages must load fast.")
        res = self.apply(p, changemod.check(p, True)["unreviewed_ids"][0])
        self.assertEqual(res["effects"]["gaps_flagged"], ["GAP-002"])
        gapmod.commit_analysis(p, True)  # the re-run is the reassessment
        self.assertFalse(p.gap("GAP-002")["needs_impact_review"])

    def test_snapshot_history_keeps_every_accepted_version(self):
        p = self.flow()
        orig = (self.src / "membership.md").read_bytes()
        self.edit_src("membership.md", "at most 5 books", "at most 3 books")
        self.apply(p, changemod.check(p, True)["unreviewed_ids"][0])
        vs = snapshots.versions(p, "membership.md")
        self.assertEqual([v["version"] for v in vs], [1, 2])
        self.assertEqual(snapshots.read_bytes(p, vs[0]), orig)
        self.assertIn(b"3 books", snapshots.read_bytes(p, vs[1]))
        self.assertEqual(vs[1]["reason"], "requirement change accepted after impact review")
        self.assertIsNotNone(vs[1]["change_id"])
        self.assertEqual(snapshots.latest(p, "lending.md")["version"], 1)  # untouched files are not re-recorded

    def test_missing_folder_is_a_warning(self):
        p = self.flow()
        for f in list(self.src.glob("*.md")):
            f.unlink()
        self.src.rmdir()
        out = changemod.check(p, True)
        self.assertEqual(out["source"], "missing")
        self.assertTrue(out["warnings"])

    def test_source_change_audit_and_adr(self):
        p = self.flow()
        self.edit_src("membership.md", "at most 5 books", "at most 3 books")
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        self.apply(p, cid)
        types = [e["event_type"] for e in p.audit_events()]
        for t in ("CHANGE_DETECTED", "SOURCE_CHANGED", "SOURCE_SNAPSHOT_RECORDED", "CHANGE_APPLIED", "AC_IMPACT_FLAGGED"):
            self.assertIn(t, types)
        ev = [e for e in p.audit_events() if e["event_type"] == "CHANGE_APPLIED"][0]
        self.assertEqual((ev["by_name"], ev["by_role"]), ("Priya Nair", "Product Owner"))


if __name__ == "__main__":
    unittest.main()
