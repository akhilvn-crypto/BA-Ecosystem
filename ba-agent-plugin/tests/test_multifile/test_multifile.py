"""Folders of requirement files: key collisions, files added/removed/renamed, skipped files, recursion, single-file source, copied source."""
import unittest

from tests.helpers import BATest, acs, changemod, checks, docsync, gapmod, reqindex, util, Project, key


def req(rid, title="Thing", body="The system shall do the thing."):
    return f"### {rid} {title}\n{body}\n\n"


class MultiFileTests(BATest):
    def folder(self, **files):
        d = self.tmp / "multi"
        for name, text in files.items():
            self.write_req(d, name.replace("__", "/"), text)
        return d

    def test_same_id_in_two_files_needs_the_full_key(self):
        d = self.folder(**{"a.md": req("REQ-001", "Alpha"), "b.md": req("REQ-001", "Beta") + req("REQ-002")})
        p = Project.create(self.tmp / "out", d)
        keys = [e["req_key"] for e in p.requirements()]
        self.assertEqual(keys, ["a.md::REQ-001", "b.md::REQ-001", "b.md::REQ-002"])
        with self.assertRaises(util.BAError) as cm:
            p.req_key("REQ-001")
        self.assertEqual(cm.exception.code, "AMBIGUOUS_REQUIREMENT")
        self.assertEqual(cm.exception.details["keys"], ["a.md::REQ-001", "b.md::REQ-001"])
        self.assertEqual(p.req_key("REQ-002"), "b.md::REQ-002")  # unique ID: the short form works
        self.assertEqual(p.req_key("b.md::REQ-001"), "b.md::REQ-001")
        self.assertEqual(p.req_key("a.md::REQ-001"), "a.md::REQ-001")
        labels = reqindex.labels(p)
        self.assertEqual(labels["a.md::REQ-001"], "a.md::REQ-001")  # the full key where the ID is ambiguous
        self.assertEqual(labels["b.md::REQ-002"], "REQ-002")
        v = checks.validate_project(p)
        self.assertTrue(any("REQ-001 appears in 2 places" in w for w in v["warnings"]))
        with self.assertRaises(util.BAError):
            p.req_key("REQ-404")

    def test_gaps_and_ac_work_with_colliding_ids(self):
        d = self.folder(**{"a.md": req("REQ-001", "Alpha", "Fines apply late."), "b.md": req("REQ-001", "Beta", "Members can register.")})
        p = Project.create(self.tmp / "out", d)
        gapmod.add_gaps(p, [dict(category="Business rules", title="Fine rate", description="No fine rate given.", evidence="a.md says 'apply'",
                                 why_it_matters="Untestable.", question="What is the fine rate per day?", impacted_req_keys=["a.md::REQ-001"],
                                 priority="LOW", priority_rationale="Minor.")])
        with self.assertRaises(util.BAError) as cm:
            gapmod.add_gaps(p, [dict(category="Business rules", title="Other", description="d" * 5, evidence="e" * 5, why_it_matters="w" * 5,
                                     question="Which REQ-001 do you mean here?", impacted_req_keys=["REQ-001"], priority="LOW", priority_rationale="Minor.")])
        self.assertIn("matches several requirements", str(cm.exception.details))
        self.assertIn("| GAP-001 |", (p.enriched_root / "a.md").read_text(encoding="utf-8"))
        self.assertNotIn("| GAP-001 |", (p.enriched_root / "b.md").read_text(encoding="utf-8"))

    def test_readme_and_index_files_are_skipped_and_reported(self):
        d = self.folder(**{"a.md": req("REQ-001"), "README.md": "# Hello\nabout this folder\n", "index.md": "# Index\n- a\n",
                           "CHANGELOG.md": "# Changes\nstuff\n", "notes.md": "just words, no headings\n", ".hidden.md": req("REQ-9")})
        p = Project.create(self.tmp / "out", d)
        self.assertEqual({e["file"] for e in p.requirements()}, {"a.md"})
        skipped = {s["file"] for s in p.index_report["skipped"]}
        self.assertTrue({"README.md", "index.md", "CHANGELOG.md", "notes.md"} <= skipped)
        self.assertNotIn(".hidden.md", skipped)

    def test_no_requirement_files_at_all(self):
        d = self.folder(**{"README.md": "# Hello\ntext\n"})
        with self.assertRaises(util.BAError) as cm:
            Project.create(self.tmp / "out", d)
        self.assertEqual(cm.exception.code, "NO_REQUIREMENTS_FOUND")

    def test_recursive(self):
        d = self.folder(**{"a.md": req("REQ-001"), "sub__b.md": req("REQ-002"), "sub__deep__c.md": req("REQ-003")})
        flat = Project.create(self.tmp / "flat", d)
        self.assertEqual({e["file"] for e in flat.requirements()}, {"a.md"})
        rec = Project.create(self.tmp / "rec", d, recursive=True)
        self.assertEqual({e["file"] for e in rec.requirements()}, {"a.md", "sub/b.md", "sub/deep/c.md"})
        self.assertTrue((rec.enriched_root / "sub" / "deep" / "c.md").exists())  # same relative path
        self.assertEqual(rec.req_key("REQ-003"), "sub/deep/c.md::REQ-003")

    def test_single_file_source(self):
        d = self.folder(**{"only.md": req("REQ-001") + req("REQ-002"), "other.md": req("REQ-009")})
        p = Project.create(self.tmp / "out", d / "only.md")
        self.assertEqual(p.state["source"]["kind"], "file")
        self.assertEqual([e["req_key"] for e in p.requirements()], ["only.md::REQ-001", "only.md::REQ-002"])  # other.md is not input
        self.assertFalse((p.enriched_root / "other.md").exists())
        self.assertEqual(changemod.detect_source(p).get("unchanged"), True)

    def test_single_file_wrong_format(self):
        d = self.folder(**{"x.txt": "REQ-001 something"})
        with self.assertRaises(util.BAError) as cm:
            Project.create(self.tmp / "out", d / "x.txt")
        self.assertEqual(cm.exception.code, "UNSUPPORTED_FORMAT")
        self.assertIn("Markdown", cm.exception.message)

    def test_copy_source_keeps_a_copy_and_reads_from_it(self):
        d = self.folder(**{"a.md": req("REQ-001"), "b.md": req("REQ-002")})
        p = Project.create(self.tmp / "out", d, copy_source=True)
        copy = p.root / "source" / "multi"
        self.assertTrue(p.state["source"]["copied"])
        self.assertEqual((copy / "a.md").read_bytes(), (d / "a.md").read_bytes())
        self.assertEqual(p.state["source"]["root"], str(copy.resolve()))
        self.assertEqual(p.state["source"]["original_path"], str(d.resolve()))
        with self.assertRaises(util.BAError) as cm:
            p.write_text("source/multi/a.md", "x")
        self.assertEqual(cm.exception.code, "SOURCE_PROTECTED")
        self.assertEqual(changemod.detect_source(p).get("unchanged"), True)

    def test_file_added_removed_and_renamed(self):
        d = self.folder(**{"a.md": req("REQ-001"), "b.md": req("REQ-002")})
        p = Project.create(self.tmp / "out", d)
        self.write_req(d, "c.md", req("REQ-003"))
        (d / "b.md").unlink()
        out = changemod.check(p, True)
        rec = out["changes"][0]
        self.assertEqual(rec["subject"]["files"], {"added": ["c.md"], "removed": ["b.md"], "changed": []})
        kinds = {(c["change"], c.get("req_key") or c.get("file")) for c in rec["changes"]}
        self.assertIn(("FILE_ADDED", "c.md"), kinds)
        self.assertIn(("FILE_REMOVED", "b.md"), kinds)
        self.assertIn(("ADDED", "c.md::REQ-003"), kinds)
        self.assertIn(("DELETED", "b.md::REQ-002"), kinds)
        changemod.apply(p, rec["change_id"], "Priya Nair", "Product Owner", "Apply.")
        keys = {e["req_key"]: e["status"] for e in p.requirements(False)}
        self.assertEqual(keys, {"a.md::REQ-001": "ACTIVE", "b.md::REQ-002": "REMOVED", "c.md::REQ-003": "ACTIVE"})
        self.assertTrue((p.enriched_root / "c.md").exists())
        self.assertFalse((p.enriched_root / "b.md").exists())
        # a rename shows as removed + added (identity comes from the document)
        (d / "c.md").rename(d / "z.md")
        out = changemod.check(p, True)
        self.assertEqual(out["changes"][0]["impact"]["new_requirements"][0]["req_key"], "z.md::REQ-003")

    def test_unreadable_file_is_reported_and_blocks_acceptance(self):
        d = self.folder(**{"a.md": req("REQ-001")})
        p = Project.create(self.tmp / "out", d)
        (d / "a.md").write_bytes(b"### REQ-001 x\n\xff\xfe broken")
        out = changemod.check(p, True)
        self.assertEqual(out["source"], "unreadable")
        self.assertEqual(out["unreviewed_ids"], [])
        self.assertTrue(out["warnings"])

    def test_invalid_utf8_refused_at_init(self):
        d = self.folder(**{"a.md": req("REQ-001")})
        (d / "bad.md").write_bytes(b"### REQ-002 x\n\xff\xfe")
        with self.assertRaises(util.BAError) as cm:
            Project.create(self.tmp / "out", d)
        self.assertEqual(cm.exception.code, "UNREADABLE_SOURCE")

    def test_find_lists_requirement_folders(self):
        from ba_lib import discover
        base = self.tmp / "ws"
        self.write_req(base / "requirements", "a.md", req("REQ-001"))
        self.write_req(base / "docs", "b.md", "# notes\nno requirements here beyond words\n")
        out = discover.find(str(base))
        self.assertEqual([c["path"] for c in out["requirement_candidates"]][0], str((base / "requirements").resolve()))
        self.assertTrue(out["requirement_candidates"][0]["with_ids"])
        self.assertFalse(out["requirement_candidates"][1]["with_ids"])  # docs/ only matches by headings, so it sorts last
        self.assertIn("Several folders", out["next"])

    def test_pairs_ignore_the_ids_themselves(self):
        d = self.folder(**{"a.md": req("REQ-003", "One", "The system shall send an email."), "b.md": req("REQ-007", "Two", "Staff can view the report.")})
        p = Project.create(self.tmp / "out", d)
        rep = reqindex.pairs_report(p)
        self.assertEqual(rep["possible_conflicts"], [])  # 3 vs 7 must not look like a numeric conflict


if __name__ == "__main__":
    unittest.main()
