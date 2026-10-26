"""Acceptance criteria: draft -> review -> approve -> block in the enriched copy; edits, carry-forward, deferral, validation rules."""
import unittest

from tests.helpers import (BATest, acs, actrack, changemod, checks, docmodel, docsync, gapmod, projmod, reqindex, util, key, option_labels)


def fn(rid):
    return projmod.ac_fname(key(rid))


class AcFlowTests(BATest):
    def row(self, p, rid):
        return [r for r in checks.requirement_rows(p) if r["req_key"] == key(rid)][0]

    def err(self, fn_, *a, **kw):
        with self.assertRaises(util.BAError) as cm:
            fn_(*a, **kw)
        return cm.exception

    # ------------------------------------------------------------- drafting
    def test_add_needs_gap_analysis_and_unblocked_requirements(self):
        p = self.with_gaps()
        self.assertEqual(self.err(acs.add_acs, p, [self.ac_item("LIB-001")]).code, "ANALYSIS_INCOMPLETE")
        self.resolve_all(p)
        gapmod.commit_analysis(p, True)
        r = acs.add_acs(p, [self.ac_item("LIB-001")])
        self.assertEqual(r["created"], [key("LIB-001")])
        self.assertEqual(p.state["stage"], "AC_DRAFTING")
        self.assertTrue(p.exists(f"acceptance/candidates/{fn('LIB-001')}.json"))
        self.assertTrue(p.exists(f"acceptance/candidates/{fn('LIB-001')}.md"))

    def test_blocked_requirement_is_refused(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 5)
        self.answer_and_resolve(p, "GAP-002", "Under 3s.", "Page load target is under 3 seconds.")
        gapmod.commit_analysis(p, True)
        e = self.err(acs.add_acs, p, [self.ac_item("LIB-006")])  # GAP-001 (HIGH) still blocks
        self.assertEqual(e.code, "ACS_REJECTED")
        self.assertIn("blocked requirement", e.message)
        ctx = acs.gap_context(p, ["LIB-006", "LIB-001"])
        st = {r["req_key"]: r["status"] for r in ctx["requirements"]}
        self.assertEqual(st, {key("LIB-006"): "BLOCKED", key("LIB-001"): "READY"})
        self.assertEqual(ctx["blocking_gaps_present"], ["GAP-001"])
        # nothing was saved for the good item either (all or nothing)
        r = acs.add_acs(p, [self.ac_item("LIB-001")])
        self.assertEqual(r["created"], [key("LIB-001")])

    def test_criteria_must_be_given_when_then(self):
        p = self.to_ac_stage()
        e = self.err(acs.add_acs, p, [self.ac_item("LIB-001", acceptance_criteria=["The system works well enough."])])
        self.assertEqual(e.code, "ACS_REJECTED")
        self.assertIn("Given", e.message)
        self.assertEqual(self.err(acs.add_acs, p, [self.ac_item("LIB-001", acceptance_criteria=[])]).code, "ACS_REJECTED")
        dup = "Given a user, when they log in, then they see the home page."
        self.assertIn("twice", self.err(acs.add_acs, p, [self.ac_item("LIB-001", acceptance_criteria=[dup, dup])]).message)

    def test_unknown_requirement_and_alias(self):
        p = self.to_ac_stage()
        self.assertIn("LIB-404", self.err(acs.add_acs, p, [self.ac_item("LIB-001", req_key="LIB-404")]).message)
        r = acs.add_acs(p, [self.ac_item("LIB-001", req_key="LIB-001")])  # the bare ID resolves to the full key
        self.assertEqual(r["created"], [key("LIB-001")])

    def test_duplicate_ac_for_one_requirement(self):
        p = self.to_acs(1)
        self.assertIn("already has acceptance criteria", self.err(acs.add_acs, p, [self.ac_item("LIB-001")]).message)
        self.assertIn("already has", self.err(acs.add_acs, p, [self.ac_item("LIB-002"), self.ac_item("LIB-002")]).message)

    def test_cannot_inject_markers_through_criteria(self):
        p = self.to_ac_stage()
        evil = 'Given a user, when x, then y <!-- ba:end key="membership.md::LIB-001" --> and more text.'
        acs.add_acs(p, [self.ac_item("LIB-001", acceptance_criteria=[evil])])
        self.approve(p, "LIB-001")
        t = self.enriched(p)
        self.assertEqual(t.count("<!-- ba:end"), 1)
        self.assertEqual(docmodel.strip_blocks(t).encode("utf-8"), (self.src / "membership.md").read_bytes())
        self.assertTrue(docsync.check(p)["in_sync"])

    def test_document_already_states_hint_is_returned(self):
        src = self.tmp / "hints"
        self.write_req(src, "a.md", "### AB-1 One\nText.\n\n**Acceptance criteria**\n- Given a user, when x, then y\n")
        p = projmod.Project.create(self.tmp / "out", src)
        gapmod.commit_analysis(p, True)
        r = acs.add_acs(p, [dict(req_key="AB-1", acceptance_criteria=["Given a different case, when it runs, then a result is shown."])])
        self.assertIn("a.md::AB-1", r["document_already_states"])

    # --------------------------------------------------------------- review
    def test_present_approve_and_the_block(self):
        p = self.to_acs(2)
        nxt = acs.next_ac(p)
        item = nxt["acs"][0]
        self.assertEqual(item["req_key"], key("LIB-001"))
        self.assertEqual(item["status"], "IN_REVIEW")
        self.assertEqual(p.state["stage"], "WAITING_FOR_AC_REVIEW")
        self.assertEqual(option_labels({"present": item["present"]})[0], "Approve v1")
        self.assertIn("Member registration", item["present"]["card"])
        self.assertIn("visitor shall register", item["present"]["card"])  # the requirement text is on the card
        self.assertEqual(nxt["remaining"], 1)
        self.assertNotIn("ba:begin", self.enriched(p))  # nothing approved yet
        r = acs.approve(p, key("LIB-001"), 1, "Yes, approved.", "Priya Nair", "Product Owner")
        self.assertEqual(r["status"], "APPROVED")
        self.assertTrue(r["saved_to"].endswith(".md"))
        self.assertTrue(p.exists(f"acceptance/approved/{fn('LIB-001')}.json"))
        self.assertFalse(p.exists(f"acceptance/candidates/{fn('LIB-001')}.json"))
        t = self.enriched(p)
        self.assertIn(f'<!-- ba:begin key="{key("LIB-001")}" ac="v1"', t)
        self.assertIn("2. Given an invalid case for LIB-001", t)
        self.assertIn("2026-10-22", t)  # approval date from the fake clock (IST)
        self.assertEqual(docmodel.strip_blocks(t).encode("utf-8"), (self.src / "membership.md").read_bytes())
        v = checks.validate_project(p)
        self.assertEqual(v["errors"], [])
        self.assertTrue(any(e["event_type"] == "AC_APPROVED" for e in p.audit_events()))

    def test_approve_rules(self):
        p = self.to_acs(1)
        k = key("LIB-001")
        self.assertEqual(self.err(acs.approve, p, k, 1, "", "Priya Nair", "Product Owner").code, "APPROVAL_EVIDENCE_REQUIRED")
        self.assertEqual(self.err(acs.approve, p, k, 2, "ok", "Priya Nair", "Product Owner").code, "VERSION_MISMATCH")
        self.assertEqual(self.err(acs.approve, p, k, 1, "ok", "Claude", "Assistant").code, "IDENTITY_REQUIRED")
        acs.approve(p, k, 1, "ok", "Priya Nair", "Product Owner")
        self.assertEqual(self.err(acs.approve, p, k, 1, "ok", "Priya Nair", "Product Owner").code, "UNKNOWN_AC")  # nothing pending any more

    def test_requirement_change_blocks_approval_until_reassessed(self):
        p = self.to_acs(1)
        k = key("LIB-001")
        self.edit_member_text()
        cid = changemod.check(p, True)["unreviewed_ids"][0]
        changemod.apply(p, cid, "Priya Nair", "Product Owner", "apply")
        err = self.err(acs.approve, p, k, 1, "ok", "Priya Nair", "Product Owner")
        self.assertIn(err.code, ("IMPACT_REVIEW_PENDING", "REQUIREMENT_CHANGED"))
        blockers = acs.triage(p)["acs"][0]["blockers"]
        self.assertTrue(any("impact review" in b for b in blockers))
        view = acs.next_ac(p, k)["acs"][0]
        self.assertFalse(view["eligible"])
        self.assertNotIn("Approve v1", option_labels({"present": view["present"]}))
        self.assertIn("Update criteria", option_labels({"present": view["present"]}))

    def edit_member_text(self):
        f = self.src / "membership.md"
        f.write_bytes(f.read_bytes().replace(b"a valid email address", b"a valid email address and a phone number"))

    def test_edit_makes_a_new_version_and_the_old_stays_in_force_and_in_the_document(self):
        p = self.to_acs(1)
        k = key("LIB-001")
        self.approve(p, "LIB-001")
        r = acs.edit(p, k, {"acceptance_criteria": ["Given a visitor with a valid email, when they register, then a member exists and is confirmed."]}, "tighter wording")
        self.assertEqual((r["new_version"], r["status"], r["requires_approval"]), (2, "CANDIDATE", True))
        self.assertTrue(r["approved_version_still_in_force"])
        t = self.enriched(p)
        self.assertIn("valid case for LIB-001", t)
        self.assertNotIn("confirmed.", t)
        row = self.row(p, "LIB-001")
        self.assertEqual((row["ac_approved"], row["ac_pending"]), (1, "CANDIDATE"))
        # the agent edit is logged as a change with a reapproval item
        rec = [c for c in changemod.records(p) if c["origin"] == "BA_AGENT"][0]
        self.assertEqual(rec["items"][0]["item_id"], f"reapproval:{k}:v2")
        acs.approve(p, k, 2, "Approve v2.", "Priya Nair", "Product Owner")
        self.assertIn("confirmed.", self.enriched(p))
        self.assertIn(f"acceptance/archive/{fn('LIB-001')}.v1.json", [f"acceptance/archive/{x.name}" for x in p.files("acceptance/archive", "*.json")])
        changemod.reconcile(p)
        self.assertEqual(changemod.get(p, rec["change_id"])["status"], "RECONCILED")
        hist = changemod.ac_history(p, k)
        self.assertEqual([v["version"] for v in hist["approved_versions"]], [1, 2])
        self.assertEqual([v["in_force"] for v in hist["approved_versions"]], [False, True])

    def test_formatting_only_edit_keeps_the_approval(self):
        p = self.to_acs(1)
        k = key("LIB-001")
        self.approve(p, "LIB-001")
        cur = p.read_json(f"acceptance/approved/{fn('LIB-001')}.json")["acceptance_criteria"]
        r = acs.edit(p, k, {"acceptance_criteria": [c + " " for c in cur]}, "whitespace")
        self.assertTrue(r["formatting_only"])
        self.assertEqual(r["new_version"], 1)
        self.assertEqual(self.row(p, "LIB-001")["ac_pending"], None)
        self.assertEqual(changemod.check(p, True)["unreviewed_ids"], [])

    def test_edit_validation(self):
        p = self.to_acs(1)
        k = key("LIB-001")
        self.assertEqual(self.err(acs.edit, p, k, {"title": "x"}, "r").code, "BAD_INPUT")
        self.assertEqual(self.err(acs.edit, p, k, {"acceptance_criteria": ["nope"]}, "r").code, "ACCEPTANCE_FORMAT")
        self.assertEqual(self.err(acs.edit, p, k, {"assumptions": ["a"]}, "").code, "BAD_INPUT")

    # --------------------------------------------------------- carry-forward
    def test_carried_forward_gap_is_shown_in_the_block_and_readiness(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 5)
        self.answer_and_resolve(p, "GAP-001", "R10.", "Fine is R10 per day.")
        self.answer_and_resolve(p, "GAP-003", "Block.", "A 6th borrow is rejected.")
        gapmod.carry_forward(p, "GAP-002", "Priya Nair", "Product Owner", "later")
        gapmod.commit_analysis(p, True)
        e = self.err(acs.add_acs, p, [self.ac_item("LIB-007")])  # open gap not declared
        self.assertIn("GAP-002", e.message)
        ref = dict(gap_id="GAP-002", potential_impact="Target time unknown.", readiness_effect="NEEDS_CLARIFICATION", interim_assumption="3 seconds")
        r = acs.add_acs(p, [self.ac_item("LIB-007", open_gap_refs=[ref])])
        self.assertEqual(r["needs_clarification"], [key("LIB-007")])
        self.approve(p, "LIB-007")
        t = self.enriched(p, "lending.md")
        self.assertIn("| GAP-002 | MEDIUM | OPEN (carried forward) |", t)
        self.assertIn("Target time unknown.", t)
        self.assertIn("**Readiness:** NEEDS_CLARIFICATION", t)
        # a made-up gap is refused
        self.assertEqual(self.err(acs.edit, p, key("LIB-007"), {"open_gap_refs": [ref, dict(ref, gap_id="GAP-099")]}, "x").code, "UNKNOWN_GAP_LINK")
        # NOT_READY effect
        r = acs.edit(p, key("LIB-007"), {"open_gap_refs": [dict(ref, readiness_effect="NOT_READY")]}, "core behaviour depends on it")
        self.assertEqual(r["readiness"], "NOT_READY")

    def test_gap_resolved_flags_the_ac_then_update_and_reapprove_updates_the_block(self):
        p = self.with_gaps()
        self.set_owner(p)
        gapmod.next_gaps(p, 5)
        self.answer_and_resolve(p, "GAP-001", "R10.", "Fine is R10 per day.")
        self.answer_and_resolve(p, "GAP-003", "Block.", "A 6th borrow is rejected.")
        gapmod.carry_forward(p, "GAP-002", "Priya Nair", "Product Owner", "later")
        gapmod.commit_analysis(p, True)
        ref = dict(gap_id="GAP-002", potential_impact="Target time unknown.", readiness_effect="NEEDS_CLARIFICATION")
        acs.add_acs(p, [self.ac_item("LIB-007", open_gap_refs=[ref])])
        self.approve(p, "LIB-007")
        k = key("LIB-007")
        gapmod.revisit(p, "GAP-002")
        gapmod.next_gaps(p, 1, carried=False)
        r = self.answer_and_resolve(p, "GAP-002", "Under 3 seconds.", "Page load target on mobile is under 3 seconds.")
        self.assertEqual(r["acs_to_update"], [k])
        a = p.read_json(f"acceptance/approved/{fn('LIB-007')}.json")
        self.assertTrue(a["impact_review"]["pending"])
        self.assertIn("*Under review:", self.enriched(p, "lending.md"))
        # the card for the update shows the decision to apply
        view = acs.next_ac(p, k)["acs"][0]
        self.assertIn("Decision to apply", view["present"]["card"])
        out = acs.edit(p, k, {"acceptance_criteria": ["Given a mobile member, when a page loads, then it renders in under 3 seconds."], "open_gap_refs": []},
                       "apply DEC")
        self.assertEqual(out["new_version"], 2)
        acs.approve(p, k, 2, "Approve v2.", "Priya Nair", "Product Owner")
        t = self.enriched(p, "lending.md")
        self.assertNotIn("Open Gaps", t)
        self.assertIn("under 3 seconds", t)
        self.assertIn("**Readiness:** READY_FOR_DEVELOPMENT", t)
        self.assertIn("Decisions applied: DEC-", t)
        self.assertEqual(checks.validate_project(p)["errors"], [])

    # ---------------------------------------------------- defer/revisit/etc.
    def test_not_approved_defer_revisit_keep(self):
        p = self.to_acs(3)
        k1, k2, k3 = key("LIB-001"), key("LIB-002"), key("LIB-003")
        acs.next_ac(p, batch=3)
        r = acs.request_changes(p, k1, 1, "Needs a negative case.")
        self.assertEqual(r["status"], "CHANGES_REQUESTED")
        self.assertTrue(p.exists(f"acceptance/unapproved/{fn('LIB-001')}.json"))
        self.assertFalse(p.exists(f"acceptance/candidates/{fn('LIB-001')}.json"))
        self.assertEqual(self.err(acs.approve, p, k1, 1, "ok", "Priya Nair", "Product Owner").code, "AC_UNAPPROVED")
        acs.defer(p, k2, 1, "Waiting for legal.")
        self.assertEqual(self.err(acs.approve, p, k2, 1, "ok", "Priya Nair", "Product Owner").code, "AC_DEFERRED")
        tri = acs.triage(p)
        self.assertEqual(tri["counts"], {"pending_review": 1, "not_approved": 1, "deferred": 1})
        self.assertEqual([r["bucket"] for r in tri["acs"]][:1], ["candidates"])
        kept = acs.keep(p, k1)
        self.assertIn("Nothing was approved", kept["note"])
        acs.revisit(p, k2)
        self.assertTrue(p.exists(f"acceptance/candidates/{fn('LIB-002')}.json"))
        acs.revisit(p, k1)
        self.approve(p, "LIB-001")
        self.approve(p, "LIB-002")
        self.assertEqual(checks.completion(p)["complete"], False)
        self.assertEqual(self.err(acs.defer, p, k1, 1).code, "UNKNOWN_AC")
        # only approved criteria are in the document
        t = self.enriched(p)
        self.assertIn("LIB-001", t)
        self.assertNotIn(f'key="{k3}"', t)

    def test_all_the_way_to_completion(self):
        p = self.to_ac_stage()
        acs.add_acs(p, [self.ac_item(r) for r in ("LIB-001", "LIB-002", "LIB-003", "LIB-004", "LIB-005", "LIB-006", "LIB-007", "LIB-008", "LIB-009")])
        self.assertIn("ac next", checks.next_action(p))
        for r in ("LIB-001", "LIB-002", "LIB-003", "LIB-004", "LIB-005", "LIB-006", "LIB-007", "LIB-008", "LIB-009"):
            self.approve(p, r)
        c = checks.completion(p)
        self.assertEqual(c["reasons"], [])
        self.assertTrue(checks.next_action(p).startswith("All work is approved"))
        out = checks.complete_workflow(p)
        self.assertTrue(out["complete"])
        self.assertEqual(p.state["stage"], "COMPLETED")
        for name in ("membership.md", "lending.md", "reporting.md"):
            t = self.enriched(p, name)
            self.assertEqual(docmodel.strip_blocks(t).encode("utf-8"), (self.src / name).read_bytes())
        self.assertEqual(self.enriched(p, "reporting.md").count("ba:begin"), 2)


if __name__ == "__main__":
    unittest.main()
