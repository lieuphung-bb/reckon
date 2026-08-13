"""Handoff and fleet — SPEC-003 §7, §4.4, and the §9 acceptance criteria.

§9 says criteria 9 and 13 are the real ones and the rest are mechanics, so both
resume tests are written as the spec frames them: reconstruct the next action
from the brief ALONE, without reading the event log.

Everything else here defends one property — the brief must stand alone. Its
reader may be a different model with no history of this engagement, so an id it
cannot explain, a bare enum, or a reference to something that lived in the dead
session is a defect, not a cosmetic issue.
"""

import os
import re
import tempfile
import unittest

from reckon import api, mcp, store
from reckon.render.handoff import handoff as render, fleet as render_fleet


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = store.ENGAGEMENTS
        store.ENGAGEMENTS = self.tmp
        api.create("t")
        api.add_node("t", "host", "lab07", node_id="host:lab07",
                     epistemic="verified")
        api.add_edge("t", "operator:me", "grants-access-to", "host:lab07",
                     edge_id="e:op-lab07", epistemic="verified",
                     props={"rank": 3})
        api.add_node("t", "objective", "shadow-cred to DA", node_id="obj:t21",
                     crown=True, requires=["host:lab07@3"])
        api.add_node("t", "artifact", "sam-hive", node_id="artifact:sam-hive",
                     epistemic="verified")
        api.add_node("t", "cred", "dcc2 : D3mo-P@ss-77", node_id="cred:dcc2",
                     epistemic="verified", confidence="A")

    def tearDown(self):
        store.ENGAGEMENTS = self._old
        os.environ.pop("RECKON_AGENT", None)

    def blocked_plan(self, agent=None):
        """§9.1's shape: five steps, 1-2 done, 3 blocked on a refusal."""
        pid = api.plan_add("t", "obj:t21", "shadow-cred to DA", agent=agent,
                           steps=["dump hives on lab07",
                                  "extract DCC2 for j.rivera",
                                  "msDS-KeyCredentialLink",
                                  "PKINIT -> DA",
                                  "prove it"])
        api.step_state("t", pid, 1, "done", produced=["artifact:sam-hive"],
                       agent=agent)
        api.step_state("t", pid, 2, "done", produced=["cred:dcc2"], agent=agent)
        api.step_state("t", pid, 3, "blocked", blocked_reason="refusal",
                       note="declined to write the cred payload", agent=agent)
        return pid


class TestResumePoint(Base):
    """§9.1 — name the step, and print the implication rather than the enum."""

    def test_the_blocked_step_is_the_resume_point(self):
        self.blocked_plan()
        block = api.handoff("t")["resume"][0]
        self.assertEqual(block["cursor"]["ordinal"], 3)
        self.assertEqual(block["cursor"]["text"], "msDS-KeyCredentialLink")
        self.assertEqual(block["total_steps"], 5)

    def test_the_brief_prints_the_implication_not_the_bare_enum(self):
        self.blocked_plan()
        text = render(api.handoff("t"))
        self.assertIn("do NOT retry as written", text)
        self.assertIn("route to the other model", text)
        # The enum may appear, but never on its own without the consequence.
        reason_line = next(l for l in text.splitlines() if "blocked (" in l)
        self.assertGreater(len(reason_line), len("- status: blocked (refusal)"))

    def test_a_running_step_tells_the_successor_to_re_verify(self):
        """§8 — a session that died mid-step leaves it running, and the
        successor must not assume it completed."""
        pid = api.plan_add("t", "obj:t21", "v1", steps=["a", "b"])
        api.step_state("t", pid, 1, "running")
        text = render(api.handoff("t"))
        self.assertIn("Re-verify", text)
        self.assertIn("do not assume it completed", text)


class TestProducedIsCarried(Base):
    """§4.2 — the point of the field: a successor sees the output is already in
    the graph rather than repeating the step that made it."""

    def test_produced_nodes_are_named_with_their_real_values(self):
        self.blocked_plan()
        text = render(api.handoff("t"))
        self.assertIn("artifact:sam-hive", text)
        self.assertIn("dcc2 : D3mo-P@ss-77", text)
        self.assertIn("do not redo the steps that made them", text)

    def test_a_done_step_with_no_produced_and_no_note_warns(self):
        """§9.8 — the output is orphaned and a successor may redo it."""
        pid = api.plan_add("t", "obj:t21", "v1", steps=["a", "b"])
        api.step_state("t", pid, 1, "done")
        h = api.handoff("t")
        self.assertTrue(any("stranded" in w for w in h["resume"][0]["warnings"]))
        self.assertIn("stranded", render(h))

    def test_a_done_step_with_a_note_does_not_warn(self):
        pid = api.plan_add("t", "obj:t21", "v1", steps=["a", "b"])
        api.step_state("t", pid, 1, "done", note="nothing to record, dead end")
        self.assertEqual(api.handoff("t")["resume"][0]["warnings"], [])


