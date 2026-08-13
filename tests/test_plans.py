"""Plan and step tests — SPEC-003 §4.1-4.3 and §5.

Two things carry the weight here.

`produced` (§4.2) is what separates a plan from a todo list: a step records the
graph nodes it created, so a successor can see that step 2's output is already
in the graph rather than repeating it. A step that claims an output which is not
in the graph is therefore refused at write, because that claim is exactly the
lie that costs a successor the work.

`blocked_reason` (§5) is an enum because the correct successor behaviour differs
by cause. `refusal` is the row that pays for it: a fresh session of the same
model will refuse again, so "retry as written" is the one thing that must not
happen.
"""

import os
import tempfile
import unittest

from reckon import api, mcp, redact, store
from reckon.model import BLOCKED_IMPLICATION, BLOCKED_REASONS


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = store.ENGAGEMENTS
        store.ENGAGEMENTS = self.tmp
        api.create("t")
        api.add_node("t", "host", "lab07", node_id="host:lab07",
                     epistemic="verified")
        api.add_node("t", "objective", "shadow-cred to DA", node_id="obj:t21",
                     requires=["host:lab07@3"])
        api.add_node("t", "objective", "exfil the corpus", node_id="obj:t22")

    def tearDown(self):
        store.ENGAGEMENTS = self._old
        os.environ.pop("RECKON_AGENT", None)

    def plan(self, objective="obj:t21", steps=None):
        return api.plan_add("t", objective, "shadow-cred to DA",
                            steps=steps or ["dump hives on lab07",
                                            "extract DCC2 for j.rivera",
                                            "msDS-KeyCredentialLink",
                                            "PKINIT -> DA"])


class TestShape(Base):

    def test_steps_are_ordered_and_addressable_by_ordinal(self):
        pid = self.plan()
        p = api.active_plan("t", "obj:t21")
        self.assertEqual([s.ordinal for s in p.steps], [1, 2, 3, 4])
        self.assertEqual(p.steps[1].text, "extract DCC2 for j.rivera")
        self.assertEqual(p.steps[1].id, f"{pid}#2")

    def test_a_plan_needs_a_real_objective_of_the_right_kind(self):
        with self.assertRaises(api.ValidationError):
            api.plan_add("t", "obj:NOPE", "x")
        with self.assertRaises(api.ValidationError) as cm:
            api.plan_add("t", "host:lab07", "x")
        self.assertIn("not an objective", str(cm.exception))

    def test_a_second_active_plan_on_one_objective_is_refused(self):
        first = self.plan()
        with self.assertRaises(api.ValidationError) as cm:
            api.plan_add("t", "obj:t21", "another way")
        self.assertIn(first, str(cm.exception))
        self.assertIn("supersede", str(cm.exception).lower())

    def test_plans_on_different_objectives_are_both_active(self):
        self.plan("obj:t21")
        api.plan_add("t", "obj:t22", "exfil path", steps=["stage", "pull"])
        self.assertEqual(len(api.active_plans("t")), 2)

    def test_appending_a_step_to_a_superseded_plan_is_refused(self):
        old = self.plan()
        api.plan_add("t", "obj:t21", "v2", steps=["a"], supersedes=old)
        with self.assertRaises(api.ValidationError):
            api.step_add("t", old, "one more")


class TestCursor(Base):
    """Where to resume. A running step wins over a later pending one, because a
    session that died mid-step leaves it running and that is the step a
    successor must re-verify rather than skip."""

    def test_cursor_is_the_first_pending_step_on_a_fresh_plan(self):
        pid = self.plan()
        self.assertEqual(api.active_plan("t", "obj:t21").cursor.ordinal, 1)

    def test_cursor_advances_past_done_steps(self):
        pid = self.plan()
        api.step_state("t", pid, 1, "done", produced=["host:lab07"])
        self.assertEqual(api.active_plan("t", "obj:t21").cursor.ordinal, 2)

    def test_a_running_step_is_the_cursor_not_the_next_pending_one(self):
        pid = self.plan()
        api.step_state("t", pid, 1, "done")
        api.step_state("t", pid, 2, "running")
        self.assertEqual(api.active_plan("t", "obj:t21").cursor.ordinal, 2)

    def test_a_blocked_step_is_the_cursor(self):
        pid = self.plan()
        api.step_state("t", pid, 1, "done")
        api.step_state("t", pid, 2, "done")
        api.step_state("t", pid, 3, "blocked", blocked_reason="refusal")
        cur = api.active_plan("t", "obj:t21").cursor
        self.assertEqual(cur.ordinal, 3)
        self.assertEqual(cur.blocked_reason, "refusal")

    def test_cursor_is_none_when_every_step_is_finished(self):
        pid = self.plan(steps=["a", "b"])
        api.step_state("t", pid, 1, "done")
        api.step_state("t", pid, 2, "skipped")
        self.assertIsNone(api.active_plan("t", "obj:t21").cursor)


