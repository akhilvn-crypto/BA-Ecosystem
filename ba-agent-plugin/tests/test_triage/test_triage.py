"""/ba-agent:triage-gaps and /ba-agent:triage-stories: the listing queues and the state transitions they drive.

The commands add no business logic of their own - they list (`gaps triage`, `story triage`), present one item
(`gaps next --id`, `story next --id`) and then call the existing actions. These tests pin that list/present layer and
check that every decision made through it keeps the audit trail, traceability, versioning and resume behaviour.
"""
import json
import subprocess
import sys
import unittest
from datetime import timedelta
from pathlib import Path

from tests.helpers import BATest, CLOCK_START, FakeClock, checks, gapmod, option_labels, stories, util
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


def ids(rows, key="gap_id"):
    return [r[key] for r in rows]


class Base(BATest):
    def events(self, p, kind):
        return [e for e in p.audit_events() if e["event_type"] == kind]

    def run_ba(self, root, *args, ok=True):
        r = subprocess.run([sys.executable, BA, "--root", str(root), *args], capture_output=True, text=True, encoding="utf-8")
        out = json.loads(r.stdout)
        self.assertEqual(out["ok"], ok, r.stdout)
        return out


class GapBase(Base):
    def seeded(self):
        """GAP-001 HIGH (material), GAP-002 MEDIUM, GAP-003 HIGH. Owner set, nothing presented yet."""
        p = self.with_gaps()
        self.set_owner(p)
        return p


class GapTriageListTests(GapBase):
    def test_lists_open_and_deferred_gaps_with_priority_reason_owner_and_links(self):
        p = self.seeded()
        gapmod.next_gaps(p, 3)
        gapmod.defer(p, "GAP-001", "Waiting for Finance", owner="Finance team", revisit_by="2099-01-01",
                     by_name="Sam Dsouza", by_role="Business Analyst")
        out = gapmod.triage(p)
        self.assertEqual(ids(out["gaps"]), ["GAP-003", "GAP-001", "GAP-002"], "HIGH before MEDIUM")
        self.assertEqual(out["counts"], {"OPEN": 2, "DEFERRED": 1, "carried_forward": 0, "blocking": 3})
        d = next(r for r in out["gaps"] if r["gap_id"] == "GAP-001")
        self.assertEqual((d["status"], d["triage_group"], d["priority"]), ("DEFERRED", "DEFERRED", "HIGH"))
        self.assertEqual(d["deferral"], {"reason": "Waiting for Finance", "by_name": "Sam Dsouza", "by_role": "Business Analyst",
                                         "at_ist": d["deferral"]["at_ist"], "revisit_by": "2099-01-01", "waiting_on": "Finance team"})
        self.assertTrue(d["deferral"]["at_ist"].endswith("+05:30"))
        self.assertNotIn("decision_owner", d, "the owner is reported once at the top, not on every row")
        self.assertEqual(d["requirement_ids"], ["REQ-006"])
        self.assertTrue(d["blocks_stories"])
        self.assertEqual(out["decision_owner"], {"name": OWNER[0], "role": OWNER[1]})

    def test_linked_stories_are_listed_for_each_gap(self):
        p = self.to_stories()  # US-001 -> REQ-001, US-002 -> REQ-002
        gapmod.add_gaps(p, [dict(self.gap_items()[0], title="Confirmation e-mail wording", question="What must the e-mail say?",
                                 impacted_requirement_ids=["REQ-002"], priority="LOW", material=False)])
        row = gapmod.triage(p)["gaps"][0]
        self.assertEqual((row["gap_id"], row["story_ids"], row["requirement_ids"]), ("GAP-004", ["US-002"], ["REQ-002"]))

    def test_resolved_gaps_are_not_listed_and_the_status_filter_works(self):
        p = self.seeded()
        gapmod.next_gaps(p, 3)
        self.answer_and_resolve(p, "GAP-003", "Reject the 6th borrow with a message.", "A 6th concurrent borrow is rejected with a message.")
        gapmod.defer(p, "GAP-002", "perf team", by_name=OWNER[0], by_role=OWNER[1])
        self.assertEqual(ids(gapmod.triage(p)["gaps"]), ["GAP-001", "GAP-002"])
        self.assertEqual(ids(gapmod.triage(p, "open")["gaps"]), ["GAP-001"])
        self.assertEqual(ids(gapmod.triage(p, "DEFERRED")["gaps"]), ["GAP-002"])
        self.assertEqual(code_of(gapmod.triage, p, "RESOLVED"), "BAD_INPUT")

    def test_open_group_covers_presented_answer_later_and_carried_forward_gaps(self):
        p = self.seeded()
        gapmod.next_gaps(p, 3)  # all WAITING_FOR_ANSWER
        gapmod.answer_later(p, "GAP-001")
        gapmod.carry_forward(p, "GAP-002", *OWNER, reason="not needed for the first release")
        out = gapmod.triage(p)
        rows = {r["gap_id"]: r for r in out["gaps"]}
        self.assertEqual({k: (v["status"], v["triage_group"]) for k, v in rows.items()},
                         {"GAP-001": ("OPEN", "OPEN"), "GAP-002": ("OPEN", "OPEN"), "GAP-003": ("WAITING_FOR_ANSWER", "OPEN")})
        self.assertTrue(rows["GAP-002"]["carried_forward"])
        self.assertFalse(rows["GAP-002"]["blocks_stories"])
        self.assertEqual(rows["GAP-001"]["skipped_count"], 1)
        self.assertEqual(out["counts"]["carried_forward"], 1)

    def test_deferred_gap_due_for_revisit_is_flagged(self):
        p = self.seeded()
        gapmod.next_gaps(p, 3)
        gapmod.defer(p, "GAP-001", "waiting", revisit_by="2026-10-23", by_name=OWNER[0], by_role=OWNER[1])
        self.assertFalse(next(r for r in gapmod.triage(p)["gaps"] if r["gap_id"] == "GAP-001")["due_for_revisit"])
        util.set_clock(FakeClock(CLOCK_START + timedelta(days=3)))
        self.assertTrue(next(r for r in gapmod.triage(p)["gaps"] if r["gap_id"] == "GAP-001")["due_for_revisit"])

    def test_listing_is_read_only_and_works_before_the_owner_is_known(self):
        p = self.with_gaps()
        before = (json.dumps(p.gaps(), sort_keys=True), len(p.audit_events()), p.state["stage"])
        out = gapmod.triage(p)
        self.assertEqual((ids(out["gaps"]), out["decision_owner"]), (["GAP-001", "GAP-003", "GAP-002"], None))
        self.assertIn("decision owner", out["next"])
        self.assertEqual((json.dumps(p.gaps(), sort_keys=True), len(p.audit_events()), p.state["stage"]), before)

    def test_empty_queue(self):
        p = self.with_gaps()
        self.resolve_all(p)
        out = gapmod.triage(p)
        self.assertEqual((out["gaps"], out["total"], out["next"]), ([], 0, "No OPEN or DEFERRED gaps."))