class TestSelfContained(Base):
    """§9.6 — it names no id it does not also explain."""

    def test_every_node_id_in_the_brief_is_explained(self):
        self.blocked_plan()
        api.change("t", "host:lab07", "dropped /tmp/pyk.py",
                   revert_hint="rm /tmp/pyk.py")
        text = render(api.handoff("t"))
        # An id never stands alone. It may be introduced by its label
        # ("shadow-cred to DA (`obj:t21`)") or followed by it
        # ("`cred:dcc2` — dcc2 : D3mo-P@ss-77"), so the check is that the line
        # carrying it says something a reader can act on besides the id itself.
        for line in text.splitlines():
            for m in re.finditer(
                    r"`((?:host|cred|artifact|obj|objective|service):[\w\-.]+)`",
                    line):
                rest = line.replace(m.group(0), "").strip(" -—·*`")
                self.assertGreater(
                    len(rest), 8,
                    f"{m.group(1)} appears with nothing explaining it: {line!r}")

    def test_the_brief_explains_what_it_rests_on(self):
        api.add_node("t", "host", "modelsrv", node_id="host:modelsrv",
                     epistemic="hypothesized")
        api.add_edge("t", "host:lab07", "escalates-to", "host:modelsrv",
                     edge_id="e:lab-model", epistemic="hypothesized",
                     confidence="D", props={"rank": 3})
        api.add_node("t", "objective", "exfil", node_id="obj:t22",
                     requires=["host:modelsrv@3"])
        api.plan_add("t", "obj:t22", "key reuse", steps=["spray"])
        text = render(api.handoff("t", agent=None, all_agents=True))
        self.assertIn("Resting on", text)
        self.assertIn("lab07 —escalates-to→ modelsrv", text)

    def test_with_no_plan_the_brief_says_so_rather_than_going_silent(self):
        text = render(api.handoff("t"))
        self.assertIn("No active plan", text)
        self.assertIn("Next moves", text)

    def test_outstanding_changes_reach_the_brief_and_cleaning_removes_them(self):
        """§9.5's other half."""
        self.blocked_plan()
        cid = api.change("t", "host:lab07", "dropped /tmp/pyk.py",
                         revert_hint="rm /tmp/pyk.py")
        self.assertIn("rm /tmp/pyk.py", render(api.handoff("t")))
        api.mark_cleaned("t", cid)
        self.assertNotIn("rm /tmp/pyk.py", render(api.handoff("t")))


class TestStalePlan(Base):
    """§8 — a plan against an objective already won is not a resume point."""

    def test_an_achieved_objective_flags_the_plan_and_points_elsewhere(self):
        self.blocked_plan()
        api.set_objective("t", "obj:t21", "achieved")
        h = api.handoff("t")
        self.assertTrue(any("stale" in w for w in h["resume"][0]["warnings"]))
        self.assertIn("frontier", render(h))


class TestSuperseded(Base):
    """§9.4 — never the resume point, still readable."""

    def test_a_superseded_plan_is_not_the_resume_point(self):
        old = self.blocked_plan()
        new = api.plan_add("t", "obj:t21", "reframed", steps=["reframe"],
                           supersedes=old)
        h = api.handoff("t")
        self.assertEqual([b["plan"] for b in h["resume"]], [new])
        self.assertEqual(store.load("t").plans[old].steps[2].blocked_reason,
                         "refusal")


