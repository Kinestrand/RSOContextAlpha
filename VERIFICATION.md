# RSO 0.8.0 verification status

Updated September 12, 2026 (UTC). This record separates observed checks from untested environments. It contains no local project paths, source excerpts, account configuration, or ledger exports.

## Linux platform checks, September 12

Codex's follow-up review ran the expanded 106-test suite successfully on
Windows (one POSIX-only test skipped) and Ubuntu 24.04 under WSL (three
Windows-only tests skipped). Ubuntu used a separate temporary MCP runtime.
The Git Bash launcher regression passed on Windows. A fresh release build
passed its manifest and archive checks with 57 files. The earlier checks
below describe the suite before the Git Bash regression was added.

Ubuntu 24.04 under WSL, Python 3.12.3, passed 105 regression tests with two
Windows-only tests skipped. The optional MCP runtime reported SDK 2.2.0.
Checks included actual stdio handshakes, CLI/MCP shared-ledger behavior, bounded
retrieval, installation, and case-sensitive root/source separation. A release
ZIP was extracted and installed into a temporary Linux prefix containing spaces;
doctor returned ready and scratch-ledger use/query returned the synthetic evidence.

The platform changes preserve case-distinct POSIX roots and source filters,
and reject installation into a virtual environment created by another OS.
The first cross-OS test attempt touched the Windows runtime configuration;
it was restored to its Python 3.12 runtime and all 14 MCP adapter tests then
passed on Windows. A fresh process using the configured Windows command also
completed tool discovery. Existing host processes need reconnecting after
configuration changes.

After runtime restoration, the complete Windows suite passed 105 tests with
one POSIX-only case-sensitivity check skipped (CLI Python 3.11.11, MCP Python
3.12.11). No personal ledger was used as test data.

The workflow now includes Windows, Ubuntu, and macOS on Python 3.11 and 3.12,
with MCP installation required before tests. Results from the published
workflow are available in GitHub Actions. The separate Debian check is recorded
below. These checks don't establish live Claude Code host acceptance or
replace the published 0.8.0 release archive.

## Live Codex on Windows

The active Codex host called the installed local MCP server's `rso_use`, `rso_resume`, `rso_query`, `rso_explain`, and `rso_expand` tools successfully.

An allowed but unregistered bounded folder first returned the explicit unregistered-project error. Calling `rso_use` registered and ingested it. Subsequent queries returned current evidence. After source updates and another ingest, two separately registered project folders returned the corresponding current evidence, and expansions returned `status: ok` with `stale: false` and matching source hashes. Their project identities remained separate.

These were live checks against existing local workspaces. They establish this installed Codex workflow, not universal host compatibility or completion of the planned disposable-project acceptance checks for every client.

## Regression and packaging

The September 10 documentation refresh passed all 78 regression tests on Windows. The suite includes scratch-ledger CLI/MCP checks, response budgets, project isolation, current-source handling, configuration preservation, installation, exact archive contents, documentation links, and identical protocol copies. GitHub's build workflow now labels its artifact as 0.8.0, matching the package version.

The September 11 release-preparation run also passed all 78 tests. The expanded
documentation passed an additional local Markdown-anchor check. The packaged
ZIP was extracted and installed into a temporary prefix; the installed command
reported 0.8.0, `doctor` reported ready, and scratch `use`/`query` returned current
synthetic-source evidence. Publication checks and the package checksum are
recorded on the versioned GitHub release page.

## Kinestrand repository integration

The September 11 integration into `Kinestrand/RSOContextAlpha` retained both
repository histories and the original Kinestrand test suite. All 103 combined
regression tests passed locally on Windows (the 78 focused tests plus 25 original
tests). The package manifest includes both suites. The versioned release and
documentation links target the Kinestrand repository.

## Three-host CLI trial

Codex, Claude Code, and Antigravity returned saved CLI results from the same
disposable project and scratch ledger. Phase 1 returned the initial settings
with observed status and matching packet hashes. Phase 2 returned the changed
authoritative settings despite a retained historical handoff. The three agents'
answers and source citations were checked against the synthetic fixture.

These are two completed phases of an ongoing five-phase host trial. They do not
establish MCP compatibility in Claude Code or Antigravity, concurrent execution
of every request, or completion of the RSO-on/off comparison. Codex authored the
fixture and was not blinded. No performance advantage is claimed.


## Live Linux install

On 2026-09-12 a disposable Debian Linux host installed 0.8.0 with
`python3 install.py --prefix ~/.local`, then verified:

- `rso-context --version` reported `0.8.0`
- `doctor` reported `ready: true` with FTS5 and SQLite quick-check ok
- `PYTHONPATH=src python3 -B -m unittest discover -s tests -v` — **103** tests OK (2 skipped: Windows-only)
- `rso-context mcp --install-runtime` produced an isolated Linux venv with `runtime_mcp_version` `2.2.0` and `runtime_ready: true`
- Scratch bounded project: `register` / `ingest` / `query` returned `rso-context-packet/v2` with an observed claim from a synthetic plate

This establishes CLI + MCP runtime readiness on Debian Linux. It does not claim
live Codex/Claude MCP sessions on Linux, nor native macOS execution.

## Remaining verification limits

- Live Claude Code MCP use/query/expand and native macOS execution remain unverified.
- Linux CLI + MCP runtime on Debian is recorded above; live agent MCP sessions on Linux are still separate.
- SDK-driven scratch tests are separate from live host checks.
- Secret filtering is a precaution, not proof that arbitrary confidential text can be safely published. The ledger contains searchable source text and metadata even when chunk bodies are pointers.
- Repository publication and package creation do not include an index or synchronize users' project files. Source releases use the explicit allowlist in `RELEASE-FILES.txt` and retain LICENSE and NOTICE.

See [DEVELOPMENT.md](DEVELOPMENT.md) for repeatable regression and package checks. Historical test counts and archive hashes in the build plan refer to their dated artifacts, not later builds.
