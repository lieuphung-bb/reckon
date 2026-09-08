"""Acceptance tests, one per real engagement failure this tool exists to catch.

These are the go/no-go. If the queries do not surface what was actually missed
on real engagements, the model is wrong and the tool should be dropped.

Each fixture encodes ONLY what was known at the moment of the miss - no hindsight.
"""

import unittest

from reckon.model import fold, OPERATOR_ID
from reckon.queries import (frontier, unrealized, unmined, stale, coverage, why,
                         verification_queue, reach, reach_pareto, unswept,
                         blocked_but_unswept, untried, blocked_but_untried)


def ev(seq, op, **args):
    return {"seq": seq, "ts": "2026-08-08T00:00:00+00:00", "op": op, "args": args}


class TestUnminedCredential(unittest.TestCase):
    """A credential written off as dead while a new exploit is chased — and the
    answer sitting in a repository nobody read. Acquisition is not examination."""

    def setUp(self):
        self.g = fold([
            ev(1, "add_node", id="host:box01", kind="host", label="box01",
               epistemic="verified", exploitation="exhausted"),
            # cred acquired early, never tried anywhere
            ev(2, "add_node", id="cred:analyst", kind="cred", label="analyst"),
            ev(3, "set_exploitation", id="cred:analyst", state="acquired"),
            # repos cloned but never read
            ev(4, "add_node", id="artifact:repo-dashboard", kind="artifact",
               label="dashboard repo"),
            ev(5, "set_exploitation", id="artifact:repo-dashboard", state="acquired"),
            ev(6, "add_node", id="artifact:sop-runbook", kind="artifact",
               label="SOP runbook"),
            ev(7, "set_exploitation", id="artifact:sop-runbook", state="acquired"),
            # an artifact we DID read
            ev(8, "add_node", id="artifact:web-config", kind="artifact",
               label="web config"),
            ev(9, "set_exploitation", id="artifact:web-config", state="acquired"),
            ev(20, "examine", id="artifact:web-config", outcome="nothing useful"),
        ])

    def test_unmined_surfaces_the_untried_credential(self):
        ids = [u["id"] for u in unmined(self.g)]
        self.assertIn("cred:analyst", ids)

    def test_unmined_surfaces_unread_artifacts(self):
        ids = [u["id"] for u in unmined(self.g)]
        self.assertIn("artifact:repo-dashboard", ids)
        self.assertIn("artifact:sop-runbook", ids)

    def test_examined_asset_is_not_flagged(self):
        ids = [u["id"] for u in unmined(self.g)]
        self.assertNotIn("artifact:web-config", ids)

    def test_oldest_held_ranks_first(self):
        out = unmined(self.g)
        self.assertEqual(out[0]["id"], "cred:analyst")

    def test_coverage_reports_the_unread_ratio(self):
        c = coverage(self.g)
        self.assertEqual(c["artifacts_total"], 3)
        self.assertEqual(c["artifacts_examined"], 1)


class TestUnrealizedObjective(unittest.TestCase):
    """An objective already unlocked — credential valid, endpoint answering — and
    never run, because nothing showed it was winnable."""

    def setUp(self):
        self.g = fold([
            ev(1, "add_node", id="cred:soc", kind="cred", label="soc",
               epistemic="verified", exploitation="acquired"),
            ev(2, "add_node", id="service:chat-api", kind="service",
               label=".85 /api/chat", epistemic="verified"),
            ev(3, "add_edge", id="e1", src=OPERATOR_ID, dst="cred:soc",
               rel="holds", epistemic="verified"),
            ev(4, "add_edge", id="e2", src="cred:soc", dst="service:chat-api",
               rel="grants-access-to", epistemic="verified",
               props={"rank": 1, "privilege": "authenticated"}),
            ev(5, "add_node", id="obj:ai-soc", kind="objective",
               label="AI SOC tool-abuse", status="open",
               props={"crown_jewel": True,
                      "requires": [{"target": "service:chat-api", "min_rank": 1}]}),
        ])

    def test_objective_is_reachable_now(self):
        f = frontier(self.g)
        self.assertIn("obj:ai-soc", [o["id"] for o in f["reachable_now"]])

    def test_unrealized_flags_it_as_winnable_but_open(self):
        u = unrealized(self.g)
        self.assertEqual([o["id"] for o in u], ["obj:ai-soc"])
        self.assertEqual(u[0]["status"], "open")

    def test_achieving_it_clears_the_alarm(self):
        g2 = fold(self.g.events + [ev(6, "set_objective", id="obj:ai-soc",
                                      status="achieved")])
        self.assertEqual(unrealized(g2), [])


