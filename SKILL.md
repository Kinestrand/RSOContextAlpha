---
name: rso-context
description: >-
  Use this when work depends on existing local project files, prior decisions,
  constraints, or cross-agent history and you need source-backed evidence from
  RSO Context. Also use when the user says to install or use RSO. Do not use
  for self-contained questions that need no local context.
---

# RSO Context

Use the external RSO Context service as a shared evidence layer. The service owns
persistence and project identity; the current agent is only a client.

If the user says to install or use RSO, the **agent** performs install and
bring-up. Do not ask the user to run PowerShell, PATH edits, or ingest commands.

## Resolve the CLI

Current program version is **0.8.0**. Check `rso-context --version`. Install the matching release using `INSTALL.md` if absent or older. Python 3.11+, Git, and SQLite FTS5 are required. The installer is `python install.py` on Windows or `python3 install.py` on macOS. It copies an explicit manifest and prints the command path; it does not change PATH or register client skills. macOS runtime execution is untested for this release.

Resolve the CLI on PATH first. Default fallback locations:

- Windows: `%USERPROFILE%\.local\bin\rso-context.cmd` or `%USERPROFILE%\.local\RSOContextAlpha\rso-context.ps1`.
- macOS/POSIX: `~/.local/bin/rso-context`, with program in `~/.local/share/RSOContextAlpha`.
- During source testing: `python -m rso_context` with `PYTHONPATH` pointing at the package `src` folder.

Run `doctor` after installation and confirm `ready: true`. If installation is unavailable, report that fact without inventing retrieval results.

Default state: `%USERPROFILE%\.rso-context` on Windows and `~/.local/share/rso-context-alpha` on POSIX. `RSO_CONTEXT_HOME` overrides the directory. Global `--db <file>` selects an isolated database and must precede the command. Never edit `context.sqlite3` directly.

Use the **current agent name** for `--agent`. Do not hardcode `--agent codex`.

## RSO vs Graft

RSO is the evidence ledger: cross-project history, identity, conflicts, and named validation.
Graft is a separate code-structure engine (symbols, callers, maps). Do not merge the engines or databases.
Directories named `graft` are ignored on ingest so derived Graft cards are not indexed as sources.
On a git work tree, ingest indexes tracked files only (`git ls-files --cached`). Untracked and gitignored files are not indexed. Directories named `private`, `backup`, `backups`, `output`, `outputs`, `renders`, `tmp`, or `temp` are never indexed, even if git tracks them.
A Graft summary an agent repeats is still an `observed` claim. It is never `verified` unless a named validation or authoritative source backs it.

Router: code-location / call-chain / impact → Graft. Policy / requirements / history / conflicts / validation / cross-project → RSO. Mixed → both, source-labeled.

## Bring-up sequence

```text
rso-context use --agent <current-agent-name> --path <bounded-workspace>
rso-context query "<actual user task>" --agent <current-agent-name> --path <bounded-workspace>
```

1. `use` registers the bounded workspace, incrementally ingests, and returns a resume packet. Do not ask the user to transcribe project metadata or run ingest.
   Do **not** skip ingest out of fear of `private/` or secrets: on 0.7.0+ git projects, ingest is tracked files only and never indexes `private/`, backups, or outputs. After you change project files, still ingest (or `watch --path --once`) so the ledger matches the plates.
2. `query` uses the user's actual task. Read requirements, evidence, partition, clarification_needed, graph_nodes, claims, validations, corpus_versions, and packet_hash.
3. Treat `observed` claims as exact source statements. Treat them as verified facts only when the packet has a matching named validation record or the source itself is the named authority.
4. Preserve conflicting evidence. Report `unknown`, `budget_exhausted`, or `disagreement`. Do not silently merge them. `disagreement` means ask the user; do not pick a winner.

Do not register a user profile, `Documents`, `Downloads`, or an entire cloud root. Automatic discovery roots are only the current working directory and `$RSO_CONTEXT_ROOTS`. `rso-context watch --path <workspace> --once` polls one already-registered folder; roots stay bounded.

## MCP adapter

Optional local stdio MCP uses the same core functions as the CLI. Pin and verify isolated `mcp==2.2.0`; `doctor` / `mcp --status` `runtime_ready` is true only for that exact version. **Tool discovery is not a promise of automatic use.** After the host lists `rso_use` / `rso_query`, still call them (or the CLI) with the current agent name and the actual task:

```text
rso_use path=<bounded-workspace> agent=<current-agent-name>
rso_query query="<actual user task>" path=<bounded-workspace> agent=<current-agent-name>
```

