# RSO Context Alpha

Local **evidence ledger** for AI agents. Files on disk are the memory. Agents are clients of a shared index; they do not each own a private truth.

**Version:** 0.7.0 (pointer index — path, content hash, line range; plates stay on disk)

This repository is **private** (Kinestrand). The SQLite ledger (`context.sqlite3`) is never in git.

## Why it exists

Most agent “memory” products act like a second brain: chat summaries, similarity search, three diaries by Thursday.

RSO is built like a VFX plate:

- The **file** is the plate (source of truth).
- A retrieval or model claim is a **take about** the plate, not a replacement for it.
- **Observed** means the current source contains that sentence. That is all.
- **Accepted / verified** only happens when a named person (or a check they already trust) records it.
- Conflicts stay visible. Disagreeing sources are not averaged into one “brain fact.”

That is a different job from a code map (Graft and friends) and from ordinary RAG. Keep those separate.

## Docs in this tree

| File | Role |
|------|------|
| [INSTALL.md](INSTALL.md) | Where the program, PATH launcher, and per-user index live |
| [FOR-AGENT-BUILDERS.md](FOR-AGENT-BUILDERS.md) | Positioning for people who already ship agents (not a protocol spec) |
| [AGENTS.md](AGENTS.md) / [SKILL.md](SKILL.md) / [protocol.md](protocol.md) | How agents call and interpret the ledger |
| [DAY-ONE.md](DAY-ONE.md) | New-project ground rules |
| [CROSS-PROJECT.md](CROSS-PROJECT.md) | Plates + search order across projects (no shared concept registry) |
| [ROADMAP.md](ROADMAP.md) | Near-term leftovers |

## Quick start (agents)

1. Install per [INSTALL.md](INSTALL.md) (program under `~/.local/RSOContextAlpha` or `%USERPROFILE%\.local\RSOContextAlpha`, launcher on PATH).
2. On a **bounded** project folder (not Documents / profile / OneDrive root):

```text
rso-context register --agent <your-name> --path <workspace>
rso-context ingest  --agent <your-name> --path <workspace>
rso-context query "<task>" --agent <your-name> --path <workspace>
```

Or: `rso-context use --agent <your-name> --path <workspace>` then `query`.

Read query packets for: requirements, evidence, claims, validations, `packet_hash`. Treat **observed** claims as “the source said this,” not as verified, unless a named validation backs them.

## What this is not

Not a second brain. Not Graft. Not ChatGPT memory. Not an embedding database. Not proof the model reasoned correctly.

## License / disclosure

