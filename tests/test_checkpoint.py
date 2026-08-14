"""Checkpoint and the alarm set — SPEC-004 §3, §4 and the §8 criteria.

The spec's argument is that a checkpoint depending on an agent remembering to
record is one that will eventually render a beautiful, confident, hours-old
picture. So every alarm here is computed from the log, and the tests care most
about two things: that the two markers cannot satisfy each other, and that an
alarm fires on evidence rather than on a plausible-looking substitute for it.

A2 vs A3 is where the second point bites. A2 cannot distinguish "nothing
happened" from "nothing was recorded" — only A3 can, by comparing the log
against the trace, an independent signal of activity. So `TestA3` asserts both
directions: it fires when the trace is newer than the last authored event, and
it stays silent when there is no trace, because an alarm inferred without
evidence would be inventing the very confidence this spec exists to remove.
A4 and A6 remain dark, and say why.
"""

import json
import os
import tempfile
import unittest

from reckon import api, mcp, store
from reckon.render.checkpoint import checkpoint as render
from tests import append_trace


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_e, self._old_h = store.ENGAGEMENTS, store.RECKON_HOME
        store.ENGAGEMENTS = self.tmp
        store.RECKON_HOME = self.tmp
        api.create("t")
        api.add_node("t", "host", "lab07", node_id="host:lab07",
                     epistemic="verified")
        api.add_edge("t", "operator:me", "grants-access-to", "host:lab07",
                     edge_id="e:op-lab07", epistemic="verified",
                     props={"rank": 3})
        api.add_node("t", "objective", "to DA", node_id="obj:t21",
                     crown=True, requires=["host:lab07@3"])

    def tearDown(self):
        store.ENGAGEMENTS, store.RECKON_HOME = self._old_e, self._old_h

    def age_the_log(self, minutes):
        """Rewrite every timestamp to N minutes ago — A1 is the one alarm that
        depends on wall-clock time rather than on the log's contents."""
        from datetime import datetime, timedelta, timezone
        when = (datetime.now(timezone.utc)
                - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        path = store.path_for("t")
        with open(path) as fh:
            events = [json.loads(line) for line in fh if line.strip()]
        with open(path, "w") as fh:
            for ev in events:
                fh.write(json.dumps({**ev, "ts": when}, sort_keys=True) + "\n")


class TestOneInvocation(Base):
    """§8.1 — delta, alarms, regeneration and stamping, with no judgment step."""

    def test_checkpoint_does_all_four_things(self):
        c = api.checkpoint("t")
        self.assertEqual(c["seq"], store.load("t").seq)
        self.assertIn("alarms", c)
        self.assertTrue(c["rendered"])
        self.assertTrue(all(os.path.exists(p) for p in c["rendered"]))
        self.assertTrue(c["stamped"])
        self.assertEqual(api.last_checkpoint("t"), c["seq"])

    def test_no_render_skips_regeneration_only(self):
        c = api.checkpoint("t", render=False)
        self.assertEqual(c["rendered"], [])
        self.assertTrue(c["stamped"])
        self.assertIn("alarms", c)


class TestMarkersAreIndependent(Base):
    """§8.2 — the criterion that keeps a read from satisfying the ritual."""

    def test_delta_does_not_satisfy_a_checkpoint(self):
        api.checkpoint("t", render=False)
        api.add_node("t", "cred", "x", node_id="cred:x")

        api.delta("t")                      # a plain read, which moves `.seen`

        self.assertLess(api.last_checkpoint("t"), store.load("t").seq)
        self.assertNotIn("A2", [a["id"] for a in api.alarms("t")])

    def test_checkpoint_does_not_move_the_seen_marker(self):
        api.delta("t")
        seen_before = api.last_seen("t")
        api.add_node("t", "cred", "x", node_id="cred:x")

        api.checkpoint("t", render=False)

        self.assertEqual(api.last_seen("t"), seen_before)
        self.assertEqual(api.last_checkpoint("t"), store.load("t").seq)


class TestDryRun(Base):
    """§8.3 — identical output, stamps nothing."""

    def test_dry_run_produces_the_same_brief_and_moves_no_marker(self):
        before = api.last_checkpoint("t")
        dry = api.checkpoint("t", render=False, dry_run=True)
        self.assertEqual(api.last_checkpoint("t"), before)
        self.assertFalse(dry["stamped"])

        wet = api.checkpoint("t", render=False)
        for key in ("seq", "since", "events", "alarms", "delta", "coverage"):
            self.assertEqual(dry[key], wet[key], key)
        self.assertEqual(render({**dry, "stamped": True}), render(wet))


class TestAlarms(Base):

    def test_A1_fires_only_once_the_log_is_older_than_the_threshold(self):
        self.assertNotIn("A1", [a["id"] for a in api.alarms("t")])
        self.age_the_log(api.STALE_RECORDING_MINUTES + 5)
        a1 = next(a for a in api.alarms("t") if a["id"] == "A1")
        self.assertEqual(a1["group"], api.RECORDING)
        self.assertIn("behind the work", a1["why"])

    def test_A2_fires_when_nothing_happened_since_the_last_checkpoint(self):
        api.checkpoint("t", render=False)
        a2 = next(a for a in api.alarms("t") if a["id"] == "A2")
        self.assertEqual(a2["group"], api.RECORDING)
        # It must say plainly that it cannot tell the two cases apart.
        self.assertIn("cannot tell you which", a2["why"])

        api.add_node("t", "cred", "x", node_id="cred:x")
        self.assertNotIn("A2", [a["id"] for a in api.alarms("t")])

    def test_A5_fires_until_a_decision_is_recorded(self):
        api.checkpoint("t", render=False)
        self.assertIn("A5", [a["id"] for a in api.alarms("t")])
        api.decide("t", "push on lab07", reason="already held")
        self.assertNotIn("A5", [a["id"] for a in api.alarms("t")])

    def test_A7_tracks_the_change_ledger_and_clears_with_it(self):
        cid = api.change("t", "host:lab07", "dropped /tmp/pyk.py")
        a7 = next(a for a in api.alarms("t") if a["id"] == "A7")
        self.assertEqual(a7["group"], api.ENGAGEMENT)
        self.assertEqual(len(a7["detail"]["changes"]), 1)
        api.mark_cleaned("t", cid)
        self.assertNotIn("A7", [a["id"] for a in api.alarms("t")])

    def test_dark_alarms_stay_dark_and_say_why(self):
        dark = {a[0] for a in api.DARK_ALARMS}
        self.assertEqual(dark, {"A4", "A6"})
        for _ in range(3):
            self.assertFalse({a["id"] for a in api.alarms("t")} & dark)
        text = render(api.checkpoint("t", render=False))
        for aid in dark:
            self.assertIn(aid, text)        # visible as not-yet-computed


class TestA3(Base):
    """§8.5, the criterion the spec calls the real one.

    A2 cannot distinguish "nothing happened" from "nothing was recorded" — both
    are an empty delta, and they demand opposite responses. A3 resolves it, and
    it is the only alarm that can, because it is the only one comparing the log
    against an independent signal of activity.
    """

    def trace(self, *cmds, minutes_ago=None):
        """Append trace lines as the §5.2 hook would have written them."""
        at = None
        if minutes_ago is not None:
            from datetime import datetime, timedelta, timezone
            at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        return append_trace("t", *cmds, at=at)

    def fired(self):
        return [a["id"] for a in api.alarms("t")]

    def test_A3_fires_when_the_trace_is_newer_than_the_last_recorded_event(self):
        self.assertNotIn("A3", self.fired())
        self.trace(*[f"nxc smb 10.99.10.{i}" for i in range(47)])

        a3 = next(a for a in api.alarms("t") if a["id"] == "A3")
        self.assertEqual(a3["group"], api.RECORDING)
        self.assertEqual(a3["detail"]["tool_calls"], 47)
        self.assertIn("47 tool call(s)", a3["why"])

    def test_A3_does_not_fire_on_an_empty_or_absent_trace(self):
        """The other direction, and the one that keeps 'quiet' meaningful: with
        no independent signal there is no evidence either way, and guessing
        from a weaker one is the confidence this spec exists to remove."""
        self.assertFalse(os.path.exists(store.trace_path_for("t")))
        self.assertNotIn("A3", self.fired())

        open(store.trace_path_for("t"), "w").close()      # present but empty
        self.assertNotIn("A3", self.fired())

        with open(store.trace_path_for("t"), "a") as fh:  # and blank lines
            fh.write("\n\n")
        self.assertNotIn("A3", self.fired())

    def test_recording_an_event_clears_A3(self):
        """The remedy has to work, or the alarm is just noise."""
        self.age_the_log(10)
        self.trace("nxc smb 10.99.10.5", minutes_ago=5)
        self.assertIn("A3", self.fired())

        api.add_node("t", "cred", "j.rivera", node_id="cred:jr")
        self.assertNotIn("A3", self.fired())

    def test_A3_and_A2_separate_quiet_from_unrecorded(self):
        """The whole argument of §4.1, as one test: two engagements with an
        identical empty delta, told apart only by the trace."""
        api.checkpoint("t", render=False)
        self.assertEqual(self.fired().count("A2"), 1)
        quiet = self.fired()

        self.trace("smbclient -L //10.99.10.5", "hashcat -m 1000 h.txt")
        unrecorded = self.fired()

        self.assertNotIn("A3", quiet)
        self.assertIn("A3", unrecorded)
        self.assertIn("A2", unrecorded)       # the delta is empty in both cases

    def test_a_stamped_checkpoint_does_not_silence_A3(self):
        """The checkpoint marker is a sidecar file, not an event, so the Stop
        hook's own checkpoint cannot reset the clock A3 measures against."""
        self.trace("nxc smb 10.99.10.5")
        api.checkpoint("t", render=False)
        self.assertIn("A3", self.fired())

    def test_A3_ignores_lines_it_cannot_place_in_time(self):
        """A3 claims work provably happened. A line with no readable timestamp
        proves nothing, and an alarm reporting on garbage loses its reader."""
        with open(store.trace_path_for("t"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"tool": "Bash", "cmd": "id"}) + "\n")
            fh.write(json.dumps({"ts": "not a date", "cmd": "id"}) + "\n")
            fh.write("{ half a line, the machine went dow\n")
        self.assertNotIn("A3", self.fired())

        self.trace("id")                       # one line that does parse
        self.assertEqual(
            next(a for a in api.alarms("t") if a["id"] == "A3")
            ["detail"]["tool_calls"], 1)

    def test_A3_carries_the_count_and_the_age(self):
        """§7's sample line: '47 tool calls since the last recorded event
        (18m)'."""
        self.age_the_log(18)
        self.trace("id", "whoami")
        a3 = next(a for a in api.alarms("t") if a["id"] == "A3")
        self.assertEqual(a3["detail"]["tool_calls"], 2)
        self.assertEqual(a3["detail"]["minutes"], 18)
        self.assertIn("(18m)", a3["why"])

    def test_A3_fires_when_nothing_at_all_has_been_recorded(self):
        """An engagement with tool calls and an empty log is the purest case of
        the failure the spec is about."""
        store.create("fresh", force=True)
        with open(store.trace_path_for("fresh"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": "2026-08-08T11:02:41Z", "tool": "Bash",
                                 "cmd": "id", "exit": 0}) + "\n")
        self.assertIn("A3", [a["id"] for a in api.alarms("fresh")])

    def test_A3_prints_in_the_recording_health_section_above_everything(self):
        self.trace("nxc smb 10.99.10.5")
        text = render(api.checkpoint("t", render=False))
        self.assertIn("A3 unrecorded-work", text)
        self.assertLess(text.index("A3"), text.index("What changed"))
        self.assertNotIn("A3", text.split("## Next")[-1])   # not in the dark list


