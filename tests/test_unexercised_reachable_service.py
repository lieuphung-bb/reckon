"""A13 — unexercised-reachable-service: the SERVICE-grain unearned negative, one
level finer than A12/`unentered`. A service reachable through a foothold we
already hold, never touched, is a door on the floor we stand on.

Three independent guards gate this alarm (reaches-edge, not-entered, no
tested-against), so each QUIET case below is built to trip ONLY the guard it
names -- a case that trips several proves only that one still works.
"""

import unittest

from reckon.model import fold, OPERATOR_ID
from reckon.queries import unexercised_reachable_service, _service_host


def ev(seq, op, **args):
    return {"seq": seq, "ts": "2026-08-08T00:00:00+00:00", "op": op, "args": args}


class TestUnexercisedReachableService(unittest.TestCase):

    def _base(self, *extra):
        """A held foothold host reaching one service, never touched."""
        return fold([
            ev(1, "add_node", id=OPERATOR_ID, kind="operator", label="me"),
            ev(2, "add_node", id="host:foothold", kind="host", label="foothold",
               epistemic="verified", exploitation="acquired"),
            ev(3, "add_node", id="host:target", kind="host", label="target",
               epistemic="verified", exploitation="discovered"),
            ev(4, "add_node", id="service:internal-api", kind="service",
               label="internal api", epistemic="verified",
               exploitation="discovered", props={"host": "target"}),
            ev(5, "add_edge", id="e1", src="host:foothold", rel="reaches",
               dst="service:internal-api", epistemic="verified"),
            *extra,
        ])

    # --- FIRES ------------------------------------------------------------

    def test_fires_on_reachable_never_touched_service(self):
        rows = unexercised_reachable_service(self._base())
        self.assertEqual([r["service"] for r in rows], ["service:internal-api"])
        self.assertEqual(rows[0]["host"], "host:target")
        # The row must name WHICH access reaches it, not just that "access
        # already held" reaches it -- a Com that believes its channel is
        # refused reads the generic form as stale and dismisses the row.
        self.assertEqual(rows[0]["via"], ["host:foothold"])
        self.assertIn("foothold", rows[0]["why"])

    # --- QUIET, one guard each ---------------------------------------------

    def test_quiet_when_no_reaches_edge(self):
        # unexplored, no tested-against, but NOTHING reaches it -- a service on
        # the internet is not our business. Only the reaches-guard can catch this.
        g = fold([
            ev(1, "add_node", id=OPERATOR_ID, kind="operator", label="me"),
            ev(2, "add_node", id="service:stray", kind="service",
               label="stray service", epistemic="verified",
               exploitation="discovered"),
        ])
        self.assertEqual(unexercised_reachable_service(g), [])

    def test_quiet_when_tested_against_recorded(self):
        # reachable, unexplored, but an attempt (any outcome) earns the negative.
        # Only the tested-against-guard can catch this: reaches-edge present,
        # exploitation still "discovered".
        g = self._base(
            ev(6, "add_edge", id="ta1", src="host:foothold",
               rel="tested-against", dst="service:internal-api",
               epistemic="refuted"))
        self.assertEqual(unexercised_reachable_service(g), [])

    def test_quiet_when_already_entered(self):
        # reachable, no tested-against, but exploitation already past discovered.
        # Only the entered-guard can catch this.
        g = self._base(
            ev(6, "set_exploitation", id="service:internal-api",
               state="examined"))
        self.assertEqual(unexercised_reachable_service(g), [])

    def test_quiet_when_refuted_or_superseded(self):
        g_refuted = self._base(
            ev(6, "set_epistemic", id="service:internal-api", state="refuted"))
        self.assertEqual(unexercised_reachable_service(g_refuted), [])

        g_superseded = self._base(
            ev(6, "add_node", id="service:internal-api-old", kind="service",
               label="internal api (old)", epistemic="verified",
               exploitation="discovered"),
            ev(7, "add_edge", id="e2", src="host:foothold", rel="reaches",
               dst="service:internal-api-old", epistemic="verified"),
            ev(8, "supersede", old_id="service:internal-api-old",
               new_id="service:internal-api", reason="respawn"))
        self.assertNotIn("service:internal-api-old",
                          [r["service"] for r in unexercised_reachable_service(g_superseded)])


class TestServiceHostHoldsFallback(unittest.TestCase):
    """_service_host: the new incoming-`holds`-edge fallback, and proof the
    existing prop/requires paths are unchanged."""

    def test_resolves_via_holds_edge_only(self):
        g = fold([
            ev(1, "add_node", id="host:web01", kind="host", label="web01",
               epistemic="verified", exploitation="acquired"),
            ev(2, "add_node", id="service:app", kind="service", label="app",
               epistemic="verified"),
            ev(3, "add_edge", id="e1", src="host:web01", rel="holds",
               dst="service:app", epistemic="verified"),
        ])
        s = g.nodes["service:app"]
        self.assertEqual(_service_host(g, s), "host:web01")

    def test_requires_path_still_wins_over_holds_edge(self):
        # a service with BOTH a requires-target and a (different) holds-edge
        # source must still resolve via requires -- the old path wins first.
        g = fold([
            ev(1, "add_node", id="host:real", kind="host", label="real",
               epistemic="verified"),
            ev(2, "add_node", id="host:decoy", kind="host", label="decoy",
               epistemic="verified"),
            ev(3, "add_node", id="service:app", kind="service", label="app",
               epistemic="verified",
               props={"requires": [{"target": "host:real"}]}),
            ev(4, "add_edge", id="e1", src="host:decoy", rel="holds",
               dst="service:app", epistemic="verified"),
        ])
        s = g.nodes["service:app"]
        self.assertEqual(_service_host(g, s), "host:real")

    def test_props_host_path_still_wins_over_holds_edge(self):
        g = fold([
            ev(1, "add_node", id="host:10.0.0.5", kind="host", label="real",
               epistemic="verified"),
            ev(2, "add_node", id="host:decoy", kind="host", label="decoy",
               epistemic="verified"),
            ev(3, "add_node", id="service:app", kind="service", label="app",
               epistemic="verified", props={"host": "10.0.0.5"}),
            ev(4, "add_edge", id="e1", src="host:decoy", rel="holds",
               dst="service:app", epistemic="verified"),
        ])
        s = g.nodes["service:app"]
        self.assertEqual(_service_host(g, s), "host:10.0.0.5")


if __name__ == "__main__":
    unittest.main()
