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

from . import adr, convert as convmod, discover, helptext, overview, baseline as basemod, changes as changemod, checks, gaps as gapmod, ingest, reqs as reqmod, snapshots, stories as storymod, sync
from .project import Project
from .util import BAError, human_ist, sha256_file

TRUE = {"1", "true", "yes", "y", "on"}

# Commands that write downstream artifacts (or present them to the user): a change check runs first and they refuse to
# continue while a detected change has not been shown to the user and applied. `changes`, `source accept`, `req confirm`,
# `req remove`, `story confirm-unaffected`, `story restore|revert-edit`, `recover`, `validate`, `status` are never blocked.
GUARDED = {("req", "add"), ("req", "commit"), ("gaps", "add"), ("gaps", "commit"), ("gaps", "next"), ("gap", "resolve"),
           ("baseline", "create"), ("baseline", "confirm"), ("story", "add"), ("story", "next"), ("story", "approve"), ("story", "edit")}
# Read-only listings: the check still runs (and is recorded) first, and its result is attached to the output.
CHECKED = {("gaps", "triage"), ("story", "triage"), ("complete", None), ("report", None)}


# Collections a caller always expects to find, even when empty; every other empty value is dropped from the output.
# `candidate_conflicts` stays explicit so "checked, none found" is never confused with "field missing".
KEEP_EMPTY = {"gaps", "stories", "changes", "requirements", "outstanding", "errors", "results", "candidate_conflicts"}


def prune(o, top=False):
    """Drop null / empty values from CLI output: absent means none. Booleans and zeros are always kept."""
    if isinstance(o, dict):
        r = {k: prune(v) for k, v in o.items()}
        return {k: v for k, v in r.items() if not (v is None or v == "" or v == {} or (v == [] and not (top and k in KEEP_EMPTY)))}
    if isinstance(o, list):
        return [prune(x) for x in o]
    return o


