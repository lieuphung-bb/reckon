"""Hook contracts — SPEC-004 §5.1, §5.3 and criterion §8.8.

Everywhere else in reckon a bad input is refused loudly. Hooks invert that, and
these tests exist to hold the inversion in place: a hook runs on the harness's
schedule, so one that raises takes a session down with it.

§8.8 is the criterion — "SessionStart with no reckon installed, no engagement, or
a corrupt log exits 0 and prints nothing" — so the failure cases are tested more
thoroughly than the happy path. The happy path failing is an inconvenience; the
failure path failing means a session that will not start.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from reckon import api, cli, hooks, store


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_e, self._old_h = store.ENGAGEMENTS, store.RECKON_HOME
        store.ENGAGEMENTS = self.tmp
        store.RECKON_HOME = self.tmp
        api.create("t")
        api.add_node("t", "host", "lab07", node_id="host:lab07",
                     epistemic="verified")
        api.add_node("t", "objective", "to DA", node_id="obj:t21")

    def tearDown(self):
        store.ENGAGEMENTS, store.RECKON_HOME = self._old_e, self._old_h
        os.environ.pop("RECKON_AGENT", None)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            cli.main(["-e", "t", *argv])
        return out.getvalue(), err.getvalue()

    def blocked_plan(self, agent="a3"):
        pid = api.plan_add("t", "obj:t21", "shadow-cred to DA", agent=agent,
                           steps=["dump hives", "extract DCC2", "PKINIT"])
        api.step_state("t", pid, 1, "done", produced=["host:lab07"],
                       agent=agent)
        api.step_state("t", pid, 2, "blocked", blocked_reason="refusal",
                       note="declined", agent=agent)
        return pid


class TestSessionStart(Base):
    """§5.1 — a session begins already holding the resume brief."""

    def test_the_brief_is_returned_for_the_named_agent(self):
        self.blocked_plan(agent="a3")
        text = hooks.session_start("t", agent="a3")
        self.assertIn("# Handoff", text)
        self.assertIn("extract DCC2", text)
        self.assertIn("do NOT retry as written", text)

    def test_an_unset_agent_env_var_arrives_as_empty_and_is_treated_as_none(self):
        """`--agent "$RECKON_AGENT"` with the variable unset passes "", not a
        missing flag, so the empty string has to mean 'nobody named'."""
        self.blocked_plan(agent="a3")
        self.assertEqual(hooks.session_start("t", agent=""),
                         hooks.session_start("t", agent=None))

    def test_several_live_plans_show_all_rather_than_nothing(self):
        """Refusing is right for an operator at a prompt; for a starting session
        it would mean beginning with no brief at all."""
        self.blocked_plan(agent="a3")
        api.add_node("t", "objective", "exfil", node_id="obj:t22")
        api.plan_add("t", "obj:t22", "second path", agent="a1", steps=["a"])

        with self.assertRaises(api.AmbiguousHandoff):
            api.handoff("t")                      # the operator-facing refusal

        text = hooks.session_start("t")           # the hook does not refuse
        self.assertIn("shadow-cred to DA", text)
        self.assertIn("second path", text)

    def test_an_engagement_with_no_plan_still_gets_position_and_next_moves(self):
        text = hooks.session_start("t")
        self.assertIn("No active plan", text)
        self.assertIn("Next moves", text)

    def test_redaction_is_available_to_the_hook(self):
        """Covers what the brief actually renders — the cursor's note and
        command, and outstanding revert hints. A secret on a step the brief does
        not print is not a leak the brief can make."""
        tok = "ghp_A1b2C3d4E5f6G7h8I9j0"
        pid = api.plan_add("t", "obj:t21", "v1", agent="a3",
                           steps=["one", "two"])
        api.step_state("t", pid, 1, "blocked", blocked_reason="operator",
                       note=f"stopped, {tok} was live", agent="a3")
        api.change("t", "host:lab07", "registered a runner",
                   revert_hint=f"curl -H 'Authorization: {tok}' -X DELETE /r")

        clear = hooks.session_start("t", agent="a3")
        self.assertIn(tok, clear)
        self.assertEqual(clear.count(tok), 2)      # the note and the hint

        masked = hooks.session_start("t", agent="a3", redact=True)
        self.assertNotIn(tok, masked)
        self.assertIn("Resume here", masked)       # still a usable brief


class TestFailsOpen(Base):
    """§8.8 — it can never block a session from starting."""

    def test_a_missing_engagement_is_silence_not_an_error(self):
        self.assertEqual(hooks.session_start("nosuchengagement"), "")
        self.assertIsNone(hooks.stop("nosuchengagement"))

    def test_no_engagement_name_at_all_is_silence(self):
        for empty in ("", None):
            self.assertEqual(hooks.session_start(empty), "")
            self.assertIsNone(hooks.stop(empty))

    def test_a_corrupt_log_is_silence(self):
        with open(store.path_for("t"), "a") as fh:
            fh.write("{not json at all\n")
        with self.assertRaises(store.StoreError):
            store.load("t")                       # loud everywhere else...
        self.assertEqual(hooks.session_start("t"), "")   # ...silent here
        self.assertIsNone(hooks.stop("t"))

    def test_a_log_from_a_newer_schema_is_silence(self):
        with open(store.path_for("t"), "a") as fh:
            fh.write(json.dumps({"seq": 99, "v": store.SCHEMA_VERSION + 1,
                                 "op": "note", "args": {}}) + "\n")
        self.assertEqual(hooks.session_start("t"), "")
        self.assertIsNone(hooks.stop("t"))

    def test_an_unreadable_engagements_directory_is_silence(self):
        store.ENGAGEMENTS = os.path.join(self.tmp, "gone")
        self.assertEqual(hooks.session_start("t"), "")
        self.assertIsNone(hooks.stop("t"))

    def test_an_invalid_engagement_name_is_silence(self):
        for bad in ("../escape", ".hidden", "with/slash"):
            self.assertEqual(hooks.session_start(bad), "")
            self.assertIsNone(hooks.stop(bad))


class TestStop(Base):
    """§5.3 — a stamped checkpoint even when a session ends abruptly."""

    def test_stop_stamps_a_checkpoint_and_evaluates_alarms(self):
        self.assertEqual(api.last_checkpoint("t"), 0)
        c = hooks.stop("t")
        self.assertEqual(api.last_checkpoint("t"), store.load("t").seq)
        self.assertIn("alarms", c)

    def test_stop_does_not_regenerate_documents(self):
        """It runs on every session end; regenerating the console and six views
        on the way out is latency nobody asked for."""
        self.assertEqual(hooks.stop("t")["rendered"], [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "out")))

    def test_stop_leaves_the_per_agent_seen_marker_alone(self):
        api.delta("t")
        seen = api.last_seen("t")
        api.add_node("t", "cred", "x", node_id="cred:x")
        hooks.stop("t")
        self.assertEqual(api.last_seen("t"), seen)

    def test_the_next_session_start_reflects_where_work_actually_stopped(self):
        self.blocked_plan(agent="a3")
        hooks.stop("t")
        self.assertIn("extract DCC2", hooks.session_start("t", agent="a3"))


class TestCliContract(Base):
    """The commands the harness actually runs."""

    def test_session_start_exits_0_and_prints_nothing_on_every_failure(self):
        with open(store.path_for("t"), "a") as fh:
            fh.write("{corrupt\n")
        out, err = self.run_cli("hook", "session-start", "--agent", "a3")
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_stop_exits_0_and_prints_nothing_on_every_failure(self):
        with open(store.path_for("t"), "a") as fh:
            fh.write("{corrupt\n")
        out, err = self.run_cli("hook", "stop")
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_session_start_prints_the_brief_when_all_is_well(self):
        self.blocked_plan(agent="a3")
        out, _ = self.run_cli("hook", "session-start", "--agent", "a3")
        self.assertIn("# Handoff", out)

    def test_config_emits_a_valid_paste_ready_fragment(self):
        out, _ = self.run_cli("hook", "config")
        frag = json.loads(out)
        cmds = [h["command"]
                for k in ("SessionStart", "Stop")
                for entry in frag["hooks"][k] for h in entry["hooks"]]
        self.assertTrue(all(c.endswith("|| true") for c in cmds),
                        "every hook command must survive reckon being absent "
                        f"from PATH: {cmds}")
        self.assertTrue(any("session-start" in c for c in cmds))
        self.assertTrue(any("--no-render" in c or "hook stop" in c
                            for c in cmds))
        self.assertNotIn("env", frag)

    def test_config_can_pin_the_engagement(self):
        out, _ = self.run_cli("hook", "config", "--pin")
        self.assertEqual(json.loads(out)["env"]["RECKON_CURRENT"], "t")

    def test_config_writes_nothing(self):
        """reckon emits the fragment; it never edits the config that invokes
        it."""
        before = sorted(os.listdir(self.tmp))
        self.run_cli("hook", "config")
        self.assertEqual(sorted(os.listdir(self.tmp)), before)


class TestTraceStaysOutOfScope(Base):
    """§5.2 and §5.4 are not built, which is what keeps A3 dark. Assert the
    absence rather than leaving it ambiguous."""

    def test_no_trace_file_is_written_by_either_hook(self):
        self.blocked_plan()
        hooks.session_start("t", agent="a3")
        hooks.stop("t")
        self.assertFalse([f for f in os.listdir(self.tmp) if "trace" in f])

    def test_A3_stays_dark(self):
        hooks.stop("t")
        self.assertNotIn("A3", [a["id"] for a in api.alarms("t")])
        self.assertIn("A3", [a[0] for a in api.DARK_ALARMS])


if __name__ == "__main__":
    unittest.main()
