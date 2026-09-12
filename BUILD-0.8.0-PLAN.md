# RSO 0.8.0 build plan

Prepared September 9, 2026; status updated September 12. Days 1-7 and follow-up runtime, configuration-preservation, scope, and wire-budget fixes are implemented. Live Codex and Claude Code use/resume/query/explain/expand now pass on Windows against existing bounded workspaces (September 10 and September 12). Native macOS remains unverified; the full disposable-project host acceptance requirement below is not complete. See [VERIFICATION.md](VERIFICATION.md). Dated records below describe their original sessions; statements that nothing was pushed or no live host was tested are historical, not current status.

## Outcome and scope

Make the existing local evidence ledger available through MCP, with bounded responses and an explicit way to retrieve omitted evidence. Keep the CLI and MCP on the same core functions, project identity, and SQLite ledger.

[Inference] This is the highest-value combination from the six-project review. All effort and completion dates below are planning estimates, not measured implementation times or scheduled background work. The schedule assumes one implementer with 5-7 focused hours per workday, starting September 10. No parallel agents are assumed.

## Ranked work

| Priority | Deliverable | Impact | Estimated effort | Release |
| --- | --- | --- | --- | --- |
| 1 | Local stdio MCP adapter, shared core, two-client verification | Direct access from compatible agents without shell-command translation | 10-14 hours including contract and packaging investigation | Required |
| 2 | Complete response budget, exact snippets, evidence expansion | Smaller usable responses without hiding omissions or changing evidence | 6-8 hours | Required |
| 3 | Client setup, doctor checks, upgrade and removal behavior | Makes the interface usable beyond a development checkout | 5-7 hours | Required |
| 4 | Per-run coverage reasons and honest empty-result reporting | Distinguishes missing evidence from known ingestion gaps | 3-5 hours | Bounded scope; first item to defer |
| 5 | Regression, concurrency, artifact, and installed-client checks | Establishes that the new build works and preserves the ledger | 6-8 hours | Required |

Total: 30-42 focused hours. These rows include all scheduled work; testing time is not added again.

## Implementation contracts

### MCP access

- Add a proposed `rso-context mcp` entry point using local stdio. Do not add HTTP hosting, remote authentication, a daemon, or another database in this release.
- Reuse ingestion, query, resume, and explanation functions directly. Preserve existing CLI commands and their default output contract.
- Start with `rso_use`, `rso_query`, `rso_resume`, `rso_explain`, and `rso_expand`. Defer proposal/validation mutation tools until retrieval access is proven. Existing CLI proposal workflows remain available.
- Require bounded project selection and explicit agent attribution. Resolve paths through existing project identity rules. Caller-provided agent names are attribution, not authentication or authority.
- Treat query run records, budgets, and ingestion as writes where applicable. Do not advertise every retrieval operation as having no side effects.
- Bind each server launch to allowed bounded roots and a configured database. Do not expose arbitrary database paths or unrestricted filesystem reads in tool arguments.
- Preserve observed/proposed/verified distinctions and named-validation authority. Do not expose generic SQL or arbitrary command execution.
- Day 1 compares a supported Python MCP SDK with current packaging constraints. Prefer the SDK; pin dependencies in an isolated MCP runtime if needed, preserving the existing CLI installation. Do not handwrite a protocol implementation simply to claim zero dependencies.
- Check supported protocol versions against the actual clients used for release acceptance. Do not equate implementing the latest specification with compatibility everywhere.
- Exercise separate client-launched processes against one scratch ledger. Reuse existing WAL and busy-timeout behavior first; add bounded retries only where a reproduced failure warrants them. Never remove another process's locks.

### Evidence packets and expansion

