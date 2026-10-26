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
FIXTURE = PLUGIN / "tests" / "fixtures" / "requirements"
OWNER = ("Priya Nair", "Product Owner")


class CliBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ba-e2e-"))
        self.root = self.tmp / "ba-output"
        self.reqs = self.tmp / "requirements"
        shutil.copytree(FIXTURE, self.reqs)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def ba(self, *args, expect_ok=True, args_file=None, root=True, code=1):
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
            self.assertEqual(r.returncode, code, f"{args}\n{r.stdout}\n{r.stderr}")
            self.assertFalse(out["ok"])
        return out

    def j(self, name, data):
        f = self.tmp / name
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return str(f)

    def status_text(self):
        r = subprocess.run([sys.executable, BA, "--root", str(self.root), "status"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def source_hashes(self):
        return {f.name: f.read_bytes() for f in self.reqs.glob("*.md")}


class CliTests(CliBase):
    def test_full_workflow_to_completion(self):
        before = self.source_hashes()
        out = self.ba("init", "--requirements", str(self.reqs), "--name", "library")
        self.assertEqual(out["stage"], "SOURCE_INDEXED")
        self.assertEqual((out["source"]["files"], out["source"]["requirements"]), (3, 9))
        self.assertEqual(out["skipped"][0]["file"], "README.md")
        self.assertEqual(out["instruction_like_lines"], 1)
        self.assertFalse(out["delegate"])
        self.assertEqual(len(out["batches"][0]), 9)
        self.assertTrue((self.root / "enriched" / "lending.md").exists())

        listed = self.ba("req", "list")["requirements"]
        self.assertEqual([r["label"] for r in listed], [f"LIB-00{n}" for n in (5, 6, 7, 1, 2, 3, 4, 8, 9)])
        self.assertEqual({r["status"] for r in listed}, {"READY"})
        shown = self.ba("req", "show", "LIB-006")
        self.assertEqual(shown["req_key"], "lending.md::LIB-006")
        self.assertIn("fine rate is to be decided", shown["text"])
        self.assertEqual(self.ba("req", "show", "LIB-404", expect_ok=False)["error"]["code"], "UNKNOWN_REQUIREMENT")
        self.assertEqual(self.ba("req", "pairs").get("possible_conflicts", []), [])

        gaps = [dict(category="Business rules", title="Fine rate undefined", description="The rate is missing.",
                     evidence="LIB-006 says the rate is to be decided", evidence_quote="The fine rate is to be decided.",
                     why_it_matters="Cannot compute the fine.", question="What is the fine per day late, and the maximum?",
                     impacted_req_keys=["LIB-006"], priority="HIGH", priority_rationale="Changes calculation and tests.", material=True)]
        self.assertEqual(self.ba("gaps", "add", "--file", self.j("gaps.json", gaps))["created"], ["GAP-001"])
        self.assertIn("| GAP-001 | HIGH | OPEN |", (self.root / "enriched" / "lending.md").read_text(encoding="utf-8"))
        self.assertEqual(self.ba("req", "list", "--status", "BLOCKED")["requirements"][0]["label"], "LIB-006")

        # gate: no gap may be presented before the decision owner is known
        self.assertEqual(self.ba("gaps", "next", expect_ok=False)["error"]["code"], "OWNER_REQUIRED")
        self.ba("owner", "set", "--name", OWNER[0], "--role", OWNER[1])
        nxt = self.ba("gaps", "next")
        self.assertEqual(nxt["gaps"][0]["gap_id"], "GAP-001")
        self.assertIn("LIB-006", nxt["gaps"][0]["present"]["dialog"]["question"])
        self.assertEqual(self.ba("owner", "show")["decision_owner"]["name"], "Priya Nair")

        # shell-hostile free text goes through --args-file untouched
        raw = 'R10 per day; "cap" R200 → max.\nSecond line. It\'s $5!'
        ans = self.ba("gap", "answer", "GAP-001", args_file={"response": raw, "respondent-name": OWNER[0], "respondent-role": OWNER[1]})
        self.assertEqual(ans["answer_id"], "A1")
        res = self.ba("gap", "resolve", "GAP-001", args_file={
            "answer-id": "A1", "interpretation": "The fine is R10 per day late, capped at R200 per loan.",
            "closed-by-name": OWNER[0], "closed-by-role": OWNER[1], "closure-summary": "Rate and cap defined.",
            "confirmation": "Yes — that closes it.", "reviewed": ",".join(c["artifact_id"] for c in ans["candidate_conflicts"])})
        self.assertRegex(res["closed_at_ist"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+05:30$")
        self.assertEqual(self.ba("gap", "show", "GAP-001")["answers"][0]["response"], raw, "raw response preserved byte-for-byte")
        self.assertNotIn("| GAP-001 |", (self.root / "enriched" / "lending.md").read_text(encoding="utf-8"))

        self.ba("gaps", "commit", "--cross-pass-done")
        gctx = self.ba("ac", "gaps", "--requirements", "LIB-006,LIB-001")
        self.assertEqual([r["status"] for r in gctx["requirements"]], ["READY", "READY"])
        acs = [dict(req_key=k, acceptance_criteria=[f"Given a member case for {k}, when it is processed, then the documented outcome occurs.",
                                                     f"Given an invalid case for {k}, when it is processed, then an error is shown."])
               for k in ("LIB-006", "LIB-001", "LIB-002")]
        self.assertEqual(self.ba("ac", "add", "--file", self.j("acs.json", acs))["created"],
                         ["lending.md::LIB-006", "membership.md::LIB-001", "membership.md::LIB-002"])
        part = self.ba("complete")
        self.assertFalse(part["complete"])
        self.assertIn("PARTIAL", part["message"])

        shown = self.ba("ac", "next")
        self.assertEqual(shown["acs"][0]["req_key"], "lending.md::LIB-006")
        self.assertEqual(shown["acs"][0]["present"]["dialog"]["options"][0]["label"], "Approve v1")
        self.assertEqual(self.ba("ac", "approve", "LIB-006", "--version", "1", expect_ok=False)["error"]["code"], "APPROVAL_EVIDENCE_REQUIRED")
        ap = self.ba("ac", "approve", "LIB-006", "--version", "1", "--statement", "Approve it", "--approver-name", OWNER[0], "--approver-role", OWNER[1])
        self.assertTrue(ap["enriched_copy"].endswith("lending.md"))
        enriched = (self.root / "enriched" / "lending.md").read_text(encoding="utf-8")
        self.assertIn('ac="v1"', enriched)
        self.assertIn("*Decisions applied: DEC-001*", enriched)
        docs = self.ba("doc", "check")
        self.assertTrue(docs["in_sync"], docs)
        edited = self.ba("ac", "edit", "LIB-001", "--file", self.j("edit.json", {"acceptance_criteria": [
            "Given a visitor with a valid email, when they register, then a member is created and a confirmation email is queued."]}), "--reason", "Clearer")
        self.assertEqual(edited["new_version"], 2)
        self.ba("ac", "approve", "LIB-001", "--version", "2", "--statement", "Approve v2 as edited")
        self.ba("ac", "approve", "LIB-002", "--version", "1", "--statement", "Approve")
        rest = [dict(req_key=k, acceptance_criteria=[f"Given a case for {k}, when it is processed, then the documented outcome occurs."]) for k in
                ("LIB-003", "LIB-004", "LIB-005", "LIB-007", "LIB-008", "LIB-009")]
        self.ba("ac", "add", "--file", self.j("rest.json", rest))
        for k in ("LIB-003", "LIB-004", "LIB-005", "LIB-007", "LIB-008", "LIB-009"):
            self.ba("ac", "approve", k, "--version", "1", "--statement", "Approve")
        done = self.ba("complete")
        self.assertTrue(done["complete"], done)

        self.assertEqual(sorted(p.name for p in (self.root / "acceptance/approved/md").glob("*.md")).__len__(), 9)
        self.assertEqual(list((self.root / "acceptance/candidates/md").glob("*")) + list((self.root / "acceptance/candidates/json").glob("*")), [])
        self.assertTrue(list((self.root / "acceptance/archive/json").glob("*LIB-001.v1.json")))
        self.assertIn("confirmation email is queued", (self.root / "enriched" / "membership.md").read_text(encoding="utf-8"))
        self.assertEqual((self.root / "enriched" / "reporting.md").read_text(encoding="utf-8").count("ba:begin"), 2)
        self.assertTrue(self.ba("validate")["ok"])
        self.assertTrue(self.ba("trace", "--write")["ok"])
        text = self.status_text()
        self.assertIn("stage: COMPLETED", text)
        self.assertIn("Decision owner: Priya Nair — Product Owner", text)
        self.assertIn("9 approved", text)
        self.assertEqual(before, self.source_hashes(), "the supplied requirements are untouched")
        audit = [json.loads(l) for l in (self.root / "state/audit.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(e["at_ist"].endswith("+05:30") for e in audit))
        types = {e["event_type"] for e in audit}
        for t in ("WORKFLOW_STARTED", "DECISION_OWNER_SET", "GAP_RESOLVED", "AC_APPROVED", "AC_EDITED", "WORKFLOW_COMPLETED", "ENRICHED_RENDERED"):
            self.assertIn(t, types)
        self.assertTrue((self.root / "START-HERE.md").exists())

    def test_error_shape_and_exit_codes(self):
        self.assertEqual(self.ba("status", expect_ok=False)["error"]["code"], "NOT_A_PROJECT")
        self.assertEqual(self.ba("init", "--requirements", str(self.tmp / "missing"), expect_ok=False)["error"]["code"], "SOURCE_NOT_FOUND")
        pdf = self.tmp / "x.pdf"
        pdf.write_bytes(b"%PDF")
        self.assertEqual(self.ba("init", "--requirements", str(pdf), expect_ok=False)["error"]["code"], "UNSUPPORTED_FORMAT")
        self.assertEqual(self.ba("init", "--requirements", str(self.reqs), "--locator", "([", expect_ok=False)["error"]["code"], "BAD_LOCATOR")
        self.assertEqual(self.ba("init", "--requirements", str(self.reqs), root=False, expect_ok=False)["error"]["code"], "NO_PROJECT")
        inside = self.ba("--root", str(self.reqs / "out"), "init", "--requirements", str(self.reqs), root=False, expect_ok=False)
        self.assertEqual(inside["error"]["code"], "OUTPUT_INSIDE_SOURCE")
        r = subprocess.run([sys.executable, BA, "bogus"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        r = subprocess.run([sys.executable, BA, "init", "--brd", "x.md"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, "the old --brd flag is gone")

    def test_wrappers_and_single_file(self):
        self.ba("init", "--requirements", str(self.reqs / "lending.md"))
        self.assertEqual(self.ba("req", "list")["requirements"][0]["req_key"], "lending.md::LIB-005")
        for script in ("validate_artifacts.py", "check_traceability.py"):
            r = subprocess.run([sys.executable, str(PLUGIN / "scripts" / script), "--root", str(self.root)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(json.loads(r.stdout)["ok"])
        r = subprocess.run([sys.executable, str(PLUGIN / "scripts" / "fingerprint_source.py"), str(self.reqs / "lending.md")], capture_output=True, text=True)
        self.assertRegex(json.loads(r.stdout)["fingerprint"], r"^sha256:[0-9a-f]{64}$")

    def test_init_options(self):
        out = self.ba("init", "--requirements", str(self.reqs), "--copy-source", "--enriched-dir", str(self.tmp / "docs"))
        self.assertEqual(out["enriched_folder"], str((self.tmp / "docs").resolve()))
        self.assertTrue((self.tmp / "docs" / "lending.md").exists())
        self.assertTrue((self.root / "source" / "requirements" / "lending.md").exists())
        f = self.ba("files")
        self.assertEqual(f["enriched_folder"], str((self.tmp / "docs").resolve()))
        sub = self.tmp / "other"
        shutil.copytree(self.reqs, sub / "deep")
        (sub / "top.md").write_text("### ZZ-1 Top\nThe system shall be top.\n", encoding="utf-8")
        r = self.ba("--root", str(self.tmp / "out2"), "init", "--requirements", str(sub), "--recursive", root=False)
        self.assertEqual(r["source"]["requirements"], 10)
        r = self.ba("--root", str(self.tmp / "out3"), "init", "--requirements", str(self.reqs / "membership.md"), "--locator", r"\bLIB-00[1-3]\b", root=False)
        self.assertEqual(r["source"]["requirements"], 3)

    def test_find_and_help(self):
        out = self.ba("find", "--dir", str(self.tmp), root=False)
        self.assertEqual(out["requirement_candidates"][0]["path"], str(self.reqs.resolve()))
        self.assertEqual(out["requirement_candidates"][0]["requirements"], 9)
        h = self.ba("help", root=False)
        for g in ("init", "req", "gaps", "gap", "ac", "doc", "changes", "source"):
            self.assertIn(g, h["groups"])
        self.assertNotIn("story", h["groups"])
        self.assertNotIn("baseline", h["groups"])
        self.assertNotIn("convert", h["groups"])
        self.assertTrue(self.ba("help", "ac", root=False)["commands"])
        self.assertEqual(self.ba("help", "story", root=False, expect_ok=False)["error"]["code"], "USAGE")

    def test_source_change_is_blocked_until_shown_and_applied(self):
        self.ba("init", "--requirements", str(self.reqs))
        self.ba("gaps", "commit", "--cross-pass-done")
        f = self.reqs / "membership.md"
        f.write_bytes(f.read_bytes().replace(b"at most 5 books", b"at most 3 books"))
        err = self.ba("ac", "add", "--file", self.j("a.json", [dict(req_key="LIB-001", acceptance_criteria=["Given a, when b, then c happens."])]), expect_ok=False)
        self.assertEqual(err["error"]["code"], "CHANGES_UNREVIEWED")
        self.assertEqual(err["error"]["details"]["changes"][0]["impact"]["affected_requirements"][0]["req_key"], "membership.md::LIB-003")
        # `source check` and `changes check` only explain
        chk = self.ba("source", "check")
        self.assertEqual(chk["unreviewed_ids"], ["CHG-001"])
        self.assertEqual(self.ba("changes", "show", "CHG-001")["status"], "DETECTED")
        text = self.status_text()
        self.assertIn("AWAITING DECISION CHG-001", text)
        self.assertIn("await the user's decision", text)
        app = self.ba("changes", "apply", "--id", "CHG-001", "--by-name", OWNER[0], "--by-role", OWNER[1], "--statement", "I saw the impact.")
        self.assertEqual(app["effects"]["requirements_changed"], ["membership.md::LIB-003"])
        self.assertIn("at most 3 books", (self.root / "enriched" / "membership.md").read_text(encoding="utf-8"))
        self.assertEqual(self.ba("changes", "list")["changes"][0]["status"], "APPLIED")
        self.assertEqual(self.ba("changes", "status").get("unreviewed", []), [])

    def test_hand_edit_of_an_approved_criterion_is_detected_through_the_cli(self):
        self.ba("init", "--requirements", str(self.reqs))
        self.ba("gaps", "commit", "--cross-pass-done")
        self.ba("ac", "add", "--file", self.j("a.json", [dict(req_key="LIB-001", acceptance_criteria=["Given a visitor, when they register, then a member exists."])]))
        self.ba("ac", "approve", "LIB-001", "--version", "1", "--statement", "ok")
        f = self.root / "enriched" / "membership.md"
        f.write_text(f.read_text(encoding="utf-8").replace("then a member exists.", "then a member exists and is greeted."), encoding="utf-8")
        out = self.ba("changes", "check")
        self.assertEqual(out["changes"][0]["subject"]["edited_via"], "enriched")
        drift = self.ba("doc", "check")
        self.assertEqual({x["file"]: x["state"] for x in drift["files"]}["membership.md"], "DRIFT")
        self.assertFalse(drift["in_sync"])
        self.assertEqual(self.ba("doc", "sync", expect_ok=False)["error"]["code"], "ENRICHED_DRIFT")
        self.ba("changes", "apply", "--id", out["changes"][0]["change_id"], "--by-name", OWNER[0], "--by-role", OWNER[1], "--statement", "Keep it as a proposal.")
        self.assertNotIn("greeted", f.read_text(encoding="utf-8"))
        hist = self.ba("ac", "history", "LIB-001")
        self.assertEqual([v["in_force"] for v in hist["approved_versions"]], [True])  # the approved v1 stays in force
        nxt = self.ba("ac", "next", "--key", "LIB-001")
        self.assertEqual(nxt["acs"][0]["version"], 2)

    def test_legacy_project_is_refused_but_status_is_readable(self):
        root = self.tmp / "v1"
        (root / "state").mkdir(parents=True)
        (root / "state" / "workflow.json").write_text(json.dumps({"schema_version": "1.0", "project_id": "old-brd", "stage": "STORIES_READY",
                                                                  "baseline_version": 1}), encoding="utf-8")
        out = self.ba("--root", str(root), "gaps", "list", root=False, expect_ok=False)
        self.assertEqual(out["error"]["code"], "LEGACY_PROJECT")
        self.assertIn("1.10.2", out["error"]["message"])
        self.assertEqual(out["error"]["details"]["project_id"], "old-brd")
        st = self.ba("--root", str(root), "status", root=False)
        self.assertTrue(st["legacy_project"])
        self.assertEqual((st["stage"], st["project_id"]), ("STORIES_READY", "old-brd"))
        self.assertEqual(self.ba("--root", str(root), "ac", "next", root=False, expect_ok=False)["error"]["code"], "LEGACY_PROJECT")
        # nothing in the legacy project was touched
        self.assertEqual(sorted(p.name for p in (root / "state").iterdir()), ["workflow.json"])


if __name__ == "__main__":
    unittest.main()
