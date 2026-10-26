"""Context engineering: each stage reads only what it needs.

Covers the presentation layer (card + dialog built by the core), lean/compact CLI output, the triage-ac flow (a record flagged by
a resolved gap), requirement batching for helper agents, and byte budgets for the prompt files and the CLI results a model reads.
The budgets are regression guards: raise one only on purpose.
"""
import contextlib
import io
import json
import os
import re
from unittest import mock

from tests.helpers import PLUGIN, BATest, acs, gapmod, key, option_labels, reqindex
from ba_lib import cli, present
from ba_lib.project import Project

OWNER = ("Priya Nair", "Product Owner")
SKILLS = PLUGIN / "skills"
STAGES = SKILLS / "ba-workflow" / "references" / "stages"
K1, K6, K7, K3 = key("LIB-001"), key("LIB-006"), key("LIB-007"), key("LIB-003")


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
    """A gap resolved after criteria were written for it - the exact state `/ba-agent:triage-ac` starts from."""

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
        acs.add_acs(p, [
            self.ac_item("LIB-001"),
            self.ac_item("LIB-006"),
            self.ac_item("LIB-007", open_gap_refs=[
                dict(gap_id="GAP-002", potential_impact="The load-time target is unknown.", readiness_effect="NEEDS_CLARIFICATION")])])
        # what /ba-agent:triage-gaps does next: revisit the carried-forward gap and close it
        gapmod.revisit(p, "GAP-002")
        self.answer_and_resolve(p, "GAP-002", "Under 3 seconds on 4G.", "Mobile pages load in under 3 seconds on 4G.")
        return p


