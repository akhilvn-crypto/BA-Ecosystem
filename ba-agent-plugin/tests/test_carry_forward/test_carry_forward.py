"""Medium/low gap workflow: Suggested Answer / Other / Explain This Question / Carry Forward, acceptance-criteria traceability and
readiness, blocking rules for critical/material gaps, and revisit-and-update when a carried-forward gap is finally resolved."""
import json
import subprocess
import sys
from pathlib import Path

from tests.helpers import BATest, acs, checks, docsync, gapmod, key, option_labels, projmod
from ba_lib.project import Project
from ba_lib.util import BAError

OWNER = ("Priya Nair", "Product Owner")
BA = str(Path(__file__).resolve().parents[2] / "scripts" / "ba.py")
K7 = key("LIB-007")


def code_of(fn, *a, **k):
    try:
        fn(*a, **k)
    except BAError as e:
        return e.code
    return None


def approved_json(p, rid):
    return p.read_json(f"acceptance/approved/{projmod.ac_fname(key(rid))}.json")


def cand_json(p, rid):
    return p.read_json(f"acceptance/candidates/{projmod.ac_fname(key(rid))}.json")


class Base(BATest):
    """GAP-001 HIGH+material (LIB-006), GAP-002 MEDIUM (LIB-007), GAP-003 HIGH (LIB-003)."""

    def presented(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 10)
        return p

    def carry(self, p, gid="GAP-002", reason="Performance target comes from the infra team later."):
        return gapmod.carry_forward(p, gid, *OWNER, reason)

    def carried_stage(self):
        """GAP-001/003 resolved, GAP-002 carried forward, gap analysis committed: criteria can be written."""
        p = self.presented()
        self.answer_and_resolve(p, "GAP-001", "R10 per day late, capped at R200.", "Fine is R10 per day late, maximum R200 per loan.")
        self.answer_and_resolve(p, "GAP-003", "Block the borrow and show a limit-reached message.",
                                "A 6th concurrent borrow is rejected with a limit-reached message.")
        self.carry(p)
        gapmod.commit_analysis(p, True)
        return p

    def speed_ac(self, **kw):
        d = self.ac_item("LIB-007",
                         acceptance_criteria=["Given a member on a mobile device, when a catalogue page is requested, "
                                              "then the page loads within the target agreed in the requirements."],
                         open_gap_refs=[dict(gap_id="GAP-002", readiness_effect="NEEDS_CLARIFICATION",
                                             potential_impact="The page-load target (seconds) and network are undecided, so "
                                                              "the pass/fail threshold of the load-time test may change.")])
        d.update(kw)
        return d

    def audit(self, p, event_type):
        return [e for e in p.audit_events() if e["event_type"] == event_type]


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
        items[0].pop("evidence_quote")
        items[0].update(title="Auth model unclear", category="Security and access", priority="MEDIUM",
                        question="Who may see the overdue report?", impacted_req_keys=["LIB-008"])
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


