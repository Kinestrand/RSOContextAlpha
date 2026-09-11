# RSO 0.8.0 verification status

Updated September 10, 2026. This record separates observed checks from untested environments. It contains no local project paths, source excerpts, account configuration, or ledger exports.

## Live Codex on Windows

The active Codex host called the installed local MCP server's `rso_use`, `rso_resume`, `rso_query`, `rso_explain`, and `rso_expand` tools successfully.

An allowed but unregistered bounded folder first returned the explicit unregistered-project error. Calling `rso_use` registered and ingested it. Subsequent queries returned current evidence. After source updates and another ingest, two separately registered project folders returned the corresponding current evidence, and expansions returned `status: ok` with `stale: false` and matching source hashes. Their project identities remained separate.

These were live checks against existing local workspaces. They establish this installed Codex workflow, not universal host compatibility or completion of the planned disposable-project acceptance checks for every client.

## Regression and packaging

The September 10 documentation refresh passed all 78 regression tests on Windows. The suite includes scratch-ledger CLI/MCP checks, response budgets, project isolation, current-source handling, configuration preservation, installation, exact archive contents, documentation links, and identical protocol copies. GitHub's build workflow now labels its artifact as 0.8.0, matching the package version.

## Remaining verification limits

- Live Claude Code use/query/expand and native macOS execution remain unverified.
- SDK-driven scratch tests are separate from live host checks.
- Secret filtering is a precaution, not proof that arbitrary confidential text can be safely published. The ledger contains searchable source text and metadata even when chunk bodies are pointers.
- Repository publication and package creation do not include an index or synchronize users' project files. Source releases use the explicit allowlist in `RELEASE-FILES.txt` and retain LICENSE and NOTICE.

See [DEVELOPMENT.md](DEVELOPMENT.md) for repeatable regression and package checks. Historical test counts and archive hashes in the build plan refer to their dated artifacts, not later builds.
