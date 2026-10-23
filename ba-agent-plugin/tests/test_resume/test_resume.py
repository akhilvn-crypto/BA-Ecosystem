"""Resume, interruption, crash recovery, defer/reopen, write failures, source-change impact."""
import json
import unittest

from tests.helpers import BATest, basemod, checks, gapmod, projmod, reqs, stories, util
from ba_lib.project import Project
from ba_lib.util import BAError


def code_of(fn, *a, **k):
    try:
        fn(*a, **k)
    except BAError as e:
        return e.code
    return None


class Boom(RuntimeError):
    pass


class CrashRecoveryTests(BATest):
    """A crash at ANY point of gap closure must leave a recoverable, never-half-closed state."""

    POINTS = ["after_journal", "after_decision", "after_event", "before_register", "after_register"]

    def _crash_at(self, point):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        a = gapmod.record_answer(p, "GAP-003", "Reject the 6th borrow with a message.")

        def hook(name):
            if name == point:
                raise Boom(name)
        projmod.FAULT_HOOK = hook
        with self.assertRaises(Boom):
            gapmod.resolve(p, "GAP-003", a["answer_id"], "A 6th borrow is rejected with a message.", "Priya Nair", "Product Owner",
                           "Resolves the ambiguity.", "Yes, close it.")
        projmod.FAULT_HOOK = None
        return a

    def test_crash_at_every_point_is_recoverable_without_duplicates(self):
        for point in self.POINTS:
            with self.subTest(point=point):
                import shutil
                shutil.rmtree(self.tmp / "out", ignore_errors=True)
                self._crash_at(point)
                p = Project.open(self.tmp / "out")  # simulates a new session after the crash
                v = checks.validate_project(p)
                self.assertFalse(v["ok"], "interrupted closure must be flagged, not silently accepted")
                self.assertTrue(any("recover" in e for e in v["errors"]), v["errors"])
                if point in ("after_journal", "after_decision", "after_event", "before_register"):
                    self.assertNotEqual(p.gap("GAP-003")["status"], "RESOLVED", "gap not resolved until the register commits")
                self.assertEqual(code_of(gapmod.resolve, p, "GAP-003", "A1", "interp text", "Priya Nair", "Product Owner", "s", "c"),
                                 "CLOSURE_IN_PROGRESS", "retrying must not create a second closure")
                out = checks.recover(p)
                self.assertEqual(out["closures_rolled_forward"], ["GAP-003"])
                g = p.gap("GAP-003")
                self.assertEqual(g["status"], "RESOLVED")
                self.assertEqual(g["decision_id"], "DEC-001")
                self.assertEqual(len([e for e in p.audit_events() if e["event_type"] == "GAP_RESOLVED"]), 1)
                self.assertEqual(len(p.decisions()), 1)
                v = checks.validate_project(p)
                self.assertTrue(v["ok"], v["errors"])
                again = checks.recover(p)  # idempotent
                self.assertEqual(again["closures_rolled_forward"], [])
                self.assertEqual(len(p.decisions()), 1)

    def test_write_failure_keeps_gap_pending_then_recover_completes(self):
        """Test 12 — destination becomes unwritable mid-closure."""
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        a = gapmod.record_answer(p, "GAP-003", "Reject with message.")
        real = projmod.atomic_write_text

        def failing(path, text):
            if "gap-register" in str(path):
                raise OSError("No space left on device")
            return real(path, text)
        projmod.atomic_write_text = failing
        try:
            with self.assertRaises(BAError) as c:
                gapmod.resolve(p, "GAP-003", a["answer_id"], "Rejected with message.", "Priya Nair", "Product Owner", "ok", "yes")
            self.assertEqual(c.exception.code, "WRITE_FAILED")
        finally:
            projmod.atomic_write_text = real
        self.assertTrue(any("OUTPUT_WRITE_FAILED" == e["event_type"] for e in p.audit_events()), "write failure audited")
        p = Project.open(self.tmp / "out")
        self.assertEqual(p.gap("GAP-003")["status"], "WAITING_FOR_ANSWER", "never reported resolved while the register write failed")
        checks.recover(p)
        self.assertEqual(p.gap("GAP-003")["status"], "RESOLVED")
        self.assertTrue(checks.validate_project(p)["ok"])

    def test_journal_write_failure_leaves_nothing_behind(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        a = gapmod.record_answer(p, "GAP-003", "Reject with message.")
        real = gapmod.atomic_write_text
        gapmod.atomic_write_text = lambda path, text: (_ for _ in ()).throw(OSError("read-only file system"))
        try:
            with self.assertRaises(BAError) as c:
                gapmod.resolve(p, "GAP-003", a["answer_id"], "Rejected with message.", "Priya Nair", "Product Owner", "ok", "yes")
        finally:
            gapmod.atomic_write_text = real
        self.assertEqual(c.exception.code, "WRITE_FAILED")
        self.assertEqual(p.gap("GAP-003")["status"], "WAITING_FOR_ANSWER")
        self.assertEqual(p.decisions(), {})
        self.assertTrue(checks.validate_project(p)["ok"])

    def test_init_into_unwritable_destination(self):
        blocker = self.tmp / "iamafile"
        blocker.write_text("x")
        with self.assertRaises(BAError) as c:
            Project.create(blocker / "sub", self.brd_copy())
        self.assertIn(c.exception.code, ("OUTPUT_NOT_WRITABLE", "BAD_ROOT"))
        self.assertFalse((blocker / "sub").exists())

    def test_approval_crash_is_repaired(self):
        p = self.to_stories()

        def hook(name):
            if name == "story_after_approved_write":
                raise Boom(name)
        projmod.FAULT_HOOK = hook
        with self.assertRaises(Boom):
            stories.approve(p, "US-001", 1, "approved", "Priya Nair", "Product Owner")
        projmod.FAULT_HOOK = None
        p = Project.open(self.tmp / "out")
        v = checks.validate_project(p)
        self.assertFalse(v["ok"])
        out = checks.recover(p)
        self.assertEqual(out["approvals_repaired"], ["US-001"])
        self.assertEqual(out["audit_backfilled"], ["US-001"])
        self.assertFalse((p.root / "stories/candidates/US-001.json").exists())
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])


