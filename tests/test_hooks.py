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
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout

from reckon import api, cli, hooks, store


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_e, self._old_h = store.ENGAGEMENTS, store.RECKON_HOME
        self._old_o = store.OUT
        # The real layout, not a flattened one: the §5.2 hook is a shell command
        # that derives its path from $RECKON_HOME, so a test home that does not
        # match production would test a file nothing writes to.
        store.ENGAGEMENTS = os.path.join(self.tmp, "engagements")
        store.RECKON_HOME = self.tmp
        store.OUT = os.path.join(self.tmp, "out")
        api.create("t")
        api.add_node("t", "host", "lab07", node_id="host:lab07",
                     epistemic="verified")
        api.add_node("t", "objective", "to DA", node_id="obj:t21")

    def tearDown(self):
        store.ENGAGEMENTS, store.RECKON_HOME = self._old_e, self._old_h
        store.OUT = self._old_o
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
        self.assertEqual(set(frag["hooks"]),
                         {"SessionStart", "PostToolUse", "Stop"})
        cmds = [h["command"]
                for k in ("SessionStart", "PostToolUse", "Stop")
                for entry in frag["hooks"][k] for h in entry["hooks"]]
        self.assertTrue(all(c.endswith("|| true") for c in cmds),
                        "every hook command must survive reckon being absent "
                        f"from PATH: {cmds}")
        self.assertTrue(any("session-start" in c for c in cmds))
        self.assertTrue(any("--no-render" in c or "hook stop" in c
                            for c in cmds))
        self.assertNotIn("env", frag)

    def test_config_traces_every_tool_rather_than_only_bash(self):
        """Narrowing the matcher would silently agree that reading forty files
        is not work — the judgment A3 exists to avoid making."""
        frag = json.loads(self.run_cli("hook", "config")[0])
        entry, = frag["hooks"]["PostToolUse"]
        self.assertEqual(entry["matcher"], "*")
        self.assertIn(".trace.jsonl", entry["hooks"][0]["command"])

    def test_config_can_pin_the_engagement(self):
        out, _ = self.run_cli("hook", "config", "--pin")
        self.assertEqual(json.loads(out)["env"]["RECKON_CURRENT"], "t")

    def test_config_warns_on_stderr_when_the_trace_cannot_be_written(self):
        """The precondition of the loud-failure mechanism cannot itself fail
        quietly: with no jq the hook no-ops on every tool call, A3 never fires,
        and a permanently silent alarm reads exactly like a healthy engagement.
        """
        empty = os.path.join(self.tmp, "nopath")
        os.makedirs(empty, exist_ok=True)
        with unittest.mock.patch.dict(os.environ, {"PATH": empty}):
            out, err = self.run_cli("hook", "config")

        self.assertTrue(err.strip(), "an unwritable trace must be said out loud")
        self.assertIn("jq", err)
        self.assertIn("A3", err)                  # what is lost, not just what
        self.assertIn("install", err)             # is missing, and the fix
        json.loads(out)                           # stdout is still pasteable

    def test_the_warning_stays_out_of_the_fragment_and_off_the_exit_code(self):
        """It gets pasted into settings.json, and it emits a config rather than
        checking one."""
        empty = os.path.join(self.tmp, "nopath")
        os.makedirs(empty, exist_ok=True)
        with unittest.mock.patch.dict(os.environ, {"PATH": empty}):
            out, _ = self.run_cli("hook", "config")     # no SystemExit: exit 0
        self.assertNotIn("warning", out)
        self.assertEqual(set(json.loads(out)["hooks"]),
                         {"SessionStart", "PostToolUse", "Stop"})

    def test_no_warning_when_the_dependency_is_present(self):
        if not shutil.which("jq"):
            self.skipTest("jq absent — this asserts the quiet path")
        self.assertEqual(self.run_cli("hook", "config")[1], "")

    def test_config_writes_nothing(self):
        """reckon emits the fragment; it never edits the config that invokes
        it."""
        before = sorted(os.listdir(self.tmp))
        self.run_cli("hook", "config")
        self.assertEqual(sorted(os.listdir(self.tmp)), before)


