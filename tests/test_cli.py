"""CLI wiring tests.

These exist because the rest of the suite drives `api` and `mcp` directly, so a
parser that is wired wrong is invisible to it. That is not hypothetical: adding
the nested `plan` subparser shadowed the variable holding the top-level parser,
and `build_parser()` returned a sub-subparser instead — every command broke at
once while all 122 tests stayed green.

So: build the parser, and route one command through each shape.
"""

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout

from reckon import api, cli, store


def _subcommands(parser) -> dict:
    """{name: subparser}. Matches on the action type rather than on `choices`,
    which a plain flag like `--reason` also carries."""
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return dict(a.choices)
    return {}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = store.ENGAGEMENTS
        store.ENGAGEMENTS = self.tmp
        api.create("t")
        api.add_node("t", "host", "lab07", node_id="host:lab07",
                     epistemic="verified")
        api.add_node("t", "objective", "to DA", node_id="obj:t21")

    def tearDown(self):
        store.ENGAGEMENTS = self._old

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["-e", "t", *argv])
        return buf.getvalue().strip()


class TestParser(unittest.TestCase):

    def test_build_parser_returns_the_top_level_parser(self):
        """The regression that motivated this file: a nested subparser must not
        become what build_parser hands back."""
        p = cli.build_parser()
        args = p.parse_args(["-e", "eng", "board"])
        self.assertEqual(args.name, "eng")
        self.assertEqual(args.func, cli.cmd_board)

    def test_every_subcommand_binds_a_handler(self):
        seen = []
        for name, parser in _subcommands(cli.build_parser()).items():
            nested = _subcommands(parser)
            for label, sp in ([(f"{name} {k}", v) for k, v in nested.items()]
                              or [(name, parser)]):
                self.assertTrue(sp.get_default("func"),
                                f"subcommand {label!r} has no handler bound")
                seen.append(label)
        for expected in ("board", "plan add", "plan show", "plan supersede",
                         "plan step", "plans", "step", "change", "changes",
                         "cleaned"):
            self.assertIn(expected, seen)


class TestPlanCommands(Base):

    def test_the_whole_plan_flow_routes_through_the_cli(self):
        pid = self.run_cli("plan", "add", "obj:t21", "shadow-cred to DA",
                           "--step", "dump hives", "--step", "extract DCC2")
        self.assertTrue(pid.startswith("plan:"))

        self.run_cli("step", "done", pid, "1", "--produced", "host:lab07")
        out = self.run_cli("step", "blocked", pid, "2", "--reason", "refusal")
        self.assertIn("do NOT retry as written", out)

        shown = self.run_cli("plan", "show", pid)
        self.assertIn("produced: host:lab07", shown)
        self.assertIn("←", shown)                      # the cursor is marked

        self.assertIn(pid, self.run_cli("plans"))

    def test_supersede_moves_the_active_plan_and_keeps_the_old_one(self):
        old = self.run_cli("plan", "add", "obj:t21", "v1", "--step", "a")
        new = self.run_cli("plan", "add", "obj:t21", "v2", "--step", "b",
                           "--supersedes", old)
        active = self.run_cli("plans")
        self.assertIn(new, active)
        self.assertNotIn(old, active)
        self.assertIn("SUPERSEDED", self.run_cli("plans", "--all"))

    def test_appending_a_step_to_a_live_plan(self):
        pid = self.run_cli("plan", "add", "obj:t21", "v1", "--step", "a")
        self.run_cli("plan", "step", pid, "b", "--command", "nxc smb 10.99.10.5")
        self.assertIn("nxc smb", self.run_cli("plan", "show", pid))


class TestChangeCommands(Base):

    def test_change_then_cleaned_round_trips(self):
        cid = self.run_cli("change", "host:lab07", "dropped /tmp/pyk.py",
                           "--revert", "rm /tmp/pyk.py")
        self.assertIn("rm /tmp/pyk.py", self.run_cli("changes"))
        self.run_cli("cleaned", cid)
        self.assertEqual(self.run_cli("changes"), "no outstanding target changes")
        self.assertIn(cid, self.run_cli("changes", "--all"))


class TestRefusalsReachTheUser(Base):
    """`main` turns a ValidationError into a non-zero exit with the reason —
    a refusal that exits 0 is indistinguishable from success in a script."""

    def test_blocking_without_a_reason_exits_non_zero(self):
        pid = self.run_cli("plan", "add", "obj:t21", "v1", "--step", "a")
        with self.assertRaises(SystemExit) as cm:
            cli.main(["-e", "t", "step", "blocked", pid, "1"])
        self.assertIn("needs a reason", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
