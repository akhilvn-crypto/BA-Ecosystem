"""Measure context/reprocessing efficiency of the deterministic core on the synthetic fixtures.

Prints JSON. Measures BYTES and COUNTS only — the core makes no model calls, so token counts are not
available here; real token usage must come from the host (see docs/TEST_REPORT.md for live-run numbers).

    python -m tests.measure_efficiency
"""
import contextlib
import io
import json
import re
import time

from tests.helpers import PLUGIN, BATest, FIXTURE, basemod, checks, gapmod, reqs, stories
from ba_lib import cli


class Measure(BATest):
    def runTest(self):
        pass

    def measure(self) -> dict:
        full = len(FIXTURE.read_bytes())
        p = self.to_baseline()
        out = {"fixture": FIXTURE.name, "brd_bytes": full, "requirements": len(p.req_ids()), "gaps": len(p.gaps())}

        sec_bytes = [len(reqs.source_section(p, s["section_id"])["text"].encode("utf-8")) for s in p.source_metadata()["sections"]]
        out["per_section_bytes"] = {"mean": round(sum(sec_bytes) / len(sec_bytes), 1), "max": max(sec_bytes)}
        out["section_context_vs_full_brd"] = f"{max(sec_bytes) / full:.0%} (largest single section) of the full BRD"

        gaps = [g for g in p.gaps()]
        reqmap = {r["requirement_id"]: r for r in p.requirements()}
        packet = [len(json.dumps(gapmod._packet(p, g, reqmap)).encode("utf-8")) for g in gaps]
        all_reqs = sum(len(json.dumps(r).encode("utf-8")) for r in reqmap.values())
        out["gap_packet_bytes_mean"] = round(sum(packet) / len(packet), 1)
        out["gap_packet_vs_all_requirements"] = f"{(sum(packet) / len(packet)) / all_reqs:.0%}"

        t = time.perf_counter()
        again = basemod.create(p)
        out["baseline_recreate_unchanged"] = {"reused": again["reused"], "ms": round((time.perf_counter() - t) * 1000, 1)}

        # incremental re-analysis after a BRD edit
        brd = self.tmp / "inputs" / "brd.md"
        brd.write_bytes(brd.read_bytes().replace(b"within 14 days", b"within 21 days"))
        t = time.perf_counter()
        ch = reqs.source_check(p)
        out["source_change"] = {"requirements_total": len(p.req_ids()), "requirements_to_reassess": len(ch["affected_requirement_ids"]),
                                "reassessed_fraction": f"{len(ch['affected_requirement_ids']) / len(p.req_ids()):.0%}",
                                "sections_changed": len(ch["diff"]["changed"]), "sections_unchanged": len(ch["diff"]["unchanged"]),
                                "ms": round((time.perf_counter() - t) * 1000, 1)}
        out["prompt_surface_bytes"] = self.prompt_surface()
        out["cli_output_bytes"] = self.cli_outputs()
        out["model_calls_made_by_core"] = 0
        out["artifact_bytes_total"] = sum(f.stat().st_size for f in p.root.rglob("*") if f.is_file())
        return out


    @staticmethod
    def prompt_surface() -> dict:
        """Bytes of instructions a model loads per stage (skills and references are read only when their branch is reached)."""
        size = lambda *names: sum((PLUGIN / n).stat().st_size for n in names)
        sk = lambda n: "skills/ba-workflow/SKILL.md" if n == "ba-workflow" else f"skills/ba-workflow/references/stages/{n}.md"
        sub = lambda n, f: f"skills/ba-workflow/references/stages/{n}/{f}.md"
        descs = sum(len(re.search(r"^description: (.*)$", f.read_text(encoding="utf-8"), re.M).group(1)) for f in PLUGIN.glob("skills/*/SKILL.md"))
        return {
            "skill_descriptions_always_loaded_chars": descs,
            "gap_session": size(sk("ba-workflow"), sk("gap-resolution")),
            "gap_session_with_validate_and_actions": size(sk("ba-workflow"), sk("gap-resolution"), sub("gap-resolution", "validate-and-resolve"),
                                                           sub("gap-resolution", "actions")),
            "story_session": size(sk("ba-workflow"), sk("story-review")),
            "triage_gaps_command": size("commands/triage-gaps.md", sk("ba-workflow"), sk("gap-resolution"), sub("gap-resolution", "triage")),
            "triage_stories_command": size("commands/triage-stories.md", sk("ba-workflow"), sk("story-review"), sub("story-review", "triage")),
        }

    def cli_outputs(self) -> dict:
        """Bytes of the CLI results a model reads while working one gap and one story."""
        from tests.test_context.test_context import Flow
        f = Flow()
        f.setUp()
        try:
            p = f.after_gap_triage()
            sizes = {}
            for label, argv in (("gaps_triage", ("gaps", "triage")), ("story_triage", ("story", "triage")),
                                ("story_next", ("story", "next", "--id", "US-003")), ("baseline_create", ("baseline", "create")),
                                ("status", ("status",))):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    cli.main(["--root", str(p.root), *argv])
                sizes[label] = len(buf.getvalue().encode("utf-8"))
            return sizes
        finally:
            f._cleanup()


if __name__ == "__main__":
    m = Measure()
    m.setUp()
    try:
        print(json.dumps(m.measure(), indent=2))
    finally:
        m._cleanup()