class TestPluralShape(Base):
    """§9.10 — three agents, three objectives."""

    def three_agents(self):
        self.blocked_plan(agent="a3")
        for i, agent in enumerate(("a1", "a2"), start=2):
            oid = f"obj:t2{i}"
            api.add_node("t", "objective", f"objective {i}", node_id=oid)
            pid = api.plan_add("t", oid, f"plan for {agent}", agent=agent,
                               steps=["one", "two"])
            api.step_state("t", pid, 1, "running", agent=agent)

    def test_no_agent_named_raises_rather_than_choosing(self):
        self.three_agents()
        with self.assertRaises(api.AmbiguousHandoff) as cm:
            api.handoff("t")
        for agent in ("a1", "a2", "a3"):
            self.assertIn(agent, str(cm.exception))

    def test_agent_returns_only_that_agents_resume_point(self):
        self.three_agents()
        h = api.handoff("t", agent="a3")
        self.assertEqual([b["agent"] for b in h["resume"]], ["a3"])
        self.assertEqual(h["resume"][0]["objective"], "obj:t21")

    def test_all_returns_every_active_plan(self):
        self.three_agents()
        h = api.handoff("t", all_agents=True)
        self.assertEqual(sorted(b["agent"] for b in h["resume"]),
                         ["a1", "a2", "a3"])

    def test_shared_sections_print_once_under_all(self):
        self.three_agents()
        text = render(api.handoff("t", all_agents=True))
        self.assertEqual(text.count("## Position"), 1)
        self.assertEqual(text.count("## Outstanding target changes (RoE)"), 1)
        self.assertEqual(text.count("### "), 3)


class TestFleet(Base):
    """§7.2 and §9.11."""

    def test_one_row_per_agent_with_cursor_and_last_authored_event(self):
        self.blocked_plan(agent="a3")
        rows = {r["agent"]: r for r in api.fleet("t")}
        self.assertIn("a3", rows)
        self.assertIn("msDS-KeyCredentialLink", rows["a3"]["cursor"])
        self.assertEqual(rows["a3"]["status"], "blocked")
        self.assertGreater(rows["a3"]["last_seq"], 0)

    def test_an_agent_holding_no_plan_is_idle_not_a_fault(self):
        self.blocked_plan(agent="a3")
        api.change("t", "host:lab07", "dropped a file", agent="a9")
        rows = {r["agent"]: r for r in api.fleet("t")}
        self.assertEqual(rows["a9"]["status"], "idle")
        self.assertIsNone(rows["a9"]["plan"])

    def test_stalled_stays_dark_until_claims_exist(self):
        """§9.11 defines STALLED against claim expiry (SPEC-002), which is not
        built. It must not be guessed from a running cursor alone — that would
        report every live agent as dead."""
        self.blocked_plan(agent="a3")
        rows = api.fleet("t")
        self.assertTrue(all(r["stalled"] is False for r in rows))
        self.assertTrue(all(r["claim"] is None for r in rows))
        self.assertIn("SPEC-002", render_fleet(rows))

    def test_fleet_with_no_agents_says_so(self):
        self.assertIn("no agents", render_fleet(api.fleet("t")))


class TestReassign(Base):
    """§9.12 — preserves step state and produced, leaves the cursor put."""

    def test_reassignment_preserves_everything_and_records_the_old_owner(self):
        pid = self.blocked_plan(agent="a3")
        before = api.handoff("t", agent="a3")["resume"][0]

        api.plan_reassign("t", pid, "a1", reason="a3 stalled at step 3")

        after = api.handoff("t", agent="a1")["resume"][0]
        self.assertEqual(after["plan"], pid)
        self.assertEqual(after["agent"], "a1")
        self.assertEqual(after["cursor"], before["cursor"])
        self.assertEqual(after["produced"], before["produced"])
        self.assertEqual(api.handoff("t", agent="a3")["resume"], [])

        ev = [e for e in store.read_events("t") if e["op"] == "plan_reassign"][0]
        self.assertEqual(ev["args"]["from_agent"], "a3")
        self.assertEqual(ev["args"]["to_agent"], "a1")
        self.assertEqual(ev["args"]["reason"], "a3 stalled at step 3")

    def test_reassign_needs_a_target_agent(self):
        pid = self.blocked_plan(agent="a3")
        with self.assertRaises(api.ValidationError):
            api.plan_reassign("t", pid, "")

    def test_reassigning_a_superseded_plan_is_refused(self):
        old = self.blocked_plan(agent="a3")
        api.plan_add("t", "obj:t21", "v2", steps=["a"], supersedes=old)
        with self.assertRaises(api.ValidationError):
            api.plan_reassign("t", old, "a1")


