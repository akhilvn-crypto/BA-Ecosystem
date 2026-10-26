"""Traceability and validation on the acceptance-criteria model: blocking policy, requirement coverage, orphans, tampering, recovery, completion."""
import json
import unittest

from tests.helpers import BATest, acs, changemod, checks, gapmod, projmod, reqindex, util, key

ALL = ("LIB-001", "LIB-002", "LIB-003", "LIB-004", "LIB-005", "LIB-006", "LIB-007", "LIB-008", "LIB-009")


def code_of(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except util.BAError as e:
        return e.code
    return None


class BlockingPolicyTests(BATest):
    def test_requirement_with_unresolved_high_gap_cannot_get_acceptance_criteria(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        gapmod.defer(p, "GAP-001", "Waiting for finance", owner="Finance", revisit_by="2026-11-30", by_name="Priya Nair", by_role="Product Owner")
        self.answer_and_resolve(p, "GAP-002", "Under 3 seconds.", "Mobile page load is under 3 seconds.")
        self.answer_and_resolve(p, "GAP-003", "Reject with message.", "A 6th borrow is rejected with a message.")
        gapmod.commit_analysis(p, True)
        rows = {r["req_key"]: r for r in checks.requirement_rows(p)}
        self.assertEqual(rows[key("LIB-006")]["status"], "BLOCKED")
        self.assertEqual(rows[key("LIB-006")]["blocked_by"], ["GAP-001"])
        self.assertEqual(rows[key("LIB-001")]["status"], "READY")
        with self.assertRaises(util.BAError) as c:
            acs.add_acs(p, [self.ac_item("LIB-006")])
        self.assertIn("blocked", c.exception.message)
        self.assertEqual(acs.add_acs(p, [self.ac_item("LIB-001")])["created"], [key("LIB-001")], "unaffected requirements still proceed")
        self.assertEqual(p.gap("GAP-001")["status"], "DEFERRED", "a deferred gap is never implied to be resolved")
        self.assertEqual(checks.traceability(p)["blocked"], [key("LIB-006")])
        t = self.enriched(p, "lending.md")
        self.assertIn("*Acceptance criteria withheld until blocking gaps are resolved.*", t)
        self.assertIn("| GAP-001 | HIGH | DEFERRED |", t)
        comp = checks.complete_workflow(p)
        self.assertFalse(comp["complete"])
        self.assertIn("PARTIAL", comp["message"])
        self.assertTrue(any("blocked by unresolved gaps" in r for r in comp["outstanding"]))

    def test_nonblocking_gap_carry_forward_needs_the_explicit_policy_or_a_carry_forward(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        self.answer_and_resolve(p, "GAP-001", "R10/day max R200", "Fine is R10/day, max R200.")
        self.answer_and_resolve(p, "GAP-003", "Reject with message", "A 6th borrow is rejected with a message.")
        gapmod.defer(p, "GAP-002", "later", by_name="Priya Nair", by_role="Product Owner")  # MEDIUM
        gapmod.commit_analysis(p, True)
        st = {r["req_key"]: r["status"] for r in checks.requirement_rows(p)}
        self.assertEqual(st[key("LIB-007")], "BLOCKED", "strict default: even MEDIUM unresolved gaps block until the policy is accepted")
        self.assertEqual(code_of(gapmod.set_policy, p, True, "", "", "I accept"), "IDENTITY_REQUIRED")
        gapmod.set_policy(p, True, "Priya Nair", "Product Owner", "I accept carrying medium gaps forward.")
        st = {r["req_key"]: r for r in checks.requirement_rows(p)}
        self.assertEqual(st[key("LIB-007")]["status"], "READY_WITH_OPEN_GAPS")
        self.assertEqual(st[key("LIB-007")]["open_gaps"], ["GAP-002"])
        self.assertEqual(acs.add_acs(p, [self.ac_item("LIB-007", open_gap_refs=[dict(gap_id="GAP-002", potential_impact="Target time unknown.", readiness_effect="NEEDS_CLARIFICATION")])])["with_open_gaps"],
                         [key("LIB-007")])

    def test_open_gap_added_later_flags_existing_criteria_and_blocks_approval(self):
        p = self.to_ac_stage()
        acs.add_acs(p, [self.ac_item("LIB-004")])
        gapmod.add_gaps(p, [dict(self.gap_items()[0], title="Weekends", question="Do weekends count towards the 14 days?", priority="HIGH",
                                 priority_rationale="Changes the rule.", impacted_req_keys=[key("LIB-004")], material=False, evidence_quote=None)])
        a = p.read_json(f"acceptance/candidates/{projmod.ac_fname(key('LIB-004'))}.json")
        self.assertTrue(a["impact_review"]["pending"])
        self.assertEqual(p.state["stage"], "WAITING_FOR_GAP_INPUT")
        tri = acs.triage(p)["acs"][0]
        self.assertTrue(any("blocked by unresolved gap" in b for b in tri["blockers"]))
        with self.assertRaises(util.BAError) as c:
            acs.approve(p, key("LIB-004"), 1, "ok", "Priya Nair", "Product Owner")
        self.assertIn(c.exception.code, ("IMPACT_REVIEW_PENDING", "OPEN_GAPS"))


class CoverageTests(BATest):
    def test_uncovered_requirements_listed(self):
        p = self.to_ac_stage()
        acs.add_acs(p, [self.ac_item("LIB-001")])
        t = checks.traceability(p)
        self.assertEqual(t["uncovered_by_any_ac"], sorted(key(r) for r in ALL if r != "LIB-001"))
        self.assertEqual(t["not_covered_by_approved_ac"], sorted(key(r) for r in ALL))
        self.approve(p, "LIB-001")
        t = checks.traceability(p)
        self.assertEqual(t["coverage"][key("LIB-001")]["approved"], ["v1"])
        self.assertEqual(t["document_location"][key("LIB-006")], "lending.md:6")
        self.assertEqual(t["document_location"][key("LIB-008")], "reporting.md:5")

    def test_open_gap_traceability_names_the_criteria_that_declare_it(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        self.answer_and_resolve(p, "GAP-001", "R10", "Fine is R10 per day.")
        self.answer_and_resolve(p, "GAP-003", "Reject", "A 6th borrow is rejected.")
        gapmod.carry_forward(p, "GAP-002", "Priya Nair", "Product Owner", "later")
        gapmod.commit_analysis(p, True)
        acs.add_acs(p, [self.ac_item("LIB-007", open_gap_refs=[dict(gap_id="GAP-002", potential_impact="Target unknown.", readiness_effect="NEEDS_CLARIFICATION")])])
        t = checks.traceability(p)
        g = t["open_gap_traceability"]["GAP-002"]
        self.assertEqual((g["carried_forward"], g["blocks_ac"], g["acs"], g["acs_missing_the_link"]), (True, False, [key("LIB-007")], []))
        # a record that does not name an open gap on its requirement is reported
        rec = p.read_json(f"acceptance/candidates/{projmod.ac_fname(key('LIB-007'))}.json")
        rec["open_gap_refs"] = []
        rec["readiness"] = "READY_FOR_DEVELOPMENT"
        p.write_json(f"acceptance/candidates/{projmod.ac_fname(key('LIB-007'))}.json", rec)
        t = checks.traceability(p, write=True)
        self.assertEqual(t["acs_missing_open_gap_links"], [key("LIB-007")])
        self.assertTrue(any("differ from the criteria's declared open gaps" in w for w in checks.validate_project(p)["warnings"]))
        report = p.path("reports/traceability-report.md").read_text(encoding="utf-8")
        self.assertIn("| GAP-002 | MEDIUM | carried forward |", report)
        self.assertIn("lending.md:", report)

    def test_orphan_and_dangling_detection(self):
        p = self.to_acs(1)
        a = p.read_json(f"acceptance/candidates/{projmod.ac_fname(key('LIB-001'))}.json")
        orphan = dict(a, req_key="membership.md::LIB-777")
        p.write_json(f"acceptance/candidates/{projmod.ac_fname('membership.md::LIB-777')}.json", orphan)
        t = checks.traceability(p)
        self.assertIn("membership.md::LIB-777", t["orphan_acs"])
        self.assertTrue(any(d["to"] == "membership.md::LIB-777" for d in t["dangling_references"]))
        self.assertFalse(t["ok"])
        v = checks.validate_project(p)
        self.assertFalse(v["ok"])
        self.assertTrue(any("LIB-777" in e for e in v["errors"]))

    def test_a_requirement_with_criteria_in_two_unapproved_folders_is_an_error(self):
        p = self.to_acs(1)
        a = p.read_json(f"acceptance/candidates/{projmod.ac_fname(key('LIB-001'))}.json")
        a2 = dict(a, status="DEFERRED")
        p.write_json(f"acceptance/deferred/{projmod.ac_fname(key('LIB-001'))}.json", a2)
        self.assertTrue(any("multiple non-approved folders" in e for e in checks.validate_project(p)["errors"]))


class ValidateAndRecoverTests(BATest):
    def test_validate_detects_tampering(self):
        p = self.to_ac_stage()
        self.assertTrue(checks.validate_project(p)["ok"])
        gaps = p.read_json("state/gap-register.json")
        gaps["gaps"][0]["closed_by_name"] = None
        p.write_json("state/gap-register.json", gaps)
        v = checks.validate_project(p)
        self.assertFalse(v["ok"])
        self.assertTrue(any("missing closure metadata" in e for e in v["errors"]))

    def test_approved_record_without_audit_event_or_valid_approval_is_an_error(self):
        p = self.to_acs(1)
        self.approve(p, "LIB-001")
        f = f"acceptance/approved/{projmod.ac_fname(key('LIB-001'))}.json"
        a = p.read_json(f)
        a["approval"]["version"] = 9
        p.write_json(f, a)
        self.assertTrue(any("lacks a valid approval" in e for e in checks.validate_project(p)["errors"]))

    def test_readiness_must_match_the_open_gaps(self):
        p = self.to_acs(1)
        f = f"acceptance/candidates/{projmod.ac_fname(key('LIB-001'))}.json"
        a = p.read_json(f)
        a["readiness"] = "NOT_READY"
        p.write_json(f, a)
        self.assertTrue(any("readiness NOT_READY does not match" in e for e in checks.validate_project(p)["errors"]))

    def test_resolved_without_audit_event_is_detected_and_repaired_by_recover(self):
        p = self.with_gaps()
        gaps = p.read_json("state/gap-register.json")
        g = gaps["gaps"][0]
        g.update(status="RESOLVED", decision_id="DEC-001", resolution="x", decision_owner_name="A B", decision_owner_role="PO",
                 closed_by_name="A B", closed_by_role="PO", closed_at_ist="2026-10-22T18:45:00+05:30", closure_summary="x",
                 closure_validation={"status": "PASSED", "validated_at_ist": "2026-10-22T18:45:00+05:30"})
        g["history"].append({"from": "OPEN", "to": "RESOLVED", "at_ist": "2026-10-22T18:45:00+05:30"})
        p.write_json("state/gap-register.json", gaps)
        v = checks.validate_project(p)
        self.assertFalse(v["ok"])
        out = checks.recover(p)
        self.assertEqual(out["gaps_reopened"], ["GAP-001"])
        self.assertEqual(p.gap("GAP-001")["status"], "REOPENED")

    def test_interrupted_approval_is_repaired_by_recover(self):
        p = self.to_acs(1)
        name = projmod.ac_fname(key("LIB-001"))
        cand = p.read_json(f"acceptance/candidates/{name}.json")
        # crash after the approved record was written but before the candidate was removed and the audit event written
        approved = dict(cand, status="APPROVED", approval={"version": 1, "decision": "APPROVED", "at_ist": "2026-10-22T19:00:00+05:30",
                                                          "approver_name": "Priya Nair", "approver_role": "Product Owner", "statement": "ok"})
        p.write_json(f"acceptance/approved/{name}.json", approved)
        self.assertTrue(any("interrupted approval" in e for e in checks.validate_project(p)["errors"]))
        out = checks.recover(p)
        self.assertEqual(out["approvals_repaired"], [key("LIB-001")])
        self.assertEqual(out["audit_backfilled"], [key("LIB-001")])
        self.assertFalse(p.exists(f"acceptance/candidates/{name}.json"))
        self.assertTrue(any(e["event_type"] == "AC_APPROVED" and e.get("recovered") for e in p.audit_events()))
        self.assertIn("AC v1", self.enriched(p))
        self.assertTrue(checks.validate_project(p)["ok"])

    def test_crash_during_approval_leaves_a_recoverable_state(self):
        p = self.to_acs(1)
        calls = []

        def boom(point):
            if point == "ac_after_approved_write":
                calls.append(point)
                raise RuntimeError("crash")
        projmod.FAULT_HOOK = boom
        with self.assertRaises(RuntimeError):
            self.approve(p, "LIB-001")
        projmod.FAULT_HOOK = None
        self.assertEqual(calls, ["ac_after_approved_write"])
        checks.recover(p)
        v = checks.validate_project(p)
        self.assertEqual(v["errors"], [])
        self.assertFalse(p.exists(f"acceptance/candidates/{projmod.ac_fname(key('LIB-001'))}.json"))

    def test_recover_rolls_forward_an_interrupted_gap_closure_and_the_copy_follows(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        projmod.FAULT_HOOK = lambda pt: (_ for _ in ()).throw(RuntimeError("crash")) if pt == "after_decision" else None
        with self.assertRaises(RuntimeError):
            self.answer_and_resolve(p, "GAP-003", "Reject.", "A 6th borrow is rejected.")
        projmod.FAULT_HOOK = None
        self.assertTrue(list(p.path("state/pending").glob("*.closure.json")))
        self.assertIn("recover", checks.next_action(p))
        out = checks.recover(p)
        self.assertEqual(out["closures_rolled_forward"], ["GAP-003"])
        self.assertEqual(p.gap("GAP-003")["status"], "RESOLVED")
        self.assertNotIn("GAP-003", self.enriched(p))
        self.assertEqual(checks.validate_project(p)["errors"], [])

    def test_recover_re_renders_a_stale_copy(self):
        p = self.to_acs(1)
        self.approve(p, "LIB-001")
        f = p.enriched_root / "membership.md"
        stale = f.read_bytes()
        # simulate a crash between writing the record and writing the copy: the copy is the old generated one
        p.write_enriched("membership.md", stale.replace(b"ac=\"v1\"", b"ac=\"v0\""))
        out = checks.recover(p)
        self.assertEqual(out.get("enriched_drift"), ["membership.md"])  # a copy that differs from what was generated is drift, not silently fixed
        changemod.check(p, True)
        self.assertEqual(f.read_bytes(), stale)


class CompletionTests(BATest):
    def test_full_completion_only_when_everything_is_approved(self):
        p = self.to_ac_stage()
        c = checks.completion(p)
        self.assertFalse(c["complete"])
        acs.add_acs(p, [self.ac_item(r) for r in ALL])
        for r in ALL[:-1]:
            self.approve(p, r)
        c = checks.completion(p)
        self.assertFalse(c["complete"])
        self.assertTrue(any("not approved" in r for r in c["reasons"]))
        self.approve(p, ALL[-1])
        c = checks.completion(p)
        self.assertEqual(c["reasons"], [])
        out = checks.complete_workflow(p)
        self.assertTrue(out["complete"])
        self.assertEqual(p.state["stage"], "COMPLETED")
        self.assertIn("Workflow COMPLETE", out["message"])
        self.assertIn("COMPLETE", p.path("reports/workflow-summary.md").read_text(encoding="utf-8"))
        self.assertEqual(checks.traceability(p)["not_covered_by_approved_ac"], [])

    def test_not_ready_criteria_keep_the_workflow_partial_but_needs_clarification_does_not(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        self.answer_and_resolve(p, "GAP-001", "R10", "Fine is R10 per day.")
        self.answer_and_resolve(p, "GAP-003", "Reject", "A 6th borrow is rejected.")
        gapmod.carry_forward(p, "GAP-002", "Priya Nair", "Product Owner", "later")
        gapmod.commit_analysis(p, True)
        ref = dict(gap_id="GAP-002", potential_impact="Target unknown.", readiness_effect="NEEDS_CLARIFICATION")
        acs.add_acs(p, [self.ac_item(r, **({"open_gap_refs": [ref]} if r == "LIB-007" else {})) for r in ALL])
        for r in ALL:
            self.approve(p, r)
        c = checks.completion(p)
        self.assertEqual(c["reasons"], [])
        self.assertEqual(c["carried_forward"], ["GAP-002"])
        self.assertIn("carried-forward", checks.next_action(p))
        acs.edit(p, key("LIB-007"), {"open_gap_refs": [dict(ref, readiness_effect="NOT_READY")]}, "core behaviour depends on it")
        self.approve(p, "LIB-007", 2)
        self.assertTrue(any("not ready for development" in r for r in checks.completion(p)["reasons"]))

    def test_status_text_and_json(self):
        p = self.to_acs(1)
        self.approve(p, "LIB-001")
        r = checks.status_report(p)
        self.assertEqual(r["acceptance"]["approved"], 1)
        self.assertEqual(r["requirement_status"], {"READY": 9})
        self.assertTrue(r["enriched"]["in_sync"])
        text = checks.status_text(r)
        self.assertIn("Acceptance criteria: 1 approved", text)
        self.assertIn("Enriched copies: in step", text)
        self.assertIn("NEXT:", text)
        json.dumps(r)  # serialisable


if __name__ == "__main__":
    unittest.main()
