# RSO Context Alpha 0.7.0

RSO Context is a local evidence ledger for agents working with project files. It gives different agents a shared way to retrieve current requirements, decisions, source references, and recorded approvals from the same bounded project folder.

For example, a project file might say, "Decision: export the preview at 24 fps." RSO can return that sentence with its file path, line range, and content hash. If the file changes, the next ingest records a new version. If someone explicitly approves a claim, their named validation can travel with the evidence.

The files remain the source of truth. RSO doesn't learn from chat, call a model, use embeddings, or change model weights. It uses Python and local SQLite full-text search. Its command-line interface returns JSON; the optional browser viewer runs on localhost.

## Start here

1. Read [INSTALL.md](INSTALL.md) for Windows or macOS setup. An agent asked to install RSO should perform the installation and checks itself.
2. Point it at one project folder, then ask a real question:

   ```text
   rso-context use --agent <current-agent-name> --path <bounded-project-folder>
   rso-context query "What export settings are required?" --agent <current-agent-name> --path <bounded-project-folder>
   ```

3. Read [MANUAL.md](MANUAL.md) for the workflow, examples, command reference, and troubleshooting. [SKILL.md](SKILL.md) is the agent adapter, with [references/protocol.md](references/protocol.md) describing the packet contract.

Replace angle-bracket placeholders before running commands. `use` registers the folder, ingests its files, and returns a resume packet. `query` retrieves evidence for the actual task.

## What it keeps separate

An **observed** claim means a source contains a statement. A **proposed** claim is a suggestion awaiting review. **Verified**, **disputed**, and **superseded** are named validation states. Repetition, search ranking, and agent agreement don't approve a claim.

RSO preserves historical source records while retrieving current evidence. It marks missing or changed source files as stale until ingestion reconciles them. Its lexical matching and coverage checks help locate evidence; they don't prove a conclusion or guarantee that every contradiction was found.

Graft and other code maps remain separate tools. They answer structural questions such as where a function lives or what calls it. RSO handles source evidence and validation history. It excludes folders named `graft` so derived cards don't become original evidence.

Paul Griswold's evaluation, September 5, 2026: RSO has been tested with Graft in place, and they work excellently together. This is the project owner's reported experience. Graft is a separate third-party skill, not part of RSO or a required dependency.

## Using it in an agent harness

[Inference] A harness could call `use` at task start, call `query` with the task, provide the resulting evidence packet to its agent, and record an authorized validation after a person or named check approves a claim. This is an integration pattern, not a supplied integration with any particular harness. RSO's CLI and skill are the shipped interface; it doesn't require a particular model provider.

## Release boundaries

Development lives in the [paulrus/rso-context](https://github.com/paulrus/rso-context) repository. See [DEVELOPMENT.md](DEVELOPMENT.md) for checkout, testing, and release-build commands. RSO builds independently of the projects it indexes.

This is an alpha. Windows checks are included in the release tests. A POSIX launcher and macOS installation instructions are supplied, but macOS execution hasn't been tested on a Mac for this release. Python 3.11 or newer, Git, and SQLite FTS5 support are required; no third-party Python packages or provider account are required by the core runtime.

The ZIP contains the program, installer, documentation, a project interview, and focused regression tests. It doesn't contain a personal index, credentials, agent configuration, or internal repair notes. Each recipient starts their own ledger. [RELEASE-FILES.txt](RELEASE-FILES.txt) is the explicit package allowlist.

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
