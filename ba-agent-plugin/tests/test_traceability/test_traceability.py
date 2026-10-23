"""Story generation/review rules, traceability, blocked requirements, duplicates/orphans, validation tamper-detection."""
import json
import unittest

from tests.helpers import BATest, basemod, checks, gapmod, reqs, stories, util
from ba_lib.util import BAError


def code_of(fn, *a, **k):
    try:
        fn(*a, **k)
    except BAError as e:
        return e.code
    return None


class StoryGenerationTests(BATest):
    def test_stories_require_approved_baseline(self):
        p = self.to_baseline(approve=False)
        self.assertEqual(code_of(stories.add_stories, p, [self.story_item()]), "NO_APPROVED_BASELINE")
        basemod.confirm(p, 1, "Priya Nair", "Product Owner", "approved")
        self.assertEqual(stories.add_stories(p, [self.story_item()])["created"], ["US-001"])
        self.assertEqual(p.state["stage"], "STORIES_READY")

    def test_story_contents_validated(self):
        p = self.to_baseline()
        bad_ac = self.story_item(acceptance_criteria=["It works well for everybody."])
        self.assertEqual(code_of(stories.add_stories, p, [bad_ac]), "STORIES_REJECTED")
        with self.assertRaises(BAError) as c:
            stories.add_stories(p, [bad_ac])
        self.assertIn("Given", c.exception.message)
        self.assertIn("UNLINKED_REQUIREMENT", "UNLINKED_REQUIREMENT" if code_of(stories.add_stories, p, [self.story_item(reqs_=("REQ-404",))]) else "")
        self.assertEqual(code_of(stories.add_stories, p, [self.story_item(reqs_=())]), "STORIES_REJECTED")
        no_role = self.story_item(user_story=dict(role="", capability="to do x thing", value="because y"))
        self.assertEqual(code_of(stories.add_stories, p, [no_role]), "STORIES_REJECTED")
        self.assertEqual(p.all_stories(), [], "a rejected batch saves nothing")

    def test_story_traces_to_requirements_decisions_sources(self):
        p = self.to_baseline()
        stories.add_stories(p, [self.story_item("Enforce borrow limit", ("REQ-003",),
                               acceptance_criteria=["Given a member with 5 loans, when borrowing a 6th, then it is rejected with a limit message."])])
        s = p.read_json("stories/candidates/US-001.json")
        self.assertEqual(s["requirement_ids"], ["REQ-003"])
        self.assertTrue(s["decision_ids"], "linked to the decision that clarified REQ-003")
        self.assertIn("§", s["source_references"][0])
        self.assertEqual(s["status"], "CANDIDATE")
        self.assertTrue((p.root / "stories/candidates/US-001.md").read_text(encoding="utf-8").startswith("## US-001:"))

    def test_requirement_with_unresolved_high_gap_cannot_become_story(self):
        """Test 14 — strict policy blocks the affected requirement only."""
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        gapmod.defer(p, "GAP-001", "Waiting for finance", owner="Finance", revisit_by="2026-11-30",
                    by_name="Priya Nair", by_role="Product Owner")
        self.answer_and_resolve(p, "GAP-002", "Under 3 seconds.", "Mobile page load is under 3 seconds.")
        self.answer_and_resolve(p, "GAP-003", "Reject with message.", "A 6th borrow is rejected with a message.")
        gapmod.commit_analysis(p, True)
        out = basemod.create(p)
        self.assertEqual(out["blocked_requirements"], 1)
        basemod.confirm(p, 1, "Priya Nair", "Product Owner", "ok")
        base = p.read_json("baselines/baseline-v1.json")
        blocked = [r for r in base["requirements"] if r["baseline_status"] == "BLOCKED"]
        self.assertEqual([r["requirement_id"] for r in blocked], ["REQ-006"])
        self.assertEqual(blocked[0]["blocked_by_gap_ids"], ["GAP-001"])
        with self.assertRaises(BAError) as c:
            stories.add_stories(p, [self.story_item("Charge late fine", ("REQ-006",),
                                    acceptance_criteria=["Given a late return, when it is checked in, then a fine is charged."])])
        self.assertIn("blocked", c.exception.message)
        self.assertEqual(stories.add_stories(p, [self.story_item()])["created"], ["US-001"], "unaffected requirements still proceed")
        self.assertEqual(p.gap("GAP-001")["status"], "DEFERRED", "a deferred gap is never implied to be resolved")
        comp = checks.complete_workflow(p)
        self.assertFalse(comp["complete"])
        self.assertIn("PARTIAL", comp["message"])

    def test_nonblocking_gap_carry_forward_needs_explicit_policy(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        self.answer_and_resolve(p, "GAP-001", "R10/day max R200", "Fine is R10/day, max R200.")
        self.answer_and_resolve(p, "GAP-003", "Reject with message", "A 6th borrow is rejected with a message.")
        gapmod.defer(p, "GAP-002", "later", by_name="Priya Nair", by_role="Product Owner")  # MEDIUM
        gapmod.commit_analysis(p, True)
        basemod.create(p)
        base = p.read_json("baselines/baseline-v1.json")
        self.assertEqual([r["requirement_id"] for r in base["requirements"] if r["baseline_status"] == "BLOCKED"], ["REQ-008"],
                         "strict default: even MEDIUM unresolved gaps block until the carry-forward policy is accepted")
        self.assertEqual(code_of(gapmod.set_policy, p, True, "", "", "I accept"), "IDENTITY_REQUIRED")
        gapmod.set_policy(p, True, "Priya Nair", "Product Owner", "I accept carrying medium gaps forward.")
        out = basemod.create(p, "policy accepted")
        self.assertFalse(out["reused"])
        self.assertEqual(out["version"], 2)
        base2 = p.read_json("baselines/baseline-v2.json")
        self.assertTrue(all(r["baseline_status"] == "READY" for r in base2["requirements"]))
        self.assertTrue(any(g["gap_id"] == "GAP-002" for g in base2["outstanding_gaps"]), "carried-forward gap is clearly marked")

    def test_baseline_versions_preserved_and_unchanged_inputs_reused(self):
        p = self.to_baseline()
        again = basemod.create(p)
        self.assertTrue(again["reused"], "unchanged inputs must not regenerate the baseline")
        self.assertEqual(len(p.baseline_manifest()["versions"]), 1)
        gapmod.add_gaps(p, [dict(self.gap_items()[0], title="New one", question="What about weekends?", priority="LOW",
                                 priority_rationale="Minor", impacted_requirement_ids=["REQ-004"], material=False)])
        self.assertTrue(p.baseline_manifest()["stale"])
        self.assertIn(code_of(stories.add_stories, p, [self.story_item()]), ("BASELINE_STALE", "INVALID_STAGE"), "no stories while gaps reopened")
        self.set_owner(p, "Priya Nair", "Product Owner")
        gapmod.next_gaps(p, 5)
        self.answer_and_resolve(p, "GAP-004", "Weekends count.", "Weekends count towards the 14 days.")
        gapmod.commit_analysis(p, True)
        out = basemod.create(p)
        self.assertEqual(out["version"], 2)
        self.assertTrue(p.exists("baselines/baseline-v1.md") and p.exists("baselines/baseline-v2.md"))
        self.assertEqual(p.baseline_manifest()["versions"][0]["status"], "APPROVED", "v1 stays approved until v2 is confirmed")
        basemod.confirm(p, 2, "Priya Nair", "Product Owner", "ok")
        self.assertEqual(p.baseline_manifest()["versions"][0]["status"], "SUPERSEDED")
        self.assertEqual(code_of(basemod.confirm, p, 1, "Priya Nair", "Product Owner", "ok"), "BASELINE_NOT_CONFIRMABLE")


class StoryReviewTests(BATest):
    def test_approve_writes_only_approved_version(self):
        p = self.to_stories()
        self.assertFalse(list((p.root / "stories/approved").glob("*")))
        out = stories.approve(p, "US-001", 1, "Looks good, approved.", "Priya Nair", "Product Owner")
        self.assertEqual(out["status"], "APPROVED")
        self.assertTrue((p.root / "stories/approved/US-001.md").exists())
        self.assertFalse((p.root / "stories/candidates/US-001.json").exists())
        self.assertTrue((p.root / "stories/candidates/US-002.json").exists(), "unreviewed story must not be approved")
        self.assertEqual([x.name for x in (p.root / "stories/approved").glob("*.json")], ["US-001.json"])
        a = p.read_json("stories/approved/US-001.json")
        self.assertEqual(a["approval"]["version"], 1)
        self.assertEqual(a["approval"]["statement"], "Looks good, approved.")
        self.assertRegex(a["approval"]["at_ist"], r"\+05:30$")
        self.assertIn("**Approved:** v1", (p.root / "stories/approved/US-001.md").read_text(encoding="utf-8"))
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])

    def test_presenting_a_story_never_approves_it(self):
        p = self.to_stories()
        out = stories.next_story(p)
        self.assertEqual(out["stories"][0]["status"], "IN_REVIEW")
        self.assertEqual(p.state["stage"], "WAITING_FOR_STORY_REVIEW")
        self.assertFalse(list((p.root / "stories/approved").glob("*.json")))

    def test_edit_candidate_creates_new_version_requiring_approval(self):
        """Test 9."""
        p = self.to_stories()
        stories.next_story(p)
        out = stories.edit(p, "US-001", {"title": "Register a new member account"}, "Clearer title requested by reviewer")
        self.assertEqual(out["new_version"], 2)
        s = p.read_json("stories/candidates/US-001.json")
        self.assertEqual((s["version"], s["status"], s["supersedes_version"]), (2, "CANDIDATE", 1))
        self.assertTrue((p.root / "stories/archive/US-001.v1.json").exists())
        self.assertEqual(code_of(stories.approve, p, "US-001", 1, "approve"), "VERSION_MISMATCH", "old version cannot be approved")
        stories.approve(p, "US-001", 2, "Approved v2")
        a = p.read_json("stories/approved/US-001.json")
        self.assertEqual(a["version"], 2)
        self.assertIsNone(a["approval"]["approver_name"], "approver identity recorded only if provided")

    def test_editing_approved_story_requires_reapproval_and_keeps_history(self):
        """Test 10."""
        p = self.to_stories()
        stories.approve(p, "US-001", 1, "approved v1", "Priya Nair", "Product Owner")
        out = stories.edit(p, "US-001", {"acceptance_criteria": [
            "Given a visitor with a valid email, when they register, then an account is created and a confirmation is queued.",
            "Given an existing email, when they register, then registration is refused."]}, "Add duplicate-email path")
        self.assertEqual(out["new_version"], 2)
        self.assertTrue(out["approved_version_still_in_force"])
        self.assertEqual(p.read_json("stories/approved/US-001.json")["version"], 1, "v1 remains the approved version until v2 is approved")
        self.assertEqual(p.read_json("stories/candidates/US-001.json")["status"], "CANDIDATE")
        stories.approve(p, "US-001", 2, "approved v2", "Priya Nair", "Product Owner")
        self.assertEqual(p.read_json("stories/approved/US-001.json")["version"], 2)
        arch = p.read_json("stories/archive/US-001.v1.json")
        self.assertEqual((arch["status"], arch["approval"]["version"]), ("SUPERSEDED", 1), "approved v1 preserved in history")
        self.assertFalse((p.root / "stories/candidates/US-001.json").exists())
        events = [e for e in p.audit_events() if e["event_type"] in ("STORY_APPROVED", "STORY_EDITED")]
        self.assertEqual([e["event_type"] for e in events], ["STORY_APPROVED", "STORY_EDITED", "STORY_APPROVED"])
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])

    def test_defer_and_revisit(self):
        p = self.to_stories()
        stories.defer(p, "US-002", 1, "Needs design input")
        self.assertTrue((p.root / "stories/deferred/US-002.json").exists())
        self.assertFalse((p.root / "stories/candidates/US-002.json").exists())
        self.assertFalse((p.root / "stories/approved/US-002.json").exists())
        self.assertEqual(p.read_json("stories/deferred/US-002.json")["status"], "DEFERRED")
        self.assertEqual(stories.next_story(p, deferred=True)["stories"][0]["story_id"], "US-002")
        stories.revisit(p, "US-002")
        self.assertEqual(p.read_json("stories/candidates/US-002.json")["status"], "CANDIDATE")
        stories.approve(p, "US-002", 1, "ok now")
        self.assertTrue((p.root / "stories/approved/US-002.md").exists())

    def test_duplicate_story_rejected(self):
        p = self.to_stories()
        self.assertEqual(code_of(stories.add_stories, p, [self.story_item("register as a MEMBER")]), "STORIES_REJECTED")
        with self.assertRaises(BAError) as c:
            stories.add_stories(p, [self.story_item("Register as a member")])
        self.assertIn("duplicates US-001", json.dumps(c.exception.details))

    def test_approval_blocked_by_open_gap_on_linked_requirement(self):
        p = self.to_stories()
        gapmod.add_gaps(p, [dict(self.gap_items()[0], title="Email rules", question="Are disposable email domains allowed?",
                                 priority="LOW", priority_rationale="Minor", impacted_requirement_ids=["REQ-001"], material=False)])
        # the new gap flags the existing story (it must name the gap) and stales the baseline: approval is refused
        self.assertEqual(code_of(stories.approve, p, "US-001", 1, "ok"), "IMPACT_REVIEW_PENDING")
        self.assertIn("GAP-004", p.read_json("stories/candidates/US-001.json")["impact_review"]["reason"])
        self.assertTrue(p.baseline_manifest()["stale"])


