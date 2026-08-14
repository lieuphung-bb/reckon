"""The reference layer: the file resolver, and the seam it comes through.

Two properties are load-bearing here and everything else is detail.

**A source either yields stable ids or it yields text.** An id table resolves
deterministically and may be cited; a text match enters as a hypothesis at
confidence D. Blurring those is how a keyword hit promotes itself into the plan.
So the parser is strict about what counts as an id row — a document contains
prose tables too, and ingesting one would mint ids that resolve to nothing while
validating references that should have been refused.

**Everything goes through the Protocol.** The last class here is a second
resolver written in this file, driven through `api`, the CLI and the console
without touching a line of production code. If that ever needs an edit outside
`reference.py`, the seam has been bypassed.

No network, no database, no corpus: the fixture is ten synthetic rows.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from reckon import api, cli, reference, store
from reckon.render.html import console

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "reference-source.md")


def drawer_nodes(page: str) -> dict:
    """The node payload the drawer reads, back out of the rendered page."""
    return json.loads(page.split("<script>const N=", 1)[1]
                          .split(";</script>", 1)[0])


class ResolverBase(unittest.TestCase):
    """Every test restores the process-wide resolver, or it leaks into the rest
    of the suite — which would quietly break acceptance criterion 1."""

    def setUp(self):
        self._old_env = os.environ.get(reference.REFERENCES_ENV)
        os.environ.pop(reference.REFERENCES_ENV, None)
        reference.set_resolver(None)

    def tearDown(self):
        reference.set_resolver(None)
        os.environ.pop(reference.REFERENCES_ENV, None)
        if self._old_env is not None:
            os.environ[reference.REFERENCES_ENV] = self._old_env


class TestParser(unittest.TestCase):

    def setUp(self):
        with open(FIXTURE, encoding="utf-8") as fh:
            self.index = reference.parse_index(fh.read())

    def test_index_rows_parse(self):
        self.assertEqual(self.index["SYN.T0001"],
                         "Probe The Model For Its Instructions")
        self.assertEqual(self.index["SYN.T0002.001"],
                         "Poison A Retrieval Corpus: Public Wiki")
        self.assertEqual(len(self.index), 3)

    def test_prose_rows_do_not_become_ids(self):
        """The other direction, and the one that matters: a source is a
        document. A sentence in the first column is not an id."""
        self.assertNotIn("A chat endpoint that echoes system text", self.index)
        for key in self.index:
            self.assertNotIn(" ", key)

    def test_a_code_span_inside_prose_is_not_an_id(self):
        """`curl`-shaped docs ... — contains a code span, is not one."""
        self.assertNotIn("curl", self.index)

    def test_a_wide_table_is_not_an_index(self):
        """Three columns, first cell a clean code span. Still refused: the id
        table is two columns, and a wider table is some other table."""
        self.assertNotIn("SYN.T9999", self.index)

    def test_headers_and_separators_are_ignored(self):
        self.assertNotIn("id", self.index)
        self.assertNotIn("---", self.index)

    def test_first_definition_wins(self):
        idx = reference.parse_index(
            "| `A.1` | canonical |\n"
            "later prose mentions it again:\n"
            "| `A.1` | offhand |\n")
        self.assertEqual(idx["A.1"], "canonical")


class TestFileResolver(ResolverBase):

    def setUp(self):
        super().setUp()
        self.r = reference.FileResolver({"atlas": FIXTURE})

    def test_it_satisfies_the_protocol(self):
        self.assertIsInstance(self.r, reference.Resolver)

    def test_resolve_returns_the_canonical_name(self):
        self.assertEqual(self.r.resolve("atlas", "technique", "SYN.T0002"),
                         {"title": "Poison A Retrieval Corpus", "source": FIXTURE})

    def test_an_unknown_id_or_store_returns_nothing(self):
        self.assertEqual(self.r.resolve("atlas", "technique", "SYN.T4242"), {})
        self.assertEqual(self.r.resolve("attack", "technique", "SYN.T0001"), {})

    def test_the_label_is_carried_not_matched(self):
        """A markdown index holds one kind of thing, so a label to check
        against could only ever be config that is wrong — and the miss would
        look exactly like an absent id. Whatever label the operator records as
        provenance, the id still resolves."""
        for label in ("technique", "CVE", "", "anything at all"):
            self.assertEqual(self.r.resolve("atlas", label, "SYN.T0001")["title"],
                             "Probe The Model For Its Instructions")

    def test_stores_reports_what_it_answers_for(self):
        self.assertEqual(self.r.stores(), ("atlas",))

    def test_search_is_empty_not_broken(self):
        """Semantic retrieval is a separate capability that does not exist yet.
        [] is the honest answer; a fake hit would enter the plan."""
        self.assertEqual(self.r.search("poison the corpus"), [])

    def test_a_missing_source_degrades_rather_than_crashing(self):
        r = reference.FileResolver({"atlas": "/no/such/source.md"})
        self.assertEqual(r.resolve("atlas", "technique", "SYN.T0001"), {})
        self.assertEqual(r.index_for("atlas"), {})

    def test_the_source_is_parsed_once(self):
        """A drawer asks for every reference it draws. Re-reading per call
        would make the console cost scale with the source."""
        tmp = os.path.join(tempfile.mkdtemp(), "src.md")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("| `X.1` | first read |\n")
        r = reference.FileResolver({"atlas": tmp})
        self.assertEqual(r.resolve("atlas", "technique", "X.1")["title"],
                         "first read")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("| `X.1` | second read |\n")
        self.assertEqual(r.resolve("atlas", "technique", "X.1")["title"],
                         "first read")
        fresh = reference.FileResolver({"atlas": tmp})
        self.assertEqual(fresh.resolve("atlas", "technique", "X.1")["title"],
                         "second read")


class TestConfiguration(ResolverBase):

    def test_nothing_configured_means_nullresolver(self):
        """Acceptance criterion 1: the default is unchanged."""
        self.assertIsInstance(reference.get_resolver(), reference.NullResolver)
        self.assertEqual(reference.known_stores(), ("chroma", "neo4j"))

    def test_a_source_binds_to_a_store_name(self):
        os.environ[reference.REFERENCES_ENV] = f"atlas={FIXTURE}"
        r = reference.get_resolver()
        self.assertEqual(r.stores(), ("atlas",))
        self.assertEqual(r.resolve("atlas", "technique", "SYN.T0001")["title"],
                         "Probe The Model For Its Instructions")
        self.assertEqual(reference.known_stores(), ("atlas", "chroma", "neo4j"))

    def test_several_sources_at_once(self):
        env = {reference.REFERENCES_ENV:
               os.pathsep.join([f"atlas={FIXTURE}", f"owasp={FIXTURE}"])}
        self.assertEqual(reference.sources_from_env(env),
                         {"atlas": FIXTURE, "owasp": FIXTURE})

    def test_a_store_name_may_contain_a_dot(self):
        """Nothing in the format claims the dot, so nothing may quietly eat
        half a name."""
        self.assertEqual(
            reference.sources_from_env(
                {reference.REFERENCES_ENV: f"atlas.v2={FIXTURE}"}),
            {"atlas.v2": FIXTURE})

    def test_a_relative_path_hangs_off_reckon_home(self):
        old = store.RECKON_HOME
        store.RECKON_HOME = "/opt/eng"
        try:
            got = reference.sources_from_env(
                {reference.REFERENCES_ENV: "atlas=ref/atlas.md"})
        finally:
            store.RECKON_HOME = old
        self.assertEqual(got["atlas"], "/opt/eng/ref/atlas.md")

    def test_malformed_entries_are_skipped_not_fatal(self):
        """Lenient on read. The loud half is at write time, where `reckon ref`
        into an unknown store names the stores that do exist."""
        env = {reference.REFERENCES_ENV:
               os.pathsep.join(["", "no-equals-sign", "=/orphan.md",
                                "atlas=", f"real={FIXTURE}"])}
        self.assertEqual(reference.sources_from_env(env), {"real": FIXTURE})

    def test_a_file_may_not_shadow_a_wired_store(self):
        """`neo4j` carries schema validation of its own; letting a file claim
        the name would make two different things answer to one word."""
        self.assertEqual(
            reference.sources_from_env(
                {reference.REFERENCES_ENV: f"neo4j={FIXTURE}"}), {})

    def test_a_configured_but_missing_path_still_starts(self):
        os.environ[reference.REFERENCES_ENV] = "atlas=/no/such/source.md"
        r = reference.get_resolver()
        self.assertEqual(r.stores(), ("atlas",))
        self.assertEqual(r.resolve("atlas", "technique", "SYN.T0001"), {})


class TestWriteTimeValidation(ResolverBase):
    """Criterion 4: a reference to an id absent from its source is refused."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self._old = store.ENGAGEMENTS
        store.ENGAGEMENTS = self.tmp
        api.create("t")
        api.add_node("t", "service", "chat", node_id="service:chat")
        os.environ[reference.REFERENCES_ENV] = f"atlas={FIXTURE}"

    def tearDown(self):
        store.ENGAGEMENTS = self._old
        super().tearDown()

    def test_a_known_id_is_accepted(self):
        ref = api.add_reference("t", "service:chat", "atlas", "technique",
                                "SYN.T0002.001")
        self.assertEqual(ref, {"store": "atlas", "label": "technique",
                               "key": "SYN.T0002.001"})
        g = store.load("t")
        self.assertEqual(g.nodes["service:chat"].props["references"], [ref])

    def test_an_id_absent_from_the_source_is_refused(self):
        with self.assertRaises(reference.ReferenceError) as caught:
            api.add_reference("t", "service:chat", "atlas", "technique",
                              "SYN.T4242")
        self.assertIn("SYN.T4242", str(caught.exception))
        self.assertNotIn("references",
                         store.load("t").nodes["service:chat"].props)

    def test_a_prose_row_is_not_a_usable_id(self):
        """The parser rule and the write rule are the same rule seen twice."""
        with self.assertRaises(reference.ReferenceError):
            api.add_reference("t", "service:chat", "atlas", "technique",
                              "SYN.T9999")

    def test_the_label_is_recorded_but_not_validated(self):
        """For a file store the id is what is checked. The label rides along as
        provenance, so an operator's choice of word cannot make a present id
        look absent."""
        ref = api.add_reference("t", "service:chat", "atlas", "CVE",
                                "SYN.T0001")
        self.assertEqual(ref["label"], "CVE")

    def test_an_unconfigured_store_names_the_ones_that_exist(self):
        with self.assertRaises(reference.ReferenceError) as caught:
            api.add_reference("t", "service:chat", "attack", "technique", "X")
        self.assertIn("atlas", str(caught.exception))

    def test_neo4j_validation_is_untouched(self):
        """The graph schema still validates itself; a file store does not
        loosen it."""
        self.assertEqual(
            reference.make_reference("neo4j", "CVE", "CVE-2023-46604")["store"],
            "neo4j")
        with self.assertRaises(reference.ReferenceError):
            reference.make_reference("neo4j", "ATLAS", "SYN.T0001")

    def test_the_cli_reaches_the_same_validation(self):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            cli.main(["-e", "t", "ref", "service:chat", "atlas", "technique",
                      "SYN.T0001"])
        self.assertEqual(json.loads(buf.getvalue())["key"], "SYN.T0001")
        with self.assertRaises(SystemExit) as caught:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                cli.main(["-e", "t", "ref", "service:chat", "atlas",
                          "technique", "SYN.T4242"])
        self.assertIn("SYN.T4242", str(caught.exception))