class TestAccessHeldObjectiveOpen(unittest.TestCase):
    """Root on a host already held, task still open — a quick win left undone
    because nothing connected the access to the objective."""

    def setUp(self):
        self.g = fold([
            ev(1, "add_node", id="host:app01", kind="host",
               label="app01", epistemic="verified"),
            ev(2, "add_edge", id="e1", src=OPERATOR_ID, dst="host:app01",
               rel="grants-access-to", epistemic="verified",
               props={"rank": 3, "privilege": "root"}),
            ev(3, "add_node", id="obj:pubkey", kind="objective",
               label="plant pubkey", status="open",
               props={"requires": [{"target": "host:app01", "min_rank": 3}]}),
            # an objective we genuinely cannot reach yet
            ev(4, "add_node", id="host:dc02", kind="host", label="DC02"),
            ev(5, "add_node", id="obj:t22", kind="objective", label="T22 DA",
               status="open",
               props={"crown_jewel": True,
                      "requires": [{"target": "host:dc02", "min_rank": 3}]}),
        ])

    def test_held_access_surfaces_the_open_objective(self):
        self.assertIn("obj:pubkey", [o["id"] for o in unrealized(self.g)])

    def test_unreachable_objective_is_not_claimed_as_winnable(self):
        self.assertNotIn("obj:t22", [o["id"] for o in unrealized(self.g)])
        f = frontier(self.g)
        self.assertIn("obj:t22", [o["id"] for o in f["unreachable"]])

    def test_insufficient_privilege_does_not_satisfy(self):
        g2 = fold([e for e in self.g.events if e["seq"] != 2] +
                  [ev(9, "add_edge", id="e1", src=OPERATOR_ID,
                      dst="host:app01", rel="grants-access-to",
                      epistemic="verified", props={"rank": 1})])
        self.assertEqual(unrealized(g2), [])


class TestUnverifiedIdentity(unittest.TestCase):
    """Hours spent against an orphaned host — it sat on the working path with its
    identity never actually confirmed."""

    def setUp(self):
        self.g = fold([
            ev(1, "add_node", id="cred:aws", kind="cred", label="aws key",
               epistemic="verified"),
            ev(2, "add_edge", id="e1", src=OPERATOR_ID, dst="cred:aws",
               rel="holds", epistemic="verified"),
            # target assumed, never verified as THE live box
            ev(3, "add_node", id="host:ec2-target", kind="host",
               label="44.200.140.48", epistemic="hypothesized"),
            ev(4, "add_edge", id="e2", src="cred:aws", dst="host:ec2-target",
               rel="grants-access-to", epistemic="verified", props={"rank": 3}),
        ])

    def test_unverified_node_on_active_path_is_flagged(self):
        ids = [s["id"] for s in stale(self.g)]
        self.assertIn("host:ec2-target", ids)

    def test_verifying_it_clears_the_flag(self):
        g2 = fold(self.g.events + [ev(5, "set_epistemic", id="host:ec2-target",
                                      state="verified", confidence="A")])
        self.assertNotIn("host:ec2-target", [s["id"] for s in stale(g2)])