class BulkCarryForwardTests(Base):
    """One-time choice: walk through every MEDIUM/LOW gap, or carry them all forward in one step."""

    def two_nonblocking(self):
        """GAP-001 HIGH+material, GAP-002 MEDIUM, GAP-003 HIGH, GAP-004 LOW (all non-material except GAP-001)."""
        p = self.with_requirements()
        items = self.gap_items()
        items.append(dict(items[1], title="Footer wording undefined", question="What wording goes in the footer?",
                          impacted_req_keys=["LIB-001"], priority="LOW", priority_rationale="Cosmetic."))
        gapmod.add_gaps(p, items)
        self.set_owner(p)
        return p

    def test_hint_is_offered_only_when_the_gap_shown_is_eligible_and_more_than_one_waits(self):
        p = self.two_nonblocking()
        first = gapmod.next_gaps(p, 1)
        self.assertEqual(first["gaps"][0]["priority"], "HIGH")
        self.assertFalse(first["bulk_carry_forward"]["offer_now"])
        self.assertEqual(first["bulk_carry_forward"]["eligible_gap_ids"], ["GAP-002", "GAP-004"])
        self.answer_and_resolve(p, "GAP-001", "R10 per day late, capped at R200.", "Fine is R10 per day late, maximum R200 per loan.")
        self.assertEqual(gapmod.next_gaps(p, 1)["gaps"][0]["gap_id"], "GAP-003")
        gapmod.answer_later(p, "GAP-003")
        # GAP-003 skipped once, so the MEDIUM gap is shown next and the choice is offered
        nxt = gapmod.next_gaps(p, 1)
        self.assertEqual(nxt["gaps"][0]["gap_id"], "GAP-002")
        self.assertTrue(nxt["bulk_carry_forward"]["offer_now"])

    def test_carry_all_carries_each_eligible_gap_with_its_own_record_and_leaves_the_rest(self):
        p = self.two_nonblocking()
        gapmod.next_gaps(p, 10)
        out = gapmod.carry_forward_all(p, *OWNER, "Not needed for the first release.")
        self.assertEqual((out["carried_forward"], out["count"]), (["GAP-002", "GAP-004"], 2))
        self.assertEqual({x["gap_id"] for x in out["not_carried"]}, {"GAP-001", "GAP-003"})
        self.assertTrue(all(x["why"] for x in out["not_carried"]))
        for gid in ("GAP-002", "GAP-004"):
            g = p.gap(gid)
            self.assertEqual((g["status"], g["decision_id"]), ("OPEN", None))
            self.assertEqual((g["carry_forward"]["by_name"], g["carry_forward"]["reason"]), (OWNER[0], "Not needed for the first release."))
        for gid in ("GAP-001", "GAP-003"):
            self.assertIsNone(p.gap(gid)["carry_forward"])
            self.assertEqual(p.gap(gid)["status"], "WAITING_FOR_ANSWER")
        self.assertEqual(len(self.audit(p, "GAP_CARRIED_FORWARD")), 2)
        bulk = self.audit(p, "GAP_CARRIED_FORWARD_BULK")
        self.assertEqual((len(bulk), bulk[0]["gap_ids"]), (1, ["GAP-002", "GAP-004"]))
        self.assertEqual(gapmod.bulk_carry_forward_candidates(p), [])
        self.assertTrue(checks.validate_project(p)["ok"])

    def test_carry_all_needs_a_named_person_and_something_to_carry(self):
        p = self.two_nonblocking()
        gapmod.next_gaps(p, 10)
        self.assertEqual(code_of(gapmod.carry_forward_all, p), "IDENTITY_REQUIRED")
        self.assertEqual(code_of(gapmod.carry_forward_all, p, "Claude", "AI"), "IDENTITY_REQUIRED")
        self.assertEqual([g["carry_forward"] for g in p.gaps()], [None] * 4, "refused attempts change nothing")
        gapmod.carry_forward_all(p, *OWNER)
        self.assertEqual(code_of(gapmod.carry_forward_all, p, *OWNER), "NOTHING_TO_CARRY_FORWARD")

    def test_carry_all_skips_material_medium_gaps_and_requires_an_owner(self):
        p = self.with_requirements()
        gapmod.add_gaps(p, self.gap_items())
        self.assertEqual(code_of(gapmod.carry_forward_all, p, *OWNER), "OWNER_REQUIRED")
        self.set_owner(p)
        gapmod.next_gaps(p, 10)
        gaps = p.gaps()
        gaps[1]["material"] = True
        p.save_gaps(gaps, touched=["GAP-002"])
        self.assertEqual(code_of(gapmod.carry_forward_all, p, *OWNER), "NOTHING_TO_CARRY_FORWARD")


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
        self.assertEqual((ev["actor"]["name"], ev["decision_owner"]["name"], ev["gap_id"], ev["req_keys"]), (OWNER[0], OWNER[0], "GAP-002", [K7]))
        self.assertEqual(g["history"][-1]["event_id"], ev["event_id"])
        self.assertFalse(out["blocks_ac"])
        self.assertIn("still OPEN", out["note"])
        self.assertIn("GAP-002", p.state["pending_gap_ids"], "still outstanding")
        self.assertEqual(checks.status_report(p)["gaps"]["carried_forward"], ["GAP-002"])
        self.assertIn("CARRIED FORWARD", (p.root / "gaps/open/md/GAP-002.md").read_text(encoding="utf-8"))
        self.assertTrue(checks.validate_project(p)["ok"])

    def test_carried_gap_no_longer_blocks_but_an_uncarried_medium_gap_still_does(self):
        p = self.presented()
        self.assertTrue(gapmod.blocks_requirements(p, p.gap("GAP-002")), "strict default: open MEDIUM gap blocks")
        self.carry(p)
        self.assertFalse(gapmod.blocks_requirements(p, p.gap("GAP-002")))
        self.assertNotIn(K7, gapmod.blocked_requirements(p))
        self.assertIn(key("LIB-006"), gapmod.blocked_requirements(p), "HIGH gaps keep blocking")
        self.assertIn(key("LIB-003"), gapmod.blocked_requirements(p))

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
        items[0]["suggested_answer"] = {"answer": "R10 per day, max R200", "source": "lending.md and LIB-006"}
        items[1]["suggested_answer"] = {"answer": "Under 3 seconds on 4G", "source": "lending.md and LIB-007"}
        gapmod.add_gaps(p, items)
        self.set_owner(p)
        by_id = {g["gap_id"]: g for g in gapmod.next_gaps(p, 3)["gaps"]}
        self.assertEqual(option_labels(by_id["GAP-002"]), ["Suggested Answer", "Explain This Question", "Carry Forward"])
        self.assertNotIn("Suggested Answer", option_labels(by_id["GAP-001"]), "HIGH/material gaps never offer a suggestion shortcut")
        self.assertEqual(option_labels(by_id["GAP-001"])[-2:], ["Answer Later", "Defer This Gap"])

    def test_choosing_the_suggestion_records_it_as_the_humans_validated_answer(self):
        p = self.with_requirements()
        items = self.gap_items()
        items[1]["suggested_answer"] = {"answer": "Pages load in under 3 seconds on mobile 4G", "source": "lending.md and LIB-007"}
        gapmod.add_gaps(p, items)
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        self.assertEqual(p.gap("GAP-002")["answers"], [], "never an answer by itself")
        out = gapmod.record_answer(p, "GAP-002", None, use_suggestion=True)
        a = p.gap("GAP-002")["answers"][0]
        self.assertEqual((out["basis"], a["basis"], a["suggestion_source"]), ("SUGGESTED_ANSWER",) * 2 + ("lending.md and LIB-007",))
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
        items[1]["suggested_answer"] = {"answer": "3 seconds", "source": "lending.md"}
        gapmod.add_gaps(p, items)
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        self.assertEqual(code_of(gapmod.record_answer, p, "GAP-002", "2 seconds", use_suggestion=True), "BAD_INPUT")

    def test_other_answer_on_a_medium_or_low_gap_is_checked_against_the_requirements(self):
        p = self.with_requirements()
        items = self.gap_items()[:1]
        items[0].pop("evidence_quote")
        items[0].update(title="Over-limit message wording", category="Validation and exception handling", priority="LOW",
                        question="How many books may a member borrow?", impacted_req_keys=["LIB-003"], material=False,
                        priority_rationale="Wording only")
        gapmod.add_gaps(p, items)
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        a = gapmod.record_answer(p, "GAP-001", "A member may borrow at most 8 books at the same time.")
        ids = [c["artifact_id"] for c in a["candidate_conflicts"]]
        self.assertIn("LIB-003", ids)

        def resolve(**k):
            return gapmod.resolve(p, "GAP-001", a["answer_id"], "Up to 8 books.", *OWNER, "x", "Yes.", **k)
        self.assertEqual(code_of(resolve), "CONFLICT_REVIEW_REQUIRED")
        gapmod.record_conflict(p, "GAP-001", a["answer_id"], ["LIB-003"], "LIB-003 says at most 5.")
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
        self.assertEqual(out["impact_on_requirements"][0]["id"], "LIB-007")
        self.assertEqual(out["impact_on_requirements"][0]["key"], K7)
        self.assertIn("Pages must load quickly", out["impact_on_requirements"][0]["text"])
        self.assertTrue(out["blocks_ac_if_unresolved"], "an open MEDIUM gap blocks unless it is carried forward")
        self.assertIn("flagged NEEDS_CLARIFICATION", out["if_carried_forward"])
        self.assertEqual(json.dumps(p.gap("GAP-002"), sort_keys=True), before, "explaining records no answer and changes no status")
        self.assertEqual(len(self.audit(p, "GAP_EXPLAINED")), 1)
        high = gapmod.explain(p, "GAP-001")
        self.assertIsNone(high["if_carried_forward"])
        self.assertIn("HIGH", high["carry_forward_not_available_because"])


