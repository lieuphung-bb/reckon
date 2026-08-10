"""Acceptance tests, one per real engagement failure this tool exists to catch.

These are the go/no-go. If the queries do not surface what was actually missed
on real engagements, the model is wrong and the tool should be dropped.

Each fixture encodes ONLY what was known at the moment of the miss - no hindsight.
"""

import unittest

from reckon.model import fold, OPERATOR_ID
from reckon.queries import (frontier, unrealized, unmined, stale, coverage, why,
                         verification_queue, reach, reach_pareto)


def ev(seq, op, **args):
    return {"seq": seq, "ts": "2026-08-08T00:00:00+00:00", "op": op, "args": args}


class TestUnminedCredential(unittest.TestCase):
    """A credential written off as dead while a new exploit is chased — and the
    answer sitting in a repository nobody read. Acquisition is not examination."""

    def setUp(self):
        self.g = fold([
            ev(1, "add_node", id="host:box01", kind="host", label="box01",
               epistemic="verified", exploitation="exhausted"),
            # cred acquired early, never tried anywhere
            ev(2, "add_node", id="cred:analyst", kind="cred", label="analyst"),
            ev(3, "set_exploitation", id="cred:analyst", state="acquired"),
            # repos cloned but never read
            ev(4, "add_node", id="artifact:repo-dashboard", kind="artifact",
               label="dashboard repo"),
            ev(5, "set_exploitation", id="artifact:repo-dashboard", state="acquired"),
            ev(6, "add_node", id="artifact:sop-runbook", kind="artifact",
               label="SOP runbook"),
            ev(7, "set_exploitation", id="artifact:sop-runbook", state="acquired"),
            # an artifact we DID read
            ev(8, "add_node", id="artifact:web-config", kind="artifact",
               label="web config"),
            ev(9, "set_exploitation", id="artifact:web-config", state="acquired"),
            ev(20, "examine", id="artifact:web-config", outcome="nothing useful"),
        ])

    def test_unmined_surfaces_the_untried_credential(self):
        ids = [u["id"] for u in unmined(self.g)]
        self.assertIn("cred:analyst", ids)

    def test_unmined_surfaces_unread_artifacts(self):
        ids = [u["id"] for u in unmined(self.g)]
        self.assertIn("artifact:repo-dashboard", ids)
        self.assertIn("artifact:sop-runbook", ids)

    def test_examined_asset_is_not_flagged(self):
        ids = [u["id"] for u in unmined(self.g)]
        self.assertNotIn("artifact:web-config", ids)

    def test_oldest_held_ranks_first(self):
        out = unmined(self.g)
        self.assertEqual(out[0]["id"], "cred:analyst")

    def test_coverage_reports_the_unread_ratio(self):
        c = coverage(self.g)
        self.assertEqual(c["artifacts_total"], 3)
        self.assertEqual(c["artifacts_examined"], 1)


class TestUnrealizedObjective(unittest.TestCase):
    """An objective already unlocked — credential valid, endpoint answering — and
    never run, because nothing showed it was winnable."""

    def setUp(self):
        self.g = fold([
            ev(1, "add_node", id="cred:soc", kind="cred", label="soc",
               epistemic="verified", exploitation="acquired"),
            ev(2, "add_node", id="service:chat-api", kind="service",
               label=".85 /api/chat", epistemic="verified"),
            ev(3, "add_edge", id="e1", src=OPERATOR_ID, dst="cred:soc",
               rel="holds", epistemic="verified"),
            ev(4, "add_edge", id="e2", src="cred:soc", dst="service:chat-api",
               rel="grants-access-to", epistemic="verified",
               props={"rank": 1, "privilege": "authenticated"}),
            ev(5, "add_node", id="obj:ai-soc", kind="objective",
               label="AI SOC tool-abuse", status="open",
               props={"crown_jewel": True,
                      "requires": [{"target": "service:chat-api", "min_rank": 1}]}),
        ])

    def test_objective_is_reachable_now(self):
        f = frontier(self.g)
        self.assertIn("obj:ai-soc", [o["id"] for o in f["reachable_now"]])

    def test_unrealized_flags_it_as_winnable_but_open(self):
        u = unrealized(self.g)
        self.assertEqual([o["id"] for o in u], ["obj:ai-soc"])
        self.assertEqual(u[0]["status"], "open")

    def test_achieving_it_clears_the_alarm(self):
        g2 = fold(self.g.events + [ev(6, "set_objective", id="obj:ai-soc",
                                      status="achieved")])
        self.assertEqual(unrealized(g2), [])