class GapTriageActionTests(GapBase):
    def test_presenting_an_open_gap_marks_it_waiting_and_requires_the_owner(self):
        p = self.with_gaps()
        self.assertEqual(code_of(gapmod.next_gaps, p, 1, False, "GAP-002"), "OWNER_REQUIRED")
        self.set_owner(p)
        pk = gapmod.next_gaps(p, 1, gap_id="GAP-002")["gaps"][0]
        self.assertEqual((pk["gap_id"], pk["status"]), ("GAP-002", "WAITING_FOR_ANSWER"))
        self.assertEqual(p.gap("GAP-002")["status"], "WAITING_FOR_ANSWER")
        self.assertEqual(p.gap("GAP-001")["status"], "OPEN", "only the chosen gap is presented")
        self.assertEqual(p.state["stage"], "WAITING_FOR_GAP_INPUT")
        self.assertEqual(self.events(p, "GAP_PRESENTED")[-1]["gap_ids"], ["GAP-002"])
        self.assertIn("Carry Forward", option_labels(pk))

    def test_viewing_a_deferred_gap_changes_nothing(self):
        p = self.seeded()
        gapmod.next_gaps(p, 3)
        gapmod.defer(p, "GAP-001", "Waiting for Finance", by_name=OWNER[0], by_role=OWNER[1])
        snap = (json.dumps(p.gaps(), sort_keys=True), len(p.audit_events()))
        pk = gapmod.next_gaps(p, 1, gap_id="GAP-001")["gaps"][0]
        self.assertEqual((json.dumps(p.gaps(), sort_keys=True), len(p.audit_events())), snap)
        self.assertEqual((pk["status"], pk["revisit_needed_to_answer"]), ("DEFERRED", True))
        self.assertEqual(option_labels(pk), ["Explain This Question", "Keep Deferred"])
        self.assertIn("Waiting for Finance", pk["present"]["card"], "the deferral history is on the card")
        self.assertFalse(pk["present"]["inline"], "a gap with history needs its card")
        self.assertEqual(pk["impacted_requirement_ids"], ["REQ-006"])

    def test_viewing_a_carried_forward_gap_changes_nothing_and_offers_keep(self):
        p = self.seeded()
        gapmod.next_gaps(p, 3)
        gapmod.carry_forward(p, "GAP-002", *OWNER)
        snap = json.dumps(p.gaps(), sort_keys=True)
        pk = gapmod.next_gaps(p, 1, gap_id="GAP-002")["gaps"][0]
        self.assertEqual(json.dumps(p.gaps(), sort_keys=True), snap)
        self.assertTrue(pk["revisit_needed_to_answer"])
        self.assertEqual(option_labels(pk)[-1], "Keep Carried Forward")

    def test_unknown_and_resolved_gaps_cannot_be_presented(self):
        p = self.seeded()
        gapmod.next_gaps(p, 3)
        self.assertEqual(code_of(gapmod.next_gaps, p, 1, False, "GAP-099"), "UNKNOWN_GAP")
        self.answer_and_resolve(p, "GAP-003", "Reject the 6th borrow.", "A 6th borrow is rejected.")
        self.assertEqual(code_of(gapmod.next_gaps, p, 1, False, "GAP-003"), "INVALID_TRANSITION")

    def test_keep_on_an_open_gap_is_answer_later_and_it_stays_in_the_queue(self):
        p = self.seeded()
        gapmod.next_gaps(p, 1, gap_id="GAP-001")
        out = gapmod.keep(p, "GAP-001")
        self.assertEqual((out["status"], out["skipped_count"]), ("OPEN", 1))
        self.assertIn("GAP-001", ids(gapmod.triage(p)["gaps"]))
        self.assertEqual(len(self.events(p, "GAP_ANSWER_LATER")), 1)

    def test_keep_on_a_deferred_or_carried_gap_leaves_it_unchanged_and_audits_the_decision(self):
        p = self.seeded()
        gapmod.next_gaps(p, 3)
        gapmod.defer(p, "GAP-001", "Waiting for Finance", by_name=OWNER[0], by_role=OWNER[1])
        gapmod.carry_forward(p, "GAP-002", *OWNER)
        d, c = gapmod.keep(p, "GAP-001"), gapmod.keep(p, "GAP-002")
        self.assertEqual((d["status"], d["kept_as"], c["status"], c["kept_as"]), ("DEFERRED", "DEFERRED", "OPEN", "CARRIED_FORWARD"))
        self.assertEqual(p.gap("GAP-001")["deferral"]["reason"], "Waiting for Finance")
        self.assertIsNotNone(p.gap("GAP-002")["carry_forward"])
        kept = self.events(p, "GAP_TRIAGE_KEPT")
        self.assertEqual([(e["gap_id"], e["kept_as"]) for e in kept], [("GAP-001", "DEFERRED"), ("GAP-002", "CARRIED_FORWARD")])
        self.assertTrue(all(e["at_ist"].endswith("+05:30") for e in kept))
        self.assertEqual(code_of(gapmod.keep, p, "GAP-099"), "UNKNOWN_GAP")

    def test_defer_from_triage_needs_reason_actor_and_is_not_resolution(self):
        p = self.seeded()
        gapmod.next_gaps(p, 1, gap_id="GAP-003")
        self.assertEqual(code_of(gapmod.defer, p, "GAP-003", "", None, None, OWNER[0], OWNER[1]), "REASON_REQUIRED")
        self.assertEqual(code_of(gapmod.defer, p, "GAP-003", "legal review"), "IDENTITY_REQUIRED")
        out = gapmod.defer(p, "GAP-003", "legal review", by_name="Sam Dsouza", by_role="Business Analyst")
        self.assertEqual(out["status"], "DEFERRED")
        self.assertTrue(out["blocks_stories"], "deferred HIGH gaps still block")
        row = next(r for r in gapmod.triage(p)["gaps"] if r["gap_id"] == "GAP-003")
        self.assertEqual((row["triage_group"], row["deferral"]["by_name"]), ("DEFERRED", "Sam Dsouza"))
        self.assertEqual(self.events(p, "GAP_DEFERRED")[-1]["actor"], {"name": "Sam Dsouza", "role": "Business Analyst"})

    def test_answering_a_deferred_gap_needs_a_revisit_and_keeps_the_deferral_history(self):
        p = self.seeded()
        gapmod.next_gaps(p, 3)
        gapmod.defer(p, "GAP-003", "Waiting for the policy team", by_name=OWNER[0], by_role=OWNER[1])
        self.assertEqual(code_of(gapmod.record_answer, p, "GAP-003", "Reject the 6th borrow."), "NOT_PRESENTED")
        gapmod.revisit(p, "GAP-003")
        self.assertEqual(p.gap("GAP-003")["status"], "WAITING_FOR_ANSWER")
        a = gapmod.record_answer(p, "GAP-003", "Reject the 6th borrow with a limit-reached message.")
        gapmod.resolve(p, "GAP-003", a["answer_id"], "A 6th concurrent borrow is rejected with a limit-reached message.", *OWNER,
                       "Answer settles the over-limit behaviour.", "Yes, close it.",
                       reviewed_ids=[c["artifact_id"] for c in a["candidate_conflicts"]])
        g = p.gap("GAP-003")
        self.assertEqual((g["status"], g["decision_id"]), ("RESOLVED", "DEC-001"))
        self.assertEqual(g["deferral_history"][0]["reason"], "Waiting for the policy team")
        self.assertNotIn("GAP-003", ids(gapmod.triage(p)["gaps"]))
        trail = [e["event_type"] for e in p.audit_events() if e.get("gap_id") == "GAP-003"]
        for expected in ("GAP_DEFERRED", "GAP_REVISITED", "GAP_ANSWER_RECEIVED", "GAP_RESOLVED"):
            self.assertIn(expected, trail)
        self.assertTrue(checks.validate_project(p)["ok"])

    def test_conflicting_answer_given_in_triage_cannot_resolve_the_gap(self):
        p = self.seeded()
        gapmod.next_gaps(p, 3)
        gapmod.defer(p, "GAP-003", "waiting", by_name=OWNER[0], by_role=OWNER[1])
        gapmod.revisit(p, "GAP-003")
        a = gapmod.record_answer(p, "GAP-003", "Allow at most 8 books at the same time instead.")
        self.assertIn("REQ-003", [c["artifact_id"] for c in a["candidate_conflicts"]])
        self.assertEqual(code_of(gapmod.resolve, p, "GAP-003", a["answer_id"], "Up to 8 books.", *OWNER, "x", "Yes."),
                         "CONFLICT_REVIEW_REQUIRED")
        gapmod.record_conflict(p, "GAP-003", a["answer_id"], ["REQ-003"], "REQ-003 says at most 5; the answer says 8.")
        self.assertEqual(code_of(gapmod.resolve, p, "GAP-003", a["answer_id"], "Up to 8 books.", *OWNER, "x", "Yes.",
                                 reviewed_ids=["REQ-003"]), "CONFLICT_UNRESOLVED")
        self.assertEqual(p.gap("GAP-003")["status"], "WAITING_FOR_ANSWER")
        self.assertIn("GAP-003", ids(gapmod.triage(p)["gaps"]), "still on the triage list")
        self.assertEqual(p.gap("GAP-003")["conflicts"][0]["status"], "OPEN")

    def test_carry_forward_from_triage_follows_the_existing_rules(self):
        p = self.seeded()
        gapmod.next_gaps(p, 3)
        self.assertEqual(code_of(gapmod.carry_forward, p, "GAP-001", *OWNER), "CARRY_FORWARD_NOT_ALLOWED")
        self.assertEqual(gapmod.carry_forward(p, "GAP-002", *OWNER)["status"], "OPEN")

    def test_triage_survives_a_new_session(self):
        p = self.seeded()
        gapmod.next_gaps(p, 3)
        gapmod.defer(p, "GAP-001", "Waiting for Finance", by_name=OWNER[0], by_role=OWNER[1])
        gapmod.answer_later(p, "GAP-002")
        q = Project.open(p.root)
        self.assertEqual(gapmod.triage(q), gapmod.triage(p))
        self.assertEqual(checks.status_report(q)["gaps"]["deferred"], ["GAP-001"])


