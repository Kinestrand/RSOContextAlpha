# Changelog


## 0.11.0 (unreleased, branch feat/0.11.0-lean)

Leaner packets, cheaper writes, better recall per byte.

- Defaults: compact byte budget 12 KB -> 4 KB, query limit 8 -> 4 (rso_check keeps 8).
- Graph nodes are off by default in query packets (`include_graph` / `--graph` to opt in).
- CLI `query` prints the compact packet by default; `--full` returns rso-context-packet/v2.
- `packet_hash` no longer covers budget counters (`run`), so budgeted and unbudgeted runs hash alike.
- MCP server instructions drop the "call rso_use first" rule. `rso_use` on a known project is a
  read-only freshness check plus a small targeted refresh, never a whole-project ingest.
- Per-file dedup (max 2 items per file), adjacent span merging, cross-file identical-chunk dedup.
- Compact evidence items carry hashes and project id only inside `expand`.
- Claims: capped at 4, short/code-like noise dropped, no unconditional verified/proposed boosts;
  coordination-board paths rank after other hits.
- Very long spans (one-line or minified files) are windowed around the match in packets.
- Ingest skips vendored and generated content (third_party, worktrees, nested checkouts, lock
  files, *.min.js, *.map) and indexes a repeated chunk inside one file once.
- Edited files no longer return empty evidence: the query re-ingests just those files and retries;
  with refresh off, the indexed copy is returned labelled stale.
- Schema revision 8 (compatible with schema_version 7, so running 0.10 servers keep working): FTS5 porter stemming with a prefix index (rebuilt from the existing index on
  migration, orphan rows dropped), larger stop-word list, bm25 column weights, AND-first query
  planning with OR fill.
- Ingest reads and hashes files outside the write lock, writes only changed files, and commits in
  batches of 200 files.
- New `prune-orphans` command removes chunks of superseded file versions (protected claim evidence
  kept); dry run unless `--apply --yes`.
- A query performs at most one write transaction (budget, cache and run record together).

## 0.10.0

### Fixed

- Queries on large ledgers took minutes. Graph matching scanned every node of
  the project with a prefix `LIKE` and checked liveness for every match, then
  re-hydrated every edge of each hub node. It now uses a new
  `(project_id, canonical_name)` index with equality and prefix ranges, scores
  candidates, and stops at the first live plate per node. On a 1.7M-chunk
  ledger a fixed 12-query set went from a median of about 98 s (p90 205 s) to
  0.55 s (p90 1.0 s) with the cache off.
- The packet cache almost never hit. The key included the run-budget counter,
  so a budgeted agent never matched its own earlier query; any packet with one
  stale candidate was treated as invalid forever; and `Z:\x`, a UNC spelling
  and `x/` were different keys. The key now uses spacing-normalized query text,
  the resolved scope path, corpus versions and retrieval config; freshness is
  checked by requiring each recorded plate to be in the state it was in when the
  packet was built; and run info is refreshed on the reused packet. Packets now
  carry `cache: {hit, key}` (excluded from `packet_hash`).
- `rso_use` and ingest failed with `too many SQL variables` when a file with
  more chunk versions than the SQLite build allows (32,766 in the MCP runtime)
  changed. FTS deletes now go through a chunk-to-rowid map in batches sized to
  the connection's limit, and no longer scan the whole FTS table per file.
- One folder could be registered as several projects (`git:`, `git-common:`,
  `git-path:` and `directory-path:` keys) when agents saw the Git remote
  differently or a remote was renamed. Registration now reuses the existing
  Git project registered at the same folder. A different root commit is never
  joined, and a plain-directory project still does not become a Git project.
- `doctor` ran a full `PRAGMA quick_check` and counted every FTS match, which
  took minutes on a multi-GB ledger. It now reads the header and schema by
  default and reports `timings_ms`; `doctor --integrity` runs the full check.

### Added

- `cleanup-projects --apply` deletes in short, separately committed batches with a pause between them, prints progress, reports the longest lock hold, and resumes an interrupted run from a work list saved in the ledger.
- Indexes on `edges(subject_id)`, `edges(object_id)` and `edges(evidence_chunk_id)`, added idempotently on open, so deleting nodes and chunks no longer scans every edge for foreign-key checks.
- `compact` reports reclaimable space; `compact --yes` runs VACUUM with an explicit locking warning.

