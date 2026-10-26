"""State machine, decision-owner gate, IST closure audit, conflict handling, acceptance-criteria approval rules."""
import json
import unittest

from tests.helpers import BATest, acs, checks, gapmod, key, projmod, util
from ba_lib.util import BAError


def code_of(fn, *a, **k):
    try:
        fn(*a, **k)
    except BAError as e:
        return e.code
    return None


class TransitionTableTests(BATest):
    def test_stage_transitions_enforced(self):
        p = self.new_project()
        self.assertEqual(p.state["stage"], "SOURCE_INDEXED")
        with self.assertRaises(BAError) as c:
            p.set_stage("COMPLETED", "x")
        self.assertEqual(c.exception.code, "INVALID_TRANSITION")
        with self.assertRaises(BAError):
            p.set_stage("AC_DRAFTING", "x")  # cannot skip gap analysis
        with self.assertRaises(BAError):
            p.set_stage("WAITING_FOR_AC_REVIEW", "x")

    def test_new_project_starts_at_source_indexed_with_the_read_only_index(self):
        p = self.new_project()
        self.assertEqual(p.state["schema_version"], "2.0")
        stages = [e for e in p.audit_events() if e["event_type"] == "STAGE_CHANGED"]
        self.assertEqual([(e["from_stage"], e["to_stage"]) for e in stages], [("INITIALIZED", "SOURCE_INDEXED")])
        self.assertEqual(len(p.requirements()), 9)

    def test_the_whole_stage_path_of_the_v2_state_machine(self):
        p = self.with_gaps()
        self.assertEqual(p.state["stage"], "GAP_ANALYSIS", "adding gaps starts gap analysis")
        self.set_owner(p)
        gapmod.next_gaps(p, 10)
        self.assertEqual(p.state["stage"], "WAITING_FOR_GAP_INPUT")
        self.resolve_all(p)
        gapmod.commit_analysis(p, True)
        self.assertEqual(p.state["stage"], "WAITING_FOR_GAP_INPUT", "committing does not skip ahead; drafting criteria does")
        acs.add_acs(p, [self.ac_item("LIB-001")])
        self.assertEqual(p.state["stage"], "AC_DRAFTING")
        acs.next_ac(p)
        self.assertEqual(p.state["stage"], "WAITING_FOR_AC_REVIEW")
        self.approve(p, "LIB-001")
        out = checks.complete_workflow(p)
        self.assertFalse(out["complete"], "the other requirements still have no criteria")
        self.assertEqual(p.state["stage"], "WAITING_FOR_AC_REVIEW")
        acs.add_acs(p, [self.ac_item(r) for r in ("LIB-002", "LIB-003", "LIB-004", "LIB-005", "LIB-006", "LIB-007", "LIB-008", "LIB-009")])
        for e in p.requirements():
            if e["id"] != "LIB-001":
                acs.approve(p, e["req_key"], 1, "approve", "Priya Nair", "Product Owner")
        self.assertTrue(checks.complete_workflow(p)["complete"])
        self.assertEqual(p.state["stage"], "COMPLETED")
        # a new gap on an approved requirement reopens the workflow to gap input
        gapmod.add_gaps(p, [dict(self.gap_items()[1], title="Extra", question="What extra rule applies?", impacted_req_keys=["LIB-002"], priority="LOW")])
        self.assertEqual(p.state["stage"], "WAITING_FOR_GAP_INPUT")

    def test_stage_table_has_no_story_or_baseline_stages(self):
        self.assertEqual(projmod.STAGES, ["INITIALIZED", "SOURCE_INDEXED", "GAP_ANALYSIS", "WAITING_FOR_GAP_INPUT", "AC_DRAFTING",
                                         "WAITING_FOR_AC_REVIEW", "COMPLETED", "BLOCKED", "PAUSED"])
        self.assertEqual(projmod.STAGE_TRANSITIONS["SOURCE_INDEXED"], {"GAP_ANALYSIS"})
        self.assertIn("AC_DRAFTING", projmod.STAGE_TRANSITIONS["WAITING_FOR_GAP_INPUT"])
        self.assertTrue({"GAP_ANALYSIS", "WAITING_FOR_GAP_INPUT"} <= projmod.STAGE_TRANSITIONS["COMPLETED"], "later work reopens to gap input")

    def test_pause_resumes_only_to_saved_stage(self):
        p = self.with_requirements()
        p.set_stage("PAUSED", "pause")
        self.assertEqual(p.state["resume_stage"], "SOURCE_INDEXED")
        with self.assertRaises(BAError):
            p.set_stage("GAP_ANALYSIS", "x")
        p.set_stage("SOURCE_INDEXED", "resume")
        self.assertEqual(p.state["stage"], "SOURCE_INDEXED")

    def test_gap_cannot_resolve_without_validated_resolution(self):
        p = self.with_gaps()
        self.set_owner(p)
        # still OPEN: not presented
        self.assertEqual(code_of(gapmod.resolve, p, "GAP-001", "A1", "x y", "Priya Nair", "Product Owner", "s", "c"), "NOT_PRESENTED")
        gapmod.next_gaps(p, 1)
        # no answer recorded yet
        self.assertEqual(code_of(gapmod.resolve, p, "GAP-001", "A1", "interp text", "Priya Nair", "Product Owner", "summary", "confirm"), "UNKNOWN_ANSWER")
        self.assertEqual(p.gap("GAP-001")["status"], "WAITING_FOR_ANSWER")

    def test_gap_status_table(self):
        self.assertIn("RESOLVED", projmod.GAP_TRANSITIONS["WAITING_FOR_ANSWER"])
        self.assertNotIn("RESOLVED", projmod.GAP_TRANSITIONS["OPEN"])
        self.assertNotIn("RESOLVED", projmod.GAP_TRANSITIONS["DEFERRED"])
        self.assertEqual(projmod.GAP_TRANSITIONS["RESOLVED"], {"REOPENED"})

    def test_ac_status_table(self):
        self.assertEqual(projmod.AC_TRANSITIONS["APPROVED"], {"SUPERSEDED"})
        self.assertEqual(projmod.AC_TRANSITIONS["SUPERSEDED"], set())
        self.assertIn("APPROVED", projmod.AC_TRANSITIONS["IN_REVIEW"])
        self.assertNotIn("APPROVED", projmod.AC_TRANSITIONS["CHANGES_REQUESTED"])
        self.assertNotIn("APPROVED", projmod.AC_TRANSITIONS["DEFERRED"])

    def test_ac_cannot_be_approved_from_deferred_or_without_evidence(self):
        p = self.to_acs(2)
        self.assertEqual(code_of(acs.approve, p, "LIB-001", 1, ""), "APPROVAL_EVIDENCE_REQUIRED")
        self.assertEqual(code_of(acs.approve, p, "LIB-001", 2, "approve"), "VERSION_MISMATCH")
        acs.defer(p, "LIB-001", 1, "later")
        self.assertEqual(code_of(acs.approve, p, "LIB-001", 1, "approve"), "AC_DEFERRED")

    def test_criteria_cannot_be_written_before_analysis_or_for_unknown_requirements(self):
        p = self.with_gaps()
        self.assertEqual(code_of(acs.add_acs, p, [self.ac_item("LIB-001")]), "ANALYSIS_INCOMPLETE")
        self.to_ac_stage(p)
        self.assertEqual(code_of(acs.add_acs, p, [dict(self.ac_item("LIB-001"), req_key="LIB-404")]), "ACS_REJECTED")
        self.assertEqual(code_of(acs.add_acs, p, [self.ac_item("LIB-001", acceptance_criteria=["The member can register."])]), "ACS_REJECTED")