class StoryBase(Base):
    def three_buckets(self):
        """US-001 pending review, US-002 not approved, US-003 deferred, US-004 approved."""
        p = self.to_baseline()
        stories.add_stories(p, [
            self.story_item("Register as a member", ("REQ-001",)),
            self.story_item("Receive a confirmation email", ("REQ-002",),
                            user_story=dict(role="New member", capability="to receive a confirmation email", value="I know it worked"),
                            acceptance_criteria=["Given a completed registration, when the account is created, then a confirmation email is sent."]),
            self.story_item("Limit concurrent borrows", ("REQ-003",),
                            user_story=dict(role="Member", capability="to borrow up to five books", value="I can read more"),
                            acceptance_criteria=["Given a member with five loans, when they borrow a sixth book, then it is rejected with a message."]),
            self.story_item("Record borrow dates", ("REQ-005",),
                            user_story=dict(role="Librarian", capability="loan dates are recorded", value="I can see what is due"),
                            acceptance_criteria=["Given a loan, when it is created, then the borrow and due dates are stored."]),
        ])
        stories.next_story(p, "US-002")
        stories.request_changes(p, "US-002", 1, "criteria too vague")
        stories.next_story(p, "US-003")
        stories.defer(p, "US-003", 1, "waiting for the policy team")
        stories.next_story(p, "US-004")
        stories.approve(p, "US-004", 1, "Approved as written.", *OWNER)
        return p


