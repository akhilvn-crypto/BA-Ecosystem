"""Every artifact folder keeps readable copies in md/ and machine records in json/, the enriched copies sit in their own folder,
the supplied requirements are never written to, and every stage proves its files are saved."""
import json
import subprocess
import sys

from tests.helpers import BATest, FIXTURE, PLUGIN, acs, checks, gapmod, key, projmod, util
from ba_lib.project import SPLIT_DIRS, Project, physical
from ba_lib.util import BAError

BA = str(PLUGIN / "scripts" / "ba.py")


def names(folder):
    return sorted(x.name for x in folder.glob("*") if x.is_file())


def tree(folder):
    return {x.relative_to(folder).as_posix(): x.read_bytes() for x in folder.rglob("*") if x.is_file()}


class PhysicalPathTests(BATest):
    def test_files_map_into_md_and_json_subfolders(self):
        self.assertEqual(physical("acceptance/approved/lending.md--LIB-006.md"), "acceptance/approved/md/lending.md--LIB-006.md")
        self.assertEqual(physical("acceptance/approved/lending.md--LIB-006.json"), "acceptance/approved/json/lending.md--LIB-006.json")
        self.assertEqual(physical("index/requirements-index.json"), "index/json/requirements-index.json")
        self.assertEqual(physical("index/requirements-index.md"), "index/md/requirements-index.md")
        self.assertEqual(physical("decisions/decision-log.jsonl"), "decisions/json/decision-log.jsonl")
        self.assertEqual(physical("gaps/open/GAP-001.md"), "gaps/open/md/GAP-001.md")
        self.assertEqual(physical("changes" + chr(92) + "CHG-001.md"), "changes/md/CHG-001.md")
        for untouched in ("state/workflow.json", "state/enriched-manifest.json", "reports/workflow-summary.md", "snapshots/source/index.json",
                          "snapshots/ac/index.json", "enriched/membership.md", "acceptance/approved", "acceptance/approved/md/x.md"):
            self.assertEqual(physical(untouched), untouched)

    def test_split_dirs_are_the_v2_artifact_folders(self):
        self.assertEqual(set(SPLIT_DIRS), {"index", "decisions", "changes", "gaps/open", "gaps/deferred", "gaps/resolved", "acceptance/candidates",
                                           "acceptance/approved", "acceptance/unapproved", "acceptance/deferred", "acceptance/archive"})

    def test_new_project_has_md_and_json_folders_ready(self):
        p = self.new_project()
        for d in SPLIT_DIRS:
            self.assertTrue((p.root / d / "md").is_dir() and (p.root / d / "json").is_dir(), d)
        self.assertTrue((p.root / "index" / "md" / "requirements-index.md").exists())
        self.assertTrue((p.root / "index" / "json" / "requirements-index.json").exists())
        self.assertEqual(names(p.root / "index"), [], "no loose files next to the md/ and json/ folders")
        for gone in ("extracted", "atomic-requirements", "baselines", "stories"):
            self.assertFalse((p.root / gone).exists(), gone)

    def test_new_project_snapshots_the_source_and_renders_one_enriched_copy_per_requirements_file(self):
        p = self.new_project()
        r = p.root
        self.assertEqual(names(r / "snapshots/source"), ["index.json", "lending.md@v1.md", "membership.md@v1.md", "reporting.md@v1.md"])
        self.assertEqual(names(r / "enriched"), ["lending.md", "membership.md", "reporting.md"], "README.md is skipped")
        self.assertTrue((r / "state/enriched-manifest.json").is_file())
        self.assertEqual(sorted(json.loads((r / "state/enriched-manifest.json").read_text(encoding="utf-8"))["files"]),
                         ["lending.md", "membership.md", "reporting.md"])
        for rel in ("lending.md", "membership.md", "reporting.md"):
            self.assertEqual((r / "snapshots/source" / f"{rel}@v1.md").read_bytes(), (FIXTURE / rel).read_bytes(), "snapshot is the exact source")
        self.assertTrue((r / "snapshots/ac").is_dir())


