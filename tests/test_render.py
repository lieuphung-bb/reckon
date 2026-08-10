"""Render tests: the console must be well-formed and self-consistent.

A card whose data-n is missing from the embedded node payload is a dead click -
the failure is invisible in Python and only shows up as a drawer that never
opens, so it is asserted here instead.
"""

import json
import re
import unittest
from html.parser import HTMLParser

from reckon.model import fold, OPERATOR_ID
from reckon.render.html import console, md2html, inline, access_class
from reckon.render.board import board
from reckon.render.views import render_all, VIEWS


def ev(seq, op, **args):
    return {"seq": seq, "ts": "2026-08-08T00:00:00+00:00", "op": op, "args": args}


GRAPH = fold([
    ev(1, "add_node", id="cred:a", kind="cred", label="a-cred"),
    ev(2, "set_exploitation", id="cred:a", state="acquired"),
    ev(3, "add_node", id="host:b", kind="host", label="B-HOST",
       props={"zone": "dmz", "ip": "10.0.0.5"}),
    ev(4, "add_edge", id="e1", src=OPERATOR_ID, dst="cred:a", rel="holds",
       epistemic="verified"),
    ev(5, "add_edge", id="e2", src="cred:a", dst="host:b", rel="grants-access-to",
       epistemic="verified", props={"rank": 3}),
    ev(6, "add_node", id="obj:x", kind="objective", label="own B", status="open",
       props={"crown_jewel": True,
              "requires": [{"target": "host:b", "min_rank": 3}]}),
])


class Balance(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.stray = [], []

    def handle_startendtag(self, t, a):
        pass

    def handle_starttag(self, t, a):
        if t not in ("meta", "br", "hr", "img", "input", "link"):
            self.stack.append(t)

    def handle_endtag(self, t):
        if self.stack and self.stack[-1] == t:
            self.stack.pop()
        elif t in self.stack:
            while self.stack and self.stack.pop() != t:
                pass
        else:
            self.stray.append(t)


class TestConsole(unittest.TestCase):

    def setUp(self):
        self.html = console(GRAPH, "t")

    def test_well_formed(self):
        p = Balance()
        p.feed(self.html)
        self.assertEqual(p.stray, [], f"stray close tags: {p.stray}")
        self.assertEqual(p.stack, [], f"unclosed: {p.stack}")

    def test_every_card_has_a_node_payload(self):
        payload = json.loads(re.search(r"const N=(\{.*?\});</script>",
                                       self.html, re.S).group(1))
        cards = re.findall(r'class="card[^"]*" data-n="([^"]+)"', self.html)
        self.assertTrue(cards)
        for c in cards:
            self.assertIn(c, payload, f"dead click: {c}")

    def test_all_panes_present(self):
        for pane in ("board", "chain", "brief", "assumptions", "threat_model",
                     "plan", "recon"):
            self.assertIn(f'id="pane-{pane}"', self.html)

    def test_self_contained(self):
        """No CDN, no fonts, no network. The SVG xmlns is a namespace URI, not a
        fetch, so it is the one permitted occurrence."""
        stripped = self.html.replace('xmlns="http://www.w3.org/2000/svg"', "")
        for bad in ("http://", "https://", "<link", "cdn."):
            self.assertNotIn(bad, stripped.lower(), f"external ref: {bad}")

    def test_onclick_handlers_are_complete_calls(self):
        """json.dumps inside a double-quoted attribute truncated every handler to
        `view(`, and the first version of this test could not see it: the regex
        `onclick="([^"]*)"` matched the truncation itself and found no quote in
        it. Parse the attribute for real and require a closing paren."""
        class Handlers(HTMLParser):
            def __init__(self):
                super().__init__()
                self.found = []

            def handle_starttag(self, tag, attrs):
                for k, v in attrs:
                    if k == "onclick":
                        self.found.append(v)

        p = Handlers()
        p.feed(self.html)
        self.assertGreater(len(p.found), 5)
        for h in p.found:
            self.assertTrue(h.rstrip().endswith(")"), f"truncated handler: {h!r}")
            self.assertRegex(h, r"^[A-Za-z_$][\w$]*\(.*\)$")

    def test_alarms_surface_in_the_payload(self):
        payload = json.loads(re.search(r"const N=(\{.*?\});</script>",
                                       self.html, re.S).group(1))
        self.assertTrue(payload["obj:x"]["alarms"])
        self.assertTrue(payload["cred:a"]["alarms"])   # acquired, never examined


class TestAccessClass(unittest.TestCase):

    def test_mapping(self):
        self.assertEqual(access_class(None), "no-route")
        self.assertEqual(access_class({"cost": 1, "rank": 3}), "cond")
        self.assertEqual(access_class({"cost": 0, "rank": 3}), "owned")
        self.assertEqual(access_class({"cost": 0, "rank": 1}), "app")
        self.assertEqual(access_class({"cost": 0, "rank": 0}), "reachable")


class TestMarkdown(unittest.TestCase):

    def test_balanced_inline_on_hostile_prose(self):
        """Engagement prose is full of svc_bind, 10.x.y.z and lone backticks."""
        for s in ("svc_bind and svc_batch", "a `lone backtick",
                  "_leading underscore", "10.x.y.z_", "**bold** and `code`"):
            p = Balance()
            p.feed("<div>" + inline(s) + "</div>")
            self.assertEqual(p.stray, [], f"{s!r} -> {inline(s)!r}")
            self.assertEqual(p.stack, [], f"{s!r} -> {inline(s)!r}")

    def test_table_conversion(self):
        out = md2html("| A | B |\n|---|---|\n| 1 | 2 |")
        self.assertIn("<table>", out)
        self.assertIn("<td>1</td>", out)


class TestViews(unittest.TestCase):

    def test_all_six_render(self):
        out = render_all(GRAPH, "t")
        self.assertEqual(set(out), set(VIEWS))
        for name, text in out.items():
            self.assertTrue(text.strip(), f"{name} empty")

    def test_board_flags_the_winnable_objective(self):
        self.assertIn("UNREALIZED", board(GRAPH, "t"))


if __name__ == "__main__":
    unittest.main()