class LegacyProjectTests(BATest):
    def test_a_1x_project_is_not_opened_by_2x(self):
        p = self.new_project()
        st = p.read_json("state/workflow.json")
        st["schema_version"] = "1.0"
        st["stage"] = "REQUIREMENTS_BASELINED"
        p.path("state/workflow.json").write_text(json.dumps(st), encoding="utf-8")
        with self.assertRaises(BAError) as c:
            projmod.Project.open(p.root)
        self.assertEqual(c.exception.code, "LEGACY_PROJECT")
        self.assertEqual((c.exception.details["stage"], c.exception.details["schema_version"]), ("REQUIREMENTS_BASELINED", "1.0"))
        self.assertIn("1.10.2", c.exception.message)

    def test_status_of_a_legacy_project_stays_read_only_and_explains(self):
        import subprocess
        import sys
        from tests.helpers import PLUGIN
        p = self.new_project()
        st = p.read_json("state/workflow.json")
        st["schema_version"] = "1.0"
        p.path("state/workflow.json").write_text(json.dumps(st), encoding="utf-8")
        before = p.path("state/workflow.json").read_bytes()
        r = subprocess.run([sys.executable, str(PLUGIN / "scripts" / "ba.py"), "--root", str(p.root), "status"], capture_output=True, text=True,
                           encoding="utf-8")
        out = json.loads(r.stdout)
        self.assertEqual((out["ok"], out["legacy_project"]), (True, True))
        self.assertEqual(p.path("state/workflow.json").read_bytes(), before, "nothing was written")


