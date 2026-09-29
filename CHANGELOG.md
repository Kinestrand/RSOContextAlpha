# Changelog

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