class RequirementStatusTests(Base):
    def status(self, p, rid):
        return {r["req_key"]: r for r in checks.requirement_rows(p)}[key(rid)]

    def test_status_is_computed_live_ready_ready_with_open_gaps_blocked(self):
        p = self.presented()
        self.assertEqual(self.status(p, "LIB-001")["status"], "READY")
        self.assertEqual(self.status(p, "LIB-007")["status"], "BLOCKED", "uncarried MEDIUM gap blocks under the strict default")
        self.assertEqual(self.status(p, "LIB-006")["blocked_by"], ["GAP-001"])
        self.carry(p)
        self.assertEqual(self.status(p, "LIB-007")["status"], "READY_WITH_OPEN_GAPS")
        self.assertEqual(self.status(p, "LIB-007")["open_gaps"], ["GAP-002"])
        self.assertEqual(docsync.requirement_status(p, K7), "READY_WITH_OPEN_GAPS")
        self.answer_and_resolve(p, "GAP-001", "R10 per day.", "R10 per day.")
        self.assertEqual(self.status(p, "LIB-006")["status"], "READY")

    def test_the_enriched_copy_shows_the_open_carried_gap_and_withholds_criteria_for_blocked_requirements(self):
        p = self.presented()
        t = self.enriched(p, "lending.md")
        self.assertIn("Acceptance criteria withheld until blocking gaps are resolved.", t)
        self.assertIn("| GAP-001 | HIGH | OPEN |", t)
        self.carry(p)
        t = self.enriched(p, "lending.md")
        self.assertIn("| GAP-002 | MEDIUM | OPEN (carried forward) |", t)
        self.assertEqual(t.count("withheld"), 1, "only LIB-006 stays blocked")

    def test_revisiting_a_carried_gap_after_criteria_exist_reopens_the_stage_and_blocks_the_requirement_again(self):
        p = self.carried_stage()
        acs.add_acs(p, [self.speed_ac()])
        self.assertEqual(p.state["stage"], "AC_DRAFTING")
        gapmod.next_gaps(p, 1, carried=True)
        self.assertEqual(p.state["stage"], "WAITING_FOR_GAP_INPUT")
        self.assertEqual(self.status(p, "LIB-007")["status"], "BLOCKED")
        self.assertTrue(any("blocked by unresolved gap" in b for b in acs._ac_blockers(p, cand_json(p, "LIB-007"))))


