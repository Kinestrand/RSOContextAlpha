# RSO development and builds

The source repository is https://github.com/Kinestrand/RSOContextAlpha. RSO is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Repository visibility is managed separately from licensing.

RSO is a standalone Python project. Kinestrand and other projects may use its CLI, but this repository does not import their code, their evidence ledgers, or their coordination services. The CLI runtime uses Python's standard library and Git. The optional
`rso-context mcp` adapter pins the official MCP Python SDK (`mcp==2.2.0`) in an
isolated venv (`mcp-runtime/` or `$RSO_MCP_RUNTIME`). That SDK is not imported
by ordinary CLI commands. Create it with `rso-context mcp --install-runtime`.
`runtime_ready` and post-install checks must report exactly `2.2.0`, not any
other 2.x release. Do not install the standalone Prefect `fastmcp` package, and
do not add the SDK to the default CLI interpreter.

Graft is an optional third-party development skill. It is not part of RSO, is not distributed here, and is not needed to build, test, install, or run RSO. References to it describe the boundary between the two tools.

On September 5, 2026, Paul Griswold reported that RSO has been tested with Graft in place and that they work excellently together. This records the owner's evaluation, not an automated compatibility certification or a claim that Graft is included in RSO.

## Work from a checkout

On the original Windows machine, `%USERPROFILE%\.local\RSOContextAlpha` is both the canonical checkout and the installed program. Edit and commit RSO here. Changes to runtime source in this folder affect the installed CLI immediately.

On another machine, clone the repository (GitHub authorization is required while it is private):

```text
git clone https://github.com/Kinestrand/RSOContextAlpha.git
cd RSOContextAlpha
```

Install Python 3.11 or newer and Git. From PowerShell in the checkout, run:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
python -B -m unittest discover -s tests -v
```

From a POSIX shell:

```sh
PYTHONPATH=src python3 -B -m unittest discover -s tests -v
```

Tests use temporary folders and isolated databases. Manual experiments must also use a scratch database via `--db <scratch-path>` or `RSO_CONTEXT_HOME`; never use the personal ledger as test data. Do not commit either database.

## Build a package

Run from the checkout:

```text
python -B build_release.py --output outputs/RSOContextAlpha-0.9.0.zip
```

The builder uses only `RELEASE-FILES.txt`, verifies archive integrity, and prints a SHA-256 hash. It refuses to replace an existing ZIP. Choose a new output path when preserving an earlier build. Install an extracted package with `python -B install.py`; see [INSTALL.md](INSTALL.md) for the platform paths and isolated installation checks.

The current version is 0.9.0. For a new version, update `src/rso_context/__init__.py`, the version guard and archive prefix in `build_release.py`, related tests, and versioned documentation together before building.

## GitHub checks

The workflow runs the regression suite on Windows, Ubuntu, and macOS with Python 3.11 and 3.12. Each Python 3.11 job also builds a ZIP and saves it as a separate workflow artifact for seven days. The isolated MCP SDK installation must succeed before tests run. Artifact access follows GitHub repository permissions. The workflow can be started manually. It does not create a public release or publish a package.

Windows, Ubuntu, and macOS automated checks passed on Python 3.11 and 3.12 for commit feeaa68. See VERIFICATION.md for the Actions run and remaining live-host limits.

## Publish a versioned release

Pushing source, building a ZIP, and publishing a GitHub Release are separate
steps. An Actions artifact expires and does not create a release. A release is
complete only after the version tag, release page, downloadable package, and
checksum are visible to an authorized repository reader.

1. Confirm the intended source commit and clean owned paths. Update the runtime
   version, README, changelog, installer examples, build guard/prefix, tests,
   workflow artifact name, and release allowlist together where applicable.
2. Run the regression suite and build a new archive. Check its exact allowlist,
   internal links, LICENSE/NOTICE, and installation with a temporary prefix and
   a scratch database. Record the package SHA-256 and source commit.
3. Push the reviewed commit to the default branch without force. Check that
   GitHub's branch README and version match and that required CI checks pass.
4. Create the version tag at that exact commit. Create a GitHub Release targeting
   the tag; attach the packaged ZIP and its checksum file. Copy the version's
   changes and verification limits into the release notes. Do not treat alpha
   software as proven across untested hosts.
5. Read the release back from GitHub. Verify its target commit, asset names and
   sizes, checksum, version, and repository visibility. Follow the README's
   download link and confirm it resolves to this release. Report the actual
   release URL, not only a branch or transient Actions artifact.

Release publication must preserve repository visibility. Do not overwrite an
existing version tag or replace a published package silently. If a published
version needs a code correction, prepare a new version with its own evidence.

The release package is built only from [RELEASE-FILES.txt](RELEASE-FILES.txt).
Developer-only benchmarks can live in the source repository without being
installed. Raw host transcripts and benchmark ledgers stay in ignored outputs;
publish only sanitized, accurately scoped verification summaries.

## Repository boundaries

Before publishing, review the staged paths and the commit range being pushed. Keep personal indexes (including SQLite WAL/SHM sidecars), local MCP configurations, credentials, source-project data, and backup archives outside Git. `.gitignore` does not remove already tracked files. Use an explicit file list when staging, and verify the release archive against `RELEASE-FILES.txt`; retain LICENSE and NOTICE. Publishing reviewed code does not authorize changing repository visibility.

Keep [VERIFICATION.md](VERIFICATION.md) current when recording new live-host results. Use generic workspace descriptions; never attach a personal ledger, local configuration, or private project evidence as a test report. [RESEARCH-ORIGIN.md](RESEARCH-ORIGIN.md) records the name and proposed research contribution separately from implemented behavior.

Track source, tests, project instructions, documentation, and build configuration. `.gitignore` excludes personal SQLite files, credentials, generated archives, bytecode, Graft output, old repair notes, and machine-local coordination/MCP state. Package contents remain controlled by the separate release allowlist. Both LICENSE and NOTICE are required in release archives and installations; preserve applicable third-party notices if dependencies are added.

The initial Git commit imports the current standalone source snapshot. Historical repair copies remain local and are not Git history. Future builds should come from this repository and record the commit being built.
