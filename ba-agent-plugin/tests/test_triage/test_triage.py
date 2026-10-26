"""/ba-agent:triage-gaps and /ba-agent:triage-ac: the listing queues and the state transitions they drive.

The commands add no business logic of their own - they list (`gaps triage`, `ac triage`), present one item
(`gaps next --id`, `ac next --key`) and then call the existing actions. These tests pin that list/present layer and
check that every decision made through it keeps the audit trail, traceability, versioning and resume behaviour.
"""
import json
import subprocess
import sys
import unittest
from datetime import timedelta
from pathlib import Path

from tests.helpers import BATest, CLOCK_START, FakeClock, acs, checks, gapmod, key, option_labels, projmod, util
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
        self.assertEqual(d["req_keys"], [key("LIB-006")])
        self.assertTrue(d["blocks_ac"])
        self.assertEqual(out["decision_owner"], {"name": OWNER[0], "role": OWNER[1]})

    def test_linked_acceptance_criteria_are_listed_for_each_gap(self):
        p = self.to_acs(2)  # criteria for LIB-001 and LIB-002
        item = dict(self.gap_items()[0], title="Confirmation e-mail wording", question="What must the e-mail say?",
                    impacted_req_keys=["LIB-002"], priority="LOW", material=False)
        item.pop("evidence_quote")
        gapmod.add_gaps(p, [item])
        row = gapmod.triage(p)["gaps"][0]
        self.assertEqual((row["gap_id"], row["ac_keys"], row["req_keys"]), ("GAP-004", [key("LIB-002")], [key("LIB-002")]))

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
        self.assertFalse(rows["GAP-002"]["blocks_ac"])
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
        self.assertEqual(pk["impacted_req_keys"], [key("LIB-006")])

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
        self.assertTrue(out["blocks_ac"], "deferred HIGH gaps still block")
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
        self.assertIn("LIB-003", [c["artifact_id"] for c in a["candidate_conflicts"]])
        self.assertEqual(code_of(gapmod.resolve, p, "GAP-003", a["answer_id"], "Up to 8 books.", *OWNER, "x", "Yes."),
                         "CONFLICT_REVIEW_REQUIRED")
        gapmod.record_conflict(p, "GAP-003", a["answer_id"], ["LIB-003"], "LIB-003 says at most 5; the answer says 8.")
        self.assertEqual(code_of(gapmod.resolve, p, "GAP-003", a["answer_id"], "Up to 8 books.", *OWNER, "x", "Yes.",
                                 reviewed_ids=["LIB-003"]), "CONFLICT_UNRESOLVED")
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


class AcBase(Base):
    def three_buckets(self):
        """LIB-001 pending review, LIB-002 not approved, LIB-003 deferred, LIB-005 approved."""
        p = self.to_ac_stage()
        acs.add_acs(p, [
            self.ac_item("LIB-001"),
            self.ac_item("LIB-002", acceptance_criteria=["Given a completed registration, when the account is created, then a confirmation email is sent."]),
            self.ac_item("LIB-003", acceptance_criteria=["Given a member with five loans, when they borrow a sixth book, then it is rejected with a message."]),
            self.ac_item("LIB-005", acceptance_criteria=["Given a loan, when it is created, then the borrow and due dates are stored."]),
        ])
        acs.next_ac(p, "LIB-002")
        acs.request_changes(p, "LIB-002", 1, "criteria too vague")
        acs.next_ac(p, "LIB-003")
        acs.defer(p, "LIB-003", 1, "waiting for the policy team")
        acs.next_ac(p, "LIB-005")
        acs.approve(p, "LIB-005", 1, "Approved as written.", *OWNER)
        return p


K1, K2, K3, K5, K6 = (key(r) for r in ("LIB-001", "LIB-002", "LIB-003", "LIB-005", "LIB-006"))


def fname(rid):
    return projmod.ac_fname(key(rid))


