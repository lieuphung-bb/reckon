"""nmap ingest: the graph gets seeded by the scan, not by remembering to record.

An engagement ran to completion with an empty graph because "write your findings"
was an instruction rather than an event. A scan is the one recon artifact that is
already structured, already run first, and already honest about what answered — so
importing it removes the human step that was being skipped.

Every case below is a claim the importer must NOT make: filtered is not open, a
port guess is not a version, a scan is not access.
"""

import os
import tempfile
import unittest

from reckon.ingest import from_nmap, IngestError


def xml(body: str, args: str = "nmap -sV -oX - 10.10.11.5") -> str:
    d = tempfile.mkdtemp()
    path = os.path.join(d, "scan.xml")
    with open(path, "w") as fh:
        fh.write('<?xml version="1.0"?>\n'
                 f'<nmaprun scanner="nmap" args="{args}">\n{body}\n</nmaprun>\n')
    return path


HOST_UP = '''
<host><status state="up"/>
  <address addr="10.10.11.5" addrtype="ipv4"/>
  <hostnames><hostname name="kobold.htb"/></hostnames>
  <ports>
    <port protocol="tcp" portid="22"><state state="open"/>
      <service name="ssh" product="OpenSSH" version="9.2p1"/></port>
    <port protocol="tcp" portid="80"><state state="open"/>
      <service name="http" product="nginx"/></port>
    <port protocol="tcp" portid="3306"><state state="open"/>
      <service name="mysql"/></port>
    <port protocol="tcp" portid="445"><state state="filtered"/>
      <service name="microsoft-ds"/></port>
    <port protocol="tcp" portid="21"><state state="closed"/></port>
  </ports>
</host>'''


def nodes(events, kind=None):
    out = [e["args"] for e in events if e["op"] == "add_node"]
    return [n for n in out if kind is None or n["kind"] == kind]


def edges(events):
    return [e["args"] for e in events if e["op"] == "add_edge"]


class TestWhatBecomesANode(unittest.TestCase):

    def setUp(self):
        self.ev = from_nmap(xml(HOST_UP))

    def test_open_ports_only(self):
        """filtered is absence of evidence; closed is evidence of absence.

        Neither is a service. A graph whose job is telling those apart must not
        record them as if something answered.
        """
        labels = " ".join(n["label"] for n in nodes(self.ev, "service"))
        self.assertIn("22", labels)
        self.assertIn("80", labels)
        self.assertIn("3306", labels)
        self.assertNotIn("445", labels)     # filtered
        self.assertNotIn("21", labels)      # closed
        self.assertEqual(len(nodes(self.ev, "service")), 3)

    def test_host_labelled_by_name_when_known(self):
        h = nodes(self.ev, "host")[0]
        self.assertEqual(h["label"], "kobold.htb")
        self.assertEqual(h["props"]["ip"], "10.10.11.5")

    def test_service_carries_product_and_version(self):
        ssh = [n for n in nodes(self.ev, "service") if ":22" in n["label"]][0]
        self.assertIn("OpenSSH", ssh["label"])
        self.assertEqual(ssh["props"]["version"], "9.2p1")
        self.assertEqual(ssh["props"]["port"], "22")

    def test_services_require_their_host(self):
        """Reachability is a graph fact, not a naming convention."""
        hid = nodes(self.ev, "host")[0]["id"]
        for svc in nodes(self.ev, "service"):
            self.assertEqual(svc["props"]["requires"],
                             [{"target": hid, "min_rank": 0}])
        self.assertTrue(any(e["rel"] == "hosts" for e in edges(self.ev)))


class TestConfidenceTracksEvidence(unittest.TestCase):
    """Confidence is source reliability, so it must fall when -sV learned less."""

    def setUp(self):
        self.svc = {n["props"]["port"]: n
                    for n in nodes(from_nmap(xml(HOST_UP)), "service")}

    def test_product_and_version_is_A(self):
        self.assertEqual(self.svc["22"]["confidence"], "A")

    def test_product_only_is_B(self):
        self.assertEqual(self.svc["80"]["confidence"], "B")

    def test_bare_port_guess_is_C(self):
        """No product: this is nmap's guess from the port number, not a banner."""
        self.assertEqual(self.svc["3306"]["confidence"], "C")


class TestAScanIsNotAccess(unittest.TestCase):

    def test_host_stays_discovered(self):
        """Inferring `acquired` here made every host an UNMINED alarm in the
        workspace importer. A scan proves reachability and nothing else."""
        h = nodes(from_nmap(xml(HOST_UP)), "host")[0]
        self.assertNotIn("exploitation", h)
        self.assertEqual(h["epistemic"], "verified")

    def test_operator_edge_is_network_reach_only(self):
        e = [e for e in edges(from_nmap(xml(HOST_UP)))
             if e["src"] == "operator:me"][0]
        self.assertEqual(e["props"]["rank"], 0)
        self.assertEqual(e["props"]["privilege"], "network reach")


class TestHostsThatShouldBeSkipped(unittest.TestCase):

    def test_down_hosts_are_not_imported(self):
        body = ('<host><status state="down"/>'
                '<address addr="10.10.11.9" addrtype="ipv4"/></host>' + HOST_UP)
        self.assertEqual(len(nodes(from_nmap(xml(body)), "host")), 1)

    def test_host_with_no_address_or_name_is_skipped(self):
        body = '<host><status state="up"/><ports/></host>' + HOST_UP
        self.assertEqual(len(nodes(from_nmap(xml(body)), "host")), 1)

    def test_ipv6_only_host_uses_its_address(self):
        body = ('<host><status state="up"/>'
                '<address addr="fe80::1" addrtype="ipv6"/>'
                '<ports><port protocol="tcp" portid="80"><state state="open"/>'
                '</port></ports></host>')
        h = nodes(from_nmap(xml(body)), "host")[0]
        self.assertEqual(h["label"], "fe80::1")


class TestFailsLoudly(unittest.TestCase):
    """A half-parsed graph that looks complete is worse than no graph."""

    def test_malformed_xml(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "scan.xml")
        with open(p, "w") as fh:
            fh.write("<nmaprun><host>")
        with self.assertRaises(IngestError):
            from_nmap(p)

    def test_not_an_nmap_file(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "other.xml")
        with open(p, "w") as fh:
            fh.write('<?xml version="1.0"?><results><host/></results>')
        with self.assertRaises(IngestError):
            from_nmap(p)

    def test_no_hosts_up(self):
        body = '<host><status state="down"/><address addr="10.0.0.1" addrtype="ipv4"/></host>'
        with self.assertRaises(IngestError):
            from_nmap(xml(body))


class TestReimportIsIdempotent(unittest.TestCase):

    def test_same_scan_yields_same_ids(self):
        """Re-running a scan must not double the graph -- ids are derived from
        host and port, never from scan time or file name."""
        a = [e["args"]["id"] for e in from_nmap(xml(HOST_UP)) if e["op"] == "add_node"]
        b = [e["args"]["id"] for e in from_nmap(xml(HOST_UP)) if e["op"] == "add_node"]
        self.assertEqual(a, b)
        self.assertEqual(len(a), len(set(a)))


if __name__ == "__main__":
    unittest.main()
