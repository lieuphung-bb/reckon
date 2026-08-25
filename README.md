# reckon

**Engagement state as a graph — for penetration tests and red-team operations.**
You record what you find as you go. reckon keeps the record, and answers the two
questions a notes file cannot: what can I reach right now, and what am I holding
that I never used.

*Dead reckoning*: fixing your position from a known point plus a log of the moves
you made. That is exactly what this does.

Stdlib Python 3 · no dependencies · v0.6.0

## Why

Two things go wrong on a long engagement, and notes cannot tell you either one is
happening.

**You stop being sure what is actually true.** Hours go into a host whose identity
was assumed and never confirmed, or a route everyone believed worked and nobody
tested.

**You lose track of what you already hold.** A credential taken fifty moves ago
and never tried against the service it opens. Five repositories cloned, two read.

reckon tracks those separately on every item, so both become questions you can
ask instead of things you hope you would notice:

```
$ reckon unmined
⚠ assist01 /api/chat (service) — reachable now, never examined, 50 events
⚠ backups share (artifact)     — reachable now, never examined, 42 events
⚠ assist-svc (cred)            — acquired, never examined, 21 events
```

That is the whole idea: *have* and *tried* are different facts, and a document
that records only the first cannot warn you about the second.

| Axis | States | Question |
|---|---|---|
| **epistemic** | `unexplored → hypothesized → verified → refuted` | is this true? |
| **exploitation** | `discovered → acquired → examined → exhausted` | have I used what I hold? |

## Install

```sh
git clone https://github.com/lieuphung-bb/reckon.git ~/projects/reckon
export PATH="$HOME/projects/reckon/bin:$PATH"
reckon ls
```

One optional dependency, for one thing: `jq`, used by the hook that records tool
activity. Without it that hook writes nothing, and the alarm for unrecorded work
(below) can never fire. `reckon hook config` says so when it is missing.

Engagement data does not live in the checkout. It goes to
`~/.local/share/reckon` (or `$XDG_DATA_HOME/reckon`), so deleting or re-cloning
the tool cannot take an engagement record with it. Set `RECKON_HOME` to keep it
somewhere else — a per-client volume, an encrypted disk — and `RECKON_OUT` to
send only the rendered files elsewhere, such as a folder a host machine reads.
The log itself should stay on a local filesystem: single-writer safety rests on
`flock`, which is unreliable over network and shared-folder mounts.

Everything reckon reads from the environment, in one place:

| | Default | |
|---|---|---|
| `RECKON_HOME` | `$XDG_DATA_HOME/reckon`, else `~/.local/share/reckon` | the data root: logs and markers |
| `RECKON_OUT` | `$RECKON_HOME/out` | rendered console + views, separable so they can travel |
| `RECKON_CURRENT` | none | default engagement, so `-e` can be omitted |
| `RECKON_REFERENCES` | none | catalog sources, `store=path`, `:`-separated |
| `RECKON_AGENT` | none | who is recording, when several agents share an engagement |
| `RECKON_AUTORENDER` | off | `1`/`true`/`yes`/`on`: regenerate the board after every write |

## Try it

Three synthetic engagements ship with the repo. `demo-prior` exists so `recall` has
history to answer from — recall excludes the current engagement, so one fixture
could never demonstrate it.

```sh
reckon -e demo-prior new demo-prior && reckon -e demo-prior apply examples/demo-prior.json
reckon -e demo new demo && reckon -e demo apply examples/demo.json

reckon -e demo board                  # all four alarms, frontier, verification queue
reckon -e demo console                # -> <data root>/out/demo.html
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
examples/*.json                    input fixtures, tracked, in the repo
        ↓ reckon apply
<data root>/engagements/<name>.jsonl   the source of truth: append-only log
        ↓ reckon console / views
<data root>/out/<name>.html + views    derived, regenerated on demand
                                       (or after every write — see below)
```

The data root is `~/.local/share/reckon` unless you moved it. Only the event log
matters: delete the rendered output and it rebuilds; delete a log and the
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

Those describe the engagement. A second group describes **the instrument**, and
it prints above everything else — if the record is behind the work, every
section below it is a confident picture of an hour ago:

| Alarm | Means |
|---|---|
| ⚠ STALE RECORDING | nothing has been recorded for 30 minutes |
| ⚠ EMPTY DELTA | nothing since the last checkpoint — quiet, **or** unrecorded |
| ★ UNRECORDED WORK | tool calls happened and none of them were written up |

**UNRECORDED WORK is the one that pays for the rest.** reckon keeps its own
automatic log of activity, independent of what anyone remembers to write down. It
compares the two and raises this alarm when work happened but nothing was
recorded — so a report can no longer look healthy just because reporting stopped.
It also drafts the cleanup list of changes made to the target's systems, which a
human confirms before anything is recorded.