class StoryTriageListTests(StoryBase):
    def test_lists_pending_not_approved_and_deferred_but_never_approved_stories(self):
        p = self.three_buckets()
        out = stories.triage(p)
        self.assertEqual([(r["story_id"], r["status"], r["bucket"]) for r in out["stories"]],
                         [("US-001", "CANDIDATE", "candidates"), ("US-002", "CHANGES_REQUESTED", "unapproved"),
                          ("US-003", "DEFERRED", "deferred")])
        self.assertEqual(out["counts"], {"pending_review": 1, "not_approved": 1, "deferred": 1})
        self.assertEqual(out["eligible"], ["US-001", "US-002", "US-003"])
        by = {r["story_id"]: r for r in out["stories"]}
        self.assertEqual(by["US-002"]["last_review_note"]["note"], "criteria too vague")
        self.assertEqual(by["US-002"]["bucket"], "unapproved")
        self.assertEqual(by["US-003"]["deferral"]["reason"], "waiting for the policy team")
        self.assertTrue(by["US-003"]["deferral"]["at_ist"].endswith("+05:30"))
        self.assertEqual(by["US-001"]["requirement_ids"], ["REQ-001"])
        self.assertEqual(by["US-001"]["readiness"], "READY_FOR_DEVELOPMENT")

    def test_in_review_stories_come_first_and_the_listing_changes_nothing(self):
        p = self.three_buckets()
        stories.next_story(p, "US-001")  # CANDIDATE -> IN_REVIEW
        snap = (json.dumps([s for _, s in p.all_stories()], sort_keys=True), len(p.audit_events()), p.state["stage"])
        out = stories.triage(p)
        self.assertEqual(out["stories"][0]["status"], "IN_REVIEW")
        self.assertEqual((json.dumps([s for _, s in p.all_stories()], sort_keys=True), len(p.audit_events()), p.state["stage"]), snap)

    def test_story_with_a_blocking_gap_or_impact_flag_is_listed_but_not_eligible(self):
        p = self.to_baseline()
        stories.add_stories(p, [self.story_item("Charge a late fine", ("REQ-006",),
                                                user_story=dict(role="Librarian", capability="late returns are fined", value="revenue is protected"),
                                                acceptance_criteria=["Given a late return, when it is processed, then a fine is charged."])])
        stories.next_story(p)
        stories.defer(p, "US-001", 1, "unsure about the fine")
        self.assertEqual(stories.triage(p)["eligible"], ["US-001"])
        gapmod.reopen(p, "GAP-001", "Finance changed the fine rule", *OWNER)
        out = stories.triage(p)
        row = out["stories"][0]
        self.assertFalse(row["eligible"])
        self.assertEqual(out["not_eligible"], ["US-001"])
        self.assertTrue(any("GAP-001" in b for b in row["blockers"]), row["blockers"])
        self.assertEqual([x["id"] for x in out["preflight"]], ["baseline_stale"], "shared problems are reported once, not per row")
        self.assertFalse(any("baseline" in b for b in row["blockers"]), row["blockers"])
        self.assertEqual(row["status"], "DEFERRED", "deferred stories stay available for later review")

    def test_empty_queue(self):
        p = self.to_baseline()
        out = stories.triage(p)
        self.assertEqual((out["stories"], out["total"], out["next"]), ([], 0, "No unapproved stories."))