class CoverageAndCompletionTests(BATest):
    def _story_for(self, rid, i):
        return self.story_item(f"Deliver {rid} outcome {i}", (rid,),
                               user_story=dict(role="Member", capability=f"to have {rid} delivered", value=f"outcome {i} is met"),
                               acceptance_criteria=[f"Given the system is running, when {rid} is exercised, then the documented outcome {i} is observed."])

    def test_full_completion_only_when_everything_approved(self):
        p = self.to_baseline()
        rids = p.req_ids()
        stories.add_stories(p, [self._story_for(r, i) for i, r in enumerate(rids)])
        t = checks.traceability(p, write=True)
        self.assertEqual(t["uncovered_by_any_story"], [])
        self.assertEqual(len(t["not_covered_by_approved_story"]), 8)
        self.assertTrue((p.root / "reports/traceability-report.md").exists())
        out = checks.complete_workflow(p)
        self.assertFalse(out["complete"], out)
        self.assertNotEqual(p.state["stage"], "COMPLETED")
        for n in range(1, 9):
            stories.approve(p, f"US-{n:03d}", 1, "approve", "Priya Nair", "Product Owner")
        out = checks.complete_workflow(p)
        self.assertTrue(out["complete"], out)
        self.assertEqual(p.state["stage"], "COMPLETED")
        self.assertIn("COMPLETE", (p.root / "reports/workflow-summary.md").read_text(encoding="utf-8"))
        # a COMPLETED workflow reopens when a story is edited
        stories.edit(p, "US-001", {"title": "Deliver REQ-001 outcome 0 (revised)"}, "tweak")
        self.assertEqual(p.state["stage"], "WAITING_FOR_STORY_REVIEW")
        self.assertFalse(checks.completion(p)["complete"])

    def test_uncovered_requirements_listed(self):
        p = self.to_baseline()
        stories.add_stories(p, [self._story_for("REQ-001", 1)])
        t = checks.traceability(p)
        self.assertEqual(t["uncovered_by_any_story"], [f"REQ-{n:03d}" for n in range(2, 9)])

    def test_orphan_dangling_and_duplicate_detection(self):
        """Test 15."""
        p = self.to_baseline()
        stories.add_stories(p, [self._story_for("REQ-001", 1)])
        s = p.read_json("stories/candidates/US-001.json")
        orphan = dict(s, story_id="US-050", title="Orphan story", requirement_ids=["REQ-777"])
        dupe = dict(s, story_id="US-051", title=s["title"].upper())
        for rec in (orphan, dupe):
            p.write_json(f"stories/candidates/{rec['story_id']}.json", rec)
        t = checks.traceability(p)
        self.assertIn("US-050", t["orphan_stories"])
        self.assertIn({"from": "US-050", "to": "REQ-777"}, t["dangling_references"])
        self.assertEqual(t["duplicate_stories"], [["US-001", "US-051"]])
        v = checks.validate_project(p)
        self.assertFalse(v["ok"])
        self.assertTrue(any("REQ-777" in e for e in v["errors"]))

    def test_validate_detects_tampering(self):
        p = self.to_baseline()
        self.assertTrue(checks.validate_project(p)["ok"])
        gaps = p.read_json("gaps/gap-register.json")
        gaps["gaps"][0]["closed_by_name"] = None
        p.write_json("gaps/gap-register.json", gaps)
        v = checks.validate_project(p)
        self.assertFalse(v["ok"])
        self.assertTrue(any("missing closure metadata" in e for e in v["errors"]))

    def test_resolved_without_audit_event_is_detected_and_repaired_by_recover(self):
        p = self.with_gaps()
        gaps = p.read_json("gaps/gap-register.json")
        g = gaps["gaps"][0]
        g.update(status="RESOLVED", decision_id="DEC-001", resolution="x", decision_owner_name="A B", decision_owner_role="PO",
                 closed_by_name="A B", closed_by_role="PO", closed_at_ist="2026-10-22T18:45:00+05:30", closure_summary="x",
                 closure_validation={"status": "PASSED", "validated_at_ist": "2026-10-22T18:45:00+05:30"})
        g["history"].append({"from": "OPEN", "to": "RESOLVED", "at_ist": "2026-10-22T18:45:00+05:30"})
        p.write_json("gaps/gap-register.json", gaps)
        v = checks.validate_project(p)
        self.assertFalse(v["ok"])
        out = checks.recover(p)
        self.assertEqual(out["gaps_reopened"], ["GAP-001"])
        self.assertEqual(p.gap("GAP-001")["status"], "REOPENED")


if __name__ == "__main__":
    unittest.main()
