"""ADR-LOG.md: who approved / decided what, and when (IST), at the top of every artifact folder."""
import json
import subprocess
import sys

from tests.helpers import BATest, FIXTURE, PLUGIN, acs, adr_refresh, gapmod, key
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
        self.assertNotIn("index", log_folders(), "the requirement index records no decisions")
        self.assertIn("acceptance/approved", log_folders())

    def test_ac_approvals_show_who_when_in_ist(self):
        p = self.to_acs(2)
        acs.approve(p, "LIB-001", 1, "Approved as written.", "Priya Nair", "Product Owner")
        adr_refresh(p)
        approved = (p.root / "acceptance/approved/ADR-LOG.md").read_text(encoding="utf-8")
        self.assertIn("Acceptance criteria approved", approved)
        self.assertIn(f"{key('LIB-001')} v1", approved)
        self.assertIn("Priya Nair — Product Owner", approved)
        self.assertIn("Approved as written.", approved)
        self.assertIn("22 Oct 2026", approved)
        self.assertIn("IST", approved)
        self.assertNotIn(key("LIB-002"), approved)
        gaps = (p.root / "gaps/ADR-LOG.md").read_text(encoding="utf-8")
        self.assertIn("Gap closed, decision recorded", gaps)
        self.assertNotIn("Acceptance criteria approved", gaps)
        root = (p.root / "ADR-LOG.md").read_text(encoding="utf-8")
        for needle in ("Acceptance criteria approved", "Gap closed, decision recorded"):
            self.assertIn(needle, root)

    def test_not_approved_and_deferred_records_are_logged_in_their_own_folders(self):
        p = self.to_acs(2)
        acs.next_ac(p, "LIB-001")
        acs.request_changes(p, "LIB-001", 1, "criteria too vague")
        acs.defer(p, "LIB-002", 1, "waiting for the policy team")
        adr_refresh(p)
        self.assertIn("Acceptance criteria not approved", (p.root / "acceptance/unapproved/ADR-LOG.md").read_text(encoding="utf-8"))
        self.assertIn("Acceptance criteria deferred", (p.root / "acceptance/deferred/ADR-LOG.md").read_text(encoding="utf-8"))
        self.assertNotIn("not approved", (p.root / "acceptance/approved/ADR-LOG.md").read_text(encoding="utf-8"))

    def test_an_upstream_change_requirement_is_logged_with_the_requirement_it_concerns(self):
        p = self.to_ac_stage()
        gapmod.add_gaps(p, [dict(self.gap_items()[2], title="Limit review", question="Should the borrow limit stay at 5?", priority="CRITICAL",
                                 priority_rationale="Alters a core business rule.", impacted_req_keys=["LIB-003"])])
        gapmod.next_gaps(p, 1)
        a = gapmod.record_answer(p, "GAP-004", "No, raise it to 8 books.")
        gapmod.resolve(p, "GAP-004", a["answer_id"], "Limit is 8 books.", "Priya Nair", "Product Owner", "Owner authorised a limit change.", "Yes.",
                       conflict_status="CHANGE_CONFIRMED", conflicting_ids=["LIB-003"], change_by_name="Priya Nair", change_by_role="Product Owner",
                       change_statement="Yes, change the limit to 8.")
        adr_refresh(p)
        log = (p.root / "gaps/ADR-LOG.md").read_text(encoding="utf-8")
        self.assertIn("Decision differs from the requirement text (upstream change needed)", log)
        self.assertIn(f"GAP-004 → {key('LIB-003')}", log)

    def test_a_change_applied_by_a_person_is_logged_in_the_changes_folder(self):
        from ba_lib import changes
        p = self.to_ac_stage()
        f = self.src / "reporting.md"
        f.write_text(f.read_text(encoding="utf-8").replace("overdue books", "overdue and lost books"), encoding="utf-8")
        cid = changes.check(p)["unreviewed_ids"][0]
        changes.apply(p, cid, "Priya Nair", "Product Owner", "I have seen the impact; apply it.")
        adr_refresh(p)
        log = (p.root / "changes/ADR-LOG.md").read_text(encoding="utf-8")
        self.assertIn("Change applied", log)
        self.assertIn(cid, log)
        self.assertIn("I have seen the impact; apply it.", log)

    def test_log_does_not_get_moved_into_md_by_opening_the_project(self):
        p = self.new_project()
        adr_refresh(p)
        type(p).open(p.root)
        self.assertTrue((p.root / "gaps" / "ADR-LOG.md").exists())
        self.assertFalse((p.root / "gaps" / "open" / "md" / "ADR-LOG.md").exists())

    def test_cli_writes_the_log_on_init(self):
        out = self.tmp / "cli-out"
        r = subprocess.run([sys.executable, BA, "--root", str(out), "init", "--requirements", str(FIXTURE)], capture_output=True, text=True,
                           encoding="utf-8")
        self.assertTrue(json.loads(r.stdout)["ok"], r.stdout)
        self.assertTrue((out / "ADR-LOG.md").exists())
        self.assertTrue((out / "acceptance" / "approved" / "ADR-LOG.md").exists())
        self.assertFalse((out / "index" / "ADR-LOG.md").exists())
