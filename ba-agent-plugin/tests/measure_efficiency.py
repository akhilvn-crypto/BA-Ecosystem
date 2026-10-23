"""Measure context/reprocessing efficiency of the deterministic core on the synthetic fixtures.

Prints JSON. Measures BYTES and COUNTS only — the core makes no model calls, so token counts are not
available here; real token usage must come from the host (see docs/TEST_REPORT.md for live-run numbers).

    python -m tests.measure_efficiency
"""
import json
import time

from tests.helpers import BATest, FIXTURE, basemod, checks, gapmod, reqs, stories


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
        out["model_calls_made_by_core"] = 0
        out["artifact_bytes_total"] = sum(f.stat().st_size for f in p.root.rglob("*") if f.is_file())
        return out


if __name__ == "__main__":
    m = Measure()
    m.setUp()
    try:
        print(json.dumps(m.measure(), indent=2))
    finally:
        m._cleanup()
