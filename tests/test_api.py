"""API, store and redaction tests — the production guarantees.

The theme is loudness. A tool whose whole purpose is catching what you missed
must never quietly miss: `reckon state host:TYPO verified` used to print success and
record nothing, because the event appended and the fold found no such node.
"""

import json
import os
import tempfile
import threading
import unittest

from reckon import api, redact, reference, store


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = store.ENGAGEMENTS
        store.ENGAGEMENTS = self.tmp
        api.create("t")

    def tearDown(self):
        store.ENGAGEMENTS = self._old


class TestValidation(Base):

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(api.ValidationError):
            api.add_node("t", "widget", "x")

    def test_unknown_state_is_refused(self):
        with self.assertRaises(api.ValidationError):
            api.add_node("t", "host", "x", epistemic="probably")

    def test_edge_to_unknown_node_is_refused(self):
        api.add_node("t", "host", "a", node_id="host:a")
        with self.assertRaises(api.ValidationError) as cm:
            api.add_edge("t", "host:a", "grants-access-to", "host:ghost")
        self.assertIn("host:ghost", str(cm.exception))

    def test_state_change_on_unknown_id_is_refused(self):
        """The regression that motivated the whole api layer."""
        with self.assertRaises(api.ValidationError):
            api.set_epistemic("t", "host:TYPO", "verified")

    def test_objective_status_on_a_non_objective_is_refused(self):
        api.add_node("t", "host", "a", node_id="host:a")
        with self.assertRaises(api.ValidationError):
            api.set_objective("t", "host:a", "achieved")

    def test_unknown_relation_is_refused(self):
        api.add_node("t", "host", "a", node_id="host:a")
        api.add_node("t", "host", "b", node_id="host:b")
        with self.assertRaises(api.ValidationError):
            api.add_edge("t", "host:a", "pwns", "host:b")

    def test_requires_parsing(self):
        self.assertEqual(api.parse_requires(["host:dc01@3"]),
                         [{"target": "host:dc01", "min_rank": 3}])
        with self.assertRaises(api.ValidationError):
            api.parse_requires(["host:dc01@high"])

    def test_valid_writes_still_work(self):
        api.add_node("t", "host", "a", node_id="host:a", epistemic="verified")
        api.add_node("t", "objective", "own a", node_id="obj:a",
                     requires=["host:a@0"], crown=True)
        api.add_edge("t", api.OPERATOR_ID if hasattr(api, "OPERATOR_ID")
                     else "operator:me", "grants-access-to", "host:a",
                     epistemic="verified")
        rep = api.status("t")
        self.assertEqual(rep["coverage"]["objectives_total"], 1)


class TestStore(Base):

    def test_concurrent_appends_do_not_collide(self):
        """Phase 2 adds an MCP server beside the CLI: two writers, one log."""
        def worker(n):
            for i in range(20):
                store.append("t", "note", {"target_id": "operator:me",
                                           "text": f"{n}-{i}"})
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        events = store.read_events("t")
        seqs = [e["seq"] for e in events]
        self.assertEqual(len(seqs), len(set(seqs)), "duplicate sequence numbers")
        self.assertEqual(seqs, sorted(seqs), "sequence numbers out of order")

    def test_batch_is_atomic_and_sequential(self):
        written = store.append_many("t", [{"op": "note", "args": {
            "target_id": "operator:me", "text": str(i)}} for i in range(50)])
        self.assertEqual([e["seq"] for e in written],
                         list(range(written[0]["seq"], written[0]["seq"] + 50)))

    def test_seq_is_read_from_the_tail(self):
        """Deriving seq by parsing the whole log made a session quadratic."""
        store.append_many("t", [{"op": "note", "args": {
            "target_id": "operator:me", "text": "x" * 50}} for _ in range(500)])
        path = store.path_for("t")
        self.assertGreater(os.path.getsize(path), store._TAIL_BYTES)
        self.assertEqual(store._last_seq(path), 501)     # +1 for the create note

    def test_schema_from_the_future_fails_loudly(self):
        with open(store.path_for("t"), "a") as fh:
            fh.write(json.dumps({"seq": 999, "v": store.SCHEMA_VERSION + 1,
                                 "op": "note", "args": {}}) + "\n")
        with self.assertRaises(store.SchemaTooNew):
            store.read_events("t")

    def test_corrupt_line_names_the_line(self):
        with open(store.path_for("t"), "a") as fh:
            fh.write("{not json\n")
        with self.assertRaises(store.StoreError) as cm:
            store.read_events("t")
        self.assertIn(":2:", str(cm.exception))

    def test_dangerous_engagement_names_refused(self):
        for bad in ("../escape", "a/b", "", ".hidden"):
            with self.assertRaises(store.StoreError):
                store.path_for(bad)


