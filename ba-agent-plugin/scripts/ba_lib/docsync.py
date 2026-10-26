"""The enriched copies: generate them from the accepted source, the approved acceptance criteria and the gap state.

`enriched/<same relative path as the source>` is a pure function of (accepted source snapshot, approved AC records, gap register):
the source text byte for byte, a generated header line, and one fenced BA block per requirement that has something to show.
The supplied source is never opened for writing. Because the copy can always be regenerated, nothing here needs a write journal.

A copy that was changed by hand is never overwritten silently: `refresh` leaves it alone and reports DRIFT until `changes check`
has recorded what was edited (and kept the edit), after which the next render restores the generated content.
"""
from __future__ import annotations

from . import docmodel, gaps as gapmod, reqindex, snapshots
from .util import BAError, ist_now_iso, safe_join, sha256_bytes

MANIFEST = "state/enriched-manifest.json"


def manifest(p) -> dict:
    return p.read_json(MANIFEST) if p.exists(MANIFEST) else {"schema_version": "2.0", "files": {}}


# --------------------------------------------------------------- the block
def _status_text(g: dict) -> str:
    if g["status"] == "OPEN" and g.get("carry_forward"):
        return "OPEN (carried forward)"
    return {"DEFERRED": "DEFERRED", "REOPENED": "REOPENED", "BLOCKED": "BLOCKED"}.get(g["status"], "OPEN")


def _when(iso: str | None) -> str:
    return (iso or "")[:16].replace("T", " ")


def requirement_status(p, key: str, blocked: dict | None = None, open_nonblocking: dict | None = None) -> str:
    """READY | READY_WITH_OPEN_GAPS | BLOCKED - computed live from the gaps, never stored."""
    blocked = gapmod.blocked_requirements(p) if blocked is None else blocked
    if key in blocked:
        return "BLOCKED"
    open_nb = gapmod.open_nonblocking_requirements(p) if open_nonblocking is None else open_nonblocking
    return "READY_WITH_OPEN_GAPS" if key in open_nb else "READY"


def context(p) -> dict:
    gaps_by_key: dict[str, list[dict]] = {}
    for g in p.gaps():
        if g["status"] != "RESOLVED":
            for k in g["impacted_req_keys"]:
                gaps_by_key.setdefault(k, []).append(g)
    approved = {a["req_key"]: a for b, a in p.all_acs() if b == "approved"}
    return {"gaps": gaps_by_key, "approved": approved, "blocked": gapmod.blocked_requirements(p)}


def block_body(entry: dict, ctx: dict) -> tuple[list[str], int | None] | None:
    """The lines between the BA markers for one requirement, and the AC version shown (None when no approved criteria)."""
    key = entry["req_key"]
    ac = ctx["approved"].get(key)
    gaps = sorted(ctx["gaps"].get(key, []), key=lambda g: g["gap_id"])
    blockers = ctx["blocked"].get(key, [])
    if not (ac or gaps):
        return None
    h = "#" * (min((entry.get("level") or 3) + 1, 6))
    out = [f"{h} Acceptance Criteria"]
    if ac:
        ap = ac.get("approval") or {}
        who = f"{ap['approver_name']} ({ap['approver_role']})" if ap.get("approver_name") and ap.get("approver_role") else \
            (ap.get("approver_name") or "approver not recorded")
        out.append(f"*Approved by {docmodel.safe(who)}, {_when(ap.get('at_ist'))} IST · AC v{ac['version']}*")
        if ac["decision_ids"]:
            out.append(f"*Decisions applied: {', '.join(ac['decision_ids'])}*")
        if blockers:
            out.append(f"*Blocking gap(s) open ({', '.join(blockers)}): treat these criteria as provisional until they are resolved.*")
        ir = ac.get("impact_review") or {}
        if ir.get("pending"):
            out.append(f"*Under review: {docmodel.safe(ir.get('reason'), 160)}*")
        out += [f"{i}. {docmodel.safe(c)}" for i, c in enumerate(ac["acceptance_criteria"], 1)]
    elif blockers:
        out.append("*Acceptance criteria withheld until blocking gaps are resolved.*")
    else:
        out.append("*Acceptance criteria not yet approved.*")
    if gaps:
        impact = {r["gap_id"]: r["potential_impact"] for r in (ac or {}).get("open_gap_refs", [])}
        out += ["", f"{h} Open Gaps", "| Gap | Priority | Status | Question | Potential impact |", "|---|---|---|---|---|"]
        for g in gaps:
            out.append(f"| {g['gap_id']} | {g['priority']} | {_status_text(g)} | {docmodel.cell(g['question'])} | "
                       f"{docmodel.cell(impact.get(g['gap_id']) or g['why_it_matters'])} |")
    if ac:
        readiness = ac["readiness"]
    else:
        readiness = "NOT_READY" if blockers else "AC_PENDING"
    out += ["", f"**Readiness:** {readiness}"]
    return out, (ac["version"] if ac else None)