class UnapprovedAcTests(BATest):
    """'Not approved' records are saved in their own folder, never in approved/."""

    def test_not_approved_record_is_saved_in_unapproved_folder(self):
        p = self.to_acs(2)
        acs.next_ac(p)
        out = acs.request_changes(p, "LIB-001", 1, "criteria too vague")
        name = projmod.ac_fname(key("LIB-001"))
        self.assertEqual(out["location"], f"acceptance/unapproved/md/{name}.md")
        self.assertTrue(p.exists(f"acceptance/unapproved/json/{name}.json") and p.exists(f"acceptance/unapproved/md/{name}.md"))
        self.assertFalse(p.exists(f"acceptance/candidates/json/{name}.json"))
        self.assertFalse(p.exists(f"acceptance/approved/json/{name}.json"))
        self.assertEqual(code_of(acs.approve, p, "LIB-001", 1, "approve"), "AC_UNAPPROVED")
        self.assertIn(key("LIB-001"), p.state["pending_ac_keys"])
        self.assertEqual(checks.validate_project(p)["errors"], [])
        self.assertNotIn(key("LIB-001"), [x["req_key"] for x in acs.next_ac(p, batch=10)["acs"]])
        self.assertEqual([x["req_key"] for x in acs.next_ac(p, unapproved=True)["acs"]], [key("LIB-001")])
        self.assertNotIn("Approved by", self.enriched(p, "membership.md"), "nothing unapproved is rendered into the document")

    def test_editing_unapproved_record_makes_a_new_candidate_version(self):
        p = self.to_acs(2)
        acs.next_ac(p)
        acs.request_changes(p, "LIB-001", 1, "reword")
        crit = ["Given a visitor with a valid email, when they register, then the member account exists."]
        out = acs.edit(p, "LIB-001", {"acceptance_criteria": crit}, "reword")
        name = projmod.ac_fname(key("LIB-001"))
        self.assertEqual(out["new_version"], 2)
        self.assertFalse(p.exists(f"acceptance/unapproved/json/{name}.json"))
        self.assertTrue(p.exists(f"acceptance/candidates/json/{name}.json"))
        self.assertTrue(p.exists(f"acceptance/archive/json/{name}.v1.json"))