class TestTraversal(unittest.TestCase):
    """The Dijkstra core: verified edges are free, hypothesised edges cost 1."""

    def setUp(self):
        self.g = fold([
            ev(1, "add_node", id="cred:a", kind="cred", label="a"),
            ev(2, "add_node", id="host:b", kind="host", label="b"),
            ev(3, "add_node", id="host:c", kind="host", label="c"),
            ev(4, "add_edge", id="e1", src=OPERATOR_ID, dst="cred:a",
               rel="holds", epistemic="verified"),
            ev(5, "add_edge", id="e2", src="cred:a", dst="host:b",
               rel="grants-access-to", epistemic="verified", props={"rank": 1}),
            ev(6, "add_edge", id="e3", src="host:b", dst="host:c",
               rel="escalates-to", epistemic="hypothesized", props={"rank": 3}),
            ev(7, "add_node", id="obj:x", kind="objective", label="x", status="open",
               props={"requires": [{"target": "host:c", "min_rank": 3}]}),
        ])

    def test_verified_path_costs_nothing(self):
        self.assertEqual(reach(self.g)["host:b"]["cost"], 0)

    def test_hypothesised_edge_costs_one(self):
        self.assertEqual(reach(self.g)["host:c"]["cost"], 1)

    def test_objective_behind_a_hypothesis_is_conditional(self):
        f = frontier(self.g)
        self.assertEqual([o["id"] for o in f["reachable_if"]], ["obj:x"])
        self.assertEqual(f["reachable_if"][0]["assumptions"], ["e3"])

    def test_verifying_the_edge_promotes_it_to_reachable_now(self):
        g2 = fold(self.g.events + [ev(8, "set_epistemic", id="e3", state="verified")])
        self.assertIn("obj:x", [o["id"] for o in frontier(g2)["reachable_now"]])

    def test_refuted_edge_breaks_the_path(self):
        g2 = fold(self.g.events + [ev(8, "set_epistemic", id="e3", state="refuted")])
        self.assertIn("obj:x", [o["id"] for o in frontier(g2)["unreachable"]])

    def test_verification_queue_ranks_by_objectives_gated(self):
        q = verification_queue(self.g)
        self.assertEqual(q[0]["edge"], "e3")
        self.assertEqual(q[0]["gates"], 1)

    def test_why_explains_the_path(self):
        w = why(self.g, "obj:x")
        self.assertEqual(w["assumptions"], ["e3"])
        self.assertEqual([s["edge"] for s in w["steps"]], ["e1", "e2", "e3"])

    def test_privileged_conditional_path_survives_a_cheaper_weak_one(self):
        """A host is routinely reachable over the network at low privilege AND via
        an untested credential at high privilege. Keeping only the cheapest entry
        discarded the privileged route and reported such objectives as needing
        fresh discovery — the opposite of the truth, which is that they need one
        test. Found by the demo fixture, not by a unit test."""
        g = fold([
            ev(1, "add_node", id="host:box", kind="host", label="box"),
            ev(2, "add_node", id="cred:k", kind="cred", label="k"),
            # cheap but weak: network reach only
            ev(3, "add_edge", id="e:weak", src=OPERATOR_ID, dst="host:box",
               rel="grants-access-to", epistemic="verified", props={"rank": 1}),
            # expensive but strong: an untested credential
            ev(4, "add_edge", id="e:hold", src=OPERATOR_ID, dst="cred:k",
               rel="holds", epistemic="verified"),
            ev(5, "add_edge", id="e:strong", src="cred:k", dst="host:box",
               rel="grants-access-to", epistemic="hypothesized", props={"rank": 3}),
            ev(6, "add_node", id="obj:own", kind="objective", label="own box",
               status="open",
               props={"requires": [{"target": "host:box", "min_rank": 3}]}),
        ])
        f = frontier(g)
        self.assertIn("obj:own", [o["id"] for o in f["reachable_if"]])
        self.assertEqual(f["unreachable"], [])
        self.assertEqual(f["reachable_if"][0]["assumptions"], ["e:strong"])

    def test_display_reach_still_shows_what_you_have_now(self):
        """`reach` is the display view: the cheapest access, not the aspirational
        one. Requirement checking uses the pareto set instead."""
        g = fold([
            ev(1, "add_node", id="host:box", kind="host", label="box"),
            ev(2, "add_edge", id="e:weak", src=OPERATOR_ID, dst="host:box",
               rel="grants-access-to", epistemic="verified", props={"rank": 1}),
            ev(3, "add_edge", id="e:strong", src=OPERATOR_ID, dst="host:box",
               rel="grants-access-to", epistemic="hypothesized", props={"rank": 3}),
        ])
        self.assertEqual(reach(g)["host:box"]["cost"], 0)
        self.assertEqual(reach(g)["host:box"]["rank"], 1)
        self.assertEqual(len(reach_pareto(g)["host:box"]), 2)

    def test_contains_inherits_privilege(self):
        g2 = fold(self.g.events + [
            ev(8, "add_node", id="artifact:key", kind="artifact", label="id_rsa"),
            ev(9, "add_edge", id="e4", src="host:b", dst="artifact:key",
               rel="contains", epistemic="verified"),
        ])
        self.assertEqual(reach(g2)["artifact:key"]["rank"], 1)


