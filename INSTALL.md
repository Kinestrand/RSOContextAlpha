# Install layout

RSO is per-user and agent-installed. A person should not need to run PowerShell. An agent told "install and use RSO" follows this layout so it works the same on every machine.

## Three pieces

1. **Command on PATH.** `%USERPROFILE%\.local\bin\rso-context.cmd` (Windows) or `~/.local/bin/rso-context` (Linux). This is a short pointer. Agents type `rso-context`; they do not care where the program folder is.
2. **Program.** `%USERPROFILE%\.local\RSOContextAlpha\` (Windows) or a folder next to that launcher. Contains `src\`, `rso-context.ps1`, and this docs set.
3. **Index.** `%USERPROFILE%\.rso-context\context.sqlite3`. Each person has their own ledger. Sharing RSO shares the program and the skill, not someone else's database.

Do not put the program in `AppData\Local`. That path is a leftover Windows dump and is not part of the shareable layout.

## Agent install (no human CLI)

If `rso-context` is already on PATH, skip to **Use**.

Otherwise:

1. Copy this folder to `%USERPROFILE%\.local\RSOContextAlpha` (or `~/.local/share/RSOContextAlpha` on Linux).
2. Write the PATH pointer:
   - Windows `rso-context.cmd`: `powershell -NoProfile -File "%USERPROFILE%\.local\RSOContextAlpha\rso-context.ps1" %*`
   - Put that file in `%USERPROFILE%\.local\bin` and ensure that directory is on the user PATH.
3. Copy `SKILL.md` plus `references/` into the current agent's skill folder.
4. Run doctor. If it is not ready, fix Python/git/SQLite. Do not ask the user to paste commands.

## Use

The current agent runs this itself, using its own name, on the bounded workspace it is already in:

```text
rso-context use --agent <current-agent-name> --path <bounded-workspace>
```

That registers, incrementally ingests, and returns a resume packet. Then `query` with the user's actual task.

Do not register Documents, Downloads, a user profile, or an entire cloud root. Do not edit `context.sqlite3` by hand.

## Sharing with someone else

Give them this folder (or a release zip of it) and the skill. Their agent repeats **Agent install**. Their index starts empty and grows from *their* projects. Do not copy `context.sqlite3` unless they explicitly want that corpus.
