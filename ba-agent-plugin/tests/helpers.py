"""Shared test helpers: fake IST-verifiable clock, project builders, drive-to-stage helpers."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "scripts"))

from ba_lib import adr, baseline as basemod, checks, gaps as gapmod, ingest, project as projmod, reqs, stories, util  # noqa: E402
from ba_lib.project import Project  # noqa: E402

FIXTURE = PLUGIN / "tests" / "fixtures" / "brd_library.md"
# 2026-10-22 13:15:00 UTC == 2026-10-22 18:45:00 IST (+05:30)
CLOCK_START = datetime(2026, 10, 22, 13, 15, 0, tzinfo=timezone.utc)


def adr_refresh(p) -> None:
    adr.refresh(p)


def option_labels(packet: dict) -> list[str]:
    """Labels of the choice dialog the core built for a gap or story packet."""
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
    def brd_copy(self, name="brd.md", text=None) -> Path:
        dest = self.tmp / "inputs" / name
        dest.parent.mkdir(exist_ok=True)
        if text is None:
            shutil.copyfile(FIXTURE, dest)
        else:
            dest.write_text(text, encoding="utf-8")
        return dest

    def new_project(self, **kw) -> Project:
        brd = kw.pop("brd", None) or self.brd_copy()
        return Project.create(self.tmp / "out", brd, **kw)

    def sec(self, p, title_prefix: str) -> str:
        for s in p.source_metadata()["sections"]:
            if s["title"].startswith(title_prefix):
                return s["section_id"]
        raise AssertionError(f"no section {title_prefix}")

    def req_items(self, p):
        return [
            dict(title="Member registration", statement="A visitor registers with a valid email and password to become a member.",
                 type="FUNCTIONAL", source_section_ids=[self.sec(p, "2.1")],
                 source_excerpt="A visitor shall register with a valid email address and a password to become a member.",
                 actors=["Visitor"]),
            dict(title="Registration confirmation email", statement="The system sends a confirmation email after registration.",
                 type="FUNCTIONAL", source_section_ids=[self.sec(p, "2.1")],
                 source_excerpt="The system must send a confirmation email after registration."),
            dict(title="Concurrent borrow limit", statement="A member may borrow at most 5 books at the same time.",
                 type="BUSINESS_RULE", source_section_ids=[self.sec(p, "2.2")],
                 source_excerpt="A member may borrow at most 5 books at the same time."),
            dict(title="Return period", statement="Borrowed books must be returned within 14 days.", type="BUSINESS_RULE",
                 source_section_ids=[self.sec(p, "2.2")], source_excerpt="Borrowed books must be returned within 14 days."),
            dict(title="Record borrow and due dates", statement="The system records the borrow date and due date for each loan.",
                 type="DATA", source_section_ids=[self.sec(p, "3.1")],
                 source_excerpt="The system must record the borrow date and due date."),
            dict(title="Late return fine", statement="A member who returns a book late is charged a fine.", type="BUSINESS_RULE",
                 source_section_ids=[self.sec(p, "3.2")], source_excerpt="A member who returns a book late shall be charged a fine."),
            dict(title="Overdue report", statement="Staff can view a report of overdue books.", type="FUNCTIONAL",
                 source_section_ids=[self.sec(p, "4. Reporting")], source_excerpt="The system should provide staff a report of overdue books."),
            dict(title="Mobile page speed", statement="Pages load quickly for members on mobile devices.", type="NON_FUNCTIONAL",
                 source_section_ids=[self.sec(p, "5. Non-functional")], source_excerpt="Pages must load quickly for members on mobile devices."),
        ]

    def with_requirements(self, **kw) -> Project:
        p = self.new_project(**kw)
        reqs.add_requirements(p, self.req_items(p))
        reqs.commit_requirements(p)
        return p

    def gap_items(self):
        return [
            dict(category="Business rules", title="Fine rate undefined", description="The fine for late returns has no rate.",
                 evidence="BRD 3.2: 'The fine rate is to be decided.'", why_it_matters="Cannot implement or test fine calculation.",
                 question="What is the fine amount per day late, and is there a maximum?", impacted_requirement_ids=["REQ-006"],
                 priority="HIGH", priority_rationale="Changes calculation logic and acceptance tests.", material=True),
            dict(category="Non-functional requirements", title="'Quickly' is not measurable", description="No target page-load time.",
                 evidence="BRD 5: 'load quickly'", why_it_matters="Not testable.", question="What page-load time target applies on mobile?",
                 impacted_requirement_ids=["REQ-008"], priority="MEDIUM", priority_rationale="Untestable but not blocking core flows.",
                 material=False),
            dict(category="Validation and exception handling", title="Over-limit borrow behaviour", description="Behaviour on 6th book unspecified.",
                 evidence="BRD 2.2 limit of 5", why_it_matters="Defines the error path.", question="What should happen when a member tries to borrow a 6th book?",
                 impacted_requirement_ids=["REQ-003"], priority="HIGH", priority_rationale="Alters acceptance criteria.", material=False),
        ]

    def with_gaps(self, **kw) -> Project:
        p = self.with_requirements(**kw)
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

    def to_baseline(self, p=None, approve=True):
        p = p or self.with_gaps()
        self.resolve_all(p)
        gapmod.commit_analysis(p, True)
        basemod.create(p, "initial")
        if approve:
            basemod.confirm(p, 1, "Priya Nair", "Product Owner", "I approve baseline v1.")
        return p

    def story_item(self, title="Register as a member", reqs_=("REQ-001",), **kw):
        d = dict(title=title, user_story=dict(role="Visitor", capability="to register with my email and password",
                                              value="I can borrow books online"),
                 acceptance_criteria=["Given a visitor with a valid email, when they submit registration, then a member account is created.",
                                      "Given an invalid email, when they submit registration, then an error is shown and no account is created."],
                 requirement_ids=list(reqs_))
        d.update(kw)
        return d

    def to_stories(self, n=2):
        p = self.to_baseline()
        items = [self.story_item("Register as a member", ("REQ-001",)),
                 self.story_item("Receive a confirmation email", ("REQ-002",),
                                 user_story=dict(role="New member", capability="to receive a confirmation email", value="I know my registration worked"),
                                 acceptance_criteria=["Given a completed registration, when the account is created, then a confirmation email is sent."])]
        stories.add_stories(p, items[:n])
        return p


def flatten_to_legacy(root: Path) -> None:
    """Turn a project into the pre-1.6.0 layout where `.md` and `.json` files shared one folder."""
    for d in projmod.SPLIT_DIRS:
        base = Path(root) / d
        for sub in ("md", "json"):
            for f in sorted((base / sub).glob("*")) if (base / sub).is_dir() else []:
                f.replace(base / f.name)
            if (base / sub).is_dir():
                (base / sub).rmdir()


def to_legacy_gap_layout(root: Path) -> None:
    """Turn a project gaps into the pre-1.9.0 layout: every GAP-nnn.md in gaps/md/, the register in gaps/json/."""
    root = Path(root)
    (root / "gaps" / "md").mkdir(parents=True, exist_ok=True)
    (root / "gaps" / "json").mkdir(parents=True, exist_ok=True)
    (root / "state" / "gap-register.json").replace(root / "gaps" / "json" / "gap-register.json")
    for b in projmod.GAP_BUCKETS:
        base = root / "gaps" / b
        for f in list((base / "md").glob("GAP-*.md")):
            f.replace(root / "gaps" / "md" / f.name)
        for f in list((base / "json").glob("GAP-*.json")):
            f.unlink()