class TestRedaction(Base):

    def test_known_token_shapes_are_masked(self):
        for secret in ("glpat-Dem0Fixture0123456", "ghp_abcdefghij0123456789",
                       "AKIAIOSFODNN7EXAMPLE", "hf_abcdefghijklmnop12345"):
            self.assertNotIn(secret, redact.redact_text(f"token {secret} here"))

    def test_user_secret_pairs_lose_only_the_secret(self):
        out = redact.redact_text("Administrator : D3mo-P@ss-99")
        self.assertIn("Administrator", out)
        self.assertNotIn("D3mo-P@ss-99", out)

    def test_urls_are_not_mangled(self):
        url = "https://intel.meetless.ai/v1/ask"
        self.assertIn("intel.meetless.ai", redact.redact_text(url))

    def test_secrets_slugged_into_ids_are_masked_too(self):
        """The importer slugs a credential label into its id, and ids print in
        data-n, the drawer and the graph - the one field masking used to skip."""
        api.add_node("t", "cred", "deploy / glpat-Dem0Fixture0123456",
                     node_id="cred:deploy-glpat-dem0fixture0123456")
        g = redact.redact_graph(store.load("t"))
        self.assertFalse([i for i in g.nodes if "glpat" in i.lower()])

    def test_id_remap_keeps_edges_resolvable(self):
        """Remapping ids without fixing edge endpoints would break every click."""
        api.add_node("t", "host", "a", node_id="host:a")
        api.add_node("t", "cred", "x", node_id="cred:glpat-Dem0Fixture0123456")
        api.add_edge("t", "cred:glpat-Dem0Fixture0123456", "grants-access-to",
                     "host:a")
        g = redact.redact_graph(store.load("t"))
        for e in g.edges.values():
            self.assertIn(e.src, g.nodes, "edge src dangling after redaction")
            self.assertIn(e.dst, g.nodes, "edge dst dangling after redaction")

    def test_redaction_never_touches_the_event_log(self):
        api.add_node("t", "cred", "admin:hunter2please", node_id="cred:a")
        g = redact.redact_graph(store.load("t"))
        self.assertNotIn("hunter2please", g.nodes["cred:a"].label)
        with open(store.path_for("t")) as fh:
            raw = fh.read()
        self.assertIn("hunter2please", raw)      # history keeps the truth


class TestReferenceSeam(Base):

    def test_reference_validates_store_and_label(self):
        self.assertEqual(reference.make_reference("neo4j", "CVE", "CVE-2025-1")["key"],
                         "CVE-2025-1")
        with self.assertRaises(reference.ReferenceError):
            reference.make_reference("mysql", "CVE", "x")
        with self.assertRaises(reference.ReferenceError):
            reference.make_reference("neo4j", "ATLAS", "x")   # not in the schema

    def test_add_reference_attaches_to_a_node(self):
        api.add_node("t", "finding", "rce", node_id="finding:rce")
        api.add_reference("t", "finding:rce", "neo4j", "CVE", "CVE-2023-46604")
        g = store.load("t")
        self.assertEqual(g.nodes["finding:rce"].props["references"][0]["key"],
                         "CVE-2023-46604")

    def test_null_resolver_satisfies_the_protocol(self):
        r = reference.NullResolver()
        self.assertIsInstance(r, reference.Resolver)
        self.assertEqual(r.resolve("neo4j", "CVE", "x"), {})

    def test_retrieval_hits_enter_as_low_confidence_hypotheses(self):
        """A cosine distance is not evidence. Chroma output must never arrive
        verified, or a vector search promotes itself into the plan."""
        evs = reference.retrieval_to_events(
            [{"text": "abuse the deploy key", "score": 0.91, "source": "kb"}],
            about="host:a")
        nodes = [e for e in evs if e["op"] == "add_node"]
        self.assertTrue(nodes)
        for n in nodes:
            self.assertEqual(n["args"]["epistemic"], "hypothesized")
            self.assertEqual(n["args"]["confidence"], reference.RETRIEVAL_CONFIDENCE)
        for e in evs:
            self.assertNotEqual(e["args"].get("epistemic"), "verified")


if __name__ == "__main__":
    unittest.main()