class SourceIsNeverWrittenTests(BATest):
    def test_a_whole_workflow_leaves_the_supplied_folder_byte_identical(self):
        src = self.reqs_copy()
        before = tree(src)
        p = self.with_gaps(src=src)
        self.to_acs(3, p)
        for rid in ("LIB-001", "LIB-002", "LIB-003"):
            self.approve(p, rid)
        checks.complete_workflow(p)
        checks.recover(p)
        self.assertEqual(tree(src), before)
        self.assertEqual(sorted(x.name for x in src.iterdir()), sorted(before))

    def test_the_project_folder_must_be_outside_the_requirements_folder(self):
        src = self.reqs_copy()
        for bad in (src, src / "ba-output", src / "deep" / "out"):
            with self.assertRaises(BAError) as c:
                Project.create(bad, src)
            self.assertEqual(c.exception.code, "OUTPUT_INSIDE_SOURCE", bad)
        with self.assertRaises(BAError) as c:
            Project.create(self.tmp / "out", src, enriched_dir=str(src / "enriched"))
        self.assertEqual(c.exception.code, "OUTPUT_INSIDE_SOURCE")
        self.assertEqual(tree(src), tree(self.reqs_copy("other")), "nothing was created in the source")

    def test_writes_to_the_source_are_refused_by_the_core(self):
        p = self.new_project()
        target = self.src / "lending.md"
        with self.assertRaises(BAError) as c:
            p._guard_source(target)
        self.assertEqual(c.exception.code, "SOURCE_PROTECTED")
        with self.assertRaises(BAError):
            p.write_enriched("../../inputs/reqs/lending.md", b"x")

    def test_a_file_that_is_an_enriched_copy_is_refused_as_source(self):
        p = self.new_project()
        copy_dir = self.tmp / "fed-back"
        copy_dir.mkdir()
        (copy_dir / "lending.md").write_bytes((p.enriched_root / "lending.md").read_bytes())
        with self.assertRaises(BAError) as c:
            Project.create(self.tmp / "out2", copy_dir)
        self.assertEqual(c.exception.code, "ENRICHED_FILE_AS_SOURCE")
        self.assertIn("lending.md", c.exception.details["files"])

    def test_enriched_copies_can_go_to_another_folder_outside_the_project(self):
        shared = self.tmp / "shared-docs"
        p = self.new_project(enriched_dir=str(shared))
        gapmod.add_gaps(p, self.gap_items())
        self.assertEqual(names(shared), ["lending.md", "membership.md", "reporting.md"])
        self.assertFalse((p.root / "enriched").exists())
        self.to_acs(1, p)
        self.approve(p, "LIB-001")
        self.assertIn("AC v1", (shared / "membership.md").read_text(encoding="utf-8"))
        self.assertEqual(p.receipt()["enriched"]["folder"], str(shared.resolve()))