class AcTriageListTests(AcBase):
    def test_lists_pending_not_approved_and_deferred_but_never_approved_records(self):
        p = self.three_buckets()
        out = acs.triage(p)
        self.assertEqual([(r["req_key"], r["status"], r["bucket"]) for r in out["acs"]],
                         [(K1, "CANDIDATE", "candidates"), (K2, "CHANGES_REQUESTED", "unapproved"), (K3, "DEFERRED", "deferred")])
        self.assertEqual(out["counts"], {"pending_review": 1, "not_approved": 1, "deferred": 1})
        self.assertEqual(out["eligible"], [K1, K2, K3])
        by = {r["req_key"]: r for r in out["acs"]}
        self.assertEqual(by[K2]["last_review_note"]["note"], "criteria too vague")
        self.assertEqual(by[K2]["bucket"], "unapproved")
        self.assertEqual(by[K3]["deferral"]["reason"], "waiting for the policy team")
        self.assertTrue(by[K3]["deferral"]["at_ist"].endswith("+05:30"))
        self.assertEqual(by[K1]["label"], "LIB-001")
        self.assertEqual(by[K1]["readiness"], "READY_FOR_DEVELOPMENT")

    def test_in_review_records_come_first_and_the_listing_changes_nothing(self):
        p = self.three_buckets()
        acs.next_ac(p, "LIB-001")  # CANDIDATE -> IN_REVIEW
        snap = (json.dumps([a for _, a in p.all_acs()], sort_keys=True), len(p.audit_events()), p.state["stage"])
        out = acs.triage(p)
        self.assertEqual(out["acs"][0]["status"], "IN_REVIEW")
        self.assertEqual((json.dumps([a for _, a in p.all_acs()], sort_keys=True), len(p.audit_events()), p.state["stage"]), snap)

    def test_record_with_a_blocking_gap_or_impact_flag_is_listed_but_not_eligible(self):
        p = self.to_ac_stage()
        acs.add_acs(p, [self.ac_item("LIB-006", acceptance_criteria=["Given a late return, when it is processed, then a fine is charged."])])
        acs.next_ac(p)
        acs.defer(p, "LIB-006", 1, "unsure about the fine")
        self.assertEqual(acs.triage(p)["eligible"], [K6])
        gapmod.reopen(p, "GAP-001", "Finance changed the fine rule", *OWNER)
        out = acs.triage(p)
        row = out["acs"][0]
        self.assertFalse(row["eligible"])
        self.assertEqual(out["not_eligible"], [K6])
        self.assertTrue(any("GAP-001" in b for b in row["blockers"]), row["blockers"])
        self.assertTrue(any("impact review" in b for b in row["blockers"]), row["blockers"])
        self.assertEqual(out["preflight"], [], "nothing is shared by every record here")
        self.assertEqual(row["status"], "DEFERRED", "deferred records stay available for later review")

    def test_a_pending_source_change_is_reported_once_as_preflight(self):
        p = self.three_buckets()
        f = self.src / "membership.md"
        f.write_text(f.read_text(encoding="utf-8").replace("14 days", "21 days"), encoding="utf-8")
        out = acs.triage(p)
        self.assertEqual([x["id"] for x in out["preflight"]], ["source_change"])
        self.assertEqual(out["eligible"], [], "nothing is eligible while the change is unreviewed")
        self.assertEqual(out["eligible_after_preflight"], [K1, K2, K3])
        self.assertFalse(any("source" in b for r in out["acs"] for b in r["blockers"]), "per-row blockers stay row-specific")

    def test_empty_queue(self):
        p = self.to_ac_stage()
        out = acs.triage(p)
        self.assertEqual((out["acs"], out["total"], out["next"]), ([], 0, "No unapproved acceptance criteria."))