class TestUnsweptSurface(unittest.TestCase):
    """fries 2026-09-07: a web surface closed on twelve hand-typed vhost guesses;
    the real vhost (the intended entry) was never in the list, and no alarm saw
    the absence because `unmined` needs a node to exist. The floor: an
    established surface must carry evidence of its standard recon before it
    counts as covered — the ATTEMPT, not an outcome."""

    def _web(self, extra=()):
        return fold([
            ev(1, "add_node", id="host:box", kind="host", label="box",
               epistemic="verified"),
            ev(2, "add_node", id="service:box-80-http", kind="service",
               label="http nginx :80", epistemic="verified",
               props={"port": "80", "proto": "tcp", "product": "nginx",
                      "host": "box",
                      "requires": [{"min_rank": 0, "target": "host:box"}]}),
            *extra,
        ])

    def test_fires_on_established_web_with_no_sweep(self):
        methods = {u["method"] for u in unswept(self._web())
                   if u["surface"] == "web"}
        self.assertEqual(methods,
                         {"vhost-enum", "content-discovery", "tech-fingerprint"})

    def test_earned_negative_clears_it(self):
        # a vhost sweep that found nothing, recorded honestly, still clears —
        # the floor demands the attempt, never a positive result. Web coverage is
        # per-listener, so the evidence must NAME the port it swept.
        g = self._web([
            ev(3, "add_node", id="finding:v", kind="finding",
               label="vhost brute: none found", epistemic="verified",
               props={"method": "gobuster-vhost", "host": "box", "port": "80",
                      "count": "5000"}),
        ])
        methods = {u["method"] for u in unswept(g) if u["surface"] == "web"}
        self.assertNotIn("vhost-enum", methods)

    def test_thin_sweep_stays_unswept(self):
        # fries 2026-09-07: twelve hand-typed names recorded as "namespace closed".
        # A count below the breadth floor is a spot-check, not a sweep — it stays.
        g = self._web([
            ev(3, "add_node", id="finding:thin", kind="finding",
               label="12 guessed names, none hit", epistemic="verified",
               props={"method": "vhost-enum", "host": "box", "port": "80",
                      "count": "12"}),
        ])
        rows = [u for u in unswept(g)
                if u["surface"] == "web" and u["method"] == "vhost-enum"]
        self.assertEqual(len(rows), 1)          # still firing
        self.assertTrue(rows[0]["thin"])        # flagged as a spot-check
        # and a real sweep of the same surface clears it
        g2 = self._web([
            ev(3, "add_node", id="finding:real", kind="finding",
               label="5000-name sweep", epistemic="verified",
               props={"method": "vhost-enum", "host": "box", "port": "80",
                      "count": "5000"}),
        ])
        self.assertNotIn("vhost-enum",
                         {u["method"] for u in unswept(g2) if u["surface"] == "web"})

    def test_web_coverage_is_per_listener_not_per_host(self):
        # fries 2026-09-07 (the bug Com caught): a content-discovery sweep of :80
        # must NOT clear :443's alarm — one listener's coverage is not another's.
        g = fold([
            ev(1, "add_node", id="host:box", kind="host", label="box",
               epistemic="verified"),
            ev(2, "add_node", id="service:box-80-http", kind="service",
               label="http :80", epistemic="verified",
               props={"port": "80", "host": "box"}),
            ev(3, "add_node", id="service:box-443-https", kind="service",
               label="https :443", epistemic="verified",
               props={"port": "443", "host": "box"}),
            ev(4, "add_node", id="finding:cd80", kind="finding",
               label="dirbrute :80 done", epistemic="verified",
               props={"method": "content-discovery", "host": "box",
                      "port": "80", "count": "4700"}),
        ])
        got = {(u["port"], u["method"]) for u in unswept(g)
               if u["surface"] == "web" and u["method"] == "content-discovery"}
        self.assertNotIn(("80", "content-discovery"), got)   # :80 cleared
        self.assertIn(("443", "content-discovery"), got)     # :443 still fires

    def test_host_level_surface_clears_without_a_port(self):
        # SMB/LDAP/DNS are one logical service across ports, so a host-linked
        # enum (no port) clears them — per-port there would only cry wolf.
        g = fold([
            ev(1, "add_node", id="host:box", kind="host", label="box",
               epistemic="verified"),
            ev(2, "add_node", id="service:box-445-smb", kind="service",
               label="microsoft-ds :445", epistemic="verified",
               props={"port": "445", "host": "box"}),
            ev(3, "add_node", id="finding:se", kind="finding",
               label="enum4linux shares", epistemic="verified",
               props={"method": "share-enum", "host": "box"}),
        ])
        methods = {u["method"] for u in unswept(g) if u["surface"] == "smb"}
        self.assertNotIn("share-enum", methods)

    def test_only_after_the_surface_is_established(self):
        # a merely-hypothesized service must not cry wolf during first contact.
        g = fold([
            ev(1, "add_node", id="host:box", kind="host", label="box",
               epistemic="verified"),
            ev(2, "add_node", id="service:box-80-http", kind="service",
               label="http :80", epistemic="hypothesized",
               props={"port": "80", "host": "box"}),
        ])
        self.assertEqual(unswept(g), [])

    def test_http_framed_non_web_is_not_a_web_surface(self):
        # WinRM (:5985) and RPC-over-HTTP (:593) carry "http" but a vhost/content
        # sweep against them is nonsense — the exact cry-wolf the floor avoids.
        g = fold([
            ev(1, "add_node", id="host:box", kind="host", label="box",
               epistemic="verified"),
            ev(2, "add_node", id="service:box-5985-http", kind="service",
               label="http Microsoft HTTPAPI httpd :5985", epistemic="verified",
               props={"port": "5985", "host": "box"}),
            ev(3, "add_node", id="service:box-593-rpc", kind="service",
               label="ncacn_http Microsoft Windows RPC :593", epistemic="verified",
               props={"port": "593", "host": "box"}),
        ])
        self.assertEqual([u for u in unswept(g) if u["surface"] == "web"], [])


