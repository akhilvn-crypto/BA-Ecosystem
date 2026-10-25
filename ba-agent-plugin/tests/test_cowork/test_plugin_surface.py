"""C4 and T2: the plugin folder may be read-only (no bytecode written into it), and the CLI reference is generated, not stored."""
import json
import shutil
import subprocess
import sys
import zipfile

from tests.helpers import BATest, FIXTURE, PLUGIN
from ba_lib import cli, helptext
from ba_lib.util import BAError


class NoBytecodeTests(BATest):
    def runTest(self):
        pass

    def test_running_the_core_from_a_fresh_copy_leaves_no_pycache_behind(self):
        copy = self.tmp / "plugin-copy"
        shutil.copytree(PLUGIN, copy, ignore=shutil.ignore_patterns("__pycache__", "tests", ".git"))
        out = self.tmp / "project"
        brd = self.brd_copy()
        for argv in (["find", "--dir", str(brd.parent)], ["init", "--brd", str(brd)], ["status"], ["help", "gap"]):
            r = subprocess.run([sys.executable, str(copy / "scripts" / "ba.py"), "--root", str(out), *argv],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(list(copy.rglob("__pycache__")), [], "the plugin folder must stay untouched")
        self.assertTrue((out / "state" / "workflow.json").exists(), "the core works with --root outside the plugin folder")


class GeneratedHelpTests(BATest):
    def runTest(self):
        pass

    def test_every_command_has_a_one_line_purpose_and_none_is_stale(self):
        rows = dict(helptext.commands(cli.build_parser()))
        self.assertEqual(sorted(set(rows) - set(helptext.PURPOSE)), [], "add a purpose for each new command")
        self.assertEqual(sorted(set(helptext.PURPOSE) - set(rows)), [], "a purpose for a command that no longer exists")

    def test_help_lists_groups_then_one_command_with_options_taken_from_the_parser(self):
        top = helptext.render(cli.build_parser())
        self.assertIn("gap", top["groups"])
        one = helptext.render(cli.build_parser(), "gap", "resolve")["commands"]
        self.assertEqual([c["cmd"] for c in one], ["gap resolve"])
        self.assertIn("--answer-id", one[0]["usage"])
        self.assertIn("RESOLVED", one[0]["does"])
        group = helptext.render(cli.build_parser(), "story")["commands"]
        self.assertGreater(len(group), 10)
        with self.assertRaises(BAError):
            helptext.render(cli.build_parser(), "nope")

    def test_help_is_far_smaller_than_the_static_reference_it_replaces(self):
        one = json.dumps(helptext.render(cli.build_parser(), "gap", "resolve"))
        self.assertLess(len(one), 1200)


class PackagingTests(BATest):
    def runTest(self):
        pass

    def test_the_upload_archive_leaves_out_housekeeping_files_and_keeps_what_runs(self):
        r = subprocess.run([sys.executable, str(PLUGIN / "scripts" / "package_plugin.py"), "--out", str(self.tmp / "dist")],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with zipfile.ZipFile(json.loads(r.stdout)["archive"]) as z:
            names = set(z.namelist())
        for gone in ("CHANGELOG.md", "docs/TEST_REPORT.md"):
            self.assertNotIn(gone, names)
        self.assertFalse([n for n in names if n.startswith(("tests/", ".in_use/")) or "__pycache__" in n])
        for kept in (".claude-plugin/plugin.json", "scripts/ba.py", "scripts/ba_lib/convert.py", "skills/ba-workflow/SKILL.md",
                     "skills/ba-workflow/references/cowork-sync.md", "skills/ba-workflow/references/stages/gap-resolution.md"):
            self.assertIn(kept, names)
        self.assertNotIn("agents/brd-analyst.md", names)
