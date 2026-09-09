# RSO 0.8.0 build plan

Prepared September 9, 2026. Status: planned, not implemented or released.

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
