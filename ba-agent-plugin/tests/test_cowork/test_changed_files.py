"""C1: in a cloud workspace only the files committed back reach the user, so a command must say exactly which files it wrote,
moved or removed (`--changed-files`), and `manifest` must show drift between a staged copy and the last committed state."""
import contextlib
import io
import json
import shutil

from tests.helpers import BATest, PLUGIN, gapmod, projmod, key
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

    def gaps_file(self, p, n=3):
        f = self.tmp / "gaps.json"
        f.write_text(json.dumps(self.gap_items()[:n], ensure_ascii=False), encoding="utf-8")
        return f

    def test_gaps_add_lists_only_the_files_it_wrote(self):
        p = self.new_project()
        out = ba(p.root, "gaps", "add", "--file", str(self.gaps_file(p, 2)))
        self.assertTrue(out["ok"], out)
        for want in ("gaps/open/md/GAP-001.md", "gaps/open/json/GAP-002.json", "state/audit.jsonl", "state/gap-register.json",
                     "enriched/lending.md"):
            self.assertIn(want, out["changed_files"])
        self.assertNotIn("enriched/membership.md", out["changed_files"], "a copy with no new block is not rewritten")
        self.assertFalse([x for x in out["changed_files"] if x.startswith(("source/", "snapshots/", "index/"))], "untouched files are not listed")
        self.assertNotIn("removed_files", out)
        self.assertIn("files_saved", out, "files_saved is kept as it was")
        self.assertEqual(out["files_saved"]["enriched"]["files"], 3)

    def test_the_supplied_requirement_files_are_never_listed(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        before = {f.name: f.read_bytes() for f in self.src.glob("*.md")}
        seen = []
        for argv in (("gaps", "next"), ("gap", "answer", "GAP-003", "--response", "Block it.", "--respondent-name", OWNER[0], "--respondent-role", OWNER[1])):
            out = ba(p.root, *argv)
            seen += out.get("changed_files", []) + out.get("removed_files", [])
        self.assertTrue(seen)
        self.assertFalse([x for x in seen if x.startswith(("../", "source/")) or x.endswith(("membership.md", "lending.md", "reporting.md")) and not x.startswith("enriched/")])
        self.assertEqual(before, {f.name: f.read_bytes() for f in self.src.glob("*.md")})

    def test_local_recovery_copies_and_helper_input_are_never_reported_or_hashed(self):
        p = self.with_gaps()
        (p.root / "state" / "inbox").mkdir(parents=True, exist_ok=True)
        (p.root / "state" / "inbox" / "gaps.json").write_text("[]", encoding="utf-8")
        self.set_owner(p, *OWNER)
        gapmod.next_gaps(p, 5)
        out = ba(p.root, "gap", "defer", "GAP-003", "--reason", "Waiting", "--by-name", OWNER[0], "--by-role", OWNER[1])
        self.assertTrue(out["ok"], out)
        self.assertTrue(list((p.root / "state" / "backups").glob("*.bak")), "the fixture must really have backup copies")
        self.assertFalse([x for x in out.get("changed_files", []) + out.get("removed_files", []) if x.startswith(("state/backups/", "state/inbox/"))])
        self.assertFalse([x for x in ba(p.root, "manifest", "--full")["files"] if x.startswith(("state/backups/", "state/inbox/"))])

    def test_without_the_flag_nothing_is_added(self):
        p = self.new_project()
        out = ba(p.root, "gaps", "add", "--file", str(self.gaps_file(p, 1)), changed=False)
        self.assertNotIn("changed_files", out)

    def test_gap_resolve_reports_the_move_between_folders(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        gapmod.next_gaps(p, 5)
        a = ba(p.root, "gap", "answer", "GAP-003", "--response", "Block the borrow and show a limit-reached message.",
               "--respondent-name", OWNER[0], "--respondent-role", OWNER[1])
        out = ba(p.root, "gap", "resolve", "GAP-003", "--answer-id", a["answer_id"],
                 "--interpretation", "A 6th concurrent borrow is rejected with a limit-reached message.")
        self.assertTrue(out["ok"], out)
        for want in ("gaps/resolved/md/GAP-003.md", "decisions/md/DEC-001.md", "decisions/json/decision-log.jsonl"):
            self.assertIn(want, out["changed_files"])
        self.assertIn("gaps/open/md/GAP-003.md", out["removed_files"])
        self.assertEqual(out["gap_file"]["folder"], "gaps/resolved")

    def test_ac_approve_reports_the_new_home_and_the_enriched_copy(self):
        p = self.to_acs(2)
        ba(p.root, "ac", "next", "--key", "LIB-001")
        out = ba(p.root, "ac", "approve", "LIB-001", "--version", "1", "--statement", "Approved, it reads correctly.")
        self.assertTrue(out["ok"], out)
        name = projmod.ac_fname(key("LIB-001"))
        self.assertIn(f"acceptance/approved/md/{name}.md", out["changed_files"])
        self.assertIn(f"acceptance/approved/json/{name}.json", out["changed_files"])
        self.assertIn("enriched/membership.md", out["changed_files"])
        self.assertIn(f"acceptance/candidates/md/{name}.md", out["removed_files"])
        self.assertIn(f"acceptance/candidates/json/{name}.json", out["removed_files"])
        self.assertTrue(out["enriched_copy"].endswith("membership.md"))

    def test_init_reports_every_file_it_created(self):
        root = self.tmp / "fresh"
        src = self.reqs_copy()
        out = ba(root, "init", "--requirements", str(src), "--name", "library")
        self.assertTrue(out["ok"], out)
        for want in ("state/workflow.json", "state/audit.jsonl", "index/json/requirements-index.json", "snapshots/source/index.json",
                     "snapshots/source/lending.md@v1.md", "enriched/lending.md", "enriched/reporting.md", "START-HERE.md"):
            self.assertIn(want, out["changed_files"], want)
        self.assertNotIn("enriched/README.md", out["changed_files"])

    def test_new_project_flow_init_then_bridge_on_then_commands_report_without_the_flag(self):
        root = self.tmp / "fresh"
        ba(root, "init", "--requirements", str(self.reqs_copy()), changed=False)
        on = ba(root, "bridge", "on", changed=False)
        self.assertEqual(on["bridge"], "on")
        out = ba(root, "owner", "set", "--name", OWNER[0], "--role", OWNER[1], changed=False)
        self.assertIn("state/workflow.json", out["changed_files"])

    def test_the_flag_works_before_or_after_the_command(self):
        p = self.new_project()
        for argv in (["--root", str(p.root), "--changed-files", "owner", "set", "--name", OWNER[0], "--role", OWNER[1]],
                     ["--root", str(p.root), "owner", "set", "--name", "Lee Chan", "--role", "Delivery Lead", "--changed-files"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli.main(argv)
            self.assertIn("state/workflow.json", json.loads(buf.getvalue())["changed_files"])

    def test_pretty_after_the_command_indents_the_json(self):
        p = self.new_project()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["--root", str(p.root), "owner", "show", "--pretty"])
        self.assertIn('\n  "', buf.getvalue())

    def test_a_command_without_the_flags_is_unchanged(self):
        p = self.new_project()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["--root", str(p.root), "owner", "show"])
        self.assertNotIn("changed_files", json.loads(buf.getvalue()))
        self.assertNotIn("\n", buf.getvalue().strip())

    def test_manifest_detects_a_hand_edited_enriched_copy(self):
        p = self.to_acs(1)
        self.approve(p, "LIB-001")
        first = ba(p.root, "manifest", "--write")
        self.assertEqual(first["written"], "state/manifest.json")
        self.assertTrue(ba(p.root, "manifest")["in_sync"])
        f = p.root / "enriched/membership.md"
        f.write_text(f.read_text(encoding="utf-8") + "\nEdited by hand.\n", encoding="utf-8")
        out = ba(p.root, "manifest")
        self.assertFalse(out["in_sync"])
        self.assertEqual(out["drift"]["edited"], ["enriched/membership.md"])
        f.unlink()
        self.assertEqual(ba(p.root, "manifest")["drift"]["missing"], ["enriched/membership.md"])

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
        self.assertFalse([x for x in staged["stage"] if "/md/" in x and not x.startswith(("enriched/", "snapshots/"))], "md views are rebuilt")
        self.assertIn("enriched/lending.md", staged["stage"])
        self.assertTrue(set(full) <= set(staged["stage"]), "the manifest covers exactly what is staged")
        lean = self.tmp / "workspace"
        for rel in staged["stage"]:
            (lean / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p.root / rel, lean / rel)
        self.assertLess(len(staged["stage"]), len(list(p.root.rglob("*.*"))), "something was left out")
        drift = ba(lean, "manifest")
        self.assertTrue(drift["in_sync"], drift)
        gapmod.next_gaps(p, 1, gap_id="GAP-003")  # presentation state lives in the project; stage it again
        shutil.copy2(p.root / "state" / "gap-register.json", lean / "state" / "gap-register.json")
        a = ba(lean, "gap", "answer", "GAP-003", "--response", "Block the borrow and show a limit-reached message.",
               "--respondent-name", OWNER[0], "--respondent-role", OWNER[1])
        self.assertTrue(a["ok"], a)
        out = ba(lean, "gap", "resolve", "GAP-003", "--answer-id", a["answer_id"],
                 "--interpretation", "A 6th concurrent borrow is rejected with a limit-reached message.")
        self.assertTrue(out["ok"], out)
        self.assertIn("gaps/resolved/md/GAP-003.md", out["changed_files"])
        self.assertIn("enriched/membership.md", out["changed_files"])  # the open-gap block for LIB-003 went away

    def test_bridge_on_makes_changed_files_the_default_until_it_is_turned_off(self):
        p = self.new_project()
        self.assertEqual(ba(p.root, "bridge", changed=False)["bridge"], "off")
        on = ba(p.root, "bridge", "on", changed=False)
        self.assertEqual(on["bridge"], "on")
        self.assertIn("state/bridge.json", on["changed_files"])
        self.assertIn("state/gap-register.json", ba(p.root, "gaps", "add", "--file", str(self.gaps_file(p, 1)), changed=False)["changed_files"])
        self.assertEqual(ba(p.root, "bridge", "off", changed=False)["bridge"], "off")
        self.assertNotIn("changed_files", ba(p.root, "owner", "show", changed=False))

    def test_lean_copy_with_bridge_on_does_not_warn_about_md_views_that_are_not_staged(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        ba(p.root, "manifest", "--write")
        lean = self.tmp / "workspace"
        for rel in ba(p.root, "manifest", "--stage-list")["stage"]:
            (lean / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p.root / rel, lean / rel)
        before = ba(lean, "validate", changed=False)
        self.assertTrue([w for w in before["warnings"] if "readable copy missing" in w], "without the bridge the missing views are reported")
        ba(lean, "bridge", "on", changed=False)
        out = ba(lean, "validate", changed=False)
        self.assertTrue(out["ok"], out)
        self.assertEqual([w for w in out["warnings"] if "readable copy missing" in w], [])
        self.assertIn("md_views", out)

    def test_changes_check_still_sees_the_hand_edit_once_fresh_copies_are_staged(self):
        p = self.to_acs(1)
        self.approve(p, "LIB-001")
        f = p.root / "enriched/membership.md"
        f.write_text(f.read_text(encoding="utf-8").replace("then the expected outcome is produced", "then a welcome page is shown", 1), encoding="utf-8")
        out = ba(p.root, "changes", "check")
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(out["changes"]), 1)
        self.assertEqual(out["changes"][0]["subject"]["edited_via"], "enriched")
