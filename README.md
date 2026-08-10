# reckon

**Engagement state as a graph — for penetration tests and red-team operations.**
An append-only record of what you found, what you hold, and what you have not yet
tried, folded into a graph that answers where you stand and what you can already
reach.

*Dead reckoning*: fixing your position from a known point plus a log of the moves
you made. That is exactly what this does.

Stdlib Python 3 · no dependencies · 104 tests · v0.4.0

Built to catch two failures I kept repeating across engagements: working for hours
against a host whose identity I had only assumed, and cloning five repositories
while reading two.

## Why

Long engagements fail in two distinct ways, and a prose workspace cannot tell
them apart:

| Axis | States | Question | Typical miss |
|---|---|---|---|
| **epistemic** | `unexplored → hypothesized → verified → refuted` | is this true? | hours of work against a host whose identity was assumed, never confirmed |
| **exploitation** | `discovered → acquired → examined → exhausted` | have I used what I hold? | a credential cloned and never tried; five repos cloned, two read |

A document saying *"we have analyst"* cannot distinguish **have** from **tried**.
Two fields can, and once they exist the interesting questions become queries.

## What it looks like

```
$ reckon unmined
⚠ assist01 /api/chat (service) — reachable now, never examined, 50 events
⚠ fileserver backups share (artifact) — reachable now, never examined, 42 events
⚠ fileserver hr-private share (artifact) — reachable now, never examined, 41 events
⚠ assist-svc : D3mo-P@ss-03 (cred) — acquired, never examined, 21 events
⚠ j.rivera AS-REP hash (cred) — acquired, never examined, 15 events
```

A credential taken fifty moves ago and never tried against the service it opens
is invisible in a notes file and obvious here. `reckon board` gives the same
reading across every axis at once — objectives already satisfiable, assets never
mined, approaches over their failure budget, links trusted without verification.

## What it answers

| Command | Question |
|---|---|
| `reckon frontier` | what is reachable now, what is reachable *if*, what needs discovery |
| `reckon unrealized` | **objectives I can already satisfy and have not done** |
| `reckon unmined` | **assets acquired and never examined**, oldest first |
| `reckon stale` | hosts/services/creds trusted on an active path without verification |
| `reckon queue` | which single unverified link unlocks the most objectives |
| `reckon why <obj>` | the path to an objective and what it rests on |
| `reckon retro` | capability→realization latency, time-to-mine, assumption calibration |
| `reckon import <dir>` | parse a markdown workspace into events |
| `reckon console` | emit the self-contained HTML console |
| `reckon delta` | **what changed since you last looked** |
| `reckon decide` | record a choice, what it ruled out, and why |
| `reckon attempt` · `budget` | **2-strike failure budget, computed** |
| `reckon recall` · `suggest` | techniques that worked on nodes like this before |
| `reckon mcp` | MCP server on stdio — lets an agent record while it works |

## Why not a BloodHound-style graph

BloodHound is the mature example of the obvious shape: principals and computers
are nodes, abuse primitives are edges, and everything else hangs off them as
attributes. It works, it is battle-tested, and it is tighter than this.

reckon takes a different rule — **anything that can be independently held and
independently examined is a node** — because an attribute cannot carry state. If
a credential is a property of an edge, you cannot ask *"have I examined this?"*,
and that question is the whole point. The same applies to repositories cloned and
never read: as attributes of a host they are invisible, and you cannot raise an
alarm on an attribute.

## Install

```sh
git clone <repo> ~/projects/reckon
export PATH="$HOME/projects/reckon/bin:$PATH"
reckon ls
```

## The loop

### Once, when the engagement starts

```sh
reckon new opsnet                       # or: reckon import ~/work/opsnet --fresh
export RECKON_CURRENT=opsnet            # every later command targets this
```

### Every move — record what changed, one line each

The whole tool rests on one distinction, so keep these two separate:

```sh
reckon add cred analyst --state verified --conf B    # I now HAVE this
reckon hold cred:analyst acquired                    #   ...and have not used it
reckon examine cred:analyst "works on /api/login"    #   ...now I have
```

`acquired` is a promise to yourself that you will come back to it. `examine` is
the only thing that clears the alarm. Recording acquisition and skipping
examination is exactly the failure this tool exists to catch, so do not mark
something examined because you glanced at it.

Everything else is the same shape:

```sh
reckon add host dc01 --state verified -p zone=internal ip=10.99.10.5
reckon add artifact "FS02 backups share" --id artifact:backups
reckon add objective "proof.txt on DC01" --id obj:t7 --crown --requires host:dc01@3

reckon edge operator:me grants-access-to host:dc01 --state verified -p rank=3 privilege=root
reckon edge cred:analyst grants-access-to service:dash --state hypothesized --conf C -p rank=2

reckon state host:dc01 verified --conf A            # promote a hypothesis
reckon state e:rchen-dash refuted                   # or kill it
reckon obj obj:t7 achieved
reckon supersede finding:old finding:new "direct test disproved it"
```

