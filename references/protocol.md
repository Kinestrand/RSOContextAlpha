# RSO Context Alpha Protocol

## Command and state resolution

Prefer an installed `rso-context` command on PATH, then its full path: `%USERPROFILE%\.local\bin\rso-context.cmd` on Windows or `~/.local/bin/rso-context` on macOS/POSIX. During checkout testing, Windows can use the optional `rso-context.ps1` and POSIX can use `sh ./rso-context`. Launchers set the package `src` folder on `PYTHONPATH` without installing Python packages. The generated Windows command launches Python directly.

Default state is `%USERPROFILE%\.rso-context` on Windows or `~/.local/share/rso-context-alpha` on macOS/POSIX. `RSO_CONTEXT_HOME` overrides it. The `--db` global argument selects a database for one invocation and must appear before the command.

## Agent session sequence

```powershell
rso-context use --agent <current-agent-name> --path <workspace>
rso-context query "<actual user task>" --agent <current-agent-name> --path <workspace>
```

Run incremental ingestion before a context-dependent task unless a watcher has already reconciled the project. Don't register a user profile, `Documents`, `Downloads`, or an entire OneDrive root as one project.

Run `audit` when requirement coverage or output traceability matters:

```powershell
rso-context audit "<request>" --answer-file <answer-path> --agent <current-agent-name> --path <workspace>
```

Run `validate` when database integrity is in question. Use `explain-project` to inspect project identity and possible matches. Use `explain <packet-hash>` to inspect a recorded packet.

## Context packet fields

- `project`: canonical project identity and scope.
- `corpus_versions`: exact versions used for project and shared branches.
- `requirements`: bounded request clauses with `evidence_found`, `unknown`, `disagreement`, or `budget_exhausted`.
- `evidence`: current source path, media type, coordinate kind, line range, text, source hash, chunk hash, rank, and matched requirement indexes.
- `graph_nodes`: nodes backed by active current source evidence.
- `claims`: policy-like statements backed by active current source evidence.
- `validations`: named validation records for returned claims.
- `config`: retrieval algorithm and budgets.
- `packet_hash`: deterministic identifier for the packet.

Text and code evidence uses `span_kind: source_lines`. DOCX evidence uses `span_kind: extracted_text_lines`; those coordinates refer to deterministic extracted text, not native Word pages or paragraph identifiers.

## Claim validation

Record a validation only when the named validator or user supplies or authorizes the decision:

```powershell
rso-context record-validation <claim-id> --validator <name> --result verified --details <json-object>
```

Allowed results are `verified`, `disputed`, and `superseded`. The command records the decision, updates the claim state, advances the corpus version, and invalidates cached packets. Never infer validation from repeated retrieval or agreement among agents.

Query packets also carry `run` (remaining/initial integer after the query) and `checks` (symbolic solvers: schema holds, span exists, and hash matches when there is conflict or a failed check). A passing check is not a named validation. Agents may `propose` a claim; it stays `proposed` until a named validator records verified, disputed, or superseded. `pending` lists proposed claims and observed claims that still have no validation rows. `run-budget` stores the remaining integer per project and agent.

## Trust states

- `observed`: the exact current source contains the record.
- `proposed`: an agent or extractor suggested the record.
- `verified`: a named validator or authority accepted it.
- `disputed`: a named validation records a conflict.
- `superseded`: a named validation records a replacement.

Historical source versions and their evidence remain stored, but queries return only records backed by active current versions.

Chunks persist as pointers: empty `text`, plus heading, line range, and hashes. FTS still stores searchable text at ingest. Query reconstructs plate text from the live file when sha256 matches `sources.current_hash`. Hash mismatch or a missing file marks evidence `stale` and does not return the current file's lines as the plate. Legacy stored text may remain for diagnostic fallback. Stale spans are excluded from current claims, graph nodes, requirement coverage, and successful symbolic checks; re-ingest before relying on changed sources.

After upgrading to schema 5, run `rso-context compact-pointers` once. Migrate does not wipe bodies. `rso-context admin [--port 7432]` is a read-only HTML viewer bound to 127.0.0.1.

Same-path content changes use existing versioning. A new path with exactly one project source sharing that `current_hash` updates the source path (`files_renamed`). Multiple hash matches insert a new source.

## Project identity rules

Directory identity uses the resolved folder path, preserving existing directory aliases for legacy records. Git identity includes the working-tree path as well as available remote/root-commit or common-directory information. Separate worktrees remain separate. Structural similarities are possible matches only; they never automatically merge independent folders. Names are display metadata only. Use explain-project and check the resolved path before relying on identity.

## Source safety

The ingester excludes hidden tool folders, dependency and build directories, symlinks, detected credential filenames, private-key suffixes, private-key blocks, and common credential assignments. On a git work tree, ingest reads only tracked files from `git ls-files --cached` (not untracked or gitignored). Directories named `private`, `backup`, `backups`, `output`, `outputs`, `renders`, `tmp`, `temp`, and `graft` are never indexed, even when git tracks them. If Git is missing or file enumeration fails, ingestion stops without walking untracked files. Only a positively identified non-repository can fall back to a bounded file walk. These checks reduce exposure but cannot identify every secret stored under an arbitrary name. Keep registered roots bounded and treat the database like the indexed source material. Do not register a user profile.

`validate` applies the same detectors to current indexed paths and chunks and reports any violation.

Query packets include `search_order`: the active project, then parent domain projects whose registered path contains the active folder, then shared projects. A path filter applies only to the active project, so domain and shared evidence is not hidden. Cross-project retrieval uses that order. Do not add a concept registry or store concept bodies in sqlite. A span from another project is observed until a named validation. Override by copying the plate into the project. Resume packets include `takes` counts of current sources and older stored versions without dumping old text. `watch --path` polls one already-registered folder; `--once` runs a single poll. Do not use watch to scan a user profile.

## Boundaries

The alpha does not use provider APIs, embeddings, screen capture, keystrokes, browser histories, or private-message monitoring. Its observer reads supported files inside registered project folders and Git metadata available through the local `git` executable.

## Portable clients

Use Python 3.11+, Git, and SQLite FTS5. See INSTALL.md for Windows/macOS layouts and installation. macOS execution has not been verified on a Mac for this release. CLI JSON uses ASCII escapes for Unicode transport; JSON parsing restores original characters. RSO exposes a local CLI, not a bundled harness or provider service.