MCP `rso_query` returns `rso-mcp-packet/v1` (compact). Compact packets stay within the requested byte budget, including fallback `insufficient_budget` packets. CLI `query` stays `rso-context-packet/v2` unless `--compact` is passed. Recover omitted spans with `rso_expand`, not by guessing. MCP query and explain omit sources whose files sit outside launch `--root` folders.

```text
rso-context mcp --install-runtime
rso-context mcp --setup --client codex --root <bounded-workspace>
rso-context mcp --setup --client claude-code --root <bounded-workspace>
rso-context mcp --remove --client codex
```

`--setup` writes only the `rso-context` entry. `--remove` deletes only that entry. Tests and isolated checks must pass `--config <file>` so live host files are not edited. Other hosts get the stdio command from `doctor` JSON (`mcp_clients.stdio`); that is documentation, not a compatibility claim.

## Pointers, compact, and admin

Chunks are pointers into live files: ingest still reads, chunks, fills FTS, and extracts claims, but it stores `chunks.text` as empty. Heading, line range, chunk hash, and ordinal stay. Query reconstructs plate text from the live file when its sha256 matches `sources.current_hash`, using the same newline rules as chunking. Hash mismatch or a missing file sets `stale=true` and does not treat the current file's lines as the plate. Pre-compact rows may still hold stored text as a fallback.

Schema 5 does not wipe bodies on migrate. After upgrading an existing database, run `rso-context compact-pointers` once so stored file bodies become pointers. Missing files and hash mismatches keep legacy text.

`rso-context admin [--port 7432]` serves a read-only HTML viewer on 127.0.0.1 only.



## Cross-project retrieval

Query already searches the active project, then domain, then shared. Do not add a `concept_id@version` registry or put concept bodies in sqlite. Shared rules stay plates on disk; hash is the version. A hit in another project is `observed`, not inherited as `verified`. To diverge, copy the plate into the current project. Keep unrelated project policies separate.

## Validations and audit

Record `verified`, `disputed`, or `superseded` only when the named validator or user explicitly supplies that decision. Retrieval frequency and agent agreement are not validation.

Agents may propose. They must not record verified, disputed, or superseded unless the user (named) told them to. Solver agreement is evidence, never proof. A `checks` pass is not `verified`.

```text
rso-context compact-pointers
rso-context admin --port 7432
rso-context watch --path <workspace> --once
rso-context pending --path <workspace>
rso-context propose "<agent solve>" --agent <current-agent-name> --path <workspace>
rso-context record-validation <claim-id> --validator <name> --result verified
rso-context run-budget --agent <current-agent-name> --path <workspace>
rso-context audit "<request>" --answer-file <answer-path> --agent <current-agent-name> --path <workspace>
rso-context validate
rso-context explain-project --path <workspace>
rso-context explain <packet-hash>
```

## Packet fields

Query packet (`rso-context-packet/v2`):

- `project`: canonical identity and scope
- `search_order`: projects actually searched (active project, parent domain, shared)
- `corpus_versions`: versions used
- `requirements`: clauses with `evidence_found`, `unknown`, `disagreement`, or `budget_exhausted`
- `evidence`: path, media type, span kind, line range, hashes, stale, reconstructed text when not stale, rank
- `partition`: deterministic split tree (`root`, `method`, `max_leaves`, `leaves` with `stop_reason`)
- `clarification_needed`: true when a requirement status is `disagreement`
- `graph_nodes`: nodes backed by current source evidence
- `claims`: policy-like statements attached to retrieved evidence (plus a small verified-claim boost)
- `validations`: named validation records
- `run`: remaining/initial integer budget after this query
- `checks`: symbolic solvers (`schema_holds`, `span_exists`, and `hash_matches` on conflict). A pass is not verified.
- `packet_hash`: deterministic packet id

Query and audit packets include `partition`. `disagreement` means ask the user; keep both spans and do not pick a winner. Audit schema `rso-match-move-audit/v2` adds a match-move matrix, residuals, and `worst_residual` (exposed, not auto-requeried). Lexical mapping is not proof.

Resume packet (`rso-context-resume/v1`):

- `project`: id, name, scope, kind, canonical_key, corpus_version, resolved_path
- `db_schema_version`
- `freshness`: last_seen / updated_at and counts
- `takes`: `current` and `older_versions` counts only (old text is not included)
- `claims`: compact trust-ordered claims (no chunk dumps)
- `last_runs`: recent query metadata without `packet_json`
- `packet_hash`

Text/code uses `span_kind: source_lines`. DOCX uses `span_kind: extracted_text_lines`.

See [references/protocol.md](references/protocol.md) for identity matching, source safety, and alpha boundaries. See [INSTALL.md](INSTALL.md) for the shareable PATH layout.
