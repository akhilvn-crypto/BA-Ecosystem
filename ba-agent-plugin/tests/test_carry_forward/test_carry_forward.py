"""Medium/low gap workflow: Suggested Answer / Other / Explain This Question / Carry Forward, story traceability and readiness,
blocking rules for critical/material gaps, and revisit-and-update when a carried-forward gap is finally resolved."""
import json
import subprocess
import sys
from pathlib import Path

from tests.helpers import BATest, basemod, checks, gapmod, stories
from ba_lib.project import Project
from ba_lib.util import BAError

OWNER = ("Priya Nair", "Product Owner")
BA = str(Path(__file__).resolve().parents[2] / "scripts" / "ba.py")


def code_of(fn, *a, **k):
    try:
        fn(*a, **k)
    except BAError as e:
        return e.code
    return None


class Base(BATest):
    """GAP-001 HIGH+material (REQ-006), GAP-002 MEDIUM (REQ-008), GAP-003 HIGH (REQ-003)."""

    def presented(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 10)
        return p

    def carry(self, p, gid="GAP-002", reason="Performance target comes from the infra team later."):
        return gapmod.carry_forward(p, gid, *OWNER, reason)

    def carried_baseline(self, approve=True):
        """GAP-001/003 resolved, GAP-002 carried forward, baseline v1 approved."""
        p = self.presented()
        self.answer_and_resolve(p, "GAP-001", "R10 per day late, capped at R200.", "Fine is R10 per day late, maximum R200 per loan.")
        self.answer_and_resolve(p, "GAP-003", "Block the borrow and show a limit-reached message.",
                                "A 6th concurrent borrow is rejected with a limit-reached message.")
        self.carry(p)
        gapmod.commit_analysis(p, True)
        basemod.create(p, "initial")
        if approve:
            basemod.confirm(p, 1, *OWNER, "I approve baseline v1.")
        return p

    def speed_story(self, **kw):
        d = self.story_item("Pages load quickly on mobile", ("REQ-008",),
                            user_story=dict(role="Member", capability="to load library pages quickly on my phone",
                                            value="I can browse the catalogue without waiting"),
                            acceptance_criteria=["Given a member on a mobile device, when a catalogue page is requested, "
                                                 "then the page loads within the target agreed in the BRD."],
                            unresolved_decisions=[dict(gap_id="GAP-002", readiness_effect="NEEDS_CLARIFICATION",
                                                       potential_impact="The page-load target (seconds) and network are undecided, so "
                                                                        "the pass/fail threshold of the load-time test may change.")])
        d.update(kw)
        return d

    def audit(self, p, event_type):
        return [e for e in p.audit_events() if e["event_type"] == event_type]

    def refresh_baseline(self, p):
        """After a carried gap is resolved the baseline is stale: recreate it and have the human confirm it."""
        gapmod.commit_analysis(p, True)
        out = basemod.create(p, "refresh after gap resolution")
        basemod.confirm(p, out["version"], *OWNER, f"I approve baseline v{out['version']}.")
        return out


class EligibilityTests(Base):
    def test_only_non_material_medium_and_low_gaps_can_be_carried_forward(self):
        p = self.presented()
        for gid in ("GAP-001", "GAP-003"):  # HIGH
            self.assertEqual(code_of(gapmod.carry_forward, p, gid, *OWNER), "CARRY_FORWARD_NOT_ALLOWED", gid)
        gaps = p.gaps()
        gaps[1]["material"] = True  # MEDIUM but compliance/security sensitive
        p.save_gaps(gaps, touched=["GAP-002"])
        self.assertEqual(code_of(gapmod.carry_forward, p, "GAP-002", *OWNER), "CARRY_FORWARD_NOT_ALLOWED")
        gaps[1]["material"] = False
        p.save_gaps(gaps, touched=["GAP-002"])
        self.assertIsNone(code_of(gapmod.carry_forward, p, "GAP-002", *OWNER))
        self.assertEqual([g["status"] for g in p.gaps()], ["WAITING_FOR_ANSWER", "OPEN", "WAITING_FOR_ANSWER"])

    def test_critical_and_security_gaps_are_refused(self):
        p = self.with_requirements()
        items = self.gap_items()[:2]
        items[0].pop("material")  # a security gap defaults to material
        items[0].update(title="Auth model unclear", category="Security and access", priority="MEDIUM",
                        question="Who may see the overdue report?", impacted_requirement_ids=["REQ-007"])
        items[1].update(priority="CRITICAL", question="What happens to data on failure?", title="Data loss")
        gapmod.add_gaps(p, items)
        self.set_owner(p)
        gapmod.next_gaps(p, 5)
        self.assertTrue(p.gap("GAP-001")["material"])
        self.assertEqual(code_of(gapmod.carry_forward, p, "GAP-001", *OWNER), "CARRY_FORWARD_NOT_ALLOWED")
        self.assertEqual(code_of(gapmod.carry_forward, p, "GAP-002", *OWNER), "CARRY_FORWARD_NOT_ALLOWED")
        self.assertEqual(self.audit(p, "GAP_CARRIED_FORWARD"), [])

    def test_requires_decision_owner_and_a_named_actor(self):
        p = self.with_gaps()
        self.assertEqual(code_of(gapmod.carry_forward, p, "GAP-002", *OWNER), "OWNER_REQUIRED")
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        self.assertEqual(code_of(gapmod.carry_forward, p, "GAP-002"), "IDENTITY_REQUIRED")
        self.assertEqual(code_of(gapmod.carry_forward, p, "GAP-002", "Claude", "AI"), "IDENTITY_REQUIRED")
        self.assertEqual(p.gap("GAP-002")["status"], "WAITING_FOR_ANSWER", "refused attempts change nothing")

    def test_cannot_carry_forward_twice_or_a_resolved_gap(self):
        p = self.presented()
        self.carry(p)
        self.assertEqual(code_of(gapmod.carry_forward, p, "GAP-002", *OWNER), "ALREADY_CARRIED_FORWARD")
        self.answer_and_resolve(p, "GAP-003", "Reject.", "A 6th borrow is rejected.")
        self.assertEqual(code_of(gapmod.carry_forward, p, "GAP-003", *OWNER), "INVALID_TRANSITION")


