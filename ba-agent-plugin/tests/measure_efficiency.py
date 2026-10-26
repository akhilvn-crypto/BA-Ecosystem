"""Measure context/reprocessing efficiency of the deterministic core on the synthetic requirements fixture.

Prints JSON. Measures BYTES and COUNTS only - the core makes no model calls, so token counts are not available here; real token
usage must come from the host (see docs/TEST_REPORT.md for live-run numbers).

    python -m tests.measure_efficiency
"""
import contextlib
import io
import json
import re
import time

from tests.helpers import BATest, FIXTURE, PLUGIN, acs, changemod, checks, docsync, gapmod, key, reqindex
from ba_lib import cli


def size(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


class Measure(BATest):
    def runTest(self):
        pass

    def measure(self) -> dict:
        p = self.to_ac_stage()
        files = sorted(FIXTURE.glob("*.md"))
        all_bytes = sum(f.stat().st_size for f in files if f.name != "README.md")
        texts = reqindex.texts(p)
        sizes = [len(t.encode("utf-8")) for t in texts.values()]
        out = {"fixture": FIXTURE.name, "requirement_files": len(files) - 1, "requirement_bytes": all_bytes,
               "requirements": len(texts), "gaps": len(p.gaps())}
        out["per_requirement_bytes"] = {"mean": round(sum(sizes) / len(sizes), 1), "max": max(sizes)}
        out["req_show_vs_all_files"] = f"{max(sizes) / all_bytes:.0%} (largest single requirement) of all requirement text"

        reqmap = gapmod._reqmap(p)
        packet = [size(gapmod._packet(p, g, reqmap)) for g in p.gaps()]
        out["gap_packet_bytes_mean"] = round(sum(packet) / len(packet), 1)
        out["gap_packet_vs_all_requirement_text"] = f"{(sum(packet) / len(packet)) / all_bytes:.0%}"

        acs.add_acs(p, [self.ac_item(r) for r in ("LIB-001", "LIB-002", "LIB-003")])
        view = acs.next_ac(p, key("LIB-001"))["acs"][0]
        out["ac_card_bytes"] = size(view["present"])
        for r in ("LIB-001", "LIB-002", "LIB-003"):
            self.approve(p, r)

        t = time.perf_counter()
        again = docsync.refresh(p)
        out["render_unchanged"] = {"files_written": len(again["written"]), "ms": round((time.perf_counter() - t) * 1000, 1)}
        enriched = sum(f.stat().st_size for f in p.enriched_root.rglob("*.md"))
        out["enriched_bytes"] = enriched
        out["enriched_overhead_vs_source"] = f"{(enriched - all_bytes) / all_bytes:.0%}"

        # incremental re-analysis after an upstream edit: only the touched requirement is reassessed
        f = self.src / "membership.md"
        f.write_bytes(f.read_bytes().replace(b"within 14 days", b"within 21 days"))
        t = time.perf_counter()
        ch = changemod.check(p, False)
        imp = ch["changes"][0]["impact"]
        out["source_change"] = {"requirements_total": len(texts), "requirements_to_reassess": len(imp["affected_requirements"]),
                                "reassessed_fraction": f"{len(imp['affected_requirements']) / len(texts):.0%}",
                                "ms": round((time.perf_counter() - t) * 1000, 1)}
        out["prompt_surface_bytes"] = self.prompt_surface()
        out["cli_output_bytes"] = self.cli_outputs()
        out["model_calls_made_by_core"] = 0
        out["artifact_bytes_total"] = sum(x.stat().st_size for x in p.root.rglob("*") if x.is_file())
        return out

    @staticmethod
    def prompt_surface() -> dict:
        """Bytes of instructions a model loads (skills and references are read only when their branch is reached)."""
        def size_of(*paths):
            return sum(x.stat().st_size for x in paths if x.exists())
        stages = PLUGIN / "skills" / "ba-workflow" / "references" / "stages"
        descs = sum(len(m.group(1)) for f in PLUGIN.glob("skills/*/SKILL.md") if (m := re.search(r"^description: (.*)$", f.read_text(encoding="utf-8"), re.M)))
        out = {"skill_descriptions_always_loaded_chars": descs, "router": size_of(PLUGIN / "skills" / "ba-workflow" / "SKILL.md")}
        for f in sorted(stages.glob("*.md")):
            out[f"stage:{f.stem}"] = size_of(f)
        out["commands"] = {f.stem: size_of(f) for f in sorted((PLUGIN / "commands").glob("*.md"))}
        return out

    def cli_outputs(self) -> dict:
        """Bytes of the CLI results a model reads while working one gap and one requirement's criteria through the real CLI."""
        root = self.tmp / "cli"

        def ba(*argv):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli.main(["--root", str(root), *argv])
            text = buf.getvalue()
            return len(text.encode("utf-8")), json.loads(text) if text.lstrip().startswith("{") else text

        sizes = {}
        src = self.reqs_copy("cli-src")
        sizes["init"], _ = ba("init", "--requirements", str(src))
        gf = self.tmp / "g.json"
        gf.write_text(json.dumps(self.gap_items()[:1]), encoding="utf-8")
        sizes["gaps add"], _ = ba("gaps", "add", "--file", str(gf))
        ba("owner", "set", "--name", "Priya Nair", "--role", "Product Owner")
        sizes["gaps next"], nxt = ba("gaps", "next")
        sizes["gap answer"], ans = ba("gap", "answer", "GAP-001", "--response", "R10 per day, capped at R200.", "--respondent-name", "Priya Nair",
                                      "--respondent-role", "Product Owner")
        sizes["gap resolve"], _ = ba("gap", "resolve", "GAP-001", "--answer-id", "A1", "--interpretation", "Fine is R10 per day, maximum R200 per loan.",
                                     "--reviewed", ",".join(c["artifact_id"] for c in ans.get("candidate_conflicts", [])))
        ba("gaps", "commit", "--cross-pass-done")
        sizes["req list"], _ = ba("req", "list")
        sizes["req show"], _ = ba("req", "show", "LIB-006")
        af = self.tmp / "a.json"
        af.write_text(json.dumps([self.ac_item("LIB-006")]), encoding="utf-8")
        sizes["ac add"], _ = ba("ac", "add", "--file", str(af))
        sizes["ac next"], _ = ba("ac", "next")
        sizes["ac approve"], _ = ba("ac", "approve", "LIB-006", "--version", "1", "--statement", "Approved.")
        sizes["changes check (nothing changed)"], _ = ba("changes", "check")
        sizes["doc check"], _ = ba("doc", "check")
        sizes["validate"], _ = ba("validate")
        sizes["status (text)"], _ = ba("status")
        return sizes


if __name__ == "__main__":
    m = Measure()
    m.setUp()
    try:
        print(json.dumps(m.measure(), indent=2, ensure_ascii=False))
    finally:
        m._cleanup()
