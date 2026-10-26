"""Shared test helpers: fake IST-verifiable clock, project builders, drive-to-stage helpers.

The fixture is a small requirements folder (`tests/fixtures/requirements/`): membership.md (LIB-001..004, ID headings),
lending.md (LIB-005..007, ID headings, one line that tries to instruct the assistant) and reporting.md (LIB-008..009, table rows);
README.md is skipped as "not a requirements file".
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "scripts"))

from ba_lib import (acs, actrack, adr, changes as changemod, checks, discover, docmodel, docsync, gaps as gapmod, ingest, project as projmod,  # noqa: E402
                    reqindex, snapshots, sync, util)
from ba_lib.project import Project  # noqa: E402

FIXTURE = PLUGIN / "tests" / "fixtures" / "requirements"
# 2026-10-22 13:15:00 UTC == 2026-10-22 18:45:00 IST (+05:30)
CLOCK_START = datetime(2026, 10, 22, 13, 15, 0, tzinfo=timezone.utc)
FILES = {"LIB-001": "membership.md", "LIB-002": "membership.md", "LIB-003": "membership.md", "LIB-004": "membership.md",
         "LIB-005": "lending.md", "LIB-006": "lending.md", "LIB-007": "lending.md", "LIB-008": "reporting.md", "LIB-009": "reporting.md"}


def key(rid: str) -> str:
    """Full requirement key of a fixture requirement: LIB-006 -> lending.md::LIB-006."""
    return f"{FILES[rid]}::{rid}"


def adr_refresh(p) -> None:
    adr.refresh(p)


def option_labels(packet: dict) -> list[str]:
    """Labels of the choice dialog the core built for a gap or an acceptance-criteria packet."""
    return [o["label"] for o in packet["present"]["dialog"]["options"]]


class FakeClock:
    def __init__(self, start=CLOCK_START):
        self.t = start

    def __call__(self):
        cur = self.t
        self.t += timedelta(seconds=1)
        return cur


class BATest(unittest.TestCase):
    """Base: temp dir, deterministic clock, fault hook reset."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ba-test-"))
        self.clock = FakeClock()
        util.set_clock(self.clock)
        projmod.FAULT_HOOK = None
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        util.set_clock(None)
        projmod.FAULT_HOOK = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- builders
    def reqs_copy(self, name="reqs") -> Path:
        """A fresh copy of the fixture folder (the 'supplied' requirements) under tmp/inputs/<name>."""
        dest = self.tmp / "inputs" / name
        dest.parent.mkdir(exist_ok=True)
        shutil.copytree(FIXTURE, dest)
        return dest

    def write_req(self, folder: Path, name: str, text: str) -> Path:
        f = folder / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(text.encode("utf-8"))
        return f

    def new_project(self, **kw) -> Project:
        src = kw.pop("src", None) or self.reqs_copy()
        self.src = Path(src)
        return Project.create(self.tmp / "out", src, **kw)

    with_requirements = new_project

    def key(self, rid: str) -> str:
        return key(rid)

    def gap_items(self):
        return [
            dict(category="Business rules", title="Fine rate undefined", description="The fine for late returns has no rate.",
                 evidence="LIB-006: 'The fine rate is to be decided.'", evidence_quote="The fine rate is to be decided.",
                 why_it_matters="Cannot implement or test fine calculation.",
                 question="What is the fine amount per day late, and is there a maximum?", impacted_req_keys=[key("LIB-006")],
                 priority="HIGH", priority_rationale="Changes calculation logic and acceptance tests.", material=True),
            dict(category="Non-functional requirements", title="'Quickly' is not measurable", description="No target page-load time.",
                 evidence="LIB-007: 'load quickly'", why_it_matters="Not testable.", question="What page-load time target applies on mobile?",
                 impacted_req_keys=[key("LIB-007")], priority="MEDIUM", priority_rationale="Untestable but not blocking core flows.",
                 material=False),
            dict(category="Validation and exception handling", title="Over-limit borrow behaviour", description="Behaviour on 6th book unspecified.",
                 evidence="LIB-003 limit of 5", why_it_matters="Defines the error path.",
                 question="What should happen when a member tries to borrow a 6th book?",
                 impacted_req_keys=[key("LIB-003")], priority="HIGH", priority_rationale="Alters acceptance criteria.", material=False),
        ]

    def with_gaps(self, **kw) -> Project:
        p = self.new_project(**kw)
        gapmod.add_gaps(p, self.gap_items())
        return p

    def set_owner(self, p, name="Priya Nair", role="Product Owner"):
        return gapmod.set_owner(p, name, role)

    def answer_and_resolve(self, p, gid, response, interpretation, closer=("Priya Nair", "Product Owner"), **kw):
        a = gapmod.record_answer(p, gid, response, kw.pop("respondent_name", None), kw.pop("respondent_role", None))
        return gapmod.resolve(p, gid, a["answer_id"], interpretation, closer[0], closer[1], kw.pop("summary", "Answer resolves the documented ambiguity."),
                              kw.pop("confirmation", "Yes, that is resolved."), **kw)

    def resolve_all(self, p, owner=("Priya Nair", "Product Owner")):
        """Resolve the three seeded gaps."""
        self.set_owner(p, *owner)
        gapmod.next_gaps(p, 10)
        self.answer_and_resolve(p, "GAP-001", "R10 per day late, capped at R200.", "Fine is R10 per day late, maximum R200 per loan.", closer=owner)
        self.answer_and_resolve(p, "GAP-002", "Under 3 seconds on 4G.", "Page load time target on mobile 4G is under 3 seconds.", closer=owner)
        self.answer_and_resolve(p, "GAP-003", "Block the borrow and show a limit-reached message.", "A 6th concurrent borrow is rejected with a limit-reached message.", closer=owner)

    def to_ac_stage(self, p=None):
        """Gaps resolved and gap analysis committed: acceptance criteria can be written for every requirement."""
        p = p or self.with_gaps()
        self.resolve_all(p)
        gapmod.commit_analysis(p, True)
        return p

    def ac_item(self, rid="LIB-001", **kw):
        d = dict(req_key=key(rid),
                 acceptance_criteria=[f"Given a valid case for {rid}, when the action is performed, then the expected outcome is produced.",
                                      f"Given an invalid case for {rid}, when the action is performed, then an error is shown and nothing is saved."])
        d.update(kw)
        return d

    def to_acs(self, n=2, p=None):
        p = self.to_ac_stage(p)
        acs.add_acs(p, [self.ac_item(r) for r in ("LIB-001", "LIB-002", "LIB-003")[:n]])
        return p

    def approve(self, p, rid, version=1):
        return acs.approve(p, key(rid), version, "I approve this version.", "Priya Nair", "Product Owner")

    def enriched(self, p, rel="membership.md") -> str:
        return (p.enriched_root / rel).read_text(encoding="utf-8")
