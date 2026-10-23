"""Unit tests: schema validator, record schemas, IDs, IST time, fingerprints."""
import json
import unittest

from tests.helpers import BATest, FIXTURE, gapmod, reqs, util
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
        for n in ("requirement", "gap", "decision", "story", "workflow_state", "change"):
            self.assertIn("required", schema.load_schema(n))


class RecordSchemaTests(BATest):
    def test_requirement_record_valid_and_invalid(self):
        p = self.with_requirements()
        r = p.requirement("REQ-001")
        self.assertEqual(schema.validate(r, "requirement"), [])
        bad = dict(r, requirement_id="R1", status="DONE")
        self.assertGreaterEqual(len(schema.validate(bad, "requirement")), 2)
        del bad["source_reference"]
        self.assertTrue(any("source_reference" in e for e in schema.validate(bad, "requirement")))

    def test_workflow_state_valid(self):
        p = self.with_requirements()
        self.assertEqual(schema.validate(p.state, "workflow_state"), [])
        self.assertEqual(p.state["stage"], "REQUIREMENTS_READY")

    def test_gap_requires_justified_priority_and_known_category(self):
        p = self.with_requirements()
        bad = dict(self.gap_items()[0], priority_rationale="")
        with self.assertRaises(util.BAError) as c:
            gapmod.add_gaps(p, [bad])
        self.assertEqual(c.exception.code, "GAPS_REJECTED")
        bad = dict(self.gap_items()[0], category="Vibes")
        with self.assertRaises(util.BAError):
            gapmod.add_gaps(p, [bad])
        with self.assertRaises(util.BAError):  # unknown requirement
            gapmod.add_gaps(p, [dict(self.gap_items()[0], impacted_requirement_ids=["REQ-999"])])

    def test_invalid_state_write_is_refused(self):
        p = self.with_requirements()
        p.state["stage"] = "NOPE"
        with self.assertRaises(util.BAError) as c:
            p.save_state("x")
        self.assertEqual(c.exception.code, "SCHEMA_INVALID")


class IdTests(BATest):
    def test_stable_unique_sequential_ids(self):
        p = self.with_requirements()
        ids = p.req_ids()
        self.assertEqual(ids, [f"REQ-{i:03d}" for i in range(1, 9)])
        out = reqs.add_requirements(p, [dict(self.req_items(p)[0], title="Another one", statement="A totally different statement about audit trails.",
                                             source_excerpt="A visitor shall register with a valid email address and a password to become a member.")])
        self.assertEqual(out["created"], ["REQ-009"])
        self.assertEqual(len(set(p.req_ids())), 9)

    def test_next_id_skips_gaps_and_never_reuses(self):
        p = self.new_project()
        self.assertEqual(p.next_id("DEC", ["DEC-001", "DEC-007"]), "DEC-008")
        self.assertEqual(p.next_id("US", []), "US-001")


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
        a = self.brd_copy("a.md")
        b = self.brd_copy("b.md")
        self.assertEqual(util.sha256_file(a), util.sha256_file(b))
        b.write_text(b.read_text(encoding="utf-8") + "\nmore", encoding="utf-8")
        self.assertNotEqual(util.sha256_file(a), util.sha256_file(b))
        self.assertTrue(util.sha256_file(a).startswith("sha256:"))
        self.assertEqual(len(util.sha256_file(a)), 71)

    def test_requirement_carries_source_fingerprint(self):
        p = self.with_requirements()
        self.assertEqual(p.requirement("REQ-001")["source_fingerprint"], util.sha256_file(p.brd_path) if hasattr(p, "brd_path") else p.state["source"]["fingerprint"])
        self.assertEqual(p.state["source"]["fingerprint"], util.sha256_file(FIXTURE))


if __name__ == "__main__":
    unittest.main()