# ------------------------------------------------------------ one file
def build_file(p, rel: str, ctx: dict) -> dict:
    """The expected enriched text of one source file: {text, source_text, blocks {key: body lines}, ac_versions {key: n}}."""
    meta = reqindex.load(p)["files"][rel]
    source_text = snapshots.accepted_text(p, rel)
    if source_text is None or snapshots.latest(p, rel)["file_hash"] != meta["file_hash"]:
        raise BAError("SNAPSHOT_MISSING", f"The accepted copy of {rel} is missing or damaged; run `changes check`.", file=rel)
    blocks, bodies, versions = [], {}, {}
    if not meta.get("unclosed_fence"):
        for e in (x for x in reqindex.entries(p) if x["file"] == rel):
            got = block_body(e, ctx)
            if got:
                body, ver = got
                bodies[e["req_key"]], versions[e["req_key"]] = body, ver
                blocks.append((e["insert_after"], docmodel.wrap_block(e["req_key"], ver, body)))
    text = docmodel.render_enriched(source_text, blocks, rel)
    if docmodel.strip_blocks(text) != source_text:
        raise BAError("RENDER_INVARIANT_FAILED", f"Removing the BA blocks from the enriched copy of {rel} does not give the source text back; "
                      "the previous copy was kept.", file=rel)
    return {"text": text, "source_text": source_text, "blocks": bodies, "ac_versions": versions}


def expected(p) -> dict[str, dict]:
    ctx = context(p)
    out = {}
    for rel, f in reqindex.load(p)["files"].items():
        if f["status"] == "ACTIVE":
            out[rel] = build_file(p, rel, ctx)
    return out


def disk_path(p, rel: str):
    return safe_join(p.enriched_root, rel)


# ------------------------------------------------------------- driver
def refresh(p, force: bool = False, only: set[str] | None = None) -> dict:
    """Write the enriched copies that are out of date. A copy that differs from what was last written is DRIFT and is left
    alone unless `force`. Returns {written, drift, errors}."""
    man = manifest(p)
    ctx = context(p)
    files = reqindex.load(p)["files"]
    written, drift, errors = [], [], []
    dirty = False
    for rel, f in files.items():
        if f["status"] != "ACTIVE" or (only is not None and rel not in only):
            continue
        try:
            built = build_file(p, rel, ctx)
        except BAError as e:
            errors.append({"file": rel, "code": e.code, "message": e.message})
            continue
        data = built["text"].encode("utf-8")
        h = sha256_bytes(data)
        ent = man["files"].get(rel)
        target = disk_path(p, rel)
        disk = sha256_bytes(target.read_bytes()) if target.exists() else None
        entry = {"source_hash": f["file_hash"], "expected_hash": h, "blocks": {k: docmodel.body_hash(b) for k, b in built["blocks"].items()},
                 "ac_versions": built["ac_versions"]}
        if disk is not None and disk != h and not force and not (ent and disk == ent["expected_hash"]):
            drift.append(rel)  # somebody edited the copy (or it was never ours): leave it for `changes check`
            continue
        if disk != h:
            p.write_enriched(rel, data)
            written.append(rel)
        if not ent or {k: v for k, v in ent.items() if k != "rendered_at_ist"} != entry:
            man["files"][rel] = dict(entry, rendered_at_ist=ist_now_iso())
            dirty = True
    active = {r for r, f in files.items() if f["status"] == "ACTIVE"}
    if only is None:
        for rel in sorted(set(man["files"]) - active):
            p.remove_enriched(rel)
            p.vacated.add(f"enriched/{rel}")
            del man["files"][rel]
            dirty = True
    if dirty:
        p.write_json(MANIFEST, man)
    if written:
        p.audit("ENRICHED_RENDERED", files=written)
    return {"written": written, "drift": drift, "errors": errors}


def sync(p, key: str | None = None, force: bool = False) -> dict:
    """`doc sync`: re-render the enriched copies (all, or the file that holds `key`)."""
    only = None
    if key:
        only = {p.requirement(key)["file"]}
    out = refresh(p, force=force, only=only)
    if out["drift"] and not force:
        raise BAError("ENRICHED_DRIFT", f"{out['drift']} were changed by hand since they were generated. Run `changes check` to record "
                      "what was edited (nothing is lost: the copy is kept) and have it decided; the next render then restores the "
                      "generated content.", files=out["drift"])
    if out["errors"]:
        raise BAError(out["errors"][0]["code"], out["errors"][0]["message"], errors=out["errors"])
    return out


def check(p) -> dict:
    """`doc check` (read-only): is each enriched copy in step with the state, and does the render invariant hold?"""
    man = manifest(p)
    ctx = context(p)
    rows = []
    for rel, f in reqindex.load(p)["files"].items():
        if f["status"] != "ACTIVE":
            continue
        row = {"file": rel, "path": str(disk_path(p, rel))}
        try:
            built = build_file(p, rel, ctx)
        except BAError as e:
            rows.append(dict(row, state="ERROR", problem=e.message))
            continue
        h = sha256_bytes(built["text"].encode("utf-8"))
        target = disk_path(p, rel)
        ent = man["files"].get(rel)
        if not target.exists():
            state = "MISSING"
        else:
            disk = sha256_bytes(target.read_bytes())
            state = "IN_SYNC" if disk == h else ("STALE" if ent and disk == ent["expected_hash"] else "DRIFT")
        rows.append(dict(row, state=state, requirements_with_blocks=len(built["blocks"])))
    bad = [r for r in rows if r["state"] != "IN_SYNC"]
    return {"files": rows, "in_sync": not bad, "next": None if not bad else
            "STALE/MISSING: `doc sync` re-renders. DRIFT: a copy was edited by hand; run `changes check` to record the edit first."}