class TriageAcTests(Flow):
    def test_the_list_shows_every_pending_record_with_its_own_blockers(self):
        p = self.after_gap_triage()
        out = acs.triage(p)
        self.assertEqual(out["preflight"], [])
        self.assertEqual(out["total"], 3)
        self.assertEqual(sorted(out["eligible"]), sorted([K1, K6]), "records that need nothing else can be approved now")
        self.assertEqual(out["not_eligible"], [K7], "the record that named the resolved gap needs its update first")
        row = next(r for r in out["acs"] if r["req_key"] == K7)
        self.assertTrue(any("flagged for impact review" in b for b in row["blockers"]), row["blockers"])
        self.assertIn("3 record(s)", out["summary"])
        self.assertNotIn("\n", out["summary"])

    def test_a_record_flagged_by_a_resolved_gap_offers_update_and_carries_the_decision_to_apply(self):
        p = self.after_gap_triage()
        item = acs.next_ac(p, K7)["acs"][0]
        self.assertEqual(option_labels(item), ["Update criteria", "Defer", "Keep as is"])
        self.assertNotIn("No change needed", option_labels(item), "a resolved gap cannot be cleared as unaffected")
        self.assertFalse(item["eligible"])
        card = item["present"]["card"]
        self.assertIn("Decision to apply", card)
        self.assertIn("Mobile pages load in under 3 seconds on 4G.", card)
        self.assertIn("Pages must load quickly", card, "the requirement text is on the card: criteria are reviewed against it")
        self.assertIn(" of 3", item["present"]["dialog"]["question"])

    def test_a_record_with_nothing_wrong_can_be_approved_and_the_dialog_says_so(self):
        p = self.after_gap_triage()
        labels = option_labels(acs.next_ac(p, K1)["acs"][0])
        self.assertEqual(labels, ["Approve v1", "Defer", "Not approved"])

    def test_a_record_blocked_by_an_open_gap_offers_no_approve(self):
        p = self.after_gap_triage()
        gapmod.reopen(p, "GAP-001", "Finance changed the fine rule", *OWNER)
        item = acs.next_ac(p, K6)["acs"][0]
        labels = option_labels(item)
        self.assertFalse(any(x.startswith("Approve") for x in labels), labels)
        self.assertIn("open GAP-001", item["present"]["dialog"]["question"])

    def test_ac_next_returns_the_card_not_the_raw_record(self):
        p = self.after_gap_triage()
        item = acs.next_ac(p, K1)["acs"][0]
        self.assertNotIn("ac", item)
        self.assertNotIn("review_history", json.dumps(item))
        full = acs.next_ac(p, K1, full=True)["acs"][0]
        self.assertIn("review_history", full["ac"])
        self.assertIn("requirement_text", full["ac"])

    def test_dialogs_respect_the_tool_limits(self):
        p = self.after_gap_triage()
        for k in (K1, K6, K7):
            d = acs.next_ac(p, k)["acs"][0]["present"]["dialog"]
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
        for needle in ("HIGH", "blocks acceptance criteria", "GAP-003", "borrow a 6th book", "Why:", "Source:", "LIB-003"):
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
        self.assertEqual(gapmod.explain(p, "GAP-003")["impact_on_requirements"][0]["id"], "LIB-003")

    def test_history_moves_a_gap_from_inline_to_card(self):
        p = self.seeded()
        gapmod.next_gaps(p, 3)
        gapmod.record_answer(p, "GAP-002", "Fast enough")
        pk = gapmod.next_gaps(p, 1, gap_id="GAP-002")["gaps"][0]
        self.assertFalse(pk["present"]["inline"])
        self.assertIn("Earlier answer A1", pk["present"]["card"])
        self.assertIn("Fast enough", pk["present"]["card"])

    def test_a_long_gap_is_truncated_in_the_dialog_and_complete_on_the_card(self):
        p = self.new_project()
        items = self.gap_items()
        items[0]["evidence"] = "LIB-006: " + "the fine rate is still to be decided by the finance team; " * 6
        gapmod.add_gaps(p, items)
        self.set_owner(p, *OWNER)
        pk = gapmod.next_gaps(p, 1, gap_id="GAP-001")["gaps"][0]
        self.assertFalse(pk["present"]["inline"])
        self.assertIn("…", pk["present"]["dialog"]["question"])
        self.assertIn(items[0]["evidence"].strip(), pk["present"]["card"])
        self.assertIn("The fine rate is to be decided.", pk["present"]["card"], "the card quotes the requirement text it is about")

    def test_suggestion_is_an_option_description_with_its_source(self):
        p = self.new_project()
        items = self.gap_items()
        items[1]["suggested_answer"] = {"answer": "Under 3 seconds on 4G", "source": "the mobile requirement LIB-007"}
        gapmod.add_gaps(p, items)
        self.set_owner(p, *OWNER)
        pk = gapmod.next_gaps(p, 1, gap_id="GAP-002")["gaps"][0]
        first = pk["present"]["dialog"]["options"][0]
        self.assertEqual(first["label"], "Suggested Answer")
        self.assertIn("Under 3 seconds on 4G", first["description"])
        self.assertIn("LIB-007", first["description"])

    def test_triage_summary_is_one_line_and_rows_are_sparse(self):
        p = self.seeded()
        out = gapmod.triage(p)
        self.assertNotIn("\n", out["summary"])
        self.assertIn("3 gap(s)", out["summary"])
        self.assertIn("GAP-001 (HIGH), GAP-003 (HIGH), GAP-002 (MEDIUM)", out["summary"])
        raw, js = ba(p.root, "gaps", "triage")
        row = js["gaps"][0]
        for absent in ("carried_forward", "due_for_revisit", "skipped_count", "question"):
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
        self.assertEqual(cli.prune({"a": None, "b": [], "c": {}, "d": "", "e": 0, "f": False, "gaps": [], "acs": [], "candidate_conflicts": []}, True),
                         {"e": 0, "f": False, "gaps": [], "acs": [], "candidate_conflicts": []})

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
                     "--confirmation", "Yes, resolved.", "--reviewed", "LIB-003,LIB-004")
        self.assertTrue(js["ok"], js)
        self.assertEqual(set(js["files_saved"]), {"gaps/open", "gaps/resolved", "decisions", "enriched"},
                         "only what this command wrote (the gap moved from open to resolved; the open-gaps table in the enriched copy changed)")
        self.assertNotIn("snapshots", js["files_saved"])
        self.assertNotIn("json_folder", js["files_saved"]["gaps/resolved"])
        self.assertTrue(js["files_saved"]["decisions"]["md_folder"].endswith("md"))
        full = Project.open(p.root).receipt()
        self.assertIn("enriched", full)
        self.assertIn("json_folder", full["gaps/resolved"], "`files` still returns the complete receipt")


