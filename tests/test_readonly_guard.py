"""RECKON_READONLY — the hard write-guard at the single choke point.

Incident this closes: an Arc-side read-only `--falsify` audit session had a
subagent run `reckon examine ...` through its allowed Bash, appending a write
to a LIVE engagement graph (cleared alarms). A tool-allowlist cannot
distinguish a read-shaped `reckon` invocation from a write-shaped one — both
are Bash — so the guard has to live inside reckon itself, at the point every
mutating op converges: `store.append` / `store.append_many`.

Modeled on `tests/test_api.py`'s `Base`: a temp `store.ENGAGEMENTS` swapped in
for the duration, restored after.
"""

import os
import tempfile
import unittest

from reckon import api, store


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = store.ENGAGEMENTS
        store.ENGAGEMENTS = self.tmp
        api.create("t")
        api.add_node("t", "host", "a", node_id="host:a", epistemic="verified")

    def tearDown(self):
        store.ENGAGEMENTS = self._old
        os.environ.pop("RECKON_READONLY", None)

    def _log_bytes(self):
        with open(store.path_for("t"), "rb") as fh:
            return fh.read()

    def _log_line_count(self):
        return self._log_bytes().count(b"\n")


class TestReadonlyEnabled(Base):
    """`$RECKON_READONLY` set truthy: every write path refuses, the log does
    not move, and reads are untouched."""

    def test_examine_raises_and_leaves_the_log_unchanged(self):
        before = self._log_bytes()
        os.environ["RECKON_READONLY"] = "1"
        with self.assertRaises(store.ReadOnlyRefused):
            api.examine("t", "host:a", outcome="tested")
        after = self._log_bytes()
        self.assertEqual(before, after, "a refused write must not touch the jsonl")

    def test_add_node_raises_and_leaves_the_log_unchanged(self):
        before_lines = self._log_line_count()
        os.environ["RECKON_READONLY"] = "1"
        with self.assertRaises(store.ReadOnlyRefused):
            api.add_node("t", "host", "b", node_id="host:b", epistemic="verified")
        self.assertEqual(self._log_line_count(), before_lines)

    def test_append_many_batch_path_also_refuses(self):
        """`plan_add`'s batch call, and anything else that calls
        `append_many` directly rather than through `append`, must refuse
        independently -- it is a second entry point, not a re-export."""
        before = self._log_bytes()
        os.environ["RECKON_READONLY"] = "1"
        with self.assertRaises(store.ReadOnlyRefused):
            store.append_many("t", [{"op": "note",
                                     "args": {"target_id": "host:a", "text": "x"}}])
        self.assertEqual(self._log_bytes(), before)

    def test_other_truthy_spellings_also_refuse(self):
        for val in ("true", "YES", "On"):
            with self.subTest(val=val):
                os.environ["RECKON_READONLY"] = val
                with self.assertRaises(store.ReadOnlyRefused):
                    api.examine("t", "host:a", outcome="tested")

    def test_reads_are_unaffected(self):
        """board/alarms/load — the falsify session's legitimate path — must
        keep working with the guard set."""
        os.environ["RECKON_READONLY"] = "1"
        g = store.load("t")
        self.assertIn("host:a", g.nodes)
        self.assertEqual(api.alarms("t"), api.alarms("t"))  # does not raise

    def test_cli_write_verb_exits_clean_not_a_raw_traceback(self):
        """§ cli.py main() already catches store.StoreError and turns it into
        `sys.exit(f"reckon: {exc}")`. ReadOnlyRefused subclasses StoreError,
        so it rides that existing handler rather than needing a new one."""
        from reckon import cli
        os.environ["RECKON_READONLY"] = "1"
        with self.assertRaises(SystemExit) as cm:
            cli.main(["-e", "t", "examine", "host:a", "tested"])
        self.assertIn("RECKON_READONLY", str(cm.exception))


class TestReadonlyUnsetOrFalsy(Base):
    """The env unset (today's behaviour) or set falsy: writes proceed."""

    def test_unset_writes_normally(self):
        before = self._log_line_count()
        api.examine("t", "host:a", outcome="tested")
        self.assertEqual(self._log_line_count(), before + 1)

    def test_falsy_values_also_write_normally(self):
        for val in ("0", "false", "", "no"):
            with self.subTest(val=val):
                os.environ["RECKON_READONLY"] = val
                before = self._log_line_count()
                api.examine("t", "host:a", outcome="tested")
                self.assertEqual(self._log_line_count(), before + 1)


if __name__ == "__main__":                  # pragma: no cover
    unittest.main()