class TestResumeFromTheBriefAlone(Base):
    """§9.9 and §9.13 — the two criteria the spec calls the real ones."""

    def test_single_session_resume(self):
        """§9.9: kill a session mid-plan; from the brief alone, reconstruct the
        next action without reading the event log."""
        self.blocked_plan(agent="a3")
        api.change("t", "host:lab07", "dropped /tmp/pyk.py",
                   revert_hint="rm /tmp/pyk.py")
        brief = render(api.handoff("t", agent="a3"))

        # Everything a successor needs, present in one document.
        self.assertIn("msDS-KeyCredentialLink", brief)          # where we are
        self.assertIn("3 of 5", brief)                          # how far in
        self.assertIn("do NOT retry as written", brief)         # what to do
        self.assertIn("cred:dcc2", brief)                       # what exists
        self.assertIn("declined to write the cred payload", brief)   # why
        self.assertIn("rm /tmp/pyk.py", brief)                  # what we owe
        # ...and nothing pointing back at a session that is gone.
        for dangling in ("see above", "as discussed", "earlier in", "scrollback"):
            self.assertNotIn(dangling, brief.lower())

    def test_fleet_resume_of_one_agent_leaves_the_other_two_alone(self):
        """§9.13: kill one of three agents mid-step; a replacement continues
        that plan from fleet + handoff --agent, touching nothing else."""
        self.blocked_plan(agent="a3")
        others = {}
        for i, agent in enumerate(("a1", "a2"), start=2):
            oid = f"obj:t2{i}"
            api.add_node("t", "objective", f"objective {i}", node_id=oid)
            pid = api.plan_add("t", oid, f"plan for {agent}", agent=agent,
                               steps=["one", "two"])
            api.step_state("t", pid, 1, "running", agent=agent)
            others[agent] = api.handoff("t", agent=agent)["resume"][0]

        # The operator sees three rows and picks the one that needs a decision.
        rows = {r["agent"]: r for r in api.fleet("t")}
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows["a3"]["cursor_status"], "blocked")

        # A replacement takes over exactly that plan.
        api.plan_reassign("t", rows["a3"]["plan"], "a4", reason="a3 died")
        took_over = api.handoff("t", agent="a4")["resume"]
        self.assertEqual(len(took_over), 1)
        self.assertEqual(took_over[0]["cursor"]["ordinal"], 3)
        self.assertEqual(len(took_over[0]["produced"]), 2)

        # The other two are untouched — same plan, same cursor, same owner.
        for agent, before in others.items():
            self.assertEqual(api.handoff("t", agent=agent)["resume"][0], before)


class TestRedaction(Base):
    """§9.7 — masks secrets in step commands, notes and revert hints."""

    def test_secrets_in_commands_notes_and_revert_hints_are_masked(self):
        from reckon.redact import redact_obj, PLACEHOLDER
        tok = "ghp_A1b2C3d4E5f6G7h8I9j0"
        pid = api.plan_add("t", "obj:t21", "v1", steps=["a"])
        api.step_add("t", pid, "register", command=f"gh auth login --with-token {tok}")
        api.step_state("t", pid, 2, "blocked", blocked_reason="operator",
                       note=f"stopped, {tok} was live")
        api.change("t", "host:lab07", "registered a runner",
                   revert_hint=f"curl -H 'Authorization: {tok}' -X DELETE /r")

        clear = render(api.handoff("t"))
        self.assertIn(tok, clear)

        masked = render(redact_obj(api.handoff("t")))
        self.assertNotIn(tok, masked)
        self.assertIn(PLACEHOLDER, masked)

    def test_redacting_the_brief_never_touches_the_log(self):
        from reckon.redact import redact_obj
        tok = "ghp_A1b2C3d4E5f6G7h8I9j0"
        pid = api.plan_add("t", "obj:t21", "v1", steps=["a"])
        api.step_add("t", pid, "x", command=f"echo {tok}")
        redact_obj(api.handoff("t"))
        self.assertIn(tok, store.load("t").plans[pid].steps[1].command)


class TestSurfaces(Base):

    def test_mcp_exposes_handoff_and_tells_an_agent_to_call_it_first(self):
        tool = next(t for t in mcp.TOOLS if t["name"] == "handoff")
        self.assertIn("FIRST", tool["description"])
        self.blocked_plan(agent="a3")
        out = mcp.dispatch("handoff", {"engagement": "t", "agent": "a3"})
        self.assertIn("msDS-KeyCredentialLink", out)

    def test_plan_reassign_is_not_exposed_over_mcp(self):
        """One agent taking over another's plan unprompted is how two agents
        end up running the same steps against one target."""
        self.assertNotIn("plan_reassign", [t["name"] for t in mcp.TOOLS])
        with self.assertRaises(api.ValidationError):
            mcp.dispatch("plan_reassign", {"engagement": "t"})


if __name__ == "__main__":
    unittest.main()
