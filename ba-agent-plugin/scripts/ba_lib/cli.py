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

from . import acs as acmod, adr, changes as changemod, checks, discover, docsync, gaps as gapmod, helptext, overview, reqindex, snapshots, sync
from .project import Project
from .util import BAError, human_ist, sha256_file

TRUE = {"1", "true", "yes", "y", "on"}

# Commands that write downstream artifacts (or present them to the user): a change check runs first and they refuse to
# continue while a detected change has not been shown to the user and applied. `changes`, `ac confirm-unaffected`,
# `ac restore|revert-edit|archive`, `doc`, `recover`, `validate`, `status` are never blocked.
GUARDED = {("gaps", "add"), ("gaps", "commit"), ("gaps", "next"), ("gap", "resolve"), ("ac", "add"), ("ac", "next"), ("ac", "approve"),
           ("ac", "edit")}
# Read-only listings: the check still runs (and is recorded) first, and its result is attached to the output.
CHECKED = {("gaps", "triage"), ("ac", "triage"), ("complete", None), ("report", None)}

# Collections a caller always expects to find, even when empty; every other empty value is dropped from the output.
# `candidate_conflicts` stays explicit so "checked, none found" is never confused with "field missing".
KEEP_EMPTY = {"gaps", "acs", "changes", "requirements", "outstanding", "errors", "results", "candidate_conflicts"}


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


GLOBAL_FLAGS = ("--changed-files", "--pretty")


def _hoist(argv):
    """Global flags work before or after the command: move them to the front, where the top-level parser reads them."""
    argv = list(argv)
    return [a for a in argv if a in GLOBAL_FLAGS] + [a for a in argv if a not in GLOBAL_FLAGS]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ba.py", description="Interactive BA plugin — deterministic workflow core")
    ap.add_argument("--root", help="project folder (or env BA_PROJECT_ROOT, or cwd if it is a project)")
    ap.add_argument("--args-file", help="JSON file supplying option values (avoids shell quoting)")
    ap.add_argument("--changed-files", action="store_true", help="report changed_files / removed_files: every project file this command wrote, moved or removed (env BA_CHANGED_FILES=1, or `bridge on` once per project)")
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
    mf.add_argument("--stage-list", action="store_true", help="list the files to stage into a cloud workspace instead of comparing")
    br = sub.add_parser("bridge"); br.add_argument("mode", nargs="?", choices=("on", "off"))
    t = sub.add_parser("trace"); t.add_argument("--write", action="store_true")
    sub.add_parser("recover")
    sub.add_parser("files")
    hp = sub.add_parser("help"); hp.add_argument("topic", nargs="?"); hp.add_argument("what", nargs="?")
    fd = sub.add_parser("find"); fd.add_argument("--dir", help="folder to look in (default: --root, else the current folder)")
    sub.add_parser("complete")
    sub.add_parser("report")
    f = sub.add_parser("fingerprint"); f.add_argument("file")
    i = sub.add_parser("init")
    i.add_argument("--requirements", required=True, help="a folder of .md requirement files, or one .md file"); i.add_argument("--name")
    i.add_argument("--copy-source", action="store_true"); i.add_argument("--recursive", action="store_true")
    i.add_argument("--locator", help="regular expression for a requirement ID if the default (REQ-012, FR-2.1, AUTH-004 ...) does not fit")
    i.add_argument("--enriched-dir", help="write the enriched copies here instead of <project>/enriched (never inside the requirements folder)")
    i.add_argument("--delegate-above", type=int); i.add_argument("--batch-chars", type=int)

    grp("source", {"index": [opt("--delegate-above", type=int), opt("--batch-chars", type=int)], "check": [opt("--no-record", action="store_true")],
                   "history": []})
    grp("changes", {"check": [opt("--no-record", action="store_true")],
                    "apply": [opt("--id"), opt("--by-name"), opt("--by-role"), opt("--statement")],
                    "list": [opt("--status")], "show": [pos("id")], "status": [],
                    "dismiss": [pos("id"), opt("--item"), opt("--reason"), opt("--by-name"), opt("--by-role")]})
    grp("req", {"list": [opt("--status")], "show": [pos("key")], "pairs": []})
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
                    opt("--owner-confirmation")]
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
    grp("ac", {"add": [opt("--file")], "show": [pos("key")], "gaps": [opt("--requirements")], "triage": [], "keep": [pos("key")],
               "next": [opt("--key"), opt("--deferred", action="store_true"), opt("--unapproved", action="store_true"), opt("--batch", type=int, default=1),
                        opt("--full", action="store_true"), opt("--fallback", action="store_true")],
               "approve": [pos("key"), opt("--version", type=int), opt("--statement"), opt("--approver-name"), opt("--approver-role")],
               "edit": [pos("key"), opt("--file"), opt("--reason")],
               "defer": [pos("key"), opt("--version", type=int), opt("--reason")], "revisit": [pos("key")],
               "history": [pos("key")],
               "restore": [pos("key"), opt("--reason"), opt("--by-name"), opt("--by-role")],
               "revert-edit": [pos("key"), opt("--reason"), opt("--by-name"), opt("--by-role")],
               "archive": [pos("key"), opt("--reason"), opt("--by-name"), opt("--by-role")],
               "request-changes": [pos("key"), opt("--version", type=int), opt("--note")],
               "confirm-unaffected": [pos("key"), opt("--statement"), opt("--by-name"), opt("--by-role")]})
    grp("doc", {"sync": [opt("--key"), opt("--force", action="store_true")], "check": []})
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
READ_ONLY = {"show", "list", "pairs", "index", "history", "triage", "next", "gaps", "check", "status", "explain"}