- Schema 7 (additive migration): `runs.agent`, `chunk_fts_map`, and indexes on
  `nodes(project_id, canonical_name)`, `claim_evidence(chunk_id)`,
  `cache(project_id)` and `runs(agent, created_at)`. Query and check runs from
  the CLI and MCP record the agent. `rso-context usage [--since DATE]` reports
  runs by agent and project.
- `rso-context cleanup-projects` reports duplicate registrations of one folder
  (merge groups with a survivor) and junk projects (agent worktrees, temp
  folders, missing paths, scratch names, empty projects). It is a dry run
  unless `--apply --yes` is given; projects with proposed or validated claims
  are protected unless `--include-protected`. Merges move aliases, runs,
  budgets, proposed claims and validations to the survivor and never drop a
  named validation.
- `record-validation` accepts a unique 8+ character id prefix or `--text` with
  `--path`, plus `--agent` and `--evidence`. New MCP tool `rso_validate`
  records a named validation (validator + result). Existing tools and flags are
  unchanged.
- Degraded compact packets explain themselves: a `degraded` object gives the
  reason, evidence found vs inline, a byte budget that would fit, and how to
  recover omitted spans. `insufficient_budget` with zero inline evidence now
  says it is a budget limit, not an empty result.

### Changed

- FTS search is scoped to the active, domain and shared projects inside
  `MATCH`, so the candidate cap no longer fills with other projects' hits.
- For requests with four or more terms, the any-term fallback drops spans that
  share only one term with the request when better spans exist, and graph nodes
  must share two terms (or equal one) with such requests. Project-id nodes are
  never returned as graph matches. These cut unrelated board cards from packets.
- Connections use a 64 MB page cache, in-memory temp storage and memory-mapped
  reads.
- Query config algorithm is `fts5-bounded-requirements-v5-scoped-graph`; replay
  of runs recorded by older builds may report algorithmic drift.

## 0.9.4

### Changed

- MCP access is open by default so any agent host can use RSO on any project
  folder. Every host entry written by setup used to pin the folders named at
  setup time, and the server refused every other path, which is why agents in
  other projects reported that they could not run RSO. A tool call may now name
  any bounded project folder. `--strict-roots` restores launch-only access.
- One bounded-folder rule now covers every MCP path, including launch roots. It
  adds filesystem roots, folders that contain a user profile, hidden profile
  folders, operating-system folders, and the ledger home to the existing
  profile, Desktop, Documents, Downloads, and OneDrive refusals.
- The server starts however a host launches it. A desktop app that starts
  servers from its install folder or the user profile used to get a startup
  error; that directory is now just not used as a root.
- `mcp --setup` pins no `--root` unless one is given. A rewrite keeps the
  user's keys on the JSON entry and Codex tool-approval tables.

### Fixed

- `rso_query`, `rso_check`, and `rso_expand` could return an empty
  `insufficient_budget` packet at the default byte budget. JSON escaping made
  the wire result a few bytes larger than the packet, and the shrink loop
  stepped down too slowly to get under before falling back to 256 bytes. The
  inner budget is now found by binary search, so the largest packet that fits
  is returned.
- A host that replaces the child environment left Git off `PATH`. Ingest then
  stopped with a generic tool error, and project identity fell back to a
  non-Git key, so one checkout could register as two projects. RSO now finds
  Git through `RSO_GIT`, `PATH`, or the standard Windows install folders, and
  setup writes `RSO_GIT` into the entry.
- The profile refusal no longer depends on `USERPROFILE` or `HOME`; it asks the
  operating system for the profile folder. Another account's profile, including the
  shared Public profile, is refused as well.
- Every tool failure now reports its reason. Unexpected exceptions used to
  surface as "Error executing tool" with no cause.

### Added

- `mcp --setup --client all` configures every installed host and reports the
  ones it skipped. New hosts: `claude-desktop`, `cursor`, `windsurf`.
- `--client opencode` is accepted by the CLI; the parser used to reject it even
  though setup supported it. `doctor` reports every supported host.

## 0.9.3

### Added

- A launch with no `--root` binds the directory the host started the server in,
  which is the project the user opened, so one configured MCP entry serves every
  project instead of needing a config edit per folder. This is a launch-time
  bound, not run-time widening: the same refusal still rejects a user profile or
  an unbounded home child, and `--root` still wins when given.
