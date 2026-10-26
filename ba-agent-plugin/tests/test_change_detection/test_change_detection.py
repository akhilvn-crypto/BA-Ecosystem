"""Approved acceptance criteria: hand edits (in the enriched copy or the record), deletion, restore, revert, agent edits, reconciliation."""
import json
import unittest

from tests.helpers import (BATest, acs, actrack, changemod, checks, docmodel, docsync, gapmod, projmod, reqindex, util, key)

K1, K2, K3 = key("LIB-001"), key("LIB-002"), key("LIB-003")
EDITED = "Given a visitor with a valid email, when they register, then a member exists and a welcome page is shown."


def fn(rid):
    return projmod.ac_fname(key(rid))


class Base(BATest):
    def approved3(self):
        p = self.to_acs(3)
        for r in ("LIB-001", "LIB-002", "LIB-003"):
            self.approve(p, r)
        return p

    def jpath(self, p, rid, bucket="approved"):
        return p.path(f"acceptance/{bucket}/{fn(rid)}.json")

    def edit_json(self, p, rid, mutate):
        f = self.jpath(p, rid)
        d = json.loads(f.read_text(encoding="utf-8"))
        mutate(d)
        f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")

    def edit_block(self, p, old, new, rel="membership.md"):
        f = p.enriched_root / rel
        t = f.read_text(encoding="utf-8")
        self.assertIn(old, t)
        f.write_bytes(t.replace(old, new, 1).encode("utf-8"))

    def apply(self, p, cid=None):
        return changemod.apply(p, cid, "Priya Nair", "Product Owner", "I have seen it; apply.")

    def err(self, fn_, *a, **kw):
        with self.assertRaises(util.BAError) as cm:
            fn_(*a, **kw)
        return cm.exception


