"""Command-line interface: `python ba.py [--root DIR] <group> <action> [options]`.

Every command prints one JSON object (`ok: true|false`) except `status`/`report`, which print text.
For free-text values containing quotes/newlines, put them in a JSON file and pass `--args-file FILE`
(keys = option names with `_` or `-`); this avoids shell-quoting problems on every platform.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

from . import baseline as basemod, checks, gaps as gapmod, ingest, reqs as reqmod, stories as storymod
from .project import Project
from .util import BAError, human_ist, sha256_file

TRUE = {"1", "true", "yes", "y", "on"}


def _csv(v):
    return [x.strip() for x in v.split(",") if x.strip()] if isinstance(v, str) else (v or [])


def _json_file(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        raise BAError("BAD_INPUT", f"Cannot read JSON file {path}: {e}")


def _text(a, name):
    v = getattr(a, name, None)
    f = getattr(a, name + "_file", None)
    if f:
        try:
            return Path(f).read_text(encoding="utf-8-sig").strip()
        except OSError as e:
            raise BAError("BAD_INPUT", f"Cannot read {f}: {e}")
    return v


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ba.py", description="Interactive BA plugin — deterministic workflow core")
    ap.add_argument("--root", help="project folder (or env BA_PROJECT_ROOT, or cwd if it is a project)")
    ap.add_argument("--args-file", help="JSON file supplying option values (avoids shell quoting)")
    sub = ap.add_subparsers(dest="group", required=True)

    def grp(name, actions):
        g = sub.add_parser(name)
        gs = g.add_subparsers(dest="action", required=True)
        for aname, opts in actions.items():
            s = gs.add_parser(aname)
            for o in opts:
                flags, kw = (o[0], o[1]) if isinstance(o, tuple) else (o, {})
                s.add_argument(*([flags] if isinstance(flags, str) else flags), **kw)
        return g

    pos = lambda n: (n, {})
    opt = lambda n, **k: (n, k)
    sub.add_parser("status").add_argument("--json", action="store_true")
    sub.add_parser("validate")
    t = sub.add_parser("trace"); t.add_argument("--write", action="store_true")
    sub.add_parser("recover")
    sub.add_parser("complete")
    sub.add_parser("report")
    f = sub.add_parser("fingerprint"); f.add_argument("file")
    i = sub.add_parser("init")
    i.add_argument("--brd", required=True); i.add_argument("--name"); i.add_argument("--copy-source", action="store_true")

    grp("source", {"check": [], "accept": [opt("--by-name"), opt("--by-role"), opt("--statement")],
                   "section": [pos("section_id"), opt("--allow-changed", action="store_true")],
                   "outline": []})
    grp("req", {"add": [opt("--file")], "commit": [], "pairs": [], "list": [opt("--status")], "show": [pos("id")],
                "update": [pos("id"), opt("--file"), opt("--reason"), opt("--reason-file"), opt("--cause", default="decision"),
                          opt("--decision-id"), opt("--by-name"), opt("--by-role")],
                "confirm": [pos("id"), opt("--note")],
                "remove": [pos("id"), opt("--reason"), opt("--by-name"), opt("--by-role")]})
    grp("owner", {"set": [opt("--name"), opt("--role"), opt("--gap"), opt("--statement")],
                  "unknown": [opt("--choice", default="pause")], "show": []})
    grp("gaps", {"add": [opt("--file")], "commit": [opt("--cross-pass-done", action="store_true")],
                 "next": [opt("--limit", type=int, default=3)], "list": [opt("--status")]})
    resolve_opts = [pos("id"), opt("--answer-id"), opt("--interpretation"), opt("--interpretation-file"), opt("--closed-by-name"),
                    opt("--closed-by-role"), opt("--closure-summary"), opt("--confirmation"), opt("--confirmation-file"),
                    opt("--conflict-status", default="PASSED"), opt("--conflicting"), opt("--reviewed"),
                    opt("--change-by-name"), opt("--change-by-role"), opt("--change-statement"),
                    opt("--requirement-updates"), opt("--owner-confirmation")]
    grp("gap", {"show": [pos("id")],
                "answer": [pos("id"), opt("--response"), opt("--response-file"), opt("--respondent-name"), opt("--respondent-role")],
                "followup": [pos("id"), opt("--answer-id"), opt("--note")],
                "conflict": [pos("id"), opt("--answer-id"), opt("--with", dest="with_ids"), opt("--description")],
                "resolve": resolve_opts,
                "defer": [pos("id"), opt("--reason"), opt("--owner"), opt("--revisit-by"), opt("--by-name"), opt("--by-role")],
                "skip": [pos("id")], "revisit": [pos("id")],
                "reopen": [pos("id"), opt("--reason"), opt("--by-name"), opt("--by-role"), opt("--statement")]})
    grp("policy", {"set": [opt("--carry-forward"), opt("--by-name"), opt("--by-role"), opt("--statement")]})
    grp("baseline", {"create": [opt("--summary")],
                     "confirm": [opt("--version", type=int), opt("--by-name"), opt("--by-role"), opt("--statement")],
                     "show": [opt("--version", type=int)]})
    grp("story", {"add": [opt("--file")], "show": [pos("id")],
                  "next": [opt("--id"), opt("--deferred", action="store_true"), opt("--batch", type=int, default=1)],
                  "approve": [pos("id"), opt("--version", type=int), opt("--statement"), opt("--approver-name"), opt("--approver-role")],
                  "edit": [pos("id"), opt("--file"), opt("--reason")],
                  "defer": [pos("id"), opt("--version", type=int), opt("--reason")], "revisit": [pos("id")],
                  "request-changes": [pos("id"), opt("--version", type=int), opt("--note")],
                  "confirm-unaffected": [pos("id"), opt("--statement"), opt("--by-name"), opt("--by-role")]})
    return ap


def _apply_args_file(ns, path):
    data = _json_file(path)
    if not isinstance(data, dict):
        raise BAError("BAD_INPUT", "--args-file must contain a JSON object.")
    for k, v in data.items():
        setattr(ns, k.replace("-", "_"), v)


def _open(a) -> Project:
    root = a.root or os.environ.get("BA_PROJECT_ROOT")
    if not root and (Path.cwd() / "state" / "workflow.json").exists():
        root = str(Path.cwd())
    if not root:
        raise BAError("NO_PROJECT", "Pass --root <project folder> (the folder the user confirmed for outputs).")
    return Project.open(root)


def run(a) -> dict | str:
    g, act = a.group, getattr(a, "action", None)
    if g == "fingerprint":
        fp = Path(a.file)
        if not fp.is_file():
            raise BAError("SOURCE_NOT_FOUND", f"{fp} not found")
        return {"file": str(fp.resolve()), "fingerprint": sha256_file(fp)}
    if g == "init":
        if not a.root:
            raise BAError("NO_PROJECT", "init requires --root <project folder> explicitly confirmed by the user.")
        p = Project.create(a.root, a.brd, a.name, a.copy_source)
        meta = p.source_metadata()
        return {"project_root": str(p.root), "project_id": p.state["project_id"], "stage": p.state["stage"],
                "source": {"format": meta["format"], "fingerprint": meta["fingerprint"], "sections": len(meta["sections"]),
                           "parser": meta["parser"]},
                "warnings": meta["warnings"], "instruction_like_lines": len(meta["injection_suspects"]),
                "outline": [{"id": s["section_id"], "title": s["title"], "lines": [s["line_start"], s["line_end"]]} for s in meta["sections"]],
                "next": "Decompose the BRD into atomic requirements (`req add`)."}
    p = _open(a)
    if g == "status":
        r = checks.status_report(p)
        return r if a.json else checks.status_text(r)
    if g == "validate":
        return checks.validate_project(p)
    if g == "trace":
        return checks.traceability(p, a.write)
    if g == "recover":
        return checks.recover(p)
    if g == "complete":
        r = checks.complete_workflow(p)
        r.pop("summary", None)
        return r
    if g == "report":
        return checks.write_summary(p)
    if g == "source":
        if act == "check":
            return reqmod.source_check(p)
        if act == "accept":
            return reqmod.source_accept(p, a.by_name, a.by_role, a.statement)
        if act == "section":
            return reqmod.source_section(p, a.section_id, a.allow_changed)
        return {"outline": p.source_metadata()["sections"]}
    if g == "req":
        if act == "add":
            return reqmod.add_requirements(p, _json_file(a.file))
        if act == "commit":
            return reqmod.commit_requirements(p)
        if act == "pairs":
            return reqmod.pairs_report(p)
        if act == "list":
            rs = [r for r in p.requirements() if not a.status or r["status"] == a.status]
            return {"requirements": [{k: r[k] for k in ("requirement_id", "title", "statement", "type", "status", "version", "source_reference")} for r in rs]}
        if act == "show":
            return p.requirement(a.id)
        if act == "update":
            return reqmod.update_requirement(p, a.id, _json_file(a.file),
                                             _text(a, "reason"), a.cause, a.decision_id, a.by_name, a.by_role)
        if act == "confirm":
            return reqmod.confirm_requirement(p, a.id, a.note)
        if act == "remove":
            return reqmod.remove_requirement(p, a.id, a.reason, a.by_name, a.by_role)
    if g == "owner":
        if act == "set":
            return gapmod.set_owner(p, a.name, a.role, a.gap, a.statement)
        if act == "unknown":
            return gapmod.owner_unknown(p, a.choice)
        return {"decision_owner": p.state.get("decision_owner"), "unknown": p.state.get("decision_owner_unknown"),
                "history": p.state["decision_owner_history"]}
    if g == "gaps":
        if act == "add":
            return gapmod.add_gaps(p, _json_file(a.file))
        if act == "commit":
            return gapmod.commit_analysis(p, a.cross_pass_done)
        if act == "next":
            return gapmod.next_gaps(p, a.limit)
        return {"gaps": [{k: x[k] for k in ("gap_id", "category", "priority", "status", "title", "impacted_requirement_ids", "decision_id")}
                         for x in p.gaps() if not a.status or x["status"] == a.status]}
    if g == "gap":
        if act == "show":
            return p.gap(a.id)
        if act == "answer":
            return gapmod.record_answer(p, a.id, _text(a, "response"), a.respondent_name, a.respondent_role)
        if act == "followup":
            return gapmod.followup(p, a.id, a.answer_id, a.note)
        if act == "conflict":
            return gapmod.record_conflict(p, a.id, a.answer_id, _csv(a.with_ids), a.description)
        if act == "resolve":
            ru = a.requirement_updates
            ru = _json_file(ru) if isinstance(ru, str) else (ru or {})
            return gapmod.resolve(
                p, a.id, a.answer_id, _text(a, "interpretation"), a.closed_by_name, a.closed_by_role, a.closure_summary,
                _text(a, "confirmation"), a.conflict_status, _csv(a.conflicting), _csv(a.reviewed), a.change_by_name,
                a.change_by_role, a.change_statement, ru, a.owner_confirmation)
        if act == "defer":
            return gapmod.defer(p, a.id, a.reason, a.owner, a.revisit_by, a.by_name, a.by_role)
        if act == "skip":
            return gapmod.skip(p, a.id)
        if act == "revisit":
            return gapmod.revisit(p, a.id)
        if act == "reopen":
            return gapmod.reopen(p, a.id, a.reason, a.by_name, a.by_role, a.statement)
    if g == "policy":
        return gapmod.set_policy(p, str(a.carry_forward).lower() in TRUE, a.by_name, a.by_role, a.statement)
    if g == "baseline":
        if act == "create":
            return basemod.create(p, a.summary)
        if act == "confirm":
            return basemod.confirm(p, a.version, a.by_name, a.by_role, a.statement)
        v = a.version or p.state["baseline_version"] or (p.baseline_manifest()["versions"][-1]["version"] if p.baseline_manifest()["versions"] else None)
        if v is None:
            raise BAError("UNKNOWN_BASELINE", "No baseline exists yet.")
        return p.read_json(f"baselines/baseline-v{v}.json")
    if g == "story":
        if act == "add":
            return storymod.add_stories(p, _json_file(a.file))
        if act == "next":
            return storymod.next_story(p, a.id, a.deferred, a.batch)
        if act == "show":
            return storymod._load(p, a.id)[1]
        if act == "approve":
            return storymod.approve(p, a.id, a.version, a.statement, a.approver_name, a.approver_role)
        if act == "edit":
            return storymod.edit(p, a.id, _json_file(a.file), a.reason)
        if act == "defer":
            return storymod.defer(p, a.id, a.version, a.reason)
        if act == "revisit":
            return storymod.revisit(p, a.id)
        if act == "request-changes":
            return storymod.request_changes(p, a.id, a.version, a.note)
        if act == "confirm-unaffected":
            return storymod.confirm_unaffected(p, a.id, a.statement, a.by_name, a.by_role)
    raise BAError("USAGE", f"Unhandled command {g} {act}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = build_parser()
    ns = ap.parse_args(argv)
    try:
        if ns.args_file:
            _apply_args_file(ns, ns.args_file)
        out = run(ns)
        if isinstance(out, str):
            print(out)
        else:
            print(json.dumps({"ok": True, **out} if isinstance(out, dict) else {"ok": True, "result": out},
                             indent=2, ensure_ascii=False))
        return 0
    except BAError as e:
        print(json.dumps({"ok": False, "error": {"code": e.code, "message": e.message, "details": e.details}},
                         indent=2, ensure_ascii=False, default=str))
        return 1
    except Exception as e:  # pragma: no cover - defensive
        print(json.dumps({"ok": False, "error": {"code": "INTERNAL_ERROR", "message": f"{type(e).__name__}: {e}",
                                                 "trace": traceback.format_exc(limit=4)}}, indent=2, ensure_ascii=False))
        return 3
