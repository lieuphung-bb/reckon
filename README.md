# reckon

**Engagement state as a graph — for penetration tests and red-team operations.**
An append-only record of what you found, what you hold, and what you have not yet
tried, folded into a graph that answers where you stand and what you can already
reach.

*Dead reckoning*: fixing your position from a known point plus a log of the moves
you made. That is exactly what this does.

Stdlib Python 3 · no dependencies · 122 tests · v0.4.0

## Why

Long engagements fail in two distinct ways, and a prose workspace cannot tell
them apart:

| Axis | States | Question | Typical miss |
|---|---|---|---|
| **epistemic** | `unexplored → hypothesized → verified → refuted` | is this true? | hours of work against a host whose identity was assumed, never confirmed |
| **exploitation** | `discovered → acquired → examined → exhausted` | have I used what I hold? | a credential acquired and never tried; five repos cloned, two read |

A document saying *"we have this credential"* cannot distinguish **have** from
**tried**. Two fields can, and once they exist the interesting questions become
queries:

```
$ reckon unmined
⚠ assist01 /api/chat (service) — reachable now, never examined, 50 events
⚠ backups share (artifact)     — reachable now, never examined, 42 events
⚠ assist-svc (cred)            — acquired, never examined, 21 events
```

A credential taken fifty moves ago and never tried against the service it opens
is invisible in a notes file and obvious here.

## How it works

### The model — and why not a BloodHound-style graph

BloodHound is the mature example of the obvious shape: principals and computers
are nodes, abuse primitives are edges, everything else hangs off as attributes.
It is tighter than this.

reckon takes a different rule — **anything that can be independently held and
independently examined is a node** — because an attribute cannot carry state. If
a credential is a property of an edge you cannot ask *"have I examined this?"*,
and that question is the whole point.

Nodes: `host` `cred` `service` `artifact` `finding` `assumption` `objective`
`technique`. Edges assert that access exists, not how it was obtained, and carry
their own epistemic state plus a privilege `rank` (`0` reach · `1` app · `2` shell
· `3` admin).

### Reachability

Dijkstra over access edges, where an edge costs **0 if verified** and **1 if
hypothesized**, keeping every non-dominated `(cost, rank)` pair:

- `dist == 0` → reachable **now**
- `dist >= 1` → reachable **if** — and the hypothesized edges on the path *are*
  the assumptions to test
- no path → unreachable; that needs discovery, not verification

Keeping the pareto set matters: a host is routinely reachable cheaply-but-weakly
over the network *and* expensively-but-privileged via an untested credential.
Ranking those hypothesized edges by how many objectives each gates gives the
cheapest next test for free.

### Storage

One append-only JSONL file per engagement under `engagements/`. State is a fold
over the log, so history is never destroyed, a refuted conclusion is superseded
rather than deleted, and any past moment can be replayed — which is what makes
the retro metrics computable.

Plain text is deliberate: an agent can read the file and append events directly,
with no CLI round-trip.

### Guarantees

- **Concurrent-writer safe** — every append takes an exclusive `flock`, so a CLI
  and an MCP server can share one log without colliding sequence numbers.
- **O(1) appends** — sequence is read from the log tail, not by parsing it.
- **Loud on write, lenient on read** — an invalid write is refused with the
  reason; `fold` stays tolerant so older logs still load. `reckon state
  host:TYPO verified` fails instead of printing success and recording nothing.
- **Versioned log** — a log from a newer schema refuses to load rather than
  being silently misread.

## Install

```sh
git clone https://github.com/lieuphung-bb/reckon.git ~/projects/reckon
export PATH="$HOME/projects/reckon/bin:$PATH"
reckon ls
```

## Try it

Three synthetic engagements ship with the repo. `demo-prior` exists so `recall` has
history to answer from — recall excludes the current engagement, so one fixture
could never demonstrate it.

```sh
reckon -e demo-prior new demo-prior && reckon -e demo-prior apply examples/demo-prior.json
reckon -e demo new demo && reckon -e demo apply examples/demo.json

reckon -e demo board                  # all four alarms, frontier, verification queue
reckon -e demo console                # -> out/demo.html
reckon -e demo recall host:portal01   # a technique that worked on a node like this
```

`examples/demo.json` is a wholly fictional engagement built to fire every
mechanism at once — something a real engagement never obliges you by doing.

`examples/demo-ai.json` is the same exercise against an LLM application — a RAG
backend, a tool gateway, and an agent runner instead of a domain. It turns on one
refutation: `assumption:tenant-scope` ("retrieval is tenant-scoped") dies mid-log,
and killing it is what makes the crown objective reachable.

```sh
reckon -e demo-ai new demo-ai && reckon -e demo-ai apply examples/demo-ai.json
reckon -e demo-ai why objective:xtenant-read   # the four-edge chain the refutation opened
reckon -e demo-ai queue                        # one untested edge gating two objectives
```

```
examples/*.json           input fixtures, tracked
        ↓ reckon apply
engagements/<name>.jsonl  the source of truth: append-only event log, gitignored
        ↓ reckon console / views
out/<name>.html + views   derived, regenerated on demand, gitignored
```

Only the event log matters. Delete `out/` and it rebuilds; delete a log and the
engagement is gone.

## The loop

```sh
reckon new opsnet && export RECKON_CURRENT=opsnet
```

**Every move, one line each.** The whole tool rests on one distinction:

```sh
reckon add cred analyst --state verified --conf B    # I now HAVE this
reckon hold cred:analyst acquired                    #   ...and have not used it
reckon examine cred:analyst "works on /api/login"    #   ...now I have
```