class DecisionOwnerTests(BATest):
    """Tests 16: owner prompt before first gap."""

    def test_gap_triage_blocked_until_owner_identified(self):
        p = self.with_gaps()
        with self.assertRaises(BAError) as c:
            gapmod.next_gaps(p, 3)
        self.assertEqual(c.exception.code, "OWNER_REQUIRED")
        self.assertIn("name and role", c.exception.message)
        self.assertTrue(all(g["status"] == "OPEN" for g in p.gaps()), "no gap may be presented before the owner is known")
        self.set_owner(p)
        out = gapmod.next_gaps(p, 3)
        self.assertEqual(len(out["gaps"]), 3)
        self.assertEqual(p.state["decision_owner"]["name"], "Priya Nair")
        self.assertNotIn("decision_owner", out["gaps"][0])

    def test_placeholder_or_missing_identity_rejected(self):
        p = self.with_gaps()
        for name, role in (("", "PO"), ("Claude", "PO"), ("unknown", "PO"), ("Priya Nair", ""), ("Priya Nair", "tbd")):
            self.assertEqual(code_of(gapmod.set_owner, p, name, role), "IDENTITY_REQUIRED", (name, role))

    def test_owner_unknown_pause_blocks_triage_and_resumes_when_identified(self):
        p = self.with_gaps()
        gapmod.owner_unknown(p, "pause")
        self.assertEqual(p.state["stage"], "PAUSED")
        self.assertEqual(code_of(gapmod.next_gaps, p, 1), "OWNER_UNKNOWN")
        gapmod.set_owner(p, "Priya Nair", "Product Owner")
        self.assertNotEqual(p.state["stage"], "PAUSED")
        self.assertEqual(len(gapmod.next_gaps(p, 1)["gaps"]), 1)

    def test_analysis_only_mode_still_blocks_triage(self):
        p = self.with_gaps()
        gapmod.owner_unknown(p, "analysis-only")
        self.assertEqual(code_of(gapmod.next_gaps, p, 1), "OWNER_UNKNOWN")

    def test_owner_change_is_audited_and_history_kept(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.set_owner(p, "Arun Rao", "Head of Library")
        self.assertEqual(p.state["decision_owner"]["name"], "Arun Rao")
        self.assertEqual(p.state["decision_owner_history"][0]["name"], "Priya Nair")
        types = [e["event_type"] for e in p.audit_events()]
        self.assertIn("DECISION_OWNER_SET", types)
        self.assertIn("DECISION_OWNER_CHANGED", types)

    def test_per_gap_override_and_resolved_gap_owner_not_rewritten(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.set_owner(p, "Meera Iyer", "Security Lead", gap_id="GAP-003")
        self.assertEqual(p.gap("GAP-003")["decision_owner_name"], "Meera Iyer")
        self.assertEqual(p.gap("GAP-001")["decision_owner_name"], "Priya Nair")
        gapmod.next_gaps(p, 3)
        self.answer_and_resolve(p, "GAP-001", "R10/day max R200", "Fine is R10 per day, max R200.")
        gapmod.set_owner(p, "Arun Rao", "Head of Library")
        self.assertEqual(p.gap("GAP-001")["decision_owner_name"], "Priya Nair", "history must not be rewritten")
        self.assertEqual(p.gap("GAP-003")["decision_owner_name"], "Meera Iyer")


class ClosureAuditTests(BATest):
    """Tests 17, 18: closure metadata + exact IST timestamp; blocked when identity is missing."""

    def test_closure_records_everything_with_exact_ist(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        out = self.answer_and_resolve(p, "GAP-003", "Block it and show a message", "A 6th borrow is rejected with a limit message.",
                                      closer=("Sam Dsouza", "Business Analyst"), respondent_name="Priya Nair", respondent_role="Product Owner",
                                      confirmation="Yes, close it.")
        g = p.gap("GAP-003")
        self.assertEqual(g["status"], "RESOLVED")
        self.assertRegex(g["closed_at_ist"], r"^2026-10-22T18:4\d:\d\d\+05:30$")
        self.assertEqual((g["closed_by_name"], g["closed_by_role"]), ("Sam Dsouza", "Business Analyst"))
        self.assertEqual((g["decision_owner_name"], g["decision_owner_role"]), ("Priya Nair", "Product Owner"))
        self.assertEqual((g["respondent_name"], g["respondent_role"]), ("Priya Nair", "Product Owner"))
        self.assertEqual(g["decision_id"], out["decision_id"])
        self.assertEqual(g["closure_validation"]["status"], "PASSED")
        self.assertRegex(g["closure_validation"]["validated_at_ist"], r"\+05:30$")
        ev = [e for e in p.audit_events() if e["event_type"] == "GAP_RESOLVED"][0]
        self.assertEqual(ev["closed_by"], {"name": "Sam Dsouza", "role": "Business Analyst"})
        self.assertEqual(ev["decision_owner"], {"name": "Priya Nair", "role": "Product Owner"})
        self.assertEqual(ev["respondent"], {"name": "Priya Nair", "role": "Product Owner"})
        self.assertEqual(ev["timezone"], "Asia/Kolkata")
        self.assertEqual(ev["closed_at_ist"], g["closed_at_ist"])
        self.assertEqual(ev["conflict_check"], {"status": "PASSED", "conflicting_artifact_ids": []})
        self.assertNotEqual(ev["response_received_at_ist"], ev["closed_at_ist"])
        d = p.decisions()[g["decision_id"]]
        self.assertEqual(d["human_response"], "Block it and show a message")
        self.assertEqual(d["validated_interpretation"], "A 6th borrow is rejected with a limit message.")
        self.assertTrue(p.exists(f"decisions/{g['decision_id']}.md"))
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])

    def test_closure_blocked_without_closer_identity_gap_stays_pending(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        a = gapmod.record_answer(p, "GAP-003", "Block it")
        for name, role in (("", "BA"), ("Sam", ""), ("assistant", "BA"), ("Sam Dsouza", "n/a")):
            self.assertEqual(code_of(gapmod.resolve, p, "GAP-003", a["answer_id"], "Rejected with message.", name, role, "summary", "confirm"),
                             "IDENTITY_REQUIRED", (name, role))
        self.assertEqual(code_of(gapmod.resolve, p, "GAP-003", a["answer_id"], "Rejected with message.", "Sam Dsouza", "BA", "summary", ""), "MISSING_METADATA")
        self.assertEqual(code_of(gapmod.resolve, p, "GAP-003", a["answer_id"], "", "Sam Dsouza", "BA", "summary", "ok"), "MISSING_METADATA")
        self.assertEqual(p.gap("GAP-003")["status"], "WAITING_FOR_ANSWER")
        self.assertEqual([e for e in p.audit_events() if e["event_type"] == "GAP_RESOLVED"], [])
        self.assertEqual(p.decisions(), {})

    def test_material_gap_needs_owner_confirmation_when_closer_is_not_owner(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        a = gapmod.record_answer(p, "GAP-001", "R10 per day, max R200")
        args = (p, "GAP-001", a["answer_id"], "Fine R10 per day, max R200.", "Sam Dsouza", "Business Analyst", "Answer is specific.", "Close it.")
        self.assertEqual(code_of(gapmod.resolve, *args), "AWAITING_OWNER_CONFIRMATION")
        self.assertEqual(p.gap("GAP-001")["status"], "WAITING_FOR_ANSWER")
        self.assertIn("GAP_AWAITING_OWNER_CONFIRMATION", [e["event_type"] for e in p.audit_events()])
        out = gapmod.resolve(*args, owner_confirmation="I (Priya) confirm R10/day max R200.")
        d = p.decisions()[out["decision_id"]]
        self.assertEqual(d["confirmed_by_name"], "Priya Nair")
        self.assertEqual(d["confirmed_by_role"], "Product Owner")

    def test_owner_who_is_closer_needs_no_extra_confirmation(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        out = self.answer_and_resolve(p, "GAP-001", "R10/day max R200", "Fine is R10/day, max R200.")
        self.assertEqual(p.gap("GAP-001")["status"], "RESOLVED")
        self.assertEqual(p.decisions()[out["decision_id"]]["confirmed_by_name"], "Priya Nair")


class ConflictTests(BATest):
    """Tests 19, 20: answers that contradict the requirements / earlier decisions; a confirmed change keeps the history and never edits the source."""

    def test_answer_contradicting_requirement_cannot_be_closed_as_passed(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        a = gapmod.record_answer(p, "GAP-003", "Members can borrow at most 8 books at once; block the 9th.")
        self.assertTrue(any(c["artifact_id"] == "LIB-003" for c in a["candidate_conflicts"]), a)
        with self.assertRaises(BAError) as c:
            gapmod.resolve(p, "GAP-003", a["answer_id"], "Members can borrow at most 8 books at once.", "Priya Nair", "Product Owner", "ok", "close")
        self.assertEqual(c.exception.code, "CONFLICT_REVIEW_REQUIRED")
        self.assertIn("LIB-003", c.exception.message)
        self.assertEqual(p.gap("GAP-003")["status"], "WAITING_FOR_ANSWER")
        # the model/user confirms it IS a conflict -> recorded, raw answer preserved, still cannot pass
        gapmod.record_conflict(p, "GAP-003", a["answer_id"], ["LIB-003"], "Answer says 8 books; LIB-003 says 5.")
        self.assertEqual(code_of(gapmod.resolve, p, "GAP-003", a["answer_id"], "At most 8 books.", "Priya Nair", "Product Owner", "ok", "close",
                                 reviewed_ids=["LIB-003"]), "CONFLICT_UNRESOLVED")
        self.assertEqual(p.gap("GAP-003")["answers"][0]["response"], "Members can borrow at most 8 books at once; block the 9th.")
        self.assertIn("CONFLICT_DETECTED", [e["event_type"] for e in p.audit_events()])

    def test_reviewed_compatible_candidate_can_pass(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        a = gapmod.record_answer(p, "GAP-003", "When a member tries to borrow a 6th book, reject it with a message.")
        cands = [c["artifact_id"] for c in a["candidate_conflicts"]]
        out = gapmod.resolve(p, "GAP-003", a["answer_id"], "A 6th borrow is rejected with a message.", "Priya Nair", "Product Owner",
                             "Adds error path only.", "Yes", reviewed_ids=cands)
        self.assertEqual(out["status"], "RESOLVED")

    def test_confirmed_change_against_a_requirement_flags_approved_criteria_and_leaves_the_source_alone(self):
        p = self.to_ac_stage()
        before = {f.name: f.read_bytes() for f in self.src.iterdir() if f.is_file()}
        acs.add_acs(p, [self.ac_item("LIB-003", acceptance_criteria=[
            "Given a member holding 5 books, when they borrow a 6th, then the borrow is rejected with a limit message."])])
        acs.approve(p, "LIB-003", 1, "Approved.", "Priya Nair", "Product Owner")
        # reopen flow: new gap about the limit, answer changes 5 -> 8
        item = dict(self.gap_items()[2], title="Limit review", question="Should the borrow limit stay at 5?", priority="CRITICAL",
                    priority_rationale="Alters a core business rule.", impacted_req_keys=["LIB-003"])
        gapmod.add_gaps(p, [item])
        self.assertEqual(p.state["stage"], "WAITING_FOR_GAP_INPUT")
        gapmod.next_gaps(p, 1)
        a = gapmod.record_answer(p, "GAP-004", "No, raise it to 8 books.")
        with self.assertRaises(BAError):  # a conflict with LIB-003 that nobody confirmed
            gapmod.resolve(p, "GAP-004", a["answer_id"], "Limit is 8 books.", "Priya Nair", "Product Owner", "Changed limit", "yes")
        self.assertEqual(code_of(gapmod.resolve, p, "GAP-004", a["answer_id"], "Limit is 8 books.", "Priya Nair", "Product Owner", "Changed", "yes",
                                 conflict_status="CHANGE_CONFIRMED", conflicting_ids=["LIB-003"]), "IDENTITY_REQUIRED",
                         "the person authorising the change must be named")
        out = gapmod.resolve(p, "GAP-004", a["answer_id"], "Limit is 8 books.", "Priya Nair", "Product Owner", "Owner authorised a limit change.",
                             "Yes, close it.", conflict_status="CHANGE_CONFIRMED", conflicting_ids=["LIB-003"], change_by_name="Priya Nair",
                             change_by_role="Product Owner", change_statement="Yes, change the limit to 8.")
        self.assertEqual(out["upstream_change_required"], [key("LIB-003")])
        d = p.decisions()[out["decision_id"]]
        self.assertEqual(d["validation_status"], "CHANGE_CONFIRMED")
        self.assertEqual(d["change_authorization"]["name"], "Priya Nair")
        self.assertIn(key("LIB-003"), d["related_artifact_ids"])
        self.assertEqual({f.name: f.read_bytes() for f in self.src.iterdir() if f.is_file()}, before, "the supplied files are never edited")
        approved = p.read_json(f"acceptance/approved/{projmod.ac_fname(key('LIB-003'))}.json")
        self.assertTrue(approved["impact_review"]["pending"], "approved criteria linked to the contradicted requirement must be flagged")
        self.assertIn("Confirmed change", approved["impact_review"]["reason"])
        self.assertIn(key("LIB-003"), checks.status_report(p)["acceptance"]["flagged_for_impact_review"])
        self.assertEqual([e["req_keys"] for e in p.audit_events() if e["event_type"] == "UPSTREAM_CHANGE_REQUIRED"], [[key("LIB-003")]])

    def test_change_to_prior_decision_supersedes_and_reopens_gap(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        first = self.answer_and_resolve(p, "GAP-003", "Reject the 6th borrow", "A 6th borrow is rejected with a message.")
        gapmod.add_gaps(p, [dict(self.gap_items()[2], title="Over limit review", question="Should the 6th borrow go to a waiting list instead?",
                                 priority="HIGH", priority_rationale="Alters earlier decision.", impacted_req_keys=["LIB-003"])])
        gapmod.next_gaps(p, 5)
        a = gapmod.record_answer(p, "GAP-004", "Instead of rejecting, put the member on a waiting list.")
        out = gapmod.resolve(p, "GAP-004", a["answer_id"], "6th borrow request is placed on a waiting list.", "Priya Nair", "Product Owner",
                             "Supersedes the rejection decision.", "Yes.", conflict_status="CHANGE_CONFIRMED",
                             conflicting_ids=[first["decision_id"]], change_by_name="Priya Nair", change_by_role="Product Owner",
                             change_statement="Yes, supersede it.")
        self.assertEqual(p.decisions()[first["decision_id"]]["current_status"], "SUPERSEDED")
        self.assertEqual(p.decisions()[out["decision_id"]]["supersedes_decision_id"], first["decision_id"])
        g3 = p.gap("GAP-003")
        self.assertEqual(g3["status"], "REOPENED")
        self.assertEqual(g3["closure_history"][0]["decision_id"], first["decision_id"], "closure preserved in history")
        self.assertIsNone(g3["closed_at_ist"])
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])


if __name__ == "__main__":
    unittest.main()