class TestProduced(Base):
    """§9.2 — a step cannot claim an output that is not in the graph."""

    def test_produced_ids_must_resolve_to_live_nodes(self):
        pid = self.plan()
        with self.assertRaises(api.ValidationError) as cm:
            api.step_state("t", pid, 2, "done", produced=["cred:dcc2"])
        self.assertIn("cred:dcc2", str(cm.exception))

        api.add_node("t", "cred", "dcc2", node_id="cred:dcc2",
                     epistemic="verified")
        api.step_state("t", pid, 2, "done", produced=["cred:dcc2"])
        p = api.active_plan("t", "obj:t21")
        self.assertEqual(p.steps[1].produced, ["cred:dcc2"])

    def test_produced_accumulates_without_duplicating(self):
        pid = self.plan()
        api.add_node("t", "cred", "dcc2", node_id="cred:dcc2")
        api.add_node("t", "artifact", "sam", node_id="artifact:sam")
        api.step_state("t", pid, 1, "done", produced=["artifact:sam"])
        api.step_state("t", pid, 1, "done",
                       produced=["artifact:sam", "cred:dcc2"])
        self.assertEqual(api.active_plan("t", "obj:t21").steps[0].produced,
                         ["artifact:sam", "cred:dcc2"])

    def test_the_produced_node_is_the_real_graph_node_not_a_copy(self):
        """§4.2 — the plan references the graph; it never becomes a second copy."""
        pid = self.plan()
        api.add_node("t", "cred", "dcc2 : D3mo-P@ss-9", node_id="cred:dcc2",
                     epistemic="verified", confidence="A")
        api.step_state("t", pid, 2, "done", produced=["cred:dcc2"])
        g = store.load("t")
        nid = g.plans[pid].steps[1].produced[0]
        self.assertEqual(g.nodes[nid].epistemic, "verified")
        self.assertIn("D3mo-P@ss-9", g.nodes[nid].label)


class TestTypedBlockage(Base):
    """§9.3 — both directions of the blocked/reason coupling are refused."""

    def test_blocked_without_a_reason_is_refused(self):
        pid = self.plan()
        with self.assertRaises(api.ValidationError) as cm:
            api.step_state("t", pid, 3, "blocked")
        self.assertIn("refusal", str(cm.exception))

    def test_a_reason_without_blocked_is_refused(self):
        pid = self.plan()
        with self.assertRaises(api.ValidationError):
            api.step_state("t", pid, 3, "done", blocked_reason="refusal")

    def test_an_unknown_reason_is_refused(self):
        pid = self.plan()
        with self.assertRaises(api.ValidationError):
            api.step_state("t", pid, 3, "blocked", blocked_reason="tired")

    def test_every_reason_carries_an_actionable_implication(self):
        for reason in BLOCKED_REASONS:
            self.assertTrue(BLOCKED_IMPLICATION[reason].strip())
        self.assertIn("not retry", BLOCKED_IMPLICATION["refusal"].lower())

    def test_moving_off_blocked_clears_the_reason(self):
        pid = self.plan()
        api.step_state("t", pid, 3, "blocked", blocked_reason="timeout")
        api.step_state("t", pid, 3, "done")
        self.assertIsNone(api.active_plan("t", "obj:t21").steps[2].blocked_reason)


class TestSupersede(Base):
    """§9.4 — a superseded plan never resumes, but stays readable."""

    def test_superseded_plan_drops_out_of_active_but_keeps_its_steps(self):
        old = self.plan()
        api.step_state("t", old, 1, "done")
        api.step_state("t", old, 2, "blocked", blocked_reason="dependency")

        new = api.plan_add("t", "obj:t21", "different shape",
                           steps=["probe the sink"], supersedes=old)

        self.assertEqual([p.id for p in api.active_plans("t")], [new])
        self.assertEqual(api.active_plan("t", "obj:t21").id, new)

        g = store.load("t")
        self.assertEqual(g.plans[old].superseded_by, new)
        self.assertEqual(g.plans[old].steps[1].blocked_reason, "dependency")

    def test_superseding_twice_is_refused(self):
        old = self.plan()
        new = api.plan_add("t", "obj:t21", "v2", steps=["a"], supersedes=old)
        with self.assertRaises(api.ValidationError):
            api.plan_supersede("t", old, new)

    def test_a_plan_cannot_supersede_itself(self):
        pid = self.plan()
        with self.assertRaises(api.ValidationError):
            api.plan_supersede("t", pid, pid)


