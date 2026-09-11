# RSO Context Alpha 0.8.0

[Download 0.8.0](https://github.com/Kinestrand/RSOContextAlpha/releases/tag/v0.8.0) ·
[Quick-start](QUICKSTART.md) · [Installation](INSTALL.md) ·
[Manual](MANUAL.md) · [Changes](CHANGELOG.md) · [Verification](VERIFICATION.md)

**RSO means Recursive Semantic Octree**, from Paul Griswold's research paper. [Research origin](RESEARCH-ORIGIN.md) explains the graphics-inspired proposal and which parts this application implements.

RSO Context is a local evidence ledger for agents working with project files. It gives different agents a shared way to retrieve current requirements, decisions, source references, and recorded approvals from the same bounded project folder.

For example, a project file might say, "Decision: export the preview at 24 fps." RSO can return that sentence with its file path, line range, and content hash. If the file changes, the next ingest records a new version. If someone explicitly approves a claim, their named validation can travel with the evidence.

The files remain the source of truth. RSO doesn't learn from chat, call a model, use embeddings, or change model weights. It uses Python and local SQLite full-text search. Its command-line interface returns JSON; the optional browser viewer runs on localhost. An optional local stdio MCP adapter (`rso-context mcp`) uses the same CLI functions and ledger. It pins `mcp==2.2.0` in an isolated runtime and is not required for ordinary CLI use. See [INSTALL.md](INSTALL.md#optional-mcp-adapter).

## Start here

Download **RSOContextAlpha-0.8.0.zip** from the
[0.8.0 release](https://github.com/Kinestrand/RSOContextAlpha/releases/tag/v0.8.0) and
extract it. The release page includes a SHA-256 checksum. GitHub access is
required while the repository is private. A release ZIP is tied to a version;
**Code > Download ZIP** downloads the selected branch's current source instead.

**New to GitHub or command-line tools? Follow the [beginner quick-start](QUICKSTART.md).**
It covers downloading the ZIP, installing Python and Git, setting up RSO, and
trying a complete example. It also includes a request you can paste into a local
coding agent to have it perform setup.

1. Read [INSTALL.md](INSTALL.md) for Windows or macOS setup. An agent asked to install RSO should perform the installation and checks itself.
2. Point it at one project folder, then ask a real question:

   ```text
   rso-context use --agent <current-agent-name> --path <bounded-project-folder>
   rso-context query "What export settings are required?" --agent <current-agent-name> --path <bounded-project-folder>
   ```

3. Read [MANUAL.md](MANUAL.md) for the workflow, examples, command reference, and troubleshooting. [SKILL.md](SKILL.md) is the agent adapter, with [references/protocol.md](references/protocol.md) describing the packet contract.

Replace angle-bracket placeholders before running commands. `use` registers the folder, ingests its files, and returns a resume packet. `query` retrieves evidence for the actual task.

MCP permissions and searchable context are separate. A launch `--root` permits access to a folder; it does not register or ingest it. Call `rso_use` for each permitted project before querying it. Re-ingest after file changes. Copies keep their own project identity; RSO does not copy or update their source files.

## What you can do

| Need | Workflow |
| --- | --- |
| Start with current project decisions | `use`, then `query` with the actual task |
| Hand work to another agent | Share the project files and database; use distinct agent names |
| Check where an answer came from | Inspect source spans and hashes; use `explain` for a saved packet |
| Pick up an edited requirement | Run `use` again; unchanged files are skipped |
| Separate suggestions from approval | Use `propose`; reserve validation for a named, authorized decision |
| Integrate through MCP | Configure bounded launch roots and call `rso_use` before querying |
| Inspect the ledger locally | Run the optional read-only localhost viewer with `admin` |

## What's new in 0.8.0

- Optional local stdio MCP tools for use, resume, query, explain, and expansion.
- Compact evidence packets with byte budgets and recovery of omitted spans.
- Ingestion coverage reports and explicit empty-result diagnostics.
- Codex and Claude Code configuration helpers with isolated-test support.
- Fixes for commented TOML headers, launch-root filtering, and serialized MCP
  response budgets.

See [CHANGELOG.md](CHANGELOG.md) for upgrade notes. The multi-agent trial is
ongoing; no RSO-on/off performance benefit is claimed.

## Requirements and first run

The core CLI requires Python 3.11+, Git on PATH, and Python SQLite with FTS5.
It needs no provider account or API key. From PowerShell in the extracted folder:

```powershell
python -B install.py
& "$env:USERPROFILE\.local\bin\rso-context.cmd" --version
& "$env:USERPROFILE\.local\bin\rso-context.cmd" doctor
```

The version should be `0.8.0`; `doctor` should report `ready: true`. The installer
prints the command path and does not change PATH. Follow the
[quick-start demo](QUICKSTART.md) for a complete example using a disposable
project and ledger. [INSTALL.md](INSTALL.md) covers macOS and custom paths;
native macOS execution remains unverified.

In a Git project, source files must be tracked before ingestion sees them.
A new non-Git project needs a marker such as `AGENTS.md`. Select one bounded
project, not a home directory or an entire cloud storage root. After edits, run
`use` again. `resume` alone does not refresh the files.

## Documentation map

| Document | Read it for |
| --- | --- |
| [QUICKSTART.md](QUICKSTART.md) | Beginner Windows download, installation, demo, and first real project |
| [INSTALL.md](INSTALL.md) | Platform paths, prerequisites, MCP, upgrades, and removal |
| [MANUAL.md](MANUAL.md) | Daily workflow, packet fields, trust states, commands, and troubleshooting |
| [SKILL.md](SKILL.md) | Instructions for agents using the installed program |
| [Protocol](references/protocol.md) | Packet contracts, project identity, and source safety |
| [RESEARCH-ORIGIN.md](RESEARCH-ORIGIN.md) | Research proposal versus implemented behavior |
| [VERIFICATION.md](VERIFICATION.md) | Recorded checks and environments still untested |
| [CHANGELOG.md](CHANGELOG.md) | Version changes and upgrade considerations |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Source checkout, tests, package construction, and publication |

## What it keeps separate

An **observed** claim means a source contains a statement. A **proposed** claim is a suggestion awaiting review. **Verified**, **disputed**, and **superseded** are named validation states. Repetition, search ranking, and agent agreement don't approve a claim.

RSO preserves historical source records while retrieving current evidence. It marks missing or changed source files as stale until ingestion reconciles them. Its lexical matching and coverage checks help locate evidence; they don't prove a conclusion or guarantee that every contradiction was found.

Graft and other code maps remain separate tools. They answer structural questions such as where a function lives or what calls it. RSO handles source evidence and validation history. It excludes folders named `graft` so derived cards don't become original evidence.

Paul Griswold's evaluation, September 5, 2026: RSO has been tested with Graft in place, and they work excellently together. This is the project owner's reported experience. Graft is a separate third-party skill, not part of RSO or a required dependency.

## Using it in an agent harness

[Inference] A harness could call `use` at task start, call `query` with the task, provide the resulting evidence packet to its agent, and record an authorized validation after a person or named check approves a claim. This is an integration pattern, not a supplied integration with any particular harness. RSO's CLI and skill are the shipped interface; it doesn't require a particular model provider.

## Release boundaries

Development lives in the [Kinestrand/RSOContextAlpha](https://github.com/Kinestrand/RSOContextAlpha) repository. See [DEVELOPMENT.md](DEVELOPMENT.md) for checkout, testing, and release-build commands. RSO builds independently of the projects it indexes.

This is an alpha. Windows checks are included in the release tests. Python 3.11 or newer, Git, and SQLite FTS5 support are required. The core CLI needs no third-party Python packages or provider account. The optional MCP adapter is the only shipped pip install, and `doctor` / `mcp --status` must report exactly `mcp==2.2.0` (`runtime_mcp_version` `2.2.0`) before that adapter is ready. Live Codex MCP use, resume, query, explain, and expansion were checked on Windows on September 10, 2026. Live Claude Code and native macOS execution remain unverified. See [verification status](VERIFICATION.md) for the scope of those checks.

The ZIP contains the program, installer, documentation, a project interview, and focused regression tests. It doesn't contain a personal index, credentials, agent configuration, or internal repair notes. Each recipient starts their own ledger. [RELEASE-FILES.txt](RELEASE-FILES.txt) is the explicit package allowlist.

The private ledger can contain searchable source text and metadata. Keep it, its backups, local MCP configuration, and indexed project data outside source commits and releases. Publishing RSO's code does not authorize publishing those files or changing repository visibility.

## License

Copyright 2026 Paul Griswold.

The RSO source code, documentation, and other project files in this repository
are licensed under the Apache License, Version 2.0, unless otherwise noted.
See [LICENSE](LICENSE) for the full terms and [NOTICE](NOTICE) for attribution.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this work except in compliance with the License.
You may obtain a copy of the License at
https://www.apache.org/licenses/LICENSE-2.0.

Unless required by applicable law or agreed to in writing, this work is
distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
either express or implied. See the License for the specific language governing
permissions and limitations under the License.

This license applies to RSO, not to the files or evidence ledgers users index
with it. Graft is a separate third-party tool and is not included in this grant.
