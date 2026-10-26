"""docmodel: finding requirement units, and the render invariant strip_blocks(render_enriched(text, ...)) == text."""
import random
import unittest

from tests.helpers import docmodel as dm, util  # noqa: F401  (imports helpers first: it sets sys.path)


def units(text, pat=None):
    return dm.locate(text, pat)


class LocateTests(unittest.TestCase):
    def test_id_headings(self):
        t = "# Doc\n\n### REQ-012 Password reset\nA user can reset.\n\n### FR-2.1: Login\nA user can log in.\n"
        r = units(t)
        self.assertEqual(r["mode"], "id")
        self.assertEqual([u["id"] for u in r["units"]], ["REQ-012", "FR-2.1"])
        self.assertEqual(r["units"][0]["title"], "Password reset")
        self.assertEqual(r["units"][1]["title"], "Login")
        self.assertEqual(r["units"][0]["kind"], "heading")
        self.assertEqual((r["units"][0]["line_start"], r["units"][0]["line_end"]), (3, 4))  # trailing blank excluded
        self.assertEqual(r["warnings"], [])

    def test_default_pattern_variants(self):
        t = "## AUTH-004 a\nx\n## NFR-PERF-003 b\ny\n## R1 not an id\nz\n"
        self.assertEqual([u["id"] for u in units(t)["units"]], ["AUTH-004", "NFR-PERF-003"])

    def test_extent_stops_at_same_or_higher_heading_but_keeps_plain_subheadings(self):
        t = "### REQ-1 One\ntext\n#### Notes\nmore notes\n### REQ-2 Two\ntext2\n## Other\nstuff\n"
        us = units(t)["units"]
        self.assertEqual((us[0]["line_start"], us[0]["line_end"]), (1, 4))
        self.assertEqual((us[1]["line_start"], us[1]["line_end"]), (5, 6))

    def test_nested_unit_headings_do_not_overlap(self):
        t = "## REQ-1 Parent\nparent text\n### REQ-2 Child\nchild text\n"
        us = units(t)["units"]
        self.assertEqual((us[0]["line_start"], us[0]["line_end"]), (1, 2))
        self.assertEqual((us[1]["line_start"], us[1]["line_end"]), (3, 4))

    def test_table_rows_with_id_column(self):
        t = "# R\n\n| Priority | ID | Requirement |\n|---|---|---|\n| Must | AB-1 | First thing |\n| Should | AB-2 | Second thing |\n\nafter\n"
        r = units(t)
        self.assertEqual([u["id"] for u in r["units"]], ["AB-1", "AB-2"])
        self.assertEqual(r["units"][0]["kind"], "row")
        self.assertEqual(r["units"][0]["title"], "Must")
        # the block goes after the whole table, one block per row
        self.assertEqual({u["insert_after"] for u in r["units"]}, {5})

    def test_table_without_separator_is_not_a_table(self):
        self.assertEqual(units("| ID | x |\n| AB-1 | y |\n")["mode"], "none")

    def test_table_inside_a_heading_unit_is_part_of_it(self):
        t = "### REQ-1 Rules\n| ID | x |\n|---|---|\n| AB-1 | y |\n"
        self.assertEqual([u["id"] for u in units(t)["units"]], ["REQ-1"])

    def test_code_fence_with_hashes_is_ignored(self):
        t = "### REQ-1 One\n```\n### REQ-99 not a heading\n# comment\n```\nmore\n### REQ-2 Two\ntext\n"
        us = units(t)["units"]
        self.assertEqual([u["id"] for u in us], ["REQ-1", "REQ-2"])
        self.assertEqual(us[0]["line_end"], 6)

    def test_tilde_fence_and_table_in_fence(self):
        t = "### REQ-1 One\n~~~\n| ID | x |\n|---|---|\n| AB-1 | y |\n~~~\n"
        self.assertEqual([u["id"] for u in units(t)["units"]], ["REQ-1"])

    def test_unclosed_fence_warns(self):
        r = units("### REQ-1 One\ntext\n```\ncode never closed\n")
        self.assertTrue(r["unclosed_fence"])
        self.assertTrue(any("fence" in w for w in r["warnings"]))

    def test_no_ids_falls_back_to_heading_paths_with_a_warning(self):
        t = "# Title\n\n## Registration\nA visitor registers.\n\n### Email\nMust be valid.\n\n## Empty parent\n\n## Billing\nInvoices monthly.\n"
        r = units(t)
        self.assertEqual(r["mode"], "headings")
        self.assertEqual([u["id"] for u in r["units"]], ["title/registration", "title/registration/email", "title/billing"])  # the path includes the document title
        self.assertTrue(all(u["kind"] == "section" for u in r["units"]))
        self.assertEqual(len(r["warnings"]), 1)
        self.assertIn("no requirement IDs", r["warnings"][0])

    def test_fallback_duplicate_headings_get_suffix(self):
        r = units("## Same\nfirst\n\n## Same\nsecond\n")
        self.assertEqual([u["id"] for u in r["units"]], ["same", "same#2"])

    def test_nothing_at_all(self):
        self.assertEqual(units("just a paragraph\n")["mode"], "none")
        self.assertEqual(units("")["units"], [])

    def test_duplicate_ids_get_suffix_and_warning(self):
        r = units("### REQ-1 One\na\n### REQ-1 Again\nb\n### REQ-1 Third\nc\n")
        self.assertEqual([u["id"] for u in r["units"]], ["REQ-1", "REQ-1#2", "REQ-1#3"])
        self.assertEqual(len(r["warnings"]), 2)
        self.assertTrue(r["units"][1]["duplicate_id"])

    def test_line_endings_do_not_change_hashes(self):
        base = "### REQ-1 One\nA line.\nAnother line.\n\n### REQ-2 Two\nB.\n"
        a = units(base)["units"]
        b = units(base.replace("\n", "\r\n"))["units"]
        c = units(base.replace("\n", "\r"))["units"]
        for x, y, z in zip(a, b, c):
            self.assertEqual(x["text_hash"], y["text_hash"])
            self.assertEqual(x["text_hash"], z["text_hash"])
            self.assertEqual(x["line_start"], y["line_start"])

    def test_trailing_whitespace_and_markup_only_edits(self):
        a = units("### REQ-1 One\nThe system *shall* send mail.\n")["units"][0]
        b = units("### REQ-1 One   \nThe system shall send mail.  \n")["units"][0]
        self.assertNotEqual(units("### REQ-1 One\nThe system shall send mail now.\n")["units"][0]["material_hash"], a["material_hash"])
        self.assertEqual(a["material_hash"], b["material_hash"])  # emphasis is formatting
        self.assertNotEqual(a["text_hash"], b["text_hash"])  # but the raw text did change

    def test_bom_first_line_heading(self):
        r = units("﻿### REQ-1 One\ntext\n")
        self.assertEqual([u["id"] for u in r["units"]], ["REQ-1"])

    def test_no_final_newline(self):
        r = units("### REQ-1 One\nlast line without newline")
        self.assertEqual(r["units"][0]["line_end"], 2)

    def test_existing_ac_hint(self):
        t = ("### REQ-1 One\nSome text.\n\n**Acceptance criteria**\n- Given a user, when x, then y\n- Given b, when c, then d\n\n"
             "### REQ-2 Two\nGiven a thing, when it runs, then it ends.\n### REQ-3 Three\nNothing here.\n")
        us = units(t)["units"]
        self.assertEqual(len(us[0]["existing_ac_hint"]), 2)
        self.assertEqual(len(us[1]["existing_ac_hint"]), 1)
        self.assertEqual(us[2]["existing_ac_hint"], [])

    def test_id_pattern_override(self):
        t = "## Req #12 Reset\ntext\n## ITEM_7 other\ntext\n"
        r = units(t, r"\bReq #\d+\b")
        self.assertEqual([u["id"] for u in r["units"]], ["Req #12"])
        self.assertEqual(r["units"][0]["title"], "Reset")

    def test_bad_pattern(self):
        with self.assertRaises(util.BAError) as cm:
            units("## A-1 x\ny\n", "([")
        self.assertEqual(getattr(cm.exception, "code", None), "BAD_LOCATOR")