class AcTriageActionTests(AcBase):
    def test_a_chosen_record_can_be_presented_from_any_pending_folder(self):
        p = self.three_buckets()
        for rid, status in (("LIB-001", "IN_REVIEW"), ("LIB-002", "CHANGES_REQUESTED"), ("LIB-003", "DEFERRED")):
            out = acs.next_ac(p, rid)
            self.assertEqual([(x["req_key"], x["status"]) for x in out["acs"]], [(key(rid), status)])
        self.assertEqual(code_of(acs.next_ac, p, "LIB-099"), "UNKNOWN_REQUIREMENT")
        self.assertEqual(code_of(acs.next_ac, p, "LIB-004"), "UNKNOWN_AC")
        self.assertTrue(p.exists(f"acceptance/unapproved/{fname('LIB-002')}.json") and p.exists(f"acceptance/deferred/{fname('LIB-003')}.json"),
                        "presenting does not move not-approved or deferred records")
        view = acs.next_ac(p, "LIB-001")["acs"][0]["present"]
        self.assertIn("The system must send", acs.next_ac(p, "LIB-002")["acs"][0]["present"]["card"], "the card shows the requirement text")
        self.assertEqual(view["dialog"]["header"], "LIB-001")

    def test_approve_a_pending_record_and_it_appears_in_the_enriched_copy(self):
        p = self.three_buckets()
        acs.next_ac(p, "LIB-001")
        out = acs.approve(p, "LIB-001", 1, "Approved, looks right.", *OWNER)
        self.assertEqual((out["status"], out["location"]), ("APPROVED", f"acceptance/approved/md/{fname('LIB-001')}.md"))
        self.assertNotIn(K1, ids(acs.triage(p)["acs"], "req_key"))
        self.assertEqual(self.events(p, "AC_APPROVED")[-1]["statement"], "Approved, looks right.")
        t = self.enriched(p, "membership.md")
        self.assertIn("AC v1", t)
        self.assertIn("Approved by Priya Nair (Product Owner)", t)

    def test_approving_a_deferred_record_goes_through_revisit_and_is_audited(self):
        p = self.three_buckets()
        self.assertEqual(code_of(acs.approve, p, "LIB-003", 1, "approve"), "AC_DEFERRED")
        acs.revisit(p, "LIB-003")
        self.assertEqual(p.read_json(f"acceptance/candidates/{fname('LIB-003')}.json")["status"], "CANDIDATE")
        self.assertFalse(p.exists(f"acceptance/deferred/{fname('LIB-003')}.json"))
        acs.next_ac(p, "LIB-003")
        acs.approve(p, "LIB-003", 1, "Policy team confirmed; approve.", *OWNER)
        a = p.read_json(f"acceptance/approved/{fname('LIB-003')}.json")
        self.assertEqual(a["approval"]["version"], 1)
        self.assertEqual([h["action"] for h in a["review_history"]][-4:], ["DEFERRED", "REVISITED", "PRESENTED", "APPROVED"])
        self.assertEqual(self.events(p, "AC_REVISITED")[-1]["from_status"], "DEFERRED")
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p))

    def test_a_not_approved_record_can_be_reconsidered_and_approved_without_an_edit(self):
        p = self.three_buckets()
        self.assertEqual(code_of(acs.approve, p, "LIB-002", 1, "approve"), "AC_UNAPPROVED")
        self.assertEqual(acs.revisit(p, "LIB-002")["status"], "CANDIDATE")
        self.assertFalse(p.exists(f"acceptance/unapproved/{fname('LIB-002')}.json"))
        acs.approve(p, "LIB-002", 1, "On reflection it is fine; approve.", *OWNER)
        self.assertTrue(p.exists(f"acceptance/approved/{fname('LIB-002')}.json"))
        self.assertEqual(self.events(p, "AC_REVISITED")[-1]["from_status"], "CHANGES_REQUESTED")
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p))

    def test_edit_returns_the_record_for_approval_as_a_new_version(self):
        p = self.three_buckets()
        for rid in ("LIB-001", "LIB-002", "LIB-003"):
            crit = [f"Given a case for {rid}, when it is exercised, then the reworded outcome is observed."]
            out = acs.edit(p, rid, {"acceptance_criteria": crit}, "user suggested clearer criteria")
            self.assertEqual((out["new_version"], out["status"], out["requires_approval"]), (2, "CANDIDATE", True))
            self.assertTrue(p.exists(f"acceptance/candidates/{fname(rid)}.json"))
            self.assertTrue(p.exists(f"acceptance/archive/{fname(rid)}.v1.json"), "the earlier version is kept")
            self.assertFalse(p.exists(f"acceptance/approved/{fname(rid)}.json"), "an edit is never an approval")
        for bucket in ("unapproved", "deferred"):
            self.assertEqual(list(p.path(f"acceptance/{bucket}").glob("*.json")), [])
        self.assertEqual([(r["req_key"], r["version"], r["status"]) for r in acs.triage(p)["acs"]],
                         [(K1, 2, "CANDIDATE"), (K2, 2, "CANDIDATE"), (K3, 2, "CANDIDATE")])
        self.assertEqual(code_of(acs.approve, p, "LIB-001", 1, "approve"), "VERSION_MISMATCH", "approval names the exact version")
        edited = self.events(p, "AC_EDITED")
        self.assertEqual(len(edited), 3)
        self.assertTrue(all(e["requires_reapproval"] for e in edited))

    def test_editing_approved_criteria_does_not_replace_the_approved_version(self):
        p = self.three_buckets()
        out = acs.edit(p, "LIB-005", {"acceptance_criteria": ["Given a loan, when it is created, then the borrow date and the due date are both stored."]},
                       "clearer wording")
        self.assertTrue(out["approved_version_still_in_force"])
        self.assertEqual(p.read_json(f"acceptance/approved/{fname('LIB-005')}.json")["version"], 1)
        self.assertEqual(ids(acs.triage(p)["acs"], "req_key"), [K5, K1, K2, K3])
        self.assertNotIn("borrow date and the due date are both", self.enriched(p, "lending.md"), "the document keeps showing v1")

    def test_defer_a_pending_or_not_approved_record_and_it_stays_available(self):
        p = self.three_buckets()
        acs.next_ac(p, "LIB-001")
        acs.defer(p, "LIB-001", 1, "need the product owner's input")
        self.assertEqual(acs.defer(p, "LIB-002", 1, "park until the wording is agreed")["status"], "DEFERRED")
        for rid in ("LIB-001", "LIB-002"):
            self.assertTrue(p.exists(f"acceptance/deferred/{fname(rid)}.json") and p.exists(f"acceptance/deferred/{fname(rid)}.md"))
        self.assertFalse(p.exists(f"acceptance/unapproved/{fname('LIB-002')}.json"))
        self.assertEqual(self.events(p, "AC_DEFERRED")[-1]["from_status"], "CHANGES_REQUESTED")
        rows = {r["req_key"]: r for r in acs.triage(p)["acs"]}
        self.assertEqual([rows[k]["status"] for k in (K1, K2, K3)], ["DEFERRED"] * 3)
        self.assertEqual(rows[K2]["deferral"]["reason"], "park until the wording is agreed")
        self.assertEqual(code_of(acs.defer, p, "LIB-001", 1, "again"), "UNKNOWN_AC", "already deferred")
        self.assertEqual(code_of(acs.defer, p, "LIB-005", 1, "x"), "UNKNOWN_AC", "approved criteria are not deferred here")
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p))

    def test_deferred_record_returns_to_the_review_queue_and_stays_pending_for_completion(self):
        p = self.three_buckets()
        self.assertIn(K3, p.state["pending_ac_keys"])
        self.assertFalse(checks.completion(p)["complete"])
        acs.revisit(p, "LIB-003")
        out = acs.triage(p)
        self.assertEqual((ids(out["acs"], "req_key"), out["counts"]["deferred"]), ([K1, K3, K2], 0))

    def test_keep_leaves_any_pending_record_unchanged_and_audits_the_decision(self):
        p = self.three_buckets()
        before = json.dumps([a for _, a in p.all_acs()], sort_keys=True)
        for rid, status in (("LIB-001", "CANDIDATE"), ("LIB-002", "CHANGES_REQUESTED"), ("LIB-003", "DEFERRED")):
            self.assertEqual(acs.keep(p, rid)["status"], status)
        self.assertEqual(json.dumps([a for _, a in p.all_acs()], sort_keys=True), before)
        kept = self.events(p, "AC_TRIAGE_KEPT")
        self.assertEqual([(e["req_key"], e["status"]) for e in kept], [(K1, "CANDIDATE"), (K2, "CHANGES_REQUESTED"), (K3, "DEFERRED")])
        self.assertEqual(code_of(acs.keep, p, "LIB-005"), "UNKNOWN_AC", "approved criteria are not in the triage queue")

    def test_audit_and_review_history_cover_every_decision(self):
        p = self.three_buckets()
        acs.revisit(p, "LIB-003")
        acs.edit(p, "LIB-003", {"acceptance_criteria": ["Given a member with five loans, when they borrow a sixth, then it is rejected with a limit message."]}, "tidy wording")
        acs.next_ac(p, "LIB-003")
        acs.approve(p, "LIB-003", 2, "Approved v2.", *OWNER)
        trail = [e["event_type"] for e in p.audit_events() if e.get("req_key") == K3]
        self.assertEqual(trail, ["AC_DEFERRED", "AC_REVISITED", "AC_EDITED", "AC_APPROVED"])
        hist = [h["action"] for h in p.read_json(f"acceptance/approved/{fname('LIB-003')}.json")["review_history"]]
        self.assertEqual(hist, ["GENERATED", "PRESENTED", "DEFERRED", "REVISITED", "EDITED", "PRESENTED", "APPROVED"])
        self.assertTrue(p.exists(f"acceptance/archive/{fname('LIB-003')}.v1.json"))

    def test_traceability_and_resume(self):
        p = self.three_buckets()
        acs.revisit(p, "LIB-003")
        q = Project.open(p.root)
        self.assertEqual(acs.triage(q), acs.triage(p))
        self.assertTrue(checks.traceability(q)["ok"])
        self.assertEqual(checks.validate_project(q)["errors"], [])

    def test_presenting_a_record_starts_ac_review(self):
        p = self.to_acs(2)
        self.assertEqual(p.state["stage"], "AC_DRAFTING")
        acs.next_ac(p, "LIB-001")
        self.assertEqual(p.state["stage"], "WAITING_FOR_AC_REVIEW")