class AcceptanceCriteriaTests(Base):
    def test_criteria_for_a_carried_gap_must_name_it_and_its_impact(self):
        p = self.carried_stage()
        item = self.speed_ac()
        item.pop("open_gap_refs")
        with self.assertRaises(BAError) as c:
            acs.add_acs(p, [item])
        self.assertIn("do not declare them", json.dumps(c.exception.details))
        self.assertEqual(p.all_acs(), [], "nothing saved")
        for bad, fragment in (([dict(gap_id="GAP-002", potential_impact="x")], "needs a potential_impact"),
                              ([dict(gap_id="GAP-099", potential_impact="Could change something important")], "not an unresolved gap"),
                              ([dict(gap_id="GAP-001", potential_impact="Could change something important")], "GAP-001 is resolved"),
                              ([dict(gap_id="GAP-002", potential_impact="Could change something important", readiness_effect="MAYBE")],
                               "readiness_effect must be")):
            with self.assertRaises(BAError, msg=fragment) as c:
                acs.add_acs(p, [self.speed_ac(open_gap_refs=bad)])
            self.assertIn(fragment, json.dumps(c.exception.details), fragment)

    def test_record_links_open_gaps_readiness_and_renders_the_unresolved_decision(self):
        p = self.carried_stage()
        out = acs.add_acs(p, [self.speed_ac()])
        self.assertEqual((out["needs_clarification"], out["not_ready"]), ([K7], []))
        a = cand_json(p, "LIB-007")
        self.assertEqual(a["readiness"], "NEEDS_CLARIFICATION")
        u = a["open_gap_refs"][0]
        self.assertEqual((u["gap_id"], u["priority"], u["carried_forward"]), ("GAP-002", "MEDIUM", True))
        self.assertIn("page-load target", u["potential_impact"])
        md = (p.root / "acceptance/candidates/md" / (projmod.ac_fname(K7) + ".md")).read_text(encoding="utf-8")
        self.assertIn("NEEDS CLARIFICATION", md)
        self.assertIn("GAP-002", md)
        self.assertIn("NOT an approved decision", md)
        self.assertIn("Potential impact", md)
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p))

    def test_not_ready_effect_and_working_assumption_are_labelled_never_approved(self):
        p = self.carried_stage()
        refs = [dict(gap_id="GAP-002", readiness_effect="NOT_READY", potential_impact="Cannot write the load-time test without a target.",
                     interim_assumption="Assume 3 seconds for estimation only")]
        out = acs.add_acs(p, [self.speed_ac(open_gap_refs=refs)])
        self.assertEqual(out["not_ready"], [K7])
        a = cand_json(p, "LIB-007")
        self.assertEqual((a["readiness"], a["decision_ids"]), ("NOT_READY", []), "an assumption is not a decision")
        md = (p.root / "acceptance/candidates/md" / (projmod.ac_fname(K7) + ".md")).read_text(encoding="utf-8")
        self.assertIn("NOT READY FOR DEVELOPMENT", md)
        self.assertIn("Working assumption (NOT approved, to be confirmed): Assume 3 seconds for estimation only", md)
        self.assertEqual(a["assumptions"], [], "interim assumptions do not leak into approved assumptions")

    def test_criteria_without_open_gaps_are_ready_for_development(self):
        p = self.carried_stage()
        acs.add_acs(p, [self.ac_item("LIB-001")])
        a = cand_json(p, "LIB-001")
        self.assertEqual((a["readiness"], a["open_gap_refs"]), ("READY_FOR_DEVELOPMENT", []))
        md = (p.root / "acceptance/candidates/md" / (projmod.ac_fname(key("LIB-001")) + ".md")).read_text(encoding="utf-8")
        self.assertIn("Ready for development", md)

    def test_blocking_gaps_still_block_criteria_creation(self):
        p = self.presented()
        self.answer_and_resolve(p, "GAP-003", "Reject.", "Reject the 6th borrow.")
        self.carry(p)
        gapmod.commit_analysis(p, True)
        # LIB-006 has an unresolved HIGH + material gap: no criteria, whatever the human says
        item = self.ac_item("LIB-006")
        self.assertEqual(code_of(acs.add_acs, p, [item]), "ACS_REJECTED")
        with self.assertRaises(BAError) as c:
            acs.add_acs(p, [item])
        self.assertIn("blocked requirement", c.exception.message)
        self.assertIn("GAP-001", c.exception.message)
        ctx = acs.gap_context(p, ["LIB-006"])
        self.assertEqual(ctx["blocking_gaps_present"], ["GAP-001"])
        self.assertEqual(ctx["requirements"][0]["status"], "BLOCKED")

    def test_gap_analysis_must_be_committed_before_criteria(self):
        p = self.presented()
        self.assertEqual(code_of(acs.add_acs, p, [self.ac_item("LIB-001")]), "ANALYSIS_INCOMPLETE")

    def test_gap_context_helps_the_author(self):
        p = self.carried_stage()
        ctx = acs.gap_context(p, ["LIB-007", "LIB-001"])
        g = ctx["open_gaps"][0]
        self.assertEqual((len(ctx["open_gaps"]), g["gap_id"], g["carried_forward"], g["blocks_ac"]), (1, "GAP-002", True, False))
        self.assertIn("What page-load time", g["question"])
        self.assertEqual({r["label"]: r["status"] for r in ctx["requirements"]}, {"LIB-007": "READY_WITH_OPEN_GAPS", "LIB-001": "READY"})
        self.assertEqual(code_of(acs.gap_context, p, ["LIB-404"]), "UNKNOWN_REQUIREMENT")

    def test_carried_gap_does_not_stop_approval_but_is_recorded(self):
        p = self.carried_stage()
        acs.add_acs(p, [self.speed_ac()])
        acs.next_ac(p)
        out = acs.approve(p, K7, 1, "Approved with the open gap understood.", *OWNER)
        self.assertEqual((out["readiness"], out["open_gap_ids"]), ("NEEDS_CLARIFICATION", ["GAP-002"]))
        a = approved_json(p, "LIB-007")
        self.assertEqual((a["readiness"], [r["gap_id"] for r in a["open_gap_refs"]]), ("NEEDS_CLARIFICATION", ["GAP-002"]))
        t = self.enriched(p, "lending.md")
        self.assertIn("OPEN (carried forward)", t)
        self.assertIn("**Readiness:** NEEDS_CLARIFICATION", t)

    def test_approval_refused_if_a_new_open_gap_appeared_after_the_criteria_were_written(self):
        p = self.carried_stage()
        acs.add_acs(p, [self.speed_ac()])
        gapmod.add_gaps(p, [dict(self.gap_items()[1], title="Mobile image sizes", question="What image size limits apply on mobile?",
                                 impacted_req_keys=["LIB-007"], priority="LOW", priority_rationale="Minor", material=False)])
        self.assertTrue(cand_json(p, "LIB-007")["impact_review"]["pending"])
        self.assertEqual(code_of(acs.approve, p, K7, 1, "ok", *OWNER), "IMPACT_REVIEW_PENDING")
        self.carry(p, "GAP-004", reason="Design to supply")  # otherwise the new gap blocks LIB-007 altogether
        self.assertEqual(code_of(acs.confirm_unaffected, p, K7, "still fine"), "OPEN_GAP_MISSING",
                         "the criteria cannot be cleared until they name the new gap")