class TestBlockedButUnswept(unittest.TestCase):
    """fries 2026-09-07: 'every remaining path is credential-gated' — drawn while
    a web surface was never adequately swept. The block was a coverage gap. This
    challenges the terminal negative while any standard recon remains."""

    def _gated(self, extra=()):
        # a verified web service (so unswept fires) + an objective that needs a
        # privilege nothing grants (so nothing is winnable right now).
        return fold([
            ev(1, "add_node", id="host:box", kind="host", label="box",
               epistemic="verified"),
            ev(2, "add_node", id="service:box-80-http", kind="service",
               label="http :80", epistemic="verified",
               props={"port": "80", "host": "box"}),
            ev(3, "add_node", id="obj:root", kind="objective", label="root the box",
               props={"requires": [{"target": "host:box", "min_rank": 3}]}),
            *extra,
        ])

    def test_fires_when_no_win_and_recon_incomplete(self):
        b = blocked_but_unswept(self._gated())
        self.assertEqual(len(b), 1)
        self.assertGreaterEqual(b[0]["unswept"], 1)

    def test_clears_once_recon_is_complete(self):
        # satisfy every web method at adequate breadth; the block is now earned.
        g = self._gated([
            ev(4, "add_node", id="f:vh", kind="finding", label="vhost sweep",
               epistemic="verified", props={"method": "vhost-enum",
               "host": "box", "port": "80", "count": "5000"}),
            ev(5, "add_node", id="f:cd", kind="finding", label="content sweep",
               epistemic="verified", props={"method": "content-discovery",
               "host": "box", "port": "80", "count": "5000"}),
            ev(6, "add_node", id="f:tf", kind="finding", label="tech fp",
               epistemic="verified", props={"method": "tech-fingerprint",
               "host": "box", "port": "80"}),
        ])
        self.assertEqual(unswept(g), [])            # recon done
        self.assertEqual(blocked_but_unswept(g), [])  # so the block is legitimate

    def test_silent_when_a_win_is_available(self):
        # if an objective is winnable now, we are not blocked — no matter the floor.
        g = fold([
            ev(1, "add_node", id="host:box", kind="host", label="box",
               epistemic="verified"),
            ev(2, "add_node", id="service:box-80-http", kind="service",
               label="http :80", epistemic="verified",
               props={"port": "80", "host": "box"}),
            ev(3, "add_node", id="obj:easy", kind="objective", label="reach the box",
               props={"requires": [{"target": "host:box", "min_rank": 0}]}),
            ev(4, "add_edge", id="e1", src=OPERATOR_ID, rel="grants-access-to",
               dst="host:box", epistemic="verified"),
        ])
        self.assertEqual(blocked_but_unswept(g), [])