**Ranks** are the privilege ladder used by `--requires`: `0` reach · `1` app/user
· `2` shell · `3` admin/root. An objective needing root on DC01 is
`--requires host:dc01@3`.

**Edge state is the assumption.** `hypothesized` means "I believe this works and
have not proved it" — those edges become the verification queue, ordered by how
many objectives each unlocks.

### Resuming — read the change, not the state

```sh
reckon delta         # what changed since you last looked; re-stamps the marker
reckon delta --since 40   # inspect history without disturbing the marker
```

This is the compass move: under information overload you do not want state, you
want the *change* in state. The output stays three lines whether the engagement
has 30 nodes or 3000.

### When an approach keeps failing

```sh
reckon attempt host:box01 failed "no local admin"
reckon attempt host:box01 failed "BYOVD blocked"     # -> ⚠ BUDGET BLOWN
reckon budget
```

Two failures with no success trips the alarm. Tweak-and-retry feels like
progress, which is exactly why it needs an external counter rather than
self-discipline.

### When you choose a path

```sh
reckon decide "pivot via .31" --reason "box01 privesc exhausted" \
              --rejected "brute box01" "BYOVD"
```

Reasoning that is not recorded lives in scrollback and gets re-litigated a week
later. Decisions render as a versioned log in `attack_brief`.

### When you want to know what worked before

```sh
reckon recall host:portal    # techniques applied to nodes like this, in past engagements
reckon suggest               # the same, for everything reachable right now
```

Your own history, not an external corpus: a curated KB tells you what is
*possible*; this tells you what worked *for you, on targets like this*.
Suggestions, never facts.

### Every checkpoint — read what you are missing

```sh
reckon board            # the three alarms, position, frontier, queue
reckon console          # the same, clickable, plus the six views
```

**Mid-engagement, keep the page open.** The console is a static artifact and does
not re-derive itself, so click **⟳ auto** in the header once: the tab then
reloads every 5s and picks up whatever the last `reckon console` wrote. Regenerate
after each move and the board stays live without you switching windows.

```sh
firefox ~/projects/reckon/out/$RECKON_CURRENT.html &   # once
reckon console                                      # after each move; tab catches up
```

The open pane survives the reload, so leaving it on `chain` or `brief` keeps it
there.

Read the alarms first; they are ordered by how cheap the win is:

| Alarm | Means |
|---|---|
| ⚠ UNREALIZED | you can already do this and have not |
| ⚠ UNMINED | you hold or can reach this and never looked at it |
| ⚠ UNVERIFIED | a live path depends on something you assumed |

Then `reckon queue` for the single test that unlocks the most, and
`reckon why obj:t7` for the path to any objective.

### When something is blocked

```sh
reckon frontier         # reachable now / if / unreachable / undeclared
```

`undeclared` means the objective has no `--requires` yet — that is an annotation
gap, not a dead end. `unreachable` means you genuinely need new discovery.

### At close

```sh
reckon retro            # capability->realization latency, time-to-mine, calibration
reckon console --redact --out ~/share/opsnet.html
```

`retro` answers "what did I leave on the table": objectives that became winnable
at event N and were never executed, and assets that sat in hand unexamined.

## Use

```sh
reckon new opsnet                      # start an engagement
export RECKON_CURRENT=opsnet

reckon add cred analyst --state verified --conf B
reckon hold cred:analyst acquired
reckon add host dc02
reckon edge operator:me holds cred:analyst --state verified
reckon edge cred:analyst grants-access-to service:dash --state hypothesized -p rank=2
reckon add objective "read protected.key" --id obj:key --requires service:dash@2 --crown

reckon board                           # where am I, what did I miss
reckon views                           # regenerate all six documents
reckon retro                           # what did I leave on the table
```

Batch a session in one call — the path an agent should use:

```sh
reckon apply session.json              # [{"op": ..., "args": {...}}, ...]
```

## How reachability works

Dijkstra over access edges, where an edge costs **0 if verified** and **1 if
hypothesized**:

- `dist == 0` → reachable **now**
- `dist >= 1` → reachable **if**, and the hypothesized edges on the path *are*
  the assumptions to test
- no path → unreachable; that needs discovery, not verification

Ranking those hypothesized edges by how many objectives each gates gives the
cheapest next test, for free.

## Storage

One append-only JSONL file per engagement under `engagements/` (gitignored).
State is a fold over the log, so history is never destroyed, a refuted
conclusion is superseded rather than deleted, and any past moment can be
replayed — which is what makes the retro metrics computable.

Plain text is deliberate: an agent can read the file and append events directly,
with no CLI round-trip.

## Console

```sh
reckon import ~/work/engagements/acme --fresh   # parse a markdown workspace
reckon console                                 # -> out/<name>.html
```