class TraceBase(Base):
    """Runs POST_TOOL_USE_COMMAND as what it is: a shell command.

    Asserting that the string contains `>>` proves nothing about whether it
    produces valid JSON, and that is exactly how the last dead-button bug in
    this repo got through.
    """

    def setUp(self):
        super().setUp()
        if not shutil.which("jq"):
            self.skipTest("jq absent — the hook writes nothing by design")

    def fire(self, payload, agent="a1", current="t"):
        """One PostToolUse call. `payload` is the object the harness pipes in."""
        env = {**os.environ, "RECKON_HOME": self.tmp, "RECKON_AGENT": agent}
        env.pop("RECKON_CURRENT", None)
        if current is not None:
            env["RECKON_CURRENT"] = current
        body = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(["sh", "-c", hooks.POST_TOOL_USE_COMMAND],
                              input=body, text=True, capture_output=True,
                              env=env, cwd=self.tmp)

    def trace_lines(self, name="t"):
        path = store.trace_path_for(name)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return [ln for ln in fh.read().split("\n") if ln]

    def any_trace_file(self):
        return [os.path.join(d, f)
                for d, _dirs, files in os.walk(self.tmp) for f in files
                if "trace" in f]

    @staticmethod
    def call(cmd, tool="Bash", exit_code=0, cwd="/home/kali"):
        return {"session_id": "s1", "hook_event_name": "PostToolUse",
                "cwd": cwd, "tool_name": tool,
                "tool_input": {"command": cmd},
                "tool_response": {"exit_code": exit_code, "stdout": "..."}}