class RevisitAndUpdateTests(Base):
    def _approved_with_carried_gap(self):
        p = self.carried_stage()
        acs.add_acs(p, [self.speed_ac(), self.ac_item("LIB-001")])
        acs.approve(p, K7, 1, "Approved with open gap.", *OWNER)
        acs.approve(p, key("LIB-001"), 1, "Approved.", *OWNER)
        return p

    def _resolve_carried(self, p):
        gapmod.next_gaps(p, 1, carried=True)
        return self.answer_and_resolve(p, "GAP-002", "Under 3 seconds on 4G.", "Mobile page-load target is under 3 seconds on 4G.")

    def test_resolving_a_carried_gap_flags_only_the_criteria_that_listed_it_and_reports_them(self):
        p = self._approved_with_carried_gap()
        out = self._resolve_carried(p)
        self.assertEqual((out["was_carried_forward"], out["acs_to_update"]), (True, [K7]))
        self.assertIn("ac edit", out["next"])
        a7, a1 = approved_json(p, "LIB-007"), approved_json(p, "LIB-001")
        self.assertTrue(a7["impact_review"]["pending"])
        self.assertIn("GAP-002 resolved", a7["impact_review"]["reason"])
        self.assertIsNone(a1["impact_review"])
        self.assertEqual(p.gap("GAP-002")["status"], "RESOLVED")
        self.assertEqual(len(p.gap("GAP-002")["carry_forward_history"]), 1, "history of the carry-forward is kept")
        self.assertEqual(len(self.audit(p, "GAP_CARRIED_FORWARD")), 1)
        self.assertTrue(self.audit(p, "AC_IMPACT_FLAGGED"))
        self.assertIn("Under review", self.enriched(p, "lending.md"), "the enriched copy says the criteria are under review")
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p))

    def test_flagged_criteria_cannot_be_cleared_or_approved_unchanged_they_must_be_updated(self):
        p = self._approved_with_carried_gap()
        self._resolve_carried(p)
        self.assertEqual(code_of(acs.confirm_unaffected, p, K7, "nothing changed"), "OPEN_GAP_RESOLVED")
        keep = {"open_gap_refs": [dict(gap_id="GAP-002", potential_impact="Could change something important")]}
        self.assertEqual(code_of(acs.edit, p, K7, keep, "no real update"), "OPEN_GAP_RESOLVED")

    def test_updated_criteria_are_a_new_version_reassessed_and_reviewed_again(self):
        p = self._approved_with_carried_gap()
        self._resolve_carried(p)
        crit = ["Given a member on a mobile device, when a catalogue page is requested, then it loads in under 3 seconds on 4G."]
        out = acs.edit(p, K7, {"acceptance_criteria": crit, "open_gap_refs": []},
                       "GAP-002 resolved by DEC-003: the target is under 3 seconds on 4G")
        self.assertEqual((out["new_version"], out["status"], out["readiness"], out["open_gap_ids"]),
                         (2, "CANDIDATE", "READY_FOR_DEVELOPMENT", []))
        self.assertTrue(out["approved_version_still_in_force"])
        self.assertEqual(approved_json(p, "LIB-007")["version"], 1, "approved v1 untouched until v2 is approved")
        self.assertIn("AC v1", self.enriched(p, "lending.md"), "the document still shows the approved version")
        self.assertEqual(cand_json(p, "LIB-007")["decision_ids"], ["DEC-003"])
        self.assertEqual(code_of(acs.approve, p, K7, 2, ""), "APPROVAL_EVIDENCE_REQUIRED")
        acs.approve(p, K7, 2, "Approved after GAP-002 was resolved.", *OWNER)
        a = approved_json(p, "LIB-007")
        self.assertEqual((a["version"], a["readiness"], a["open_gap_refs"]), (2, "READY_FOR_DEVELOPMENT", []))
        self.assertTrue(p.exists(f"acceptance/archive/{projmod.ac_fname(K7)}.v1.json"), "v1 kept in the archive")
        self.assertEqual(self.audit(p, "AC_APPROVED")[-1]["version"], 2)
        t = self.enriched(p, "lending.md")
        self.assertIn("AC v2", t)
        self.assertIn("under 3 seconds on 4G", t)
        self.assertNotIn("OPEN (carried forward)", t)

    def test_editing_criteria_when_a_gap_exists_requires_declaring_it(self):
        p = self._approved_with_carried_gap()
        gapmod.add_gaps(p, [dict(self.gap_items()[1], title="Password rules", question="Which password rules apply?",
                                 impacted_req_keys=["LIB-001"], priority="LOW", priority_rationale="Minor", material=False)])
        gapmod.next_gaps(p, 5)
        self.carry(p, "GAP-004", reason="Security team to supply")
        crit = ["Given a visitor with a valid email, when they register, then a member account is created with a compliant password."]
        self.assertEqual(code_of(acs.edit, p, key("LIB-001"), {"acceptance_criteria": crit}, "tighten wording"), "OPEN_GAP_MISSING")

    def test_readiness_is_reassessed_when_one_of_two_gaps_is_resolved(self):
        p = self.presented()
        self.answer_and_resolve(p, "GAP-001", "R10 per day.", "R10 per day.")
        self.answer_and_resolve(p, "GAP-003", "Reject.", "Reject the 6th borrow.")
        extra = dict(self.gap_items()[1], title="Mobile image sizes", question="What image size limits apply on mobile?",
                     impacted_req_keys=["LIB-007"], priority="LOW", priority_rationale="Minor", material=False)
        gapmod.add_gaps(p, [extra])
        gapmod.next_gaps(p, 5)
        self.carry(p, "GAP-002")
        self.carry(p, "GAP-004", reason="Design to supply")
        gapmod.commit_analysis(p, True)
        refs = [dict(gap_id="GAP-002", readiness_effect="NOT_READY", potential_impact="No test threshold without the target."),
                dict(gap_id="GAP-004", readiness_effect="NEEDS_CLARIFICATION", potential_impact="Image budget may change page weight.")]
        acs.add_acs(p, [self.speed_ac(open_gap_refs=refs)])
        self.assertEqual(cand_json(p, "LIB-007")["readiness"], "NOT_READY")
        acs.approve(p, K7, 1, "ok", *OWNER)
        self.assertTrue(any("not ready for development" in r and "LIB-007" in r for r in checks.completion(p)["reasons"]))
        gapmod.next_gaps(p, 1, carried=True)  # GAP-002 (MEDIUM) before GAP-004 (LOW)
        self.answer_and_resolve(p, "GAP-002", "Under 3 seconds.", "Target is 3 seconds.")
        refs2 = [dict(gap_id="GAP-004", readiness_effect="NEEDS_CLARIFICATION", potential_impact="Image budget may change page weight.")]
        out = acs.edit(p, K7, {"open_gap_refs": refs2}, "GAP-002 resolved; GAP-004 still open")
        self.assertEqual((out["readiness"], out["open_gap_ids"]), ("NEEDS_CLARIFICATION", ["GAP-004"]))