class TestUntriedSurface(unittest.TestCase):
    """fries 2026-09-08: a documented credential was tested only via Kerberos/AD,
    refused on six AD surfaces, and written off as dead — while the web-app login it
    was actually for was never tried. Both executor and verifier varied the
    INSTRUMENT (impacket AES -> RC4) but not the SURFACE, and counted six refusals on
    one credential store as thoroughness. The exploitation-axis mirror of `unswept`:
    a held primitive must be tried against every present surface KIND, and refusals
    within one store are ONE negative, not N."""

    def _box(self, extra=(), web_epistemic="verified"):
        # a held credential, an AD surface, and a web surface it may actually be for.
        return fold([
            ev(1, "add_node", id="host:dc", kind="host", label="dc",
               epistemic="verified"),
            ev(2, "add_node", id="service:dc-445-smb", kind="service",
               label="microsoft-ds :445", epistemic="verified",
               props={"port": "445", "host": "dc"}),
            ev(3, "add_node", id="service:dc-389-ldap", kind="service",
               label="ldap :389", epistemic="verified",
               props={"port": "389", "host": "dc"}),
            ev(4, "add_node", id="service:dc-88-krb", kind="service",
               label="kerberos-sec :88", epistemic="verified",
               props={"port": "88", "host": "dc"}),
            ev(5, "add_node", id="service:dc-80-gitea", kind="service",
               label="http gitea :80", epistemic=web_epistemic,
               props={"port": "80", "host": "dc", "product": "gitea"}),
            ev(6, "add_node", id="cred:cooper", kind="cred", label="d.cooper",
               exploitation="acquired"),
            *extra,
        ])

    def _refused_on_ad(self, *services):
        # a tested-against edge that FAILED for each named AD service.
        out = []
        for i, sid in enumerate(services):
            out.append(ev(10 + i, "add_edge", id=f"ta:{sid}", src="cred:cooper",
                          rel="tested-against", dst=sid, epistemic="refuted"))
        return out

    def test_fires_when_failed_on_ad_and_web_untried(self):
        g = self._box(self._refused_on_ad("service:dc-445-smb"))
        rows = untried(g)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cred"], "cred:cooper")
        self.assertIn("ad-auth", rows[0]["tried_kinds"])
        self.assertIn("webapp:host:dc:80", rows[0]["untried_kinds"])

    def test_six_ad_refusals_collapse_to_one_tried_kind(self):
        # the whole point: SMB + LDAP + Kerberos are one credential store. Three
        # refusals are ONE tried-cell, and the web door is still untried.
        g = self._box(self._refused_on_ad(
            "service:dc-445-smb", "service:dc-389-ldap", "service:dc-88-krb"))
        rows = untried(g)
        self.assertEqual(rows[0]["tried_kinds"], ["ad-auth"])      # not three
        self.assertEqual(rows[0]["untried_kinds"], ["webapp:host:dc:80"])

    def test_success_anywhere_silences_it(self):
        # the credential worked on the web app -> it is live, not a false wall.
        g = self._box(self._refused_on_ad("service:dc-445-smb") + [
            ev(20, "add_edge", id="ta:web", src="cred:cooper", rel="tested-against",
               dst="service:dc-80-gitea", epistemic="verified"),
        ])
        self.assertEqual(untried(g), [])

    def test_never_tried_anywhere_is_unmined_not_untried(self):
        # a held cred nobody has tried yet is `unmined`'s job; `untried` fires only
        # on the dangerous tried-and-failed-here-but-not-there state.
        g = self._box()
        self.assertEqual(untried(g), [])

    def test_trying_the_web_clears_it(self):
        g = self._box(self._refused_on_ad("service:dc-445-smb") + [
            ev(20, "add_edge", id="ta:web", src="cred:cooper", rel="tested-against",
               dst="service:dc-80-gitea", epistemic="refuted"),   # tried, even if fail
        ])
        self.assertEqual(untried(g), [])

    def test_only_verified_surfaces_count(self):
        # a merely-hypothesized web service must not manufacture an untried cell.
        g = self._box(self._refused_on_ad("service:dc-445-smb"),
                      web_epistemic="hypothesized")
        self.assertEqual(untried(g), [])

    def test_objective_adjacent_web_is_flagged(self):
        g = self._box(self._refused_on_ad("service:dc-445-smb") + [
            ev(20, "add_node", id="obj:root", kind="objective", label="root dc",
               props={"requires": [{"target": "host:dc", "min_rank": 3}]}),
        ])
        cell = untried(g)[0]["cells"][0]
        self.assertEqual(cell["kind"], "webapp:host:dc:80")
        self.assertTrue(cell["objective_adjacent"])