def dumps(o, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(o, indent=2, ensure_ascii=False, default=str)
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"), default=str)


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
    ap.add_argument("--changed-files", action="store_true", help="report changed_files / removed_files: every project file this command wrote, moved or removed (env BA_CHANGED_FILES=1)")
    ap.add_argument("--pretty", action="store_true", help="indent the JSON output (default is compact: it costs fewer tokens)")
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
    mf = sub.add_parser("manifest"); mf.add_argument("--write", action="store_true"); mf.add_argument("--full", action="store_true")
    t = sub.add_parser("trace"); t.add_argument("--write", action="store_true")
    sub.add_parser("recover")
    sub.add_parser("files")
    cv = sub.add_parser("convert"); cv.add_argument("--in", dest="src", required=True); cv.add_argument("--out")
    hp = sub.add_parser("help"); hp.add_argument("topic", nargs="?"); hp.add_argument("what", nargs="?")
    fd = sub.add_parser("find"); fd.add_argument("--dir", help="folder to look in (default: --root, else the current folder)")
    sub.add_parser("complete")
    sub.add_parser("report")
    f = sub.add_parser("fingerprint"); f.add_argument("file")
    i = sub.add_parser("init")
    i.add_argument("--brd", required=True); i.add_argument("--name"); i.add_argument("--copy-source", action="store_true")
    i.add_argument("--delegate-above", type=int); i.add_argument("--batch-chars", type=int)

    grp("source", {"check": [opt("--no-record", action="store_true")],
                   "accept": [opt("--by-name"), opt("--by-role"), opt("--statement")],
                   "section": [pos("section_id"), opt("--allow-changed", action="store_true")],
                   "outline": [opt("--delegate-above", type=int), opt("--batch-chars", type=int)], "history": []})
    grp("changes", {"check": [opt("--no-record", action="store_true")],
                    "apply": [opt("--id"), opt("--by-name"), opt("--by-role"), opt("--statement")],
                    "list": [opt("--status")], "show": [pos("id")], "status": [],
                    "dismiss": [pos("id"), opt("--item"), opt("--reason"), opt("--by-name"), opt("--by-role")]})
    grp("req", {"add": [opt("--file")], "commit": [], "pairs": [], "list": [opt("--status")], "show": [pos("id")],
                "update": [pos("id"), opt("--file"), opt("--reason"), opt("--reason-file"), opt("--cause", default="decision"),
                          opt("--decision-id"), opt("--by-name"), opt("--by-role")],
                "confirm": [pos("id"), opt("--note")],
                "remove": [pos("id"), opt("--reason"), opt("--by-name"), opt("--by-role")]})
    grp("owner", {"set": [opt("--name"), opt("--role"), opt("--gap"), opt("--statement")],
                  "unknown": [opt("--choice", default="pause")], "show": []})
    grp("gaps", {"add": [opt("--file")], "commit": [opt("--cross-pass-done", action="store_true")],
                 "next": [opt("--limit", type=int, default=1), opt("--carried", action="store_true"), opt("--id"), opt("--fallback", action="store_true")],
                 "list": [opt("--status")], "triage": [opt("--status")],
                 "carry-forward-all": [opt("--by-name"), opt("--by-role"), opt("--reason"), opt("--reason-file")]})
    resolve_opts = [pos("id"), opt("--answer-id"), opt("--interpretation"), opt("--interpretation-file"), opt("--closed-by-name"),
                    opt("--closed-by-role"), opt("--closure-summary"), opt("--confirmation"), opt("--confirmation-file"),
                    opt("--conflict-status", default="PASSED"), opt("--conflicting"), opt("--reviewed"),
                    opt("--change-by-name"), opt("--change-by-role"), opt("--change-statement"),
                    opt("--requirement-updates"), opt("--owner-confirmation")]
    grp("gap", {"show": [pos("id")],
                "answer": [pos("id"), opt("--response"), opt("--response-file"), opt("--respondent-name"), opt("--respondent-role"),
                           opt("--use-suggestion", action="store_true")],
                "explain": [pos("id")],
                "carry-forward": [pos("id"), opt("--by-name"), opt("--by-role"), opt("--reason"), opt("--reason-file")],
                "followup": [pos("id"), opt("--answer-id"), opt("--note")],
                "conflict": [pos("id"), opt("--answer-id"), opt("--with", dest="with_ids"), opt("--description")],
                "resolve": resolve_opts,
                "defer": [pos("id"), opt("--reason"), opt("--reason-file"), opt("--owner"), opt("--revisit-by"), opt("--by-name"), opt("--by-role")],
                "answer-later": [pos("id")], "skip": [pos("id")], "revisit": [pos("id")],
                "reopen": [pos("id"), opt("--reason"), opt("--by-name"), opt("--by-role"), opt("--statement")],
                "keep": [pos("id")]})
    grp("policy", {"set": [opt("--carry-forward"), opt("--deferral-revisit-required"), opt("--by-name"), opt("--by-role"),
                           opt("--statement")]})
    grp("baseline", {"create": [opt("--summary")],
                     "confirm": [opt("--version", type=int), opt("--by-name"), opt("--by-role"), opt("--statement")],
                     "show": [opt("--version", type=int)]})
    grp("story", {"add": [opt("--file")], "show": [pos("id")], "gaps": [opt("--requirements")], "triage": [], "keep": [pos("id")],
                  "next": [opt("--id"), opt("--deferred", action="store_true"), opt("--unapproved", action="store_true"), opt("--batch", type=int, default=1),
                           opt("--full", action="store_true"), opt("--fallback", action="store_true")],
                  "approve": [pos("id"), opt("--version", type=int), opt("--statement"), opt("--approver-name"), opt("--approver-role")],
                  "edit": [pos("id"), opt("--file"), opt("--reason")],
                  "defer": [pos("id"), opt("--version", type=int), opt("--reason")], "revisit": [pos("id")],
                  "history": [pos("id")],
                  "restore": [pos("id"), opt("--reason"), opt("--by-name"), opt("--by-role")],
                  "revert-edit": [pos("id"), opt("--reason"), opt("--by-name"), opt("--by-role")],
                  "request-changes": [pos("id"), opt("--version", type=int), opt("--note")],
                  "confirm-unaffected": [pos("id"), opt("--statement"), opt("--by-name"), opt("--by-role")]})
    return ap


def _apply_args_file(ns, path):
    data = _json_file(path)
    if not isinstance(data, dict):
        raise BAError("BAD_INPUT", "--args-file must contain a JSON object.")
    for k, v in data.items():
        setattr(ns, k.replace("-", "_"), v)


def _root(a) -> str | None:
    root = a.root or os.environ.get("BA_PROJECT_ROOT")
    if not root and (Path.cwd() / "state" / "workflow.json").exists():
        root = str(Path.cwd())
    return root


def _open(a) -> Project:
    root = _root(a)
    if not root:
        raise BAError("NO_PROJECT", "Pass --root <project folder> (the folder the user confirmed for outputs).")
    return Project.open(root)


# Actions that only read; every other command may have written files and so reports where they now are.
READ_ONLY = {"show", "list", "pairs", "outline", "history", "triage", "next", "gaps", "check", "status", "explain"}


def run(a) -> dict | str:
    want = (a.changed_files or os.environ.get("BA_CHANGED_FILES", "").lower() in TRUE) and a.group not in ("help", "find", "fingerprint")
    root = _root(a) if want else None
    before = sync.sig(root) if root else None
    p, out = _run(a)
    out = _finish(a, p, out)
    if before is not None and isinstance(out, dict):
        out.update(sync.changed(before, sync.sig(root)))
    return out


def _finish(a, p, out) -> dict | str:
    if p is not None and a.group not in ("status", "validate", "trace", "files") and getattr(a, "action", None) not in READ_ONLY:
        adr.refresh(p)  # ADR-LOG.md at the top of every artifact folder: who decided / approved what, when (IST)
        overview.refresh(p)  # START-HERE.md: where everything is and where the project stands
    if p is not None and a.group not in ("status", "validate", "trace"):
        done = changemod.reconcile(p)  # close change items whose underlying work is now complete
        if done and isinstance(out, dict):
            out["changes_reconciled"] = done
    if p is not None and isinstance(out, dict) and a.group not in ("status", "validate", "trace", "files")             and getattr(a, "action", None) not in READ_ONLY:
        saved = p.receipt(since=getattr(a, "_before", None))  # proof of what this command wrote and where to open it
        if saved:
            out["files_saved"] = saved
        gid = getattr(a, "id", None) if a.group == "gap" else None
        if gid and p._has_register() and any(g["gap_id"] == gid for g in p.gaps()):
            out["gap_file"] = p.gap_location(gid)  # where the gap lives now: it moves between open/deferred/resolved
    return out


def _run(a):
    g, act = a.group, getattr(a, "action", None)
    if g == "fingerprint":
        fp = Path(a.file)
        if not fp.is_file():
            raise BAError("SOURCE_NOT_FOUND", f"{fp} not found")
        return None, {"file": str(fp.resolve()), "fingerprint": sha256_file(fp)}
    if g == "find":
        return None, discover.find(a.dir or a.root)
    if g == "help":
        return None, helptext.render(build_parser(), a.topic, a.what)
    if g == "convert":
        return None, convmod.convert(a.root or os.environ.get("BA_PROJECT_ROOT"), a.src, a.out)
    if g == "init":
        if not a.root:
            raise BAError("NO_PROJECT", "init requires --root <project folder> explicitly confirmed by the user.")
        p = Project.create(a.root, a.brd, a.name, a.copy_source)
        adr.refresh(p)
        overview.refresh(p)
        meta = p.source_metadata()
        return None, {"project_root": str(p.root), "project_id": p.state["project_id"], "stage": p.state["stage"],
                "source": {"format": meta["format"], "fingerprint": meta["fingerprint"], "sections": len(meta["sections"]),
                           "parser": meta["parser"]},
                "warnings": meta["warnings"], "instruction_like_lines": len(meta["injection_suspects"]),
                **ingest.outline_batches(p.load_source(verify=False), a.delegate_above, a.batch_chars),
                "next": "Decompose the BRD into atomic requirements (`req add`). If `delegate` is true, ask the user once whether to use "
                        "`ba-agent:requirement-analyst` helpers (one per batch, in parallel); if they decline or the Agent tool is not "
                        "allowed, read the batches one section at a time here (`source section`)."}
    p = _open(a)
    a._before = p._dir_sig()
    key = (g, act if (g, act) in GUARDED or (g, act) in CHECKED else None)
    checked = None
    if key in GUARDED:
        changemod.guard(p, f"{g} {act}")
    elif key in CHECKED:
        checked = changemod.check(p, True, f"{g} {act}" if act else g)
    out = _dispatch(a, p, g, act)
    if checked is not None and isinstance(out, dict):
        out["change_check"] = {"unreviewed": checked["unreviewed_ids"], "awaiting_reconciliation": checked["awaiting_reconciliation"],
                               "formatting_only_accepted": checked["formatting_only_accepted"], "warnings": checked["warnings"],
                               "note": checked["next"] if checked["unreviewed_ids"] else None}
    return p, out


def _dispatch(a, p, g, act):
    if g == "status":
        r = checks.status_report(p)
        return r if a.json else checks.status_text(r)
    if g == "validate":
        return checks.validate_project(p)
    if g == "manifest":
        return sync.manifest(p.root, a.write, a.full)
    if g == "trace":
        return checks.traceability(p, a.write)
    if g == "recover":
        return checks.recover(p)
    if g == "files":
        return {"project_folder": str(p.root), "stage": p.state["stage"], "folders": p.receipt(),
                "note": "Each folder keeps readable copies in md/ and machine records in json/. Tell the user these paths."}
    if g == "complete":
        r = checks.complete_workflow(p)
        r.pop("summary", None)
        return r
    if g == "report":
        return checks.write_summary(p)
    if g == "changes":
        if act == "check":
            return changemod.check(p, not a.no_record, "changes check")
        if act == "apply":
            return changemod.apply(p, a.id, a.by_name, a.by_role, a.statement)
        if act == "list":
            return {"changes": [changemod._row(r) for r in changemod.records(p, a.status.upper() if a.status else None)]}
        if act == "show":
            return changemod.get(p, a.id)
        if act == "dismiss":
            return changemod.dismiss(p, a.id, a.item, a.reason, a.by_name, a.by_role)
        return changemod.status(p)
    if g == "source":
        if act == "check":  # detect + explain only; `changes apply` is what touches downstream artifacts
            return changemod.check(p, not a.no_record, "source check")
        if act == "accept":
            return reqmod.source_accept(p, a.by_name, a.by_role, a.statement)
        if act == "section":
            return reqmod.source_section(p, a.section_id, a.allow_changed)
        if act == "history":
            return snapshots.index(p)
        return ingest.outline_batches(p.load_source(verify=False), a.delegate_above, a.batch_chars)
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
            return gapmod.next_gaps(p, a.limit, a.carried, a.id, a.fallback)
        if act == "triage":
            return gapmod.triage(p, a.status)
        if act == "carry-forward-all":
            return gapmod.carry_forward_all(p, a.by_name, a.by_role, _text(a, "reason"))
        return {"gaps": [dict({k: x[k] for k in ("gap_id", "category", "priority", "status", "title", "impacted_requirement_ids", "decision_id")},
                              carried_forward=bool(x.get("carry_forward")))
                         for x in p.gaps() if not a.status or x["status"] == a.status]}
    if g == "gap":
        if act == "show":
            return p.gap(a.id)
        if act == "answer":
            return gapmod.record_answer(p, a.id, _text(a, "response"), a.respondent_name, a.respondent_role, a.use_suggestion)
        if act == "explain":
            return gapmod.explain(p, a.id)
        if act == "carry-forward":
            return gapmod.carry_forward(p, a.id, a.by_name, a.by_role, _text(a, "reason"))
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
            return gapmod.defer(p, a.id, _text(a, "reason"), a.owner, a.revisit_by, a.by_name, a.by_role)
        if act in ("answer-later", "skip"):
            return gapmod.answer_later(p, a.id)
        if act == "revisit":
            return gapmod.revisit(p, a.id)
        if act == "reopen":
            return gapmod.reopen(p, a.id, a.reason, a.by_name, a.by_role, a.statement)
        if act == "keep":
            return gapmod.keep(p, a.id)
    if g == "policy":
        cf = None if a.carry_forward is None else str(a.carry_forward).lower() in TRUE
        dr = None if a.deferral_revisit_required is None else [x.upper() for x in _csv(a.deferral_revisit_required) if x.lower() != "none"]
        return gapmod.set_policy(p, cf, a.by_name, a.by_role, a.statement, dr)
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
            return storymod.next_story(p, a.id, a.deferred, a.batch, a.unapproved, a.full, a.fallback)
        if act == "show":
            return storymod._load(p, a.id)[1]
        if act == "triage":
            return storymod.triage(p)
        if act == "keep":
            return storymod.keep(p, a.id)
        if act == "gaps":
            return storymod.gap_context(p, _csv(a.requirements))
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
        if act == "history":
            return changemod.story_history(p, a.id)
        if act == "restore":
            return changemod.restore_story(p, a.id, a.reason, a.by_name, a.by_role)
        if act == "revert-edit":
            return changemod.revert_edit(p, a.id, a.reason, a.by_name, a.by_role)
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
            print(dumps(prune({"ok": True, **out}, True) if isinstance(out, dict) else {"ok": True, "result": out}, ns.pretty))
        return 0
    except BAError as e:
        print(dumps({"ok": False, "error": {"code": e.code, "message": e.message, "details": e.details}}, ns.pretty))
        return 1
    except Exception as e:  # pragma: no cover - defensive
        print(dumps({"ok": False, "error": {"code": "INTERNAL_ERROR", "message": f"{type(e).__name__}: {e}",
                                            "trace": traceback.format_exc(limit=4)}}, ns.pretty))
        return 3