class TestTraceWrite(TraceBase):
    """§5.2 — one line per tool call, in the shape the spec fixes."""

    def test_one_call_writes_one_line_in_the_documented_shape(self):
        p = self.fire(self.call("nxc smb 10.99.10.5 -u j.rivera"))
        self.assertEqual(p.returncode, 0)
        lines = self.trace_lines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(set(rec), {"ts", "tool", "cmd", "exit", "cwd", "agent"})
        self.assertEqual(rec["tool"], "Bash")
        self.assertEqual(rec["cmd"], "nxc smb 10.99.10.5 -u j.rivera")
        self.assertEqual(rec["exit"], 0)
        self.assertEqual(rec["cwd"], "/home/kali")
        self.assertEqual(rec["agent"], "a1")
        self.assertRegex(rec["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_quotes_backslashes_and_newlines_survive_as_one_json_line(self):
        """The whole reason the hook shells out to a real encoder."""
        nasty = ('printf "%s\\n" \'a\'\'b\' | sed -i s/x/y/ '
                 '\t# tab, "quote", \\ backslash\nsecond line é中')
        self.fire(self.call(nasty))
        lines = self.trace_lines()
        self.assertEqual(len(lines), 1, "a newline in cmd must not split the line")
        self.assertEqual(json.loads(lines[0])["cmd"], nasty)

    def test_each_call_appends_rather_than_replacing(self):
        for i in range(5):
            self.fire(self.call(f"whoami {i}"))
        lines = self.trace_lines()
        self.assertEqual(len(lines), 5)
        self.assertEqual([json.loads(l)["cmd"] for l in lines],
                         [f"whoami {i}" for i in range(5)])

    def test_the_trace_is_a_separate_file_from_the_event_log(self):
        before = open(store.path_for("t")).read()
        self.fire(self.call("id"))
        self.assertEqual(open(store.path_for("t")).read(), before)
        self.assertTrue(self.trace_lines())

    def test_a_non_bash_tool_still_registers_as_activity(self):
        """A3 is about work happening, not about shells. Forty file reads are
        work, and a trace that only sees Bash would call them quiet."""
        self.fire({"cwd": "/home/kali", "tool_name": "Read",
                   "tool_input": {"file_path": "/etc/shadow"}})
        rec = json.loads(self.trace_lines()[0])
        self.assertEqual(rec["tool"], "Read")
        self.assertEqual(rec["cmd"], "/etc/shadow")

    def test_a_failed_command_records_its_exit_code(self):
        self.fire(self.call("nxc smb 10.99.10.5", exit_code=1))
        self.assertEqual(json.loads(self.trace_lines()[0])["exit"], 1)


class TestTraceSize(TraceBase):
    """§8.6 — a 5000-byte command truncates below 4000 and stays valid JSON.

    The cap is what makes the no-lock append safe rather than merely
    convenient: a single `O_APPEND` write under PIPE_BUF (4096) is atomic.
    """

    def test_a_5000_byte_command_is_truncated_and_still_parses(self):
        cmd = "python3 -c " + "A" * 5000
        self.fire(self.call(cmd))
        line = self.trace_lines()[0]
        self.assertLess(len(line.encode()), 4000)
        rec = json.loads(line)                       # the point of the cap
        self.assertLessEqual(len(rec["cmd"]), 1500)
        self.assertTrue(cmd.startswith(rec["cmd"]))  # the head, not the tail

    def test_the_cap_is_in_bytes_not_characters(self):
        """1500 multi-byte characters would blow the 4000-byte line cap that
        makes the no-lock append safe."""
        for filler in ("\U0001f600", "é", "\u4e2d"):
            with self.subTest(filler=filler):
                self.fire(self.call(filler * 5000))
                line = self.trace_lines()[-1]
                self.assertLess(len(line.encode()), 4000)
                json.loads(line)

    def test_a_command_of_escape_heavy_bytes_still_fits(self):
        """Control characters encode to six JSON bytes each — the case where a
        character-only truncation is off by a factor of six."""
        self.fire(self.call("\x01\x02\x03" * 2000))
        line = self.trace_lines()[0]
        self.assertLess(len(line.encode()), 4000)
        json.loads(line)


class TestTraceConcurrency(TraceBase):
    """§8.7 — 4 concurrent appends, 4 well-formed lines, none interleaved."""

    def test_four_concurrent_appends_do_not_interleave(self):
        """Each writer is fed from a file it already holds open, so the four
        appends race rather than being serialised by this test feeding them one
        at a time. The commands are multi-byte fillers, which land the lines
        just under the 3990-byte cap — the worst case the cap has to cover."""
        env = {**os.environ, "RECKON_HOME": self.tmp,
               "RECKON_CURRENT": "t", "RECKON_AGENT": "a1"}
        fillers = ("\U0001f600", "中", "é", "ü")
        stdins = []
        for i, filler in enumerate(fillers):
            p = os.path.join(self.tmp, f"in{i}.json")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(self.call(f"marker-{i} " + filler * 5000)))
            stdins.append(open(p, "rb"))

        procs = [subprocess.Popen(["sh", "-c", hooks.POST_TOOL_USE_COMMAND],
                                  stdin=fh, env=env, cwd=self.tmp)
                 for fh in stdins]
        for proc in procs:
            self.assertEqual(proc.wait(), 0)
        for fh in stdins:
            fh.close()

        lines = self.trace_lines()
        self.assertEqual(len(lines), 4)
        recs = [json.loads(l) for l in lines]          # none may be spliced
        self.assertEqual({r["cmd"][:8] for r in recs},
                         {f"marker-{i}" for i in range(4)})
        for line, rec in zip(lines, recs):
            self.assertGreater(len(line.encode()), 2000)   # near the cap
            self.assertLess(len(line.encode()), 4000)
            # one writer's filler only: a spliced line would carry two
            self.assertEqual(len(set(rec["cmd"][9:])), 1)


class TestTraceFailsOpen(TraceBase):
    """The hook runs on every tool call; it may never make one fail."""

    def test_an_unset_engagement_writes_nothing_and_exits_0(self):
        p = self.fire(self.call("id"), current=None)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")
        self.assertEqual(self.any_trace_file(), [])

    def test_an_engagement_name_that_would_escape_the_directory_is_refused(self):
        """`store.path_for` refuses these three; nothing validates the
        environment variable the shell reads them from."""
        for bad in ("../escape", ".hidden", "with/slash"):
            with self.subTest(bad=bad):
                p = self.fire(self.call("id"), current=bad)
                self.assertEqual(p.returncode, 0)
        self.assertEqual(self.any_trace_file(), [])

    def test_a_missing_engagements_directory_is_silence(self):
        p = self.fire(self.call("id"), current="nosuchengagement")
        self.assertEqual(p.returncode, 0)

    def test_malformed_stdin_writes_nothing_and_exits_0(self):
        for junk in ("", "not json at all {", "[]", '"a bare string"', "null"):
            with self.subTest(junk=junk):
                p = self.fire(junk)
                self.assertEqual(p.returncode, 0)
                self.assertEqual(p.stderr, "")
        self.assertEqual(self.trace_lines(), [])

    def test_an_unset_agent_is_an_empty_field_not_a_broken_line(self):
        p = self.fire(self.call("id"), agent="")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(self.trace_lines()[0])["agent"], "")

    def test_no_jq_means_no_line_rather_than_a_corrupt_one(self):
        """The documented fallback: A3 stays dark, which it says out loud. A
        hand-rolled escaper that gets one input wrong writes garbage into a file
        an alarm then reports on."""
        empty_path = os.path.join(self.tmp, "nopath")
        os.makedirs(empty_path, exist_ok=True)
        # An absolute shell: the point is a PATH with no jq on it, not a PATH
        # with no shell on it.
        p = subprocess.run([shutil.which("sh"), "-c", hooks.POST_TOOL_USE_COMMAND],
                           input=json.dumps(self.call("id")), text=True,
                           capture_output=True, cwd=self.tmp,
                           env={"PATH": empty_path, "HOME": self.tmp,
                                "RECKON_HOME": self.tmp, "RECKON_CURRENT": "t"})
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.trace_lines(), [])

    def test_neither_reckon_hook_writes_a_trace_line(self):
        """The trace is the harness's job. `stop` writing one would make A3
        compare the log against itself."""
        self.blocked_plan()
        hooks.session_start("t", agent="a3")
        hooks.stop("t")
        self.assertEqual(self.any_trace_file(), [])


if __name__ == "__main__":
    unittest.main()