class CarryForwardStateTests(Base):
    def test_gap_stays_open_with_a_full_audit_record(self):
        p = self.presented()
        out = self.carry(p, reason="  Waiting for   infra team  ")
        g = Project.open(p.root).gap("GAP-002")
        cf = g["carry_forward"]
        self.assertEqual((g["status"], g["decision_id"], g["resolution"], g["closed_at_ist"]), ("OPEN", None, None, None))
        self.assertEqual((cf["by_name"], cf["by_role"], cf["reason"], cf["priority"]), (*OWNER, "Waiting for infra team", "MEDIUM"))
        self.assertEqual((cf["decision_owner_name"], cf["decision_owner_role"], cf["timezone"]), (*OWNER, "Asia/Kolkata"))
        self.assertRegex(cf["at_ist"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+05:30$")
        self.assertEqual(g["skipped_count"], 0, "carry forward is not 'answer later'")
        ev = self.audit(p, "GAP_CARRIED_FORWARD")[0]
        self.assertEqual((ev["actor"]["name"], ev["decision_owner"]["name"], ev["gap_id"], ev["requirement_ids"]), (OWNER[0], OWNER[0], "GAP-002", ["REQ-008"]))
        self.assertEqual(g["history"][-1]["event_id"], ev["event_id"])
        self.assertFalse(out["blocks_stories"])
        self.assertIn("still OPEN", out["note"])
        self.assertIn("GAP-002", p.state["pending_gap_ids"], "still outstanding")
        self.assertEqual(checks.status_report(p)["gaps"]["carried_forward"], ["GAP-002"])
        self.assertIn("CARRIED FORWARD", (p.root / "gaps/md/GAP-002.md").read_text(encoding="utf-8"))
        self.assertTrue(checks.validate_project(p)["ok"])

    def test_carried_gap_no_longer_blocks_but_an_uncarried_medium_gap_still_does(self):
        p = self.presented()
        self.assertTrue(gapmod.blocks_requirements(p, p.gap("GAP-002")), "strict default: open MEDIUM gap blocks")
        self.carry(p)
        self.assertFalse(gapmod.blocks_requirements(p, p.gap("GAP-002")))
        self.assertNotIn("REQ-008", gapmod.blocked_requirements(p))
        self.assertIn("REQ-006", gapmod.blocked_requirements(p), "HIGH gaps keep blocking")
        self.assertIn("REQ-003", gapmod.blocked_requirements(p))

    def test_existing_blocking_rules_return_if_a_carried_gap_becomes_material_or_policy_blocks_its_priority(self):
        p = self.presented()
        self.carry(p)
        gaps = p.gaps()
        gaps[1]["material"] = True
        p.save_gaps(gaps, touched=["GAP-002"])
        self.assertTrue(gapmod.blocks_requirements(p, p.gap("GAP-002")))
        self.assertTrue(any("no longer eligible" in w for w in checks.validate_project(p)["warnings"]))
        gaps[1]["material"] = False
        p.save_gaps(gaps, touched=["GAP-002"])
        p.state["policy"]["blocking_priorities"] = ["CRITICAL", "HIGH", "MEDIUM"]
        self.assertTrue(gapmod.blocks_requirements(p, p.gap("GAP-002")))

    def test_carried_gap_is_not_asked_again_until_revisited(self):
        p = self.presented()
        self.carry(p)
        out = gapmod.next_gaps(p, 10)
        self.assertEqual([g["gap_id"] for g in out["gaps"]], ["GAP-001", "GAP-003"], "the carried gap is not asked again")
        self.assertEqual([c["gap_id"] for c in out["carried_forward_open"]], ["GAP-002"])
        self.assertEqual(code_of(gapmod.answer_later, p, "GAP-002"), "ALREADY_CARRIED_FORWARD")
        self.assertIn("2 gap(s) await answers", checks.next_action(p), "the carried gap is not counted as awaiting an answer")

    def test_revisit_asks_the_gap_again_and_keeps_the_carry_forward_history(self):
        p = self.presented()
        self.carry(p)
        out = gapmod.next_gaps(p, 1, carried=True)
        self.assertEqual([g["gap_id"] for g in out["gaps"]], ["GAP-002"])
        g = p.gap("GAP-002")
        self.assertEqual((g["status"], g["carry_forward"]), ("WAITING_FOR_ANSWER", None))
        self.assertEqual(len(g["carry_forward_history"]), 1)
        self.assertEqual(g["carry_forward_history"][0]["by_name"], OWNER[0])
        self.assertTrue(gapmod.blocks_requirements(p, g), "while it is being asked again it blocks like any open gap")
        self.assertEqual(len(self.audit(p, "GAP_CARRY_FORWARD_REVISITED")), 1)
        self.assertEqual(len(self.audit(p, "GAP_CARRIED_FORWARD")), 1, "earlier audit events are never rewritten")
        self.carry(p)  # choose to carry it again
        self.assertEqual(len(p.gap("GAP-002")["carry_forward_history"]), 1)
        self.assertEqual(len(self.audit(p, "GAP_CARRIED_FORWARD")), 2)
        gapmod.revisit(p, "GAP-002")  # `gap revisit` works for a single carried gap too
        self.assertEqual(p.gap("GAP-002")["status"], "WAITING_FOR_ANSWER")
        self.assertEqual(code_of(gapmod.revisit, p, "GAP-001"), "INVALID_TRANSITION")

    def test_deferring_a_carried_gap_ends_the_carry_forward(self):
        p = self.presented()
        self.carry(p)
        gapmod.defer(p, "GAP-002", "Parked by the steering group", by_name=OWNER[0], by_role=OWNER[1])
        g = p.gap("GAP-002")
        self.assertEqual((g["status"], g["carry_forward"]), ("DEFERRED", None))
        self.assertEqual(len(g["carry_forward_history"]), 1)
        self.assertTrue(gapmod.blocks_requirements(p, g), "deferred is not carried forward")
        self.assertTrue(checks.validate_project(p)["ok"])

    def test_interrupted_carry_forward_is_repaired_by_recover(self):
        p = self.presented()

        def boom(point):
            if point == "after_gap_status_saved":
                raise RuntimeError("crash")
        from ba_lib import project as projmod
        projmod.FAULT_HOOK = boom
        with self.assertRaises(RuntimeError):
            self.carry(p)
        projmod.FAULT_HOOK = None
        p2 = Project.open(p.root)
        self.assertEqual(self.audit(p2, "GAP_CARRIED_FORWARD"), [])
        self.assertTrue(any("missing" in w for w in checks.validate_project(p2)["warnings"]))
        out = checks.recover(p2)
        ev = self.audit(p2, "GAP_CARRIED_FORWARD")[0]
        self.assertEqual((ev["actor"]["name"], ev["reason"], ev["recovered"]), (OWNER[0], "Performance target comes from the infra team later.", True))
        self.assertEqual(len(out["audit_backfilled"]), 1)


class SuggestedAnswerAndOtherTests(Base):
    def test_suggested_answer_is_an_option_only_for_carry_forward_eligible_gaps_with_evidence(self):
        p = self.with_requirements()
        items = self.gap_items()
        items[0]["suggested_answer"] = {"answer": "R10 per day, max R200", "source": "BRD 3.2 and REQ-006"}
        items[1]["suggested_answer"] = {"answer": "Under 3 seconds on 4G", "source": "BRD 5 and REQ-008"}
        gapmod.add_gaps(p, items)
        self.set_owner(p)
        by_id = {g["gap_id"]: g for g in gapmod.next_gaps(p, 3)["gaps"]}
        self.assertEqual(by_id["GAP-002"]["options"], ["Suggested Answer", "Other (type an answer or an instruction)",
                                                       "Explain This Question", "Carry Forward"])
        self.assertNotIn("Suggested Answer", by_id["GAP-001"]["options"], "HIGH/material gaps use the original four options")
        self.assertEqual(by_id["GAP-001"]["options"][-2:], ["Answer Later", "Defer This Gap"])

    def test_choosing_the_suggestion_records_it_as_the_humans_validated_answer(self):
        p = self.with_requirements()
        items = self.gap_items()
        items[1]["suggested_answer"] = {"answer": "Pages load in under 3 seconds on mobile 4G", "source": "BRD 5 and REQ-008"}
        gapmod.add_gaps(p, items)
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        self.assertEqual(p.gap("GAP-002")["answers"], [], "never an answer by itself")
        out = gapmod.record_answer(p, "GAP-002", None, use_suggestion=True)
        a = p.gap("GAP-002")["answers"][0]
        self.assertEqual((out["basis"], a["basis"], a["suggestion_source"]), ("SUGGESTED_ANSWER",) * 2 + ("BRD 5 and REQ-008",))
        self.assertEqual(self.audit(p, "GAP_ANSWER_RECEIVED")[0]["basis"], "SUGGESTED_ANSWER")
        self.assertEqual(p.gap("GAP-002")["status"], "WAITING_FOR_ANSWER", "recording it does not resolve the gap")
        gapmod.resolve(p, "GAP-002", out["answer_id"], "Mobile page-load target is under 3 seconds on 4G.", *OWNER, "Suggestion accepted.",
                       "Yes, use that.", reviewed_ids=[c["artifact_id"] for c in out["candidate_conflicts"]])
        self.assertEqual(p.gap("GAP-002")["status"], "RESOLVED")

    def test_suggestion_cannot_be_chosen_when_there_is_none(self):
        p = self.presented()
        self.assertEqual(code_of(gapmod.record_answer, p, "GAP-002", None, use_suggestion=True), "NO_SUGGESTED_ANSWER")
        self.assertEqual(p.gap("GAP-002")["answers"], [])

    def test_suggestion_and_typed_text_cannot_be_combined(self):
        p = self.with_requirements()
        items = self.gap_items()
        items[1]["suggested_answer"] = {"answer": "3 seconds", "source": "BRD 5"}
        gapmod.add_gaps(p, items)
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        self.assertEqual(code_of(gapmod.record_answer, p, "GAP-002", "2 seconds", use_suggestion=True), "BAD_INPUT")

    def test_other_answer_on_a_medium_or_low_gap_is_checked_against_requirements_and_brd(self):
        p = self.with_requirements()
        items = self.gap_items()[:1]
        items[0].update(title="Over-limit message wording", category="Validation and exception handling", priority="LOW",
                        question="How many books may a member borrow?", impacted_requirement_ids=["REQ-003"], material=False,
                        priority_rationale="Wording only")
        gapmod.add_gaps(p, items)
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        a = gapmod.record_answer(p, "GAP-001", "A member may borrow at most 8 books at the same time.")
        ids = [c["artifact_id"] for c in a["candidate_conflicts"]]
        self.assertIn("REQ-003", ids)
        self.assertTrue(any(i.startswith("REQ-003/BRD") for i in ids))

        def resolve(**k):
            return gapmod.resolve(p, "GAP-001", a["answer_id"], "Up to 8 books.", *OWNER, "x", "Yes.", **k)
        self.assertEqual(code_of(resolve), "CONFLICT_REVIEW_REQUIRED")
        gapmod.record_conflict(p, "GAP-001", a["answer_id"], ["REQ-003"], "REQ-003 says at most 5.")
        self.assertEqual(code_of(resolve), "CONFLICT_UNRESOLVED")
        self.assertEqual(p.gap("GAP-001")["status"], "WAITING_FOR_ANSWER", "nothing is resolved until clarified")
        # carrying the conflicted gap forward is allowed; the raw answer and the conflict stay on record
        gapmod.carry_forward(p, "GAP-001", *OWNER, "Waiting for clarification from the business")
        g = p.gap("GAP-001")
        self.assertEqual((g["status"], len(g["answers"]), g["conflicts"][0]["status"]), ("OPEN", 1, "OPEN"))

    def test_explain_this_question_returns_missing_info_importance_and_impact_without_changing_the_gap(self):
        p = self.presented()
        before = json.dumps(p.gap("GAP-002"), sort_keys=True)
        out = gapmod.explain(p, "GAP-002")
        self.assertEqual(out["missing_information"], "No target page-load time.")
        self.assertEqual(out["why_it_matters"], "Not testable.")
        self.assertEqual(out["impact_on_requirements"][0]["id"], "REQ-008")
        self.assertTrue(out["blocks_stories_if_unresolved"], "an open MEDIUM gap blocks unless it is carried forward")
        self.assertIn("flagged NEEDS_CLARIFICATION", out["if_carried_forward"])
        self.assertEqual(json.dumps(p.gap("GAP-002"), sort_keys=True), before, "explaining records no answer and changes no status")
        self.assertEqual(len(self.audit(p, "GAP_EXPLAINED")), 1)
        high = gapmod.explain(p, "GAP-001")
        self.assertIsNone(high["if_carried_forward"])
        self.assertIn("HIGH", high["carry_forward_not_available_because"])


class BaselineTests(Base):
    def test_baseline_marks_requirement_ready_with_its_open_gaps_and_flags_the_gap_as_carried(self):
        p = self.carried_baseline(approve=False)
        doc = p.read_json("baselines/json/baseline-v1.json")
        e = next(x for x in doc["requirements"] if x["requirement_id"] == "REQ-008")
        self.assertEqual((e["baseline_status"], e["open_gap_ids"], e["blocked_by_gap_ids"]), ("READY", ["GAP-002"], []))
        self.assertTrue(doc["outstanding_gaps"][0]["carried_forward"])
        md = (p.root / "baselines/md/baseline-v1.md").read_text(encoding="utf-8")
        self.assertIn("READY with open gaps GAP-002", md)
        self.assertIn("CARRIED FORWARD", md)

    def test_uncarried_open_gap_still_blocks_the_baseline(self):
        p = self.presented()
        self.answer_and_resolve(p, "GAP-001", "R10 per day.", "R10 per day.")
        self.answer_and_resolve(p, "GAP-003", "Reject.", "Reject the 6th borrow.")
        gapmod.commit_analysis(p, True)
        basemod.create(p)
        e = next(x for x in p.read_json("baselines/json/baseline-v1.json")["requirements"] if x["requirement_id"] == "REQ-008")
        self.assertEqual((e["baseline_status"], e["blocked_by_gap_ids"]), ("BLOCKED", ["GAP-002"]))

    def test_carrying_forward_after_a_baseline_makes_it_stale_and_changes_the_baseline_inputs(self):
        p = self.presented()
        self.answer_and_resolve(p, "GAP-001", "R10 per day.", "R10 per day.")
        self.answer_and_resolve(p, "GAP-003", "Reject.", "Reject the 6th borrow.")
        gapmod.commit_analysis(p, True)
        basemod.create(p)
        basemod.confirm(p, 1, *OWNER, "approved")
        self.assertEqual(code_of(stories.add_stories, p, [self.speed_story()]), "STORIES_REJECTED", "REQ-008 is BLOCKED in v1")
        self.carry(p)
        self.assertTrue(p.baseline_manifest()["stale"])
        self.assertEqual(p.state["stage"], "WAITING_FOR_GAP_INPUT")
        gapmod.commit_analysis(p, True)
        out = basemod.create(p)
        self.assertFalse(out["reused"])
        self.assertEqual(out["ready_with_open_gaps"], {"REQ-008": ["GAP-002"]})


class StoryTests(Base):
    def test_story_for_a_carried_gap_must_name_it_and_its_impact(self):
        p = self.carried_baseline()
        item = self.speed_story()
        item.pop("unresolved_decisions")
        with self.assertRaises(BAError) as c:
            stories.add_stories(p, [item])
        self.assertIn("does not declare them", json.dumps(c.exception.details))
        self.assertEqual(p.all_stories(), [], "nothing saved")
        for bad, fragment in (([dict(gap_id="GAP-002", potential_impact="x")], "needs a potential_impact"),
                              ([dict(gap_id="GAP-099", potential_impact="Could change something important")], "not an unresolved gap"),
                              ([dict(gap_id="GAP-001", potential_impact="Could change something important")], "GAP-001 is resolved"),
                              ([dict(gap_id="GAP-002", potential_impact="Could change something important", readiness_effect="MAYBE")],
                               "readiness_effect must be")):
            with self.assertRaises(BAError, msg=fragment) as c:
                stories.add_stories(p, [self.speed_story(unresolved_decisions=bad)])
            self.assertIn(fragment, json.dumps(c.exception.details), fragment)

    def test_story_links_open_gap_ids_readiness_and_renders_the_unresolved_decision(self):
        p = self.carried_baseline()
        out = stories.add_stories(p, [self.speed_story()])
        self.assertEqual((out["needs_clarification"], out["not_ready"]), (["US-001"], []))
        s = p.read_json("stories/candidates/json/US-001.json")
        self.assertEqual((s["open_gap_ids"], s["readiness"]), (["GAP-002"], "NEEDS_CLARIFICATION"))
        u = s["unresolved_decisions"][0]
        self.assertEqual((u["gap_id"], u["priority"], u["carried_forward"]), ("GAP-002", "MEDIUM", True))
        self.assertIn("page-load target", u["potential_impact"])
        md = (p.root / "stories/candidates/md/US-001.md").read_text(encoding="utf-8")
        self.assertIn("NEEDS CLARIFICATION", md)
        self.assertIn("GAP-002", md)
        self.assertIn("NOT an approved decision", md)
        self.assertIn("Potential impact", md)
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p))

    def test_not_ready_effect_and_working_assumption_are_labelled_never_approved(self):
        p = self.carried_baseline()
        ud = [dict(gap_id="GAP-002", readiness_effect="NOT_READY", potential_impact="Cannot write the load-time test without a target.",
                   interim_assumption="Assume 3 seconds for estimation only")]
        out = stories.add_stories(p, [self.speed_story(unresolved_decisions=ud)])
        self.assertEqual(out["not_ready"], ["US-001"])
        s = p.read_json("stories/candidates/json/US-001.json")
        self.assertEqual((s["readiness"], s["decision_ids"]), ("NOT_READY", []), "an assumption is not a decision")
        md = (p.root / "stories/candidates/md/US-001.md").read_text(encoding="utf-8")
        self.assertIn("NOT READY FOR DEVELOPMENT", md)
        self.assertIn("Working assumption (NOT approved, to be confirmed): Assume 3 seconds for estimation only", md)
        self.assertEqual(s["assumptions"], [], "interim assumptions do not leak into approved assumptions")

    def test_story_without_open_gaps_is_ready_for_development(self):
        p = self.carried_baseline()
        stories.add_stories(p, [self.story_item("Register as a member", ("REQ-001",))])
        s = p.read_json("stories/candidates/json/US-001.json")
        self.assertEqual((s["readiness"], s["unresolved_decisions"], s["open_gap_ids"]), ("READY_FOR_DEVELOPMENT", [], []))
        self.assertIn("Ready for development", (p.root / "stories/candidates/md/US-001.md").read_text(encoding="utf-8"))

    def test_blocking_gaps_still_block_story_creation(self):
        p = self.presented()
        self.answer_and_resolve(p, "GAP-003", "Reject.", "Reject the 6th borrow.")
        self.carry(p)
        gapmod.commit_analysis(p, True)
        basemod.create(p)
        basemod.confirm(p, 1, *OWNER, "approved")
        # REQ-006 has an unresolved HIGH + material gap: no story, whatever the human says
        item = self.story_item("Charge a late fine", ("REQ-006",), user_story=dict(role="Member", capability="to be charged a fine",
                                                                                   value="late returns are discouraged"))
        self.assertEqual(code_of(stories.add_stories, p, [item]), "STORIES_REJECTED")
        with self.assertRaises(BAError) as c:
            stories.add_stories(p, [item])
        self.assertIn("blocked requirement", c.exception.message)
        self.assertIn("GAP-001", c.exception.message)
        self.assertEqual(stories.gap_context(p, ["REQ-006"])["blocking_gaps_present"], ["GAP-001"])

    def test_story_gap_context_helps_the_author(self):
        p = self.carried_baseline()
        ctx = stories.gap_context(p, ["REQ-008", "REQ-001"])
        g = ctx["open_gaps"][0]
        self.assertEqual((len(ctx["open_gaps"]), g["gap_id"], g["carried_forward"], g["blocks_stories"]), (1, "GAP-002", True, False))
        self.assertIn("What page-load time", g["question"])
        self.assertEqual(code_of(stories.gap_context, p, ["REQ-404"]), "UNKNOWN_REQUIREMENT")

    def test_carried_gap_does_not_stop_story_approval_but_is_recorded(self):
        p = self.carried_baseline()
        stories.add_stories(p, [self.speed_story()])
        stories.next_story(p)
        out = stories.approve(p, "US-001", 1, "Approved with the open gap understood.", *OWNER)
        self.assertEqual((out["readiness"], out["open_gap_ids"]), ("NEEDS_CLARIFICATION", ["GAP-002"]))
        s = p.read_json("stories/approved/json/US-001.json")
        self.assertEqual((s["readiness"], s["open_gap_ids"]), ("NEEDS_CLARIFICATION", ["GAP-002"]))

    def test_approval_refused_if_a_new_open_gap_appeared_after_the_story_was_written(self):
        p = self.carried_baseline()
        stories.add_stories(p, [self.speed_story()])
        gapmod.add_gaps(p, [dict(self.gap_items()[1], title="Mobile image sizes", question="What image size limits apply on mobile?",
                                 impacted_requirement_ids=["REQ-008"], priority="LOW", priority_rationale="Minor", material=False)])
        self.assertTrue(p.read_json("stories/candidates/json/US-001.json")["impact_review"]["pending"])
        self.assertEqual(code_of(stories.approve, p, "US-001", 1, "ok", *OWNER), "IMPACT_REVIEW_PENDING")
        self.carry(p, "GAP-004", reason="Design to supply")  # otherwise the new gap blocks REQ-008 altogether
        self.refresh_baseline(p)
        self.assertEqual(code_of(stories.confirm_unaffected, p, "US-001", "still fine"), "UNRESOLVED_DECISION_MISSING",
                         "the story cannot be cleared until it names the new gap")


