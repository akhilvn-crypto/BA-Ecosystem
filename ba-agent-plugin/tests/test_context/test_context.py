"""Context engineering: each stage reads only what it needs.

Covers the presentation layer (card + dialog built by the core), lean/compact CLI output, the triage-stories dead end
(a stale baseline after gap triage), BRD batching for helper agents, and byte budgets for the prompt files and the CLI
results a model reads. The budgets are regression guards: raise one only on purpose.
"""
import contextlib
import io
import json
import os
import re
from pathlib import Path
from unittest import mock

from tests.helpers import PLUGIN, BATest, basemod, gapmod, ingest, option_labels, stories
from ba_lib import cli, present
from ba_lib.project import Project

OWNER = ("Priya Nair", "Product Owner")
SKILLS = PLUGIN / "skills"
STAGES = SKILLS / "ba-workflow" / "references" / "stages"


def ba(root, *argv):
    """Run the real CLI in-process; returns (raw text, parsed JSON or None)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(["--root", str(root), *argv])
    raw = buf.getvalue()
    try:
        return raw, json.loads(raw)
    except ValueError:
        return raw, None


class Flow(BATest):
    """Gaps resolved after stories were written - the exact state `/ba-agent:triage-stories` starts from."""

    def runTest(self):
        pass

    def after_gap_triage(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        gapmod.next_gaps(p, 3)
        gapmod.carry_forward(p, "GAP-002", *OWNER, reason="later")
        self.answer_and_resolve(p, "GAP-001", "R10 per day late, capped at R200.", "Fine is R10/day, max R200.")
        self.answer_and_resolve(p, "GAP-003", "Block the borrow.", "A 6th borrow is rejected.")
        gapmod.commit_analysis(p, True)
        basemod.create(p, "initial")
        basemod.confirm(p, 1, *OWNER, "I approve baseline v1.")
        stories.add_stories(p, [
            self.story_item("Register as a member", ("REQ-001",)),
            self.story_item("Charge a late fine", ("REQ-006",)),
            self.story_item("Mobile page speed", ("REQ-008",), unresolved_decisions=[
                dict(gap_id="GAP-002", potential_impact="The load-time target is unknown.", readiness_effect="NEEDS_CLARIFICATION")])])
        # what /ba-agent:triage-gaps does next: revisit the carried-forward gap and close it
        gapmod.revisit(p, "GAP-002")
        self.answer_and_resolve(p, "GAP-002", "Under 3 seconds on 4G.", "Mobile pages load in under 3 seconds on 4G.")
        return p


class TriageStoriesDeadEndTests(Flow):
    def test_a_stale_baseline_is_one_preflight_item_not_a_blocker_on_every_story(self):
        p = self.after_gap_triage()
        out = stories.triage(p)
        self.assertEqual([x["id"] for x in out["preflight"]], ["baseline_stale"])
        self.assertIn("`baseline create`", out["preflight"][0]["action"])
        self.assertEqual(out["eligible"], [], "nothing can be approved until the baseline is refreshed")
        self.assertEqual(out["eligible_after_preflight"], ["US-001", "US-002"], "US-003 still needs its own update")
        for row in out["stories"]:
            self.assertFalse(any("baseline" in b for b in row["blockers"]), row["blockers"])
        self.assertIn("Clear first: the baseline is stale", out["summary"])
        self.assertIn("one dialog", out["next"])

    def test_refreshing_the_baseline_unblocks_the_stories_that_have_no_problem_of_their_own(self):
        p = self.after_gap_triage()
        basemod.create(p)
        basemod.confirm(p, 2, *OWNER, "I approve baseline v2.")
        out = stories.triage(p)
        self.assertEqual(out["preflight"], [])
        self.assertEqual(out["eligible"], ["US-001", "US-002"])
        self.assertEqual(out["not_eligible"], ["US-003"])
        labels = option_labels(stories.next_story(p, "US-001")["stories"][0])
        self.assertEqual(labels, ["Approve v1", "Defer story", "Not approved"])

    def test_a_story_flagged_by_a_resolved_gap_offers_update_and_carries_the_decision_to_apply(self):
        p = self.after_gap_triage()
        basemod.create(p)
        basemod.confirm(p, 2, *OWNER, "I approve baseline v2.")
        item = stories.next_story(p, "US-003")["stories"][0]
        self.assertEqual(option_labels(item), ["Update story", "Defer story", "Keep as is"])
        self.assertNotIn("No change needed", option_labels(item), "a resolved gap cannot be cleared as unaffected")
        self.assertFalse(item["eligible"])
        card = item["present"]["card"]
        self.assertIn("Decision to apply", card)
        self.assertIn("Mobile pages load in under 3 seconds on 4G.", card)
        self.assertIn("3 of 3", item["present"]["dialog"]["question"])

    def test_a_story_blocked_by_an_open_gap_offers_no_update_and_no_approve(self):
        p = self.after_gap_triage()
        gapmod.reopen(p, "GAP-001", "Finance changed the fine rule", *OWNER)
        gapmod.commit_analysis(p, True)
        basemod.create(p)
        basemod.confirm(p, 2, *OWNER, "I approve baseline v2.")
        item = next(s for s in stories.next_story(p, "US-002", batch=1)["stories"])
        labels = option_labels(item)
        self.assertFalse(any(x.startswith("Approve") for x in labels), labels)
        self.assertIn("open GAP-001", item["present"]["dialog"]["question"])

    def test_story_next_returns_the_card_not_the_raw_story(self):
        p = self.after_gap_triage()
        item = stories.next_story(p, "US-001")["stories"][0]
        self.assertNotIn("story", item)
        self.assertNotIn("review_history", json.dumps(item))
        full = stories.next_story(p, "US-001", full=True)["stories"][0]
        self.assertIn("review_history", full["story"])
        self.assertIn("requirement_statements", full["story"])

    def test_dialogs_respect_the_tool_limits(self):
        p = self.after_gap_triage()
        basemod.create(p)
        basemod.confirm(p, 2, *OWNER, "I approve baseline v2.")
        for sid in ("US-001", "US-002", "US-003"):
            d = stories.next_story(p, sid)["stories"][0]["present"]["dialog"]
            self.assertLessEqual(len(d["header"]), 12)
            self.assertTrue(2 <= len(d["options"]) <= 4, d["options"])
            self.assertTrue(all(o["label"] and o["description"] and len(o["label"].split()) <= 5 for o in d["options"]))
            self.assertFalse(d["multiSelect"])
            self.assertFalse(any("Recommended" in o["label"] for o in d["options"]))


class GapPresentationTests(BATest):
    def runTest(self):
        pass

    def seeded(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        return p

    def test_a_plain_gap_is_inline_and_fits_the_dialog(self):
        p = self.seeded()
        pk = gapmod.next_gaps(p, 1, gap_id="GAP-003")["gaps"][0]
        view = pk["present"]
        self.assertTrue(view["inline"], "the dialog alone carries everything: no separate chat message")
        q = view["dialog"]["question"]
        self.assertLessEqual(len(q), present.DIALOG_QUESTION_MAX)
        for needle in ("HIGH", "blocks stories", "GAP-003", "borrow a 6th book", "Why:", "Source:", "REQ-003"):
            self.assertIn(needle, q)
        self.assertLessEqual(len(view["dialog"]["header"]), 12)
        self.assertNotIn("card", view, "an inline gap has no separate card")
        self.assertNotIn("fallback", view, "the fallback repeats the dialog, so it is built only on request")
        self.assertNotIn("question", pk, "the question is carried once, by the dialog")
        view = gapmod.next_gaps(p, 1, gap_id="GAP-003", fallback=True)["gaps"][0]["present"]
        self.assertIn(q, view["fallback"], "without the tool the question itself is the message")
        self.assertIn("Reply with:", view["fallback"])

    def test_the_packet_no_longer_repeats_what_the_card_and_explain_carry(self):
        p = self.seeded()
        pk = gapmod.next_gaps(p, 1, gap_id="GAP-003")["gaps"][0]
        for dropped in ("explain", "impacted_requirements", "evidence", "why_it_matters", "options", "decision_owner", "previous_answers"):
            self.assertNotIn(dropped, pk)
        self.assertEqual(gapmod.explain(p, "GAP-003")["impact_on_requirements"][0]["id"], "REQ-003")

    def test_history_moves_a_gap_from_inline_to_card(self):
        p = self.seeded()
        gapmod.next_gaps(p, 3)
        gapmod.record_answer(p, "GAP-002", "Fast enough")
        pk = gapmod.next_gaps(p, 1, gap_id="GAP-002")["gaps"][0]
        self.assertFalse(pk["present"]["inline"])
        self.assertIn("Earlier answer A1", pk["present"]["card"])
        self.assertIn("Fast enough", pk["present"]["card"])

    def test_a_long_gap_is_truncated_in_the_dialog_and_complete_on_the_card(self):
        p = self.with_requirements()
        items = self.gap_items()
        items[0]["evidence"] = "BRD 3.2: " + "the fine rate is still to be decided by the finance team; " * 6
        gapmod.add_gaps(p, items)
        self.set_owner(p, *OWNER)
        pk = gapmod.next_gaps(p, 1, gap_id="GAP-001")["gaps"][0]
        self.assertFalse(pk["present"]["inline"])
        self.assertIn("…", pk["present"]["dialog"]["question"])
        self.assertIn(items[0]["evidence"].strip(), pk["present"]["card"])

    def test_suggestion_is_an_option_description_with_its_source(self):
        p = self.with_requirements()
        items = self.gap_items()
        items[1]["suggested_answer"] = {"answer": "Under 3 seconds on 4G", "source": "BRD 5 and REQ-008"}
        gapmod.add_gaps(p, items)
        self.set_owner(p, *OWNER)
        pk = gapmod.next_gaps(p, 1, gap_id="GAP-002")["gaps"][0]
        first = pk["present"]["dialog"]["options"][0]
        self.assertEqual(first["label"], "Suggested Answer")
        self.assertIn("Under 3 seconds on 4G", first["description"])
        self.assertIn("BRD 5 and REQ-008", first["description"])

    def test_triage_summary_is_one_line_and_rows_are_sparse(self):
        p = self.seeded()
        out = gapmod.triage(p)
        self.assertNotIn("\n", out["summary"])
        self.assertIn("3 gap(s)", out["summary"])
        self.assertIn("GAP-001 (HIGH), GAP-003 (HIGH), GAP-002 (MEDIUM)", out["summary"])
        raw, js = ba(p.root, "gaps", "triage")
        row = js["gaps"][0]
        for absent in ("carried_forward", "material" if row["gap_id"] != "GAP-001" else "x", "due_for_revisit", "skipped_count", "question"):
            self.assertNotIn(absent, row)


class CompactOutputTests(BATest):
    def runTest(self):
        pass

    def test_json_is_compact_and_empty_values_are_omitted(self):
        p = self.with_gaps()
        raw, js = ba(p.root, "gaps", "list")
        self.assertEqual(raw.strip().count("\n"), 0, "one line of compact JSON")
        self.assertNotIn(": ", raw)
        raw, js = ba(p.root, "owner", "set", "--name", "Priya Nair", "--role", "Product Owner")
        self.assertNotIn("null", raw)
        self.assertNotIn("{}", raw)

    def test_pretty_flag_restores_indentation(self):
        p = self.with_gaps()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["--root", str(p.root), "--pretty", "gaps", "list"])
        self.assertIn('\n  "gaps"', buf.getvalue())

    def test_collections_callers_rely_on_survive_pruning(self):
        p = self.with_gaps()
        raw, js = ba(p.root, "gaps", "list", "--status", "NOPE")
        self.assertEqual(js["gaps"], [])
        self.assertEqual(cli.prune({"a": None, "b": [], "c": {}, "d": "", "e": 0, "f": False, "gaps": [], "candidate_conflicts": []}, True),
                         {"e": 0, "f": False, "gaps": [], "candidate_conflicts": []})

    def test_receipt_lists_only_the_folders_the_command_changed(self):
        p = self.with_gaps()
        raw, js = ba(p.root, "owner", "set", "--name", "Priya Nair", "--role", "Product Owner")
        self.assertEqual(set(js["files_saved"]), {"gaps/open"}, "only the gap files were rewritten (owner recorded on them)")
        raw, js = ba(p.root, "gaps", "list")
        self.assertNotIn("files_saved", js, "read-only commands report nothing")
        ba(p.root, "gaps", "next", "--id", "GAP-003")
        ba(p.root, "gap", "answer", "GAP-003", "--response", "Block the borrow and show a limit-reached message.")
        raw, js = ba(p.root, "gap", "resolve", "GAP-003", "--answer-id", "A1", "--interpretation", "A 6th borrow is rejected.",
                     "--closed-by-name", "Priya Nair", "--closed-by-role", "Product Owner", "--closure-summary", "Clear answer.",
                     "--confirmation", "Yes, resolved.", "--reviewed", "REQ-003")
        self.assertTrue(js["ok"], js)
        self.assertEqual(set(js["files_saved"]), {"gaps/open", "gaps/resolved", "decisions", "atomic-requirements"}, "only what this command wrote (the gap moved from open to resolved; the requirement now links its decision)")
        self.assertNotIn("extracted", js["files_saved"])
        self.assertNotIn("json_folder", js["files_saved"]["gaps/resolved"])
        self.assertTrue(js["files_saved"]["decisions"]["md_folder"].endswith("md"))
        full = Project.open(p.root).receipt()
        self.assertIn("atomic-requirements", full)
        self.assertIn("json_folder", full["gaps/resolved"], "`files` still returns the complete receipt")


class BrdBatchingTests(BATest):
    def runTest(self):
        pass

    def test_small_brd_is_read_in_the_main_session(self):
        p = self.new_project()
        out = ingest.outline_batches(p.load_source())
        self.assertFalse(out["delegate"])
        self.assertEqual(len(out["batches"]), 1)
        self.assertEqual(sum(s["chars"] for s in out["outline"]), out["total_chars"])

    def test_large_brd_is_split_into_bounded_batches_for_helper_agents(self):
        sections = "\n\n".join(f"## {i}. Area {i}\n\n" + ("The system shall support capability %d with its documented limits. " % i) * 60
                               for i in range(1, 25))
        p = self.new_project(brd=self.brd_copy("big.md", "# Big BRD\n\n" + sections))
        out = ingest.outline_batches(p.load_source())
        self.assertTrue(out["delegate"])
        self.assertGreater(len(out["batches"]), 1)
        sizes = {s["id"]: s["chars"] for s in out["outline"]}
        for batch in out["batches"]:
            self.assertTrue(len(batch) == 1 or sum(sizes[i] for i in batch) <= ingest.BATCH_CHARS, batch)
        self.assertEqual([i for b in out["batches"] for i in b], [s["id"] for s in out["outline"]], "every section, in order, once")
        raw, js = ba(p.root, "source", "outline")
        self.assertTrue(js["delegate"])

    def test_delegation_threshold_and_batch_size_are_overridable(self):
        p = self.new_project()
        meta = p.load_source()
        self.assertTrue(ingest.outline_batches(meta, delegate_above=100)["delegate"])
        self.assertGreater(len(ingest.outline_batches(meta, batch_chars=150)["batches"]), 1)
        raw, js = ba(p.root, "source", "outline", "--delegate-above", "100")
        self.assertTrue(js["delegate"])
        with mock.patch.dict(os.environ, {"BA_DELEGATE_ABOVE": "100"}):
            self.assertTrue(ingest.outline_batches(meta)["delegate"])
        self.assertFalse(ingest.outline_batches(meta)["delegate"], "default threshold is well above a small BRD")
        self.assertGreaterEqual(ingest.DELEGATE_ABOVE, 60000)


class PromptBudgetTests(Flow):
    """Byte budgets: raising one is a deliberate decision, not drift."""

    def test_skill_files_are_lean_and_descriptions_short(self):
        for f in SKILLS.glob("*/SKILL.md"):
            text = f.read_text(encoding="utf-8")
            desc = re.search(r"^description: (.*)$", text, re.M).group(1)
            self.assertLessEqual(len(desc), 260, f"{f.parent.name}: description is loaded for every session")
            self.assertLessEqual(len(text), 7000, f"{f.parent.name}: move detail into references/")

    def test_frontmatter_stays_valid_yaml(self):
        """An unquoted `: ` in a description breaks YAML parsing and the host silently drops the skill's or agent's metadata."""
        bad = []
        for f in list(PLUGIN.glob("skills/*/SKILL.md")) + list(PLUGIN.glob("agents/*.md")) + list(PLUGIN.glob("commands/*.md")):
            head = f.read_text(encoding="utf-8").split("---")[1]
            for line in head.strip().splitlines():
                key, _, value = line.partition(": ")
                value = value.strip()
                quoted = value[:1] in "\"'"
                if not quoted and (": " in value or " #" in value or value[:1] in "`[{&*!|>%@"):
                    bad.append(f"{f.relative_to(PLUGIN)}: {key}")
        self.assertEqual(bad, [])

    def test_a_gap_session_loads_little_up_front(self):
        up_front = len((STAGES.parent.parent / "SKILL.md").read_text(encoding="utf-8")) + len((STAGES / "gap-resolution.md").read_text(encoding="utf-8"))
        self.assertLessEqual(up_front, 11200, "router + gap loop (was ~28 KB before the split)")
        for ref in (STAGES / "gap-resolution").glob("*.md"):
            self.assertLessEqual(len(ref.read_text(encoding="utf-8")), 4800, ref.name)

    def test_a_story_session_loads_little_up_front(self):
        up_front = len((STAGES.parent.parent / "SKILL.md").read_text(encoding="utf-8")) + len((STAGES / "story-review.md").read_text(encoding="utf-8"))
        self.assertLessEqual(up_front, 11100)

    def test_only_the_router_is_a_listed_skill_and_stage_files_carry_no_frontmatter(self):
        """T5: ten stage skills used to be advertised in every session; now one skill lists, and stage files load on demand."""
        self.assertEqual([f.parent.name for f in SKILLS.glob("*/SKILL.md")], ["ba-workflow"])
        for f in STAGES.rglob("*.md"):
            self.assertFalse(f.read_text(encoding="utf-8").startswith("---"), f.name)

    def test_commands_stay_short(self):
        for f in (PLUGIN / "commands").glob("*.md"):
            self.assertLessEqual(len(f.read_text(encoding="utf-8")), 3600, f.name)

    def test_every_referenced_file_exists(self):
        """The skills were split into on-demand references: a dangling pointer would silently drop rules."""
        missing = []
        for f in list(PLUGIN.glob("skills/**/*.md")) + list(PLUGIN.glob("commands/*.md")) + list(PLUGIN.glob("agents/*.md")):
            for ref in re.findall(r"`((?:\.\./|references/|[a-z-]+/references/)[\w./-]+\.md)`", f.read_text(encoding="utf-8")):
                relative_to_file = (f.parent / ref).resolve()
                relative_to_skills = (SKILLS / ref).resolve()
                relative_to_workflow = (SKILLS / "ba-workflow" / ref).resolve()
                if not relative_to_file.exists() and not relative_to_skills.exists() and not relative_to_workflow.exists():
                    missing.append(f"{f.relative_to(PLUGIN)} -> {ref}")
            for ref in re.findall(r"`((?:stages/)?(?:gap-resolution|story-review|gap-analysis)/[\w-]+\.md)`", f.read_text(encoding="utf-8")):  # sibling pointers inside stage files
                if not any((base / ref).exists() for base in (f.parent, STAGES, STAGES.parent)):
                    missing.append(f"{f.relative_to(PLUGIN)} -> {ref}")
        self.assertEqual(missing, [])

    def test_cli_results_a_model_reads_stay_small(self):
        p = self.after_gap_triage()
        basemod.create(p)
        basemod.confirm(p, 2, *OWNER, "I approve baseline v2.")
        budgets = [(("story", "triage"), 1600), (("story", "next", "--id", "US-003"), 2300), (("gaps", "triage"), 700), (("status",), 2200)]
        for argv, limit in budgets:
            raw, _ = ba(p.root, *argv)
            self.assertLessEqual(len(raw.encode("utf-8")), limit, f"{' '.join(argv)}: {len(raw.encode('utf-8'))} bytes")


class GapPacketBudgetTests(BATest):
    def runTest(self):
        pass

    def test_default_gap_packet_stays_small_and_fallback_is_opt_in(self):
        p = self.with_gaps()
        self.set_owner(p, *OWNER)
        raw, js = ba(p.root, "gaps", "next", "--id", "GAP-003")
        self.assertLessEqual(len(raw.encode("utf-8")), 1274, len(raw.encode("utf-8")))  # was 1592 (-20% or better)
        self.assertNotIn("fallback", js["gaps"][0]["present"])
        full, js = ba(p.root, "gaps", "next", "--id", "GAP-003", "--fallback")
        self.assertIn("fallback", js["gaps"][0]["present"])
        self.assertGreaterEqual(len(full.encode("utf-8")) - len(raw.encode("utf-8")), 300, "the fallback is what was dropped")