def run(a) -> dict | str:
    root = _root(a)
    want = (a.changed_files or os.environ.get("BA_CHANGED_FILES", "").lower() in TRUE or sync.bridge_on(root) or a.group == "bridge") and a.group not in ("help", "find", "fingerprint")
    root = root if want else None
    before = sync.sig(root) if root else None
    p, out = _run(a)
    out = _finish(a, p, out)
    if before is not None and isinstance(out, dict):
        after = sync.sig(root)
        out.update(sync.changed(before, after, getattr(p, "vacated", ())))
    return out


def _finish(a, p, out) -> dict | str:
    writing = a.group not in ("status", "validate", "trace", "files", "manifest", "bridge") and getattr(a, "action", None) not in READ_ONLY
    if p is not None and writing:
        p.sync_docs()  # the enriched copies follow every state change (never overwriting a hand edit: that waits for `changes check`)
        adr.refresh(p)  # ADR-LOG.md at the top of every artifact folder: who decided / approved what, when (IST)
        overview.refresh(p)  # START-HERE.md: where everything is and where the project stands
    if p is not None and a.group not in ("status", "validate", "trace"):
        done = changemod.reconcile(p)  # close change items whose underlying work is now complete
        if done and isinstance(out, dict):
            out["changes_reconciled"] = done
    if p is not None and isinstance(out, dict) and a.group not in ("status", "validate", "trace", "files") and getattr(a, "action", None) not in READ_ONLY:
        saved = p.receipt(since=getattr(a, "_before", None))  # proof of what this command wrote and where to open it
        if saved:
            out["files_saved"] = saved
        if p.doc_warnings:
            out["enriched_copy_warnings"] = list(dict.fromkeys(p.doc_warnings))
        gid = getattr(a, "id", None) if a.group == "gap" else None
        if gid and p._has_register() and any(g["gap_id"] == gid for g in p.gaps()):
            out["gap_file"] = p.gap_location(gid)  # where the gap lives now: it moves between open/deferred/resolved
    return out


