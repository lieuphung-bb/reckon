"""Reading the trace — SPEC-004 §5.2, §5.4 and criteria §8.9, §8.10.

Two properties carry this file.

**The trace is evidence, not interpretation.** It is written by a shell
one-liner on the harness's schedule, so the reader is tolerant where the event
log's reader is loud: a half-written line means the machine went down
mid-append, not that the file is worthless. And nothing in it ever reaches the
graph — §8.9 is the criterion, and it is what makes the trace safe to keep
appending to at hundreds of lines per session.

**§5.4 proposes and never asserts.** A pattern match says a command is worth
looking at, not that anything changed on the target.
"""

import json
import os
import tempfile
import unittest

from reckon import api, store
from tests import append_trace


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_e, self._old_h = store.ENGAGEMENTS, store.RECKON_HOME
        store.ENGAGEMENTS = os.path.join(self.tmp, "engagements")
        store.RECKON_HOME = self.tmp
        api.create("t")
        api.add_node("t", "host", "lab07", node_id="host:lab07",
                     epistemic="verified")

    def tearDown(self):
        store.ENGAGEMENTS, store.RECKON_HOME = self._old_e, self._old_h

    def raw(self, *lines):
        with open(store.trace_path_for("t"), "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")


class TestTracePath(Base):

    def test_the_trace_sits_beside_the_log_and_is_not_the_log(self):
        self.assertEqual(store.trace_path_for("t"),
                         store.path_for("t").replace(".jsonl", ".trace.jsonl"))
        self.assertNotEqual(store.trace_path_for("t"), store.path_for("t"))

    def test_an_invalid_name_is_refused_the_same_way(self):
        for bad in ("../escape", ".hidden", "with/slash", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(store.StoreError):
                    store.trace_path_for(bad)

    def test_a_trace_file_is_not_listed_as_an_engagement(self):
        """`<name>.trace.jsonl` also ends in .jsonl, and would otherwise list as
        an engagement called "t.trace"."""
        append_trace("t", "id")
        self.assertEqual(store.list_engagements(), ["t"])


class TestReaderIsTolerant(Base):
    """Deliberately unlike `read_events`, and the docstring in `store` says
    why: the log is written under a lock by this process, the trace by a shell
    one-liner on someone else's schedule."""

    def test_a_corrupt_line_is_skipped_rather_than_fatal(self):
        append_trace("t", "id")
        self.raw("{ half a line, the machine went dow")
        append_trace("t", "whoami")
        self.assertEqual([r["cmd"] for r in api.trace("t")], ["id", "whoami"])

    def test_the_same_corruption_in_the_event_log_is_still_loud(self):
        """The tolerance is scoped to evidence. It must not have leaked into
        the log, where a corrupt line is a real defect."""
        with open(store.path_for("t"), "a") as fh:
            fh.write("{ not json\n")
        with self.assertRaises(store.StoreError):
            store.load("t")

    def test_blank_lines_and_non_objects_are_skipped(self):
        self.raw("", "   ", "[1,2,3]", '"a bare string"', "null")
        append_trace("t", "id")
        self.assertEqual([r["cmd"] for r in api.trace("t")], ["id"])

    def test_an_absent_trace_reads_as_empty_not_as_an_error(self):
        self.assertEqual(api.trace("t"), [])
        self.assertEqual(api.suggest_changes("t"), [])

    def test_a_line_missing_fields_still_reads(self):
        """The shape is a contract with a shell command, and a shell command
        will one day be edited by hand."""
        self.raw(json.dumps({"cmd": "id"}))
        self.assertEqual(api.trace("t")[0]["cmd"], "id")
        self.assertEqual(api.trace("t")[0]["seq"], 1)


class TestTraceReads(Base):

    def test_seq_is_the_line_position_and_since_reads_after_it(self):
        append_trace("t", *[f"cmd-{i}" for i in range(10)])
        self.assertEqual([r["seq"] for r in api.trace("t")], list(range(1, 11)))
        self.assertEqual([r["cmd"] for r in api.trace("t", since=8)],
                         ["cmd-8", "cmd-9"])
        self.assertEqual(api.trace("t", since=10), [])

    def test_limit_takes_the_most_recent_and_0_takes_everything(self):
        append_trace("t", *[f"cmd-{i}" for i in range(150)])
        self.assertEqual(len(api.trace("t")), 100)          # the default
        self.assertEqual(api.trace("t")[-1]["cmd"], "cmd-149")
        self.assertEqual(len(api.trace("t", limit=3)), 3)
        self.assertEqual(len(api.trace("t", limit=0)), 150)
        self.assertEqual(len(api.trace("t", limit=None)), 150)

    def test_since_and_limit_compose(self):
        append_trace("t", *[f"cmd-{i}" for i in range(10)])
        self.assertEqual([r["cmd"] for r in api.trace("t", since=5, limit=2)],
                         ["cmd-8", "cmd-9"])


class TestTraceNeverEntersTheGraph(Base):
    """§8.9 — the criterion that makes the trace safe to keep appending to."""

    def test_100_trace_lines_leave_the_graph_identical(self):
        api.add_edge("t", "operator:me", "grants-access-to", "host:lab07",
                     edge_id="e:op-lab07", epistemic="verified")
        before = store.load("t")
        log_before = open(store.path_for("t")).read()

        append_trace("t", *[f"nxc smb 10.99.10.{i} -u u -p p" for i in range(100)])

        after = store.load("t")
        self.assertEqual(len(api.trace("t", limit=0)), 100)
        self.assertEqual(after.seq, before.seq)
        self.assertEqual(set(after.nodes), set(before.nodes))
        self.assertEqual(set(after.edges), set(before.edges))
        self.assertEqual(after.changes, before.changes)
        self.assertEqual(after.decisions, before.decisions)
        self.assertEqual(open(store.path_for("t")).read(), log_before)

    def test_a_trace_full_of_recordable_looking_commands_records_nothing(self):
        """The tempting failure: these all look like events, and folding them
        would be interpretation done by a regex."""
        append_trace("t", "reg add HKLM\\Software\\X /v Y /d Z",
                     "useradd -m svc_backup", "cp /etc/shadow /tmp/s")
        before = store.load("t")
        self.assertEqual(api.status("t")["changes"], [])
        self.assertEqual(store.load("t").seq, before.seq)


class TestSuggestChanges(Base):
    """§5.4 and §8.10."""

    def test_each_documented_pattern_is_proposed(self):
        cmds = {
            ">": "echo 'x' > /var/www/html/shell.php",
            "tee": "echo data | tee -a /etc/hosts",
            "cp": "cp /tmp/pyk.py /opt/app/pyk.py",
            "mv": "mv /etc/passwd.bak /etc/passwd",
            "sed -i": "sed -i s/PermitRootLogin/#/ /etc/ssh/sshd_config",
            "useradd": "useradd -m -s /bin/bash svc_backup",
            "net user": "net user svc_backup Passw0rd! /add",
            "reg add": "reg add HKLM\\System\\CCS\\Services\\X /v Start /d 2",
            "msiexec": "msiexec /i \\\\10.99.10.5\\share\\agent.msi /qn",
            "schtasks": "schtasks /create /tn Updater /tr c:\\u.exe /sc onlogon",
        }
        append_trace("t", *cmds.values())
        got = {p["pattern"]: p["cmd"] for p in api.suggest_changes("t")}
        for pattern, cmd in cmds.items():
            with self.subTest(pattern=pattern):
                self.assertEqual(got.get(pattern), cmd)

    def test_a_read_only_command_is_not_proposed(self):
        append_trace("t", "id", "whoami", "nxc smb 10.99.10.5 -u j.rivera",
                     "cat /etc/passwd", "ls -la /opt")
        self.assertEqual(api.suggest_changes("t"), [])

    def test_a_redirect_to_dev_null_is_not_a_change(self):
        """Half the commands in any trace carry `2>/dev/null`, and a proposal
        list that is mostly noise is a list nobody reads."""
        append_trace("t", "nxc smb 10.99.10.5 2>/dev/null",
                     "ping -c1 10.99.10.5 >/dev/null 2>&1")
        self.assertEqual(api.suggest_changes("t"), [])

    def test_a_repeated_command_is_one_proposal_with_a_count(self):
        append_trace("t", *["cp /tmp/pyk.py /opt/app/pyk.py"] * 3)
        proposals = api.suggest_changes("t")
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["count"], 3)

    def test_a_proposal_carries_its_evidence_and_the_line_that_confirms_it(self):
        append_trace("t", "sed -i s/x/y/ /etc/ssh/sshd_config", agent="a3")
        p, = api.suggest_changes("t")
        self.assertEqual(p["agent"], "a3")
        self.assertEqual(p["cmd"], "sed -i s/x/y/ /etc/ssh/sshd_config")
        self.assertTrue(p["ts"])
        self.assertIn("reckon change", p["confirm"])
        self.assertEqual(p["target"], "",
                         "a shell command names paths, not graph nodes — "
                         "guessing the target is the interpretation step")

    def test_suggest_writes_nothing(self):
        """§8.10. The whole surface is a read."""
        append_trace("t", "useradd -m svc_backup", "cp a b")
        log = open(store.path_for("t")).read()
        trace = open(store.trace_path_for("t")).read()

        self.assertTrue(api.suggest_changes("t"))
        self.assertTrue(api.suggest_changes("t"))       # twice, for luck

        self.assertEqual(open(store.path_for("t")).read(), log)
        self.assertEqual(open(store.trace_path_for("t")).read(), trace)
        self.assertEqual(api.changes("t"), [])
        self.assertEqual(store.load("t").changes, [])

    def test_confirming_a_proposal_drops_it_from_the_list(self):
        """Otherwise the list repeats what you have already recorded, and a
        list that never shrinks stops being read."""
        cmd = "cp /tmp/pyk.py /opt/app/pyk.py"
        append_trace("t", cmd, "useradd -m svc_backup")
        self.assertEqual(len(api.suggest_changes("t")), 2)

        api.change("t", "host:lab07", "dropped a payload", revert_hint=cmd)

        left = api.suggest_changes("t")
        self.assertEqual([p["pattern"] for p in left], ["useradd"])


if __name__ == "__main__":
    unittest.main()