class TestStrict(Base):
    """§8.4 — exit 2 on recording health, 0 on engagement health."""

    def test_strict_fails_on_a_recording_alarm(self):
        api.checkpoint("t", render=False)                 # arm A2
        self.assertTrue(api.checkpoint("t", render=False,
                                       strict=True)["strict_fail"])

    def test_strict_passes_when_only_engagement_alarms_fire(self):
        api.change("t", "host:lab07", "dropped a file")   # A7
        api.decide("t", "something", reason="so A5 is quiet")
        c = api.checkpoint("t", render=False, strict=True)
        self.assertIn("A7", [a["id"] for a in c["alarms"]])
        self.assertFalse([a for a in c["alarms"] if a["group"] == api.RECORDING])
        self.assertFalse(c["strict_fail"])

    def test_strict_is_opt_in(self):
        api.checkpoint("t", render=False)
        self.assertFalse(api.checkpoint("t", render=False)["strict_fail"])


class TestOutput(Base):
    """§8.11 — recording health prints above all other sections."""

    def test_recording_health_is_the_first_section(self):
        api.checkpoint("t", render=False)                 # arm A2
        self.age_the_log(api.STALE_RECORDING_MINUTES + 5)  # and A1
        text = render(api.checkpoint("t", render=False))
        headings = [l for l in text.splitlines() if l.startswith("## ")]
        self.assertEqual(headings[0], "## ⚠ Recording health")
        self.assertLess(text.index("Recording health"), text.index("What changed"))
        self.assertLess(text.index("Recording health"), text.index("Where you are"))

    def test_a_healthy_checkpoint_has_no_recording_section(self):
        api.add_node("t", "cred", "x", node_id="cred:x")
        text = render(api.checkpoint("t", render=False))
        self.assertNotIn("Recording health", text)

    def test_the_brief_names_what_changed_and_what_to_do_next(self):
        api.checkpoint("t", render=False)
        api.add_node("t", "cred", "dcc2", node_id="cred:dcc2",
                     epistemic="verified")
        text = render(api.checkpoint("t", render=False))
        self.assertIn("cred:dcc2", text)
        self.assertIn("to DA", text)                      # unrealized objective
        self.assertIn("reckon decide", text)              # A5's remedy

    def test_composing_delta_leaves_delta_usable_alone(self):
        """§2 — checkpoint composes delta; delta remains usable on its own."""
        api.checkpoint("t", render=False)
        api.add_node("t", "cred", "x", node_id="cred:x")
        self.assertEqual(api.checkpoint("t", render=False)["delta"]["events"], 1)
        self.assertTrue(api.delta("t")["new_nodes"])


class TestSurfaces(Base):

    def test_mcp_exposes_checkpoint_and_alarms(self):
        names = [t["name"] for t in mcp.TOOLS]
        self.assertIn("checkpoint", names)
        self.assertIn("alarms", names)
        tool = next(t for t in mcp.TOOLS if t["name"] == "checkpoint")
        self.assertIn("update checkpoint", tool["description"])
        out = mcp.dispatch("checkpoint", {"engagement": "t", "render": False})
        self.assertIn("# Checkpoint", out)

    def test_trace_is_not_exposed_over_mcp(self):
        """An agent re-reading its own raw command history is a context-cost
        trap; A3 already surfaces the only fact that matters about it."""
        self.assertNotIn("trace", [t["name"] for t in mcp.TOOLS])


if __name__ == "__main__":
    unittest.main()
