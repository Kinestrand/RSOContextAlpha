# RSO verification status

## 0.8.1, September 18

Release checks for 0.8.1 on Windows 11 with Python 3.12.5: 114 regression
tests passed with one POSIX-only test skipped, including the new Windows
launcher test for `RSO_MCP_RUNTIME`. The release builder produced a 57-file
archive that passed its manifest and archive checks; the SHA-256 is published
with the release. GitHub Actions runs the suite on Windows, Ubuntu, and macOS
with Python 3.11 and 3.12 for the release commit; the release page links that
run.

The disagreement and budget changes were compared on this repository's own
documentation before merging. Two queries that previously reported a false
disagreement and returned no evidence or expand refs now return evidence and
refs; a query with no conflict was unchanged. No live Gemini CLI or
Antigravity MCP session has been recorded, so those two setup targets are
checked only by isolated configuration tests.

## 0.8.0 records


Updated September 12, 2026 (UTC). This record separates observed checks from untested environments. It contains no local project paths, source excerpts, account configuration, or ledger exports.

## Linux platform checks, September 12

GitHub Actions [run 34718651864](https://github.com/Kinestrand/RSOContextAlpha/actions/runs/34718651864)
passed all six jobs for commit `feeaa68`: Windows, Ubuntu, and macOS on
Python 3.11 and 3.12. Each job installed the pinned MCP runtime and ran the
regression suite; each Python 3.11 job also built and uploaded a ZIP artifact.
This tests the current-branch changes, not a replacement published release.

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

## Live Claude Code on Windows

On 2026-09-12, an interactive Claude Code session called the installed local MCP server's `rso_use`, `rso_query`, `rso_resume`, `rso_explain`, and `rso_expand` tools against the RSOContextAlpha workspace itself, using launch roots configured in the host's own `.claude.json`.

`rso_use` ingested the bounded folder (63 active sources, no changes) and returned a resume packet. `rso_query` asked what the optional MCP adapter's runtime version must be and what happens if it isn't installed; it returned a compact `rso-mcp-packet/v1` with evidence from INSTALL.md, QUICKSTART.md, DEVELOPMENT.md, README.md, and MANUAL.md. One of the query's two split requirements returned `status: disagreement`, exercising that path live rather than through a fixture. `rso_resume` returned the same project's current claims and run history. `rso_explain` retrieved the saved packet by its hash. `rso_expand` recovered the one span the byte budget had omitted (MANUAL.md's troubleshooting section), returning `status: ok`, `stale: false`, and a matching source hash.

This establishes live Claude Code MCP acceptance on Windows against a real bounded workspace. It does not establish native macOS execution or a from-scratch disposable-project session for this host.

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

- Live Claude Code MCP use/query/resume/explain/expand on Windows is recorded above. Native macOS live-agent acceptance remains unverified; the automated macOS runner coverage recorded above does not establish that.
- Linux CLI + MCP runtime on Debian is recorded above; live agent MCP sessions on Linux are still separate.
- SDK-driven scratch tests are separate from live host checks.
- Secret filtering is a precaution, not proof that arbitrary confidential text can be safely published. The ledger contains searchable source text and metadata even when chunk bodies are pointers.
- Repository publication and package creation do not include an index or synchronize users' project files. Source releases use the explicit allowlist in `RELEASE-FILES.txt` and retain LICENSE and NOTICE.

See [DEVELOPMENT.md](DEVELOPMENT.md) for repeatable regression and package checks. Historical test counts and archive hashes in the build plan refer to their dated artifacts, not later builds.
