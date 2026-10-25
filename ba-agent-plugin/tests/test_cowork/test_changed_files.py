"""C1: in a cloud workspace only the files committed back reach the user, so a command must say exactly which files it wrote,
moved or removed (`--changed-files`), and `manifest` must show drift between a staged copy and the last committed state."""
import contextlib
import io
import json

from tests.helpers import BATest, gapmod
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

    def test_changes_check_still_sees_the_hand_edit_once_fresh_copies_are_staged(self):
        p = self.to_stories()
        ba(p.root, "story", "next", "--id", "US-001")
        ba(p.root, "story", "approve", "US-001", "--version", "1", "--statement", "Approved, it reads correctly.")
        story = p.root / "stories/approved/md/US-001.md"
        story.write_text(story.read_text(encoding="utf-8").replace("register", "sign up", 1), encoding="utf-8")
        out = ba(p.root, "changes", "check")
        self.assertTrue(out["ok"], out)
        self.assertTrue(out.get("changes") or out.get("warnings"), out)
