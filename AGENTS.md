# RSO Context Alpha

## Intent
RSO means Recursive Semantic Octree, from Paul Griswold's research paper. `RESEARCH-ORIGIN.md` explains the proposal and the implemented evidence-ledger subset. Do not claim the application implements or validates the full research architecture.
This project must give agents a local, shared evidence ledger over bounded project folders.
The canonical workspace is this folder. Do not treat git worktrees, Antigravity copies, Codex output dumps, or paper checkouts as this project.
Agents are clients. The SQLite service owns persistence and project identity.

## Repository and development
The standalone source repository is `https://github.com/Kinestrand/RSOContextAlpha`.
RSO is licensed under Apache-2.0; preserve LICENSE and NOTICE in releases and installations.
Keep the repository private unless the user explicitly authorizes public disclosure. Licensing alone does not authorize changing repository visibility.
This checkout owns RSO development and builds. Kinestrand and other indexed projects are clients, not source locations or build dependencies.
Read `DEVELOPMENT.md` for tests, release packaging, and installation from a checkout.
Never commit or upload a user's ledger, credentials, local MCP configuration, or historical coordination state.
The ignored repair board and repair notes are historical local artifacts, not instructions for new development work.

## Non-goals
This project must not become a second brain, chat archive, or embedding store.
Do not merge this ledger with Graft. Graft is a separate code-structure engine with a separate database.
Graft is a third-party development skill, not part of RSO. It is optional and must not be bundled or required to build, test, install, or run RSO.
Do not ingest directories named `graft`. Derived Graft cards must never become RSO sources.
A Graft summary an agent repeats is observed. It is never verified unless a named validation or authoritative source backs it.

## Constraints
Agents must register only a bounded project folder.
Do not register a user profile, Documents, Downloads, or an entire OneDrive root.
Never edit `context.sqlite3` directly. Use the CLI.
Secrets must never be committed or ingested. Use placeholders.
Do not use provider APIs, embeddings, screen capture, keystrokes, or browser history.

## Indexing
RSO does not learn from chat. It re-reads files.
Required before context work: ingest this folder, then resume, then query with the actual task.
MCP access and registration are separate. An MCP tool may reach a bounded project folder; that does not register or ingest it. Call `rso_use` for each project before querying it. An unregistered-project error calls for `rso_use`, not for broader access. Source synchronization and index refresh are separate operations.
Git ingest uses tracked files only (`git ls-files --cached`). Never ingest `private/`, gitignored files, backups, or output folders.
Do not register a user profile.
`ingest` must skip unchanged files and re-chunk files that changed.
Chunks store pointers, not file bodies. After a schema 4→5 upgrade, run `compact-pointers` once.
`watch` may poll registered folders; it is optional. Keep watch roots bounded.
`watch --path <workspace>` observes one already-registered folder; it must not discover or register siblings or a user profile.
Queries must use current active sources only. Historical versions remain stored.

## Decisions
Decision: observed means the current source contains the statement. Retrieval frequency and agent agreement are not validation.
Decision: proposed means an agent filed a solve that is not on the plate. Do not treat proposed as observed or verified. Promotion still needs a named validator or the user.
Decision: verified, disputed, or superseded requires a named validator or the user.
Decision (2026-09-28, owner request: RSO must run on any agent and any folder through MCP): MCP access is open by default. A tool call may name any bounded project folder, whether or not it sits under a launch `--root`. Setup writes one portable host entry with no pinned `--root`. The server must start however the host launches it; a working directory that cannot be a root yields no launch root, not a startup failure. One bounded-folder rule covers every MCP path: it refuses a filesystem root, a user profile or any folder containing one, Desktop, Documents, Downloads, a OneDrive root, a hidden profile folder such as `.ssh`, operating-system folders, and the ledger home. `--strict-roots` restores launch-only access for a user who wants it. This supersedes the earlier rule that no tool grants access to a folder the launch did not.
Decision: RSO and Graft stay separate engines. Mixed questions must be source-labeled.
The approved stack is local SQLite/FTS5 plus the `rso-context` CLI. Optional local stdio MCP (`rso-context mcp`) uses the same core functions, project identity, and ledger. Do not add HTTP hosting, a daemon, or a cloud memory provider without a new decision in this file.
Decision: cross-project retrieval uses `search_order` (active project, domain, shared). Do not add a concept_id registry or store concept bodies in sqlite. Shared rules live as plates. A span found in another project is observed, not inherited as verified. Override by copying the plate into the project (new source). Studio and class work do not share one concept space.
Decision: the shareable install is a PATH command plus `%USERPROFILE%\.local\RSOContextAlpha`. Do not keep the program in AppData\Local. The index stays at `%USERPROFILE%\.rso-context`. Each person gets their own index.
Required: an agent told to install and use RSO must perform the install itself. Do not require the user to run PowerShell or ingest by hand.

## Sources of truth
AUTHORITATIVE: this file, then `DAY-ONE.md`, then `ROADMAP.md`, then `SKILL.md`.
Reader note for agent-literate people: `FOR-AGENT-BUILDERS.md` (positioning, not a spec).
Required agent name for `--agent` is the current agent. Do not hardcode `codex`.

## Project identity
Register only this bounded checkout for RSO development. Other indexed projects retain their own identity and scope.
