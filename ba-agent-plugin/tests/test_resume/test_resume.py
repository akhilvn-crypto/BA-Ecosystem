"""Resume, interruption, crash recovery, defer/reopen, write failures, source-change impact."""
import json
import shutil
import unittest

from tests.helpers import BATest, acs, changemod, checks, docsync, gapmod, key, projmod, reqindex, util
from ba_lib.project import Project
from ba_lib.util import BAError

OWNER = ("Priya Nair", "Product Owner")


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
                shutil.rmtree(self.tmp / "out", ignore_errors=True)
                shutil.rmtree(self.tmp / "inputs", ignore_errors=True)
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
            Project.create(blocker / "sub", self.reqs_copy())
        self.assertIn(c.exception.code, ("OUTPUT_NOT_WRITABLE", "BAD_ROOT"))
        self.assertFalse((blocker / "sub").exists())

    def test_init_with_nothing_to_read_creates_nothing(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        for bad, code in ((empty, "NO_REQUIREMENTS_FOUND"), (self.tmp / "missing", "SOURCE_NOT_FOUND")):
            with self.assertRaises(BAError) as c:
                Project.create(self.tmp / "out", bad)
            self.assertEqual(c.exception.code, code)
            self.assertFalse((self.tmp / "out").exists(), "nothing is created before the source has been read")
        docx = self.tmp / "brd.docx"
        docx.write_bytes(b"PK")
        with self.assertRaises(BAError) as c:
            Project.create(self.tmp / "out", docx)
        self.assertEqual(c.exception.code, "UNSUPPORTED_FORMAT")

    def test_approval_crash_is_repaired(self):
        p = self.to_acs(2)

        def hook(name):
            if name == "ac_after_approved_write":
                raise Boom(name)
        projmod.FAULT_HOOK = hook
        with self.assertRaises(Boom):
            acs.approve(p, "LIB-001", 1, "approved", "Priya Nair", "Product Owner")
        projmod.FAULT_HOOK = None
        p = Project.open(self.tmp / "out")
        v = checks.validate_project(p)
        self.assertFalse(v["ok"])
        out = checks.recover(p)
        self.assertEqual(out["approvals_repaired"], [key("LIB-001")])
        self.assertEqual(out["audit_backfilled"], [key("LIB-001")])
        self.assertFalse((p.root / "acceptance/candidates/json" / (projmod.ac_fname(key("LIB-001")) + ".json")).exists())
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])
        self.assertIn("AC v1", self.enriched(p, "membership.md"), "the approved criteria are in the document")

    def test_the_enriched_copy_is_regenerable_so_recover_restores_a_lost_or_stale_one(self):
        p = self.to_acs(1)
        self.approve(p, "LIB-001")
        f = p.enriched_root / "membership.md"
        good = f.read_bytes()
        f.unlink()
        self.assertTrue(any("membership.md" in w and "missing" in w for w in checks.validate_project(p)["warnings"]))
        out = checks.recover(p)
        self.assertEqual(out["enriched_rendered"], ["membership.md"])
        self.assertEqual(f.read_bytes(), good)
        self.assertTrue(docsync.check(p)["in_sync"])

    def test_a_crash_while_rendering_keeps_the_previous_enriched_copy(self):
        p = self.to_acs(1)
        before = (p.enriched_root / "membership.md").read_bytes()
        real = projmod.atomic_write_bytes

        def failing(path, data):
            if "enriched" in str(path):
                raise OSError("disk full")
            return real(path, data)
        projmod.atomic_write_bytes = failing
        try:
            self.approve(p, "LIB-001")  # the approval itself is saved; the view could not be written
        finally:
            projmod.atomic_write_bytes = real
        self.assertTrue(p.doc_warnings and "WRITE_FAILED" in p.doc_warnings[0], p.doc_warnings)
        self.assertEqual((p.enriched_root / "membership.md").read_bytes(), before, "never a half-written copy")
        self.assertTrue(p.exists(f"acceptance/approved/{projmod.ac_fname(key('LIB-001'))}.json"))
        checks.recover(p)
        self.assertIn("AC v1", self.enriched(p, "membership.md"))


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
        view = again["gaps"][0]["present"]
        self.assertFalse(view["inline"], "a gap with an earlier answer is sent with its card")
        self.assertIn("Earlier answer A1:", view["card"])
        self.assertIn("R10/day", view["card"])
        self.assertEqual(p.gap("GAP-001")["answers"][0]["respondent_name"], "Priya Nair")
        gapmod.followup(p, "GAP-001", "A1", "Need a maximum and the grace period.")
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
        self.assertIn("| GAP-002 | MEDIUM | DEFERRED |", self.enriched(p, "lending.md"), "the document shows the deferred gap")
        gapmod.revisit(p, "GAP-002")
        self.answer_and_resolve(p, "GAP-002", "Under 3 seconds.", "Mobile page load is under 3 seconds.")
        self.assertEqual(p.gap("GAP-002")["status"], "RESOLVED")
        self.assertNotIn("GAP-002", self.enriched(p, "lending.md"), "a resolved gap leaves the open-gap table")

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
        p = self.to_ac_stage()
        acs.add_acs(p, [self.ac_item("LIB-006")])
        self.approve(p, "LIB-006")
        self.assertEqual(p.state["stage"], "AC_DRAFTING")
        out = gapmod.reopen(p, "GAP-001", "Finance revised the fine policy", "Priya Nair", "Product Owner", "We need to revisit the fine.")
        g = p.gap("GAP-001")
        self.assertEqual(g["status"], "REOPENED")
        self.assertIsNone(g["closed_at_ist"])
        self.assertEqual(len(g["closure_history"]), 1)
        self.assertEqual(p.decisions()[out["invalidated_decision"]]["current_status"], "INVALIDATED")
        self.assertEqual(out["flagged_acs"], [key("LIB-006")], "the approved criteria are flagged, not silently invalidated")
        self.assertEqual(p.state["stage"], "WAITING_FOR_GAP_INPUT")
        self.assertEqual(code_of(gapmod.reopen, p, "GAP-002", "x", "A B", "PO"), None)
        self.assertEqual(code_of(gapmod.reopen, p, "GAP-001", "x", "A B", "PO"), "INVALID_TRANSITION")
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])
        t = self.enriched(p, "lending.md")
        self.assertIn("Blocking gap(s) open (GAP-001)", t, "approved criteria are shown as provisional while the gap is open again")
        self.assertIn("Under review", t)
        # presenting + closing again yields a second closure event and a new decision; old decision stays on record
        gapmod.next_gaps(p, 5)
        self.answer_and_resolve(p, "GAP-001", "R15/day max R300", "Fine is R15 per day, max R300.")
        self.assertEqual(len([e for e in p.audit_events() if e["event_type"] == "GAP_RESOLVED" and e["gap_id"] == "GAP-001"]), 2)
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])

    def test_resume_from_each_stage_reports_next_action(self):
        p = self.new_project()
        self.assertIn("gap analysis", checks.status_report(p)["next_action"])
        gapmod.add_gaps(p, self.gap_items())
        self.assertIn("decision owner", checks.status_report(Project.open(self.tmp / "out"))["next_action"])
        self.set_owner(p)
        gapmod.commit_analysis(p, True)
        self.assertIn("gaps next", checks.status_report(p)["next_action"])
        self.resolve_all(p)
        self.assertIn("Draft acceptance criteria", checks.status_report(p)["next_action"])
        acs.add_acs(p, [self.ac_item(e["id"]) for e in p.requirements()])
        self.assertIn("`ac next`", checks.status_report(p)["next_action"])
        for e in p.requirements():
            acs.approve(p, e["req_key"], 1, "ok", *OWNER)
        self.assertIn("Run `complete`", checks.status_report(p)["next_action"])
        checks.complete_workflow(p)
        self.assertIn("Workflow complete", checks.status_report(p)["next_action"])
        txt = checks.status_text(checks.status_report(p))
        self.assertIn("Decision owner: Priya Nair — Product Owner", txt)
        self.assertIn("NEXT:", txt)
        self.assertIn("Enriched copies: in step", txt)

    def test_next_action_puts_unreviewed_changes_first_and_names_blocked_requirements(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 3)
        f = self.src / "reporting.md"
        f.write_text(f.read_text(encoding="utf-8").replace("overdue books", "overdue and lost books"), encoding="utf-8")
        self.assertIn("changed since the last check", checks.status_report(p)["next_action"])
        changemod.check(p)
        self.assertIn("await the user's decision", checks.status_report(p)["next_action"])
        self.assertIn(key("LIB-006"), [r["req_key"] for r in checks.requirement_rows(p) if r["status"] == "BLOCKED"])

    def test_zero_gap_requirements_reach_acceptance_criteria(self):
        """Test 1."""
        p = self.with_requirements()
        self.assertEqual(code_of(acs.add_acs, p, [self.ac_item("LIB-001")]), "INVALID_STAGE", "nothing is written before gap analysis starts")
        self.assertEqual(gapmod.commit_analysis(p, True)["unresolved"], 0)
        self.assertEqual(code_of(gapmod.commit_analysis, p, False), "CROSS_PASS_REQUIRED")
        self.assertTrue(all(r["status"] == "READY" for r in checks.requirement_rows(p)))
        self.assertEqual(acs.add_acs(p, [self.ac_item("LIB-001")])["created"], [key("LIB-001")])
        self.assertEqual(p.state["stage"], "AC_DRAFTING")


