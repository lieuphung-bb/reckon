"""Ingest tests.

Each case is a defect found by running the importer against a real workspace;
the five it was built for share no table schema, so every assumption about
column position or heading text has already been wrong once.
"""

import os
import tempfile
import unittest

from reckon.ingest import (from_workspace, classify_access, parse_tables,
                        col_index, sections, IngestError)
from reckon.model import fold
from reckon.queries import unmined, unrealized


def workspace(md: str):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "topology.md"), "w") as fh:
        fh.write(md)
    return d


class TestColumnMapping(unittest.TestCase):
    """Columns are matched by header synonym; the five workspaces disagree."""

    def test_synonyms_across_schemas(self):
        cl1 = ["panel", "ip", "host / role", "seg", "os", "ports", "access / notes"]
        cl2 = ["host / ip (env var)", "role", "zone", "access", "creds"]
        m11 = [".last", "host", "zone", "role / note", "reached", "proof.txt"]
        self.assertEqual(col_index(cl1, "access"), 6)
        self.assertEqual(col_index(cl2, "access"), 3)
        self.assertEqual(col_index(m11, "access"), 4)     # "reached"
        self.assertEqual(col_index(m11, "host"), 1)
        self.assertEqual(col_index(cl2, "zone"), 2)

    def test_table_parsing_ignores_prose(self):
        lines = ["some prose", "", "| A | B |", "|---|---|", "| 1 | 2 |", "more"]
        tables = parse_tables(lines)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0][0], ["a", "b"])
        self.assertEqual(tables[0][1], [["1", "2"]])


class TestAccessClassification(unittest.TestCase):

    def test_admin_shell_is_owned(self):
        epi, expl, rank = classify_access("✅ **interactive shell as root**")
        self.assertEqual((epi, rank), ("verified", 3))

    def test_reachable_is_not_access(self):
        epi, expl, rank = classify_access("reachable from .13; not accessed")
        self.assertEqual(rank, 0)

    def test_unenumerated_stays_unexplored(self):
        self.assertEqual(classify_access("unenumerated")[0], "unexplored")

    def test_denial_is_a_real_observation(self):
        # "denied" is knowledge, not ignorance: it is verified-and-shut
        self.assertEqual(classify_access("ACCESS_DENIED to svc_*")[0], "verified")


class TestObjectiveParsing(unittest.TestCase):

    MD = """## Chain checklist
- [x] **T1 Leaked cred -> RCE**
- [x] **★ T7 proof.txt on DC01 (DA)**
- [~] **T2 pivot** — partial

### (superseded) original checklist
- [ ] T7 ★ DC01 proof.txt (Domain Admin)
"""

    def test_decorated_task_id_is_recovered(self):
        """`★ T7 ...` used to yield a junk id, letting a superseded open T7 own
        `obj:t7` and reporting an achieved objective as still open."""
        g = fold([{"seq": i + 1, "op": e["op"], "args": e["args"]}
                  for i, e in enumerate(from_workspace(workspace(self.MD)))])
        self.assertIn("obj:t7", g.nodes)
        self.assertEqual(g.nodes["obj:t7"].status, "achieved")

    def test_partial_task_is_open(self):
        g = fold([{"seq": i + 1, "op": e["op"], "args": e["args"]}
                  for i, e in enumerate(from_workspace(workspace(self.MD)))])
        self.assertEqual(g.nodes["obj:t2"].status, "open")

    def test_crown_jewel_marked(self):
        g = fold([{"seq": i + 1, "op": e["op"], "args": e["args"]}
                  for i, e in enumerate(from_workspace(workspace(self.MD)))])
        self.assertTrue(g.nodes["obj:t7"].props.get("crown_jewel"))

    def test_checkboxes_found_outside_a_task_heading(self):
        """M11 files its checklist under '## Cred / chain checklist', which
        heading-based routing classified as creds and dropped entirely."""
        md = "## Cred / chain checklist\n- [x] **T2a web -> SQL -> RCE**\n"
        ev = from_workspace(workspace(md))
        self.assertTrue(any(e["args"].get("kind") == "objective" for e in ev))


class TestConservativeAssertions(unittest.TestCase):

    MD = """## Hosts
| Host | IP | Zone | Access |
|---|---|---|---|
| WEB01 | 10.0.0.5 | dmz | ✅ shell as root |
| DB01 | 10.0.0.6 | dmz | reachable |

## Chain checklist
- [ ] **T9 own the DB01 box**
- [ ] **T8 something with no host named**
"""

    def setUp(self):
        ev = from_workspace(workspace(self.MD))
        self.g = fold([{"seq": i + 1, "op": e["op"], "args": e["args"]}
                       for i, e in enumerate(ev)])

    def test_hosts_are_not_marked_acquired(self):
        """An access column says nothing about whether the box was enumerated.
        Inferring `acquired` made every owned host an UNMINED alarm."""
        self.assertEqual(self.g.nodes["host:web01"].exploitation, "discovered")
        self.assertNotIn("host:web01", [u["id"] for u in unmined(self.g)])

    def test_requires_only_when_a_host_is_named(self):
        """Synthesising a requirement made every open objective look winnable,
        including ones that were genuinely blocked."""
        self.assertIn("requires", self.g.nodes["obj:t9"].props)
        self.assertNotIn("requires", self.g.nodes["obj:t8"].props)

    def test_objective_with_no_requirement_is_never_claimed_winnable(self):
        self.assertNotIn("obj:t8", [o["id"] for o in unrealized(self.g)])

    def test_missing_topology_raises(self):
        with self.assertRaises(IngestError):
            from_workspace(tempfile.mkdtemp())


class TestRealWorkspaces(unittest.TestCase):
    """Ingest must survive real workspaces, which share no schema.

    Paths come from $RECKON_TEST_WORKSPACES (colon-separated) so no private
    location is baked into a public repo; the test skips when unset.
    """

    @property
    def ROOTS(self):
        raw = os.environ.get("RECKON_TEST_WORKSPACES", "")
        return {os.path.basename(p): p for p in raw.split(":") if p}

    def test_every_workspace_yields_hosts_and_objectives(self):
        if not self.ROOTS:
            self.skipTest("set RECKON_TEST_WORKSPACES to run against real workspaces")
        for lab, path in self.ROOTS.items():
            if not os.path.exists(path):
                self.skipTest(f"{lab} workspace not present")
            ev = from_workspace(path)
            g = fold([{"seq": i + 1, "op": e["op"], "args": e["args"]}
                      for i, e in enumerate(ev)])
            with self.subTest(lab=lab):
                self.assertGreater(len(g.by_kind("host")), 0, f"{lab}: no hosts")
                self.assertGreater(len(g.objectives()), 0, f"{lab}: no objectives")


if __name__ == "__main__":
    unittest.main()
