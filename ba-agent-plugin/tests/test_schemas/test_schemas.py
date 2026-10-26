"""Unit tests: schema validator, record schemas, IDs and keys, IST time, fingerprints."""
import copy
import json
import unittest

from tests.helpers import BATest, FIXTURE, acs, changemod, gapmod, key, projmod, reqindex, util
from ba_lib import schema


class ValidatorTests(unittest.TestCase):
    def test_types_required_enum_pattern(self):
        s = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string", "pattern": "^X-\\d+$"},
             "b": {"enum": [1, 2]}, "c": {"type": ["string", "null"]}}}
        self.assertEqual(schema._check.__name__, "_check")
        errs = []
        schema._check({"a": "X-1", "b": 1, "c": None}, s, s, "$", errs)
        self.assertEqual(errs, [])
        errs = []
        schema._check({"b": 3, "a": "bad"}, s, s, "$", errs)
        self.assertEqual(len(errs), 2, errs)
        errs = []
        schema._check({}, s, s, "$", errs)
        self.assertIn("missing required field 'a'", errs[0])

    def test_bool_is_not_integer(self):
        errs = []
        schema._check(True, {"type": "integer"}, {}, "$", errs)
        self.assertTrue(errs)

    def test_all_schema_files_load(self):
        for n in ("gap", "decision", "ac", "workflow_state", "change"):
            self.assertIn("required", schema.load_schema(n))
        for gone in ("requirement", "story"):
            with self.assertRaises(FileNotFoundError):
                schema.load_schema(gone)


class RecordSchemaTests(BATest):
    def test_workflow_state_valid_and_describes_the_source(self):
        p = self.with_requirements()
        self.assertEqual(schema.validate(p.state, "workflow_state"), [])
        self.assertEqual(p.state["stage"], "SOURCE_INDEXED")
        src = p.state["source"]
        self.assertEqual((src["kind"], src["recursive"], src["copied"]), ("folder", False, False))
        self.assertTrue(src["locator"]["id_pattern"])
        bad = copy.deepcopy(p.state)
        bad["schema_version"] = "1.0"
        bad["stage"] = "BRD_PARSED"
        del bad["source"]["root"]
        errs = schema.validate(bad, "workflow_state")
        self.assertGreaterEqual(len(errs), 3, errs)

    def test_gap_requires_justified_priority_known_category_and_a_known_requirement(self):
        p = self.with_requirements()
        bad = dict(self.gap_items()[0], priority_rationale="")
        with self.assertRaises(util.BAError) as c:
            gapmod.add_gaps(p, [bad])
        self.assertEqual(c.exception.code, "GAPS_REJECTED")
        bad = dict(self.gap_items()[0], category="Vibes")
        with self.assertRaises(util.BAError):
            gapmod.add_gaps(p, [bad])
        with self.assertRaises(util.BAError):  # unknown requirement
            gapmod.add_gaps(p, [dict(self.gap_items()[0], impacted_req_keys=["LIB-999"])])
        with self.assertRaises(util.BAError):  # none at all
            gapmod.add_gaps(p, [dict(self.gap_items()[0], impacted_req_keys=[])])

    def test_gap_record_uses_free_form_requirement_keys(self):
        p = self.with_gaps()
        g = p.gap("GAP-001")
        self.assertEqual(schema.validate(g, "gap"), [])
        self.assertEqual(g["impacted_req_keys"], [key("LIB-006")])
        self.assertEqual(g["evidence_quote"], "The fine rate is to be decided.")
        bad = dict(g, impacted_req_keys=[])
        self.assertTrue(any("impacted_req_keys" in e for e in schema.validate(bad, "gap")))
        self.assertTrue(schema.validate(dict(g, impacted_requirement_ids=["REQ-001"], status="DONE"), "gap"))
        self.assertEqual(schema.validate(dict(g, impacted_req_keys=["docs/auth.md::AUTH-004", "FR-2.1"]), "gap"), [], "any ID scheme is accepted")

    def test_ac_record_valid_and_invalid(self):
        p = self.to_acs(1)
        a = p.read_json(f"acceptance/candidates/{projmod.ac_fname(key('LIB-001'))}.json")
        self.assertEqual(schema.validate(a, "ac"), [])
        bad = dict(a, status="DONE", acceptance_criteria=[], requirement_hash="md5:x", readiness="MAYBE")
        self.assertGreaterEqual(len(schema.validate(bad, "ac")), 4)
        bad = copy.deepcopy(a)
        del bad["req_key"]
        self.assertTrue(any("req_key" in e for e in schema.validate(bad, "ac")))
        bad = dict(a, open_gap_refs=[{"gap_id": "GAP-001", "potential_impact": "x", "readiness_effect": "WHATEVER"}])
        self.assertGreaterEqual(len(schema.validate(bad, "ac")), 2)

    def test_decision_record_valid_after_a_resolution(self):
        p = self.with_gaps()
        self.resolve_all(p)
        for d in p.decisions().values():
            rec = {k: v for k, v in d.items() if k not in ("current_status", "status_reason")}
            self.assertEqual(schema.validate(rec, "decision"), [])
            self.assertIn(key("LIB-006") if d["gap_id"] == "GAP-001" else "LIB-", " ".join(rec["related_artifact_ids"]))

    def test_change_record_valid(self):
        p = self.to_acs(1)
        self.approve(p, "LIB-001")
        f = self.src / "membership.md"
        f.write_text(f.read_text(encoding="utf-8").replace("at most 5 books", "at most 6 books"), encoding="utf-8")
        out = changemod.check(p)
        rec = out["changes"][0]
        self.assertEqual(schema.validate(rec, "change"), [])
        self.assertEqual(rec["type"], "SOURCE_CHANGE")
        bad = dict(rec, type="STORY_CHANGE", status="DONE")
        self.assertGreaterEqual(len(schema.validate(bad, "change")), 2)
        bad = dict(rec, items=[{"item_id": "baseline:baseline", "kind": "baseline", "ref": "baseline", "text": "x", "state": "OPEN"}])
        self.assertTrue(schema.validate(bad, "change"), "baseline items no longer exist")

    def test_invalid_state_write_is_refused(self):
        p = self.with_requirements()
        p.state["stage"] = "NOPE"
        with self.assertRaises(util.BAError) as c:
            p.save_state("x")
        self.assertEqual(c.exception.code, "SCHEMA_INVALID")

    def test_invalid_ac_write_is_refused(self):
        p = self.to_acs(1)
        a = p.read_json(f"acceptance/candidates/{projmod.ac_fname(key('LIB-001'))}.json")
        a["acceptance_criteria"] = []
        with self.assertRaises(util.BAError) as c:
            p.save_ac("candidates", a)
        self.assertEqual(c.exception.code, "SCHEMA_INVALID")