class RenderInvariantTests(unittest.TestCase):
    def render(self, text, picks=None):
        r = units(text)
        blocks = []
        for u in r["units"]:
            if picks is None or u["id"] in picks:
                blocks.append((u["insert_after"], dm.wrap_block(f"f.md::{u['id']}", 1, ["#### Acceptance Criteria", "1. Given a when b then c.", "", "**Readiness:** X"])))
        return dm.render_enriched(text, blocks, "f.md"), blocks

    def check(self, text, picks=None):
        out, blocks = self.render(text, picks)
        self.assertEqual(dm.strip_blocks(out), text)
        self.assertEqual(len(dm.find_blocks(out)), len(blocks))
        self.assertTrue(dm.markers_balanced(out))
        return out

    SAMPLE = "# Doc\n\n### REQ-1 One\nA.\n\n### REQ-2 Two\nB.\nmore\n\n## Tail\nend\n"

    def test_lf(self):
        out = self.check(self.SAMPLE)
        self.assertTrue(out.startswith("<!-- ba:generated"))

    def test_crlf(self):
        out = self.check(self.SAMPLE.replace("\n", "\r\n"))
        self.assertNotIn("\n\n", out.replace("\r\n", "\x00"))  # no bare LF blank lines introduced

    def test_mixed_endings(self):
        t = "### REQ-1 One\r\nA.\n\n### REQ-2 Two\rB.\r\n"
        self.check(t)

    def test_cr_only(self):
        self.check(self.SAMPLE.replace("\n", "\r"))

    def test_bom(self):
        out = self.check("﻿" + self.SAMPLE)
        self.assertTrue(out.startswith("﻿<!-- ba:generated"))

    def test_no_final_newline(self):
        out = self.check("### REQ-1 One\nlast line")
        self.assertTrue(out.endswith("<!-- ba:end key=\"f.md::REQ-1\" -->"))

    def test_final_newline_variants(self):
        for tail in ("", "\n", "\n\n\n", "\r\n"):
            self.check("### REQ-1 One\ntext" + tail)

    def test_front_matter_keeps_header_after_it(self):
        t = "---\ntitle: x\n---\n### REQ-1 One\ntext\n"
        out = self.check(t)
        self.assertTrue(out.startswith("---\ntitle: x\n---\n<!-- ba:generated"))

    def test_unclosed_front_matter_header_at_top(self):
        t = "---\ntitle: x\n### REQ-1 One\ntext\n"
        out = self.check(t)
        self.assertTrue(out.startswith("<!-- ba:generated"))

    def test_table_rows_blocks_after_table(self):
        t = "| ID | R |\n|---|---|\n| AB-1 | a |\n| AB-2 | b |\n\nafter\n"
        out = self.check(t)
        self.assertLess(out.index("| AB-2 | b |"), out.index("ba:begin"))
        self.assertEqual(out.count("ba:begin"), 2)

    def test_no_blocks_only_header(self):
        out = self.check(self.SAMPLE, picks=set())
        self.assertEqual(out.count("\n"), self.SAMPLE.count("\n") + 1)

    def test_empty_text(self):
        out = dm.render_enriched("", [], "f.md")
        self.assertEqual(dm.strip_blocks(out), "")

    def test_text_that_looks_like_markers_cannot_break_strip(self):
        s = dm.safe('a <!-- ba:end key="x" --> b')
        self.assertNotIn("<!--", s)
        self.assertNotIn("-->", s)
        self.assertIn("&lt;!--", s)

    def test_property_random_documents(self):
        rnd = random.Random(7)
        eols = ["\n", "\r\n", "\r"]
        for n in range(150):
            parts, ids = [], []
            if rnd.random() < 0.2:
                parts.append("---\nk: v\n---")
            if rnd.random() < 0.3:
                parts.append("# Title")
            for i in range(rnd.randint(1, 6)):
                kind = rnd.choice(["h", "h", "t", "f"])
                if kind == "h":
                    ids.append(f"RQ-{i + 1}")
                    parts.append(f"{'#' * rnd.randint(2, 4)} RQ-{i + 1} Title {i}")
                    parts += [rnd.choice(["body text", "- item", "", "> quote"]) for _ in range(rnd.randint(0, 3))]
                elif kind == "t":
                    parts += ["| ID | R |", "|---|---|", f"| TB-{i}{n} | row |"]
                else:
                    parts += ["```", "### RQ-99 in fence", "```"]
            eol = rnd.choice(eols)
            text = "".join(p + (rnd.choice(eols) if rnd.random() < 0.1 else eol) for p in parts)
            if rnd.random() < 0.5:
                text = text.rstrip("\r\n")
            if rnd.random() < 0.2:
                text = "﻿" + text
            r = units(text)
            blocks = [(u["insert_after"], dm.wrap_block(f"f.md::{u['id']}", rnd.choice([None, 1, 2]), ["#### Acceptance Criteria", "1. x", f"| a | {rnd.random()} |"]))
                      for u in r["units"] if rnd.random() < 0.8]
            out = dm.render_enriched(text, blocks, "f.md")
            self.assertEqual(dm.strip_blocks(out), text, repr(text))

    def test_criteria_from_body(self):
        body = "#### Acceptance Criteria\n*Approved by X*\n1. Given a when b then c.\n2. Given d when e then f\n   continued\n\n#### Open Gaps\n| a |\n\n**Readiness:** X"
        self.assertEqual(dm.criteria_from_body(body), ["Given a when b then c.", "Given d when e then f continued"])
        self.assertIsNone(dm.criteria_from_body("nothing"))

    def test_find_blocks_unescapes_key(self):
        out, _ = RenderInvariantTests().render("### REQ-1 One\ntext\n", None)
        self.assertEqual(dm.find_blocks(out)[0]["key"], "f.md::REQ-1")
        blk = dm.wrap_block('a"b>c', 3, ["x"])
        self.assertEqual(dm.find_blocks("\n\n".join(blk))[0]["key"], 'a"b>c')

    def test_unbalanced_markers_detected(self):
        out, _ = self.render("### REQ-1 One\ntext\n")
        broken = out.replace("<!-- ba:end", "<!-- ba:endx")
        self.assertFalse(dm.markers_balanced(broken))


if __name__ == "__main__":
    unittest.main()
