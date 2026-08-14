"""CLI wiring tests.

These exist because the rest of the suite drives `api` and `mcp` directly, so a
parser that is wired wrong is invisible to it. That is not hypothetical: adding
the nested `plan` subparser shadowed the variable holding the top-level parser,
and `build_parser()` returned a sub-subparser instead — every command broke at
once while all 122 tests stayed green.

So: build the parser, and route one command through each shape.
"""

import argparse
import json
import os
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from reckon import api, cli, store
from tests import append_trace


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
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
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
                         "plan step", "plan reassign", "plans", "step",
                         "change", "changes", "cleaned", "handoff", "fleet",
                         "checkpoint", "alarms", "trace", "hook session-start",
                         "hook stop", "hook config"):
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


class TestHandoffCommands(Base):

    def test_handoff_routes_through_the_cli_and_names_the_resume_point(self):
        pid = self.run_cli("plan", "add", "obj:t21", "to DA",
                           "--step", "dump hives", "--step", "extract DCC2",
                           "--agent", "a3")
        self.run_cli("step", "done", pid, "1", "--produced", "host:lab07")
        self.run_cli("step", "blocked", pid, "2", "--reason", "refusal")
        out = self.run_cli("handoff", "--agent", "a3")
        self.assertIn("# Handoff", out)
        self.assertIn("extract DCC2", out)
        self.assertIn("do NOT retry as written", out)

    def test_handoff_out_writes_the_brief_to_a_file(self):
        self.run_cli("plan", "add", "obj:t21", "to DA", "--step", "a")
        dest = os.path.join(self.tmp, "briefs", "handoff.md")
        printed = self.run_cli("handoff", "--out", dest)
        self.assertEqual(printed, dest)
        with open(dest) as fh:
            self.assertIn("# Handoff", fh.read())

    def test_fleet_and_reassign_route_through_the_cli(self):
        pid = self.run_cli("plan", "add", "obj:t21", "to DA", "--step", "a",
                           "--agent", "a3")
        self.assertIn("a3", self.run_cli("fleet"))

        self.run_cli("plan", "reassign", pid, "a1", "--reason", "a3 stalled")

        self.assertIn("a1", self.run_cli("fleet"))
        self.assertIn("to DA", self.run_cli("handoff", "--agent", "a1"))
        # a3 keeps a fleet row because it authored events, but it no longer
        # holds the plan, so its resume point is empty rather than stale.
        self.assertIn("No active plan", self.run_cli("handoff", "--agent", "a3"))


class TestCheckpointCommands(Base):

    def test_checkpoint_prints_the_brief_and_stamps(self):
        out = self.run_cli("checkpoint", "--no-render")
        self.assertIn("# Checkpoint", out)
        self.assertEqual(api.last_checkpoint("t"), store.load("t").seq)

    def test_strict_exits_2_on_a_recording_alarm_and_0_otherwise(self):
        """The exit code is what lets a hook gate on checkpoint health without
        breaking interactive use, so it is wiring worth testing."""
        self.run_cli("checkpoint", "--no-render")          # arm A2
        with self.assertRaises(SystemExit) as cm:
            self.run_cli("checkpoint", "--no-render", "--strict")
        self.assertEqual(cm.exception.code, 2)

        api.add_node("t", "cred", "x", node_id="cred:x")
        api.decide("t", "keep going", reason="quiets A5")
        self.run_cli("checkpoint", "--no-render", "--strict")   # exits 0

    def test_alarms_runs_on_its_own(self):
        self.run_cli("checkpoint", "--no-render")
        self.assertIn("A2", self.run_cli("alarms"))

    def test_strict_exits_2_for_A3_alone(self):
        """A3 is recording health, so it has to gate `--strict` on its own —
        with A1 and A2 both quiet, an unrecorded stretch still fails a script."""
        self.run_cli("checkpoint", "--no-render")          # stamp the marker
        api.add_node("t", "cred", "x", node_id="cred:x")   # events since: A2 off
        api.decide("t", "keep going", reason="quiets A5")
        # --dry-run throughout, so checking the exit code does not re-stamp the
        # marker and re-arm A2 underneath the assertion.
        self.run_cli("checkpoint", "--no-render", "--dry-run", "--strict")

        append_trace("t", "nxc smb 10.99.10.5", "hashcat -m 1000 h.txt")

        fired = [a["id"] for a in api.alarms("t")]
        self.assertEqual([a for a in fired if a in ("A1", "A2")], [])
        self.assertIn("A3", fired)

        with self.assertRaises(SystemExit) as cm:
            self.run_cli("checkpoint", "--no-render", "--dry-run", "--strict")
        self.assertEqual(cm.exception.code, 2)


class TestTraceCommand(Base):
    """§5.4 — reading the trail the §5.2 hook writes."""

    def test_trace_prints_the_trail_and_says_so_when_there_is_none(self):
        self.assertIn("no trace", self.run_cli("trace"))
        append_trace("t", "nxc smb 10.99.10.5 -u j.rivera", "id")
        out = self.run_cli("trace")
        self.assertIn("nxc smb 10.99.10.5 -u j.rivera", out)
        self.assertIn("id", out)

    def test_since_and_limit_cut_the_trail(self):
        append_trace("t", *[f"cmd-{i}" for i in range(10)])
        self.assertNotIn("cmd-3", self.run_cli("trace", "--since", "5"))
        self.assertIn("cmd-7", self.run_cli("trace", "--since", "5"))
        self.assertEqual(len(self.run_cli("trace", "--limit", "2").splitlines()), 2)
        self.assertEqual(len(self.run_cli("trace", "--limit", "0").splitlines()), 10)

    def test_trace_can_be_redacted_for_a_copy_that_leaves_the_machine(self):
        tok = "ghp_A1b2C3d4E5f6G7h8I9j0"
        stamp = append_trace("t", f"curl -H 'Authorization: {tok}' https://git/api")
        self.assertIn(tok, self.run_cli("trace"))

        masked = self.run_cli("trace", "--redact")
        self.assertNotIn(tok, masked)
        self.assertIn("curl", masked)          # still a readable trail
        self.assertIn(stamp, masked,
                      "a blanket mask reads a timestamp as a user:secret pair")

    def test_trace_json_is_machine_readable(self):
        append_trace("t", "id")
        rows = json.loads(self.run_cli("trace", "--json"))
        self.assertEqual(rows[0]["cmd"], "id")
        self.assertEqual(rows[0]["seq"], 1)


class TestSuggestCommand(Base):
    """§8.10 — proposals, never assertions."""

    def test_suggest_proposes_and_writes_nothing(self):
        append_trace("t", "sed -i s/x/y/ /etc/ssh/sshd_config", "id")
        before = open(store.path_for("t")).read()

        out = self.run_cli("changes", "--suggest")

        self.assertIn("sed -i", out)
        self.assertIn("reckon change", out)                 # how to confirm
        self.assertEqual(open(store.path_for("t")).read(), before)
        self.assertEqual(api.changes("t"), [])
        self.assertEqual(self.run_cli("changes"),
                         "no outstanding target changes")

    def test_suggest_says_so_when_it_has_nothing_to_propose(self):
        append_trace("t", "id", "whoami")
        self.assertIn("nothing", self.run_cli("changes", "--suggest").lower())


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
