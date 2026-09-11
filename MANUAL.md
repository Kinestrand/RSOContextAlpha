# RSO Context Alpha manual

RSO means Recursive Semantic Octree. [RESEARCH-ORIGIN.md](RESEARCH-ORIGIN.md) explains the research proposal and the smaller evidence-ledger implementation supplied here.

This manual describes the 0.8.0 CLI. Commands return JSON unless they're help/version output or the local browser viewer. Replace `<current-agent-name>` with the actual client identity and `<workspace>` with an absolute, bounded project directory. Installation is covered in [INSTALL.md](INSTALL.md).

## 1. Give the project something to remember

RSO reads files, not conversations. Put durable requirements and decisions in a project-owned file such as `AGENTS.md` or `PROJECT-TRUTH.md`. An existing Git repository is also a project marker. For a new non-Git folder, add an `AGENTS.md` describing that project's intent and constraints.

```markdown
# Example project
This project must produce a 24 fps preview.
Decision: keep original media unchanged.
AUTHORITATIVE: this file, then README.md.
UNKNOWN: the final delivery codec is awaiting approval.
```

The optional `project-truth.html` interview runs in a browser and downloads a project truth document. Save that document inside the project. Don't overwrite an existing authority file. The interview doesn't ingest anything by itself.

In Git projects, only tracked files are candidates. A new decision file must be tracked before ingestion sees it. Don't track secrets for the sake of indexing. In non-Git projects, supported files are discovered within the bounded root, subject to exclusions and size limits.

## 2. Start and resume work

```text
rso-context use --agent <current-agent-name> --path <workspace>
rso-context query "What must the preview preserve?" --agent <current-agent-name> --path <workspace>
```

`use` is the normal entry point. It registers the project, performs incremental ingestion, and returns a resume packet. The equivalent explicit sequence is:

```text
rso-context ingest --agent <current-agent-name> --path <workspace>
rso-context resume --agent <current-agent-name> --path <workspace>
rso-context query "What must the preview preserve?" --agent <current-agent-name> --path <workspace>
```

Check that the returned project matches the intended folder. `resume` alone doesn't refresh files. After changing project files, run `use` or `ingest` again. Unchanged files are skipped; edited files receive new chunks and a source version. Queries don't automatically ingest changes.

```text
rso-context explain-project --path <workspace>
rso-context stats
```

Directory identity is anchored to its resolved path. Similar-looking folders remain separate possible matches. Git identity incorporates repository information and the working tree; copies and worktrees shouldn't be assumed to share identity. Moving a folder may require checking identity again. The CLI has no general automatic merge command for reconciling project histories.

## 3. Read an evidence packet

| Field | Meaning |
| --- | --- |
| `project` | Project identity used for this request |
| `search_order` | Active project, applicable parent domains, then shared projects |
| `corpus_versions` | Indexed versions considered |
| `requirements` | Bounded request clauses and their retrieval status |
| `evidence` | Source path, line range, hashes, reconstructed text, rank, and stale flag |
| `claims` / `validations` | Source-backed statements and any named decisions |
| `partition` | Deterministic request split and stop reasons |
| `checks` | Symbolic checks, not approval |
| `run` | Stored query budget for this agent and project |
| `packet_hash` | Identifier usable with `explain` |

`evidence_found` means the retrieval found backing material. It doesn't certify that the material answers the question correctly. `unknown` means usable backing wasn't found. `budget_exhausted` means the bounded search stopped. `disagreement` and `clarification_needed` preserve detected conflicting evidence; the agent should seek the needed decision instead of selecting a winner by rank.

A stale span is a diagnostic pointer. It cannot support current claims, coverage, or successful checks. Re-ingest, then query again. DOCX line references point into deterministic extracted text, not Word page numbers. Historical records aren't a complete backup: current pointer text is reconstructed from live files, and FTS/claim metadata remains in the index.

```text
rso-context explain <packet-hash>
rso-context query "What is the preview frame rate?" --agent <current-agent-name> --path <workspace> --no-cache
```

`--no-cache` bypasses packet reuse; it doesn't refresh the corpus. JSON may contain escaped Unicode such as `\u00e9`. Parse the JSON to recover the original text.

## 4. Propose and validate

```text
rso-context propose "Use a 24 fps preview" --agent <current-agent-name> --path <workspace>
rso-context pending --path <workspace>
```

A proposal doesn't acquire source authority. Record a validation only after the named person or an explicitly authorized validator supplies the decision:

```text
rso-context record-validation <claim-id> --validator <validator-name> --result verified
```

Allowed results are `verified`, `disputed`, and `superseded`. Optional `--details-file <file.json>` attaches a JSON object describing the decision or check. For example, a file can contain `{"reason":"Owner approved the preview frame rate"}`. The ledger records the supplied name; it isn't an authentication system for the validator. Agent consensus and a passing retrieval check aren't permission to promote a claim.

## 5. Budgets, audits, and coordination cards

```text
rso-context run-budget --agent <current-agent-name> --path <workspace>
rso-context run-budget --agent <current-agent-name> --path <workspace> --set 8
rso-context audit "Preserve originals and export a 24 fps preview" --answer-file <answer.txt> --agent <current-agent-name> --path <workspace>
rso-context inbox --path <workspace>
```

The default run budget is 8. Set a fresh budget deliberately for a new bounded run; increasing it doesn't improve evidence quality. Query options include `--limit` (default 8) and `--token-budget` (default 4000). An audit maps request and answer coverage to evidence using lexical checks. It isn't a proof checker for arbitrary prose. `inbox` lists open `RSO-CARD/v1` coordination proposals; ordinary projects don't need coordination cards to use retrieval.

## 6. Multiple projects and agents

