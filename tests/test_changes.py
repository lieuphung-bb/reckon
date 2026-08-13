"""Change ledger tests — SPEC-003 §3.

The ledger is the one part of handoff that is also a deliverable: it is what a
successor must not re-do AND what is owed at close under the rules of engagement.
Both lists are kept by hand in real workspaces, which is exactly how they drift
apart, so the guarantees tested here are that an entry cannot be recorded against
a node that does not exist, and cannot quietly vanish once recorded.
"""

import json
import os
import tempfile
import unittest

from reckon import api, mcp, redact, store
from reckon.model import fold


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = store.ENGAGEMENTS
        store.ENGAGEMENTS = self.tmp
        api.create("t")
        api.add_node("t", "host", "lab07", node_id="host:lab07")

    def tearDown(self):
        store.ENGAGEMENTS = self._old
        os.environ.pop("RECKON_AGENT", None)


class TestWrite(Base):

    def test_change_returns_a_usable_id(self):
        cid = api.change("t", "host:lab07", "dropped /tmp/pyk.py",
                         revert_hint="rm /tmp/pyk.py")
        self.assertTrue(cid.startswith("chg:"))
        rows = api.changes("t")
        self.assertEqual([r["id"] for r in rows], [cid])
        self.assertEqual(rows[0]["revert_hint"], "rm /tmp/pyk.py")
        self.assertTrue(rows[0]["reversible"])

    def test_change_against_unknown_node_is_refused(self):
        with self.assertRaises(api.ValidationError) as cm:
            api.change("t", "host:TYPO", "dropped a file")
        self.assertIn("host:TYPO", str(cm.exception))

    def test_change_without_what_is_refused(self):
        for empty in ("", "   ", None):
            with self.assertRaises(api.ValidationError):
                api.change("t", "host:lab07", empty)

    def test_irreversible_change_is_recorded_as_such(self):
        api.change("t", "host:lab07", "overwrote /etc/hosts", reversible=False)
        self.assertFalse(api.changes("t")[0]["reversible"])


class TestCleanup(Base):
    """SPEC-003 §9.5 — the default list is what is still owed."""

    def test_cleaned_entries_drop_out_by_default_and_stay_readable(self):
        a = api.change("t", "host:lab07", "dropped /tmp/a")
        b = api.change("t", "host:lab07", "dropped /tmp/b")

        api.mark_cleaned("t", a)

        self.assertEqual([r["id"] for r in api.changes("t")], [b])
        every = api.changes("t", outstanding_only=False)
        self.assertEqual([r["id"] for r in every], [a, b])
        self.assertTrue(next(r for r in every if r["id"] == a)["cleaned"])

    def test_cleaning_an_unknown_id_is_refused_and_names_what_is_open(self):
        open_id = api.change("t", "host:lab07", "dropped /tmp/a")
        with self.assertRaises(api.ValidationError) as cm:
            api.mark_cleaned("t", "chg:999")
        self.assertIn(open_id, str(cm.exception))

    def test_fold_is_lenient_about_a_cleaned_id_it_cannot_resolve(self):
        """Strict on write, lenient on read: an unresolvable `cleaned` in an old
        log must not stop the log from loading."""
        api.change("t", "host:lab07", "dropped /tmp/a")
        store.append("t", "cleaned", {"change_id": "chg:404"})
        g = store.load("t")
        self.assertEqual(len(g.changes), 1)
        self.assertFalse(g.changes[0].cleaned)


class TestAuthorship(Base):

    def test_agent_argument_is_recorded_on_the_change(self):
        api.change("t", "host:lab07", "dropped /tmp/a", agent="a3")
        self.assertEqual(api.changes("t")[0]["by"], "a3")

    def test_agent_falls_back_to_the_environment(self):
        os.environ["RECKON_AGENT"] = "a1"
        api.change("t", "host:lab07", "dropped /tmp/a")
        self.assertEqual(api.changes("t")[0]["by"], "a1")

    def test_authorship_is_absent_rather_than_null_when_unknown(self):
        api.change("t", "host:lab07", "dropped /tmp/a")
        ev = store.read_events("t")[-1]
        self.assertNotIn("by", ev)
        self.assertIsNone(api.changes("t")[0]["by"])