```sh
reckon delta      # what changed since you last looked — fixed size at any scale
reckon board      # alarms, position, frontier, queue
reckon queue      # the single test that unlocks the most
reckon retro      # at close: what you left on the table
```

**When a session ends**, planned or not, reckon hands the next one a brief: where
in the agreed plan the work stopped, what earlier steps already produced so nobody
redoes them, and why the last step halted. A step cannot be marked done without
recording what it produced, so results are not left stranded in a closed session's
scrollback. And a blocked step must record *why* — ran out of context, hit a
refusal, waiting on someone — because the right next move is different in each
case.

```sh
reckon plan add obj:t7 "shadow-cred to DA" --step "dump hives" --step "PKINIT"
reckon step done    <plan> 1 --produced cred:dcc2
reckon step blocked <plan> 2 --reason refusal --note "declined to write the payload"

reckon handoff    # ★ call this first when resuming — position alone re-derives a path you already have
reckon fleet      # across agents: who has stopped without saying so
```

## Working with an agent

Three surfaces, and they answer different questions. Keeping them apart is what
makes the arrangement work:

| Surface | What it is for |
|---|---|
| **the chat** | judging the reasoning — an argument, persuasive whether or not it is right |
| **the board** — the HTML console | a mirror of what has actually been recorded, and nothing else |
| **the event log** | the truth both of the others are derived from |

An agent's account of an engagement is convincing by construction. The board is
not trying to convince you of anything: it shows what is in the log, so a claim
made in chat and never recorded is visibly absent from it. That is the check —
and it only works if the board in front of you is current.

```sh
export RECKON_AUTORENDER=1
```

With that set, every write regenerates the console and the six views. The loop
becomes: **the agent proposes → you agree → the agent records → the board updates
itself.** Nobody has to remember "and then checkpoint".

Off by default: one render per write is a cost, and if you are not watching a
board you should not pay it. Leave it unset and the console regenerates when you
ask for it, exactly as before. Pair it with **⟳ auto** in the console, which
reloads the open tab every 5s, and the page in front of you tracks the log
without a keystroke.

Two things it deliberately does not change. Rendering never affects recording:
if a render fails, the write still stands and the fact is still in the log — a
broken board must never cost you a recorded fact. And `reckon checkpoint` is
still the ritual for alarms, the delta and the stamp; what autorender removes is
running it *only* to refresh a stale page.

### Which tab answers which question

`reckon console` puts seven tabs across the top. They are not variations on one
page — each answers a different question, and knowing which is which is the
difference between finding a recorded decision and re-deciding it:

| Tab | Answers |
|---|---|
| **board** | where you stand: every node, grouped by zone, alarms on the cards |
| **chain** | how the access you hold connects, and which links are still assumed |
| **brief** | what was decided and what to do next — **the decision log lives here** |
| **assumptions** | what is believed and not yet verified |
| **threat model** | what the target's exposure looks like from what you found |
| **plan** | the agreed path to an objective, and where in it the work stopped |
| **recon** | what has been seen, host by host |

Six of them are also written as markdown next to the console, for anything that
reads files rather than a browser — `board` as `topology.md`, `brief` as
`attack_brief.md`, the rest under their own names. `chain` is drawn in the page
and has no file.

### Priming the agent to record

Autorender renders what was recorded. It never records — that part needs
judgment about what was chosen and what it ruled out, and it stays with the
agent. What makes it happen is an instruction, so put one in the file your agent
already reads (`AGENTS.md`, `CLAUDE.md`, or whatever your tool uses). Copy this:

```markdown
## Recording to reckon

Record as you work, not at the end. The board is only as current as the log.

- **At every decision point, call `decide` before acting on it** — what you
  chose, what you ruled out, and why. A decision that is not recorded gets
  re-argued in three hours by someone who cannot see it.
- Record a finding when you find it: `add_node` for anything you can hold or
  examine, `add_edge` for access you believe exists.
- An edge you have not tested is `hypothesized`, not `verified`. Marking it
  verified because it looks right is the one thing that makes the board lie.
- `examine` means you actually went through it. Not glanced at it.
- On a plan step, `step_state ... done` with `produced` — the node ids the step
  created. A step whose output is not in the graph leaves the next session to
  redo the work.
- If you are unsure whether something is worth recording, record it.
```