- Add an explicit byte budget for the complete serialized tool result, including metadata and any duplicated text/structured representations. Token counts remain labeled estimates unless a tokenizer is actually used.
- Keep the existing CLI query format by default. Introduce an explicit compact mode shared with MCP; version any incompatible packet schema. Do not silently change existing packet hashes or audit consumers.
- Preserve claim IDs, trust states, source hashes, corpus versions, disagreement status, and precise source ranges. Count omitted candidates and explain truncation.
- Select whole matching lines or paragraphs from hash-checked source text. Keep each excerpt's own range and the parent chunk identity. Do not splice separate ranges into an apparently continuous quote.
- Do not paraphrase policy evidence. Preserve the relevant condition and exception together. If the implementation cannot establish a safe smaller boundary, retain the whole chunk or omit it with an expansion reference.
- Provide a stable expansion reference containing or resolving project, source, expected hash, and range. Resolve it within authorized roots and active source identity. Never turn a token into unrestricted path access.
- Expansion is itself bounded and supports further ranges. Changed or missing files return a stale/unavailable result. Pointers do not guarantee recovery of a deleted historical file.
- If required conflict/provenance information cannot fit, return a small explicit insufficient-budget result. Do not drop one side of a conflict or exceed the budget silently.

### Coverage and setup

- Limit coverage work to the current ingest run: counts and a bounded list of path/reason pairs for failures and exclusions encountered during enumeration. Report whether enumeration itself was complete and whether details were truncated.
- Do not walk forbidden directories to produce a fuller exclusion report. Untracked and ignored files remain outside the corpus. A clean report means no recorded gap within the allowed enumeration, not universal completeness.
- Avoid a schema migration for coverage history in this release. If callers need persistent gap history, defer that separately.
- Provide setup and doctor support for Codex and Claude Code first. Inspect the installed client configuration formats before implementing changes. Other clients get a documented stdio configuration, not an untested compatibility claim.
- Setup preserves unrelated settings, supports repeat runs, and removes only RSO-owned entries. Test in temporary client configuration directories before live connection checks.
- The agent performs installation and connection verification. Update the short skill instruction explaining when to call use/query; tool discovery alone is not a promise of automatic use.

## Schedule

Dates use America/Chicago. This is a work plan, not an automation or calendar booking.

| Date | Work | Hours | Exit condition |
| --- | --- | --- | --- |
| Thu Sep 10 | Baseline, tool/packet contracts, SDK and packaging spike | 4-6 | One packaged stdio handshake works; dependency approach and client versions recorded |
| Fri Sep 11 | MCP adapter, scope enforcement, errors, shared-ledger integration | 6-8 | Two isolated MCP clients exercise use/query/resume against one scratch ledger |
| Mon Sep 14 | Compact packets, protected excerpts, expansion and stale handling | 6-8 | Budget, conflict, expansion, and source-change fixtures pass |
| Tue Sep 15 | Bounded coverage reporting and empty-result diagnostics | 3-5 | Skipped/unreadable/limited cases produce accurate bounded reports |
| Wed Sep 16 | Setup, doctor, documentation, client config preservation | 5-7 | Repeat setup and removal pass in isolated configurations; installed client smoke checks recorded |
| Thu Sep 17 | Full regression, concurrency, release package and clean-install verification | 6-8 | Release gates below pass; private 0.8.0 artifact and verification record ready |
| Fri Sep 18 | Contingency only | Up to 6 | Resolve packaging/client defects; otherwise unused |

[Inference] Target completion is September 17, with September 18 reserved for integration defects. If SDK packaging exceeds Day 1, re-estimate immediately. Defer coverage diagnostics before cutting evidence integrity or installation verification. If implementation starts later, shift these working-day slots rather than claiming the dates still hold.

## Day 1 record (started 2026-09-09)

Status: spike landed in this checkout. Not a 0.8.0 release. AGENTS.md approved-interface wording is unchanged.

### Dependency approach

- Keep the CLI on the standard library. Ordinary commands must not import `mcp`.
- Prefer the official PyPI package `mcp` (Model Context Protocol Python SDK v2), not the standalone Prefect `fastmcp` package, and not a handwritten JSON-RPC server.
- Pin `mcp==2.2.0` in `requirements-mcp.txt`. Install only into an isolated venv at `mcp-runtime/` (or `$RSO_MCP_RUNTIME`) via `rso-context mcp --install-runtime`.
- Do not use `mcp[cli]` (typer). Do not add HTTP hosting. stdio is the only transport in this release.
- This machine's default interpreter had `mcp` 1.28.0 (`FastMCP`). That line is unsupported for the adapter. Usable means SDK 2.x with `from mcp.server import MCPServer`.

