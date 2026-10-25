"""ADR-LOG.md: who approved / decided what, and when (IST), at the top of every artifact folder."""
import json
import subprocess
import sys

from tests.helpers import BATest, PLUGIN, adr_refresh, stories
from ba_lib.adr import log_folders

BA = str(PLUGIN / "scripts" / "ba.py")


class AdrLogTests(BATest):
    def test_every_artifact_folder_and_the_root_get_a_log_at_the_top(self):
        p = self.new_project()
        adr_refresh(p)
        for d in ("",) + log_folders():
            f = (p.root / d / "ADR-LOG.md")
            self.assertTrue(f.exists(), d)
            self.assertIn("No approvals or decisions recorded here yet", f.read_text(encoding="utf-8"))

    def test_story_and_baseline_approvals_show_who_when_in_ist(self):
        p = self.to_stories()
        stories.approve(p, "US-001", 1, "Approved as written.", "Priya Nair", "Product Owner")
        adr_refresh(p)
        approved = (p.root / "stories/approved/ADR-LOG.md").read_text(encoding="utf-8")
        self.assertIn("Story approved", approved)
        self.assertIn("US-001 v1", approved)
        self.assertIn("Priya Nair — Product Owner", approved)
        self.assertIn("Approved as written.", approved)
        self.assertIn("22 Oct 2026", approved)
        self.assertIn("IST", approved)
        self.assertNotIn("US-002", approved)
        base = (p.root / "baselines/ADR-LOG.md").read_text(encoding="utf-8")
        self.assertIn("Baseline approved", base)
        self.assertIn("I approve baseline v1.", base)
        self.assertNotIn("Story approved", base)
        gaps = (p.root / "gaps/ADR-LOG.md").read_text(encoding="utf-8")
        self.assertIn("Gap closed, decision recorded", gaps)
        root = (p.root / "ADR-LOG.md").read_text(encoding="utf-8")
        for needle in ("Story approved", "Baseline approved", "Gap closed, decision recorded"):
            self.assertIn(needle, root)

    def test_log_does_not_get_moved_into_md_by_the_layout_migration(self):
        p = self.new_project()
        adr_refresh(p)
        type(p).open(p.root)
        self.assertTrue((p.root / "gaps" / "ADR-LOG.md").exists())
        self.assertFalse((p.root / "gaps" / "open" / "md" / "ADR-LOG.md").exists())

    def test_cli_writes_the_log_on_init(self):
        brd = self.brd_copy()
        out = self.tmp / "cli-out"
        r = subprocess.run([sys.executable, BA, "--root", str(out), "init", "--brd", str(brd)], capture_output=True, text=True)
        self.assertTrue(json.loads(r.stdout)["ok"], r.stdout)
        self.assertTrue((out / "ADR-LOG.md").exists())
        self.assertTrue((out / "stories" / "approved" / "ADR-LOG.md").exists())