Each agent should use its own `--agent` name. Agents on the same machine can use the same local index and project files. A second person's installation has its own index; the ZIP doesn't synchronize project data between computers.

```text
rso-context register --agent <current-agent-name> --path <shared-rules-folder> --scope shared
rso-context ingest --agent <current-agent-name> --path <shared-rules-folder>
```

`--scope` accepts `project`, `domain`, or `shared`. A domain is an intentionally registered parent project. Avoid registering broad personal roots to manufacture a domain. Shared evidence retains its original source and trust state. A project-specific rule can be written in that project's own file; RSO doesn't silently promote a shared statement to verified policy.

## 7. Keep the index current

```text
rso-context watch --path <workspace> --agent <current-agent-name> --once
rso-context watch --path <workspace> --agent <current-agent-name> --interval 10
```

The folder must already be registered. Watch polls; it isn't an operating-system event watcher or an installed background service. Stop it with Ctrl+C. `--path` keeps polling limited to that project. For ordinary work, `use` before the task is enough.

The defaults admit at most 20,000 files per project, up to 2 MiB per file, and chunks of roughly 1,600 characters. Supported inputs include Markdown, plain text, common source/config formats, and DOCX. PDFs, images, audio, video, and old `.doc` files aren't parsed. See `src/rso_context/config.py` for the exact extension and exclusion lists.

Git enumeration errors stop ingestion. The tool doesn't fall back to reading untracked files when Git is missing or broken. Confirmed non-Git folders can be walked. Exclusions include private/output/backup/temp/Graft directories, hidden tool and dependency folders, symlinks, sensitive filenames, and common credential patterns. These detectors aren't a guarantee that arbitrary secrets will be recognized. Keep confidential material outside the registered source set.

## 8. State and maintenance

Windows defaults to `%USERPROFILE%\.rso-context\context.sqlite3`. macOS and other POSIX systems default to `~/.local/share/rso-context-alpha/context.sqlite3`. `RSO_CONTEXT_HOME` overrides the state directory. The global `--db` option selects a database file and goes before the command:

```text
rso-context --db <scratch-directory>/context.sqlite3 doctor
rso-context --db <scratch-directory>/context.sqlite3 use --agent <current-agent-name> --path <workspace>
rso-context --db <scratch-directory>/context.sqlite3 query "What must the preview preserve?" --agent <current-agent-name> --path <workspace>
```

Use an explicit scratch database for experiments. Keep the same database selection across related commands. Never edit SQLite tables directly. The index contains searchable source text and metadata even though chunk bodies use pointers; protect it like its source files. For a filesystem backup, first stop agents, watchers, and viewers using that index, then preserve the whole state directory together with the project sources.

```text
rso-context doctor
rso-context validate
rso-context compact-pointers
rso-context admin --port 7432
```

`doctor` checks Python, Git, SQLite, FTS5, and database quick-check status. It also reports optional MCP runtime status and read-only Codex/Claude Code config presence; missing MCP does not fail `ready`. `validate` checks ledger/evidence invariants. After upgrading an existing schema 4 database to schema 5, run `compact-pointers` once; only reconstructable legacy chunk bodies are cleared. It isn't a secure data erasure operation. `admin` serves a read-only viewer at `http://127.0.0.1:7432`; stop it with Ctrl+C.

Optional MCP:

```text
rso-context mcp --install-runtime
rso-context mcp --setup --client codex --root <workspace>
rso-context mcp --setup --client claude-code --root <workspace>
rso-context mcp --remove --client claude-code
```

`--setup` / `--remove` touch only the `rso-context` host entry. Pass `--config <file>` for isolated files. MCP `rso_query` returns `rso-mcp-packet/v1`; CLI `query` stays `rso-context-packet/v2` unless `--compact` is used. `doctor` and `mcp --status` treat the isolated runtime as ready only when `runtime_mcp_version` is exactly `2.2.0`.

## 9. Troubleshooting

An MCP launch allowlist controls access; it does not populate the ledger. Use `rso_use` separately for each permitted project before `rso_query`. Refresh ingestion after edits. Updating an index does not update or synchronize source files between project copies. See [VERIFICATION.md](VERIFICATION.md) for tested host behavior.

| Symptom | Action |
| --- | --- |
| Command isn't found | Use the installed command's full path from INSTALL.md, then add its `bin` folder to PATH |
| MCP says no registered project | Call `rso_use` with that permitted bounded path and the current agent name, then retry the query |
| MCP refuses a path outside its roots | Check the exact project path and configured bounded roots; registration cannot override launch permissions |
| Python launcher is missing or too old | Install Python 3.11+ and rerun the installer with that interpreter |
| `doctor` isn't ready | Read its JSON; check Git on PATH, FTS5 availability, and database permissions |
| File doesn't appear | Check Git tracking, extension, size, exclusions, and the intended project path; run `ingest` |
| Stale evidence | Restore the intended file or ingest its changed/deleted state, then query again |
| Wrong project | Use `explain-project` and inspect the resolved path before continuing |
| Empty query | Try the exact vocabulary in the source; inspect requirements and run budget |
| Database is locked | Let the current writer finish, then retry; don't delete the live database |
| Escaped non-ASCII output | Parse the JSON instead of treating transport escapes as source text |

For precise options, run `rso-context <command> --help`. Run shipped tests from the extracted release root with the source package on `PYTHONPATH`:

```powershell
$env:PYTHONPATH = Join-Path $PWD 'src'
python -B -m unittest discover -s tests -v
```

```sh
PYTHONPATH=src python3 -B -m unittest discover -s tests -v
```

Windows-only tests skip on other systems; passing a suite on Windows doesn't establish macOS runtime compatibility.