class StoryTriageActionTests(StoryBase):
    def test_a_chosen_story_can_be_presented_from_any_pending_folder(self):
        p = self.three_buckets()
        for sid, status in (("US-001", "IN_REVIEW"), ("US-002", "CHANGES_REQUESTED"), ("US-003", "DEFERRED")):
            out = stories.next_story(p, sid)
            self.assertEqual([(s["story_id"], s["status"]) for s in out["stories"]], [(sid, status)])
        self.assertEqual(code_of(stories.next_story, p, "US-099"), "UNKNOWN_STORY")
        self.assertTrue(p.exists("stories/unapproved/json/US-002.json") and p.exists("stories/deferred/json/US-003.json"),
                        "presenting does not move not-approved or deferred stories")

    def test_approve_a_pending_story(self):
        p = self.three_buckets()
        stories.next_story(p, "US-001")
        out = stories.approve(p, "US-001", 1, "Approved, looks right.", *OWNER)
        self.assertEqual((out["status"], out["location"]), ("APPROVED", "stories/approved/md/US-001.md"))
        self.assertNotIn("US-001", ids(stories.triage(p)["stories"], "story_id"))
        self.assertEqual(self.events(p, "STORY_APPROVED")[-1]["statement"], "Approved, looks right.")

    def test_approving_a_deferred_story_goes_through_revisit_and_is_audited(self):
        p = self.three_buckets()
        self.assertEqual(code_of(stories.approve, p, "US-003", 1, "approve"), "STORY_DEFERRED")
        stories.revisit(p, "US-003")
        self.assertEqual(p.read_json("stories/candidates/json/US-003.json")["status"], "CANDIDATE")
        self.assertFalse(p.exists("stories/deferred/json/US-003.json"))
        stories.next_story(p, "US-003")
        stories.approve(p, "US-003", 1, "Policy team confirmed; approve.", *OWNER)
        s = p.read_json("stories/approved/json/US-003.json")
        self.assertEqual(s["approval"]["version"], 1)
        self.assertEqual([h["action"] for h in s["review_history"]][-4:], ["DEFERRED", "REVISITED", "PRESENTED", "APPROVED"])
        self.assertEqual(self.events(p, "STORY_REVISITED")[-1]["from_status"], "DEFERRED")
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p))

    def test_a_not_approved_story_can_be_reconsidered_and_approved_without_an_edit(self):
        p = self.three_buckets()
        self.assertEqual(code_of(stories.approve, p, "US-002", 1, "approve"), "STORY_UNAPPROVED")
        self.assertEqual(stories.revisit(p, "US-002")["status"], "CANDIDATE")
        self.assertFalse(p.exists("stories/unapproved/json/US-002.json"))
        stories.approve(p, "US-002", 1, "On reflection it is fine; approve.", *OWNER)
        self.assertTrue(p.exists("stories/approved/json/US-002.json"))
        self.assertEqual(self.events(p, "STORY_REVISITED")[-1]["from_status"], "CHANGES_REQUESTED")
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p))

    def test_edit_returns_the_story_for_approval_as_a_new_version(self):
        p = self.three_buckets()
        for sid in ("US-001", "US-002", "US-003"):
            out = stories.edit(p, sid, {"title": f"Reworded {sid} title"}, "user suggested a clearer title")
            self.assertEqual((out["new_version"], out["status"], out["requires_approval"]), (2, "CANDIDATE", True))
            self.assertTrue(p.exists(f"stories/candidates/json/{sid}.json"))
            self.assertTrue(p.exists(f"stories/archive/json/{sid}.v1.json"), "the earlier version is kept")
            self.assertFalse(p.exists(f"stories/approved/json/{sid}.json"), "an edit is never an approval")
        for bucket in ("unapproved", "deferred"):
            self.assertEqual(list(p.path(f"stories/{bucket}").glob("US-*.json")), [])
        self.assertEqual([(r["story_id"], r["version"], r["status"]) for r in stories.triage(p)["stories"]],
                         [("US-001", 2, "CANDIDATE"), ("US-002", 2, "CANDIDATE"), ("US-003", 2, "CANDIDATE")])
        self.assertEqual(code_of(stories.approve, p, "US-001", 1, "approve"), "VERSION_MISMATCH", "approval names the exact version")
        edited = self.events(p, "STORY_EDITED")
        self.assertEqual(len(edited), 3)
        self.assertTrue(all(e["requires_reapproval"] for e in edited))

    def test_editing_a_story_does_not_replace_its_approved_version(self):
        p = self.three_buckets()
        out = stories.edit(p, "US-004", {"title": "Record loan dates and due dates"}, "clearer title")
        self.assertTrue(out["approved_version_still_in_force"])
        self.assertEqual(p.read_json("stories/approved/json/US-004.json")["version"], 1)
        self.assertEqual(ids(stories.triage(p)["stories"], "story_id"), ["US-001", "US-004", "US-002", "US-003"])

    def test_defer_a_pending_or_not_approved_story_and_it_stays_available(self):
        p = self.three_buckets()
        stories.next_story(p, "US-001")
        stories.defer(p, "US-001", 1, "need the product owner's input")
        self.assertEqual(stories.defer(p, "US-002", 1, "park until the wording is agreed")["status"], "DEFERRED")
        for sid in ("US-001", "US-002"):
            self.assertTrue(p.exists(f"stories/deferred/json/{sid}.json") and p.exists(f"stories/deferred/md/{sid}.md"))
        self.assertFalse(p.exists("stories/unapproved/json/US-002.json"))
        self.assertEqual(self.events(p, "STORY_DEFERRED")[-1]["from_status"], "CHANGES_REQUESTED")
        rows = {r["story_id"]: r for r in stories.triage(p)["stories"]}
        self.assertEqual([rows[s]["status"] for s in ("US-001", "US-002", "US-003")], ["DEFERRED"] * 3)
        self.assertEqual(rows["US-002"]["deferral"]["reason"], "park until the wording is agreed")
        self.assertEqual(code_of(stories.defer, p, "US-001", 1, "again"), "UNKNOWN_STORY", "already deferred")
        self.assertEqual(code_of(stories.defer, p, "US-004", 1, "x"), "UNKNOWN_STORY", "an approved story is not deferred here")
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p))

    def test_deferred_story_returns_to_the_review_queue_and_stays_pending_for_completion(self):
        p = self.three_buckets()
        self.assertIn("US-003", p.state["pending_story_ids"])
        self.assertFalse(checks.completion(p)["complete"])
        stories.revisit(p, "US-003")
        out = stories.triage(p)
        self.assertEqual((ids(out["stories"], "story_id"), out["counts"]["deferred"]), (["US-001", "US-003", "US-002"], 0))

    def test_keep_leaves_any_pending_story_unchanged_and_audits_the_decision(self):
        p = self.three_buckets()
        before = json.dumps([s for _, s in p.all_stories()], sort_keys=True)
        for sid, status in (("US-001", "CANDIDATE"), ("US-002", "CHANGES_REQUESTED"), ("US-003", "DEFERRED")):
            self.assertEqual(stories.keep(p, sid)["status"], status)
        self.assertEqual(json.dumps([s for _, s in p.all_stories()], sort_keys=True), before)
        kept = self.events(p, "STORY_TRIAGE_KEPT")
        self.assertEqual([(e["story_id"], e["status"]) for e in kept],
                         [("US-001", "CANDIDATE"), ("US-002", "CHANGES_REQUESTED"), ("US-003", "DEFERRED")])
        self.assertEqual(code_of(stories.keep, p, "US-004"), "UNKNOWN_STORY", "approved stories are not in the triage queue")

    def test_audit_and_review_history_cover_every_decision(self):
        p = self.three_buckets()
        stories.revisit(p, "US-003")
        stories.edit(p, "US-003", {"title": "Limit concurrent borrows to five"}, "tidy title")
        stories.next_story(p, "US-003")
        stories.approve(p, "US-003", 2, "Approved v2.", *OWNER)
        trail = [e["event_type"] for e in p.audit_events() if e.get("story_id") == "US-003"]
        self.assertEqual(trail, ["STORY_DEFERRED", "STORY_REVISITED", "STORY_EDITED", "STORY_APPROVED"])
        hist = [h["action"] for h in p.read_json("stories/approved/json/US-003.json")["review_history"]]
        self.assertEqual(hist, ["GENERATED", "PRESENTED", "DEFERRED", "REVISITED", "EDITED", "PRESENTED", "APPROVED"])
        self.assertTrue(p.exists("stories/archive/json/US-003.v1.json"))

    def test_traceability_and_resume(self):
        p = self.three_buckets()
        stories.revisit(p, "US-003")
        q = Project.open(p.root)
        self.assertEqual(stories.triage(q), stories.triage(p))
        self.assertTrue(checks.traceability(q)["ok"])
        self.assertEqual(checks.validate_project(q)["errors"], [])

    def test_presenting_a_story_starts_story_review(self):
        p = self.three_buckets()
        stories.next_story(p, "US-001")
        self.assertEqual(p.state["stage"], "WAITING_FOR_STORY_REVIEW")