class TestDrawer(ResolverBase):
    """Criterion 2: the canonical name beside the triple; unresolved still
    shows the triple, because that is what it was recorded for."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self._old = store.ENGAGEMENTS
        store.ENGAGEMENTS = self.tmp
        api.create("t")
        api.add_node("t", "service", "chat", node_id="service:chat")

    def tearDown(self):
        store.ENGAGEMENTS = self._old
        super().tearDown()

    def _refs(self):
        page = console(store.load("t"), "t")
        return drawer_nodes(page)["service:chat"]["references"]

    def test_a_resolved_reference_carries_its_name(self):
        os.environ[reference.REFERENCES_ENV] = f"atlas={FIXTURE}"
        api.add_reference("t", "service:chat", "atlas", "technique",
                          "SYN.T0001")
        self.assertEqual(self._refs(), [
            {"store": "atlas", "label": "technique", "key": "SYN.T0001",
             "title": "Probe The Model For Its Instructions"}])

    def test_an_unresolved_reference_still_reads_as_provenance(self):
        api.add_reference("t", "service:chat", "neo4j", "CVE", "CVE-2023-46604")
        self.assertEqual(self._refs(), [
            {"store": "neo4j", "label": "CVE", "key": "CVE-2023-46604"}])

    def test_the_name_is_never_written_back_to_the_graph(self):
        """The graph stores ids only, so the source can change under it."""
        os.environ[reference.REFERENCES_ENV] = f"atlas={FIXTURE}"
        api.add_reference("t", "service:chat", "atlas", "technique",
                          "SYN.T0001")
        self._refs()
        self.assertNotIn(
            "title", store.load("t").nodes["service:chat"].props["references"][0])

    def test_a_broken_resolver_does_not_break_the_page(self):
        class Exploding:
            def stores(self): return ("atlas",)
            def resolve(self, *a): raise RuntimeError("source on fire")
            def search(self, query, limit=5): return []

        api.add_reference("t", "service:chat", "neo4j", "CVE", "CVE-2023-46604")
        reference.set_resolver(Exploding())
        self.assertEqual(self._refs(), [
            {"store": "neo4j", "label": "CVE", "key": "CVE-2023-46604"}])


class TestRetrievalStaysHypothesis(ResolverBase):
    """Criterion 3, asserted against regression rather than newly built: a text
    match is evidence about relevance, not about this target — whatever backend
    produced it."""

    def test_hits_arrive_hypothesized_at_confidence_d(self):
        evs = reference.retrieval_to_events(
            [{"text": "poison the retrieval corpus", "score": 0.02,
              "source": "kb"}], about="service:chat")
        self.assertEqual(len(evs), 2)
        for e in evs:
            self.assertEqual(e["args"]["epistemic"], "hypothesized")
            self.assertEqual(e["args"]["confidence"], "D")
            self.assertNotEqual(e["args"]["epistemic"], "verified")
        self.assertEqual(reference.RETRIEVAL_CONFIDENCE, "D")

    def test_a_file_source_does_not_produce_retrieval_hits(self):
        r = reference.FileResolver({"atlas": FIXTURE})
        self.assertEqual(reference.retrieval_to_events(
            r.search("anything"), about="service:chat"), [])


class MemoResolver:
    """A second resolver, written here and nowhere else.

    Criterion 6: adding one must require no change to `api`, the CLI or the
    console. This class exists only in the test file, is installed through
    `reference.set_resolver`, and is exercised through all three below. If any
    of that ever needs a production edit outside `reference.py`, the Protocol
    was bypassed and phases 2 and 3 become a refactor instead of an addition.
    """

    def __init__(self, entries):
        self._entries = dict(entries)

    def stores(self):
        return ("memo",)

    def resolve(self, store, label, key):
        if store != "memo" or label != "note":
            return {}
        found = self._entries.get(key)
        return {"title": found} if found else {}

    def search(self, query, limit=5):
        return []


class TestSecondResolver(ResolverBase):

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self._old = store.ENGAGEMENTS
        store.ENGAGEMENTS = self.tmp
        api.create("t")
        api.add_node("t", "service", "chat", node_id="service:chat")
        reference.set_resolver(MemoResolver({"M-1": "the first memo"}))

    def tearDown(self):
        store.ENGAGEMENTS = self._old
        super().tearDown()

    def test_it_satisfies_the_protocol(self):
        self.assertIsInstance(reference.get_resolver(), reference.Resolver)

    def test_api_validates_against_it(self):
        api.add_reference("t", "service:chat", "memo", "note", "M-1")
        with self.assertRaises(reference.ReferenceError):
            api.add_reference("t", "service:chat", "memo", "note", "M-2")

    def test_the_cli_accepts_its_store_name_unchanged(self):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            cli.main(["-e", "t", "ref", "service:chat", "memo", "note", "M-1"])
        self.assertEqual(json.loads(buf.getvalue())["store"], "memo")

    def test_the_console_renders_its_titles(self):
        api.add_reference("t", "service:chat", "memo", "note", "M-1")
        nodes = drawer_nodes(console(store.load("t"), "t"))
        self.assertEqual(nodes["service:chat"]["references"][0]["title"],
                         "the first memo")


if __name__ == "__main__":
    unittest.main()