class ResumeTests(BATest):
    def test_interrupted_qa_resumes_without_duplicate_decisions(self):
        """Test 11."""
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 2)
        gapmod.record_answer(p, "GAP-001", "R10/day", respondent_name="Priya Nair", respondent_role="Product Owner")
        del p  # session ends here
        p = Project.open(self.tmp / "out")
        self.assertEqual(p.state["stage"], "WAITING_FOR_GAP_INPUT")
        st = checks.status_report(p)
        self.assertEqual(st["gaps"]["by_status"], {"WAITING_FOR_ANSWER": 2, "OPEN": 1})
        self.assertIn("gap(s) await answers", st["next_action"])
        again = gapmod.next_gaps(p, 3)
        self.assertEqual(again["gaps"][0]["gap_id"], "GAP-001", "already-presented gaps come back first")
        self.assertEqual(again["gaps"][0]["previous_answers"][0]["response"], "R10/day")
        self.assertEqual(p.gap("GAP-001")["answers"][0]["respondent_name"], "Priya Nair")
        fu = gapmod.followup(p, "GAP-001", "A1", "Need a maximum and the grace period.")
        a2 = gapmod.record_answer(p, "GAP-001", "R10/day, max R200, no grace period.")
        self.assertEqual(code_of(gapmod.resolve, p, "GAP-001", "A1", "Fine R10/day.", "Priya Nair", "Product Owner", "s", "c"), "ANSWER_INCOMPLETE")
        gapmod.resolve(p, "GAP-001", a2["answer_id"], "Fine is R10 per day, max R200, no grace period.", "Priya Nair", "Product Owner", "Complete answer.", "Yes")
        g = p.gap("GAP-001")
        self.assertEqual([a["answer_id"] for a in g["answers"]], ["A1", "A2"], "incomplete answer retained in history")
        self.assertEqual(g["answers"][0]["assessment"], "INCOMPLETE")
        self.assertEqual(len(p.decisions()), 1)

    def test_defer_and_resume_later(self):
        """Test 5."""
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        gapmod.defer(p, "GAP-002", "Need performance team input", owner="Perf team", revisit_by="2026-11-05", by_name="Sam Dsouza", by_role="BA")
        self.assertEqual(code_of(gapmod.revisit, Project.open(self.tmp / "out"), "GAP-001"), "INVALID_TRANSITION")
        p = Project.open(self.tmp / "out")
        g = p.gap("GAP-002")
        self.assertEqual((g["status"], g["deferral"]["reason"], g["deferral"]["owner"], g["deferral"]["revisit_by"]),
                         ("DEFERRED", "Need performance team input", "Perf team", "2026-11-05"))
        self.assertRegex(g["deferral"]["at_ist"], r"\+05:30$")
        self.assertNotIn("GAP-002", [x["gap_id"] for x in gapmod.next_gaps(p, 10)["gaps"]], "deferred gaps are not re-presented")
        self.assertEqual(p.gap("GAP-002")["status"], "DEFERRED")
        gapmod.revisit(p, "GAP-002")
        self.answer_and_resolve(p, "GAP-002", "Under 3 seconds.", "Mobile page load is under 3 seconds.")
        self.assertEqual(p.gap("GAP-002")["status"], "RESOLVED")

    def test_skip_moves_gap_to_back_of_queue(self):
        p = self.with_gaps()
        self.set_owner(p)
        first = gapmod.next_gaps(p, 1)["gaps"][0]["gap_id"]
        gapmod.skip(p, first)
        self.assertEqual(p.gap(first)["status"], "OPEN")
        self.assertEqual(p.gap(first)["skipped_count"], 1)
        self.assertNotEqual(gapmod.next_gaps(p, 1)["gaps"][0]["gap_id"], first)

    def test_reopen_resolved_gap(self):
        """Test 6."""
        p = self.to_baseline()
        out = gapmod.reopen(p, "GAP-001", "Finance revised the fine policy", "Priya Nair", "Product Owner", "We need to revisit the fine.")
        g = p.gap("GAP-001")
        self.assertEqual(g["status"], "REOPENED")
        self.assertIsNone(g["closed_at_ist"])
        self.assertEqual(len(g["closure_history"]), 1)
        self.assertEqual(p.decisions()[out["invalidated_decision"]]["current_status"], "INVALIDATED")
        self.assertTrue(p.baseline_manifest()["stale"])
        self.assertIn(p.state["stage"], ("GAP_ANALYSIS", "WAITING_FOR_GAP_INPUT"))
        self.assertEqual(code_of(gapmod.reopen, p, "GAP-002", "x", "A B", "PO"), None)
        self.assertEqual(code_of(gapmod.reopen, p, "GAP-001", "x", "A B", "PO"), "INVALID_TRANSITION")
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])
        # presenting + closing again yields a second closure event and a new decision; old decision stays on record
        gapmod.next_gaps(p, 5)
        self.answer_and_resolve(p, "GAP-001", "R15/day max R300", "Fine is R15 per day, max R300.")
        self.assertEqual(len([e for e in p.audit_events() if e["event_type"] == "GAP_RESOLVED" and e["gap_id"] == "GAP-001"]), 2)
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])

    def test_resume_from_each_stage_reports_next_action(self):
        p = self.new_project()
        self.assertIn("req add", checks.status_report(p)["next_action"])
        reqs.add_requirements(p, self.req_items(p))
        reqs.commit_requirements(p)
        self.assertIn("gaps", checks.status_report(Project.open(self.tmp / "out"))["next_action"])
        gapmod.add_gaps(p, self.gap_items())
        self.assertIn("decision owner", checks.status_report(Project.open(self.tmp / "out"))["next_action"])
        self.set_owner(p)
        gapmod.commit_analysis(p, True)
        self.assertIn("gaps next", checks.status_report(p)["next_action"])
        self.resolve_all(p)
        self.assertIn("baseline create", checks.status_report(p)["next_action"])
        basemod.create(p)
        self.assertIn("baseline confirm", checks.status_report(p)["next_action"])
        basemod.confirm(p, 1, "Priya Nair", "Product Owner", "ok")
        self.assertIn("story add", checks.status_report(p)["next_action"])
        txt = checks.status_text(checks.status_report(p))
        self.assertIn("Decision owner: Priya Nair — Product Owner", txt)
        self.assertIn("NEXT:", txt)

    def test_zero_gap_brd_reaches_baseline(self):
        """Test 1."""
        p = self.with_requirements()
        self.assertEqual(gapmod.commit_analysis(p, True)["unresolved"], 0)
        self.assertEqual(code_of(gapmod.commit_analysis, p, False), "CROSS_PASS_REQUIRED")
        out = basemod.create(p)
        self.assertEqual((out["ready_requirements"], out["blocked_requirements"]), (8, 0))
        basemod.confirm(p, 1, "Priya Nair", "Product Owner", "ok")
        self.assertEqual(stories.add_stories(p, [self.story_item()])["created"], ["US-001"])