class CliTests(AcBase):
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

    def test_ac_triage_next_key_revisit_and_defer_via_the_cli(self):
        p = self.three_buckets()
        out = self.run_ba(p.root, "ac", "triage")
        self.assertEqual((ids(out["acs"], "req_key"), out["counts"]["deferred"]), ([K1, K2, K3], 1))
        self.assertEqual(self.run_ba(p.root, "ac", "next", "--key", "LIB-003")["acs"][0]["status"], "DEFERRED")
        self.assertEqual(self.run_ba(p.root, "ac", "revisit", "LIB-002")["status"], "CANDIDATE")
        self.assertEqual(self.run_ba(p.root, "ac", "defer", "LIB-002", "--version", "1", "--reason", "later")["status"], "DEFERRED")
        self.assertEqual(self.run_ba(p.root, "ac", "triage")["counts"], {"pending_review": 1, "not_approved": 0, "deferred": 2})
        self.assertEqual(self.run_ba(p.root, "ac", "keep", "LIB-003")["status"], "DEFERRED")
        self.assertEqual(self.run_ba(p.root, "ac", "next", "--key", "LIB-099", ok=False)["error"]["code"], "UNKNOWN_REQUIREMENT")
        self.assertEqual(self.run_ba(p.root, "validate")["errors"], [])


if __name__ == "__main__":
    unittest.main()
