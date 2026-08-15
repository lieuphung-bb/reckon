"""Autorender: the board follows a write, and can never cost one.

The operator reads the engagement in an HTML console they keep open. Before
this, that file only regenerated when someone ran `checkpoint` or `console` by
hand, so the loop at a decision point was two acts and the second was the one
that gets forgotten — leaving the board in front of the operator behind the
work it is meant to mirror.

The test that matters here is `TestRenderFailureIsNonFatal`. Everything else
asserts the feature works; that one asserts it cannot do harm. Recording is
still the agent's act — nothing here writes a `decide`; it only renders what
was recorded.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

from reckon import api, cli, mcp, store


def env(**kw):
    """Patch the environment for one block, starting from a clean flag."""
    base = {"RECKON_AUTORENDER": "", "RECKON_CURRENT": "", "RECKON_AGENT": ""}
    return mock.patch.dict(os.environ, {**base, **kw})


class Base(unittest.TestCase):
    """A temp data root, with the output root deliberately NOT under it.

    Same trick as the data-root tests: `<tmp>/rendered` is neither inside the
    home nor named `out`, so anything that reassembles the path for itself
    instead of reading `store.OUT` lands somewhere else and fails the assertion
    — which is what makes criterion 7 an actual test rather than a tautology.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = store.ENGAGEMENTS, store.RECKON_HOME, store.OUT
        store.RECKON_HOME = os.path.join(self.tmp, "data")
        store.ENGAGEMENTS = os.path.join(store.RECKON_HOME, "engagements")
        store.OUT = os.path.join(self.tmp, "rendered")
        api.create("t")
        api.add_node("t", "host", "lab07", node_id="host:lab07",
                     epistemic="verified")

    def tearDown(self):
        store.ENGAGEMENTS, store.RECKON_HOME, store.OUT = self._old

    @property
    def html(self):
        return os.path.join(store.OUT, "t.html")

    def age(self, path, by=10):
        """Backdate a file so a regeneration is visible without sleeping."""
        old = os.path.getmtime(path) - by
        os.utime(path, (old, old))
        return old


class TestFlagResolution(unittest.TestCase):

    def test_truthy_spellings(self):
        for value in ("1", "true", "yes", "on", "TRUE", "On", " yes "):
            self.assertTrue(store.autorender_enabled({"RECKON_AUTORENDER": value}),
                            value)

    def test_everything_else_is_off(self):
        for value in ("", "0", "false", "no", "off", "maybe"):
            self.assertFalse(store.autorender_enabled({"RECKON_AUTORENDER": value}),
                             value)

    def test_unset_is_off(self):
        """Default off: an operator who does not watch a board pays nothing."""
        self.assertFalse(store.autorender_enabled({}))


class TestFlagOffChangesNothing(Base):
    """Criterion 1 — unset is today's behaviour exactly.

    The rest of the suite is the other half of this criterion: it runs with the
    flag unset and passes unchanged.
    """

    def test_mcp_write_renders_nothing(self):
        with env():
            mcp.dispatch("note", {"engagement": "t", "id": "host:lab07",
                                  "text": "quiet"})
        self.assertFalse(os.path.exists(store.OUT))

    def test_cli_write_renders_nothing(self):
        with env():
            cli.main(["-e", "t", "note", "host:lab07", "quiet"])
        self.assertFalse(os.path.exists(store.OUT))

    def test_checkpoint_still_renders_on_its_own(self):
        """The manual path is untouched — it never needed the flag."""
        with env():
            api.checkpoint("t")
        self.assertTrue(os.path.exists(self.html))