class TestBlockedButUntried(unittest.TestCase):
    """The engagement-scope twin of `blocked_but_unswept`: a 'blocked / every path is
    credential-gated' conclusion drawn while a credential we already HOLD has a
    present surface kind it was never tried against. The door we hold the key to but
    never opened is not a wall."""

    def _stuck(self, extra=()):
        # held cred, a verified web surface, and an objective needing a privilege
        # nothing grants (so nothing is winnable now).
        return fold([
            ev(1, "add_node", id="host:dc", kind="host", label="dc",
               epistemic="verified"),
            ev(2, "add_node", id="service:dc-80-gitea", kind="service",
               label="http gitea :80", epistemic="verified",
               props={"port": "80", "host": "dc", "product": "gitea"}),
            ev(3, "add_node", id="cred:cooper", kind="cred", label="d.cooper",
               exploitation="acquired"),
            ev(4, "add_node", id="obj:root", kind="objective", label="root dc",
               props={"requires": [{"target": "host:dc", "min_rank": 3}]}),
            *extra,
        ])

    def test_fires_when_stuck_and_held_cred_has_untried_surface(self):
        b = blocked_but_untried(self._stuck())
        self.assertEqual(len(b), 1)
        self.assertEqual(b[0]["creds"], 1)
        self.assertIn("webapp:host:dc:80", b[0]["detail"][0]["untried_kinds"])

    def test_clears_once_the_cred_is_tried_there(self):
        g = self._stuck([
            ev(5, "add_edge", id="ta:web", src="cred:cooper", rel="tested-against",
               dst="service:dc-80-gitea", epistemic="refuted"),
        ])
        self.assertEqual(blocked_but_untried(g), [])

    def test_silent_when_a_win_is_available(self):
        g = self._stuck([
            ev(5, "add_node", id="obj:easy", kind="objective", label="reach dc",
               props={"requires": [{"target": "host:dc", "min_rank": 0}]}),
            ev(6, "add_edge", id="e1", src=OPERATOR_ID, rel="grants-access-to",
               dst="host:dc", epistemic="verified"),
        ])
        self.assertEqual(blocked_but_untried(g), [])


if __name__ == "__main__":
    unittest.main()