class RevisitAndUpdateTests(Base):
    def _approved_story_with_carried_gap(self):
        p = self.carried_baseline()
        stories.add_stories(p, [self.speed_story(), self.story_item("Register as a member", ("REQ-001",))])
        stories.approve(p, "US-001", 1, "Approved with open gap.", *OWNER)
        stories.approve(p, "US-002", 1, "Approved.", *OWNER)
        return p

    def _resolve_carried(self, p):
        gapmod.next_gaps(p, 1, carried=True)
        return self.answer_and_resolve(p, "GAP-002", "Under 3 seconds on 4G.", "Mobile page-load target is under 3 seconds on 4G.")

    def test_resolving_a_carried_gap_flags_only_the_stories_that_listed_it_and_reports_them(self):
        p = self._approved_story_with_carried_gap()
        out = self._resolve_carried(p)
        self.assertEqual((out["was_carried_forward"], out["stories_to_update"]), (True, ["US-001"]))
        self.assertIn("story edit", out["next"])
        s1, s2 = p.read_json("stories/approved/json/US-001.json"), p.read_json("stories/approved/json/US-002.json")
        self.assertTrue(s1["impact_review"]["pending"])
        self.assertIn("GAP-002 resolved", s1["impact_review"]["reason"])
        self.assertIsNone(s2["impact_review"])
        self.assertTrue(p.baseline_manifest()["stale"], "the baseline recorded GAP-002 as outstanding")
        self.assertEqual(p.gap("GAP-002")["status"], "RESOLVED")
        self.assertEqual(len(p.gap("GAP-002")["carry_forward_history"]), 1, "history of the carry-forward is kept")
        self.assertEqual(len(self.audit(p, "GAP_CARRIED_FORWARD")), 1)
        self.assertTrue(self.audit(p, "STORY_IMPACT_FLAGGED"))
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p))

    def test_flagged_story_cannot_be_cleared_or_approved_unchanged_it_must_be_updated(self):
        p = self._approved_story_with_carried_gap()
        self._resolve_carried(p)
        self.assertEqual(code_of(stories.edit, p, "US-001", {"title": "Pages load fast"}, "x"), "BASELINE_STALE",
                         "the baseline is refreshed first")
        self.refresh_baseline(p)
        self.assertEqual(code_of(stories.confirm_unaffected, p, "US-001", "nothing changed"), "UNRESOLVED_DECISION_RESOLVED")
        keep = {"unresolved_decisions": [dict(gap_id="GAP-002", potential_impact="Could change something important")]}
        self.assertEqual(code_of(stories.edit, p, "US-001", keep, "no real update"), "UNRESOLVED_DECISION_RESOLVED")

    def test_updated_story_is_a_new_version_reassessed_and_reviewed_again(self):
        p = self._approved_story_with_carried_gap()
        self._resolve_carried(p)
        self.refresh_baseline(p)
        crit = ["Given a member on a mobile device, when a catalogue page is requested, then it loads in under 3 seconds on 4G."]
        out = stories.edit(p, "US-001", {"acceptance_criteria": crit, "unresolved_decisions": []},
                           "GAP-002 resolved by DEC-003: the target is under 3 seconds on 4G")
        self.assertEqual((out["new_version"], out["status"], out["readiness"], out["open_gap_ids"]),
                         (2, "CANDIDATE", "READY_FOR_DEVELOPMENT", []))
        self.assertTrue(out["approved_version_still_in_force"])
        self.assertEqual(p.read_json("stories/approved/json/US-001.json")["version"], 1, "approved v1 untouched until v2 is approved")
        self.assertEqual(p.read_json("stories/candidates/json/US-001.json")["decision_ids"], ["DEC-003"])
        self.assertEqual(code_of(stories.approve, p, "US-001", 2, ""), "APPROVAL_EVIDENCE_REQUIRED")
        stories.approve(p, "US-001", 2, "Approved after GAP-002 was resolved.", *OWNER)
        s = p.read_json("stories/approved/json/US-001.json")
        self.assertEqual((s["version"], s["readiness"], s["open_gap_ids"]), (2, "READY_FOR_DEVELOPMENT", []))
        self.assertTrue(p.exists("stories/archive/json/US-001.v1.json"), "v1 kept in the archive")
        self.assertEqual(self.audit(p, "STORY_APPROVED")[-1]["version"], 2)

    def test_editing_a_story_to_cover_a_gap_requires_declaring_it(self):
        p = self._approved_story_with_carried_gap()
        self.assertEqual(code_of(stories.edit, p, "US-002", {"requirement_ids": ["REQ-001", "REQ-008"]}, "also covers page speed"),
                         "UNRESOLVED_DECISION_MISSING")

    def test_readiness_is_reassessed_when_one_of_two_gaps_is_resolved(self):
        p = self.presented()
        self.answer_and_resolve(p, "GAP-001", "R10 per day.", "R10 per day.")
        self.answer_and_resolve(p, "GAP-003", "Reject.", "Reject the 6th borrow.")
        extra = dict(self.gap_items()[1], title="Mobile image sizes", question="What image size limits apply on mobile?",
                     impacted_requirement_ids=["REQ-008"], priority="LOW", priority_rationale="Minor", material=False)
        gapmod.add_gaps(p, [extra])
        gapmod.next_gaps(p, 5)
        self.carry(p, "GAP-002")
        self.carry(p, "GAP-004", reason="Design to supply")
        gapmod.commit_analysis(p, True)
        basemod.create(p)
        basemod.confirm(p, 1, *OWNER, "approved")
        ud = [dict(gap_id="GAP-002", readiness_effect="NOT_READY", potential_impact="No test threshold without the target."),
              dict(gap_id="GAP-004", readiness_effect="NEEDS_CLARIFICATION", potential_impact="Image budget may change page weight.")]
        stories.add_stories(p, [self.speed_story(unresolved_decisions=ud)])
        self.assertEqual(p.read_json("stories/candidates/json/US-001.json")["readiness"], "NOT_READY")
        self.assertTrue(any("not ready for development" in r and "US-001" in r for r in checks.completion(p)["reasons"]))
        gapmod.next_gaps(p, 1, carried=True)  # GAP-002 (MEDIUM) before GAP-004 (LOW)
        self.answer_and_resolve(p, "GAP-002", "Under 3 seconds.", "Target is 3 seconds.")
        self.refresh_baseline(p)
        ud2 = [dict(gap_id="GAP-004", readiness_effect="NEEDS_CLARIFICATION", potential_impact="Image budget may change page weight.")]
        out = stories.edit(p, "US-001", {"unresolved_decisions": ud2}, "GAP-002 resolved; GAP-004 still open")
        self.assertEqual((out["readiness"], out["open_gap_ids"]), ("NEEDS_CLARIFICATION", ["GAP-004"]))