class TestAutorenderOnWrite(Base):

    def test_one_mcp_write_regenerates_the_console(self):
        """Criterion 2."""
        with env(RECKON_AUTORENDER="1"):
            mcp.dispatch("add_node", {"engagement": "t", "kind": "host",
                                      "label": "dc01", "id": "host:dc01"})
            self.assertTrue(os.path.exists(self.html))
            with open(self.html) as fh:
                self.assertIn("dc01", fh.read())

            was = self.age(self.html)
            mcp.dispatch("add_node", {"engagement": "t", "kind": "cred",
                                      "label": "svc-backup", "id": "cred:bk"})

        self.assertGreater(os.path.getmtime(self.html), was)
        with open(self.html) as fh:
            self.assertIn("svc-backup", fh.read())

    def test_one_cli_write_regenerates_the_console(self):
        """Criterion 3."""
        with env(RECKON_AUTORENDER="on"):
            cli.main(["-e", "t", "add", "host", "dc01", "--id", "host:dc01"])
            self.assertTrue(os.path.exists(self.html))

            was = self.age(self.html)
            cli.main(["-e", "t", "note", "host:dc01", "domain controller"])

        self.assertGreater(os.path.getmtime(self.html), was)

    def test_the_six_views_come_with_it(self):
        """The console is the tab the operator watches; the views are the same
        board's other faces and must not be left a write behind it."""
        from reckon.render.views import VIEWS
        with env(RECKON_AUTORENDER="1"):
            cli.main(["-e", "t", "note", "host:lab07", "seen"])
        for view in VIEWS:
            self.assertTrue(
                os.path.exists(os.path.join(store.OUT, "t", f"{view}.md")), view)

    def test_a_refused_write_renders_nothing(self):
        """No fact was recorded, so there is nothing for the board to catch up
        to — and a render here would claim otherwise."""
        with env(RECKON_AUTORENDER="1"):
            with self.assertRaises(api.ValidationError):
                mcp.dispatch("note", {"engagement": "t", "id": "host:NOPE",
                                      "text": "x"})
            with self.assertRaises(SystemExit):
                cli.main(["-e", "t", "note", "host:NOPE", "x"])
        self.assertFalse(os.path.exists(store.OUT))


class TestReadsNeverRender(Base):
    """Criterion 4 — autorender is a consequence of writes only.

    This is what the rejected dirty-flush-on-read design would have changed. It
    stays out: the flush would fire on an *agent* read, and the operator
    watching a browser tab is not one, so the tab would stay stale — the exact
    gap autorender closes.
    """

    def test_mcp_reads(self):
        with env(RECKON_AUTORENDER="1"):
            for tool, args in (("status", {}), ("delta", {}), ("board", {}),
                               ("alarms", {}), ("handoff", {}),
                               ("recall", {"node": "host:lab07"})):
                mcp.dispatch(tool, {"engagement": "t", **args})
                self.assertFalse(os.path.exists(store.OUT), tool)

    def test_cli_reads(self):
        with env(RECKON_AUTORENDER="1"):
            for argv in (["board"], ["status"], ["frontier"], ["unrealized"],
                         ["stale"], ["queue"], ["alarms"], ["log"], ["plans"]):
                cli.main(["-e", "t"] + argv)
                self.assertFalse(os.path.exists(store.OUT), argv)


class TestRenderFailureIsNonFatal(Base):
    """Criterion 5, and the one that matters.

    A write that succeeded must not be undone or reported as failed because
    rendering threw. The fact is already in the log; reporting failure would be
    a lie about the log, and an agent that believes it would record the thing
    twice. A broken renderer costs a stale board, never a fact.
    """

    def setUp(self):
        super().setUp()
        boom = mock.patch.object(api, "render_board",
                                 side_effect=RuntimeError("template exploded"))
        boom.start()
        self.addCleanup(boom.stop)

    def test_mcp_write_still_succeeds(self):
        err = io.StringIO()
        with env(RECKON_AUTORENDER="1"), redirect_stderr(err):
            out = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "decide", "arguments": {
                                  "engagement": "t", "chose": "kerberoast",
                                  "reason": "cheaper than a relay",
                                  "rejected": ["ntlm-relay"]}}})

        self.assertNotIn("isError", out["result"])
        ops = [e["op"] for e in store.read_events("t")]
        self.assertIn("decide", ops)
        self.assertIn("the write is recorded", err.getvalue())

    def test_cli_write_still_succeeds(self):
        err = io.StringIO()
        with env(RECKON_AUTORENDER="1"), redirect_stderr(err):
            cli.main(["-e", "t", "note", "host:lab07", "reachable from vpn"])

        self.assertIn("note", [e["op"] for e in store.read_events("t")])
        self.assertIn("the write is recorded", err.getvalue())

    def test_the_failure_is_said_out_loud_on_stderr(self):
        """Never stdout: on the CLI that is what a caller parses, and on the
        MCP server it is the JSON-RPC transport itself."""
        err = io.StringIO()
        with env(RECKON_AUTORENDER="1"), redirect_stderr(err):
            self.assertEqual(api.autorender("t"), [])
        self.assertIn("template exploded", err.getvalue())


