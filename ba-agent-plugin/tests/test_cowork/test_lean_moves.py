"""R4: a command that moves a record must leave the user's folder as a full run would, even from a lean staged copy.

The lean copy has no `md/` views (except approved stories), so the old view of a moved gap or story is not in the workspace to be
noticed as removed. The user's side is simulated by applying `changed_files` / `removed_files` to a full copy of the project."""
import shutil

from tests.helpers import BATest, gapmod
from tests.test_cowork.test_changed_files import OWNER, ba
from ba_lib import adr, overview, sync


def stage(src, dst):
    for rel in sync.stage_list(src)["stage"]:
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, dst / rel)


class LeanMoveTests(BATest):
    def runTest(self):
        pass

    def assert_user_side_matches_a_full_run(self, p, *argv):
        user, lean = self.tmp / "user", self.tmp / "workspace"
        adr.refresh(p); overview.refresh(p)  # in a real session the earlier stages have already left their generated views on the user's side
        shutil.copytree(p.root, user)
        stage(p.root, lean)
        out = ba(lean, *argv)
        self.assertTrue(out["ok"], out)
        ref = ba(p.root, *argv)  # the same command on the full project
        self.assertTrue(ref["ok"], ref)
        for rel in out.get("removed_files", []):
            (user / rel).unlink(missing_ok=True)
        for rel in out["changed_files"]:
            (user / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(lean / rel, user / rel)
        theirs, full = set(sync.sig(user)), set(sync.sig(p.root))
        self.assertEqual(sorted(theirs - full), [], "stale files left on the user's side")
        self.assertEqual(sorted(full - theirs), [], "files a full run writes never reached the user")

    def test_story_approve_from_a_lean_copy(self):
        p = self.to_stories()
        ba(p.root, "story", "next", "--id", "US-001")
        self.assert_user_side_matches_a_full_run(p, "story", "approve", "US-001", "--version", "1", "--statement", "Approved, it reads correctly.")

    def test_story_defer_from_a_lean_copy(self):
        p = self.to_stories()
        ba(p.root, "story", "next", "--id", "US-001")
        self.assert_user_side_matches_a_full_run(p, "story", "defer", "US-001", "--version", "1", "--reason", "Waiting for the vendor")

    def test_gap_defer_from_a_lean_copy(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        gapmod.next_gaps(p, 1, gap_id="GAP-003")
        self.assert_user_side_matches_a_full_run(p, "gap", "defer", "GAP-003", "--reason", "Waiting for the performance team",
                                                 "--by-name", OWNER[0], "--by-role", OWNER[1])
