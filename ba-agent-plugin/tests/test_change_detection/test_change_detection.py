"""Change detection & impact analysis: BRD edits, story edits, deletions, formatting-only changes, downstream impact,
approval preservation and workflow resumption."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

from tests.helpers import BATest, FIXTURE  # noqa: F401  (sys.path is set up by helpers)

from ba_lib import changes, checks, cli, gaps as gapmod, ingest, reqs, snapshots, stories, storytrack, util  # noqa: E402
from ba_lib.project import Project  # noqa: E402
from ba_lib.util import BAError  # noqa: E402

PO = ("Priya Nair", "Product Owner")


def code_of(fn, *a, **k):
    try:
        fn(*a, **k)
    except BAError as e:
        return e.code
    return None


class ChangeTest(BATest):
    def brd_path(self) -> Path:
        return self.tmp / "inputs" / "brd.md"

    def edit_brd(self, fn):
        f = self.brd_path()
        f.write_text(fn(f.read_text(encoding="utf-8")), encoding="utf-8")

    def two_approved(self) -> Project:
        p = self.to_stories()
        stories.approve(p, "US-001", 1, "I approve US-001 v1.", *PO)
        stories.approve(p, "US-002", 1, "I approve US-002 v1.", *PO)
        return p

    def approved_json(self, p, sid="US-001") -> dict:
        return json.loads((p.root / "stories" / "approved" / f"{sid}.json").read_text(encoding="utf-8"))

    def write_approved_json(self, p, s, sid="US-001"):
        (p.root / "stories" / "approved" / f"{sid}.json").write_text(json.dumps(s, indent=2), encoding="utf-8")

    def events(self, p, kind=None):
        return [e for e in p.audit_events() if not kind or e["event_type"] == kind]

    def ba(self, p, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["--root", str(p.root), *argv])
        txt = buf.getvalue()
        try:
            return rc, json.loads(txt)
        except json.JSONDecodeError:
            return rc, {"raw": txt}


# =================================================================== BRD changes

class BrdChangeDetectionTests(ChangeTest):
    def test_baseline_snapshot_and_hashes_are_recorded_at_start(self):
        p = self.new_project()
        idx = snapshots.index(p)
        self.assertEqual([v["version"] for v in idx["versions"]], [1])
        v = idx["versions"][0]
        self.assertEqual(v["fingerprint"], p.state["source"]["fingerprint"])
        self.assertEqual(v["material_fingerprint"], p.state["source"]["material_fingerprint"])
        self.assertIsNotNone(snapshots.text_of(p, v), "snapshot text must match its recorded hash")
        self.assertTrue(p.exists("snapshots/brd/brd-v1.txt"))
        self.assertIn("BRD_SNAPSHOT_RECORDED", [e["event_type"] for e in p.audit_events()])

    def test_unchanged_brd_reports_nothing_and_records_nothing(self):
        p = self.to_baseline()
        out = changes.check(p)
        self.assertEqual(out["source"], "unchanged")
        self.assertEqual(out["changes"], [])
        self.assertFalse(out["requires_review"])
        self.assertEqual(changes.records(p), [])

    def test_brd_edit_is_detected_explained_and_touches_nothing_downstream(self):
        p = self.two_approved()
        stories.add_stories(p, [self.story_item("Enforce return period", ("REQ-004",),
                                                acceptance_criteria=["Given a loan older than 14 days, when checked, then it is overdue."])])
        stories.approve(p, "US-003", 1, "approve", *PO)
        before_fp = p.state["source"]["fingerprint"]
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        edited_hash = util.sha256_file(self.brd_path())

        out = changes.check(p)

        self.assertTrue(out["requires_review"])
        rec = out["changes"][0]
        self.assertEqual((rec["change_id"], rec["status"], rec["classification"], rec["type"]), ("CHG-001", "DETECTED", "MATERIAL", "SOURCE_CHANGE"))
        mod = [c for c in rec["changes"] if c["change"] == "MODIFIED"]
        self.assertEqual(len(mod), 1)
        self.assertIn("2.2", mod[0]["section_key"])
        self.assertIn("14 days", " ".join(mod[0]["hunks"][0]["removed"]))
        self.assertIn("21 days", " ".join(mod[0]["hunks"][0]["added"]))
        imp = rec["impact"]
        self.assertEqual([a["requirement_id"] for a in imp["affected_requirements"]], ["REQ-003", "REQ-004"])
        by = {a["requirement_id"]: a for a in imp["affected_requirements"]}
        self.assertEqual(by["REQ-003"]["impact"], "SECTION_CHANGED_QUOTED_TEXT_INTACT")
        self.assertEqual(by["REQ-004"]["impact"], "QUOTED_TEXT_REMOVED_OR_REWORDED")
        self.assertEqual(imp["approved_stories_at_risk"], ["US-003"])
        self.assertTrue(imp["baseline"]["will_be_marked_stale"])
        self.assertTrue(imp["decisions_to_revalidate"], "confirmed decisions are listed for re-validation, never invalidated")
        self.assertTrue(all(d["current_status"] == "ACTIVE" for d in p.decisions().values()))
        self.assertTrue(rec["required_actions"])
        self.assertTrue(p.exists("changes/CHG-001.md"))
        self.assertIn("21 days", (p.root / "changes" / "CHG-001.md").read_text(encoding="utf-8"))

        # NOTHING downstream changed yet, and the BRD itself is untouched
        self.assertEqual(p.requirement("REQ-004")["status"], "ACTIVE")
        self.assertFalse(p.baseline_manifest()["stale"])
        self.assertFalse(p.state["impact_review"]["pending"])
        self.assertIsNone(self.approved_json(p, "US-003")["impact_review"])
        self.assertEqual(p.state["source"]["fingerprint"], before_fp, "accepted fingerprint is not silently replaced")
        self.assertEqual(util.sha256_file(self.brd_path()), edited_hash, "the BRD file is only ever read")

    def test_check_is_idempotent(self):
        p = self.to_baseline()
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        a = changes.check(p)["changes"][0]["change_id"]
        b = changes.check(p)["changes"][0]["change_id"]
        self.assertEqual((a, b), ("CHG-001", "CHG-001"))
        self.assertEqual(len(changes.records(p)), 1)

    def test_apply_needs_the_person_and_their_words_then_marks_artifacts_stale(self):
        p = self.two_approved()
        self.edit_brd(lambda t: t.replace("A member may borrow at most 5 books", "A member may borrow at most 7 books"))
        cid = changes.check(p)["changes"][0]["change_id"]
        self.assertEqual(code_of(changes.apply, p, cid, "", "", "go"), "IDENTITY_REQUIRED")
        self.assertEqual(code_of(changes.apply, p, cid, *PO, ""), "BAD_INPUT")
        self.assertEqual(p.requirement("REQ-003")["status"], "ACTIVE", "a refused apply changes nothing")

        out = changes.apply(p, cid, *PO, "I have seen the impact; apply it.")

        self.assertEqual(out["status"], "APPLIED")
        self.assertEqual(p.requirement("REQ-003")["status"], "NEEDS_REVIEW")
        self.assertTrue(p.baseline_manifest()["stale"])
        self.assertTrue(p.state["impact_review"]["pending"])
        self.assertEqual(p.state["impact_review"]["change_id"], cid)
        self.assertEqual(p.state["stage"], "GAP_ANALYSIS", "workflow reopened to reassess")
        rec = changes.get(p, cid)
        self.assertEqual(rec["applied"]["by_name"], "Priya Nair")
        self.assertEqual(rec["applied"]["statement"], "I have seen the impact; apply it.")
        kinds = {i["kind"] for i in rec["items"]}
        self.assertTrue({"requirement", "source_acceptance", "baseline"} <= kinds)
        self.assertEqual(code_of(changes.apply, p, cid, *PO, "again"), "NOT_APPLICABLE")
        types = [e["event_type"] for e in p.audit_events()]
        for t in ("CHANGE_DETECTED", "CHANGE_APPLIED", "SOURCE_CHANGED"):
            self.assertIn(t, types)
        self.assertEqual(self.events(p, "CHANGE_APPLIED")[0]["by_name"], "Priya Nair")

    def test_deleted_section_is_reported_as_deletion_with_requirement_impact(self):
        p = self.with_requirements()
        self.edit_brd(lambda t: t.replace("## 4. Reporting\nThe system should provide staff a report of overdue books.\n", ""))
        rec = changes.check(p)["changes"][0]
        dele = [c for c in rec["changes"] if c["change"] == "DELETED"]
        self.assertEqual(len(dele), 1)
        self.assertIn("overdue", dele[0]["excerpt"])
        self.assertEqual(rec["impact"]["affected_requirements"][0]["requirement_id"], "REQ-007")
        self.assertEqual(rec["impact"]["affected_requirements"][0]["impact"], "SECTION_DELETED")
        cid = rec["change_id"]
        changes.apply(p, cid, *PO, "apply")
        self.assertEqual(p.requirement("REQ-007")["status"], "NEEDS_REVIEW")
        self.assertEqual(code_of(reqs.source_accept, p, *PO, "ok"), "IMPACT_REVIEW_INCOMPLETE")
        reqs.remove_requirement(p, "REQ-007", "Dropped from the BRD", *PO)
        self.assertTrue(p.exists("atomic-requirements/REQ-007.md"), "removed requirements are retained, never deleted")

    def test_added_section_is_listed_and_closed_by_requirement_or_dismissal(self):
        p = self.with_requirements()
        self.edit_brd(lambda t: t + "\n## 6. Notifications\nMembers shall receive an email reminder two days before a book is due.\n")
        rec = changes.check(p)["changes"][0]
        self.assertEqual([c["change"] for c in rec["changes"]], ["ADDED"])
        self.assertEqual(rec["impact"]["affected_requirements"], [])
        self.assertEqual(len(rec["impact"]["new_sections_without_requirements"]), 1)
        cid = rec["change_id"]
        changes.apply(p, cid, *PO, "apply")
        item = next(i for i in changes.get(p, cid)["items"] if i["kind"] == "new_section")
        self.assertEqual(item["state"], "OPEN")
        self.assertEqual(code_of(changes.dismiss, p, cid, "requirement:REQ-001", "x", *PO), "UNKNOWN_ITEM")
        self.assertEqual(code_of(changes.dismiss, p, cid, item["item_id"], "", *PO), "REASON_REQUIRED")
        reqs.source_accept(p, *PO, "accepted the new section")
        changes.dismiss(p, cid, item["item_id"], "Notifications are out of scope for this release.", *PO)
        rec = changes.get(p, cid)
        self.assertEqual(next(i for i in rec["items"] if i["kind"] == "new_section")["state"], "DISMISSED")
        self.assertIn("Notifications are out of scope", next(i for i in rec["items"] if i["kind"] == "new_section")["reason"])

    def test_formatting_only_edits_need_no_review_and_are_recorded(self):
        p = self.to_baseline()
        old_fp = p.state["source"]["fingerprint"]
        def reformat(t):
            t = t.replace("### 2.2 Borrowing limits\nA member may borrow at most 5 books at the same time.\nBorrowed books must be returned within 14 days.",
                          "### 2.2 Borrowing limits\n\n- A member may borrow at most **5** books at the same time.\n- Borrowed books must be\n  returned within 14 days.\n\n\n")
            return t.replace("The system must send a confirmation email", "The system  must send   a *confirmation* email")
        self.edit_brd(reformat)
        out = changes.check(p)
        self.assertEqual(out["source"], "formatting_only")
        self.assertEqual(out["unreviewed_ids"], [])
        self.assertFalse(out["requires_review"])
        self.assertEqual(len(out["formatting_only_accepted"]), 1)
        self.assertEqual(out["formatting_only_accepted"][0]["classification"], "FORMATTING_ONLY")
        self.assertEqual(out["formatting_only_accepted"][0]["status"], "RECORDED")
        # no review burden: nothing flagged, baseline still current, requirements untouched
        self.assertFalse(p.baseline_manifest()["stale"])
        self.assertFalse(p.state["impact_review"]["pending"])
        self.assertEqual({r["status"] for r in p.requirements()}, {"ACTIVE"})
        self.assertEqual(p.requirement("REQ-003")["version"], 1)
        # but the fingerprint moved, a new snapshot exists, and the audit trail says why
        self.assertNotEqual(p.state["source"]["fingerprint"], old_fp)
        self.assertEqual([v["version"] for v in snapshots.index(p)["versions"]], [1, 2])
        self.assertEqual(len(self.events(p, "SOURCE_FORMATTING_CHANGE_RECORDED")), 1)
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])
        self.assertEqual(changes.check(p)["source"], "unchanged")

    def test_a_changed_number_is_material_even_inside_formatting(self):
        p = self.to_baseline()
        self.edit_brd(lambda t: t.replace("at most 5 books", "at most **6** books"))
        out = changes.check(p)
        self.assertEqual(out["source"], "changed")
        self.assertEqual(out["changes"][0]["classification"], "MATERIAL")

    def test_bytes_only_change_is_formatting(self):
        p = self.to_baseline()
        self.brd_path().write_text(self.brd_path().read_text(encoding="utf-8").replace("\n", "   \n"), encoding="utf-8")
        out = changes.check(p)
        self.assertEqual(out["source"], "formatting_only")
        self.assertEqual(out["unreviewed_ids"], [])

    def test_second_edit_supersedes_unapplied_change_and_revert_is_recorded(self):
        p = self.to_baseline()
        original = self.brd_path().read_bytes()
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        first = changes.check(p)["changes"][0]["change_id"]
        self.edit_brd(lambda t: t.replace("within 21 days", "within 30 days"))
        second = changes.check(p)["changes"][0]["change_id"]
        self.assertNotEqual(first, second)
        self.assertEqual(changes.get(p, first)["status"], "SUPERSEDED")
        self.assertEqual(changes.get(p, first)["superseded_by"], second)
        self.brd_path().write_bytes(original)
        out = changes.check(p)
        self.assertEqual(out["source"], "unchanged")
        self.assertEqual(changes.get(p, second)["status"], "REVERTED")
        self.assertEqual(out["unreviewed_ids"], [])

    def test_apply_refuses_a_stale_record(self):
        p = self.to_baseline()
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        cid = changes.check(p)["changes"][0]["change_id"]
        self.edit_brd(lambda t: t.replace("within 21 days", "within 30 days"))
        self.assertEqual(code_of(changes.apply, p, cid, *PO, "apply"), "CHANGE_RECORD_STALE")
        self.assertEqual(p.requirement("REQ-004")["status"], "ACTIVE")

    def test_full_reconciliation_of_a_brd_change(self):
        p = self.two_approved()
        stories.add_stories(p, [self.story_item("Borrow limit", ("REQ-003",),
                                                acceptance_criteria=["Given five loans, when a sixth is attempted, then it is refused."])])
        stories.approve(p, "US-003", 1, "approve", *PO)
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days")
                      + "\n## 6. Notifications\nMembers shall receive an email reminder two days before a book is due.\n")
        cid = changes.check(p)["changes"][0]["change_id"]
        changes.apply(p, cid, *PO, "apply")
        self.assertEqual(changes.get(p, cid)["status"], "APPLIED")
        reqs.update_requirement(p, "REQ-004", {"statement": "Borrowed books must be returned within 21 days.",
                                               "source_excerpt": "Borrowed books must be returned within 21 days."},
                                "BRD changed return period", "source-change")
        reqs.confirm_requirement(p, "REQ-003", "Quoted text unchanged.")
        reqs.source_accept(p, *PO, "Accepted.")
        self.assertEqual([v["version"] for v in snapshots.index(p)["versions"]], [1, 2], "accepting records the new BRD version")
        self.assertEqual(snapshots.latest(p)["change_id"], cid)
        reqs.add_requirements(p, [dict(title="Due-date reminder", statement="Members receive a reminder two days before a book is due.",
                                       type="FUNCTIONAL", source_section_ids=[self.sec(p, "6. Notifications")],
                                       source_excerpt="Members shall receive an email reminder two days before a book is due.")])
        changes.reconcile(p)
        open_now = [i["item_id"] for i in changes.get(p, cid)["items"] if i["state"] == "OPEN"]
        self.assertEqual(changes.get(p, cid)["status"], "APPLIED", "requirements are reassessed but the baseline and stories are not")
        self.assertTrue(any(i.startswith("baseline") for i in open_now), open_now)
        self.assertIn("story:US-003", open_now)
        gapmod.commit_analysis(p, True)
        from ba_lib import baseline as basemod
        basemod.create(p, "after BRD change")
        basemod.confirm(p, 2, *PO, "Baseline v2 approved.")
        for _, s in p.all_stories():
            if (s.get("impact_review") or {}).get("pending"):
                stories.confirm_unaffected(p, s["story_id"], "Reviewed; the change does not affect this story.", *PO)
        changes.reconcile(p)
        rec = changes.get(p, cid)
        self.assertEqual(rec["status"], "RECONCILED", [i for i in rec["items"] if i["state"] == "OPEN"])
        self.assertIsNotNone(rec["reconciled_at_ist"])
        self.assertIn("CHANGE_RECONCILED", [e["event_type"] for e in p.audit_events()])
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])

    def test_project_without_snapshot_degrades_honestly_and_backfills_when_unchanged(self):
        p = self.to_baseline()
        import shutil
        shutil.rmtree(p.root / "snapshots")
        st = p.state["source"]
        st.pop("material_fingerprint", None)
        p.save_state("test-legacy")
        self.assertEqual(changes.check(p)["source"], "unchanged")
        self.assertEqual(len(snapshots.index(p)["versions"]), 1, "backfilled while the BRD still matched")
        shutil.rmtree(p.root / "snapshots")
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        out = changes.check(p)
        self.assertTrue(any("not stored" in w for w in out["warnings"]))
        self.assertEqual(out["changes"][0]["classification"], "MATERIAL")


# ============================================================== approved stories

class StoryChangeDetectionTests(ChangeTest):
    def test_approval_records_hashes_and_an_immutable_copy(self):
        p = self.two_approved()
        e = storytrack.entry(p, "US-001")
        v = storytrack.current(e)
        self.assertEqual(v["version"], 1)
        self.assertEqual(v["approver_name"], "Priya Nair")
        self.assertTrue(p.exists(v["snapshot"]))
        self.assertEqual(storytrack.snapshot_record(p, v)["approval"]["statement"], "I approve US-001 v1.")
        self.assertIn("material_hash", self.events(p, "STORY_APPROVED")[0])
        self.assertEqual(changes.check(p)["changes"], [], "an untouched approved story is not a change")

    def test_manual_json_edit_is_detected_and_explained_before_anything_moves(self):
        p = self.two_approved()
        s = self.approved_json(p)
        s["acceptance_criteria"].append("Given a duplicate email, when they submit registration, then an error is shown.")
        self.write_approved_json(p, s)
        edited = (p.root / "stories" / "approved" / "US-001.json").read_bytes()

        out = changes.check(p)

        rec = out["changes"][0]
        self.assertEqual((rec["type"], rec["status"], rec["origin"]), ("STORY_CHANGE", "DETECTED", "MANUAL"))
        self.assertEqual(rec["subject"]["change_type"], "MANUAL_EDIT")
        self.assertEqual(len(rec["changes"][0]["fields"]["acceptance_criteria"]["added"]), 1)
        self.assertEqual(rec["impact"]["requirements"]["after"], ["REQ-001"])
        self.assertEqual(rec["impact"]["approval_record"]["approver_name"], "Priya Nair")
        self.assertTrue(rec["required_actions"])
        # detection alone leaves the edited file exactly as the user left it
        self.assertEqual((p.root / "stories" / "approved" / "US-001.json").read_bytes(), edited)
        self.assertFalse(list((p.root / "stories" / "candidates").glob("US-001*")))

    def test_applying_a_material_manual_edit_keeps_everything_and_requires_reapproval(self):
        p = self.two_approved()
        approved_before = self.approved_json(p)
        s = json.loads(json.dumps(approved_before))
        s["acceptance_criteria"][0] = "Given a visitor with a valid email, when they register, then a member account is created and a welcome page is shown."
        self.write_approved_json(p, s)
        edited_bytes = (p.root / "stories" / "approved" / "US-001.json").read_bytes()
        cid = changes.check(p)["changes"][0]["change_id"]

        out = changes.apply(p, cid, *PO, "Seen. Keep the edit as a new version for review.")

        # the approved version is back exactly as approved (approval record intact)
        restored = self.approved_json(p)
        self.assertEqual(restored["version"], 1)
        self.assertEqual(restored["status"], "APPROVED")
        self.assertEqual(restored["approval"], approved_before["approval"])
        self.assertEqual(restored["acceptance_criteria"], approved_before["acceptance_criteria"])
        self.assertEqual(storytrack.material_hash(restored), storytrack.current(storytrack.entry(p, "US-001"))["material_hash"])
        # the manual edit is NOT discarded: it is a new candidate version, and the verbatim file is kept
        cand = json.loads((p.root / "stories" / "candidates" / "US-001.json").read_text(encoding="utf-8"))
        self.assertEqual((cand["version"], cand["status"], cand["approval"], cand["supersedes_version"]), (2, "CANDIDATE", None, 1))
        self.assertIn("welcome page", cand["acceptance_criteria"][0])
        self.assertEqual(cand["review_history"][-1]["action"], "MANUAL_EDIT_DETECTED")
        self.assertEqual(cand["review_history"][-1]["change_id"], cid)
        kept = out["effects"]["edit_preserved_as"]
        self.assertEqual((p.root / kept[0]).read_bytes(), edited_bytes)
        # audit + record
        for t in ("STORY_MANUAL_EDIT_DETECTED", "STORY_REAPPROVAL_REQUIRED"):
            self.assertEqual(len(self.events(p, t)), 1, t)
        self.assertEqual(self.events(p, "STORY_MANUAL_EDIT_DETECTED")[0]["approved_version"], 1)
        self.assertEqual(changes.get(p, cid)["status"], "APPLIED")
        self.assertEqual(changes.get(p, cid)["items"][0]["kind"], "reapproval")
        # completion cannot silently pass while the edit is unreviewed
        self.assertTrue(any("US-001" in r for r in checks.completion(p)["reasons"]))
        # reapproval closes the loop; v1's approval stays in history
        stories.approve(p, "US-001", 2, "Approved the edited wording.", *PO)
        changes.reconcile(p)
        self.assertEqual(changes.get(p, cid)["status"], "RECONCILED")
        hist = changes.story_history(p, "US-001")
        self.assertEqual([v["version"] for v in hist["approved_versions"]], [1, 2])
        self.assertEqual([v["in_force"] for v in hist["approved_versions"]], [False, True])
        self.assertEqual(hist["approved_versions"][0]["approval_statement"], "I approve US-001 v1.")
        self.assertTrue(p.exists("stories/archive/US-001.v1.json"))
        self.assertEqual(changes.check(p)["changes"], [])

    def test_manual_edit_of_the_markdown_copy_is_detected_too(self):
        p = self.two_approved()
        md = p.root / "stories" / "approved" / "US-001.md"
        text = md.read_text(encoding="utf-8")
        self.assertIn("1. Given a visitor with a valid email", text)
        md.write_text(text.replace("then a member account is created.", "then a member account is created and a welcome email is queued."), encoding="utf-8")
        rec = changes.check(p)["changes"][0]
        self.assertEqual(rec["subject"]["edited_via"], "markdown")
        self.assertIn("acceptance_criteria", rec["changes"][0]["fields"])
        changes.apply(p, rec["change_id"], *PO, "apply")
        cand = json.loads((p.root / "stories" / "candidates" / "US-001.json").read_text(encoding="utf-8"))
        self.assertIn("welcome email is queued", cand["acceptance_criteria"][0])
        self.assertEqual(cand["version"], 2)
        # the JSON of the approved version was never wrong, and its .md is regenerated from it
        self.assertNotIn("welcome email", (p.root / "stories" / "approved" / "US-001.md").read_text(encoding="utf-8"))
        self.assertEqual(self.approved_json(p)["version"], 1)

    def test_formatting_only_story_edits_do_not_touch_the_approval(self):
        p = self.two_approved()
        before = self.approved_json(p)
        s = json.loads(json.dumps(before))
        s["title"] = "  Register   as a   member  "
        s["acceptance_criteria"][1] = "Given an invalid email,   when they submit registration,  then an error is shown and no account is created.  "
        self.write_approved_json(p, s)
        md = p.root / "stories" / "approved" / "US-002.md"
        md.write_text(md.read_text(encoding="utf-8").replace("### User Story", "\n\n### User Story\n"), encoding="utf-8")
        out = changes.check(p)
        self.assertFalse(out["requires_review"])
        self.assertEqual(out["unreviewed_ids"], [])
        self.assertEqual(len(out["formatting_only_accepted"]), 2)
        self.assertTrue(all(r["classification"] == "FORMATTING_ONLY" and r["status"] == "RECORDED" for r in out["formatting_only_accepted"]))
        self.assertEqual(self.approved_json(p)["version"], 1)
        self.assertEqual(self.approved_json(p)["approval"], before["approval"])
        self.assertFalse(list((p.root / "stories" / "candidates").glob("US-*")))
        self.assertEqual(len(self.events(p, "STORY_FORMATTING_CHANGE_RECORDED")), 2)
        self.assertEqual(changes.check(p)["formatting_only_accepted"], [], "accepted once, not re-flagged")
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])

    def test_altering_the_approval_record_by_hand_is_not_accepted(self):
        p = self.two_approved()
        s = self.approved_json(p)
        s["approval"]["approver_name"] = "Somebody Else"
        s["approval"]["statement"] = "I approve everything."
        self.write_approved_json(p, s)
        rec = changes.check(p)["changes"][0]
        self.assertEqual(rec["subject"]["change_type"], "APPROVAL_ALTERED")
        changes.apply(p, rec["change_id"], *PO, "apply")
        s = self.approved_json(p)
        self.assertEqual(s["approval"]["approver_name"], "Priya Nair")
        self.assertTrue(s["impact_review"]["pending"], "the story is flagged until a person reviews it")
        self.assertIn("altered by hand", s["impact_review"]["reason"])
        self.assertFalse(list((p.root / "stories" / "candidates").glob("US-001*")), "no material edit, so no new version")
        self.assertTrue(any(p.root.joinpath("snapshots", "stories").glob("US-001.observed-*.json")), "the altered file is kept")

    def test_linked_requirements_changed_by_hand_show_up_in_the_impact(self):
        p = self.two_approved()
        s = self.approved_json(p)
        s["requirement_ids"] = ["REQ-001", "REQ-002"]
        self.write_approved_json(p, s)
        rec = changes.check(p)["changes"][0]
        req = rec["impact"]["requirements"]
        self.assertEqual((req["added"], req["removed"]), (["REQ-002"], []))
        self.assertEqual([x["story_id"] for x in rec["impact"]["stories_sharing_requirements"]], ["US-002"])

    def test_deleted_approved_story_is_detected_preserved_and_restorable(self):
        p = self.two_approved()
        original = self.approved_json(p, "US-002")
        (p.root / "stories" / "approved" / "US-002.json").unlink()
        (p.root / "stories" / "approved" / "US-002.md").unlink()
        rec = changes.check(p)["changes"][0]
        self.assertEqual(rec["subject"]["change_type"], "DELETED")
        cid = rec["change_id"]
        changes.apply(p, cid, *PO, "Seen.")
        self.assertEqual(storytrack.entry(p, "US-002")["status"], storytrack.DELETED)
        self.assertTrue(p.exists(storytrack.current(storytrack.entry(p, "US-002"))["snapshot"]), "approved record kept")
        self.assertEqual(len(self.events(p, "STORY_DELETION_DETECTED")), 1)
        self.assertEqual(checks.traceability(p)["not_covered_by_approved_story"].count("REQ-002"), 1)
        again = changes.check(p)
        self.assertEqual(again["changes"], [], "a recorded deletion is not re-detected on every check")
        self.assertEqual([c["change_id"] for c in again["awaiting_reconciliation"]], [cid], "it stays open until someone decides")
        # a new story must never reuse the deleted story's id
        self.assertEqual(stories.add_stories(p, [self.story_item("Another", ("REQ-003",),
                                                                 acceptance_criteria=["Given six books, when a seventh is borrowed, then it is refused."])])["created"], ["US-003"])
        self.assertEqual(code_of(changes.restore_story, p, "US-002", "", *PO), "REASON_REQUIRED")
        out = changes.restore_story(p, "US-002", "Deleted by mistake.", *PO)
        restored = self.approved_json(p, "US-002")
        self.assertEqual(restored["approval"], original["approval"])
        self.assertEqual(restored["acceptance_criteria"], original["acceptance_criteria"])
        self.assertEqual(out["status"], "APPROVED")
        self.assertEqual(changes.get(p, cid)["status"], "RECONCILED")
        self.assertEqual(changes.check(p)["changes"], [])

    def test_confirming_a_story_removal_is_a_recorded_decision(self):
        p = self.two_approved()
        (p.root / "stories" / "approved" / "US-002.json").unlink()
        cid = changes.check(p)["changes"][0]["change_id"]
        changes.apply(p, cid, *PO, "Seen.")
        changes.dismiss(p, cid, "story_removal:US-002", "Out of scope for release 1.", *PO)
        self.assertEqual(storytrack.entry(p, "US-002")["status"], storytrack.REMOVAL_CONFIRMED)
        self.assertEqual(changes.get(p, cid)["status"], "RECONCILED")
        self.assertEqual(changes.check(p)["changes"], [])
        self.assertTrue(p.exists(storytrack.current(storytrack.entry(p, "US-002"))["snapshot"]), "history is kept even when removal is confirmed")

    def test_reverting_a_manual_edit_keeps_the_approval_and_the_discarded_copy(self):
        p = self.two_approved()
        s = self.approved_json(p)
        s["title"] = "Register with email"
        self.write_approved_json(p, s)
        cid = changes.check(p)["changes"][0]["change_id"]
        changes.apply(p, cid, *PO, "apply")
        self.assertEqual(code_of(changes.revert_edit, p, "US-001", "", *PO), "REASON_REQUIRED")
        out = changes.revert_edit(p, "US-001", "The old title was right.", *PO)
        self.assertEqual(out["approved_version_in_force"], 1)
        self.assertEqual(self.approved_json(p)["title"], "Register as a member")
        self.assertFalse(list((p.root / "stories" / "candidates").glob("US-001*")))
        self.assertEqual(json.loads((p.root / out["discarded_copy"]).read_text(encoding="utf-8"))["title"], "Register with email")
        self.assertEqual(changes.get(p, cid)["status"], "RECONCILED")
        self.assertEqual(len(self.events(p, "STORY_EDIT_REVERTED")), 1)

    def test_user_putting_the_file_back_reverts_an_unapplied_detection(self):
        p = self.two_approved()
        path = p.root / "stories" / "approved" / "US-001.json"
        original = path.read_bytes()
        s = self.approved_json(p)
        s["title"] = "Something else entirely"
        self.write_approved_json(p, s)
        cid = changes.check(p)["changes"][0]["change_id"]
        path.write_bytes(original)
        out = changes.check(p)
        self.assertEqual(out["unreviewed_ids"], [])
        self.assertEqual(changes.get(p, cid)["status"], "REVERTED")

    def test_ba_agent_edit_of_an_approved_story_is_tracked_with_history(self):
        p = self.two_approved()
        out = stories.edit(p, "US-001", {"acceptance_criteria": [
            "Given a visitor with a valid email, when they submit registration, then a member account is created.",
            "Given an invalid email, when they submit registration, then an error is shown and no account is created.",
            "Given a duplicate email, when they register, then they are told the email is already in use."]}, "Reviewer asked for the duplicate case.")
        self.assertEqual(out["new_version"], 2)
        self.assertTrue(out["approved_version_still_in_force"])
        rec = changes.records(p)[-1]
        self.assertEqual((rec["origin"], rec["classification"], rec["status"]), ("BA_AGENT", "MATERIAL", "APPLIED"))
        self.assertIn("duplicate", " ".join(rec["changes"][0]["fields"]["acceptance_criteria"]["added"]))
        self.assertEqual(rec["items"][0]["item_id"], "reapproval:US-001:v2")
        self.assertEqual(self.approved_json(p)["version"], 1, "v1 approval untouched until v2 is approved")
        stories.approve(p, "US-001", 2, "ok", *PO)
        changes.reconcile(p)
        self.assertEqual(changes.get(p, rec["change_id"])["status"], "RECONCILED")
        self.assertEqual(changes.check(p)["changes"], [], "the agent's own edit is not mistaken for a manual one")

    def test_ba_agent_formatting_only_edit_keeps_the_approval_and_version(self):
        p = self.two_approved()
        before = self.approved_json(p)
        out = stories.edit(p, "US-001", {"title": "  Register   as a member "}, "Tidy whitespace.")
        self.assertTrue(out["formatting_only"])
        self.assertEqual(out["new_version"], 1)
        after = self.approved_json(p)
        self.assertEqual((after["version"], after["status"], after["approval"]), (1, "APPROVED", before["approval"]))
        rec = changes.records(p)[-1]
        self.assertEqual((rec["origin"], rec["classification"], rec["status"]), ("BA_AGENT", "FORMATTING_ONLY", "RECORDED"))
        self.assertFalse(list((p.root / "stories" / "candidates").glob("US-001*")))
        self.assertEqual(changes.check(p)["changes"], [])
        self.assertTrue(checks.validate_project(p)["ok"], checks.validate_project(p)["errors"])

    def test_untracked_story_dropped_into_approved_is_not_trusted(self):
        p = self.two_approved()
        s = self.approved_json(p, "US-002")
        s["story_id"], s["title"] = "US-009", "Smuggled in"
        (p.root / "stories" / "approved" / "US-009.json").write_text(json.dumps(s), encoding="utf-8")
        rec = changes.check(p)["changes"][0]
        self.assertEqual(rec["subject"]["change_type"], "UNTRACKED_APPROVED")
        changes.apply(p, rec["change_id"], *PO, "apply")
        self.assertFalse((p.root / "stories" / "approved" / "US-009.json").exists())
        cand = json.loads((p.root / "stories" / "candidates" / "US-009.json").read_text(encoding="utf-8"))
        self.assertEqual((cand["status"], cand["approval"]), ("CANDIDATE", None))

    def test_legacy_approved_stories_are_adopted_not_flagged(self):
        p = self.two_approved()
        import shutil
        shutil.rmtree(p.root / "snapshots" / "stories")
        out = changes.check(p)
        self.assertEqual(out["changes"], [])
        self.assertEqual(len(self.events(p, "STORY_TRACKING_ADOPTED")), 2)
        self.assertIsNotNone(storytrack.entry(p, "US-001"))


# =========================================== workflow: guard, resume, traceability

class WorkflowChangeTests(ChangeTest):
    def test_cli_blocks_writes_until_a_detected_change_is_shown_and_applied(self):
        p = self.to_baseline()
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        for argv in (["story", "add", "--file", str(self.tmp / "x.json")], ["baseline", "create"], ["gaps", "commit", "--cross-pass-done"],
                     ["story", "next"], ["gaps", "next"]):
            rc, out = self.ba(p, *argv)
            self.assertEqual((rc, out["error"]["code"]), (1, "CHANGES_UNREVIEWED"), argv)
            self.assertEqual(out["error"]["details"]["change_ids"], ["CHG-001"])
            self.assertIn("2.2", json.dumps(out["error"]["details"]["changes"][0]["impact"]))
        self.assertEqual(p.requirement("REQ-004")["status"], "ACTIVE", "nothing downstream changed while blocked")
        self.assertEqual(len(changes.records(p)), 1, "the detection was recorded once, by the first blocked command")
        rc, out = self.ba(p, "changes", "apply", "--id", "CHG-001", "--by-name", "Priya Nair", "--by-role", "Product Owner",
                          "--statement", "Apply it.")
        self.assertEqual((rc, out["status"]), (0, "APPLIED"))
        rc, out = self.ba(p, "baseline", "create")
        self.assertEqual(out["error"]["code"], "IMPACT_REVIEW_PENDING", "after apply the existing impact-review rules take over")

    def test_cli_source_check_only_explains(self):
        p = self.to_baseline()
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        rc, out = self.ba(p, "source", "check")
        self.assertEqual(rc, 0)
        self.assertEqual(out["changes"][0]["status"], "DETECTED")
        self.assertIn("NOTHING downstream", out["next"])
        self.assertEqual(Project.open(p.root).requirement("REQ-004")["status"], "ACTIVE")
        rc, out = self.ba(p, "changes", "check", "--no-record")
        self.assertEqual(out["changes"][0]["change_id"], "CHG-001")
        rc, prev = self.ba(p, "changes", "check", "--no-record")
        self.assertEqual(len(changes.records(p)), 1)

    def test_formatting_only_change_never_blocks_the_workflow(self):
        p = self.to_baseline()
        self.edit_brd(lambda t: t.replace("A member may borrow", "A member **may** borrow"))
        rc, out = self.ba(p, "story", "add", "--file", str(self.tmp / "stories.json"))
        self.assertNotEqual(out.get("error", {}).get("code"), "CHANGES_UNREVIEWED")
        self.assertEqual([r["classification"] for r in changes.records(p)], ["FORMATTING_ONLY"])

    def test_triage_runs_the_change_check_and_reports_without_blocking(self):
        p = self.two_approved()
        stories.add_stories(p, [self.story_item("Over-limit message", ("REQ-003",),
                                                acceptance_criteria=["Given five loans, when a sixth is attempted, then it is refused."])])
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        rc, out = self.ba(p, "story", "triage")
        self.assertEqual(rc, 0)
        self.assertEqual(out["change_check"]["unreviewed"], ["CHG-001"])
        self.assertIn("NOTHING downstream", out["change_check"]["note"])
        rc, out = self.ba(p, "gaps", "triage")
        self.assertEqual(out["change_check"]["unreviewed"], ["CHG-001"])

    def test_status_reports_changes_read_only_and_points_to_the_check(self):
        p = self.two_approved()
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        rc, st = self.ba(p, "status", "--json")
        self.assertEqual(st["changes"]["undetected"]["source"], "MATERIAL")
        self.assertIn("changes check", st["next_action"])
        self.assertEqual(changes.records(p), [], "status writes no change record")
        rc, txt = self.ba(p, "status")
        self.assertIn("NOT YET CHECKED", txt["raw"])

    def test_resume_in_a_new_session_keeps_detected_and_applied_changes(self):
        p = self.two_approved()
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        self.ba(p, "changes", "check")
        # --- new session: nothing in memory
        p2 = Project.open(p.root)
        rc, st = self.ba(p2, "status", "--json")
        self.assertEqual([c["change_id"] for c in st["changes"]["unreviewed"]], ["CHG-001"])
        self.assertIn("await the user's decision", st["next_action"])
        self.assertEqual(self.ba(p2, "changes", "check")[1]["changes"][0]["change_id"], "CHG-001", "no duplicate record after resuming")
        self.ba(p2, "changes", "apply", "--id", "CHG-001", "--by-name", "Priya Nair", "--by-role", "Product Owner", "--statement", "ok")
        # --- another new session in the middle of the review
        p3 = Project.open(p.root)
        rec = changes.get(p3, "CHG-001")
        self.assertEqual(rec["status"], "APPLIED")
        self.assertTrue([i for i in rec["items"] if i["state"] == "OPEN"])
        rc, st = self.ba(p3, "status", "--json")
        self.assertEqual(st["changes"]["awaiting_reconciliation"][0]["change_id"], "CHG-001")
        self.assertEqual(st["changes"]["stale"]["requirements_needing_review"], ["REQ-003", "REQ-004"])
        self.assertIn("reassess", st["next_action"].lower())
        # finish the work in the resumed session
        reqs.update_requirement(p3, "REQ-004", {"statement": "Borrowed books must be returned within 21 days.",
                                                "source_excerpt": "Borrowed books must be returned within 21 days."}, "BRD", "source-change")
        reqs.confirm_requirement(p3, "REQ-003", "Quoted text is unchanged.")
        rc, out = self.ba(p3, "source", "accept", "--by-name", "Priya Nair", "--by-role", "Product Owner", "--statement", "Accepted")
        self.assertEqual(rc, 0)
        self.assertEqual(out["changes_reconciled"] if "changes_reconciled" in out else [], [])
        done = {i["item_id"]: i["state"] for i in changes.get(Project.open(p.root), "CHG-001")["items"]}
        self.assertEqual(done["requirement:REQ-004"], "DONE")
        self.assertEqual(done["source_acceptance:source"], "DONE")

    def test_story_edit_survives_a_session_boundary_and_blocks_completion(self):
        p = self.two_approved()
        s = self.approved_json(p)
        s["title"] = "Register as a library member"
        self.write_approved_json(p, s)
        self.ba(p, "changes", "check")
        p2 = Project.open(p.root)
        rc, out = self.ba(p2, "complete")
        self.assertFalse(out["complete"])
        self.assertTrue(any("CHG-001" in r for r in out["outstanding"]))
        self.ba(p2, "changes", "apply", "--id", "CHG-001", "--by-name", "Priya Nair", "--by-role", "Product Owner", "--statement", "ok")
        rc, h = self.ba(Project.open(p.root), "story", "history", "US-001")
        self.assertEqual(h["awaiting_review"][0]["version"], 2)
        self.assertEqual(h["approved_versions"][0]["approval_statement"], "I approve US-001 v1.")

    def test_traceability_report_lists_change_impact_and_stale_artifacts(self):
        p = self.two_approved()
        self.edit_brd(lambda t: t.replace("A visitor shall register", "A visitor must register"))
        cid = changes.check(p)["changes"][0]["change_id"]
        changes.apply(p, cid, *PO, "apply")
        t = checks.traceability(p, write=True)
        self.assertIn("REQ-001", t["change_impact"]["stale_artifacts"]["requirements_needing_review"])
        self.assertIn("US-001", t["change_impact"]["stale_artifacts"]["stories_flagged"])
        md = (p.root / "reports" / "traceability-report.md").read_text(encoding="utf-8")
        self.assertIn("Change impact", md)
        self.assertIn(cid, md)
        # the approved story keeps its approval on record, flagged with the reason and the change id
        s = self.approved_json(p)
        self.assertEqual(s["status"], "APPROVED")
        self.assertEqual(s["impact_review"]["change_id"], cid)
        self.assertEqual(s["approval"]["statement"], "I approve US-001 v1.")

    def test_every_step_is_in_the_audit_log_with_ist_timestamps(self):
        p = self.two_approved()
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        cid = changes.check(p)["changes"][0]["change_id"]
        changes.apply(p, cid, *PO, "apply")
        evs = {e["event_type"]: e for e in p.audit_events()}
        for t in ("CHANGE_DETECTED", "CHANGE_APPLIED", "BRD_SNAPSHOT_RECORDED"):
            self.assertIn(t, evs)
            self.assertTrue(util.is_ist_iso(evs[t]["at_ist"]))
        rec = changes.get(p, cid)
        self.assertTrue(util.is_ist_iso(rec["detected_at_ist"]) and util.is_ist_iso(rec["applied"]["at_ist"]))
        self.assertEqual([h["event"] for h in rec["history"]], ["DETECTED", "APPLIED"])
        self.assertEqual(changes.status(p)["records"][0]["change_id"], cid)

    def test_library_source_check_still_works_in_one_step_and_leaves_a_record(self):
        p = self.to_baseline()
        self.edit_brd(lambda t: t.replace("within 14 days", "within 21 days"))
        out = reqs.source_check(p)
        self.assertTrue(out["changed"])
        rec = changes.records(p)[0]
        self.assertEqual(rec["status"], "APPLIED")
        self.assertIsNone(rec["applied"]["by_name"], "a direct library call carries no human acknowledgement - and says so")


if __name__ == "__main__":
    unittest.main()