class CliTests(StoryBase):
    def test_gaps_triage_next_id_keep_and_defer_via_the_cli(self):
        p = self.with_gaps()
        self.set_owner(p)
        out = self.run_ba(p.root, "gaps", "triage")
        self.assertEqual((ids(out["gaps"]), out["counts"]["OPEN"]), (["GAP-001", "GAP-003", "GAP-002"], 3))
        self.assertEqual(self.run_ba(p.root, "gaps", "triage", "--status", "DEFERRED")["gaps"], [])
        pk = self.run_ba(p.root, "gaps", "next", "--id", "GAP-002")["gaps"][0]
        self.assertEqual((pk["gap_id"], pk["status"]), ("GAP-002", "WAITING_FOR_ANSWER"))
        self.assertEqual(self.run_ba(p.root, "gap", "keep", "GAP-002")["skipped_count"], 1)
        self.run_ba(p.root, "gaps", "next", "--id", "GAP-001")
        self.run_ba(p.root, "gap", "defer", "GAP-001", "--reason", "Waiting for Finance", "--by-name", "Sam Dsouza", "--by-role", "BA")
        kept = self.run_ba(p.root, "gap", "keep", "GAP-001")
        self.assertEqual((kept["status"], kept["kept_as"]), ("DEFERRED", "DEFERRED"))
        self.assertEqual(self.run_ba(p.root, "gaps", "triage", "--status", "DEFERRED")["gaps"][0]["deferral"]["reason"], "Waiting for Finance")
        self.assertEqual(self.run_ba(p.root, "gaps", "next", "--id", "GAP-404", ok=False)["error"]["code"], "UNKNOWN_GAP")
        self.assertTrue(self.run_ba(p.root, "validate")["ok"])

    def test_story_triage_next_id_revisit_and_defer_via_the_cli(self):
        p = self.three_buckets()
        out = self.run_ba(p.root, "story", "triage")
        self.assertEqual((ids(out["stories"], "story_id"), out["counts"]["deferred"]), (["US-001", "US-002", "US-003"], 1))
        self.assertEqual(self.run_ba(p.root, "story", "next", "--id", "US-003")["stories"][0]["status"], "DEFERRED")
        self.assertEqual(self.run_ba(p.root, "story", "revisit", "US-002")["status"], "CANDIDATE")
        self.assertEqual(self.run_ba(p.root, "story", "defer", "US-002", "--version", "1", "--reason", "later")["status"], "DEFERRED")
        self.assertEqual(self.run_ba(p.root, "story", "triage")["counts"], {"pending_review": 1, "not_approved": 0, "deferred": 2})
        self.assertEqual(self.run_ba(p.root, "story", "keep", "US-003")["status"], "DEFERRED")


if __name__ == "__main__":
    unittest.main()