### Client versions observed on this machine (2026-09-09)

These are installed-host versions, not a compatibility certification. No live client config was edited.

| Host | Evidence | MCP shape |
| --- | --- | --- |
| Grok Build TUI | `~/.grok/version.json` 1.0.25 | `config.toml` `[mcp_servers.*]` command/args stdio |
| Codex | `~/.codex/version.json` latest_version 0.143.0 (last_checked 2026-07-08) | `config.toml` `[mcp_servers.*]` command/args and URL servers |
| Claude Code | `~/.claude/settings.json` present; `.claude.json` has `tengu_mcp_protocol_negotiation_stdio` | stdio MCP with protocol negotiation |
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` has `mcpServers` | stdio/JSON host config |

Release acceptance still needs live Codex and Claude Code use/query/expand against a disposable project (gate 6). A simulated client is not a substitute.

Packaged stdio handshake: isolated `mcp==2.2.0` Client against `rso-context mcp --db … --root …` negotiated protocol **2026-07-28**, server name `rso-context`, tools `rso_use rso_query rso_resume rso_explain rso_expand`. Hosts that only speak the classic `initialize` path should still connect; the SDK client tries `server/discover` first and falls back. Do not treat 2026-07-28 as the only version those hosts speak.

### Tool/packet contract

Launch bindings: `--db` and one or more `--root` values. Tools never take a database path. `path` must stay under a launch root. User profile / Documents / Downloads / Desktop / OneDrive roots are refused. Tools: `rso_use`, `rso_query`, `rso_resume`, `rso_explain`, `rso_expand`. `rso_use` and `rso_query` write. `rso_expand` resolves `rso-expand-ref/v1` inside launch roots.

CLI query packets stay `rso-context-packet/v2`. Compact MCP packets are `rso-mcp-packet/v1` (Day 3).

## Day 2 record (2026-09-09)

Status: adapter scope/errors and shared-ledger tests landed. Not a 0.8.0 release. No extra product database; tests use `--db` scratch files. WAL `busy_timeout=30000` was enough; no retries and no lock stealing.

- Two isolated MCP stdio clients (separate server subprocesses) share one scratch ledger: `rso_use` then concurrent `rso_query` for agents `alpha` and `beta`, then `rso_resume`. Same project id. Run budgets stay per-agent (both remaining 7 after one query each). In-process CLI `query_context` on that ledger returns `rso-context-packet/v2` with the same project and evidence status.
- Tool errors stay on the wire: path outside `--root`, empty agent, empty query, resume before register. The server process continues.
- `rso_explain` refuses a packet whose project alias is outside launch roots. use/query/resume reject the launch database file and non-directories.
- sqlite `OperationalError` and schema errors become tool errors. Existing WAL/busy-timeout reused.

Next slice is Day 3: compact packets, protected excerpts, expansion and stale handling.

## Day 3 record (2026-09-09)

Status: compact packets and expansion landed. Not a 0.8.0 release. CLI default remains `rso-context-packet/v2` (hashes unchanged). MCP `rso_query` returns `rso-mcp-packet/v1`. Opt-in CLI: `query --compact --byte-budget N`.

- Byte budget counts canonical ASCII JSON of the complete compact result, including metadata. `token_estimate` is labeled estimate (`utf8-bytes/4`); no tokenizer.
- Policy chunks that mix a rule and exception stay whole. Matching lines in non-policy text stay in consecutive groups; gaps are not spliced into one quote.
- Disagreement: both sides are kept, or the packet is `insufficient_budget` (neither side dropped).
- Omitted spans carry `rso-expand-ref/v1` (project_id, relative_path, expected_hash, line range, chunk_hash). Expand resolves through the ledger and launch roots, ignores client `resolved_path`, and returns `stale` on hash mismatch or `unavailable` if the source is gone.
- Expansion is bounded and can return a further range. Protected policy that cannot fit is `insufficient_budget`, not a truncated exception.

## Day 4 record (2026-09-10)

Status: bounded ingest coverage and empty-result diagnostics landed. Not a 0.8.0 release. No schema migration.

- `ingest` / `use` return `coverage` (`rso-ingest-coverage/v1`): enumeration method (`git_cached` or `bounded_walk`), `complete`, `truncated`, counts, and up to 20 path/reason gaps.
- Reasons recorded from the allowed enumeration only: `ignored_dir`, `unsupported_type`, `sensitive_name`, `too_large`, `symlink`, `unreadable`, `sensitive_content`, `stat_error`. Forbidden directories are pruned, not walked to list inner files. Gitignored/untracked files are outside the corpus and are not invented as gaps.
- A clean coverage report means no recorded gap in that enumeration, not universal completeness.
- Query packets with no current spans get `empty_result` after `packet_hash` (`no_matching_spans` or `only_stale_spans`). That is not a proof of absence. Matching queries omit the field. CLI v2 hashes stay stable (`empty_result` is excluded from the hash body).

## Day 5 record (2026-09-10)

Status: setup/doctor/docs landed on `feat/0.8.0-mcp`. Not a 0.8.0 release. Live host configs were inspected read-only; tests used isolated files only. No live Codex/Claude session was used as a substitute for gate 6.

Inspected formats on this machine:
- Codex: `~/.codex/config.toml` tables `[mcp_servers.<name>]` with `command` / `args` / optional `[mcp_servers.<name>.env]`
- Claude Code: `~/.claude.json` object `mcpServers.<name>` with `command` / `args` / optional `env`

`rso-context mcp --setup --client {codex,claude-code} --root <folder>` writes only `rso-context`. Repeat setup replaces that entry. `--remove` deletes only that entry. `--config` is required for isolated tests. `doctor` reports MCP runtime and read-only client presence; MCP is not required for `ready`. SKILL.md states tool discovery is not automatic use. Other hosts get documented stdio, not a compatibility claim.

## Day 6 record (2026-09-10)

Status: private 0.8.0 artifact built and isolated-install verified on Windows. Not a public release. Repository visibility unchanged. Nothing pushed.

- Version constants, builder prefix, install/release tests, README, INSTALL, MANUAL, QUICKSTART, SKILL, and DEVELOPMENT now say 0.8.0. CLI commands from 0.7.0 remain.
- Full suite: 74 tests pass in 46.230s.
- Archive: `outputs/RSOContextAlpha-0.8.0.zip`, 51 files, 126307 bytes, SHA256 `271fe06adcfe4977af751690a8d3bd6cd30aab8207d45f8471e23ae435fab7fb`. Manifest match and ZIP test passed. LICENSE, NOTICE, and `requirements-mcp.txt` included. No ledger, credentials, Graft, mcp-runtime, or coordination state.
- Isolated `--prefix` install into a path with spaces: installed command reports `0.8.0`, `doctor` `ready: true`, `use`/`query` on a scratch project returned `evidence_found`. `mcp --install-runtime` in that prefix installed `mcp==2.2.0`; `--status` `runtime_ready: true`. `--setup --client codex --config` wrote only an isolated temp file.
- Gate 6: live Codex and Claude Code use/query/expand were **not** run. Simulated clients are not a substitute. Native macOS remains unverified.
- Wrap-up re-check (same session): suite still 74 tests, 44.742s OK. Isolated prefix `use`/`query` with `--db` before the subcommand returned `evidence_found`. ZIP still 51 files, SHA256 unchanged, no ledger/credentials/Graft/mcp-runtime/coordination/`__pycache__`. `origin/main` remains `1f2dfda`. Nothing committed or pushed.

## Day 7 record (2026-09-10)

Status: contingency used for packaging/client lock-in. Not a public release. Live host configs were not written. Gate 6 remains unverified.

Triage:
- Isolated prefix MCP stdio handshake through paths with spaces (`install with spaces` runtime + `bounded project` root) negotiated protocol **2026-07-28**, listed the five contract tools, and `rso_use`/`rso_query` returned `evidence_found`. That path was not handshake-tested on Day 6.
- Global `--db` after `use`/`query` remains invalid by design; docs already require it before the command. `mcp` still accepts `--db` after the subcommand.
- Simulated clients are still not a substitute for live Codex/Claude Code use/query/expand.

Changes:
- `INSTALL.md` agent-adapter roots now include `~/.grok/skills/rso-context`.
- Setup tests assert JSON-quoted roots that contain spaces. Stdio handshake uses a workspace name with a space. Doctor tests restore both Codex and Claude config env overrides.
- Full suite: 75 tests pass in 46.684s.
- Day 6 ZIP left in place. Contingency archive: `outputs/RSOContextAlpha-0.8.0-contingency.zip`, 51 files, 126513 bytes, SHA256 `7416e615da510002ec0f9c4fec9c771ae14e4afb64029dd336713384844a18d0`. Manifest match; no ledger, credentials, Graft, mcp-runtime, or coordination state.

## Release gates

1. Run the existing regression suite plus targeted new tests on scratch databases. Cover parity between CLI and MCP, bounded paths, malformed requests, Unicode/Windows paths, tool errors, and interleaved client activity without cross-agent budget leakage.
2. Use deterministic evidence fixtures: a rule with a late exception, explicit conflicting rules, a match near the end of a chunk, duplicate snippets, an unreadable source, and a source changed after query. Measure full response bytes, latency, omitted evidence, and expansion overhead. No provider credits are needed.
3. Use the same fixture corpus and configuration for before/after comparisons. Budget compliance and evidence preservation are release gates; a headline token-reduction percentage is not. Test that schema/symbolic checks are not mislabeled semantic verification.
4. Update version constants, builder guards, release tests, manifest, notices for new dependencies, install instructions, manual, and skill together. Keep the old CLI usable. Update AGENTS.md's approved-interface wording when MCP is actually implemented, not while it is only planned.
5. Build the private release archive, verify its integrity, manifest and SHA-256, and install it into an isolated prefix. Exclude personal ledgers, credentials, local MCP configuration, and generated coordination state. Run installed CLI doctor and MCP checks using that artifact.
6. Verify discovery and use/query/expand from the actual Codex and Claude Code hosts, using a disposable bounded project. Missing client access remains an explicit release limitation; a simulated client is not a substitute for a claimed live-client test. Native macOS support remains unverified unless tested there.

Completion means implemented source, passing checks, a clean-installable private artifact, and recorded client evidence. It does not mean public publication, repository visibility changes, a scheduled autonomous run, or measured universal agent compatibility.

## Deferred work and review sources

| Reviewed project | Selected idea | Deferred or rejected for this build |
| --- | --- | --- |
| [context-mode](https://github.com/mksglu/context-mode) | Matching excerpts and bounded output | Search fusion, fuzzy correction, command sandbox, chat capture |
| [Headroom](https://github.com/headroomlabs-ai/headroom) | Explicit omitted-content recovery | Model compression, proxy, original-body cache, automatic instruction writing |
| [leanctx](https://github.com/jia-gao/leanctx) | Protected content and preservation checks | Model weights, provider-based rewriting, inferred tolerance for policy prose |
| [ponytail](https://github.com/DietrichGebert/ponytail) | Reuse existing implementation and document limits | One-line/code-size targets, added abstraction without a demonstrated need |
| [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) | MCP discoverability, bounded coverage, stronger requirements for absence claims | AST/call graph engine, Graft replacement, graph/ledger merger, committed database snapshots |
| [MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture) | Standard local tool interface | Remote transport and broad client installer matrix |

Task-specific resume, persistent coverage history, dependency impact analysis, search-ranking upgrades, and additional client installers are candidates after 0.8.0 usage shows a concrete gap. No upstream code is copied by this plan; evaluate licensing and notices if implementation later reuses code.
