"""C1: in a cloud workspace only the files committed back reach the user, so a command must say exactly which files it wrote,
moved or removed (`--changed-files`), and `manifest` must show drift between a staged copy and the last committed state."""
import contextlib
import io
import json
import shutil

from tests.helpers import BATest, PLUGIN, gapmod
from ba_lib import cli

OWNER = ("Priya Nair", "Product Owner")


def ba(root, *argv, changed=True):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(["--root", str(root), *(["--changed-files"] if changed else []), *argv])
    return json.loads(buf.getvalue())


class ChangedFilesTests(BATest):
    def runTest(self):
        pass

    def test_req_add_lists_only_the_files_it_wrote(self):
        p = self.new_project()
        f = self.tmp / "reqs.json"
        f.write_text(json.dumps(self.req_items(p)[:2]), encoding="utf-8")
        out = ba(p.root, "req", "add", "--file", str(f))
        self.assertTrue(out["ok"], out)
        for want in ("atomic-requirements/md/REQ-001.md", "atomic-requirements/json/REQ-002.json", "state/audit.jsonl"):
            self.assertIn(want, out["changed_files"])
        self.assertFalse([x for x in out["changed_files"] if x.startswith(("source/", "snapshots/"))], "untouched files are not listed")
        self.assertNotIn("removed_files", out)
        self.assertIn("files_saved", out, "files_saved is kept as it was")

    def test_local_recovery_copies_and_helper_input_are_never_reported_or_hashed(self):
        p = self.new_project()
        (p.root / "state" / "inbox").mkdir(parents=True, exist_ok=True)
        (p.root / "state" / "inbox" / "reqs.json").write_text("[]", encoding="utf-8")
        f = self.tmp / "reqs.json"
        f.write_text(json.dumps(self.req_items(p)[:2]), encoding="utf-8")
        ba(p.root, "req", "add", "--file", str(f))
        u = self.tmp / "u.json"
        u.write_text(json.dumps({"statement": "A visitor registers with a valid email and a password to become a member."}), encoding="utf-8")
        out = ba(p.root, "req", "update", "REQ-001", "--reason", "Wording", "--cause", "correction",
                 "--by-name", OWNER[0], "--by-role", OWNER[1], "--file", str(u))
        self.assertTrue(out["ok"], out)
        self.assertTrue(list((p.root / "state" / "backups").glob("*.bak")), "the fixture must really have backup copies")
        self.assertFalse([x for x in out.get("changed_files", []) + out.get("removed_files", [])
                          if x.startswith(("state/backups/", "state/inbox/"))])
        self.assertFalse([x for x in ba(p.root, "manifest", "--full")["files"] if x.startswith(("state/backups/", "state/inbox/"))])

    def test_without_the_flag_nothing_is_added(self):
        p = self.new_project()
        f = self.tmp / "reqs.json"
        f.write_text(json.dumps(self.req_items(p)[:1]), encoding="utf-8")
        out = ba(p.root, "req", "add", "--file", str(f), changed=False)
        self.assertNotIn("changed_files", out)

    def test_gap_resolve_reports_the_move_between_folders(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        gapmod.next_gaps(p, 1, gap_id="GAP-003")
        a = gapmod.record_answer(p, "GAP-003", "Block the borrow and show a limit-reached message.", None, None)
        out = ba(p.root, "gap", "resolve", "GAP-003", "--answer-id", a["answer_id"],
                 "--interpretation", "A 6th concurrent borrow is rejected with a limit-reached message.")
        self.assertTrue(out["ok"], out)
        self.assertIn("gaps/resolved/md/GAP-003.md", out["changed_files"])
        self.assertIn("gaps/open/md/GAP-003.md", out["removed_files"], "a move is one removal plus one new path")
        self.assertTrue(any(x.startswith("decisions/md/DEC-") for x in out["changed_files"]))
        self.assertFalse((p.root / "gaps/open/md/GAP-003.md").exists())

    def test_story_approve_reports_the_new_home_of_the_story(self):
        p = self.to_stories()
        ba(p.root, "story", "next", "--id", "US-001")
        out = ba(p.root, "story", "approve", "US-001", "--version", "1", "--statement", "Approved, it reads correctly.")
        self.assertTrue(out["ok"], out)
        self.assertIn("stories/approved/md/US-001.md", out["changed_files"])
        self.assertIn("stories/candidates/md/US-001.md", out["removed_files"])

    def test_init_reports_every_file_it_created(self):
        out = ba(self.tmp / "fresh", "init", "--brd", str(self.brd_copy()))
        self.assertTrue(out["ok"], out)
        for want in ("state/workflow.json", "START-HERE.md", "extracted/md/brd-outline.md"):
            self.assertIn(want, out["changed_files"])
        self.assertTrue([x for x in out["changed_files"] if x.startswith("snapshots/")], "the BRD snapshot is reported too")

    def test_new_project_flow_init_then_bridge_on_then_commands_report_without_the_flag(self):
        root = self.tmp / "fresh"
        init = ba(root, "init", "--brd", str(self.brd_copy()))
        self.assertTrue(init["ok"], init)
        on = ba(root, "bridge", "on", changed=False)
        self.assertEqual(on["bridge"], "on")
        self.assertIn("state/bridge.json", on["changed_files"])
        from ba_lib.project import Project
        f = self.tmp / "reqs.json"
        f.write_text(json.dumps(self.req_items(Project.open(root))[:1]), encoding="utf-8")
        out = ba(root, "req", "add", "--file", str(f), changed=False)
        self.assertIn("atomic-requirements/json/REQ-001.json", out["changed_files"], "no flag needed after bridge on")

    def test_the_new_project_path_tells_the_model_to_turn_the_bridge_on(self):
        text = (PLUGIN / "skills/ba-workflow/references/stages/brd-ingestion.md").read_text(encoding="utf-8")
        self.assertIn('--root "<output folder>" --changed-files init --brd "<BRD path>"`, then `bridge on`', text)

    def test_init_accepts_the_flag_after_the_command(self):
        out = ba(self.tmp / "fresh", "init", "--brd", str(self.brd_copy()), "--changed-files", changed=False)
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["changed_files"])

    def test_the_flag_works_before_or_after_the_command(self):
        p = self.new_project()
        self.assertTrue(ba(p.root, "owner", "show", "--changed-files", changed=False)["ok"])
        self.assertTrue(ba(p.root, "--changed-files", "owner", "show", changed=False)["ok"])

    def test_pretty_after_the_command_indents_the_json(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["--root", str(p.root), "gaps", "next", "--limit", "1", "--pretty"])
        text = buf.getvalue()
        self.assertTrue(json.loads(text)["ok"])
        self.assertIn('\n  "ok"', text)

    def test_a_command_without_the_flags_is_unchanged(self):
        p = self.new_project()
        out = ba(p.root, "owner", "show", changed=False)
        self.assertNotIn("changed_files", out)


