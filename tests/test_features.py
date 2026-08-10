"""The five compass features.

Each exists to shrink what has to be read, or to stop something being lost
before it is read. The tests assert that property, not just that the code runs.
"""

import io
import json
import tempfile
import unittest

from reckon import api, mcp, recall, store
from reckon.model import fold, OPERATOR_ID
from reckon.queries import budget, delta, frontier


def ev(seq, op, **args):
    return {"seq": seq, "ts": "2026-08-08T00:00:00+00:00", "op": op, "args": args}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = store.ENGAGEMENTS
        store.ENGAGEMENTS = self.tmp
        api.create("t")

    def tearDown(self):
        store.ENGAGEMENTS = self._old


# --- 1. delta board -----------------------------------------------------------

class TestDelta(Base):

    def _setup_reachable_objective(self):
        api.add_node("t", "host", "dc01", node_id="host:dc01", epistemic="verified")
        api.add_node("t", "objective", "own dc01", node_id="obj:x",
                     requires=["host:dc01@3"])

    def test_delta_is_empty_when_nothing_happened(self):
        api.delta("t")                                  # creation event is news once
        self.assertEqual(api.delta("t")["events"], 0)

    def test_newly_winnable_is_surfaced(self):
        self._setup_reachable_objective()
        api.delta("t")                                  # stamp: seen up to here
        api.add_edge("t", OPERATOR_ID, "grants-access-to", "host:dc01",
                     epistemic="verified", props={"rank": 3})
        d = api.delta("t")
        self.assertEqual([o["id"] for o in d["newly_winnable"]], ["obj:x"])

    def test_bare_call_advances_the_marker_but_explicit_since_does_not(self):
        """Inspecting history must not disturb where you were up to."""
        self._setup_reachable_objective()
        api.delta("t")
        seen = api.last_seen("t")
        api.delta("t", since=0)
        self.assertEqual(api.last_seen("t"), seen)
        api.note("t", "host:dc01", "x")
        api.delta("t")
        self.assertGreater(api.last_seen("t"), seen)

    def test_delta_is_bounded_while_state_grows(self):
        """The compass property: output size does not track engagement size."""
        self._setup_reachable_objective()
        for i in range(200):
            api.add_node("t", "artifact", f"a{i}", node_id=f"artifact:a{i}")
        api.delta("t")
        api.note("t", "host:dc01", "one small thing")
        d = api.delta("t")
        self.assertEqual(len(d["new_nodes"]), 0)
        self.assertEqual(d["events"], 1)

    def test_resolved_hypotheses_are_reported(self):
        api.add_node("t", "host", "a", node_id="host:a", epistemic="hypothesized")
        api.delta("t")
        api.set_epistemic("t", "host:a", "verified")
        self.assertIn("host:a", [r["id"] for r in api.delta("t")["resolved"]])


# --- 2. decision log ----------------------------------------------------------

class TestDecisions(Base):

    def test_decision_records_choice_rejection_and_reason(self):
        api.decide("t", "pivot via .31", reason="box01 exhausted",
                   rejected=["brute box01", "BYOVD"])
        g = store.load("t")
        self.assertEqual(len(g.decisions), 1)
        self.assertEqual(g.decisions[0]["rejected"], ["brute box01", "BYOVD"])

    def test_decision_is_not_a_node(self):
        """A decision is about the engagement, not a thing in it — putting it in
        the access graph would pollute reachability."""
        api.decide("t", "x")
        g = store.load("t")
        self.assertFalse([n for n in g.nodes.values() if n.kind == "decision"])

    def test_empty_choice_refused(self):
        with self.assertRaises(api.ValidationError):
            api.decide("t", "")

    def test_decisions_appear_in_the_brief(self):
        from reckon.render.views import attack_brief
        api.decide("t", "pivot via .31", reason="box01 exhausted")
        self.assertIn("Decision log", attack_brief(store.load("t"), "t"))


# --- 4. failure budget --------------------------------------------------------

class TestBudget(Base):

    def setUp(self):
        super().setUp()
        api.add_node("t", "host", "box01", node_id="host:box01")

    def test_one_failure_does_not_blow_the_budget(self):
        api.attempt("t", "host:box01", "failed", "no local admin")
        self.assertEqual(budget(store.load("t")), [])

    def test_two_failures_blow_it(self):
        api.attempt("t", "host:box01", "failed", "no local admin")
        api.attempt("t", "host:box01", "failed", "BYOVD blocked")
        b = budget(store.load("t"))
        self.assertEqual([x["id"] for x in b], ["host:box01"])
        self.assertIn("re-scope", b[0]["advice"])

    def test_success_clears_it(self):
        for _ in range(3):
            api.attempt("t", "host:box01", "failed")
        api.attempt("t", "host:box01", "succeeded", "found the path")
        self.assertEqual(budget(store.load("t")), [])

    def test_attempts_work_on_edges_too(self):
        api.add_node("t", "cred", "c", node_id="cred:c")
        eid = api.add_edge("t", "cred:c", "grants-access-to", "host:box01")
        api.attempt("t", eid, "failed")
        api.attempt("t", eid, "failed")
        self.assertIn(eid, [x["id"] for x in budget(store.load("t"))])

    def test_unknown_target_refused(self):
        with self.assertRaises(api.ValidationError):
            api.attempt("t", "host:ghost", "failed")