class VisibilityTests(Base):
    def test_traceability_status_and_summary_keep_carried_gaps_visible(self):
        p = self.carried_stage()
        acs.add_acs(p, [self.speed_ac(), self.ac_item("LIB-001")])
        t = checks.traceability(p, write=True)
        tr = t["open_gap_traceability"]["GAP-002"]
        self.assertEqual((tr["carried_forward"], tr["blocks_ac"], tr["acs"], tr["acs_missing_the_link"]), (True, False, [K7], []))
        self.assertEqual(t["acs_missing_open_gap_links"], [])
        report = (p.root / "reports/traceability-report.md").read_text(encoding="utf-8")
        self.assertIn(f"| GAP-002 | MEDIUM | carried forward | LIB-007 | {K7} |", report)
        st = checks.status_report(p)
        self.assertEqual((st["gaps"]["carried_forward"], st["acceptance"]["needs_clarification"], st["gaps"]["blocking_unresolved"]),
                         (["GAP-002"], [K7], []))
        text = checks.status_text(st)
        self.assertIn("CARRIED FORWARD (open): GAP-002", text)
        self.assertIn(f"needs clarification: {K7}", text)
        summary = checks.write_summary(p)
        self.assertIn("carried forward (still OPEN): GAP-002", summary)
        self.assertNotIn("Next: Resolve", summary)

    def test_workflow_can_complete_with_a_carried_gap_but_keeps_it_visible(self):
        p = self.carried_stage()
        items = []
        for e in p.requirements():
            rid = e["id"]
            it = self.ac_item(rid)
            if rid == "LIB-007":
                it = self.speed_ac()
            items.append(it)
        acs.add_acs(p, items)
        for e in p.requirements():
            acs.approve(p, e["req_key"], 1, "approve", *OWNER)
        out = checks.complete_workflow(p)
        self.assertTrue(out["complete"], out)
        self.assertEqual(out["carried_forward"], ["GAP-002"])
        self.assertEqual(p.state["stage"], "COMPLETED")
        self.assertEqual(p.gap("GAP-002")["status"], "OPEN", "completion does not resolve or hide the carried gap")
        self.assertIn("GAP-002", checks.next_action(p))
        self.assertIn("gaps next --carried", checks.next_action(p))
        summary = (p.root / "reports/workflow-summary.md").read_text(encoding="utf-8")
        self.assertIn("carried forward (still OPEN): GAP-002", summary)
        self.assertIn(f"needs clarification: {K7}", summary)

    def test_not_ready_criteria_keep_the_workflow_partial_but_clarification_does_not(self):
        p = self.carried_stage()
        acs.add_acs(p, [self.speed_ac()])
        acs.approve(p, K7, 1, "ok", *OWNER)
        comp = checks.completion(p)
        self.assertTrue(any(r.startswith("requirements without approved") for r in comp["reasons"]))  # other requirements have none yet
        self.assertFalse(any("not ready" in r for r in comp["reasons"]))
        a = approved_json(p, "LIB-007")
        a["open_gap_refs"][0]["readiness_effect"] = "NOT_READY"
        a["readiness"] = "NOT_READY"
        p.save_ac("approved", a)
        self.assertTrue(any("not ready for development" in r for r in checks.completion(p)["reasons"]))

    def test_validate_flags_inconsistent_readiness_and_unknown_gap_links(self):
        p = self.carried_stage()
        acs.add_acs(p, [self.speed_ac()])
        a = cand_json(p, "LIB-007")
        a["readiness"] = "READY_FOR_DEVELOPMENT"
        p.save_ac("candidates", a)
        self.assertTrue(any("readiness" in e for e in checks.validate_project(p)["errors"]))

    def test_new_gap_added_after_criteria_exist_is_reported_as_missing_link(self):
        p = self.carried_stage()
        acs.add_acs(p, [self.speed_ac()])
        gapmod.add_gaps(p, [dict(self.gap_items()[1], title="Image sizes", question="What image size limits apply on mobile?",
                                 impacted_req_keys=["LIB-007"], priority="LOW", priority_rationale="Minor", material=False)])
        t = checks.traceability(p)
        self.assertEqual(t["acs_missing_open_gap_links"], [K7])
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

    def test_cli_carry_forward_all(self):
        p = self.with_requirements()
        items = self.gap_items()
        items.append(dict(items[1], title="Footer wording undefined", question="What wording goes in the footer?",
                          impacted_req_keys=["LIB-001"], priority="LOW"))
        gapmod.add_gaps(p, items)
        self.set_owner(p)
        nxt = gapmod.next_gaps(p, 10)
        self.assertEqual(nxt["bulk_carry_forward"]["eligible_gap_ids"], ["GAP-002", "GAP-004"])
        self.run_ba(p.root, "gaps", "carry-forward-all", ok=False, args_file={"by-name": OWNER[0]})
        out = self.run_ba(p.root, "gaps", "carry-forward-all", args_file={"by-name": OWNER[0], "by-role": OWNER[1], "reason": "Later"})
        self.assertEqual(out["carried_forward"], ["GAP-002", "GAP-004"])
        self.assertEqual(self.run_ba(p.root, "validate")["errors"], [])

    def test_cli_carry_forward_explain_suggestion_ac_gaps_and_revisit(self):
        p = self.with_requirements()
        items = self.gap_items()
        items[1]["suggested_answer"] = {"answer": "Under 3 seconds on 4G", "source": "lending.md and LIB-007"}
        gapmod.add_gaps(p, items)
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        root = p.root
        ex = self.run_ba(root, "gap", "explain", "GAP-002")
        self.assertEqual(ex["impact_on_requirements"][0]["id"], "LIB-007")
        self.run_ba(root, "gap", "carry-forward", "GAP-001", ok=False, args_file={"by-name": OWNER[0], "by-role": OWNER[1]})
        out = self.run_ba(root, "gap", "carry-forward", "GAP-002", args_file={"by-name": OWNER[0], "by-role": OWNER[1], "reason": "Infra to confirm \"later\""})
        self.assertEqual(out["carry_forward"]["reason"], 'Infra to confirm "later"')
        self.assertEqual([g["carried_forward"] for g in self.run_ba(root, "gaps", "list")["gaps"]], [False, True, False])
        nxt = self.run_ba(root, "gaps", "next", "--carried")
        self.assertEqual(option_labels(nxt["gaps"][0])[0], "Suggested Answer")
        ans = self.run_ba(root, "gap", "answer", "GAP-002", "--use-suggestion")
        self.assertEqual(ans["basis"], "SUGGESTED_ANSWER")
        st = self.run_ba(root, "ac", "gaps", "--requirements", "LIB-007,LIB-001")
        self.assertEqual(st["open_gaps"][0]["gap_id"], "GAP-002")
        status = subprocess.run([sys.executable, BA, "--root", str(root), "status"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(self.run_ba(root, "validate")["errors"], [])