class TestAccessHeldObjectiveOpen(unittest.TestCase):
    """Root on a host already held, task still open — a quick win left undone
    because nothing connected the access to the objective."""

    def setUp(self):
        self.g = fold([
            ev(1, "add_node", id="host:app01", kind="host",
               label="app01", epistemic="verified"),
            ev(2, "add_edge", id="e1", src=OPERATOR_ID, dst="host:app01",
               rel="grants-access-to", epistemic="verified",
               props={"rank": 3, "privilege": "root"}),
            ev(3, "add_node", id="obj:pubkey", kind="objective",
               label="plant pubkey", status="open",
               props={"requires": [{"target": "host:app01", "min_rank": 3}]}),
            # an objective we genuinely cannot reach yet
            ev(4, "add_node", id="host:dc02", kind="host", label="DC02"),
            ev(5, "add_node", id="obj:t22", kind="objective", label="T22 DA",
               status="open",
               props={"crown_jewel": True,
                      "requires": [{"target": "host:dc02", "min_rank": 3}]}),
        ])

    def test_held_access_surfaces_the_open_objective(self):
        self.assertIn("obj:pubkey", [o["id"] for o in unrealized(self.g)])

    def test_unreachable_objective_is_not_claimed_as_winnable(self):
        self.assertNotIn("obj:t22", [o["id"] for o in unrealized(self.g)])
        f = frontier(self.g)
        self.assertIn("obj:t22", [o["id"] for o in f["unreachable"]])

    def test_insufficient_privilege_does_not_satisfy(self):
        g2 = fold([e for e in self.g.events if e["seq"] != 2] +
                  [ev(9, "add_edge", id="e1", src=OPERATOR_ID,
                      dst="host:app01", rel="grants-access-to",
                      epistemic="verified", props={"rank": 1})])
        self.assertEqual(unrealized(g2), [])


class TestUnverifiedIdentity(unittest.TestCase):
    """Hours spent against an orphaned host — it sat on the working path with its
    identity never actually confirmed."""

    def setUp(self):
        self.g = fold([
            ev(1, "add_node", id="cred:aws", kind="cred", label="aws key",
               epistemic="verified"),
            ev(2, "add_edge", id="e1", src=OPERATOR_ID, dst="cred:aws",
               rel="holds", epistemic="verified"),
            # target assumed, never verified as THE live box
            ev(3, "add_node", id="host:ec2-target", kind="host",
               label="44.200.140.48", epistemic="hypothesized"),
            ev(4, "add_edge", id="e2", src="cred:aws", dst="host:ec2-target",
               rel="grants-access-to", epistemic="verified", props={"rank": 3}),
        ])

    def test_unverified_node_on_active_path_is_flagged(self):
        ids = [s["id"] for s in stale(self.g)]
        self.assertIn("host:ec2-target", ids)

    def test_verifying_it_clears_the_flag(self):
        g2 = fold(self.g.events + [ev(5, "set_epistemic", id="host:ec2-target",
                                      state="verified", confidence="A")])
        self.assertNotIn("host:ec2-target", [s["id"] for s in stale(g2)])