# --- 5. technique recall ------------------------------------------------------

class TestRecall(Base):

    def test_recalls_a_technique_from_another_engagement(self):
        api.create("past")
        api.add_node("past", "host", "web", node_id="host:web",
                     props={"role": "nginx http portal"})
        api.add_node("past", "technique", "SQLi -> app_config",
                     node_id="technique:sqli")
        api.add_edge("past", "host:web", "applies-technique", "technique:sqli",
                     epistemic="verified")

        api.add_node("t", "host", "portal", node_id="host:portal",
                     props={"role": "http web portal"})
        hits = api.recall("t", "host:portal")
        self.assertIn("SQLi -> app_config", [h["technique"] for h in hits])
        self.assertTrue(hits[0]["confirmed"])

    def test_own_engagement_is_excluded(self):
        """Recall must answer from history, not echo the current engagement."""
        api.add_node("t", "host", "a", node_id="host:a", props={"role": "http"})
        api.add_node("t", "technique", "T", node_id="technique:t")
        api.add_edge("t", "host:a", "applies-technique", "technique:t",
                     epistemic="verified")
        self.assertEqual(api.recall("t", "host:a"), [])

    def test_refuted_techniques_are_not_recalled(self):
        api.create("past")
        api.add_node("past", "host", "web", node_id="host:web",
                     props={"role": "http"})
        api.add_node("past", "technique", "dead end", node_id="technique:d")
        api.add_edge("past", "host:web", "applies-technique", "technique:d",
                     epistemic="refuted")
        api.add_node("t", "host", "p", node_id="host:p", props={"role": "http"})
        self.assertEqual(api.recall("t", "host:p"), [])

    def test_suggestions_only_cover_what_is_reachable(self):
        """Suggesting a technique for an unreachable host is noise, and noise is
        what this tool exists to remove."""
        api.create("past")
        api.add_node("past", "host", "w", node_id="host:w", props={"role": "http"})
        api.add_node("past", "technique", "T", node_id="technique:t")
        api.add_edge("past", "host:w", "applies-technique", "technique:t",
                     epistemic="verified")
        api.add_node("t", "host", "far", node_id="host:far", props={"role": "http"})
        self.assertNotIn("host:far", api.suggestions("t"))


# --- 3. MCP server ------------------------------------------------------------

class TestMCP(Base):

    def _call(self, method, params=None, mid=1):
        return mcp.handle({"jsonrpc": "2.0", "id": mid, "method": method,
                           "params": params or {}})

    def test_initialize_advertises_tools(self):
        r = self._call("initialize")
        self.assertEqual(r["result"]["serverInfo"]["name"], "reckon")
        self.assertIn("tools", r["result"]["capabilities"])

    def test_tools_list_is_well_formed(self):
        tools = self._call("tools/list")["result"]["tools"]
        self.assertTrue(tools)
        for t in tools:
            self.assertIn("name", t)
            self.assertIn("description", t)
            self.assertEqual(t["inputSchema"]["type"], "object")
            for req in t["inputSchema"]["required"]:
                self.assertIn(req, t["inputSchema"]["properties"],
                              f"{t['name']}: required field not in properties")

    def test_write_then_read_round_trip(self):
        self._call("tools/call", {"name": "add_node", "arguments": {
            "engagement": "t", "kind": "host", "label": "dc01",
            "id": "host:dc01", "epistemic": "verified"}})
        out = self._call("tools/call", {"name": "status",
                                        "arguments": {"engagement": "t"}})
        payload = json.loads(out["result"]["content"][0]["text"])
        self.assertEqual(payload["engagement"], "t")
        self.assertIn("host:dc01", store.load("t").nodes)

    def test_tool_errors_are_reported_in_result_not_as_transport_failure(self):
        """The agent should see the message and correct itself, not have the
        transport fail underneath it."""
        out = self._call("tools/call", {"name": "set_state", "arguments": {
            "engagement": "t", "id": "host:TYPO", "state": "verified"}})
        self.assertTrue(out["result"]["isError"])
        self.assertIn("host:TYPO", out["result"]["content"][0]["text"])

    def test_notification_gets_no_response(self):
        self.assertIsNone(self._call("notifications/initialized", mid=None))

    def test_unknown_method_is_a_jsonrpc_error(self):
        self.assertEqual(self._call("nope")["error"]["code"], -32601)

    def test_serve_reads_stdio_line_by_line(self):
        stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1,
                                        "method": "tools/list"}) + "\n")
        stdout = io.StringIO()
        mcp.serve(stdin, stdout)
        self.assertIn("tools", json.loads(stdout.getvalue())["result"])

    def test_malformed_line_does_not_kill_the_server(self):
        stdin = io.StringIO("{not json\n" + json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        stdout = io.StringIO()
        mcp.serve(stdin, stdout)
        self.assertEqual(json.loads(stdout.getvalue())["id"], 2)


if __name__ == "__main__":
    unittest.main()