class RequirementBatchingTests(BATest):
    def runTest(self):
        pass

    def test_a_small_set_is_read_in_the_main_session(self):
        p = self.new_project()
        out = reqindex.outline_batches(p)
        self.assertFalse(out["delegate"])
        self.assertEqual(len(out["batches"]), 1)
        self.assertEqual(sum(r["chars"] for r in out["requirements"]), out["total_chars"])

    def _big(self):
        sections = "\n\n".join(f"### BIG-{i:03d} Capability {i}\n" + ("The system shall support capability %d with its documented limits. " % i) * 60
                               for i in range(1, 25))
        folder = self.tmp / "inputs" / "big"
        self.write_req(folder, "big.md", "# Big requirement set\n\n" + sections + "\n")
        return self.new_project(src=folder)

    def test_a_large_set_is_split_into_bounded_batches_for_helper_agents(self):
        p = self._big()
        out = reqindex.outline_batches(p)
        self.assertTrue(out["delegate"])
        self.assertGreater(len(out["batches"]), 1)
        sizes = {r["key"]: r["chars"] for r in out["requirements"]}
        for batch in out["batches"]:
            self.assertTrue(len(batch) == 1 or sum(sizes[k] for k in batch) <= reqindex.BATCH_CHARS, batch)
        self.assertEqual([k for b in out["batches"] for k in b], [r["key"] for r in out["requirements"]], "every requirement, in order, once")
        raw, js = ba(p.root, "source", "index")
        self.assertTrue(js["delegate"])

    def test_delegation_threshold_and_batch_size_are_overridable(self):
        p = self.new_project()
        self.assertTrue(reqindex.outline_batches(p, delegate_above=100)["delegate"])
        self.assertGreater(len(reqindex.outline_batches(p, batch_chars=150)["batches"]), 1)
        raw, js = ba(p.root, "source", "index", "--delegate-above", "100")
        self.assertTrue(js["delegate"])
        with mock.patch.dict(os.environ, {"BA_DELEGATE_ABOVE": "100"}):
            self.assertTrue(reqindex.outline_batches(p)["delegate"])
        self.assertFalse(reqindex.outline_batches(p)["delegate"], "default threshold is well above a small requirement set")
        self.assertGreaterEqual(reqindex.DELEGATE_ABOVE, 60000)

    def test_req_show_returns_one_requirement_only(self):
        p = self.new_project()
        raw, js = ba(p.root, "req", "show", "LIB-006")
        self.assertIn("The fine rate is to be decided.", js["text"])
        self.assertNotIn("LIB-005", raw, "no other requirement's text comes along")
        self.assertLessEqual(len(raw.encode("utf-8")), 900, len(raw.encode("utf-8")))


