"""Cowork bridge with enriched copies: the source is input only (hash-checked, never committed back); the project folder and enriched/ are."""
import shutil

from tests.helpers import BATest, gapmod, key, sync
from tests.test_cowork.test_changed_files import OWNER, ba

ALLOWED_TOPS = ("state/", "reports/", "snapshots/", "index/", "decisions/", "changes/", "gaps/", "acceptance/", "enriched/", "START-HERE.md", "ADR-LOG.md")


class CoworkEnrichedTests(BATest):
    def runTest(self):
        pass

    def all_changed(self, outs):
        paths = []
        for o in outs:
            paths += o.get("changed_files", []) + o.get("removed_files", [])
        return paths

    def test_enriched_is_staged_and_hashed_but_the_source_is_not_in_the_project_tree(self):
        p = self.to_acs(1)
        self.approve(p, "LIB-001")
        staged = ba(p.root, "manifest", "--stage-list")["stage"]
        self.assertTrue({"enriched/membership.md", "enriched/lending.md", "enriched/reporting.md"} <= set(staged))
        self.assertFalse([x for x in staged if "/md/" in x and not x.startswith(("enriched/", "snapshots/"))])
        self.assertTrue(set(ba(p.root, "manifest", "--full")["files"]) <= set(staged))
        self.assertIn("enriched/membership.md", ba(p.root, "manifest", "--full")["files"])
        note = ba(p.root, "manifest", "--stage-list")["also"]
        self.assertIn("input only", note)
        self.assertFalse([x for x in staged if x.endswith("README.md")])

    def test_only_the_project_folder_and_enriched_appear_in_changed_files(self):
        p = self.with_gaps()
        ba(p.root, "bridge", "on", changed=False)
        outs = [ba(p.root, "owner", "set", "--name", OWNER[0], "--role", OWNER[1]), ba(p.root, "gaps", "next", "--limit", "3")]
        a = ba(p.root, "gap", "answer", "GAP-003", "--response", "Block it.", "--respondent-name", OWNER[0], "--respondent-role", OWNER[1])
        outs.append(a)
        outs.append(ba(p.root, "gap", "resolve", "GAP-003", "--answer-id", a["answer_id"], "--interpretation", "A 6th borrow is rejected."))
        outs.append(ba(p.root, "gap", "carry-forward", "GAP-002", "--by-name", OWNER[0], "--by-role", OWNER[1], "--reason", "later"))
        outs.append(ba(p.root, "gap", "defer", "GAP-001", "--reason", "Waiting", "--by-name", OWNER[0], "--by-role", OWNER[1]))
        paths = self.all_changed(outs)
        self.assertTrue(paths)
        bad = [x for x in paths if not x.startswith(ALLOWED_TOPS)]
        self.assertEqual(bad, [], "nothing outside the project folder and enriched/ is committed back")
        self.assertFalse([x for x in paths if x.startswith("source/")])
        for f in self.src.glob("*.md"):
            self.assertNotIn(f.name, paths)

    def test_copied_source_is_written_once_at_init_and_never_again(self):
        root = self.tmp / "fresh"
        src = self.reqs_copy()
        out = ba(root, "init", "--requirements", str(src), "--copy-source")
        self.assertTrue(out["ok"], out)
        self.assertIn("source/reqs/lending.md", out["changed_files"])
        ba(root, "bridge", "on", changed=False)
        later = [ba(root, "owner", "set", "--name", OWNER[0], "--role", OWNER[1]), ba(root, "gaps", "add", "--file", str(self._gaps_file()))]
        self.assertFalse([x for x in self.all_changed(later) if x.startswith("source/")])
        self.assertIn("source/reqs/lending.md", ba(root, "manifest", "--stage-list")["stage"])  # staged as input so a session can check it

    def _gaps_file(self):
        import json
        f = self.tmp / "gaps.json"
        f.write_text(json.dumps(self.gap_items()[:1]), encoding="utf-8")
        return f

    def test_a_session_commits_back_exactly_the_reported_files(self):
        p = self.to_acs(2)
        user, lean = self.tmp / "user", self.tmp / "workspace"
        ba(p.root, "ac", "next", "--key", "LIB-001")
        shutil.copytree(p.root, user)
        for rel in sync.stage_list(p.root)["stage"]:
            (lean / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p.root / rel, lean / rel)
        out = ba(lean, "ac", "approve", "LIB-001", "--version", "1", "--statement", "Approved.")
        self.assertTrue(out["ok"], out)
        for rel in out.get("removed_files", []):
            (user / rel).unlink(missing_ok=True)
        for rel in out["changed_files"]:
            (user / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(lean / rel, user / rel)
        self.assertEqual((user / "enriched/membership.md").read_bytes(), (lean / "enriched/membership.md").read_bytes())
        self.assertIn('ac="v1"', (user / "enriched/membership.md").read_text(encoding="utf-8"))
        # the user's copy is a valid project, and the original requirement files never travelled
        self.assertEqual(ba(user, "validate", changed=False)["errors"], [])
        self.assertFalse((user / "membership.md").exists())

    def test_hand_edit_in_the_users_enriched_copy_is_seen_after_restaging(self):
        p = self.to_acs(1)
        self.approve(p, "LIB-001")
        ba(p.root, "manifest", "--write")
        lean = self.tmp / "workspace"
        f = p.root / "enriched/membership.md"
        f.write_text(f.read_text(encoding="utf-8").replace("then the expected outcome is produced", "then a welcome page is shown", 1), encoding="utf-8")
        self.assertEqual(ba(p.root, "manifest")["drift"]["edited"], ["enriched/membership.md"])
        for rel in sync.stage_list(p.root)["stage"]:
            (lean / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p.root / rel, lean / rel)
        out = ba(lean, "changes", "check")
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(out["unreviewed_ids"]), 1)

    def test_an_enriched_dir_outside_the_project_is_not_part_of_the_committed_tree(self):
        shared = self.tmp / "shared"
        p = self.new_project(enriched_dir=str(shared))
        out = ba(p.root, "gaps", "add", "--file", str(self._gaps_file()))
        self.assertTrue(out["ok"], out)
        self.assertFalse([x for x in out["changed_files"] if x.startswith("enriched/")])  # lives elsewhere: the person commits that folder
        self.assertIn("ba:begin", (shared / "lending.md").read_text(encoding="utf-8"))
        staged = ba(p.root, "manifest", "--stage-list")["stage"]
        self.assertFalse([x for x in staged if x.startswith("enriched/")])
