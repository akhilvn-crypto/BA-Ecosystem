"""Simplified gap resolution: Other / Explain / Answer Later / Defer This Gap, with validated resolution only."""
import json
import subprocess
import sys
import unittest
from datetime import timedelta
from pathlib import Path

from tests.helpers import BATest, FakeClock, CLOCK_START, checks, gapmod, projmod, util
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


class Boom(RuntimeError):
    pass


class Base(BATest):
    def ready(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        return p

    def audit(self, p, event_type):
        return [e for e in p.audit_events() if e["event_type"] == event_type]


class PromptOptionTests(Base):
    def test_packet_options_depend_on_whether_the_gap_may_be_carried_forward(self):
        p = self.with_gaps()
        self.set_owner(p)
        by_id = {g["gap_id"]: g for g in gapmod.next_gaps(p, 3)["gaps"]}
        # HIGH / material gaps keep the original four options and cannot be carried forward
        for gid in ("GAP-001", "GAP-003"):
            self.assertEqual(by_id[gid]["options"], ["Other (type an answer or an instruction)", "Explain This Question",
                                                     "Answer Later", "Defer This Gap"], gid)
            self.assertFalse(by_id[gid]["carry_forward_allowed"])
            self.assertTrue(by_id[gid]["carry_forward_not_allowed_because"])
        # a MEDIUM, non-material gap without evidence-backed suggestion: Other / Explain / Carry Forward
        self.assertEqual(by_id["GAP-002"]["options"], ["Other (type an answer or an instruction)", "Explain This Question",
                                                       "Carry Forward"])
        self.assertTrue(by_id["GAP-002"]["carry_forward_allowed"])

    def test_suggested_answer_only_when_evidence_backed(self):
        p = self.with_requirements()
        items = self.gap_items()
        items[0]["suggested_answer"] = {"answer": "R10 per day, max R200", "source": "BRD 3.2 and REQ-006"}
        items[1]["suggested_answer"] = {"answer": "3 seconds"}  # no source
        self.assertEqual(code_of(gapmod.add_gaps, p, items), "GAPS_REJECTED")
        items[1]["suggested_answer"] = {"answer": "3 seconds", "source": "see REQ-099"}  # unknown artifact
        self.assertEqual(code_of(gapmod.add_gaps, p, items), "GAPS_REJECTED")
        del items[1]["suggested_answer"]
        gapmod.add_gaps(p, items)
        self.set_owner(p)
        by_id = {g["gap_id"]: g for g in gapmod.next_gaps(p, 3)["gaps"]}
        self.assertEqual(by_id["GAP-001"]["suggested_answer"], {"answer": "R10 per day, max R200", "source": "BRD 3.2 and REQ-006"})
        self.assertIsNone(by_id["GAP-002"]["suggested_answer"])
        self.assertEqual(p.gap("GAP-001")["answers"], [], "a suggestion is never recorded as an answer")

    def test_packet_flags_blocking_and_revisit_requirement(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.set_policy(p, None, *OWNER, "Revisit dates are mandatory for high priority.", ["HIGH", "CRITICAL"])
        out = gapmod.next_gaps(p, 3)
        by_id = {g["gap_id"]: g for g in out["gaps"]}
        self.assertTrue(by_id["GAP-001"]["blocks_stories"] and by_id["GAP-001"]["defer_requires_revisit_date"])
        self.assertTrue(by_id["GAP-002"]["blocks_stories"] and not by_id["GAP-002"]["defer_requires_revisit_date"],
                        "strict default blocks MEDIUM too")
        self.assertEqual(sorted(out["high_priority_outstanding"]), ["GAP-001", "GAP-003"])


class AnswerLaterTests(Base):
    def test_answer_later_keeps_gap_open_without_a_reason(self):
        p = self.ready()
        out = gapmod.answer_later(p, "GAP-001")
        g = p.gap("GAP-001")
        self.assertEqual((g["status"], g["skipped_count"], out["blocks_stories"]), ("OPEN", 1, True))
        self.assertIsNone(g["deferral"])
        ev = self.audit(p, "GAP_ANSWER_LATER")[0]
        self.assertRegex(ev["at_ist"], r"\+05:30$")
        self.assertEqual(g["history"][-1]["event_id"], ev["event_id"])
        self.assertIn("GAP-001", checks.status_report(p)["gaps"]["answer_later"])
        self.assertIn("GAP-001", p.state["pending_gap_ids"])
        self.assertIsNone(code_of(gapmod.skip, p, "GAP-001"), "`skip` remains an alias")
        self.assertEqual(p.gap("GAP-001")["skipped_count"], 2)

    def test_answer_later_gap_is_asked_again_and_high_priority_first_in_each_round(self):
        p = self.ready()
        gapmod.answer_later(p, "GAP-001")
        order = [g["gap_id"] for g in gapmod.next_gaps(p, 10)["gaps"]]
        self.assertEqual(order[-1], "GAP-001", "returns after the rest of the round")
        self.assertLess(order.index("GAP-003"), order.index("GAP-002"), "HIGH before MEDIUM within the round")
        gapmod.answer_later(p, "GAP-002")
        gapmod.answer_later(p, "GAP-003")
        self.assertEqual(gapmod.next_gaps(p, 1)["gaps"][0]["gap_id"], "GAP-001", "a new round restarts with the highest priority")

    def test_cannot_answer_later_a_deferred_or_resolved_gap(self):
        p = self.ready()
        gapmod.defer(p, "GAP-002", "Waiting for performance team", by_name=OWNER[0], by_role=OWNER[1])
        self.assertEqual(code_of(gapmod.answer_later, p, "GAP-002"), "INVALID_TRANSITION")
        self.answer_and_resolve(p, "GAP-003", "Reject with message.", "A 6th borrow is rejected with a message.")
        self.assertEqual(code_of(gapmod.answer_later, p, "GAP-003"), "INVALID_TRANSITION")


class DeferTests(Base):
    def test_defer_requires_reason_actor_and_decision_owner(self):
        p = self.with_gaps()
        self.assertEqual(code_of(gapmod.defer, p, "GAP-001", "waiting", by_name=OWNER[0], by_role=OWNER[1]), "OWNER_REQUIRED")
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        for reason in (None, "", "   "):
            self.assertEqual(code_of(gapmod.defer, p, "GAP-001", reason, by_name=OWNER[0], by_role=OWNER[1]), "REASON_REQUIRED")
        self.assertEqual(code_of(gapmod.defer, p, "GAP-001", "waiting"), "IDENTITY_REQUIRED")
        self.assertEqual(code_of(gapmod.defer, p, "GAP-001", "waiting", by_name="Claude", by_role="AI"), "IDENTITY_REQUIRED")
        self.assertEqual(p.gap("GAP-001")["status"], "WAITING_FOR_ANSWER", "nothing changed by the refused attempts")
        self.assertEqual(self.audit(p, "GAP_DEFERRED"), [])

    def test_defer_records_actor_owner_time_reason_and_status(self):
        p = self.ready()
        out = gapmod.defer(p, "GAP-001", "  Waiting for   Finance sign-off ", owner="Finance team", revisit_by="2026-11-05",
                           by_name="Sam Dsouza", by_role="Business Analyst")
        g = Project.open(p.root).gap("GAP-001")
        d = g["deferral"]
        self.assertEqual(g["status"], "DEFERRED")
        self.assertEqual((d["reason"], d["by_name"], d["by_role"], d["owner"], d["revisit_by"]),
                         ("Waiting for Finance sign-off", "Sam Dsouza", "Business Analyst", "Finance team", "2026-11-05"))
        self.assertEqual((d["decision_owner_name"], d["decision_owner_role"]), OWNER)
        self.assertRegex(d["at_ist"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+05:30$")
        self.assertEqual(d["timezone"], "Asia/Kolkata")
        ev = self.audit(p, "GAP_DEFERRED")[0]
        self.assertEqual((ev["actor"]["name"], ev["decision_owner"]["name"], ev["reason"], ev["at_ist"]),
                         ("Sam Dsouza", "Priya Nair", "Waiting for Finance sign-off", d["at_ist"]))
        self.assertEqual(g["history"][-1]["to"], "DEFERRED")
        self.assertTrue(out["blocks_stories"])
        self.assertIn("Sam Dsouza", (p.root / "gaps/md/GAP-001.md").read_text(encoding="utf-8"))
        self.assertTrue(checks.validate_project(p)["ok"])

    def test_deferred_is_never_resolved_and_high_priority_still_blocks(self):
        p = self.ready()
        gapmod.defer(p, "GAP-001", "Waiting for Finance", by_name=OWNER[0], by_role=OWNER[1])
        gapmod.defer(p, "GAP-002", "Waiting for performance team", by_name=OWNER[0], by_role=OWNER[1])
        self.assertEqual(code_of(gapmod.resolve, p, "GAP-001", "A1", "x", *OWNER, "x", "x"), "NOT_PRESENTED")
        self.assertEqual(gapmod.blocked_requirements(p)["REQ-006"], ["GAP-001"])
        self.assertIn("GAP-001", checks.status_report(p)["gaps"]["blocking_unresolved"])
        self.assertNotIn("GAP-001", [g["gap_id"] for g in gapmod.next_gaps(p, 10)["gaps"]])

    def test_defer_twice_or_after_resolution_is_refused(self):
        p = self.ready()
        gapmod.defer(p, "GAP-002", "later", by_name=OWNER[0], by_role=OWNER[1])
        self.assertEqual(code_of(gapmod.defer, p, "GAP-002", "again", by_name=OWNER[0], by_role=OWNER[1]), "ALREADY_DEFERRED")
        self.answer_and_resolve(p, "GAP-003", "Reject with message.", "A 6th borrow is rejected with a message.")
        self.assertEqual(code_of(gapmod.defer, p, "GAP-003", "later", by_name=OWNER[0], by_role=OWNER[1]), "INVALID_TRANSITION")

    def test_revisit_date_validation_and_policy(self):
        p = self.ready()
        d = lambda **k: code_of(gapmod.defer, p, "GAP-001", "waiting", by_name=OWNER[0], by_role=OWNER[1], **k)
        self.assertEqual(d(revisit_by="05/11/2026"), "BAD_INPUT")
        self.assertEqual(d(revisit_by="2026-10-01"), "BAD_INPUT", "date in the past (IST)")
        self.assertEqual(p.gap("GAP-001")["status"], "WAITING_FOR_ANSWER")
        gapmod.set_policy(p, None, *OWNER, "Revisit date is mandatory for HIGH gaps.", ["HIGH"])
        self.assertEqual(d(), "REVISIT_DATE_REQUIRED")
        self.assertEqual(code_of(gapmod.set_policy, p, None, *OWNER, "x", ["URGENT"]), "BAD_INPUT")
        self.assertIsNone(d(revisit_by="2026-11-05"))
        self.assertIsNone(code_of(gapmod.defer, p, "GAP-002", "no date needed for MEDIUM", by_name=OWNER[0], by_role=OWNER[1]))
        self.assertTrue(any(e["event_type"] == "POLICY_CHANGED" and e.get("deferral_revisit_required_priorities") == ["HIGH"]
                            for e in p.audit_events()))

    def test_due_deferrals_are_surfaced_and_revisit_keeps_history(self):
        p = self.ready()
        gapmod.defer(p, "GAP-001", "Waiting for Finance", revisit_by="2026-10-25", by_name=OWNER[0], by_role=OWNER[1])
        self.assertEqual(gapmod.next_gaps(p, 1)["deferred_due_for_revisit"], [])
        util.set_clock(FakeClock(CLOCK_START + timedelta(days=4)))
        self.assertEqual([x["gap_id"] for x in gapmod.next_gaps(p, 1)["deferred_due_for_revisit"]], ["GAP-001"])
        self.assertIn("DUE FOR REVISIT", checks.status_text(checks.status_report(p)))
        gapmod.revisit(p, "GAP-001")
        g = p.gap("GAP-001")
        self.assertEqual((g["status"], g["deferral"], len(g["deferral_history"])), ("WAITING_FOR_ANSWER", None, 1))
        self.assertEqual(g["deferral_history"][0]["reason"], "Waiting for Finance")
        gapmod.defer(p, "GAP-001", "Still waiting", by_name=OWNER[0], by_role=OWNER[1])
        self.assertEqual(len(self.audit(p, "GAP_DEFERRED")), 2, "both deferrals stay in the audit trail")
        self.assertIn("Earlier deferral", (p.root / "gaps/md/GAP-001.md").read_text(encoding="utf-8"))


class ValidatedResolutionTests(Base):
    def test_conflicting_free_text_answer_cannot_resolve_until_clarified(self):
        p = self.ready()
        a1 = gapmod.record_answer(p, "GAP-003", "Allow at most 8 books at the same time instead.")
        ids = [c["artifact_id"] for c in a1["candidate_conflicts"]]
        self.assertIn("REQ-003", ids)
        self.assertTrue(any(i.startswith("REQ-003/BRD") for i in ids), "the BRD excerpt is checked too")

        def resolve(aid, **k):
            return gapmod.resolve(p, "GAP-003", aid, "A member may borrow up to 8 books.", *OWNER, "x", "Yes.", **k)
        self.assertEqual(code_of(resolve, a1["answer_id"]), "CONFLICT_REVIEW_REQUIRED")
        gapmod.record_conflict(p, "GAP-003", a1["answer_id"], ["REQ-003"], "REQ-003 says at most 5; the answer says 8.")
        self.assertEqual(code_of(resolve, a1["answer_id"]), "CONFLICT_UNRESOLVED")
        self.assertEqual(p.gap("GAP-003")["status"], "WAITING_FOR_ANSWER")
        a2 = gapmod.record_answer(p, "GAP-003", "Sorry, keep the limit of 5; a 6th borrow is rejected with a message.")
        gapmod.resolve(p, "GAP-003", a2["answer_id"], "A 6th concurrent borrow is rejected with a message.", *OWNER, "Clarified.",
                       "Yes.", reviewed_ids=[c["artifact_id"] for c in a2["candidate_conflicts"]])
        g = p.gap("GAP-003")
        self.assertEqual(g["status"], "RESOLVED")
        self.assertEqual([c["status"] for c in g["conflicts"]], ["CLARIFIED"])
        self.assertEqual(g["answers"][0]["response"], "Allow at most 8 books at the same time instead.", "raw answer preserved")

    def test_incomplete_answer_cannot_resolve(self):
        p = self.ready()
        a = gapmod.record_answer(p, "GAP-001", "Something small.")
        gapmod.followup(p, "GAP-001", a["answer_id"], "Amount and maximum missing")
        self.assertEqual(code_of(gapmod.resolve, p, "GAP-001", a["answer_id"], "x", *OWNER, "x", "x"), "ANSWER_INCOMPLETE")
        self.assertEqual(p.gap("GAP-001")["status"], "WAITING_FOR_ANSWER")

    def test_material_or_compliance_gap_blocks_even_when_non_blocking_policy_accepted(self):
        p = self.ready()
        gapmod.set_policy(p, True, *OWNER, "Carrying non-blocking gaps is acceptable.")
        blocked = gapmod.blocked_requirements(p)
        self.assertIn("REQ-006", blocked, "HIGH + material")
        self.assertIn("REQ-003", blocked, "HIGH")
        self.assertNotIn("REQ-008", blocked, "MEDIUM and not material: policy applies as before")
        gaps = p.gaps()
        next(x for x in gaps if x["gap_id"] == "GAP-002").update(material=True)
        p.save_gaps(gaps, touched=["GAP-002"])
        self.assertIn("REQ-008", gapmod.blocked_requirements(p), "a material (compliance-sensitive) gap always blocks")


class InterruptionTests(Base):
    def _crash_at_status_save(self, action):
        p = self.ready()

        def hook(name):
            if name == "after_gap_status_saved":
                raise Boom(name)
        projmod.FAULT_HOOK = hook
        with self.assertRaises(Boom):
            action(p)
        projmod.FAULT_HOOK = None
        return Project.open(p.root)

    def test_deferral_survives_interruption_and_recover_restores_the_audit_event(self):
        p = self._crash_at_status_save(lambda p: gapmod.defer(
            p, "GAP-001", "Waiting for Finance", revisit_by="2026-11-05", by_name="Sam Dsouza", by_role="Business Analyst"))
        g = p.gap("GAP-001")
        self.assertEqual((g["status"], g["deferral"]["by_name"], g["deferral"]["reason"]), ("DEFERRED", "Sam Dsouza", "Waiting for Finance"))
        self.assertEqual(self.audit(p, "GAP_DEFERRED"), [], "the crash happened before the audit append")
        v = checks.validate_project(p)
        self.assertTrue(any("is missing — run `recover`" in w for w in v["warnings"]), v["warnings"])
        out = checks.recover(p)
        ev = self.audit(p, "GAP_DEFERRED")
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["event_id"], ev[0]["at_ist"], ev[0]["actor"]["name"], ev[0]["decision_owner"]["name"], ev[0]["recovered"]),
                         (g["history"][-1]["event_id"], g["deferral"]["at_ist"], "Sam Dsouza", "Priya Nair", True))
        self.assertEqual(len(out["audit_backfilled"]), 1)
        checks.recover(p)
        self.assertEqual(len(self.audit(p, "GAP_DEFERRED")), 1, "recover is idempotent")
        self.assertFalse(any("is missing" in w for w in checks.validate_project(p)["warnings"]))

    def test_answer_later_survives_interruption(self):
        p = self._crash_at_status_save(lambda p: gapmod.answer_later(p, "GAP-002"))
        self.assertEqual((p.gap("GAP-002")["status"], p.gap("GAP-002")["skipped_count"]), ("OPEN", 1))
        checks.recover(p)
        self.assertEqual(len(self.audit(p, "GAP_ANSWER_LATER")), 1)

    def test_state_and_answers_are_preserved_when_reopened_in_a_new_session(self):
        p = self.ready()
        gapmod.record_answer(p, "GAP-003", "Reject the 6th borrow.")
        gapmod.answer_later(p, "GAP-001")
        gapmod.defer(p, "GAP-002", "waiting on perf team", by_name=OWNER[0], by_role=OWNER[1])
        q = Project.open(p.root)
        self.assertEqual([g["status"] for g in q.gaps()], ["OPEN", "DEFERRED", "WAITING_FOR_ANSWER"])
        self.assertEqual(q.gap("GAP-003")["answers"][0]["response"], "Reject the 6th borrow.")
        self.assertEqual(q.state["pending_gap_ids"], ["GAP-001", "GAP-002", "GAP-003"])
        self.assertTrue(checks.validate_project(q)["ok"])


class CliTests(BATest):
    def run_ba(self, root, *args, ok=True):
        r = subprocess.run([sys.executable, BA, "--root", str(root), *args], capture_output=True, text=True, encoding="utf-8")
        out = json.loads(r.stdout)
        self.assertEqual(out["ok"], ok, r.stdout)
        return out

    def test_cli_defer_answer_later_and_policy(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        err = self.run_ba(p.root, "gap", "defer", "GAP-001", ok=False)
        self.assertEqual(err["error"]["code"], "REASON_REQUIRED")
        err = self.run_ba(p.root, "gap", "defer", "GAP-001", "--reason", "Waiting for Finance", ok=False)
        self.assertEqual(err["error"]["code"], "IDENTITY_REQUIRED")
        self.run_ba(p.root, "policy", "set", "--deferral-revisit-required", "CRITICAL,HIGH", "--by-name", OWNER[0],
                    "--by-role", OWNER[1], "--statement", "Mandatory for high priority.")
        err = self.run_ba(p.root, "gap", "defer", "GAP-001", "--reason", "Waiting for Finance", "--by-name", "Sam Dsouza",
                          "--by-role", "BA", ok=False)
        self.assertEqual(err["error"]["code"], "REVISIT_DATE_REQUIRED")
        out = self.run_ba(p.root, "gap", "defer", "GAP-001", "--reason", "Waiting for Finance", "--by-name", "Sam Dsouza",
                          "--by-role", "BA", "--revisit-by", "2099-01-01")
        self.assertEqual(out["status"], "DEFERRED")
        self.assertEqual(self.run_ba(p.root, "gap", "answer-later", "GAP-002")["status"], "OPEN")
        self.assertEqual(self.run_ba(p.root, "gap", "skip", "GAP-003")["skipped_count"], 1)
        err = self.run_ba(p.root, "policy", "set", "--by-name", OWNER[0], "--by-role", OWNER[1], "--statement", "x", ok=False)
        self.assertEqual(err["error"]["code"], "BAD_INPUT")
        self.assertTrue(self.run_ba(p.root, "validate")["ok"])


if __name__ == "__main__":
    unittest.main()