class VisibilityTests(Base):
    def test_traceability_status_and_summary_keep_carried_gaps_visible(self):
        p = self.carried_baseline()
        stories.add_stories(p, [self.speed_story(), self.story_item("Register as a member", ("REQ-001",))])
        t = checks.traceability(p, write=True)
        tr = t["open_gap_traceability"]["GAP-002"]
        self.assertEqual((tr["carried_forward"], tr["blocks_stories"], tr["stories"], tr["stories_missing_the_link"]),
                         (True, False, ["US-001"], []))
        self.assertEqual(t["stories_missing_open_gap_links"], [])
        report = (p.root / "reports/traceability-report.md").read_text(encoding="utf-8")
        self.assertIn("| GAP-002 | MEDIUM | carried forward | REQ-008 | US-001 |", report)
        st = checks.status_report(p)
        self.assertEqual((st["gaps"]["carried_forward"], st["stories"]["needs_clarification"], st["gaps"]["blocking_unresolved"]),
                         (["GAP-002"], ["US-001"], []))
        text = checks.status_text(st)
        self.assertIn("CARRIED FORWARD (open): GAP-002", text)
        self.assertIn("needs clarification: US-001", text)
        summary = checks.write_summary(p)
        self.assertIn("carried forward (still OPEN): GAP-002", summary)
        self.assertNotIn("Next: Resolve", summary)

    def test_workflow_can_complete_with_a_carried_gap_but_keeps_it_visible(self):
        p = self.carried_baseline()
        items = []
        for i, rid in enumerate(p.req_ids()):
            it = self.story_item(f"Deliver {rid} outcome {i}", (rid,),
                                 user_story=dict(role="Member", capability=f"to have {rid} delivered", value=f"outcome {i} is met"),
                                 acceptance_criteria=[f"Given the system is running, when {rid} is exercised, then the documented outcome {i} is observed."])
            if rid == "REQ-008":
                it["unresolved_decisions"] = self.speed_story()["unresolved_decisions"]
            items.append(it)
        stories.add_stories(p, items)
        for n in range(1, len(items) + 1):
            stories.approve(p, f"US-{n:03d}", 1, "approve", *OWNER)
        out = checks.complete_workflow(p)
        self.assertTrue(out["complete"], out)
        self.assertEqual(p.gap("GAP-002")["status"], "OPEN", "completion does not resolve or hide the carried gap")
        self.assertIn("GAP-002", checks.next_action(p))
        self.assertIn("gaps next --carried", checks.next_action(p))
        summary = (p.root / "reports/workflow-summary.md").read_text(encoding="utf-8")
        self.assertIn("carried forward (still OPEN): GAP-002", summary)
        self.assertIn("needs clarification: US-008", summary)

    def test_not_ready_story_keeps_the_workflow_partial_but_clarification_does_not(self):
        p = self.carried_baseline()
        stories.add_stories(p, [self.speed_story()])
        stories.approve(p, "US-001", 1, "ok", *OWNER)
        comp = checks.completion(p)
        self.assertTrue(any(r.startswith("requirements") for r in comp["reasons"]))  # other requirements have no stories yet
        self.assertFalse(any("not ready" in r for r in comp["reasons"]))
        s = p.read_json("stories/approved/json/US-001.json")
        s["unresolved_decisions"][0]["readiness_effect"] = "NOT_READY"
        s["readiness"] = "NOT_READY"
        p.save_story("approved", s)
        self.assertTrue(any("not ready for development" in r for r in checks.completion(p)["reasons"]))

    def test_validate_flags_inconsistent_readiness_and_unknown_gap_links(self):
        p = self.carried_baseline()
        stories.add_stories(p, [self.speed_story()])
        s = p.read_json("stories/candidates/json/US-001.json")
        s["readiness"] = "READY_FOR_DEVELOPMENT"
        p.save_story("candidates", s)
        self.assertTrue(any("readiness" in e for e in checks.validate_project(p)["errors"]))

    def test_new_gap_added_after_stories_exist_is_reported_as_missing_link(self):
        p = self.carried_baseline()
        stories.add_stories(p, [self.speed_story()])
        gapmod.add_gaps(p, [dict(self.gap_items()[1], title="Image sizes", question="What image size limits apply on mobile?",
                                 impacted_requirement_ids=["REQ-008"], priority="LOW", priority_rationale="Minor", material=False)])
        t = checks.traceability(p)
        self.assertEqual(t["stories_missing_open_gap_links"], ["US-001"])
        self.assertTrue(any("missing a link" in r for r in checks.completion(p)["reasons"]))