A self-contained offline page: zone-grouped board, click-to-expand drawer per
node, layered chain graph, state filter chips, and the six views as tabs.
Colours are derived, not asserted - `owned` / `app` / `reachable` come from the
Dijkstra result, and `reachable-if` is its own colour because "I could do this
if one assumption holds" is a different thing from "I can do this".

The drawer answers, for whatever you clicked: how I get here (the path, with each
edge's state), which objectives need it, what it opens, what opened it, and any
alarms it carries.

## Import, then refine

`reckon import` reads a workspace's `topology.md`, matching columns by header
synonym because real workspaces share no schema. It **asserts only what it
parsed** and guesses nothing - the same discipline as a scaffold draft.

That leaves a gap worth being explicit about. Imported objectives usually have no
declared requirements, so the frontier cannot be computed and they land in
`undeclared` - separate from `unreachable`, because "nobody said what this needs"
and "I cannot get there" call for different work. Declaring them is what turns an
inventory into analysis:

```sh
reckon add objective "read protected.key" --id obj:key --requires service:dash@2 --crown
```

Measured on one real engagement: imported, 2 objectives sat undeclared and
nothing could be derived. With requirements declared by hand, the same graph
surfaced 2 objectives that were **satisfiable with access already in hand and
never executed** - one of them the engagement's highest-value un-run step.

## Views

`reckon views` regenerates six documents from the one graph — `topology`,
`assumptions`, `attack_brief`, `recon`, `threat_model`, `plan`. Because they are
derived, they cannot contradict each other; keeping six hand-written files in
sync is the failure this replaces.

## Secrets

Engagement data contains live credentials — importing a real workspace pulls
tokens straight into node labels, and the console writes them into HTML.

**Default output is unredacted, because mid-engagement you need the actual
credential.** `engagements/` and `out/` are gitignored; treat both as you would
`loot/`.

```sh
reckon console            # full secrets — local only, never publish
reckon console --redact   # masked copy, for anything someone else will see
```

`--redact` masks what it recognises (known token prefixes, `user:secret` pairs,
private keys, JWTs) on the rendered artifact only; the event log always keeps the
truth. It is a courtesy, not a control — read a redacted artifact before sharing it.

## MCP

```sh
reckon mcp        # JSON-RPC over stdio, stdlib only, no SDK
```

13 tools over `api`: reads (`status`, `delta`, `board`, `why`, `recall`) and
writes (`add_node`, `add_edge`, `set_state`, `examine`, `set_objective`,
`attempt`, `decide`, `note`). Every validation rule lives in `api`, so the server
is a dispatch table rather than a second place to be wrong.

This is the landing surface for an agent's output. Without it, everything the
agent produces reaches the graph only if a human retypes it — and mid-engagement
is exactly when typing costs most. Tool errors come back inside the result
(`isError`), not as transport failures, so the agent sees `unknown node id:
host:TYPO` and corrects itself.

## Integrating (phase 2)

`reckon.api` is the stable surface. Both the CLI and any future MCP server go
through it, so validation cannot be bypassed by a second caller.

```python
from reckon import api
api.add_node("cl3", "host", "DC01", node_id="host:dc01", epistemic="verified")
api.status("cl3")          # coverage, frontier, unrealized, unmined, stale, queue
api.explain("cl3", "obj:t7")
```

`reckon.reference` is the seam to the curated layer, deliberately implementation-free
— the core imports no database driver and no MCP client. It encodes one rule:

| Store | Access | Enters the graph as |
|---|---|---|
| Neo4j (CVE/CWE/CAPEC) | deterministic, resolve by `(label, key)` | may be **verified** — curated taxonomy |
| ChromaDB (technique KB) | semantic only, no stable ids | **hypothesis, confidence D** — a cosine distance is not evidence |

```sh
reckon ref finding:rce neo4j CVE CVE-2023-46604
```

Phase 2 implements `reference.Resolver` behind the existing MCP services;
`NullResolver` is the default, so the core runs identically with nothing wired.
Reads are one-way by design: engagement learnings never flow back automatically,
so a messy live engagement cannot pollute curated knowledge.

## Guarantees

- **Concurrent-writer safe** — every append takes an exclusive `flock`, so a CLI
  and an MCP server can share one log without colliding sequence numbers.
- **O(1) appends** — sequence is read from the log tail, not by parsing it.
- **Loud on write, lenient on read** — an invalid write is refused with the reason;
  `fold` stays tolerant so older logs still load. `reckon state host:TYPO verified`
  fails instead of printing success and recording nothing.
- **Versioned log** — a log from a newer schema refuses to load rather than being
  silently misread.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

The suite in `tests/test_queries.py` encodes real engagement failures as
acceptance tests — an untried credential, an objective winnable with access in
hand, a host trusted without verification. If those stop being caught, the model
is wrong.