class ApprovedAcChangeTests(Base):
    def test_approval_records_hashes_and_an_immutable_copy(self):
        p = self.approved3()
        e = actrack.entry(p, K1)
        v = actrack.current(e)
        self.assertEqual(v["version"], 1)
        self.assertEqual(v["approver_name"], "Priya Nair")
        self.assertTrue(p.exists(v["snapshot"]))
        self.assertEqual(actrack.snapshot_record(p, v)["acceptance_criteria"], p.read_json(f"acceptance/approved/{fn('LIB-001')}.json")["acceptance_criteria"])
        self.assertEqual(changemod.check(p, True)["unreviewed_ids"], [])
        self.assertEqual(changemod.records(p), [])

    # ------------------------------------------------- edit in the enriched copy
    def test_edit_of_an_approved_criterion_in_the_enriched_copy_is_detected_before_anything_moves(self):
        p = self.approved3()
        self.edit_block(p, "Given a valid case for LIB-001, when the action is performed, then the expected outcome is produced.", EDITED)
        before = self.jpath(p, "LIB-001").read_bytes()
        out = changemod.check(p, True)
        self.assertEqual(len(out["unreviewed_ids"]), 1)
        rec = changemod.get(p, out["unreviewed_ids"][0])
        self.assertEqual((rec["type"], rec["classification"], rec["origin"]), ("AC_CHANGE", "MATERIAL", "MANUAL"))
        self.assertEqual(rec["subject"]["edited_via"], "enriched")
        self.assertEqual(rec["subject"]["req_key"], K1)
        self.assertEqual(rec["subject"]["observed"]["criteria"][0], EDITED)
        self.assertEqual(len(rec["impact"]["criteria"]["added"]), 1)
        self.assertEqual(len(rec["impact"]["criteria"]["removed"]), 1)
        self.assertEqual(rec["impact"]["approval_record"]["approver_name"], "Priya Nair")
        # nothing downstream moved: approved record untouched, no candidate, the edit still sits in the copy
        self.assertEqual(self.jpath(p, "LIB-001").read_bytes(), before)
        self.assertFalse(p.exists(f"acceptance/candidates/{fn('LIB-001')}.json"))
        self.assertIn(EDITED, self.enriched(p))
        # a hand edit is never overwritten by later state changes while it waits
        gapmod.reopen(p, "GAP-003", "rethink", "Priya Nair", "Product Owner")
        self.assertIn(EDITED, self.enriched(p))
        self.assertEqual(len(changemod.records(p)), 1)  # and recording is idempotent
        self.assertEqual(changemod.check(p, True)["unreviewed_ids"], [rec["change_id"]])

    def test_applying_an_enriched_edit_keeps_everything_and_requires_reapproval(self):
        p = self.approved3()
        self.edit_block(p, "when the action is performed, then the expected outcome is produced.", "when the action is performed, then a welcome page is shown.")
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        with self.assertRaises(util.BAError):
            changemod.apply(p, cid)
        res = self.apply(p, cid)
        eff = res["effects"]
        self.assertEqual((eff["new_candidate_version"], eff["approved_version_restored"], eff["enriched_copy_restored"]), (2, 1, True))
        kept = p.path(eff["edit_preserved_as"][0])
        self.assertIn("welcome page", kept.read_text(encoding="utf-8"))  # the edit, verbatim
        # approved version is back in the document and in the folder; v2 is a candidate
        self.assertIn("then the expected outcome is produced", self.enriched(p))
        self.assertNotIn("welcome page", self.enriched(p))
        self.assertEqual(p.read_json(f"acceptance/approved/{fn('LIB-001')}.json")["version"], 1)
        cand = p.read_json(f"acceptance/candidates/{fn('LIB-001')}.json")
        self.assertEqual((cand["version"], cand["status"], cand["supersedes_version"]), (2, "CANDIDATE", 1))
        self.assertIn("welcome page", cand["acceptance_criteria"][0])
        self.assertEqual(cand["review_history"][-1]["action"], "MANUAL_EDIT_DETECTED")
        self.assertEqual([i["item_id"] for i in res["open_items"]], [f"reapproval:{K1}:v2"])
        types = [e["event_type"] for e in p.audit_events()]
        self.assertIn("AC_MANUAL_EDIT_DETECTED", types)
        self.assertIn("AC_REAPPROVAL_REQUIRED", types)
        self.assertEqual(changemod.check(p, True)["unreviewed_ids"], [])
        # approving v2 completes the change
        acs.approve(p, K1, 2, "Approve v2.", "Priya Nair", "Product Owner")
        changemod.reconcile(p)
        self.assertEqual(changemod.get(p, cid)["status"], "RECONCILED")
        self.assertIn("welcome page", self.enriched(p))
        self.assertEqual(checks.validate_project(p)["errors"], [])

    def test_two_edits_in_one_file_are_both_kept_and_the_copy_is_restored_after_the_last(self):
        p = self.approved3()
        self.edit_block(p, "Given a valid case for LIB-001, when the action is performed, then the expected outcome is produced.", "Given one, when it runs, then it is edited once.")
        self.edit_block(p, "Given a valid case for LIB-003, when the action is performed, then the expected outcome is produced.", "Given three, when it runs, then it is edited thrice.")
        ids = changemod.check(p, True)["unreviewed_ids"]
        self.assertEqual(len(ids), 2)
        first = self.apply(p, ids[0])
        self.assertFalse(first["effects"]["enriched_copy_restored"])  # the other edit is still waiting
        self.assertIn("edited thrice", self.enriched(p))
        self.assertEqual(changemod.check(p, True)["unreviewed_ids"], [ids[1]])
        second = self.apply(p, ids[1])
        self.assertTrue(second["effects"]["enriched_copy_restored"])
        t = self.enriched(p)
        self.assertNotIn("edited once", t)
        self.assertNotIn("edited thrice", t)
        c1 = p.read_json(f"acceptance/candidates/{fn('LIB-001')}.json")
        c3 = p.read_json(f"acceptance/candidates/{fn('LIB-003')}.json")
        self.assertIn("edited once", c1["acceptance_criteria"][0])
        self.assertIn("edited thrice", c3["acceptance_criteria"][0])

    def test_editing_only_generated_text_is_restored_not_a_review(self):
        p = self.approved3()
        self.edit_block(p, "*Approved by Priya Nair (Product Owner)", "*Approved by Somebody Else (CEO)")
        out = changemod.check(p, True)
        self.assertEqual(out["unreviewed_ids"], [])
        self.assertNotIn("Somebody Else", self.enriched(p))
        self.assertEqual(changemod.records(p)[-1]["status"], "RECORDED")

    def test_enriched_edit_that_is_not_given_when_then_is_captured_but_cannot_be_approved(self):
        p = self.approved3()
        self.edit_block(p, "Given a valid case for LIB-002, when the action is performed, then the expected outcome is produced.", "It should just work nicely for everyone.")
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        res = self.apply(p, cid)
        self.assertEqual(res["effects"]["new_candidate_version"], 2)
        self.assertEqual(self.err(acs.approve, p, K2, 2, "ok", "Priya Nair", "Product Owner").code, "ACCEPTANCE_FORMAT")

    def test_stale_enriched_record_is_refused(self):
        p = self.approved3()
        self.edit_block(p, "when the action is performed, then the expected outcome is produced.", "when the action is performed, then A happens.")
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        self.edit_block(p, "then A happens.", "then B happens.")
        self.assertEqual(self.err(changemod.apply, p, cid, "Priya Nair", "Product Owner", "ok").code, "CHANGE_RECORD_STALE")
        out = changemod.check(p, True)
        self.assertEqual(changemod.get(p, cid)["status"], "SUPERSEDED")
        self.assertEqual(len(out["unreviewed_ids"]), 1)

    def test_putting_the_text_back_reverts_an_unapplied_detection(self):
        p = self.approved3()
        self.edit_block(p, "then the expected outcome is produced.", "then A happens.")
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        self.edit_block(p, "then A happens.", "then the expected outcome is produced.")
        out = changemod.check(p, True)
        self.assertEqual(out["unreviewed_ids"], [])
        self.assertEqual(changemod.get(p, cid)["status"], "REVERTED")

    def test_reverting_an_edit_keeps_the_approval_and_the_discarded_copy(self):
        p = self.approved3()
        self.edit_block(p, "then the expected outcome is produced.", "then A happens.")
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        self.apply(p, cid)
        with self.assertRaises(util.BAError):
            changemod.revert_edit(p, K1, "", "Priya Nair", "Product Owner")
        r = changemod.revert_edit(p, K1, "The edit was a mistake.", "Priya Nair", "Product Owner")
        self.assertEqual((r["discarded_version"], r["approved_version_in_force"]), (2, 1))
        self.assertFalse(p.exists(f"acceptance/candidates/{fn('LIB-001')}.json"))
        self.assertTrue(p.exists(f"acceptance/archive/{fn('LIB-001')}.v2.json"))
        self.assertEqual(changemod.get(p, cid)["status"], "RECONCILED")
        self.assertEqual(self.err(changemod.revert_edit, p, K1, "again", "Priya Nair", "Product Owner").code, "NOTHING_TO_REVERT")
        self.assertEqual(self.err(changemod.revert_edit, p, K2, "x", "Priya Nair", "Product Owner").code, "NOTHING_TO_REVERT")

    def test_enriched_edit_reopens_a_completed_workflow(self):
        p = self.to_ac_stage()
        acs.add_acs(p, [self.ac_item(r) for r in ("LIB-001", "LIB-002", "LIB-003", "LIB-004", "LIB-005", "LIB-006", "LIB-007", "LIB-008", "LIB-009")])
        for r in ("LIB-001", "LIB-002", "LIB-003", "LIB-004", "LIB-005", "LIB-006", "LIB-007", "LIB-008", "LIB-009"):
            self.approve(p, r)
        checks.complete_workflow(p)
        self.assertEqual(p.state["stage"], "COMPLETED")
        self.edit_block(p, "Given a valid case for LIB-001, when the action is performed, then the expected outcome is produced.", EDITED)
        self.apply(p, changemod.check(p, True)["unreviewed_ids"][0])
        self.assertEqual(p.state["stage"], "WAITING_FOR_AC_REVIEW")
        self.assertFalse(checks.completion(p)["complete"])

    # ------------------------------------------------------ edit of the record
    def test_manual_json_edit_is_detected_and_explained_before_anything_moves(self):
        p = self.approved3()
        self.edit_json(p, "LIB-001", lambda d: d["acceptance_criteria"].append("Given an admin, when they register a member, then it is audited."))
        out = changemod.check(p, True)
        rec = changemod.get(p, out["unreviewed_ids"][0])
        self.assertEqual(rec["subject"]["change_type"], "MANUAL_EDIT")
        self.assertEqual(rec["subject"]["edited_via"], "json")
        self.assertEqual(rec["impact"]["criteria"]["added"], ["Given an admin, when they register a member, then it is audited."])
        self.assertIn("acceptance_criteria", rec["impact"]["fields_changed"])
        self.assertEqual(len(p.read_json(f"acceptance/approved/{fn('LIB-001')}.json")["acceptance_criteria"]), 3)  # not touched yet
        self.assertIn("approved criteria on disk differ", " ".join(checks.validate_project(p)["warnings"]))

    def test_applying_a_json_edit_restores_the_approval_and_makes_the_edit_the_next_version(self):
        p = self.approved3()
        approved = p.read_json(f"acceptance/approved/{fn('LIB-001')}.json")
        self.edit_json(p, "LIB-001", lambda d: d["acceptance_criteria"].append("Given an admin, when they register a member, then it is audited."))
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        res = self.apply(p, cid)
        eff = res["effects"]
        self.assertEqual((eff["new_candidate_version"], eff["approved_version_restored"]), (2, 1))
        self.assertTrue(p.path(eff["edit_preserved_as"][0]).exists())
        restored = p.read_json(f"acceptance/approved/{fn('LIB-001')}.json")
        self.assertEqual(restored["acceptance_criteria"], approved["acceptance_criteria"])
        cand = p.read_json(f"acceptance/candidates/{fn('LIB-001')}.json")
        self.assertEqual(len(cand["acceptance_criteria"]), 3)
        self.assertEqual(changemod.check(p, True)["unreviewed_ids"], [])

    def test_formatting_only_json_edits_do_not_touch_the_approval(self):
        p = self.approved3()
        self.edit_json(p, "LIB-001", lambda d: d.__setitem__("acceptance_criteria", [c + "  " for c in d["acceptance_criteria"]]))
        out = changemod.check(p, True)
        self.assertEqual(out["unreviewed_ids"], [])
        self.assertEqual(len(out["formatting_only_accepted"]), 1)
        self.assertEqual(changemod.records(p)[-1]["classification"], "FORMATTING_ONLY")
        self.assertEqual(changemod.check(p, True)["formatting_only_accepted"], [])  # accepted once

    def test_altering_the_approval_record_by_hand_is_not_accepted(self):
        p = self.approved3()
        self.edit_json(p, "LIB-002", lambda d: d["approval"].__setitem__("approver_name", "Somebody Else"))
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        rec = changemod.get(p, cid)
        self.assertEqual(rec["subject"]["change_type"], "APPROVAL_ALTERED")
        self.assertTrue(rec["subject"]["control_fields_altered"])
        res = self.apply(p, cid)
        self.assertIsNone(res["effects"]["new_candidate_version"])
        self.assertEqual(p.read_json(f"acceptance/approved/{fn('LIB-002')}.json")["approval"]["approver_name"], "Priya Nair")

    def test_corrupt_json_is_preserved_and_the_approval_restored(self):
        p = self.approved3()
        self.jpath(p, "LIB-001").write_text("{ not json", encoding="utf-8")
        self.assertEqual(p.unreadable_ac_files(), [f"acceptance/approved/{fn('LIB-001')}.json"])
        self.assertTrue(any("not valid JSON" in e for e in checks.validate_project(p)["errors"]))
        checks.status_report(p)  # must not crash
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        self.assertEqual(changemod.get(p, cid)["subject"]["change_type"], "UNREADABLE")
        res = self.apply(p, cid)
        self.assertTrue(p.path(res["effects"]["edit_preserved_as"][0]).read_text(encoding="utf-8").startswith("{ not json"))
        self.assertEqual(p.read_json(f"acceptance/approved/{fn('LIB-001')}.json")["version"], 1)
        self.assertEqual(checks.validate_project(p)["errors"], [])

    def test_damaged_snapshot_is_reported_and_never_trusted(self):
        p = self.approved3()
        v = actrack.current(actrack.entry(p, K1))
        snap = p.path(v["snapshot"])
        d = json.loads(snap.read_text(encoding="utf-8"))
        d["acceptance_criteria"] = ["Given forged, when it is read, then it is trusted wrongly."]
        snap.write_text(json.dumps(d), encoding="utf-8")
        self.assertIsNone(actrack.snapshot_record(p, v))
        self.assertTrue(any("snapshot" in w and "damaged" in w for w in checks.validate_project(p)["warnings"]))
        self.jpath(p, "LIB-001").unlink()
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        self.apply(p, cid)
        self.assertEqual(self.err(changemod.restore_ac, p, K1, "put it back", "Priya Nair", "Product Owner").code, "SNAPSHOT_UNAVAILABLE")

    # ------------------------------------------------------ deletion / restore
    def test_deleted_approved_ac_is_detected_preserved_and_restorable(self):
        p = self.approved3()
        self.jpath(p, "LIB-002").unlink()
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        rec = changemod.get(p, cid)
        self.assertEqual(rec["subject"]["change_type"], "DELETED")
        res = self.apply(p, cid)
        self.assertEqual(res["effects"]["snapshot_kept"], True)
        self.assertEqual(actrack.entry(p, K2)["status"], actrack.DELETED)
        self.assertEqual([i["kind"] for i in res["open_items"]], ["ac_removal"])
        self.assertIn("LIB-002", checks.completion(p)["reasons"][-1] + " ".join(checks.completion(p)["reasons"]))
        with self.assertRaises(util.BAError):
            changemod.restore_ac(p, K2, "", "Priya Nair", "Product Owner")
        r = changemod.restore_ac(p, K2, "Deleted by accident.", "Priya Nair", "Product Owner")
        self.assertEqual(r["version"], 1)
        self.assertTrue(self.jpath(p, "LIB-002").exists())
        self.assertEqual(actrack.entry(p, K2)["status"], actrack.TRACKED)
        self.assertEqual(changemod.get(p, cid)["status"], "RECONCILED")
        self.assertEqual(changemod.check(p, True)["unreviewed_ids"], [])
        self.assertIn(K2, self.enriched(p))

    def test_confirming_a_removal_is_a_recorded_decision(self):
        p = self.approved3()
        self.jpath(p, "LIB-002").unlink()
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        self.apply(p, cid)
        self.assertEqual(self.err(changemod.dismiss, p, cid, "ac_removal:nothing", "no", "Priya Nair", "Product Owner").code, "UNKNOWN_ITEM")
        out = changemod.dismiss(p, cid, f"ac_removal:{K2}", "These criteria were superseded by another requirement.", "Priya Nair", "Product Owner")
        self.assertEqual((out["state"], out["change_status"]), ("DISMISSED", "RECONCILED"))
        self.assertEqual(actrack.entry(p, K2)["status"], actrack.REMOVAL_CONFIRMED)
        self.assertEqual(changemod.check(p, True)["unreviewed_ids"], [])
        self.assertTrue(any(e["event_type"] == "CHANGE_ITEM_DISMISSED" for e in p.audit_events()))

    def test_untracked_record_dropped_into_approved_is_not_trusted(self):
        p = self.to_acs(1)
        src = p.path(f"acceptance/candidates/{fn('LIB-001')}.json")
        d = json.loads(src.read_text(encoding="utf-8"))
        d["status"], d["approval"] = "APPROVED", {"version": 1, "decision": "APPROVED", "at_ist": "2026-10-22T19:00:00+05:30", "approver_name": "X", "approver_role": "Y", "statement": "fake"}
        p.path(f"acceptance/approved/{fn('LIB-001')}.json").write_text(json.dumps(d), encoding="utf-8")
        src.unlink()
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        self.assertEqual(changemod.get(p, cid)["subject"]["change_type"], "UNTRACKED_APPROVED")
        self.apply(p, cid)
        self.assertFalse(p.exists(f"acceptance/approved/{fn('LIB-001')}.json"))
        self.assertTrue(p.exists(f"acceptance/candidates/{fn('LIB-001')}.json"))
        self.assertNotIn(K1, self.enriched(p))

    def test_approvals_made_before_tracking_existed_are_adopted_not_flagged(self):
        p = self.approved3()
        p.path("snapshots/ac/index.json").unlink()
        out = changemod.check(p, True)
        self.assertEqual(out["unreviewed_ids"], [])
        self.assertEqual(sorted(actrack.known_keys(p)), sorted([K1, K2, K3]))
        self.assertTrue(any(e["event_type"] == "AC_TRACKING_ADOPTED" for e in p.audit_events()))

    # ---------------------------------------------------------- agent edits
    def test_ba_agent_edit_of_an_approved_record_is_tracked_with_history(self):
        p = self.approved3()
        r = acs.edit(p, K1, {"acceptance_criteria": [EDITED]}, "wording")
        rec = [c for c in changemod.records(p) if c["origin"] == "BA_AGENT"][0]
        self.assertEqual((rec["status"], rec["subject"]["new_version"]), ("APPLIED", r["new_version"]))
        self.assertEqual(changemod.check(p, True)["unreviewed_ids"], [])  # BA Agent changes are never re-detected as manual edits
        hist = changemod.ac_history(p, K1)
        self.assertEqual(hist["awaiting_review"][0]["version"], 2)
        self.assertEqual(hist["changes"][0]["origin"], "BA_AGENT")
        self.assertEqual(checks.completion(p)["complete"], False)

    def test_history_of_unknown_requirement(self):
        p = self.approved3()
        self.assertEqual(self.err(changemod.ac_history, p, key("LIB-005")).code, "UNKNOWN_AC")

    # ---------------------------------------------------------------- misc
    def test_unknown_change_and_ambiguous_apply_are_refused(self):
        p = self.approved3()
        self.assertEqual(self.err(changemod.get, p, "CHG-009").code, "UNKNOWN_CHANGE")
        self.assertEqual(self.err(changemod.apply, p, None, "Priya Nair", "Product Owner", "ok").code, "CHANGE_ID_REQUIRED")
        self.edit_block(p, "then the expected outcome is produced.", "then A happens.", )
        self.edit_block(p, "Given a valid case for LIB-003, when the action is performed, then the expected outcome is produced.", "Given x, when y, then z happens.")
        changemod.check(p, True)
        e = self.err(changemod.apply, p, None, "Priya Nair", "Product Owner", "ok")
        self.assertEqual(e.code, "CHANGE_ID_REQUIRED")

    def test_guard_blocks_until_an_ac_change_is_applied(self):
        p = self.approved3()
        self.edit_block(p, "then the expected outcome is produced.", "then A happens.")
        e = self.err(changemod.guard, p, "ac edit")
        self.assertEqual(e.code, "CHANGES_UNREVIEWED")
        self.assertIn("CHG-001", e.message)
        self.apply(p, e.details["change_ids"][0])
        changemod.guard(p, "ac edit")  # applied: the workflow may continue

    def test_status_and_trace_report_changes_read_only(self):
        p = self.approved3()
        self.edit_block(p, "then the expected outcome is produced.", "then A happens.")
        r = checks.status_report(p)  # read-only: nothing recorded
        self.assertEqual(changemod.records(p), [])
        self.assertEqual(r["changes"]["undetected"]["enriched"], ["membership.md"])
        self.assertIn("changes check", r["next_action"])
        changemod.check(p, True)
        r = checks.status_report(p)
        self.assertEqual(len(r["changes"]["unreviewed"]), 1)
        self.assertIn("await the user's decision", r["next_action"])
        t = checks.traceability(p, write=True)
        self.assertEqual(len(t["change_impact"]["unreviewed"]), 1)
        self.assertIn("Awaiting your decision: CHG-001", p.path("reports/traceability-report.md").read_text(encoding="utf-8"))

    def test_resume_in_a_new_session_keeps_detected_and_applied_changes(self):
        p = self.approved3()
        self.edit_block(p, "then the expected outcome is produced.", "then A happens.")
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        p2 = projmod.Project.open(p.root)
        self.assertEqual([c["change_id"] for c in changemod.records(p2, "DETECTED")], [cid])
        self.apply(p2, cid)
        p3 = projmod.Project.open(p.root)
        self.assertEqual(changemod.get(p3, cid)["status"], "APPLIED")
        self.assertIn("await review", checks.next_action(p3))
        self.assertFalse(checks.completion(p3)["complete"])

    def test_every_step_is_in_the_audit_log_with_ist_timestamps(self):
        p = self.approved3()
        self.edit_block(p, "then the expected outcome is produced.", "then A happens.")
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        self.apply(p, cid)
        evs = [e for e in p.audit_events() if e["event_type"].startswith(("CHANGE_", "AC_MANUAL", "AC_REAPPROVAL"))]
        self.assertTrue({"CHANGE_DETECTED", "CHANGE_APPLIED", "AC_MANUAL_EDIT_DETECTED"} <= {e["event_type"] for e in evs})
        for e in p.audit_events():
            self.assertTrue(util.is_ist_iso(e["at_ist"]), e)
        self.assertEqual(checks.validate_project(p)["errors"], [])

    def test_library_source_check_still_works_in_one_step_and_leaves_a_record(self):
        p = self.approved3()
        f = self.src / "membership.md"
        f.write_bytes(f.read_bytes().replace(b"at most 5 books", b"at most 3 books"))
        eff = changemod.check_and_apply_source(p)
        self.assertEqual(eff["requirements_changed"], [K3])
        rec = changemod.records(p)[0]
        self.assertEqual((rec["status"], rec["applied"]["by_name"]), ("APPLIED", None))
        self.assertEqual(changemod.check_and_apply_source(p), {"changed": False, "fingerprint": p.state["source"]["fingerprint"]})


if __name__ == "__main__":
    unittest.main()