class CliTests(BATest):
    def run_ba(self, root, *args, ok=True, args_file=None):
        cmd = [sys.executable, BA, "--root", str(root)]
        if args_file is not None:
            f = self.tmp / f"args-{len(list(self.tmp.glob('args-*')))}.json"
            f.write_text(json.dumps(args_file, ensure_ascii=False), encoding="utf-8")
            cmd += ["--args-file", str(f)]
        r = subprocess.run(cmd + list(args), capture_output=True, text=True, encoding="utf-8")
        out = json.loads(r.stdout)
        self.assertEqual((r.returncode, out["ok"]), (0, True) if ok else (1, False), r.stdout + r.stderr)
        return out

    def test_cli_carry_forward_explain_suggestion_story_gaps_and_revisit(self):
        p = self.with_requirements()
        items = self.gap_items()
        items[1]["suggested_answer"] = {"answer": "Under 3 seconds on 4G", "source": "BRD 5 and REQ-008"}
        gapmod.add_gaps(p, items)
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        root = p.root
        ex = self.run_ba(root, "gap", "explain", "GAP-002")
        self.assertEqual(ex["impact_on_requirements"][0]["id"], "REQ-008")
        self.run_ba(root, "gap", "carry-forward", "GAP-001", ok=False, args_file={"by-name": OWNER[0], "by-role": OWNER[1]})
        out = self.run_ba(root, "gap", "carry-forward", "GAP-002", args_file={"by-name": OWNER[0], "by-role": OWNER[1], "reason": "Infra to confirm \"later\""})
        self.assertEqual(out["carry_forward"]["reason"], 'Infra to confirm "later"')
        self.assertEqual([g["carried_forward"] for g in self.run_ba(root, "gaps", "list")["gaps"]], [False, True, False])
        nxt = self.run_ba(root, "gaps", "next", "--carried")
        self.assertEqual(nxt["gaps"][0]["options"][0], "Suggested Answer")
        ans = self.run_ba(root, "gap", "answer", "GAP-002", "--use-suggestion")
        self.assertEqual(ans["basis"], "SUGGESTED_ANSWER")
        st = self.run_ba(root, "story", "gaps", "--requirements", "REQ-008,REQ-001")
        self.assertEqual(st["open_gaps"][0]["gap_id"], "GAP-002")
        status = subprocess.run([sys.executable, BA, "--root", str(root), "status"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(self.run_ba(root, "validate")["errors"], [])