class IndexFolderTests(BATest):
    def test_the_index_is_saved_in_its_own_folder_and_lists_every_requirement(self):
        p = self.new_project()
        doc = reqindex.load(p)
        self.assertTrue((p.root / "index" / "md" / "requirements-index.md").exists())
        self.assertEqual(len(doc["requirements"]), 9)
        e = next(x for x in doc["requirements"] if x["req_key"] == key("LIB-006"))
        self.assertEqual((e["id"], e["file"], e["title"], e["kind"]), ("LIB-006", "lending.md", "Late return fine", "heading"))
        self.assertEqual(next(x for x in doc["requirements"] if x["id"] == "LIB-008")["kind"], "row")
        md = (p.root / "index" / "md" / "requirements-index.md").read_text(encoding="utf-8")
        self.assertIn("lending.md::LIB-006", md)
        self.assertEqual(reqindex.text(p, key("LIB-006")).splitlines()[0], "### LIB-006 Late return fine")
        self.assertIn("fine rate is to be decided", reqindex.text(p, "LIB-006"))

    def test_the_index_is_never_hand_edited_input(self):
        p = self.new_project()
        before = (p.root / "index" / "json" / "requirements-index.json").read_bytes()
        Project.open(p.root)
        Project.open(p.root).requirements()
        self.assertEqual((p.root / "index" / "json" / "requirements-index.json").read_bytes(), before, "opening and reading write nothing")


