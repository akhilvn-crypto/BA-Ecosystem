"""Every artifact folder keeps readable copies in md/ and machine records in json/, and every stage proves its files are saved."""
import json
import subprocess
import sys

from tests.helpers import BATest, PLUGIN, basemod, checks, flatten_to_legacy, gapmod, projmod, reqs, stories  # noqa: F401
from ba_lib.project import SPLIT_DIRS, Project, physical

BA = str(PLUGIN / "scripts" / "ba.py")


def names(folder):
    return sorted(x.name for x in folder.glob("*") if x.is_file())


class PhysicalPathTests(BATest):
    def test_files_map_into_md_and_json_subfolders(self):
        self.assertEqual(physical("stories/approved/US-001.md"), "stories/approved/md/US-001.md")
        self.assertEqual(physical("stories/approved/US-001.json"), "stories/approved/json/US-001.json")
        self.assertEqual(physical("decisions/decision-log.jsonl"), "decisions/json/decision-log.jsonl")
        self.assertEqual(physical("baselines" + chr(92) + "baseline-v1.md"), "baselines/md/baseline-v1.md")
        for untouched in ("state/workflow.json", "reports/workflow-summary.md", "snapshots/stories/US-001.v1.json", "stories/approved",
                          "stories/approved/md/US-001.md"):
            self.assertEqual(physical(untouched), untouched)

    def test_new_project_has_md_and_json_folders_ready(self):
        p = self.new_project()
        for d in SPLIT_DIRS:
            self.assertTrue((p.root / d / "md").is_dir() and (p.root / d / "json").is_dir(), d)
        self.assertTrue((p.root / "extracted" / "md" / "brd-outline.md").exists())
        self.assertTrue((p.root / "extracted" / "json" / "source-metadata.json").exists())
        self.assertEqual(names(p.root / "extracted"), [], "no loose files next to the md/ and json/ folders")


class StageSavesFilesTests(BATest):
    def test_each_stage_writes_readable_and_machine_copies_in_their_own_folders(self):
        p = self.to_stories()
        r = p.root
        self.assertEqual(len(names(r / "atomic-requirements/md")), 8)
        self.assertEqual(names(r / "atomic-requirements/md"), [n.replace(".json", ".md") for n in names(r / "atomic-requirements/json")])
        self.assertIn("gap-register.json", names(r / "gaps/json"))
        self.assertIn("GAP-001.md", names(r / "gaps/md"))
        self.assertIn("decision-log.jsonl", names(r / "decisions/json"))
        self.assertIn("DEC-001.md", names(r / "decisions/md"))
        self.assertEqual(names(r / "baselines/md"), ["baseline-v1.md"])
        self.assertEqual(names(r / "baselines/json"), ["baseline-manifest.json", "baseline-v1.json"])
        self.assertEqual(names(r / "stories/candidates/md"), ["US-001.md", "US-002.md"])
        self.assertEqual(names(r / "stories/candidates/json"), ["US-001.json", "US-002.json"])
        for d in SPLIT_DIRS:
            self.assertEqual(names(r / d), [], f"{d}/ must hold only md/ and json/")
        stories.approve(p, "US-001", 1, "Approved.", "Priya Nair", "Product Owner")
        self.assertEqual(names(r / "stories/approved/md"), ["US-001.md"])
        self.assertEqual(names(r / "stories/approved/json"), ["US-001.json"])
        self.assertEqual(names(r / "stories/candidates/md"), ["US-002.md"])
        stories.request_changes(p, "US-002", 1, "Needs work")
        self.assertEqual(names(r / "stories/unapproved/md"), ["US-002.md"])
        self.assertEqual(names(r / "stories/unapproved/json"), ["US-002.json"])
        self.assertEqual(names(r / "stories/candidates/md") + names(r / "stories/candidates/json"), [])

    def test_receipt_lists_every_populated_folder_with_absolute_paths(self):
        p = self.to_stories()
        rc = p.receipt()
        self.assertEqual(rc["baselines"]["md_folder"], str(p.root / "baselines" / "md"))
        self.assertEqual((rc["stories/candidates"]["md_files"], rc["stories/candidates"]["json_files"]), (2, 2))
        self.assertEqual(rc["atomic-requirements"]["md_files"], 8)
        self.assertNotIn("stories/approved", rc, "an empty folder is not reported as saved work")

    def test_returned_locations_point_at_the_real_file(self):
        p = self.to_stories()
        out = stories.approve(p, "US-001", 1, "Approved.", "Priya Nair", "Product Owner")
        self.assertEqual(out["location"], "stories/approved/md/US-001.md")
        self.assertTrue((p.root / out["location"]).is_file())
        self.assertEqual(out["saved_to"], str(p.root / "stories/approved/md/US-001.md"))


class LegacyLayoutMigrationTests(BATest):
    def test_flat_project_is_split_on_open_without_losing_or_changing_anything(self):
        p = self.to_stories()
        stories.approve(p, "US-001", 1, "Approved.", "Priya Nair", "Product Owner")
        before = {x.name: x.read_bytes() for d in SPLIT_DIRS for x in (p.root / d).rglob("*") if x.is_file()}
        flatten_to_legacy(p.root)
        self.assertTrue((p.root / "stories/approved/US-001.json").exists())
        q = Project.open(p.root)
        after = {x.name: x.read_bytes() for d in SPLIT_DIRS for x in (p.root / d).rglob("*") if x.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(names(p.root / "stories/approved"), [])
        self.assertEqual(q.read_json("stories/approved/US-001.json")["status"], "APPROVED")
        self.assertTrue(checks.validate_project(q)["ok"])
        Project.open(p.root)  # idempotent

    def test_conflicting_loose_file_is_never_overwritten(self):
        p = self.to_stories()
        loose = p.root / "stories/candidates/US-001.md"
        loose.write_text("my own notes", encoding="utf-8")
        Project.open(p.root)
        self.assertEqual(loose.read_text(encoding="utf-8"), "my own notes")
        self.assertTrue((p.root / "stories/candidates/md/US-001.md").read_text(encoding="utf-8").startswith("## US-001"))


class CliReceiptTests(BATest):
    def ba(self, root, *args):
        r = subprocess.run([sys.executable, BA, "--root", str(root), *args], capture_output=True, text=True, encoding="utf-8")
        return json.loads(r.stdout)

    def test_writing_commands_report_where_files_are_and_read_only_ones_do_not(self):
        p = self.to_stories()
        out = self.ba(p.root, "story", "approve", "US-001", "--version", "1", "--statement", "Approved")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["files_saved"]["stories/approved"]["md_files"], 1)
        self.assertEqual(out["files_saved"]["stories/approved"]["md_folder"], str(p.root / "stories/approved/md"))
        self.assertNotIn("files_saved", self.ba(p.root, "story", "show", "US-002"))
        self.assertNotIn("files_saved", self.ba(p.root, "gaps", "list"))

    def test_files_command_lists_folders(self):
        p = self.to_stories()
        out = self.ba(p.root, "files")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["project_folder"], str(p.root))
        self.assertEqual(out["folders"]["baselines"]["md_files"], 1)