- `mcp --setup --client opencode` writes the OpenCode entry. That host keys
  servers under `mcp` with a command list rather than `mcpServers`, and its
  config may be `.jsonc`, so the writer tolerates comments while leaving
  unrelated settings in place. `--root` may now be omitted from `--setup`, in
  which case the current directory is used.
- Generated host entries carry `RSO_CONTEXT_HOME` and, on Windows, `SystemRoot`
  with a two-entry system `PATH`. A host that replaces rather than augments the
  child environment otherwise spawns a server that can neither locate a ledger
  nor load winsock. The user's own `PATH` is deliberately not copied into a
  config file.

- `rso-context replay` re-runs the queries and checks already recorded in the
  ledger against the current build and reports which packets changed. Semantic
  parsing changes are hard to test by hand because one rule can move answers far
  from the sentence it was written for; the stored runs are a corpus that
  already exists. Replay writes nothing: no run budget is consumed, no `runs`
  row is added, and the cache is bypassed. Runs recorded at an older corpus
  version are skipped unless `--include-historical` is given, and differences
  there are labelled `corpus_moved`. Because retrieval reads live files, a file
  edited since the run was recorded also changes the packet without the corpus
  version moving; replay checks each recorded span against its file hash and
  labels those `sources_changed`. Only drift left over once both are ruled out
  counts as a regression and fails the command.
- `benchmarks/budget_sweep.py` sweeps `--limit` and `--token-budget` over
  recorded runs and prints a coverage-against-cost table. It is advisory, and a
  sweep over a small corpus will recommend whatever fits that corpus.

### Fixed

- The stdio adapter no longer dies before answering `initialize` when the
  spawn environment carries no home directory. `Path.home()` raises
  `RuntimeError`, not `OSError`, so the existing handler never fired. A host
  that replaces the child environment produced exactly that, and reported the
  silent exit as `-32001 Request timed out`.
- Startup failures now emit a JSON-RPC error frame on stdout as well as the JSON
  on stderr, with code `-32099`, a null id and the cause in the message. A
  client watching only stdout previously could not tell a crash from a hang.
- The `--db` default no longer needs a home directory to build the argument
  parser. In a home-less environment that raised before the CLI could report
  it, giving a bare traceback and exit 1 even when `--db` was passed.
- `default_home()` names `RSO_CONTEXT_HOME` and `--db` instead of surfacing
  pathlib's `RuntimeError`, and an unreadable working directory is reported as a
  launch-root failure rather than an uncaught `OSError`.
- The user-profile root refusal no longer disappears when `Path.home()` is
  unavailable. It also reads `USERPROFILE`, `HOME` and `HOMEDRIVE`+`HOMEPATH`,
  so a partial environment keeps the guard. A process that can name no home at
  all still has nothing to compare against.

## 0.9.2

### Fixed

- Evaluating, testing, comparing or trying something is no longer read as
  choosing it. "We evaluated the Arnold renderer for finals" used to make
  `rso_check` select Arnold; it now returns `unknown`. A clause with an explicit
  decision word still counts, and a sentence is split just before a decision
  verb, so "we evaluated Arnold and chose Cycles" selects Cycles. The same rule
  keeps "we tested exporting at 30 fps" from supporting a claim or supplying a
  value.

## 0.9.1

### Fixed

- Hard-wrapped sentences are read as one sentence. A line continues into the
  next only when it does not end in sentence punctuation and neither line is
  structural (heading, list item, table row, fence, indented code), so list
  items stay separate. Evidence reports the line range the sentence spans.
  Before this, a claim written across two lines returned `unknown`, which is
  common in documentation wrapped at 80 columns.
- A recorded question or suggestion is no longer evidence. Sentences carrying a
  question mark or a cue such as "asked", "whether", "proposed", "TBD" or
  "should we" are skipped, so "Someone asked whether preview export is 24 fps"
  no longer makes that claim `supported`.
- One negation rule for the whole codebase. The conflict detector ignored a
  plain "not" while `rso_check` counted it, so the two read the same sentence
  differently. Measured on this repository's own documentation, widening the
  conflict rule changed no query result.

### Changed

- Validation lookups run one claims query and one validations query per check
  instead of two queries per answer; topic-word sets are cached; the byte-budget
  trimmer copies the result only when trimming is needed; and the conflict
  witness scan measures each excerpt once. A twelve-question check over this
  repository went from a median of 163 ms to 138 ms with identical answers.