class SourceChangeResumeTests(BATest):
    """A change in the requirements is detected, explained, applied only after a person has seen it, and worked through to the end."""

    def _approved(self):
        p = self.to_ac_stage()
        acs.add_acs(p, [self.ac_item("LIB-004"), self.ac_item("LIB-001")])
        self.approve(p, "LIB-004")
        self.approve(p, "LIB-001")
        return p

    def test_unchanged_source_is_noop(self):
        p = self.to_ac_stage()
        out = changemod.check(p)
        self.assertEqual((out["source"], out["changes"], out["unreviewed_ids"]), ("unchanged", [], []))

    def test_cosmetic_change_triggers_no_impact(self):
        p = self._approved()
        f = self.src / "membership.md"
        f.write_text(f.read_text(encoding="utf-8").replace("\n", "   \n"), encoding="utf-8")
        old = p.state["source"]["fingerprint"]
        out = changemod.check(p)
        self.assertEqual(out["source"], "formatting_only")
        self.assertEqual(out["unreviewed_ids"], [])
        self.assertEqual(len(out["formatting_only_accepted"]), 1)
        self.assertNotEqual(p.state["source"]["fingerprint"], old, "the new bytes are the accepted source")
        self.assertEqual([a["impact_review"] for _, a in p.all_acs()], [None, None])
        self.assertEqual(p.state["stage"], "AC_DRAFTING")
        self.assertTrue(docsync.check(p)["in_sync"])
        self.assertTrue(checks.validate_project(p)["ok"])

    def test_content_change_is_detected_explained_then_applied_after_the_user_has_seen_it(self):
        """Test 8."""
        p = self._approved()
        f = self.src / "membership.md"
        text = f.read_text(encoding="utf-8")
        f.write_text(text.replace("within 14 days", "within 21 days") + "\n### LIB-010 Due-date reminder\nMembers shall receive an email reminder two days before a book is due.\n",
                     encoding="utf-8")
        old_fp = p.state["source"]["fingerprint"]
        out = changemod.check(p)
        self.assertEqual((out["source"], out["unreviewed_ids"]), ("changed", ["CHG-001"]))
        rec = changemod.get(p, "CHG-001")
        self.assertEqual([a["req_key"] for a in rec["impact"]["affected_requirements"]], [key("LIB-004")])
        self.assertEqual([a["req_key"] for a in rec["impact"]["new_requirements"]], ["membership.md::LIB-010"], "only the changed requirement is affected")
        self.assertEqual([a["req_key"] for a in rec["impact"]["linked_acs"]], [key("LIB-004")])
        self.assertEqual(rec["impact"]["approved_acs_at_risk"], [key("LIB-004")])
        mod = next(c for c in rec["changes"] if c["change"] == "MODIFIED")
        self.assertEqual(mod["hunks"][0]["added"], ["Borrowed books must be returned within 21 days."])
        self.assertEqual(p.state["source"]["fingerprint"], old_fp, "nothing is accepted before the user has seen the impact")
        self.assertEqual([a["impact_review"] for _, a in p.all_acs()], [None, None], "nothing downstream was touched")
        self.assertEqual(code_of(changemod.guard, p, "ac approve"), "CHANGES_UNREVIEWED")
        self.assertNotIn("21 days", self.enriched(p, "membership.md"), "the enriched copy follows the accepted source only")
        self.assertEqual(code_of(changemod.apply, p, "CHG-001"), "IDENTITY_REQUIRED")
        res = changemod.apply(p, "CHG-001", *OWNER, "I have seen the impact; apply it.")
        self.assertEqual(res["effects"]["acs_flagged"], [key("LIB-004")])
        self.assertNotEqual(p.state["source"]["fingerprint"], old_fp)
        self.assertFalse(p.state["gap_analysis_complete"])
        self.assertEqual(p.state["stage"], "GAP_ANALYSIS")
        self.assertEqual(p.req_key("LIB-010"), "membership.md::LIB-010")
        t = self.enriched(p, "membership.md")
        self.assertIn("21 days", t)
        self.assertIn("Under review", t)
        self.assertEqual(sorted(i["item_id"] for i in res["open_items"]),
                         sorted([f"requirement:{key('LIB-004')}", "new_requirement:membership.md::LIB-010", f"ac:{key('LIB-004')}"]))
        a = p.read_json(f"acceptance/approved/{projmod.ac_fname(key('LIB-004'))}.json")
        self.assertEqual(a["version"], 1, "approved history untouched")
        self.assertTrue(a["impact_review"]["pending"])
        self.assertEqual(code_of(acs.approve, p, "LIB-001", 1, "x"), "UNKNOWN_AC", "an approved record is not approved again")
        self.assertEqual(checks.status_report(p)["acceptance"]["flagged_for_impact_review"], [key("LIB-004")])
        # work through it: gap analysis again, new version of the criteria, approval
        gapmod.commit_analysis(p, True)
        acs.edit(p, "LIB-004", {"acceptance_criteria": ["Given a loan older than 21 days, when it is checked, then it is overdue."]},
                 "return period is now 21 days")
        acs.approve(p, "LIB-004", 2, "Approved for 21 days.", *OWNER)
        self.assertEqual(sorted(changemod.reconcile(p)), ["CHG-001", "CHG-002"], "the source change and the agent's own edit are both closed by the work")
        self.assertEqual(changemod.summary(p)["awaiting_reconciliation"], [])
        self.assertEqual(p.read_json(f"acceptance/approved/{projmod.ac_fname(key('LIB-004'))}.json")["version"], 2)
        self.assertIn("AC v2", self.enriched(p, "membership.md"))
        self.assertTrue(docsync.check(p)["in_sync"])
        types = [e["event_type"] for e in p.audit_events()]
        for t_ in ("SOURCE_CHANGED", "CHANGE_APPLIED", "AC_IMPACT_FLAGGED", "SOURCE_SNAPSHOT_RECORDED"):
            self.assertIn(t_, types)
        from ba_lib import snapshots
        self.assertEqual(sorted(x["version"] for x in snapshots.versions(p, "membership.md")), [1, 2], "both versions of the file are kept")

    def test_removed_requirement_requires_explicit_handling(self):
        p = self._approved()
        f = self.src / "membership.md"
        text = f.read_text(encoding="utf-8")
        a = text.index("### LIB-004")
        f.write_text(text[:a], encoding="utf-8")
        out = changemod.check(p)
        self.assertEqual(out["source"], "changed")
        changemod.apply(p, out["unreviewed_ids"][0], *OWNER, "Dropped upstream; apply.")
        self.assertEqual(p.requirement(key("LIB-004"))["status"], "REMOVED")
        self.assertEqual(code_of(acs.confirm_unaffected, p, "LIB-004", "still fine"), "REQUIREMENT_REMOVED")
        self.assertEqual(code_of(acs.archive, p, "LIB-004", "Dropped from the requirements", "", ""), "IDENTITY_REQUIRED")
        self.assertEqual(code_of(acs.archive, p, "LIB-001", "x", *OWNER), "REQUIREMENT_STILL_PRESENT")
        acs.archive(p, "LIB-004", "Dropped from the requirements", *OWNER)
        self.assertTrue(p.exists(f"acceptance/archive/{projmod.ac_fname(key('LIB-004'))}.v1.json"), "removed requirements keep their history, nothing is deleted")
        self.assertFalse(p.exists(f"acceptance/approved/{projmod.ac_fname(key('LIB-004'))}.json"))
        self.assertNotIn("LIB-004", self.enriched(p, "membership.md"))
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])


if __name__ == "__main__":
    unittest.main()