class ManifestTests(BATest):
    def runTest(self):
        pass

    def test_manifest_detects_a_hand_edited_approved_story(self):
        p = self.to_stories()
        ba(p.root, "story", "next", "--id", "US-001")
        ba(p.root, "story", "approve", "US-001", "--version", "1", "--statement", "Approved, it reads correctly.")
        first = ba(p.root, "manifest", "--write")
        self.assertEqual(first["written"], "state/manifest.json")
        self.assertTrue(ba(p.root, "manifest")["in_sync"])
        story = p.root / "stories/approved/md/US-001.md"
        story.write_text(story.read_text(encoding="utf-8") + "\nEdited by hand.\n", encoding="utf-8")
        out = ba(p.root, "manifest")
        self.assertFalse(out["in_sync"])
        self.assertEqual(out["drift"]["edited"], ["stories/approved/md/US-001.md"])
        story.unlink()
        out = ba(p.root, "manifest")
        self.assertEqual(out["drift"]["missing"], ["stories/approved/md/US-001.md"])

    def test_a_brand_new_project_has_no_committed_manifest_yet(self):
        p = self.new_project()
        out = ba(p.root, "manifest")
        self.assertIn("committed_manifest", out)
        self.assertGreater(out["count"], 5)
        self.assertNotIn("files", out)
        self.assertIn("files", ba(p.root, "manifest", "--full"))

    def test_stage_list_is_lean_and_a_session_works_from_exactly_that_copy(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        ba(p.root, "manifest", "--write")
        full = ba(p.root, "manifest", "--full")["files"]
        staged = ba(p.root, "manifest", "--stage-list")
        self.assertEqual(staged["count"], len(staged["stage"]))
        self.assertFalse([x for x in staged["stage"] if x.startswith(("state/backups/", "state/inbox/", "reports/")) or x.endswith(".bak")])
        self.assertFalse([x for x in staged["stage"] if "/md/" in x and not x.startswith(("stories/approved/md/", "extracted/"))], "md views are rebuilt")
        self.assertTrue(set(full) <= set(staged["stage"]), "the manifest covers exactly what is staged")
        lean = self.tmp / "workspace"
        for rel in staged["stage"]:
            (lean / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p.root / rel, lean / rel)
        self.assertLess(len(staged["stage"]), len(list(p.root.rglob("*.*"))), "something was left out")
        drift = ba(lean, "manifest"); self.assertTrue(drift["in_sync"], drift)
        gapmod.next_gaps(p, 1, gap_id="GAP-003")  # presentation state lives in the project; stage it again
        shutil.copy2(p.root / "state" / "gap-register.json", lean / "state" / "gap-register.json")
        a = ba(lean, "gap", "answer", "GAP-003", "--response", "Block the borrow and show a limit-reached message.",
               "--respondent-name", OWNER[0], "--respondent-role", OWNER[1])
        self.assertTrue(a["ok"], a)
        out = ba(lean, "gap", "resolve", "GAP-003", "--answer-id", a["answer_id"],
                 "--interpretation", "A 6th concurrent borrow is rejected with a limit-reached message.")
        self.assertTrue(out["ok"], out)
        self.assertIn("gaps/resolved/md/GAP-003.md", out["changed_files"])

    def test_bridge_on_makes_changed_files_the_default_until_it_is_turned_off(self):
        p = self.new_project()
        self.assertEqual(ba(p.root, "bridge", changed=False)["bridge"], "off")
        on = ba(p.root, "bridge", "on", changed=False)
        self.assertEqual(on["bridge"], "on")
        self.assertIn("state/bridge.json", on["changed_files"])
        f = self.tmp / "reqs.json"
        f.write_text(json.dumps(self.req_items(p)[:1]), encoding="utf-8")
        self.assertIn("atomic-requirements/json/REQ-001.json", ba(p.root, "req", "add", "--file", str(f), changed=False)["changed_files"])
        self.assertEqual(ba(p.root, "bridge", "off", changed=False)["bridge"], "off")
        f.write_text(json.dumps(self.req_items(p)[1:2]), encoding="utf-8")
        self.assertNotIn("changed_files", ba(p.root, "req", "add", "--file", str(f), changed=False))

    def test_lean_copy_with_bridge_on_does_not_warn_about_md_views_that_are_not_staged(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        ba(p.root, "manifest", "--write")
        lean = self.tmp / "workspace"
        for rel in ba(p.root, "manifest", "--stage-list")["stage"]:
            (lean / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p.root / rel, lean / rel)
        before = ba(lean, "validate", changed=False)
        self.assertTrue([w for w in before["warnings"] if "md" in w], "without the bridge the missing views are reported")
        ba(lean, "bridge", "on", changed=False)
        out = ba(lean, "validate", changed=False)
        self.assertTrue(out["ok"], out)
        self.assertEqual([w for w in out["warnings"] if "rendered .md missing" in w or "readable copy missing" in w], [])
        self.assertIn("md_views", out)

    def test_changes_check_still_sees_the_hand_edit_once_fresh_copies_are_staged(self):
        p = self.to_stories()
        ba(p.root, "story", "next", "--id", "US-001")
        ba(p.root, "story", "approve", "US-001", "--version", "1", "--statement", "Approved, it reads correctly.")
        story = p.root / "stories/approved/md/US-001.md"
        story.write_text(story.read_text(encoding="utf-8").replace("register", "sign up", 1), encoding="utf-8")
        out = ba(p.root, "changes", "check")
        self.assertTrue(out["ok"], out)
        self.assertTrue(out.get("changes") or out.get("warnings"), out)