`acquired` is a promise to come back to it; `examine` is the only thing that
clears the alarm. Do not mark something examined because you glanced at it —
that gap is the failure this exists to catch.

Everything else is the same shape:

```sh
reckon add host dc01 --state verified -p zone=internal ip=10.99.10.5
reckon add objective "proof.txt on DC01" --id obj:t7 --crown --requires host:dc01@3
reckon edge cred:analyst grants-access-to service:dash --state hypothesized --conf C -p rank=2

reckon state host:dc01 verified --conf A     # promote a hypothesis
reckon state e:analyst-dash refuted          # or kill it
reckon obj obj:t7 achieved
```

**Edge state is the assumption.** `hypothesized` means "I believe this works and
have not proved it" — those edges become the verification queue.

**Every checkpoint**, read the alarms first; they are ordered by how cheap the
win is:

| Alarm | Means |
|---|---|
| ⚠ UNREALIZED | you can already do this and have not |
| ⚠ UNMINED | you hold or can reach this and never looked at it |
| ⚠ BUDGET BLOWN | two failed attempts, no success — re-scope, do not retry |
| ⚠ UNVERIFIED | a live path depends on something you assumed |

```sh
reckon delta      # what changed since you last looked — fixed size at any scale
reckon board      # alarms, position, frontier, queue
reckon queue      # the single test that unlocks the most
reckon retro      # at close: what you left on the table
```

## Commands

| Command | Question |
|---|---|
| `reckon frontier` | what is reachable now, reachable *if*, or needs discovery |
| `reckon unrealized` | **objectives I can already satisfy and have not done** |
| `reckon unmined` | **assets acquired and never examined**, oldest first |
| `reckon stale` | nodes trusted on an active path without verification |
| `reckon queue` | which single unverified link unlocks the most objectives |
| `reckon why <obj>` | the path to an objective and what it rests on |
| `reckon delta` | what changed since you last looked |
| `reckon decide` | record a choice, what it ruled out, and why |
| `reckon attempt` · `budget` | 2-strike failure budget, computed |
| `reckon change` · `changes` · `cleaned` | what you altered on the target: the RoE cleanup list, and what a successor must not re-do |
| `reckon recall` · `suggest` | techniques that worked on nodes like this before |
| `reckon retro` | capability→realization latency, time-to-mine, calibration |
| `reckon import <dir>` | parse a markdown workspace into events |
| `reckon views` · `console` | six generated documents · self-contained HTML |
| `reckon mcp` | MCP server on stdio |

## Interfaces

**Console** — `reckon console` emits a self-contained offline page: zone-grouped
board, click-to-expand drawer per node, layered chain graph, filter chips, and the
six views as tabs. Colours are derived from the Dijkstra result, with
`reachable-if` its own colour because "I could do this if one assumption holds" is
a different thing from "I can do this". Click **⟳ auto** to have the tab reload
every 5s and pick up each regeneration.

**Views** — `reckon views` regenerates `topology` `assumptions` `attack_brief`
`recon` `threat_model` `plan` from the one graph. Because they are derived they
cannot contradict each other; keeping six hand-written files in sync is the
failure this replaces.

**MCP** — `reckon mcp` serves 14 tools over stdio JSON-RPC, stdlib only, no SDK.
This is the landing surface for an agent's output: without it, everything the
agent produces reaches the graph only if a human retypes it. Tool errors return
inside the result, so the agent sees `unknown node id: host:TYPO` and corrects
itself rather than the transport failing.

**Python** — `reckon.api` is the stable surface; the CLI and MCP server are both
thin wrappers over it, so validation cannot be bypassed by a second caller.

```python
from reckon import api
api.add_node("acme", "host", "dc01", node_id="host:dc01", epistemic="verified")
api.status("acme")               # coverage, frontier, unrealized, unmined, queue
```

**Import** — `reckon import` reads a workspace's `topology.md`, matching columns
by header synonym because real workspaces share no schema. It asserts only what it
parsed. Imported objectives arrive without declared requirements and land in
`undeclared` — separate from `unreachable`, because "nobody said what this needs"
and "I cannot get there" call for different work. Declaring them is what turns an
inventory into analysis.

## Secrets

Engagement data contains live credentials. **Default output is unredacted,
because mid-engagement you need the actual credential.** `engagements/` and
`out/` are gitignored; treat both as loot.

```sh
reckon console            # full secrets — local only, never publish
reckon console --redact   # masked copy, for anything someone else will see
```

`--redact` masks what it recognises — known token prefixes, `user:secret` pairs,
private keys, JWTs — on the rendered artifact only; the event log keeps the truth.
It is a courtesy, not a control.

## Reference layer

`reckon.reference` is a seam to an external CVE/technique layer, deliberately
implementation-free: the core imports no database driver and no MCP client. It
encodes one rule about how external knowledge may enter the graph:

| Store | Access | Enters as |
|---|---|---|
| Neo4j (CVE/CWE/CAPEC) | deterministic, resolve by `(label, key)` | may be **verified** — curated taxonomy |
| Vector KB | semantic only, no stable ids | **hypothesis, confidence D** — a cosine distance is not evidence |

`NullResolver` is the default, so the core runs identically with nothing wired.
Reads are one-way: engagement learnings never flow back automatically, so a messy
live engagement cannot pollute curated knowledge.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

`tests/test_queries.py` encodes real engagement failures as acceptance tests — an
untried credential, an objective winnable with access in hand, a host trusted
without verification. If those stop being caught, the model is wrong.

## Licence

MIT.