class TestSchema(Base):
    """v2 is additive: a v1 log must fold to exactly the graph it folded to before."""

    def test_v1_log_folds_identically(self):
        v1 = [
            {"seq": 1, "ts": "2026-01-01T00:00:00+00:00", "v": 1,
             "op": "add_node", "args": {"id": "host:a", "kind": "host",
                                        "label": "a", "epistemic": "verified"}},
            {"seq": 2, "ts": "2026-01-01T00:00:00+00:00", "v": 1,
             "op": "note", "args": {"target_id": "host:a", "text": "hi"}},
        ]
        path = os.path.join(self.tmp, "old.jsonl")
        with open(path, "w") as fh:
            for e in v1:
                fh.write(json.dumps(e) + "\n")

        g = fold(store.read_events_path(path))
        self.assertEqual(g.seq, 2)
        self.assertEqual(g.nodes["host:a"].epistemic, "verified")
        self.assertEqual(g.changes, [])
        self.assertEqual(len(g.nodes["host:a"].notes), 1)

    def test_a_newer_schema_still_refuses_to_load(self):
        path = os.path.join(self.tmp, "future.jsonl")
        with open(path, "w") as fh:
            fh.write(json.dumps({"seq": 1, "v": store.SCHEMA_VERSION + 1,
                                 "op": "note", "args": {}}) + "\n")
        with self.assertRaises(store.SchemaTooNew):
            store.read_events_path(path)


class TestRedaction(Base):
    """A revert hint is a command, and the command that undoes an account
    creation tends to carry the credential that created it.

    This asserts the ledger is ROUTED through masking, not that masking is
    complete: `redact` recognises known-prefix tokens and keyword forms, and a
    bare positional secret in a command still survives it. That is the module's
    stated limit — a courtesy, not a control — and it is not widened here.
    """

    def test_revert_hint_and_description_are_masked(self):
        api.change("t", "host:lab07",
                   "registered a runner with token: ghp_A1b2C3d4E5f6G7h8I9j0",
                   revert_hint="curl -H 'Authorization: ghp_A1b2C3d4E5f6G7h8I9j0' -X DELETE /runner")
        g = redact.redact_graph(store.load("t"))
        self.assertNotIn("ghp_A1b2C3d4E5f6G7h8I9j0", g.changes[0].what)
        self.assertNotIn("ghp_A1b2C3d4E5f6G7h8I9j0", g.changes[0].revert_hint)
        self.assertIn(redact.PLACEHOLDER, g.changes[0].revert_hint)

    def test_masking_never_touches_the_log(self):
        api.change("t", "host:lab07", "dropped a key",
                   revert_hint="rm /root/.ssh/ghp_A1b2C3d4E5f6G7h8I9j0")
        redact.redact_graph(store.load("t"))
        self.assertIn("ghp_A1b2C3d4E5f6G7h8I9j0",
                      api.changes("t")[0]["revert_hint"])


class TestSurfaces(Base):

    def test_status_carries_only_outstanding_changes(self):
        a = api.change("t", "host:lab07", "dropped /tmp/a")
        api.change("t", "host:lab07", "dropped /tmp/b")
        api.mark_cleaned("t", a)
        ids = [c["id"] for c in api.status("t")["changes"]]
        self.assertNotIn(a, ids)
        self.assertEqual(len(ids), 1)

    def test_mcp_exposes_change_and_refuses_an_unknown_target(self):
        self.assertIn("change", [t["name"] for t in mcp.TOOLS])
        cid = mcp.dispatch("change", {"engagement": "t", "target": "host:lab07",
                                      "what": "dropped /tmp/a",
                                      "revert_hint": "rm /tmp/a"})
        self.assertTrue(cid.startswith("chg:"))
        with self.assertRaises(api.ValidationError):
            mcp.dispatch("change", {"engagement": "t", "target": "host:NOPE",
                                    "what": "x"})

    def test_graph_serialises_changes(self):
        api.change("t", "host:lab07", "dropped /tmp/a")
        self.assertEqual(len(store.load("t").to_dict()["changes"]), 1)


if __name__ == "__main__":
    unittest.main()
