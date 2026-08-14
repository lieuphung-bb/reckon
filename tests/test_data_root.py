"""Where reckon keeps its data, and where it writes what it renders.

The store lives outside the checkout, so cloning the tool does not make the
clone the store. Two things here are worth more than the rest:

  * **The hook resolves the root a second time, in shell.** `POST_TOOL_USE`
    is a shell string handed to the harness, so it cannot import anything and
    has to spell the default out again. If the two spellings disagree nothing
    raises — the trace goes on being written where nothing reads it, and A3
    unrecorded-work goes quiet. `TestHookAgreesWithTheModule` evaluates the
    hook's own expression in a real shell and compares.
  * **`OUT` is resolved once, not derived per call.** A test that redirects
    `RECKON_HOME` and forgets `OUT` writes an engagement's rendered console —
    every credential in it — into the operator's real data root, and passes
    while doing it. That happened once. Every test base that moves the home now
    moves `OUT` with it, and `TestOneResolvedLocation` keeps the two apart so a
    caller that rebuilds the path from the home cannot pass by coincidence.
"""

import os
import subprocess
import tempfile
import unittest

from reckon import api, cli, hooks, store
from reckon.render.views import VIEWS


class TestHomeDefault(unittest.TestCase):
    """§2 — the data root, resolved from the environment and nothing else."""

    def test_no_env_lands_under_local_share(self):
        home = store._resolve_home({"HOME": "/home/op"})
        self.assertEqual(home, "/home/op/.local/share/reckon")

    def test_xdg_data_home_is_honoured(self):
        home = store._resolve_home({"HOME": "/home/op",
                                    "XDG_DATA_HOME": "/data/xdg"})
        self.assertEqual(home, "/data/xdg/reckon")

    def test_reckon_home_wins_over_both(self):
        home = store._resolve_home({"HOME": "/home/op",
                                    "XDG_DATA_HOME": "/data/xdg",
                                    "RECKON_HOME": "/srv/eng"})
        self.assertEqual(home, "/srv/eng")

    def test_the_checkout_is_no_longer_the_default(self):
        """The whole point of the move: a clone is not the store."""
        home = store._resolve_home({"HOME": "/home/op"})
        self.assertNotIn("projects/reckon", home)

    def test_empty_values_fall_through_rather_than_resolving_to_nothing(self):
        """`RECKON_HOME=` in a shell profile is an unset root, not the root
        `""` — which would put every engagement at a relative path."""
        home = store._resolve_home({"HOME": "/home/op", "RECKON_HOME": "",
                                    "XDG_DATA_HOME": "  "})
        self.assertEqual(home, "/home/op/.local/share/reckon")

    def test_engagements_hangs_off_the_resolved_root(self):
        self.assertEqual(store.ENGAGEMENTS,
                         os.path.join(store.RECKON_HOME, "engagements"))


class TestOutDefault(unittest.TestCase):
    """§2 — output is separable from the log, in one direction only."""

    def test_defaults_to_out_under_the_data_root(self):
        out = store._resolve_out({"HOME": "/home/op"})
        self.assertEqual(out, "/home/op/.local/share/reckon/out")

    def test_follows_reckon_home_when_the_root_moves(self):
        out = store._resolve_out({"HOME": "/home/op", "RECKON_HOME": "/srv/eng"})
        self.assertEqual(out, "/srv/eng/out")

    def test_reckon_out_points_the_rendered_half_elsewhere(self):
        env = {"HOME": "/home/op", "RECKON_HOME": "/srv/eng",
               "RECKON_OUT": "/mnt/share/reckon"}
        self.assertEqual(store._resolve_out(env), "/mnt/share/reckon")
        # The log does not follow it. flock is unreliable over hgfs and NFS.
        self.assertEqual(store._resolve_home(env), "/srv/eng")

    def test_empty_reckon_out_falls_back(self):
        out = store._resolve_out({"HOME": "/home/op", "RECKON_OUT": ""})
        self.assertEqual(out, "/home/op/.local/share/reckon/out")


class TestHookAgreesWithTheModule(unittest.TestCase):
    """The trap: a hardcoded fallback inside a shell string, which no import
    can keep honest and no error will report."""

    def shell(self, env):
        r = subprocess.run(["sh", "-c", 'printf "%s" "' + hooks.HOME_EXPR + '"'],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_fallback_matches_the_module_default(self):
        env = {"HOME": "/home/op", "PATH": os.environ.get("PATH", "")}
        self.assertEqual(self.shell(env), store._resolve_home(env))

    def test_they_agree_on_xdg_data_home_too(self):
        env = {"HOME": "/home/op", "XDG_DATA_HOME": "/data/xdg",
               "PATH": os.environ.get("PATH", "")}
        self.assertEqual(self.shell(env), store._resolve_home(env))

    def test_they_agree_when_reckon_home_is_set(self):
        env = {"HOME": "/home/op", "RECKON_HOME": "/srv/eng",
               "PATH": os.environ.get("PATH", "")}
        self.assertEqual(self.shell(env), store._resolve_home(env))

    def test_the_hook_uses_that_expression_and_no_other(self):
        """Otherwise the tests above pin a constant the hook has stopped
        using."""
        self.assertIn(hooks.HOME_EXPR + "/engagements/",
                      hooks.POST_TOOL_USE_COMMAND)
        self.assertNotIn("projects/reckon", hooks.POST_TOOL_USE_COMMAND)


class TestOneResolvedLocation(unittest.TestCase):
    """checkpoint, console and views write to the same place, because they all
    read `store.OUT` rather than each rebuilding the path.

    The output root here is deliberately **not** under the data root, and not
    named `out`. Any caller that reassembles `<home>/out` for itself lands
    somewhere else and fails, which is the drift these tests exist to catch —
    with the two nested, every spelling would pass.
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

    def test_checkpoint_renders_into_out(self):
        rendered = api.checkpoint("t")["rendered"]
        self.assertTrue(rendered)
        for path in rendered:
            self.assertTrue(path.startswith(store.OUT + os.sep), path)
            self.assertTrue(os.path.exists(path), path)

    def test_console_and_views_land_beside_it(self):
        cli.main(["-e", "t", "console"])
        cli.main(["-e", "t", "views"])
        self.assertTrue(os.path.exists(os.path.join(store.OUT, "t.html")))
        for view in VIEWS:
            self.assertTrue(
                os.path.exists(os.path.join(store.OUT, "t", f"{view}.md")),
                view)

    def test_the_out_flag_still_overrides_per_call(self):
        dest = os.path.join(self.tmp, "elsewhere.html")
        cli.main(["-e", "t", "console", "--out", dest])
        self.assertTrue(os.path.exists(dest))
        self.assertFalse(os.path.exists(os.path.join(store.OUT, "t.html")))


if __name__ == "__main__":                  # pragma: no cover
    unittest.main()
