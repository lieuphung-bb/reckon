"""`--brief` for `handoff` and `delta`.

Both commands are nominally "summary views" but render full record bodies
verbatim: `handoff`'s "Already ruled out" section prints every decision's
complete rationale, and `delta` prints a new finding's complete label. A
caller reading either to orient takes that dense history into its window.

`--brief` keeps the STRUCTURE (headings, counts, the resume point) and
replaces each full record BODY with a one-line headline + its id, so a
reader gets "what was ruled out / found, and where to look" without the
verbatim text. Without the flag, output is unchanged — asserted here as
byte-for-byte identity against the pre-flag render.
"""

import tempfile
import unittest

from reckon import api, cli, store
from reckon.render.handoff import handoff as render_handoff


LONG_RATIONALE = (
    "box01 was exhausted after the local admin path, the BYOVD driver load, "
    "and a scheduled-task hijack all failed against the same EDR product, so "
    "the pivot goes through the .31 segment instead, which is reachable from "
    "the cred already held and was not covered by any of the three attempts."
)
LONG_RATIONALE_2 = (
    "the msDS-KeyCredentialLink write was refused by the model as a shadow "
    "credential attack even after the substep was narrowed to a single LDAP "
    "modify, so PKINIT is off the table for this session and the successor "
    "should route the same substep to the other provider before trying again."
)
LONG_FINDING_1 = (
    "svc-backup on host03 runs as LocalSystem and reads an ini file from a "
    "world-writable directory on startup, so a planted DLL or an ini "
    "redirect executing arbitrary code gets SYSTEM the next time the "
    "service is bounced or the host reboots, and no reboot has been forced "
    "yet to confirm it."
)
LONG_FINDING_2 = (
    "the Gitea instance on host07 accepts the same j.rivera credential the "
    "Kerberos surfaces rejected, which means the earlier 'dead cred' verdict "
    "was scoped to one surface, not the credential itself, and every repo "
    "that account can read is now in scope for a secrets sweep."
)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = store.ENGAGEMENTS
        store.ENGAGEMENTS = self.tmp
        api.create("t")
        api.add_node("t", "host", "lab07", node_id="host:lab07",
                     epistemic="verified")
        api.add_edge("t", "operator:me", "grants-access-to", "host:lab07",
                     edge_id="e:op-lab07", epistemic="verified",
                     props={"rank": 3})
        api.add_node("t", "objective", "shadow-cred to DA", node_id="obj:t21",
                     crown=True, requires=["host:lab07@3"])

    def tearDown(self):
        store.ENGAGEMENTS = self._old


# --- handoff -------------------------------------------------------------

class TestHandoffBrief(Base):

    def _decide_twice(self):
        api.decide("t", "pivot via .31", reason=LONG_RATIONALE,
                   rejected=["brute box01", "BYOVD"])
        api.decide("t", "route to other provider", reason=LONG_RATIONALE_2,
                   rejected=["retry as written"])

    def test_non_brief_contains_the_full_rationale(self):
        self._decide_twice()
        h = api.handoff("t")
        text = render_handoff(h)
        self.assertIn(LONG_RATIONALE, text)
        self.assertIn(LONG_RATIONALE_2, text)

    def test_brief_omits_the_full_rationale_but_keeps_id_and_headline(self):
        self._decide_twice()
        h = api.handoff("t")
        seqs = [d["seq"] for d in h["ruled_out"]]
        text = render_handoff(h, brief=True)

        self.assertNotIn(LONG_RATIONALE, text)
        self.assertNotIn(LONG_RATIONALE_2, text)
        # the headline (a truncated prefix of the body) and the pointer are
        # both present, so the record can be pulled back deliberately.
        self.assertIn(LONG_RATIONALE[:60], text)
        self.assertIn(LONG_RATIONALE_2[:60], text)
        for seq in seqs:
            self.assertIn(f"dec:{seq}", text)
        # the headline is what was chosen, too.
        self.assertIn("pivot via .31", text)
        self.assertIn("route to other provider", text)

    def test_brief_keeps_the_resume_point_and_structural_headers(self):
        pid = api.plan_add("t", "obj:t21", "shadow-cred to DA",
                           steps=["dump hives", "extract DCC2"])
        api.step_state("t", pid, 1, "done", produced=[])
        self._decide_twice()
        h = api.handoff("t")
        text = render_handoff(h, brief=True)

        self.assertIn("## Resume here", text)
        self.assertIn("## Position", text)
        self.assertIn("## Already ruled out", text)
        self.assertIn("Step 2 of 2", text)          # resume point still named

    def test_cli_brief_flag_is_wired(self):
        self._decide_twice()
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["-e", "t", "handoff", "--brief"])
        out = buf.getvalue()
        self.assertNotIn(LONG_RATIONALE, out)
        self.assertIn("Already ruled out", out)


# --- delta -----------------------------------------------------------------

class TestDeltaBrief(Base):

    def _findings_and_decision(self):
        api.delta("t")                                     # stamp the marker
        api.add_node("t", "finding", LONG_FINDING_1, node_id="finding:svc-backup")
        api.add_node("t", "finding", LONG_FINDING_2, node_id="finding:gitea-cred")
        api.decide("t", "sweep gitea repos", reason=LONG_RATIONALE,
                   rejected=["assume dead cred"])

    def _render(self, brief):
        """Always `--since 0`: reading history must not disturb the marker,
        so both a brief and a non-brief render see the same events."""
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            argv = ["-e", "t", "delta", "--since", "0"]
            if brief:
                argv.append("--brief")
            cli.main(argv)
        return buf.getvalue()

    def test_non_brief_contains_the_full_finding_body_and_rationale(self):
        self._findings_and_decision()
        text = self._render(brief=False)
        self.assertIn(LONG_FINDING_1, text)
        self.assertIn(LONG_FINDING_2, text)
        self.assertIn(LONG_RATIONALE, text)

    def test_brief_omits_full_bodies_but_keeps_id_and_headline(self):
        self._findings_and_decision()
        d = api.delta("t", since=0)      # re-read without disturbing the marker
        text = self._render(brief=True)

        self.assertNotIn(LONG_FINDING_1, text)
        self.assertNotIn(LONG_FINDING_2, text)
        self.assertNotIn(LONG_RATIONALE, text)

        self.assertIn(LONG_FINDING_1[:60], text)
        self.assertIn(LONG_FINDING_2[:60], text)
        self.assertIn(LONG_RATIONALE[:60], text)

        self.assertIn("finding:svc-backup", text)
        self.assertIn("finding:gitea-cred", text)
        dec_seq = next(x["seq"] for x in d["decisions"] if x["chose"] == "sweep gitea repos")
        self.assertIn(f"dec:{dec_seq}", text)

    def test_brief_keeps_change_counts_and_section_structure(self):
        self._findings_and_decision()
        full = self._render(brief=False)
        brief = self._render(brief=True)
        # same header line (from_seq/to_seq/events) and same section titles —
        # only the body under "new nodes"/"decisions" changes shape.
        header = full.splitlines()[0]
        self.assertEqual(header, brief.splitlines()[0])
        self.assertIn("new nodes", full)
        self.assertIn("new nodes", brief)
        self.assertIn("decisions", full)
        self.assertIn("decisions", brief)


if __name__ == "__main__":
    unittest.main()