class IdTests(BATest):
    def test_requirement_keys_come_from_the_documents_not_from_the_core(self):
        p = self.with_requirements()
        keys = [e["req_key"] for e in p.requirements()]
        self.assertEqual(keys, [key(f"LIB-{i:03d}") for i in (5, 6, 7, 1, 2, 3, 4, 8, 9)], "sorted by file, then position")
        self.assertEqual(len(set(keys)), 9)
        self.assertEqual(p.req_key("LIB-006"), key("LIB-006"))
        self.assertEqual(p.label(key("LIB-006")), "LIB-006")

    def test_next_id_skips_gaps_and_never_reuses(self):
        p = self.new_project()
        self.assertEqual(p.next_id("DEC", ["DEC-001", "DEC-007"]), "DEC-008")
        self.assertEqual(p.next_id("GAP", []), "GAP-001")

    def test_ac_file_names_are_filesystem_safe_and_stable(self):
        self.assertEqual(projmod.ac_fname("lending.md::LIB-006"), "lending.md--LIB-006")
        self.assertEqual(projmod.ac_fname("requirements/auth.md::REQ-012"), "requirements__auth.md--REQ-012")
        odd = projmod.ac_fname("my docs/a b.md::FR-2.1 (x)")
        self.assertRegex(odd, r"^[A-Za-z0-9._-]+$")
        self.assertEqual(odd, projmod.ac_fname("my docs/a b.md::FR-2.1 (x)"))
        self.assertNotEqual(odd, projmod.ac_fname("my docs/a b.md::FR-2.1 (y)"))


class ClockTests(BATest):
    def test_ist_format_and_offset(self):
        ts = util.ist_now_iso()
        self.assertEqual(ts, "2026-10-22T18:45:00+05:30")
        self.assertTrue(util.is_ist_iso(ts))
        self.assertFalse(util.is_ist_iso("2026-10-22T18:45:00"))
        self.assertFalse(util.is_ist_iso("2026-10-22T13:15:00+00:00"))

    def test_naive_or_implausible_clock_refused(self):
        from datetime import datetime, timezone
        util.set_clock(lambda: datetime(2026, 1, 1))
        with self.assertRaises(util.BAError) as c:
            util.now_ist()
        self.assertEqual(c.exception.code, "CLOCK_UNRELIABLE")
        util.set_clock(lambda: datetime(1999, 1, 1, tzinfo=timezone.utc))
        with self.assertRaises(util.BAError):
            util.now_ist()

    def test_human_ist_label(self):
        self.assertIn("IST (Asia/Kolkata, UTC+05:30)", util.human_ist("2026-10-22T18:45:00+05:30"))


class FingerprintTests(BATest):
    def test_fingerprint_is_content_hash(self):
        a = self.reqs_copy("a") / "lending.md"
        b = self.reqs_copy("b") / "lending.md"
        self.assertEqual(util.sha256_file(a), util.sha256_file(b))
        b.write_text(b.read_text(encoding="utf-8") + "\nmore", encoding="utf-8")
        self.assertNotEqual(util.sha256_file(a), util.sha256_file(b))
        self.assertTrue(util.sha256_file(a).startswith("sha256:"))
        self.assertEqual(len(util.sha256_file(a)), 71)

    def test_the_index_and_state_carry_the_source_fingerprints(self):
        p = self.with_requirements()
        doc = reqindex.load(p)
        for rel, f in doc["files"].items():
            self.assertEqual(f["file_hash"], util.sha256_file(FIXTURE / rel), rel)
        self.assertEqual(sorted(doc["files"]), ["lending.md", "membership.md", "reporting.md"], "README.md is not a requirements file")
        self.assertEqual(p.state["source"]["fingerprint"], reqindex.tree_fingerprint(doc))
        for e in doc["requirements"]:
            self.assertRegex(e["text_hash"], r"^sha256:[0-9a-f]{64}$")
            self.assertRegex(e["material_hash"], r"^sha256:[0-9a-f]{64}$")

    def test_the_fingerprint_changes_when_any_file_changes_but_not_when_nothing_does(self):
        a, b = self.reqs_copy("a"), self.reqs_copy("b")
        pa = projmod.Project.create(self.tmp / "oa", a)
        pb = projmod.Project.create(self.tmp / "ob", b)
        self.assertEqual(pa.state["source"]["fingerprint"], pb.state["source"]["fingerprint"])
        (b / "reporting.md").write_text((b / "reporting.md").read_text(encoding="utf-8") + "\nmore\n", encoding="utf-8")
        pc = projmod.Project.create(self.tmp / "oc", b)
        self.assertNotEqual(pa.state["source"]["fingerprint"], pc.state["source"]["fingerprint"])


if __name__ == "__main__":
    unittest.main()