class PromptBudgetTests(Flow):
    """Byte budgets: raising one is a deliberate decision, not drift."""

    def test_skill_files_are_lean_and_descriptions_short(self):
        for f in SKILLS.glob("*/SKILL.md"):
            text = f.read_text(encoding="utf-8")
            desc = re.search(r"^description: (.*)$", text, re.M).group(1)
            self.assertLessEqual(len(desc), 260, f"{f.parent.name}: description is loaded for every session")
            self.assertLessEqual(len(text), 5000, f"{f.parent.name}: move detail into references/")

    def test_frontmatter_stays_valid_yaml(self):
        """An unquoted `: ` in a description breaks YAML parsing and the host silently drops the skill's or agent's metadata."""
        bad = []
        for f in list(PLUGIN.glob("skills/*/SKILL.md")) + list(PLUGIN.glob("agents/*.md")) + list(PLUGIN.glob("commands/*.md")):
            head = f.read_text(encoding="utf-8").split("---")[1]
            for line in head.strip().splitlines():
                key_, _, value = line.partition(": ")
                value = value.strip()
                quoted = value[:1] in "\"'"
                if not quoted and (": " in value or " #" in value or value[:1] in "`[{&*!|>%@"):
                    bad.append(f"{f.relative_to(PLUGIN)}: {key_}")
        self.assertEqual(bad, [])

    def test_a_gap_session_loads_little_up_front(self):
        up_front = len((STAGES.parent.parent / "SKILL.md").read_text(encoding="utf-8")) + len((STAGES / "gap-resolution.md").read_text(encoding="utf-8"))
        self.assertLessEqual(up_front, UP_FRONT_GAP, "router + gap loop")
        for ref in (STAGES / "gap-resolution").glob("*.md"):
            self.assertLessEqual(len(ref.read_text(encoding="utf-8")), 4800, ref.name)

    def test_a_criteria_session_loads_little_up_front(self):
        up_front = len((STAGES.parent.parent / "SKILL.md").read_text(encoding="utf-8")) + len((STAGES / "ac-review.md").read_text(encoding="utf-8"))
        self.assertLessEqual(up_front, UP_FRONT_AC, "router + criteria review loop")

    def test_only_the_router_is_a_listed_skill_and_stage_files_carry_no_frontmatter(self):
        """Ten stage skills used to be advertised in every session; now one skill lists, and stage files load on demand."""
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
            text = f.read_text(encoding="utf-8")
            for ref in re.findall(r"`((?:\.\./|references/|[a-z-]+/references/)[\w./-]+\.md)`", text):
                relative_to_file = (f.parent / ref).resolve()
                relative_to_skills = (SKILLS / ref).resolve()
                relative_to_workflow = (SKILLS / "ba-workflow" / ref).resolve()
                if not relative_to_file.exists() and not relative_to_skills.exists() and not relative_to_workflow.exists():
                    missing.append(f"{f.relative_to(PLUGIN)} -> {ref}")
            for ref in re.findall(r"`((?:stages/)?(?:gap-resolution|ac-review|gap-analysis)/[\w-]+\.md)`", text):  # sibling pointers inside stage files
                if not any((base / ref).exists() for base in (f.parent, STAGES, STAGES.parent)):
                    missing.append(f"{f.relative_to(PLUGIN)} -> {ref}")
        self.assertEqual(missing, [])

    def test_every_stage_file_the_router_names_exists_and_every_stage_file_is_routed(self):
        router = (SKILLS / "ba-workflow" / "SKILL.md").read_text(encoding="utf-8")
        named = set(re.findall(r"`(stages/[\w/-]+\.md)`", router))
        for n in named:
            self.assertTrue((STAGES.parent / n).exists(), n)
        all_files = {f.relative_to(STAGES.parent).as_posix() for f in STAGES.rglob("*.md")}
        # taxonomy and the gap-resolution branch files are reached from their stage file, the rest from the router
        reached_elsewhere = {f for f in all_files if f.startswith(("stages/gap-resolution/", "stages/gap-analysis/"))}
        self.assertEqual(sorted(all_files - named - reached_elsewhere), [], "a stage guide nothing points to")

    def test_no_1x_vocabulary_survives_in_the_prompt_surface(self):
        """BRD, baseline, atomic requirements and user stories are gone; a stray mention would send the model down a path that no longer exists."""
        banned = [r"\bBRD\b", r"baseline", r"user stor", r"\bstory\b", r"\bstories\b", r"atomic requirement", r"source section", r"\bconvert\b",
                  r"triage-stories", r"requirement-analyst", r"story-builder", r"requirements-baseline", r"user-story-generation", r"brd-ingestion"]
        hits = []
        for f in list(PLUGIN.glob("skills/**/*.md")) + list(PLUGIN.glob("commands/*.md")) + list(PLUGIN.glob("agents/*.md")):
            text = f.read_text(encoding="utf-8")
            for pat in banned:
                m = re.search(pat, text, re.I)
                if m:
                    hits.append(f"{f.relative_to(PLUGIN)}: {m.group(0)}")
        self.assertEqual(hits, [])

    def test_the_removed_files_are_gone_and_the_new_ones_exist(self):
        for gone in ("commands/triage-stories.md", "agents/requirement-analyst.md", "agents/story-builder.md",
                     "skills/ba-workflow/references/stages/brd-ingestion.md", "skills/ba-workflow/references/stages/atomic-requirements.md",
                     "skills/ba-workflow/references/stages/requirements-baseline.md", "skills/ba-workflow/references/stages/user-story-generation.md",
                     "skills/ba-workflow/references/stages/story-review.md"):
            self.assertFalse((PLUGIN / gone).exists(), gone)
        for new in ("commands/triage-ac.md", "agents/ac-writer.md", "skills/ba-workflow/references/stages/requirements-intake.md",
                    "skills/ba-workflow/references/stages/acceptance-criteria.md", "skills/ba-workflow/references/stages/ac-review.md",
                    "skills/ba-workflow/references/stages/ac-review/triage.md"):
            self.assertTrue((PLUGIN / new).exists(), new)

    def test_the_router_states_the_read_only_source_and_generated_copy_rules(self):
        router = (SKILLS / "ba-workflow" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("read-only source", router)
        self.assertIn("enriched/", router)
        self.assertIn("never hand-edited", router)

    def test_the_start_command_asks_where_the_requirements_are(self):
        start = (PLUGIN / "commands" / "start.md").read_text(encoding="utf-8")
        self.assertIn("Where are the requirements", start)
        self.assertLess(start.index("Where are the requirements"), start.index("`init`"))
        self.assertIn("ba-output", start)

    def test_cli_results_a_model_reads_stay_small(self):
        p = self.after_gap_triage()
        budgets = [(("ac", "triage"), BUDGET_AC_TRIAGE), (("ac", "next", "--key", K7), BUDGET_AC_NEXT), (("gaps", "triage"), 400),
                   (("status",), BUDGET_STATUS)]
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
        self.assertLessEqual(len(raw.encode("utf-8")), BUDGET_GAP_PACKET, len(raw.encode("utf-8")))
        self.assertNotIn("fallback", js["gaps"][0]["present"])
        full, js = ba(p.root, "gaps", "next", "--id", "GAP-003", "--fallback")
        self.assertIn("fallback", js["gaps"][0]["present"])
        self.assertGreaterEqual(len(full.encode("utf-8")) - len(raw.encode("utf-8")), 300, "the fallback is what was dropped")


UP_FRONT_GAP = 9200       # SKILL.md + gap-resolution.md: 8.9 KB at 2.0.0 (11.3 KB in 1.10.0)
UP_FRONT_AC = 9500        # SKILL.md + ac-review.md: 9.3 KB at 2.0.0
BUDGET_AC_TRIAGE = 1250   # measured 1,068 bytes on the three-record fixture
BUDGET_AC_NEXT = 2150     # measured 1,858 bytes
BUDGET_STATUS = 1300      # measured 1,100 bytes
BUDGET_GAP_PACKET = 1350  # measured 1,166 bytes
