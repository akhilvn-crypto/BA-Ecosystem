"""Cowork-facing helpers: `find` proposes the requirements folder without asking for a path; START-HERE.md tells a person where things are."""
import json
import subprocess
import sys

from tests.helpers import BATest, PLUGIN, acs, discover, gapmod, key, util
from ba_lib import adr, overview

BA = str(PLUGIN / "scripts" / "ba.py")


class FindTests(BATest):
    def test_one_requirements_folder_is_proposed_without_asking_for_a_path(self):
        base = self.tmp / "ws"
        self.write_req(base / "requirements", "a.md", "### REQ-001 One\nThe system shall do one.\n")
        self.write_req(base, "notes.txt", "scratch\n")
        out = discover.find(str(base))
        self.assertEqual(out["projects"], [])
        self.assertEqual(len(out["requirement_candidates"]), 1)
        c = out["requirement_candidates"][0]
        self.assertEqual((c["kind"], c["md_files"], c["requirements"], c["with_ids"]), ("folder", 1, 1, True))
        self.assertIn("propose it", out["next"])

    def test_the_folder_itself_is_a_candidate_when_it_holds_the_files(self):
        base = self.tmp / "reqs"
        self.write_req(base, "a.md", "### REQ-001 One\nThe system shall do one.\n")
        self.write_req(base, "b.md", "### REQ-002 Two\nThe system shall do two.\n")
        out = discover.find(str(base))
        self.assertEqual(out["requirement_candidates"][0]["path"], str(base.resolve()))
        self.assertEqual(out["requirement_candidates"][0]["requirements"], 2)

    def test_the_fixture_is_recognised_and_readme_is_not_counted(self):
        out = discover.find(str(PLUGIN / "tests" / "fixtures"))
        c = [x for x in out["requirement_candidates"] if x["path"].endswith("requirements")][0]
        self.assertEqual((c["md_files"], c["requirements"]), (3, 9))
        self.assertNotIn("README.md", c["files"])

    def test_an_existing_project_is_found_and_not_offered_as_requirements(self):
        p = self.new_project()
        out = discover.find(str(p.root.parent))
        self.assertEqual([x["project_id"] for x in out["projects"]], [p.state["project_id"]])
        self.assertIn("resume", out["next"])
        inside = discover.find(str(p.root))
        self.assertEqual(inside["requirement_candidates"], [])
        self.assertIn("already a BA project", inside["next"])
        # the enriched copies inside a project are never offered as requirements
        self.assertNotIn(str(p.enriched_root), [c["path"] for c in discover.find(str(p.root.parent))["requirement_candidates"]])

    def test_nothing_found_says_to_ask(self):
        base = self.tmp / "empty"
        base.mkdir()
        self.assertIn("ask the user where the requirements are", discover.find(str(base))["next"])

    def test_missing_folder_is_a_clear_error(self):
        with self.assertRaises(util.BAError) as cm:
            discover.find(str(self.tmp / "nope"))
        self.assertEqual(cm.exception.code, "NOT_A_FOLDER")

    def test_unsupported_format_names_markdown_and_creates_nothing(self):
        from tests.helpers import Project
        f = self.tmp / "brd.pdf"
        f.write_bytes(b"%PDF-1.4")
        with self.assertRaises(util.BAError) as cm:
            Project.create(self.tmp / "out", f)
        self.assertEqual(cm.exception.code, "UNSUPPORTED_FORMAT")
        self.assertIn("Markdown", cm.exception.message)
        self.assertIn("upstream agent", cm.exception.message)
        self.assertFalse((self.tmp / "out").exists())


class StartHereTests(BATest):
    def test_start_here_is_written_on_init(self):
        src = self.reqs_copy()
        out = self.tmp / "cli-out"
        r = subprocess.run([sys.executable, BA, "--root", str(out), "init", "--requirements", str(src)], capture_output=True, text=True, encoding="utf-8")
        self.assertTrue(json.loads(r.stdout)["ok"], r.stdout)
        text = (out / "START-HERE.md").read_text(encoding="utf-8")
        self.assertIn("Requirements found | 9 in 3 file(s)", text)
        self.assertIn("never changed by the BA Agent", text)
        self.assertIn("not named yet", text)
        self.assertIn("[enriched](enriched)", text)

    def test_counts_follow_the_files(self):
        p = self.with_gaps()
        overview.refresh(p)
        text = (p.root / "START-HERE.md").read_text(encoding="utf-8")
        self.assertIn("| Open gaps (waiting for an answer, including carried forward) | 3 |", text)
        self.assertIn("| Resolved gaps | 0 |", text)
        self.set_owner(p)
        gapmod.next_gaps(p, 10)
        self.answer_and_resolve(p, "GAP-003", "Block the borrow.", "A 6th borrow is rejected.")
        overview.refresh(p)
        text = (p.root / "START-HERE.md").read_text(encoding="utf-8")
        self.assertIn("| Open gaps (waiting for an answer, including carried forward) | 2 |", text)
        self.assertIn("| Resolved gaps | 1 |", text)
        self.assertIn("Priya Nair — Product Owner", text)

    def test_acceptance_counts(self):
        p = self.to_acs(2)
        overview.refresh(p)
        text = (p.root / "START-HERE.md").read_text(encoding="utf-8")
        self.assertIn("| Acceptance criteria waiting for review | 2 |", text)
        self.approve(p, "LIB-001")
        overview.refresh(p)
        text = (p.root / "START-HERE.md").read_text(encoding="utf-8")
        self.assertIn("| Approved acceptance criteria | 1 |", text)
        self.assertIn("| Acceptance criteria waiting for review | 1 |", text)

    def test_every_writing_command_refreshes_it_and_reading_does_not_rewrite_it(self):
        from tests.test_cowork.test_changed_files import ba
        p = self.with_gaps()
        ba(p.root, "owner", "set", "--name", "Priya Nair", "--role", "Product Owner")
        self.assertIn("Priya Nair — Product Owner", (p.root / "START-HERE.md").read_text(encoding="utf-8"))
        before = (p.root / "START-HERE.md").stat().st_mtime_ns
        ba(p.root, "gaps", "list")
        self.assertEqual((p.root / "START-HERE.md").stat().st_mtime_ns, before)


class ApproverTests(BATest):
    def test_the_named_approver_is_recorded_in_the_audit_and_the_adr_log(self):
        p = self.to_acs(1)
        acs.approve(p, key("LIB-001"), 1, "Confirmed.", "Lee Chan", "Delivery Lead")
        ev = [e for e in p.audit_events() if e["event_type"] == "AC_APPROVED"][-1]
        self.assertEqual((ev["approver_name"], ev["approver_role"], ev["statement"]), ("Lee Chan", "Delivery Lead", "Confirmed."))
        adr.refresh(p)  # the CLI does this after every writing command
        log = (p.root / "acceptance" / "approved" / "ADR-LOG.md").read_text(encoding="utf-8")
        self.assertIn("Lee Chan — Delivery Lead", log)
        self.assertIn("Confirmed.", log)
