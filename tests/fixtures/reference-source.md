# Synthetic reference source (fixture)

Invented ids in the shape a real source uses. reckon ships the reader, so no
corpus lives in this repo — this file exists to exercise the parser only.

## Attack surface (prose — must NOT be ingested as ids)

| Surface | What to try |
|---|---|
| A chat endpoint that echoes system text | prompt extraction |
| `curl`-shaped docs left on staging | credential reuse |

| id | tactic | name |
|---|---|---|
| `SYN.T9999` | reconnaissance | Three columns, so not an id row |

## Technique index

| id | name |
|---|---|
| `SYN.T0001` | Probe The Model For Its Instructions |
| `SYN.T0002` | Poison A Retrieval Corpus |
| `SYN.T0002.001` | Poison A Retrieval Corpus: Public Wiki |
