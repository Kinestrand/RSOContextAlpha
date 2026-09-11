# Changelog

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
