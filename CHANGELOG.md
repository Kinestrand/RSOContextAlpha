# Changelog

## Unreleased

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