class TestOutputLocation(Base):
    """Criterion 7 — autorender writes where ADR-002 says, not a second place."""

    def test_everything_lands_under_the_resolved_out(self):
        with env(RECKON_AUTORENDER="1"):
            cli.main(["-e", "t", "note", "host:lab07", "seen"])
        self.assertTrue(os.path.exists(self.html))
        # Nothing reassembled `<home>/out` for itself.
        self.assertFalse(os.path.exists(os.path.join(store.RECKON_HOME, "out")))

    def test_reckon_out_is_what_resolves_it(self):
        self.assertEqual(
            store._resolve_out({"HOME": "/home/op", "RECKON_HOME": "/srv/eng",
                                "RECKON_OUT": "/mnt/share/boards"}),
            "/mnt/share/boards")


class TestOneRenderer(Base):
    """Criterion 6 — the four surfaces cannot produce different documents.

    `checkpoint`, `console`, `views` and autorender all render through the same
    two functions, so the guarantee is structural. This asserts it anyway, at
    the byte level: a future edit that gives one surface its own copy of the
    render call passes review and fails here.

    Byte comparison is legitimate because the console and the views are pure
    functions of the graph — neither stamps a clock, unlike the handoff.
    """

    def setUp(self):
        super().setUp()
        api.add_node("t", "cred", "svc-backup", node_id="cred:bk",
                     epistemic="verified")
        api.add_edge("t", "cred:bk", "grants-access-to", "host:lab07",
                     epistemic="hypothesized")
        api.decide("t", "kerberoast", "cheaper than a relay", ["ntlm-relay"])

    def read(self, *parts):
        with open(os.path.join(*parts)) as fh:
            return fh.read()

    def test_render_board_console_is_render_console(self):
        board = os.path.join(self.tmp, "board")
        alone = os.path.join(self.tmp, "alone.html")
        api.render_board("t", out_dir=board)
        api.render_console(store.load("t"), "t", alone)
        self.assertEqual(self.read(board, "t.html"), self.read(alone))

    def test_render_board_views_are_render_views(self):
        from reckon.render.views import VIEWS
        board = os.path.join(self.tmp, "board")
        alone = os.path.join(self.tmp, "alone")
        api.render_board("t", out_dir=board)
        api.render_views(store.load("t"), "t", alone)
        for view in VIEWS:
            self.assertEqual(self.read(board, "t", f"{view}.md"),
                             self.read(alone, f"{view}.md"), view)

    def test_autorender_checkpoint_console_and_views_agree(self):
        """The four surfaces, on one graph, byte for byte.

        The write that triggers autorender happens FIRST and once; everything
        after it renders the same post-write graph, so any difference is the
        renderer disagreeing with itself rather than the graph having moved.
        """
        from reckon.render.views import VIEWS
        with env(RECKON_AUTORENDER="1"):
            cli.main(["-e", "t", "note", "host:lab07", "reachable from vpn"])
        auto_html = self.read(self.html)
        auto_views = {v: self.read(store.OUT, "t", f"{v}.md") for v in VIEWS}

        checkpointed = api.checkpoint("t")["rendered"]
        self.assertEqual(self.read(self.html), auto_html)
        self.assertTrue(checkpointed)

        elsewhere = os.path.join(self.tmp, "by-hand")
        cli.main(["-e", "t", "console", "--out",
                  os.path.join(elsewhere, "t.html")])
        cli.main(["-e", "t", "views", "--out", elsewhere])
        self.assertEqual(self.read(elsewhere, "t.html"), auto_html)
        for view in VIEWS:
            self.assertEqual(self.read(elsewhere, f"{view}.md"),
                             auto_views[view], view)


class TestTriggerSets(unittest.TestCase):
    """The two lists are the whole trigger surface, so drift in either is a
    write that silently stops refreshing the board."""

    def test_every_write_tool_is_a_real_tool(self):
        self.assertTrue(mcp.WRITE_TOOLS <= {t["name"] for t in mcp.TOOLS})

    def test_no_read_or_checkpoint_in_the_write_set(self):
        for tool in ("status", "delta", "board", "why", "recall", "alarms",
                     "handoff", "checkpoint"):
            self.assertNotIn(tool, mcp.WRITE_TOOLS)

    def test_cli_write_commands_are_all_wired_to_the_parser(self):
        parser = cli.build_parser()
        wired = set()
        for action in parser._subparsers._group_actions:
            for sub in action.choices.values():
                wired.add(sub.get_default("func"))
                nested = getattr(sub, "_subparsers", None)
                if nested:
                    for act in nested._group_actions:
                        for deep in act.choices.values():
                            wired.add(deep.get_default("func"))
        self.assertTrue(cli.WRITE_COMMANDS <= wired,
                        cli.WRITE_COMMANDS - wired)


if __name__ == "__main__":                  # pragma: no cover
    unittest.main()
