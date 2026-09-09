# RSO development and builds

The source repository is https://github.com/paulrus/rso-context. RSO is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Repository visibility is managed separately from licensing.

RSO is a standalone Python project. Kinestrand and other projects may use its CLI, but this repository does not import their code, their evidence ledgers, or their coordination services. The runtime uses Python's standard library and Git.

Graft is an optional third-party development skill. It is not part of RSO, is not distributed here, and is not needed to build, test, install, or run RSO. References to it describe the boundary between the two tools.

On September 5, 2026, Paul Griswold reported that RSO has been tested with Graft in place and that they work excellently together. This records the owner's evaluation, not an automated compatibility certification or a claim that Graft is included in RSO.

## Work from a checkout

On the original Windows machine, `%USERPROFILE%\.local\RSOContextAlpha` is both the canonical checkout and the installed program. Edit and commit RSO here. Changes to runtime source in this folder affect the installed CLI immediately.

On another machine, clone the repository (GitHub authorization is required while it is private):

```text
git clone https://github.com/paulrus/rso-context.git
cd rso-context
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
python -B build_release.py --output outputs/RSOContextAlpha-0.7.0.zip
```

The builder uses only `RELEASE-FILES.txt`, verifies archive integrity, and prints a SHA-256 hash. It refuses to replace an existing ZIP. Choose a new output path when preserving an earlier build. Install an extracted package with `python -B install.py`; see [INSTALL.md](INSTALL.md) for the platform paths and isolated installation checks.

The current version is 0.7.0. For a new version, update `src/rso_context/__init__.py`, the version guard and archive prefix in `build_release.py`, related tests, and versioned documentation together before building.

## GitHub checks

Pushes and pull requests to `main` run the regression suite on Windows with Python 3.11 and 3.12. The Python 3.11 job also builds a ZIP and saves it as a workflow artifact for seven days. Artifact access follows GitHub repository permissions. The workflow can be started manually. It does not create a public release or publish a package.

Native macOS execution remains unverified. Supplying a POSIX launcher does not establish macOS test coverage.

## Repository boundaries

Track source, tests, project instructions, documentation, and build configuration. `.gitignore` excludes personal SQLite files, credentials, generated archives, bytecode, Graft output, old repair notes, and machine-local coordination/MCP state. Package contents remain controlled by the separate release allowlist. Both LICENSE and NOTICE are required in release archives and installations; preserve applicable third-party notices if dependencies are added.

The initial Git commit imports the current standalone source snapshot. Historical repair copies remain local and are not Git history. Future builds should come from this repository and record the commit being built.