class StageSavesFilesTests(BATest):
    def test_each_stage_writes_readable_and_machine_copies_in_their_own_folders(self):
        p = self.to_acs(2)
        r = p.root
        self.assertEqual(len(names(r / "index/json")), 1)
        self.assertTrue((r / "state/gap-register.json").is_file())
        self.assertEqual(names(r / "gaps/resolved/md"), [f"GAP-{n:03d}.md" for n in range(1, 4)])
        self.assertIn("decision-log.jsonl", names(r / "decisions/json"))
        self.assertIn("DEC-001.md", names(r / "decisions/md"))
        a, b = projmod.ac_fname(key("LIB-001")), projmod.ac_fname(key("LIB-002"))
        self.assertEqual(names(r / "acceptance/candidates/md"), sorted([f"{a}.md", f"{b}.md"]))
        self.assertEqual(names(r / "acceptance/candidates/json"), sorted([f"{a}.json", f"{b}.json"]))
        for d in SPLIT_DIRS:
            self.assertEqual(names(r / d), [], f"{d}/ must hold only md/ and json/")
        acs.approve(p, "LIB-001", 1, "Approved.", "Priya Nair", "Product Owner")
        self.assertEqual(names(r / "acceptance/approved/md"), [f"{a}.md"])
        self.assertEqual(names(r / "acceptance/approved/json"), [f"{a}.json"])
        self.assertEqual(names(r / "acceptance/candidates/md"), [f"{b}.md"])
        self.assertIn("AC v1", (r / "enriched/membership.md").read_text(encoding="utf-8"))
        self.assertTrue((r / f"snapshots/ac/{a}.v1.json").is_file(), "an immutable copy of exactly what was approved")
        acs.request_changes(p, "LIB-002", 1, "Needs work")
        self.assertEqual(names(r / "acceptance/unapproved/md"), [f"{b}.md"])
        self.assertEqual(names(r / "acceptance/unapproved/json"), [f"{b}.json"])
        self.assertEqual(names(r / "acceptance/candidates/md") + names(r / "acceptance/candidates/json"), [])

    def test_receipt_lists_every_populated_folder_with_absolute_paths(self):
        p = self.to_acs(2)
        rc = p.receipt()
        self.assertEqual(rc["index"]["md_folder"], str(p.root / "index" / "md"))
        self.assertEqual((rc["acceptance/candidates"]["md_files"], rc["acceptance/candidates"]["json_files"]), (2, 2))
        self.assertEqual(rc["gaps/resolved"]["md_files"], 3)
        self.assertEqual(rc["enriched"]["files"], 3)
        self.assertNotIn("acceptance/approved", rc, "an empty folder is not reported as saved work")

    def test_returned_locations_point_at_the_real_file(self):
        p = self.to_acs(1)
        out = acs.approve(p, "LIB-001", 1, "Approved.", "Priya Nair", "Product Owner")
        name = projmod.ac_fname(key("LIB-001"))
        self.assertEqual(out["location"], f"acceptance/approved/md/{name}.md")
        self.assertTrue((p.root / out["location"]).is_file())
        self.assertEqual(out["saved_to"], str(p.root / f"acceptance/approved/md/{name}.md"))
        self.assertEqual(out["enriched_copy"], str(p.root / "enriched" / "membership.md"))
        self.assertTrue((p.root / "enriched" / "membership.md").is_file())

    def test_generated_files_use_lf_and_the_enriched_copy_keeps_the_sources_line_endings(self):
        src = self.reqs_copy()
        (src / "crlf.md").write_bytes(b"# CRLF doc\r\n\r\n### LIB-050 Windows requirement\r\nThe system shall keep Windows line endings.\r\n")
        p = self.new_project(src=src)
        raw = (p.enriched_root / "crlf.md").read_bytes()
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), "no bare LF in a CRLF document")
        self.assertNotIn(b"\r", (p.root / "state" / "workflow.json").read_bytes())


class CliReceiptTests(BATest):
    def ba(self, root, *args):
        r = subprocess.run([sys.executable, BA, "--root", str(root), *args], capture_output=True, text=True, encoding="utf-8")
        return json.loads(r.stdout)

    def test_writing_commands_report_where_files_are_and_read_only_ones_do_not(self):
        p = self.to_acs(2)
        out = self.ba(p.root, "ac", "approve", "LIB-001", "--version", "1", "--statement", "Approved")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["files_saved"]["acceptance/approved"]["md_files"], 1)
        self.assertEqual(out["files_saved"]["acceptance/approved"]["md_folder"], str(p.root / "acceptance/approved/md"))
        self.assertEqual(out["files_saved"]["enriched"]["files"], 3)
        self.assertNotIn("files_saved", self.ba(p.root, "ac", "show", "LIB-002"))
        self.assertNotIn("files_saved", self.ba(p.root, "gaps", "list"))
        self.assertNotIn("files_saved", self.ba(p.root, "req", "list"))

    def test_files_command_lists_folders(self):
        p = self.to_acs(1)
        out = self.ba(p.root, "files")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["project_folder"], str(p.root))
        self.assertEqual(out["enriched_folder"], str(p.root / "enriched"))
        self.assertEqual(out["folders"]["index"]["md_files"], 1)
        self.assertEqual(out["folders"]["acceptance/candidates"]["md_files"], 1)

    def test_init_creates_the_project_with_an_enriched_copy_and_a_start_here_page(self):
        out = self.tmp / "cli-out"
        r = subprocess.run([sys.executable, BA, "--root", str(out), "init", "--requirements", str(FIXTURE)], capture_output=True, text=True,
                           encoding="utf-8")
        res = json.loads(r.stdout)
        self.assertTrue(res["ok"], r.stdout)
        self.assertEqual((res["stage"], res["source"]["requirements"], res["source"]["files"]), ("SOURCE_INDEXED", 9, 3))
        self.assertEqual(names(out / "enriched"), ["lending.md", "membership.md", "reporting.md"])
        start = (out / "START-HERE.md").read_text(encoding="utf-8")
        self.assertIn("never changed by the BA Agent", start)
        self.assertIn("9 in 3 file(s)", start)
