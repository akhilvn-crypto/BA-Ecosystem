"""End-to-end through the real CLI (subprocesses), exactly as the plugin's commands/skills drive it."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
BA = str(PLUGIN / "scripts" / "ba.py")
SMALL = PLUGIN / "tests" / "fixtures" / "brd_small.md"


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ba-e2e-"))
        self.root = self.tmp / "ba-output"
        self.brd = self.tmp / "brd.md"
        shutil.copyfile(SMALL, self.brd)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def ba(self, *args, expect_ok=True, args_file=None, root=True):
        cmd = [sys.executable, BA] + (["--root", str(self.root)] if root else [])
        if args_file is not None:
            f = self.tmp / f"args-{len(list(self.tmp.glob('args-*')))}.json"
            f.write_text(json.dumps(args_file, ensure_ascii=False), encoding="utf-8")
            cmd += ["--args-file", str(f)]
        r = subprocess.run(cmd + list(args), capture_output=True, text=True, encoding="utf-8")
        try:
            out = json.loads(r.stdout)
        except json.JSONDecodeError:
            out = {"raw": r.stdout}
        if expect_ok:
            self.assertEqual(r.returncode, 0, f"{args}\n{r.stdout}\n{r.stderr}")
            if isinstance(out, dict) and "ok" in out:
                self.assertTrue(out["ok"], out)
        else:
            self.assertEqual(r.returncode, 1, f"{args}\n{r.stdout}\n{r.stderr}")
            self.assertFalse(out["ok"])
        return out

    def j(self, name, data):
        f = self.tmp / name
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return str(f)

    def test_full_workflow_to_completion(self):
        brd_before = self.brd.read_bytes()
        out = self.ba("init", "--brd", str(self.brd), "--name", "task-reminders")
        self.assertEqual(out["stage"], "BRD_PARSED")
        sections = {s["title"]: s["id"] for s in out["outline"]}
        s_tasks, s_rem = sections["1. Tasks"], sections["2. Reminders"]

        reqs = [dict(title="Create task", statement="A user can create a task with a title and optional due date.", type="FUNCTIONAL",
                     source_section_ids=[s_tasks], source_excerpt="A user shall create a task with a title and an optional due date."),
                dict(title="Due-time reminder", statement="The system sends a reminder notification at the task's due time.", type="FUNCTIONAL",
                     source_section_ids=[s_rem], source_excerpt="The system must send a reminder notification at the due time of a task.")]
        self.assertEqual(self.ba("req", "add", "--file", self.j("reqs.json", reqs))["created"], ["REQ-001", "REQ-002"])
        self.assertEqual(self.ba("req", "commit")["stage"], "REQUIREMENTS_READY")

        gaps = [dict(category="Integration", title="Reminder channel unspecified", description="BRD says 'notification' without a channel.",
                     evidence="BRD §2: 'send a reminder notification'", why_it_matters="Determines integrations and cost.",
                     question="Which channels (push, email, SMS) should reminders use?", impacted_requirement_ids=["REQ-002"],
                     priority="HIGH", priority_rationale="Changes integrations and acceptance tests.")]
        self.assertEqual(self.ba("gaps", "add", "--file", self.j("gaps.json", gaps))["created"], ["GAP-001"])

        # gate: no gap may be presented before the decision owner is known
        err = self.ba("gaps", "next", expect_ok=False)
        self.assertEqual(err["error"]["code"], "OWNER_REQUIRED")
        self.ba("owner", "set", "--name", "Priya Nair", "--role", "Product Owner")
        nxt = self.ba("gaps", "next")
        self.assertEqual(nxt["gaps"][0]["gap_id"], "GAP-001")
        self.assertEqual(nxt["gaps"][0]["decision_owner"]["name"], "Priya Nair")

        # shell-hostile free text goes through --args-file untouched
        raw = 'Push + email, not SMS. "Quiet hours" 22:00–07:00 → no pushes; it\'s a $5/month cap!\nSecond line.'
        ans = self.ba("gap", "answer", "GAP-001", args_file={"response": raw, "respondent-name": "Priya Nair", "respondent-role": "Product Owner"})
        self.assertEqual(ans["answer_id"], "A1")
        # unreviewed contradiction candidates (e.g. vs requirement text) are surfaced, never auto-ignored
        res = self.ba("gap", "resolve", "GAP-001", args_file={
            "answer-id": "A1", "interpretation": "Reminders use push and email only; no SMS. Quiet hours 22:00–07:00 suppress pushes.",
            "closed-by-name": "Priya Nair", "closed-by-role": "Product Owner", "closure-summary": "Channels and quiet hours defined.",
            "confirmation": "Yes — that closes it.", "reviewed": ",".join(c["artifact_id"] for c in ans["candidate_conflicts"])})
        self.assertRegex(res["closed_at_ist"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+05:30$")
        gap = self.ba("gap", "show", "GAP-001")
        self.assertEqual(gap["answers"][0]["response"], raw, "raw response preserved byte-for-byte")
        self.assertEqual(gap["closed_by_role"], "Product Owner")

        self.ba("gaps", "commit", "--cross-pass-done")
        self.assertEqual(self.ba("baseline", "create")["status"], "PROPOSED")
        self.ba("baseline", "confirm", "--version", "1", "--by-name", "Priya Nair", "--by-role", "Product Owner", "--statement", "Approved.")

        stories = [dict(title="Create a task", user_story=dict(role="User", capability="to create a task with a title and optional due date", value="I can track work"),
                        acceptance_criteria=["Given a title, when the user saves, then the task is created.",
                                             "Given no title, when the user saves, then a validation error is shown."], requirement_ids=["REQ-001"]),
                   dict(title="Get due-time reminders", user_story=dict(role="User", capability="to be reminded at a task's due time", value="I do not miss deadlines"),
                        acceptance_criteria=["Given a task due at 10:00 and push enabled, when 10:00 arrives, then a push reminder is sent.",
                                             "Given quiet hours 22:00-07:00, when the due time falls inside them, then no push is sent."],
                        requirement_ids=["REQ-002"])]
        self.assertEqual(self.ba("story", "add", "--file", self.j("stories.json", stories))["created"], ["US-001", "US-002"])

        # complete() must refuse while stories are only candidates
        part = self.ba("complete")
        self.assertFalse(part["complete"])
        self.assertIn("PARTIAL", part["message"])

        shown = self.ba("story", "next")
        self.assertEqual(shown["stories"][0]["story_id"], "US-001")
        self.ba("story", "approve", "US-001", "--version", "1", "--statement", "Approve US-001", "--approver-name", "Priya Nair", "--approver-role", "Product Owner")
        edited = self.ba("story", "edit", "US-002", "--file", self.j("edit.json", {"title": "Receive due-time reminders"}), "--reason", "Clearer title")
        self.assertEqual(edited["new_version"], 2)
        self.ba("story", "approve", "US-002", "--version", "2", "--statement", "Approve v2 as edited")
        done = self.ba("complete")
        self.assertTrue(done["complete"], done)

        approved = sorted(p.name for p in (self.root / "stories/approved").glob("*.md"))
        self.assertEqual(approved, ["US-001.md", "US-002.md"])
        self.assertEqual(list((self.root / "stories/candidates").glob("*")), [])
        self.assertTrue((self.root / "stories/archive/US-002.v1.json").exists())
        md = (self.root / "stories/approved/US-002.md").read_text(encoding="utf-8")
        self.assertIn("As a User,", md)
        self.assertIn("**Version:** 2", md)
        self.assertTrue(self.ba("validate")["ok"])
        self.assertTrue(self.ba("trace", "--write")["ok"])
        status = subprocess.run([sys.executable, BA, "--root", str(self.root), "status"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(status.returncode, 0)
        self.assertIn("stage: COMPLETED", status.stdout)
        self.assertIn("Decision owner: Priya Nair — Product Owner", status.stdout)
        self.assertIn("2 approved", status.stdout)
        self.assertEqual(self.brd.read_bytes(), brd_before, "the original BRD is untouched")
        audit = [json.loads(l) for l in (self.root / "state/audit.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(e["at_ist"].endswith("+05:30") for e in audit))
        types = [e["event_type"] for e in audit]
        for t in ("WORKFLOW_STARTED", "DECISION_OWNER_SET", "GAP_RESOLVED", "BASELINE_APPROVED", "STORY_APPROVED", "STORY_EDITED", "WORKFLOW_COMPLETED"):
            self.assertIn(t, types)

    def test_error_shape_and_exit_codes(self):
        out = self.ba("status", expect_ok=False, root=True)
        self.assertEqual(out["error"]["code"], "NOT_A_PROJECT")
        out = self.ba("init", "--brd", str(self.tmp / "missing.md"), expect_ok=False)
        self.assertEqual(out["error"]["code"], "SOURCE_NOT_FOUND")
        pdf = self.tmp / "x.pdf"
        pdf.write_bytes(b"%PDF")
        out = self.ba("init", "--brd", str(pdf), expect_ok=False)
        self.assertEqual(out["error"]["code"], "UNSUPPORTED_FORMAT")
        r = subprocess.run([sys.executable, BA, "bogus"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_wrappers(self):
        self.ba("init", "--brd", str(self.brd))
        for script in ("validate_artifacts.py", "check_traceability.py"):
            r = subprocess.run([sys.executable, str(PLUGIN / "scripts" / script), "--root", str(self.root)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(json.loads(r.stdout)["ok"])
        r = subprocess.run([sys.executable, str(PLUGIN / "scripts" / "fingerprint_source.py"), str(self.brd)], capture_output=True, text=True)
        self.assertRegex(json.loads(r.stdout)["fingerprint"], r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
