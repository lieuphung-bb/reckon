import unittest

from reckon.model import fold, OPERATOR_ID


def ev(seq, op, **args):
    return {"seq": seq, "ts": "2026-08-08T00:00:00+00:00", "op": op, "args": args}


class TestFold(unittest.TestCase):

    def test_operator_always_exists(self):
        g = fold([])
        self.assertIn(OPERATOR_ID, g.nodes)
        self.assertEqual(g.nodes[OPERATOR_ID].epistemic, "verified")

    def test_fold_is_deterministic(self):
        events = [
            ev(1, "add_node", id="host:a", kind="host", label="a"),
            ev(2, "set_epistemic", id="host:a", state="verified"),
        ]
        a, b = fold(events), fold(events)
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_add_node_is_idempotent_and_merges(self):
        g = fold([
            ev(1, "add_node", id="host:a", kind="host", label="a", props={"ip": "1.2.3.4"}),
            ev(2, "add_node", id="host:a", kind="host", label="a", props={"os": "linux"}),
        ])
        self.assertEqual(g.nodes["host:a"].props, {"ip": "1.2.3.4", "os": "linux"})
        self.assertEqual(len(g.by_kind("host")), 1)

    def test_supersede_preserves_history(self):
        g = fold([
            ev(1, "add_node", id="finding:old", kind="finding", label="old"),
            ev(2, "add_node", id="finding:new", kind="finding", label="new"),
            ev(3, "supersede", old_id="finding:old", new_id="finding:new",
               reason="refuted by direct test"),
        ])
        self.assertIn("finding:old", g.nodes)                  # never deleted
        self.assertEqual(g.nodes["finding:old"].superseded_by, "finding:new")
        self.assertTrue(any("superseded" in n for n in g.nodes["finding:old"].notes))
        self.assertNotIn("finding:old", [n.id for n in g.by_kind("finding")])

    def test_exploitation_transitions_record_seq(self):
        g = fold([
            ev(1, "add_node", id="cred:x", kind="cred", label="x"),
            ev(3, "set_exploitation", id="cred:x", state="acquired"),
            ev(9, "examine", id="cred:x", outcome="works on /api/login"),
        ])
        n = g.nodes["cred:x"]
        self.assertEqual(n.acquired_at, 3)
        self.assertEqual(n.examined_at, 9)
        self.assertEqual(n.exploitation, "examined")

    def test_time_travel(self):
        events = [
            ev(1, "add_node", id="host:a", kind="host", label="a"),
            ev(2, "set_epistemic", id="host:a", state="hypothesized"),
            ev(5, "set_epistemic", id="host:a", state="verified"),
        ]
        early = fold([e for e in events if e["seq"] <= 2])
        late = fold(events)
        self.assertEqual(early.nodes["host:a"].epistemic, "hypothesized")
        self.assertEqual(late.nodes["host:a"].epistemic, "verified")


if __name__ == "__main__":
    unittest.main()