class TestTraversal(unittest.TestCase):
    """The Dijkstra core: verified edges are free, hypothesised edges cost 1."""

    def setUp(self):
        self.g = fold([
            ev(1, "add_node", id="cred:a", kind="cred", label="a"),
            ev(2, "add_node", id="host:b", kind="host", label="b"),
            ev(3, "add_node", id="host:c", kind="host", label="c"),
            ev(4, "add_edge", id="e1", src=OPERATOR_ID, dst="cred:a",
               rel="holds", epistemic="verified"),
            ev(5, "add_edge", id="e2", src="cred:a", dst="host:b",
               rel="grants-access-to", epistemic="verified", props={"rank": 1}),
            ev(6, "add_edge", id="e3", src="host:b", dst="host:c",
               rel="escalates-to", epistemic="hypothesized", props={"rank": 3}),
            ev(7, "add_node", id="obj:x", kind="objective", label="x", status="open",
               props={"requires": [{"target": "host:c", "min_rank": 3}]}),
        ])

    def test_verified_path_costs_nothing(self):
        self.assertEqual(reach(self.g)["host:b"]["cost"], 0)

    def test_hypothesised_edge_costs_one(self):
        self.assertEqual(reach(self.g)["host:c"]["cost"], 1)

    def test_objective_behind_a_hypothesis_is_conditional(self):
        f = frontier(self.g)
        self.assertEqual([o["id"] for o in f["reachable_if"]], ["obj:x"])
        self.assertEqual(f["reachable_if"][0]["assumptions"], ["e3"])

    def test_verifying_the_edge_promotes_it_to_reachable_now(self):
        g2 = fold(self.g.events + [ev(8, "set_epistemic", id="e3", state="verified")])
        self.assertIn("obj:x", [o["id"] for o in frontier(g2)["reachable_now"]])

    def test_refuted_edge_breaks_the_path(self):
        g2 = fold(self.g.events + [ev(8, "set_epistemic", id="e3", state="refuted")])
        self.assertIn("obj:x", [o["id"] for o in frontier(g2)["unreachable"]])

    def test_verification_queue_ranks_by_objectives_gated(self):
        q = verification_queue(self.g)
        self.assertEqual(q[0]["edge"], "e3")
        self.assertEqual(q[0]["gates"], 1)

    def test_why_explains_the_path(self):
        w = why(self.g, "obj:x")
        self.assertEqual(w["assumptions"], ["e3"])
        self.assertEqual([s["edge"] for s in w["steps"]], ["e1", "e2", "e3"])

    def test_privileged_conditional_path_survives_a_cheaper_weak_one(self):
        """A host is routinely reachable over the network at low privilege AND via
        an untested credential at high privilege. Keeping only the cheapest entry
        discarded the privileged route and reported such objectives as needing
        fresh discovery — the opposite of the truth, which is that they need one
        test. Found by the demo fixture, not by a unit test."""
        g = fold([
            ev(1, "add_node", id="host:box", kind="host", label="box"),
            ev(2, "add_node", id="cred:k", kind="cred", label="k"),
            # cheap but weak: network reach only
            ev(3, "add_edge", id="e:weak", src=OPERATOR_ID, dst="host:box",
               rel="grants-access-to", epistemic="verified", props={"rank": 1}),
            # expensive but strong: an untested credential
            ev(4, "add_edge", id="e:hold", src=OPERATOR_ID, dst="cred:k",
               rel="holds", epistemic="verified"),
            ev(5, "add_edge", id="e:strong", src="cred:k", dst="host:box",
               rel="grants-access-to", epistemic="hypothesized", props={"rank": 3}),
            ev(6, "add_node", id="obj:own", kind="objective", label="own box",
               status="open",
               props={"requires": [{"target": "host:box", "min_rank": 3}]}),
        ])
        f = frontier(g)
        self.assertIn("obj:own", [o["id"] for o in f["reachable_if"]])
        self.assertEqual(f["unreachable"], [])
        self.assertEqual(f["reachable_if"][0]["assumptions"], ["e:strong"])

    def test_display_reach_still_shows_what_you_have_now(self):
        """`reach` is the display view: the cheapest access, not the aspirational
        one. Requirement checking uses the pareto set instead."""
        g = fold([
            ev(1, "add_node", id="host:box", kind="host", label="box"),
            ev(2, "add_edge", id="e:weak", src=OPERATOR_ID, dst="host:box",
               rel="grants-access-to", epistemic="verified", props={"rank": 1}),
            ev(3, "add_edge", id="e:strong", src=OPERATOR_ID, dst="host:box",
               rel="grants-access-to", epistemic="hypothesized", props={"rank": 3}),
        ])
        self.assertEqual(reach(g)["host:box"]["cost"], 0)
        self.assertEqual(reach(g)["host:box"]["rank"], 1)
        self.assertEqual(len(reach_pareto(g)["host:box"]), 2)

    def test_contains_inherits_privilege(self):
        g2 = fold(self.g.events + [
            ev(8, "add_node", id="artifact:key", kind="artifact", label="id_rsa"),
            ev(9, "add_edge", id="e4", src="host:b", dst="artifact:key",
               rel="contains", epistemic="verified"),
        ])
        self.assertEqual(reach(g2)["artifact:key"]["rank"], 1)


if __name__ == "__main__":
    unittest.main()