That block is an **example to copy**, not a file this repo ships. The engagement
runs in your working directory, not in the reckon checkout, so an instruction
file living here would never be loaded. `AGENTS.md` is the cross-tool convention
if you want one file several agents read.

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
| `reckon changes --suggest` | drafts that cleanup list from what you actually ran — **proposals only**, nothing is recorded until you confirm |
| `reckon plan add` · `plans` · `step` | the agreed path to an objective, and where in it you are |
| `reckon handoff` · `fleet` | **the successor brief — resume point first**; who is where |
| `reckon checkpoint` · `alarms` | one command for "where are we": what changed, what is wrong, documents rebuilt |
| `reckon trace` | the record of what actually ran — **what makes "nothing happened" distinguishable from "nothing was written down"** |
| `reckon hook session-start` · `stop` · `config` | harness-invoked resume and end-of-session stamp; always exit 0 |
| `reckon recall` · `suggest` | techniques that worked on nodes like this before |
| `reckon ref` | tag a node with a catalog id — ATLAS, CVE — and show its canonical name |
| `reckon retro` | at close: how long wins sat unused, what was never looked at, how well-calibrated your confidence was |
| `reckon import <dir>` · `import --nmap <file>` | parse a markdown workspace, or an `nmap -oX` scan, into events |
| `reckon views` · `console` | six generated documents · self-contained HTML |
| `reckon mcp` | MCP server on stdio |

## Interfaces