def _legacy_status(a, e: BAError):
    d = e.details
    return None, {"legacy_project": True, "project_id": d.get("project_id"), "stage": d.get("stage"), "schema_version": d.get("schema_version"),
                  "message": e.message}


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
    if g == "init":
        if not a.root:
            raise BAError("NO_PROJECT", "init requires --root <project folder> explicitly confirmed by the user.")
        p = Project.create(a.root, a.requirements, a.name, a.copy_source, a.recursive, a.locator, a.enriched_dir)
        adr.refresh(p)
        overview.refresh(p)
        rep = p.index_report
        ents = p.requirements()
        return None, {"project_root": str(p.root), "project_id": p.state["project_id"], "stage": p.state["stage"],
                      "source": {"path": p.state["source"]["path"], "kind": p.state["source"]["kind"], "files": len({e["file"] for e in ents}),
                                 "requirements": len(ents), "fingerprint": p.state["source"]["fingerprint"],
                                 "id_pattern": p.state["source"]["locator"]["id_pattern"]},
                      "enriched_folder": str(p.enriched_root), "warnings": rep["warnings"], "skipped": rep["skipped"],
                      "instruction_like_lines": rep["instruction_like_lines"],
                      **reqindex.outline_batches(p, a.delegate_above, a.batch_chars),
                      "next": "Run gap analysis: read each requirement (`req show <key>`), add evidence-based gaps (`gaps add`), run the "
                              "cross-requirement pass (`req pairs`), then `gaps commit --cross-pass-done`. If `delegate` is true, ask the "
                              "user once whether to use `ba-agent:gap-analyst` helpers (one per batch, in parallel); if they decline or the "
                              "Agent tool is not allowed, read the batches one requirement at a time here."}
    try:
        p = _open(a)
    except BAError as e:
        if e.code == "LEGACY_PROJECT" and g == "status":
            return _legacy_status(a, e)
        raise
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
        return sync.manifest(p.root, a.write, a.full, a.stage_list)
    if g == "bridge":
        return sync.bridge(p.root, a.mode)
    if g == "trace":
        return checks.traceability(p, a.write)
    if g == "recover":
        return checks.recover(p)
    if g == "files":
        return {"project_folder": str(p.root), "stage": p.state["stage"], "enriched_folder": str(p.enriched_root), "folders": p.receipt(),
                "note": "Each folder keeps readable copies in md/ and machine records in json/; the enriched copies are in enriched/. Tell the user these paths."}
    if g == "complete":
        r = checks.complete_workflow(p)
        r.pop("summary", None)
        return r
    if g == "report":
        return checks.write_summary(p)
    if g == "doc":
        return docsync.check(p) if act == "check" else docsync.sync(p, a.key, a.force)
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
        if act == "history":
            return snapshots.index(p)
        doc = reqindex.load(p)
        return {"files": {r: {"requirements": f["units"], "mode": f["mode"], "status": f["status"]} for r, f in doc["files"].items()},
                "requirements": len(p.requirements()), "id_pattern": p.state["source"]["locator"]["id_pattern"],
                **reqindex.outline_batches(p, a.delegate_above, a.batch_chars)}
    if g == "req":
        if act == "pairs":
            return reqindex.pairs_report(p)
        if act == "list":
            rows = [r for r in checks.requirement_rows(p) if not a.status or r["status"] == a.status.upper()]
            return {"requirements": rows}
        key = p.req_key(a.key)
        v = reqindex.view(p, key)
        v["status"] = docsync.requirement_status(p, key) if v["status"] == "ACTIVE" else v["status"]
        v["gaps"] = [{"gap_id": x["gap_id"], "priority": x["priority"], "status": x["status"], "title": x["title"]}
                     for x in p.gaps() if key in x["impacted_req_keys"] and x["status"] != "RESOLVED"]
        v["decisions"] = sorted(d["decision_id"] for d in p.decisions().values() if d["current_status"] == "ACTIVE" and key in d.get("related_artifact_ids", []))
        v["acceptance_criteria"] = acmod.ac_states(p).get(key)
        return v
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
        labels = reqindex.labels(p)
        return {"gaps": [dict({k: x[k] for k in ("gap_id", "category", "priority", "status", "title", "decision_id")},
                              requirements=[labels.get(k, k) for k in x["impacted_req_keys"]], carried_forward=bool(x.get("carry_forward")))
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
            return gapmod.resolve(
                p, a.id, a.answer_id, _text(a, "interpretation"), a.closed_by_name, a.closed_by_role, a.closure_summary,
                _text(a, "confirmation"), a.conflict_status, _csv(a.conflicting), _csv(a.reviewed), a.change_by_name,
                a.change_by_role, a.change_statement, a.owner_confirmation)
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
    if g == "ac":
        if act == "add":
            return acmod.add_acs(p, _json_file(a.file))
        if act == "next":
            return acmod.next_ac(p, a.key, a.deferred, a.batch, a.unapproved, a.full, a.fallback)
        if act == "show":
            return acmod._load(p, a.key)[1]
        if act == "triage":
            return acmod.triage(p)
        if act == "keep":
            return acmod.keep(p, a.key)
        if act == "gaps":
            return acmod.gap_context(p, _csv(a.requirements))
        if act == "approve":
            return acmod.approve(p, a.key, a.version, a.statement, a.approver_name, a.approver_role)
        if act == "edit":
            return acmod.edit(p, a.key, _json_file(a.file), a.reason)
        if act == "defer":
            return acmod.defer(p, a.key, a.version, a.reason)
        if act == "revisit":
            return acmod.revisit(p, a.key)
        if act == "request-changes":
            return acmod.request_changes(p, a.key, a.version, a.note)
        if act == "confirm-unaffected":
            return acmod.confirm_unaffected(p, a.key, a.statement, a.by_name, a.by_role)
        if act == "archive":
            return acmod.archive(p, a.key, a.reason, a.by_name, a.by_role)
        if act == "history":
            return changemod.ac_history(p, a.key)
        if act == "restore":
            return changemod.restore_ac(p, a.key, a.reason, a.by_name, a.by_role)
        if act == "revert-edit":
            return changemod.revert_edit(p, a.key, a.reason, a.by_name, a.by_role)
    raise BAError("USAGE", f"Unhandled command {g} {act}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = build_parser()
    ns = ap.parse_args(_hoist(sys.argv[1:] if argv is None else argv))
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
