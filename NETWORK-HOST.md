# Network host (design)

Status: **design only** (Phase 0). Not implemented in 0.8.0.

This note plans multi-person use of one project whose files live on a LAN or
network share, with agents and people on different computers. It does not change
the current local-ledger product.

## Three pieces (unchanged)

1. **Program** — the installable RSO Context package (release ZIP / repository).
2. **Plates** — the bounded project files. These may already live on a network
   share such as `\\fileserver\team\project` or `/mnt/projects/foo`.
3. **Ledger** — the SQLite evidence index. Today this is **per install** on the
   machine that runs `rso-context`.

Chat connectors and local MCP on one computer only see folders that computer can
open. They do not peer into another machine’s ledger.

## What already works without new code

| Pattern | Works today? | Notes |
| --- | --- | --- |
| Shared plates on SMB/NFS/AFP; each person installs RSO and runs `use` / `ingest` / `query` | Yes | Everyone must re-ingest after edits. |
| Several agents on **one** machine, distinct `--agent` names, one local ledger | Yes | Documented in [MANUAL.md](MANUAL.md#6-multiple-projects-and-agents). |
| Git project on the share | Better | Project identity can follow the repository, so different OS mount paths may still match. |
| Plain folder on the share | Fragile | Directory identity follows the **resolved path**. Different mounts look like different projects. |
| Put `context.sqlite3` on the share for everyone to open | **Avoid** | SQLite over SMB/NFS is prone to lock and corruption issues. |
| Local stdio MCP (`rso-context mcp`) | Yes | Process-local only; launch `--root` must be on that host. |
| Remote MCP / HTTP API to another computer’s ledger | **No** | This document proposes that. |

`--scope shared` remains for intentionally shared rule folders. It does not
replace a network host.

## Goal

One always-on **RSO host** next to (or on) the machine that exports the project
share:

- Reads plates from one or more **bounded** network roots.
- Owns the **canonical ledger** for those roots.
- Lets clients on other computers **query / use / validate** without each holding
  the canonical database.
- Keeps files as the plate: the host does not invent truth from chat.

Non-goals for this line of work:

- Embeddings or cloud model calls.
- Merging unrelated product trees into the ledger.
- Treating agent consensus as verification.
- Editing SQLite tables by hand.
- Replacing Git as the way humans sync file contents.

## Proposed architecture

```
[Network share: project plates]
        ^
        | read / ingest
        v
[RSO host: ledger + HTTP MCP or JSON API]
        ^
        | authenticated LAN calls
        v
[Client A laptop] [Client B desktop] [Agent runtime]
```

### Host responsibilities

- Bind only configured `--root` folders (same discipline as today’s MCP launch
  roots). Refuse broader filesystem access.
- Run ingest/watch against those roots on a schedule or on demand.
- Serve the existing packet contracts (`rso-context-packet/v2`, and MCP compact
  packets if exposed) so clients do not invent a second schema.
- Store the ledger on **local disk of the host** (fast, lock-safe), not on the
  share.
- Expose a read-mostly query path; serialize or queue writes (ingest,
  `record-validation`, propose).

### Client responsibilities

- Install the ordinary RSO program **or** only an MCP/HTTP client config.
- Point at the host URL and present a shared secret or equivalent.
- Use distinct `--agent` (or client) names for budgets and audit lines.
- Still treat `observed` as source-said until a named validation exists.

### Auth and trust

- Minimum: a shared token (or equivalent) for LAN access, plus bind to
  localhost or a private interface by default.
- Validator **names** in `record-validation` remain labels, not cryptographic
  identity (same as today). Stronger auth can wrap the host later; do not pretend
  the ledger authenticates people.

## Phased plan

### Phase 0 — this document

Design and roadmap pointer only.

**Acceptance:** design merged; no server code required.

### Phase 1 — localhost host

- `rso-context host` (name TBD) serves HTTP on `127.0.0.1` with a token.
- Endpoints wrap existing semantics: health, `use`/`ingest`, `query`,
  `record-validation` (exact shapes follow CLI/MCP).
- Ledger and roots on the same machine.

**Acceptance:** two local clients get identical `packet_hash` for the same query
against one scratch project; token required; roots enforced.

### Phase 2 — LAN + remote MCP

- Bind to a private interface; document firewall expectations.
- Optional streamable HTTP MCP (or documented remote transport) using the same
  tool names as local MCP (`rso_use`, `rso_query`, …).
- Clients on other OS mounts configure the host URL instead of local stdio.

**Acceptance:** a second computer on the LAN queries the host for a share-backed
project without copying `context.sqlite3`; launch-root refusals still work.

### Phase 3 — concurrency and operations

- Safe concurrent readers; single-writer or queued writes for ingest/validation.
- Backup: stop writers, copy host state directory with the plate backup story
  already in the manual.
- Clear errors when plates are offline (stale pointers, not silent invention).

**Acceptance:** documented failure modes and a backup drill on a disposable host.

## Risks

| Risk | Mitigation |
| --- | --- |
| SQLite writers from many clients | One host process owns the DB; clients are network callers. |
| Stale index after share edits | Host-side `watch` / scheduled ingest; clients call `use` when unsure. |
| Path identity across OS mounts | Prefer Git projects; document canonical host paths for non-Git roots. |
| Plates unavailable | Queries must surface stale/missing reconstruction, not fabricate text. |
| LAN exposure | Default localhost; token; document that a ledger holds searchable source text. |

## Relationship to local MCP

Local stdio MCP remains for single-machine agents. The network host is an
**additional** deployment mode for shared plates and a shared ledger. A laptop
MCP config may point at the host URL; it should not silently fall back to an
empty local ledger and claim the same project.

## Open questions

- Exact CLI name and packaging (`host` subcommand vs separate binary).
- Whether Phase 1 ships JSON-only first and MCP remote in Phase 2.
- How clients discover the host (manual URL vs optional LAN discovery).
- Whether named validations require a stronger identity hook in a later major
  version.

## See also

- [MANUAL.md](MANUAL.md) — multiple projects and agents; keeping the index current
- [INSTALL.md](INSTALL.md) — optional local MCP adapter
- [ROADMAP.md](ROADMAP.md) — planned slices
- [VERIFICATION.md](VERIFICATION.md) — what has been live-tested