**Console** — `reckon console` emits a self-contained offline page: zone-grouped
board, click-to-expand drawer per node, layered chain graph, filter chips, and the
six views as tabs — [which tab answers which question](#which-tab-answers-which-question)
is mapped above. Colours are derived from the Dijkstra result, with
`reachable-if` its own colour because "I could do this if one assumption holds" is
a different thing from "I can do this". Click **⟳ auto** to have the tab reload
every 5s and pick up each regeneration; with `RECKON_AUTORENDER=1` there is a
regeneration to pick up after every write.

**Views** — `reckon views` regenerates `topology` `assumptions` `attack_brief`
`recon` `threat_model` `plan` from the one graph. Because they are derived they
cannot contradict each other; keeping six hand-written files in sync is the
failure this replaces.

**MCP** — `reckon mcp` serves 19 tools over stdio JSON-RPC, stdlib only, no SDK.
This is the landing surface for an agent's output: without it, everything the
agent produces reaches the graph only if a human retypes it. Tool errors return
inside the result, so the agent sees `unknown node id: host:TYPO` and corrects
itself rather than the transport failing.

It is a subprocess, not a service — no port, no container, no daemon. Register it
with your agent and it is spawned per session:

```json
{"mcpServers": {"reckon": {
  "command": "/path/to/reckon/bin/reckon",
  "args": ["mcp"],
  "env": {"RECKON_HOME": "/home/you/.local/share/reckon",
          "RECKON_AUTORENDER": "1",
          "RECKON_REFERENCES": "atlas=/home/you/ref/atlas-techniques.md"}}}}
```

The subprocess inherits the agent's environment, not your shell's, so anything
you set in a profile is absent here — set it in `env` or it is not set at all.
`RECKON_AUTORENDER` is the one people miss: exported in a shell it has no effect
on the agent's writes, and the board sits still while the log fills up.

**Leave `RECKON_CURRENT` out** and pass `engagement` per call: a stale default
sends a session's records to the wrong engagement, silently.

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

`reckon import --nmap` reads an `nmap -oX` scan instead: hosts up become hosts,
open ports become services that require them. Confidence follows the evidence —
`A` when `-sV` returned a product and version, `B` for a product alone, `C` for
nmap's guess from the port number. `filtered` and `closed` ports are not imported
at all; in a graph built to separate *absence of evidence* from *evidence of
absence*, recording them as services would destroy the distinction. Hosts stay
`discovered`: a scan proves reachability, not access.

The reason to import a scan rather than write the nodes by hand is the same reason
hooks exist below. An engagement ran to completion with an empty graph because
"record what you find" was an instruction rather than an event.

**Hooks** — three, run by the harness rather than by the agent, so they fire
whether or not anything remembers. A rule in a prompt asking an agent to fetch its
own brief is probabilistic and fails silently; that is exactly the remembering
that does not happen when a session dies mid-step.

```sh
reckon hook config >> .claude/settings.json   # the fragment to paste; edit to merge
```

`SessionStart` injects the resume brief, so a new session begins already holding
the cursor, what prior steps produced, why the last one stopped and what is owed.
`Stop` stamps a checkpoint, so the *next* brief reflects where work actually
stopped rather than whenever someone last ran a checkpoint by hand.

`PostToolUse` records one line per action taken, giving reckon its own account of
activity that does not depend on anyone writing anything down. That is what the
unrecorded-work alarm compares against, and what `reckon changes --suggest` reads
to draft the cleanup list.

It is the only hook that is not a reckon command. It runs on *every* action, so it
is a shell one-liner with no interpreter to start, and `jq` does the escaping and
the length cap in one pass. **What it records never enters the graph** — it is
evidence of what ran, and deciding what a command *meant* stays with the operator.

**These commands invert the house rule, and it is the one place that happens.**
Everywhere else an invalid input is refused loudly. A hook runs on the harness's
schedule, so one that fails loudly takes a session down with it — a missing
engagement, a corrupt log, a half-written config must all exit `0` and print
nothing. `reckon handoff` on a corrupt log exits `1` with the reason;
`reckon hook session-start` on the same log exits `0` in silence. That inversion
is confined to `reckon/hooks.py`, which is why it is its own module.

## Architecture

Stdlib Python 3, no packages, no services. One append-only file per engagement;
everything else is derived from it.

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

One append-only JSONL file per engagement, in `engagements/` under the data
root. State is a fold over the log, so history is never destroyed, a refuted
conclusion is superseded rather than deleted, and any past moment can be
replayed — which is what makes the retro metrics computable.

Plain text is deliberate: an agent can read the file and append events directly,
with no CLI round-trip.

The root sits outside the source tree, and both halves live under it: the logs
in `engagements/`, the rendered console and views in `out/`. One path to back
up, to carry between machines, and to wipe when an engagement closes. Rendered
output stays there rather than going to a cache directory, because the console
is the most credential-dense file reckon writes and a cache is where cleanup
tools delete freely.

### Guarantees

- **Concurrent-writer safe** — every append takes an exclusive `flock`, so a CLI
  and an MCP server can share one log without colliding sequence numbers.
- **O(1) appends** — sequence is read from the log tail, not by parsing it.
- **Loud on write, lenient on read** — an invalid write is refused with the
  reason; `fold` stays tolerant so older logs still load. `reckon state
  host:TYPO verified` fails instead of printing success and recording nothing.
- **Versioned log** — a log from a newer schema refuses to load rather than
  being silently misread.

## Secrets

Engagement data contains live credentials. **Default output is unredacted,
because mid-engagement you need the actual credential.** Treat the whole data
root as loot: the logs and the rendered console alike.

It is kept out of the source tree so that no ignore rule stands between a
credential and a push. A fork whose owner rewrites `.gitignore`, or a tidy-up
that moves the ignore rules, cannot publish what was never in the repository.
The ignore entries remain anyway, for anyone who points `RECKON_HOME` back at
their checkout.

```sh
reckon console            # full secrets — local only, never publish
reckon console --redact   # masked copy, for anything someone else will see
```

`--redact` masks what it recognises — known token prefixes, `user:secret` pairs,
private keys, JWTs — on the rendered artifact only; the event log keeps the truth.
It is a courtesy, not a control.

## Reference layer

A technique or CVE id recorded against a node shows its canonical name, read from
a reference file you point at. Only the id is stored, so the source can change
without touching engagement data — and an id the source does not contain is
refused when you write it, rather than resolving to nothing forever.

```sh
export RECKON_REFERENCES="atlas=~/ref/atlas-techniques.md"
reckon ref service:assist-chat atlas technique AML.T0051.001
```

A source is any markdown holding a two-column table of `` | `id` | name | ``
rows; anything wider or without the code span is prose and is ignored. Name each
source as `store=path` and separate several with `:`, as you would a `$PATH`.
Lookups are by id, so the label you record is provenance and is not checked
against a file source.

**reckon ships the reader, never the corpus.** Point it at what you have — your
own catalog, or a public one you keep current. Nothing is configured by default
and the tool behaves exactly as it does without this feature.

One rule governs how external knowledge enters the graph, and it holds whatever
the source is:

| Source | Access | Enters as |
|---|---|---|
| An id table — a file today, a graph later | deterministic, resolve by a stable id | may be **verified** — a curated taxonomy is a fact you can cite |
| Semantic search | no stable ids to join on | **hypothesis, confidence D** — a cosine distance is not evidence |

Reads are one-way: engagement learnings never flow back automatically, so a messy
live engagement cannot pollute curated knowledge.

## Upgrading

Engagement data used to live inside the checkout. If you have `engagements/`
there from an earlier version, move it yourself — nothing relocates it for you,
because data that appears to vanish on an upgrade is the worst way for a tool to
fail:

```sh
mkdir -p ~/.local/share/reckon
mv ~/projects/reckon/engagements ~/.local/share/reckon/
mv ~/projects/reckon/out ~/.local/share/reckon/     # optional; it rebuilds
```

Or leave it where it is and pin `RECKON_HOME=~/projects/reckon` in your shell
profile. Either way, do it before the first run on the new version, or reckon
will start a fresh, empty store at the new location and the old one will simply
sit there unread. Re-run `reckon hook config` afterwards: the tool-activity hook
carries the path it writes traces to.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

`tests/test_queries.py` encodes real engagement failures as acceptance tests — an
untried credential, an objective winnable with access in hand, a host trusted
without verification. If those stop being caught, the model is wrong.

## Licence

MIT.