## 0.9.0

### Added

- `rso-context check` and MCP `rso_check`: typed `claim`, `choice`, and
  `value` questions answered from current source sentences, returned as
  `rso-check/v1`. Answers carry their evidence and expand references and have
  no probability field. Claims require stated numbers to match; choices accept
  per-option aliases and never break ties by rank. One call with up to 12
  questions uses one run-budget unit. `explain` accepts a `check_hash` and
  marks evidence whose source changed since the check.
- MCP checks list only projects whose folder sits under a launch root; a
  shared or domain project outside the roots no longer appears by name or id
  in `search_order` or `corpus_versions`.

## 0.8.1

### Added

- `rso-context mcp --setup --client gemini` and `--client antigravity`. Gemini
  CLI writes `~/.gemini/settings.json`; Antigravity writes
  `~/.gemini/config/mcp_config.json`. `RSO_MCP_GEMINI_CONFIG` and
  `RSO_MCP_ANTIGRAVITY_CONFIG` override the paths, and `doctor` reports both.
- The Windows `cmd` and PowerShell launchers use `RSO_MCP_RUNTIME` when set
  and fail with a clear message when it has no `python.exe`.
- `DESIGN-RSO-CHECK.md`, a proposal for typed evidence questions. Not
  implemented.
- The release ZIP now carries the Linux path-isolation fixes, Git Bash
  launcher, and cross-platform CI that landed after 0.8.0 was packaged.

### Fixed

- Disagreement detection compares sentences, not whole chunks. A negated
  sentence conflicts with an affirmative one only when both share at least two
  topic words and the two spans come from different paths. Before this, any
  "do not" in one file and any "must" or "required" in another flagged a
  conflict, which was common in documentation-heavy projects.
- When a conflict set exceeds the compact byte budget, the packet keeps the
  smallest two-sided pair that still shows the conflict and lists the rest as
  `conflict_set` expand refs. It no longer returns an empty packet.
- Budget fallbacks keep omitted expand refs before dropping them, and a packet
  whose conflict evidence cannot fit reports `insufficient_budget` instead of
  `ok` with no evidence.

## 0.8.0

### Added

- Optional local stdio MCP adapter sharing the CLI's ledger and project identity.
  Tools: `rso_use`, `rso_resume`, `rso_query`, `rso_explain`, and `rso_expand`.
- Compact `rso-mcp-packet/v1` evidence packets, byte budgets, and expansion of
  omitted source spans. Ordinary CLI queries retain `rso-context-packet/v2`.
- Bounded ingestion coverage reports and explicit empty-result diagnostics.
- Codex and Claude Code setup/removal helpers, optional-runtime diagnostics,
  and isolated configuration tests.
- Research-origin and verification records, a documentation map, and a
  versioned release download with an explicit package manifest.
- The Kinestrand repository retains its original regression suite alongside the
  newer focused tests. Current documentation and downloads point to
  `Kinestrand/RSOContextAlpha`.

### Fixed

- Configuration removal preserves unrelated TOML tables with commented headers.
- MCP query and explain filter sources outside configured launch roots.
- Compact fallback packets respect their byte budget. MCP query uses text-only
  tool content so its serialized `tools/call` result respects the wire budget.
- MCP readiness requires exactly `mcp==2.2.0` in an isolated runtime.

### Upgrade from 0.7.0

Run the new package's installer with the same prefix, then check `--version`
and `doctor`. The installer preserves unrelated files and backs up differing
package files before replacement. Program installation does not move or delete
the separate per-user index. See [INSTALL.md](INSTALL.md#upgrade-and-removal).

The CLI remains usable without MCP. To add MCP, install the isolated runtime,
configure bounded roots, reconnect the host, and call `rso_use` for each permitted
project. Launch permission does not register or ingest a project.

Live host and platform coverage is recorded in [VERIFICATION.md](VERIFICATION.md).
The ongoing three-host CLI trial is not a completed comparative benchmark or
proof of live MCP compatibility in every host.

## 0.7.0

Earlier standalone source baseline: local SQLite/FTS5 evidence ledger, incremental
ingestion, pointer spans, proposals and named validation records, per-agent
budgets, CLI workflows, installer, and beginner documentation. Apache-2.0
licensing and notices were added before the 0.8.0 source update.

This historical entry does not imply that a 0.7.0 GitHub Release or tag existed.