class TestAmbiguity(Base):
    """§9.10 in miniature — never pick a resume point arbitrarily."""

    def test_active_plan_raises_rather_than_choosing_between_two(self):
        self.plan("obj:t21")
        api.plan_add("t", "obj:t22", "exfil", steps=["a"])
        with self.assertRaises(api.AmbiguousHandoff) as cm:
            api.active_plan("t")
        self.assertIn("obj:t21", str(cm.exception))
        self.assertIn("obj:t22", str(cm.exception))

    def test_naming_the_objective_disambiguates(self):
        self.plan("obj:t21")
        api.plan_add("t", "obj:t22", "exfil", steps=["a"])
        self.assertEqual(api.active_plan("t", "obj:t22").objective, "obj:t22")

    def test_no_plans_is_none_not_an_error(self):
        self.assertIsNone(api.active_plan("t"))


class TestResolution(Base):

    def test_steps_resolve_by_ordinal_or_by_id(self):
        pid = self.plan()
        api.step_state("t", pid, 2, "running")
        api.step_state("t", pid, f"{pid}#3", "running")
        p = api.active_plan("t", "obj:t21")
        self.assertEqual(p.steps[1].status, "running")
        self.assertEqual(p.steps[2].status, "running")

    def test_an_out_of_range_ordinal_is_refused(self):
        pid = self.plan()
        with self.assertRaises(api.ValidationError) as cm:
            api.step_state("t", pid, 9, "done")
        self.assertIn("has 4", str(cm.exception))

    def test_an_unknown_plan_names_the_active_ones(self):
        pid = self.plan()
        with self.assertRaises(api.ValidationError) as cm:
            api.step_state("t", "plan:999", 1, "done")
        self.assertIn(pid, str(cm.exception))


class TestFoldIsLenient(Base):
    """Strict on write, lenient on read: junk in an old log must not stop it
    loading, even though the API would never have written it."""

    def test_step_state_for_an_unknown_plan_or_step_is_ignored(self):
        pid = self.plan()
        store.append("t", "step_state", {"plan_id": "plan:404", "step_id": "x",
                                         "status": "done"})
        store.append("t", "step_state", {"plan_id": pid, "step_id": "nope",
                                         "status": "done"})
        g = store.load("t")
        self.assertEqual(len(g.plans), 1)
        self.assertTrue(all(s.status == "pending" for s in g.plans[pid].steps))

    def test_step_add_for_an_unknown_plan_is_ignored(self):
        self.plan()
        store.append("t", "step_add", {"plan_id": "plan:404", "step_id": "x",
                                       "ordinal": 1, "text": "ghost"})
        self.assertEqual(len(store.load("t").plans), 1)


class TestAuthorship(Base):

    def test_the_agent_that_moved_a_step_is_recorded(self):
        pid = self.plan()
        api.step_state("t", pid, 1, "done", agent="a3")
        self.assertEqual(api.active_plan("t", "obj:t21").steps[0].by, "a3")


class TestRedaction(Base):

    def test_step_commands_and_notes_are_masked(self):
        pid = self.plan()
        api.step_add("t", pid, "register the runner",
                     command="gh auth login --with-token ghp_A1b2C3d4E5f6G7h8I9j0")
        api.step_state("t", pid, 5, "blocked", blocked_reason="operator",
                       note="stopped: token ghp_A1b2C3d4E5f6G7h8I9j0 was live")
        g = redact.redact_graph(store.load("t"))
        s = g.plans[pid].steps[4]
        self.assertNotIn("ghp_A1b2C3d4E5f6G7h8I9j0", s.command)
        self.assertNotIn("ghp_A1b2C3d4E5f6G7h8I9j0", s.note)

    def test_masking_never_touches_the_log(self):
        pid = self.plan()
        api.step_add("t", pid, "x", command="echo ghp_A1b2C3d4E5f6G7h8I9j0")
        redact.redact_graph(store.load("t"))
        self.assertIn("ghp_A1b2C3d4E5f6G7h8I9j0",
                      store.load("t").plans[pid].steps[4].command)


class TestSurfaces(Base):

    def test_mcp_exposes_plan_add_and_step_state(self):
        names = [t["name"] for t in mcp.TOOLS]
        self.assertIn("plan_add", names)
        self.assertIn("step_state", names)
        pid = mcp.dispatch("plan_add", {"engagement": "t", "objective": "obj:t21",
                                        "title": "via MCP", "steps": ["a", "b"]})
        mcp.dispatch("step_state", {"engagement": "t", "plan": pid, "step": "1",
                                    "status": "blocked",
                                    "blocked_reason": "context-exhausted"})
        self.assertEqual(api.active_plan("t", "obj:t21").cursor.blocked_reason,
                         "context-exhausted")

    def test_mcp_step_state_still_refuses_a_bad_produced_id(self):
        pid = self.plan()
        with self.assertRaises(api.ValidationError):
            mcp.dispatch("step_state", {"engagement": "t", "plan": pid,
                                        "step": "1", "status": "done",
                                        "produced": ["cred:ghost"]})

    def test_graph_serialises_plans(self):
        pid = self.plan()
        d = store.load("t").to_dict()
        self.assertEqual(len(d["plans"][pid]["steps"]), 4)


if __name__ == "__main__":
    unittest.main()
