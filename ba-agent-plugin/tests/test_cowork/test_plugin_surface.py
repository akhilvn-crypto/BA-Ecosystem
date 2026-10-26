"""C4 and T2: the plugin folder may be read-only (no bytecode written into it), the CLI reference is generated, not stored,
and the packaged plugin and its manifests describe the 2.0 surface."""
import json
import re
import shutil
import subprocess
import sys
import zipfile

from tests.helpers import BATest, PLUGIN
from ba_lib import cli, helptext
from ba_lib.util import BAError


class NoBytecodeTests(BATest):
    def runTest(self):
        pass

    def test_running_the_core_from_a_fresh_copy_leaves_no_pycache_behind(self):
        copy = self.tmp / "plugin-copy"
        shutil.copytree(PLUGIN, copy, ignore=shutil.ignore_patterns("__pycache__", "tests", ".git"))
        out = self.tmp / "project"
        reqs = self.reqs_copy()
        for argv in (["find", "--dir", str(reqs.parent)], ["init", "--requirements", str(reqs)], ["status"], ["help", "gap"], ["doc", "check"]):
            r = subprocess.run([sys.executable, str(copy / "scripts" / "ba.py"), "--root", str(out), *argv],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(list(copy.rglob("__pycache__")), [], "the plugin folder must stay untouched")
        self.assertTrue((out / "state" / "workflow.json").exists(), "the core works with --root outside the plugin folder")
        self.assertTrue((out / "enriched" / "membership.md").exists(), "init wrote the first enriched copies")

    def test_the_helper_scripts_leave_no_pycache_behind_either(self):
        copy = self.tmp / "plugin-copy"
        shutil.copytree(PLUGIN, copy, ignore=shutil.ignore_patterns("__pycache__", "tests", ".git"))
        out, reqs = self.tmp / "project", self.reqs_copy()
        scripts = copy / "scripts"

        def run(*argv, **kw):
            r = subprocess.run([sys.executable, *map(str, argv)], capture_output=True, text=True, encoding="utf-8", **kw)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        run(scripts / "ba.py", "--root", out, "init", "--requirements", reqs)
        run(scripts / "safe_write.py", "--root", out, "--path", "state/inbox/x.json", "--stdin", input="[]")
        run(scripts / "validate_artifacts.py", "--root", out)
        run(scripts / "check_traceability.py", "--root", out)
        run(scripts / "fingerprint_source.py", reqs / "membership.md")
        self.assertEqual(list(copy.rglob("__pycache__")), [], "no helper script may write bytecode into the plugin folder")

    def test_the_requirement_files_are_untouched_by_everything_the_scripts_do(self):
        out, reqs = self.tmp / "project", self.reqs_copy()
        before = {f.name: f.read_bytes() for f in reqs.iterdir()}
        scripts = PLUGIN / "scripts"
        for argv in (["--root", out, "init", "--requirements", reqs], ["--root", out, "validate"], ["--root", out, "trace", "--write"],
                     ["--root", out, "changes", "check"], ["--root", out, "doc", "sync"], ["--root", out, "recover"], ["--root", out, "report"]):
            r = subprocess.run([sys.executable, str(scripts / "ba.py"), *map(str, argv)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual({f.name: f.read_bytes() for f in reqs.iterdir()}, before, "the supplied requirements are read-only input")


class GeneratedHelpTests(BATest):
    def runTest(self):
        pass

    def test_every_command_has_a_one_line_purpose_and_none_is_stale(self):
        rows = dict(helptext.commands(cli.build_parser()))
        self.assertEqual(sorted(set(rows) - set(helptext.PURPOSE)), [], "add a purpose for each new command")
        self.assertEqual(sorted(set(helptext.PURPOSE) - set(rows)), [], "a purpose for a command that no longer exists")

    def test_help_lists_groups_then_one_command_with_options_taken_from_the_parser(self):
        top = helptext.render(cli.build_parser())
        for g in ("gap", "ac", "doc", "req", "changes", "source"):
            self.assertIn(g, top["groups"])
        for gone in ("story", "baseline", "convert"):
            self.assertNotIn(gone, top["groups"], "removed in 2.0")
        one = helptext.render(cli.build_parser(), "gap", "resolve")["commands"]
        self.assertEqual([c["cmd"] for c in one], ["gap resolve"])
        self.assertIn("--answer-id", one[0]["usage"])
        self.assertNotIn("--requirement-updates", one[0]["usage"], "a decision never rewrites the supplied requirement")
        self.assertIn("RESOLVED", one[0]["does"])
        group = helptext.render(cli.build_parser(), "ac")["commands"]
        self.assertGreater(len(group), 10)
        init = helptext.render(cli.build_parser(), "init")["commands"][0]["usage"]
        for flag in ("--requirements", "--recursive", "--locator", "--enriched-dir"):
            self.assertIn(flag, init)
        with self.assertRaises(BAError):
            helptext.render(cli.build_parser(), "nope")

    def test_help_is_far_smaller_than_the_static_reference_it_replaces(self):
        one = json.dumps(helptext.render(cli.build_parser(), "gap", "resolve"))
        self.assertLess(len(one), 1300)

    def test_every_command_the_guides_tell_the_model_to_run_exists(self):
        """A guide that names `ac aprove` or a removed `story next` would only fail in a live session."""
        rows = {c for c, _ in helptext.commands(cli.build_parser())}
        top = {c.split()[0] for c in rows}
        bad = []
        for f in list((PLUGIN / "skills").rglob("*.md")) + list((PLUGIN / "commands").glob("*.md")) + list((PLUGIN / "agents").glob("*.md")):
            text = f.read_text(encoding="utf-8")
            for m in re.finditer(r"`((?:gaps?|ac|doc|req|changes|source|owner|policy) [a-z][a-z-]*)", text):
                words = m.group(1).split()
                cmd = f"{words[0]} {words[1]}"
                if cmd not in rows:
                    bad.append(f"{f.relative_to(PLUGIN)}: {cmd}")
            for m in re.finditer(r"`(status|validate|trace|recover|complete|report|files|manifest|bridge|find|help|fingerprint)(?:[ `])", text):
                self.assertIn(m.group(1), top)
        self.assertEqual(sorted(set(bad)), [])


class ManifestTests(BATest):
    def runTest(self):
        pass

    def test_the_manifests_describe_version_2(self):
        plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        market = json.loads((PLUGIN.parent / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["name"], "ba-agent")
        self.assertRegex(plugin["version"], r"^2\.\d+\.\d+$")
        self.assertEqual(market["plugins"][0]["name"], "ba-agent")
        self.assertEqual(market["plugins"][0]["source"], "./ba-agent-plugin")
        for text in (plugin["description"], market["plugins"][0]["description"], market["description"]):
            self.assertNotRegex(text, r"(?i)\bBRD\b|baseline|user stor", "1.x vocabulary in a manifest")
        self.assertIn("acceptance criteria", plugin["description"].lower())

    def test_the_changelog_and_docs_cover_the_current_version(self):
        version = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
        changelog = (PLUGIN / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertRegex(changelog, rf"(?m)^## {re.escape(version)}\b")
        self.assertIn("LEGACY_PROJECT", changelog)
        for doc in ("README.md", "docs/USER_GUIDE.md", "docs/ARCHITECTURE.md"):
            text = (PLUGIN / doc).read_text(encoding="utf-8")
            self.assertIn("enriched", text, doc)
            self.assertNotIn("D:\\BA-Agents", text, f"{doc}: no machine-specific path")
        self.assertNotIn("D:\\BA-Agents", (PLUGIN.parent / "README.md").read_text(encoding="utf-8"))


class PackagingTests(BATest):
    def runTest(self):
        pass

    def test_the_upload_archive_leaves_out_housekeeping_files_and_keeps_what_runs(self):
        r = subprocess.run([sys.executable, str(PLUGIN / "scripts" / "package_plugin.py"), "--out", str(self.tmp / "dist")],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        archive = json.loads(r.stdout)["archive"]
        version = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
        self.assertTrue(archive.endswith(f"ba-agent-{version}.zip"), archive)
        with zipfile.ZipFile(archive) as z:
            names = set(z.namelist())
        for gone in ("CHANGELOG.md", "docs/TEST_REPORT.md"):
            self.assertNotIn(gone, names)
        self.assertFalse([n for n in names if n.startswith(("tests/", ".in_use/")) or "__pycache__" in n])
        for kept in (".claude-plugin/plugin.json", "scripts/ba.py", "scripts/ba_lib/docmodel.py", "scripts/ba_lib/docsync.py",
                     "scripts/ba_lib/acs.py", "skills/ba-workflow/SKILL.md", "skills/ba-workflow/references/cowork-sync.md",
                     "skills/ba-workflow/references/stages/gap-resolution.md", "skills/ba-workflow/references/stages/acceptance-criteria.md",
                     "commands/triage-ac.md", "agents/ac-writer.md", "schemas/ac.schema.json", "templates/ac.md"):
            self.assertIn(kept, names)
        for gone in ("agents/brd-analyst.md", "agents/story-builder.md", "agents/requirement-analyst.md", "commands/triage-stories.md",
                     "scripts/ba_lib/convert.py", "scripts/ba_lib/baseline.py", "scripts/ba_lib/stories.py", "schemas/story.schema.json",
                     "schemas/requirement.schema.json", "templates/user_story.md"):
            self.assertNotIn(gone, names)
