# Day-one indexing

RSO Context is useful on a brand-new project only if the project writes something it can ingest. It does not learn from conversation. Usefulness increases as the folder grows, because ingest re-reads files.

## How the index stays current

`ingest` is incremental. Unchanged files are skipped (size, mtime, then content hash). New or edited files are chunked. Policy-like sentences become **observed** claims. Old file versions stay in the database; queries return current active sources only.

The agent, not the user, runs this before context-heavy work:

```text
rso-context use --agent <current-agent-name> --path <bounded-folder>
rso-context query "<actual user task>" --agent <current-agent-name> --path <bounded-folder>
```

`use` is ingest plus resume in one step. See `INSTALL.md` for the shareable PATH layout.

Optional background poller:

```text
rso-context watch --interval 10
```

`watch` re-ingests registered folders on a timer. It is not an OS file watcher. It will not see a folder until that folder is registered. Keep roots bounded so it does not wander into Documents or Downloads.

Nothing said in chat is indexed unless someone writes it into a file.

## Start a new project (person)

Open `project-truth.html` in a browser. It is a short interview. Save the interview file if you want to edit later. Download `PROJECT-TRUTH.md` into the **project folder** (not Documents). Do not overwrite an existing `AGENTS.md`. Then an agent runs ingest on that folder.

The interview writes policy language (`must`, `do not`, `Decision:`, `AUTHORITATIVE`, `UNKNOWN`) so the index can pick it up. Chat still is not indexed unless it lands in that file.

For agents filling the file by hand, the skeleton below still works.

## Start a new project with AGENTS.md

`AGENTS.md` is a project marker. A new folder that contains one is enough to register and ingest on day one.

Write real constraints in policy language. The extractor looks for: must, should, shall, required, do not, don't, never, prohibited, approved, canonical, decision, authoritative, supersedes. Soft notes such as "we might try X" mostly will not become claims.

Copy this skeleton into the new project's `AGENTS.md` and replace the brackets:

```markdown
# [project name]

## Intent
This project must [one-sentence job].
The canonical workspace is this folder. Do not treat copies, worktrees, or export dumps as this project.

## Non-goals
This project must not [thing it will be tempted to become].
Do not merge this index with [named adjacent tool] or ingest that tool's derived cards as sources.

## Constraints
Agents must register only this bounded folder.
Do not register Documents, Downloads, or an entire cloud root.
Secrets must never be committed or ingested. Use placeholders.

## Decisions
Decision: [choice]. This supersedes [whatever it replaced, or none yet].
The approved stack is [tools]. Do not add a second stack without a new decision in this file.

## Sources of truth
AUTHORITATIVE: this file, then [README / ROADMAP / spec].
Required before context work: ingest this folder, then resume, then query with the actual task.
Observed statements in code or chat are not verified. A named person must record verification.

## Open questions
UNKNOWN: [question]. Do not treat guesses as approved.
```

This repository's `AGENTS.md` is the worked example for RSO Context Alpha itself.

When a decision actually lands, edit the Decisions section and ingest again. Do not hope an agent chat becomes memory.