class AtomicRequirementsFolderTests(BATest):
    def test_requirements_saved_in_atomic_requirements_folder(self):
        p = self.new_project()
        reqs.add_requirements(p, self.req_items(p))
        out = reqs.commit_requirements(p)
        self.assertTrue((p.root / "atomic-requirements" / "REQ-001.md").exists())
        self.assertFalse((p.root / "requirements").exists())
        self.assertEqual(out["saved_to"], str(p.root / "atomic-requirements"))
        self.assertGreater(out["files_on_disk"], 0)

    def test_legacy_requirements_folder_is_migrated_on_open(self):
        p = self.new_project()
        reqs.add_requirements(p, self.req_items(p))
        (p.root / "atomic-requirements").rename(p.root / "requirements")
        p2 = Project.open(p.root)
        self.assertTrue((p.root / "atomic-requirements" / "REQ-001.json").exists())
        self.assertFalse((p.root / "requirements").exists())
        self.assertEqual(len(p2.requirements()), len(self.req_items(p)))


class SourceChangeTests(BATest):
    def test_unchanged_source_is_noop(self):
        p = self.to_baseline()
        self.assertFalse(reqs.source_check(p)["changed"])

    def test_cosmetic_change_triggers_no_impact(self):
        p = self.to_baseline()
        brd = self.tmp / "inputs" / "brd.md"
        brd.write_text(brd.read_text(encoding="utf-8").replace("\n", "   \n"), encoding="utf-8")
        out = reqs.source_check(p)
        self.assertFalse(out["changed"])
        self.assertTrue(out["cosmetic_change"])
        self.assertFalse(p.baseline_manifest()["stale"])
        self.assertFalse(p.state["impact_review"]["pending"])

    def test_content_change_triggers_impact_assessment_not_silent_invalidation(self):
        """Test 8."""
        p = self.to_stories()
        stories.add_stories(p, [self.story_item("Enforce return period", ("REQ-004",),
                               acceptance_criteria=["Given a loan older than 14 days, when it is checked, then it is overdue."])])
        stories.approve(p, "US-003", 1, "approved", "Priya Nair", "Product Owner")
        brd = self.tmp / "inputs" / "brd.md"
        text = brd.read_text(encoding="utf-8")
        brd.write_text(text.replace("within 14 days", "within 21 days")
                       + "\n## 6. Notifications\nMembers shall receive an email reminder two days before a book is due.\n", encoding="utf-8")
        old_fp = p.state["source"]["fingerprint"]
        out = reqs.source_check(p)
        self.assertTrue(out["changed"])
        self.assertEqual(out["affected_requirement_ids"], ["REQ-003", "REQ-004"], "only requirements in the changed SECTION (2.2) are affected")
        self.assertTrue(out["source_excerpt_still_present"]["REQ-003"], "hint: REQ-003's quoted text is untouched")
        self.assertFalse(out["source_excerpt_still_present"]["REQ-004"])
        self.assertEqual(len(out["diff"]["added"]), 1)
        self.assertEqual(p.requirement("REQ-004")["status"], "NEEDS_REVIEW")
        self.assertEqual(p.requirement("REQ-001")["status"], "ACTIVE", "unchanged requirements are left alone (incremental)")
        self.assertEqual(p.state["source"]["fingerprint"], old_fp, "fingerprint is NOT silently replaced")
        self.assertTrue(p.state["impact_review"]["pending"])
        self.assertTrue(p.baseline_manifest()["stale"])
        self.assertFalse(p.state["gap_analysis_complete"])
        self.assertTrue(p.read_json("stories/approved/US-003.json")["impact_review"]["pending"])
        self.assertEqual(p.read_json("stories/approved/US-003.json")["version"], 1, "approved story history untouched")
        self.assertEqual(code_of(stories.approve, p, "US-001", 1, "ok"), "IMPACT_REVIEW_PENDING")
        self.assertEqual(code_of(basemod.create, p), "IMPACT_REVIEW_PENDING")
        self.assertEqual(code_of(reqs.source_accept, p, "Priya Nair", "Product Owner", "accept"), "IMPACT_REVIEW_INCOMPLETE")
        # reassess: update the affected requirement from the new text, add the new section's requirement, accept
        reqs.update_requirement(p, "REQ-004", {"statement": "Borrowed books must be returned within 21 days.",
                                               "source_excerpt": "Borrowed books must be returned within 21 days."},
                                "BRD changed return period", "source-change")
        self.assertEqual(p.requirement("REQ-004")["version"], 2)
        self.assertIn("14 days", p.requirement("REQ-004")["versions"][0]["statement"])
        reqs.confirm_requirement(p, "REQ-003", "Quoted text unchanged; limit rule unaffected by the return-period edit.")
        acc = reqs.source_accept(p, "Priya Nair", "Product Owner", "I accept the revised BRD.")
        self.assertTrue(acc["accepted"])
        self.assertNotEqual(p.state["source"]["fingerprint"], old_fp)
        self.assertFalse(p.state["impact_review"]["pending"])
        new = reqs.add_requirements(p, [dict(title="Due-date reminder", statement="Members receive an email reminder two days before a book is due.",
                                             type="FUNCTIONAL", source_section_ids=[self.sec(p, "6. Notifications")],
                                             source_excerpt="Members shall receive an email reminder two days before a book is due.")])
        self.assertEqual(new["created"], ["REQ-009"])
        self.assertTrue(p.baseline_manifest()["stale"], "baseline still stale: must be regenerated and re-confirmed")
        self.assertEqual(checks.status_report(p)["stories"]["flagged_for_impact_review"], ["US-003"])
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])
        types = [e["event_type"] for e in p.audit_events()]
        for t in ("SOURCE_CHANGED", "SOURCE_CHANGE_ACCEPTED", "REQUIREMENT_UPDATED"):
            self.assertIn(t, types)

    def test_removed_section_requires_explicit_handling(self):
        p = self.with_requirements()
        brd = self.tmp / "inputs" / "brd.md"
        brd.write_text(brd.read_text(encoding="utf-8").replace("## 4. Reporting\nThe system should provide staff a report of overdue books.\n", ""), encoding="utf-8")
        out = reqs.source_check(p)
        self.assertEqual(out["affected_requirement_ids"], ["REQ-007"])
        self.assertEqual(code_of(reqs.source_accept, p, "Priya Nair", "PO", "ok"), "IMPACT_REVIEW_INCOMPLETE")
        self.assertEqual(code_of(reqs.remove_requirement, p, "REQ-007", "Dropped from BRD", "", ""), "IDENTITY_REQUIRED")
        reqs.remove_requirement(p, "REQ-007", "Dropped from BRD", "Priya Nair", "Product Owner")
        reqs.source_accept(p, "Priya Nair", "Product Owner", "ok")
        self.assertEqual(p.requirement("REQ-007")["status"], "REMOVED")
        self.assertTrue(p.exists("atomic-requirements/REQ-007.md"), "removed requirements are retained, never deleted")


if __name__ == "__main__":
    unittest.main()
