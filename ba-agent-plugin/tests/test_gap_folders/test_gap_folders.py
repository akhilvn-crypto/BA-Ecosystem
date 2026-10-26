"""Gaps are filed by status (open / deferred / resolved) and move on their own; closing a gap is one step."""
import json
import subprocess
import sys

from tests.helpers import BATest, PLUGIN, checks, gapmod
from ba_lib.project import Project
from ba_lib.util import BAError

BA = str(PLUGIN / "scripts" / "ba.py")
OWNER = ("Priya Nair", "Product Owner")


def names(d):
    return sorted(x.name for x in d.iterdir() if x.is_file()) if d.is_dir() else []


class GapFolderTests(BATest):
    def test_new_gaps_are_saved_to_open_the_moment_they_are_added(self):
        p = self.with_gaps()
        r = p.root
        self.assertEqual(names(r / "gaps/open/md"), ["GAP-001.md", "GAP-002.md", "GAP-003.md"])
        self.assertEqual(names(r / "gaps/open/json"), ["GAP-001.json", "GAP-002.json", "GAP-003.json"])
        self.assertEqual(names(r / "gaps/resolved/md"), [])
        self.assertTrue((r / "state/gap-register.json").is_file())
        self.assertFalse((r / "gaps/md").exists() or (r / "gaps/json").exists())
        text = (r / "gaps/open/md/GAP-001.md").read_text(encoding="utf-8")
        self.assertIn("**Impacted requirements:** LIB-006", text, "the readable copy names the requirement by its ID")
        self.assertIn("> The fine rate is to be decided.", text, "and shows the quoted evidence")

    def test_an_answered_gap_moves_to_resolved_and_leaves_open(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        gapmod.next_gaps(p, 10)
        self.answer_and_resolve(p, "GAP-003", "Block the borrow.", "A 6th borrow is rejected.")
        r = p.root
        self.assertEqual(names(r / "gaps/resolved/md"), ["GAP-003.md"])
        self.assertEqual(names(r / "gaps/resolved/json"), ["GAP-003.json"])
        self.assertEqual(names(r / "gaps/open/md"), ["GAP-001.md", "GAP-002.md"])
        self.assertEqual(names(r / "gaps/open/json"), ["GAP-001.json", "GAP-002.json"])
        self.assertIn("RESOLVED", (r / "gaps/resolved/md/GAP-003.md").read_text(encoding="utf-8"))
        self.assertEqual(json.loads((r / "gaps/resolved/json/GAP-003.json").read_text(encoding="utf-8"))["status"], "RESOLVED")

    def test_deferred_gap_is_filed_as_deferred_and_returns_to_open_when_revisited(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        gapmod.next_gaps(p, 10)
        gapmod.defer(p, "GAP-001", "Waiting for finance", None, None, "Sam Dsouza", "Finance Lead")
        self.assertEqual(names(p.root / "gaps/deferred/md"), ["GAP-001.md"])
        self.assertNotIn("GAP-001.md", names(p.root / "gaps/open/md"))
        gapmod.revisit(p, "GAP-001")
        self.assertEqual(names(p.root / "gaps/deferred/md"), [])
        self.assertIn("GAP-001.md", names(p.root / "gaps/open/md"))

    def test_carried_forward_gap_stays_in_open(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        gapmod.next_gaps(p, 10)
        gapmod.carry_forward(p, "GAP-002", *OWNER, "later")
        self.assertIn("GAP-002.md", names(p.root / "gaps/open/md"))

    def test_validate_flags_a_missing_copy_and_recover_rebuilds_it(self):
        p = self.with_gaps()
        (p.root / "gaps/open/md/GAP-002.md").unlink()
        self.assertTrue(any("GAP-002" in w for w in checks.validate_project(p)["warnings"]))
        out = checks.recover(p)
        self.assertEqual(out["gap_files_rebuilt"], 3)
        self.assertTrue((p.root / "gaps/open/md/GAP-002.md").exists())

    def test_cli_reports_where_the_gap_lives_now(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        gapmod.next_gaps(p, 10)
        r = subprocess.run([sys.executable, BA, "--root", str(p.root), "gap", "answer", "GAP-003", "--response", "Block the borrow."],
                           capture_output=True, text=True, encoding="utf-8")
        out = json.loads(r.stdout)
        self.assertEqual(out["gap_file"]["folder"], "gaps/open")
        r = subprocess.run([sys.executable, BA, "--root", str(p.root), "gap", "resolve", "GAP-003", "--answer-id", "A1",
                            "--interpretation", "A 6th borrow is rejected."], capture_output=True, text=True, encoding="utf-8")
        out = json.loads(r.stdout)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["gap_file"]["folder"], "gaps/resolved")
        self.assertTrue(out["gap_file"]["file"].endswith("GAP-003.md"))
        self.assertEqual(out["closed_by"], {"name": OWNER[0], "role": OWNER[1]})
        self.assertIn("gaps/resolved", out["files_saved"])
        self.assertIn("enriched", out["files_saved"], "the enriched copies follow the gap state")


class EnrichedFollowsGapFolderTests(BATest):
    def test_a_gap_moving_between_folders_moves_in_the_enriched_open_gap_table(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        t = self.enriched(p, "lending.md")
        self.assertIn("| GAP-001 | HIGH | OPEN |", t)
        gapmod.next_gaps(p, 10)
        gapmod.defer(p, "GAP-001", "Waiting for finance", None, None, "Sam Dsouza", "Finance Lead")
        self.assertIn("| GAP-001 | HIGH | DEFERRED |", self.enriched(p, "lending.md"))
        gapmod.revisit(p, "GAP-001")
        self.answer_and_resolve(p, "GAP-001", "R10 per day.", "R10 per day.")
        self.assertNotIn("GAP-001", self.enriched(p, "lending.md"))
        self.assertEqual(names(p.root / "gaps/resolved/md"), ["GAP-001.md"])


class OneStepCloseTests(BATest):
    def _answered(self, respondent=None):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        gapmod.next_gaps(p, 10)
        a = gapmod.record_answer(p, "GAP-003", "Block the borrow.", *(respondent or (None, None)))
        return p, a["answer_id"]

    def test_the_owners_checked_answer_closes_the_gap_without_naming_a_closer(self):
        p, aid = self._answered()
        out = gapmod.resolve(p, "GAP-003", aid, "A 6th borrow is rejected.")
        self.assertEqual(out["status"], "RESOLVED")
        self.assertEqual(out["closed_by"], {"name": OWNER[0], "role": OWNER[1]})
        ev = [e for e in p.audit_events() if e["event_type"] == "GAP_RESOLVED"][-1]
        self.assertEqual(ev["closer_basis"], "DECISION_OWNER_ANSWER")
        self.assertEqual(ev["closer_confirmation"], "Block the borrow.")
        self.assertEqual(p.decisions()["DEC-001"]["confirmed_by_name"], OWNER[0])

    def test_an_answer_from_someone_else_still_needs_the_owner_to_confirm(self):
        p, aid = self._answered(respondent=("Sam Dsouza", "Analyst"))
        with self.assertRaises(BAError) as cm:
            gapmod.resolve(p, "GAP-003", aid, "A 6th borrow is rejected.")
        self.assertEqual(cm.exception.code, "IDENTITY_REQUIRED")
        self.assertEqual(p.gap("GAP-003")["status"], "WAITING_FOR_ANSWER")

    def test_a_named_closer_is_still_honoured(self):
        p, aid = self._answered()
        out = gapmod.resolve(p, "GAP-003", aid, "A 6th borrow is rejected.", "Lee Chan", "Delivery Lead", "ok", "Agreed")
        self.assertEqual(out["closed_by"], {"name": "Lee Chan", "role": "Delivery Lead"})
