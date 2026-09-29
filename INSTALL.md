# Install RSO Context Alpha 0.9.4

For step-by-step Windows download instructions and a first-use example, start
with [QUICKSTART.md](QUICKSTART.md).

Extract the release ZIP before installing. The installer copies only files listed in `RELEASE-FILES.txt`. Each person keeps their own index. An agent asked to install and use RSO should perform installation and checks itself.

## Contents

- [Requirements](#requirements)
- [Windows](#windows)
- [Linux](#linux)
- [macOS](#macos)
- [First project](#first-project)
- [Custom installation and isolated checks](#custom-installation-and-isolated-checks)
- [Optional MCP adapter](#optional-mcp-adapter)
- [Register permitted projects](#register-permitted-projects)
- [Agent adapter](#agent-adapter)
- [Upgrade and removal](#upgrade-and-removal)

## Requirements

Python **3.11 or newer**, Git on PATH, and Python's standard-library SQLite with FTS5 are required. The installed Windows command launches Python directly. PowerShell is needed only for the optional `.ps1` launcher and PowerShell examples. The core program needs no pip packages, API key, model account, or network service. The installer checks Python and Git; `doctor` checks SQLite afterward.

FTS5 is a compile-time option in SQLite, not a separate program to install, so there is nothing to add to a Python that already has it. When `doctor` reports `"fts5": false`, read the accompanying `fts5_error` and switch interpreters rather than trying to patch the current one. The python.org installers for Windows and macOS ship SQLite with FTS5 enabled, as do the Debian and Ubuntu `python3` packages and Homebrew's `python@3.12`. A Python built from source picks up the option only when a SQLite with FTS5 was present at build time; on Debian and Ubuntu that means installing `libsqlite3-dev` first and rebuilding. The Xcode Command Line Tools Python on macOS is the other common source of a failing check, and the fix there is to install a current python.org or Homebrew build and rerun `install.py` against it.

The POSIX launcher is supplied for Linux and macOS. Linux CLI + MCP runtime was verified on Debian (see VERIFICATION.md). Current-branch automated macOS checks also passed; see [VERIFICATION.md](VERIFICATION.md) for the tested commit and release boundary.

## Windows

Run PowerShell in the extracted release folder:

```powershell
python --version
git --version
python -B install.py
& "$env:USERPROFILE\.local\bin\rso-context.cmd" --version
& "$env:USERPROFILE\.local\bin\rso-context.cmd" doctor
```

If Python is available through `py`, use `py -3.11 -B install.py` or another installed 3.11+ version. The installer prints exact installed paths as JSON.

| Component | Default location |
| --- | --- |
| Program | `%USERPROFILE%\.local\RSOContextAlpha` |
| Command | `%USERPROFILE%\.local\bin\rso-context.cmd` |
| Index | `%USERPROFILE%\.rso-context\context.sqlite3` |

The Windows installation also includes an extensionless `rso-context` launcher
for Git Bash. Both launchers use the same installed source and ledger defaults.

The installer doesn't change PATH. For this PowerShell session:

```powershell
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
rso-context --version
```

For later sessions, add `%USERPROFILE%\.local\bin` to the user PATH or use the full command path. An installing agent can add the entry when installation is authorized, preserving existing PATH entries. The version check must report 0.9.4.

## Linux

Use Python 3.11 or newer, Git, and SQLite FTS5. Ubuntu 24.04 supplies these
through `python3` and `git`; the optional MCP adapter also needs `python3-venv`.
From the extracted release folder:

```sh
python3 -B install.py
export PATH="$HOME/.local/bin:$PATH"
rso-context --version
rso-context doctor
```

The program is installed in `~/.local/share/RSOContextAlpha`, the command in
`~/.local/bin/rso-context`, and the index in
`~/.local/share/rso-context-alpha/context.sqlite3`. For later Bash sessions,
add the PATH export once to `~/.profile`. The installer doesn't edit profiles.

Windows and Linux need separate MCP virtual environments, including when WSL
accesses a Windows checkout. Set `RSO_MCP_RUNTIME` to a Linux-only directory
before `rso-context mcp --install-runtime`; don't reuse a Windows venv.
`RSO_CONTEXT_HOME` selects the ledger directory independently of the install.

## macOS

Use Python 3.11+ with SQLite FTS5 and a working Git executable. In Terminal, from the extracted release folder:

```sh
python3 --version
git --version
python3 -B install.py
"$HOME/.local/bin/rso-context" --version
"$HOME/.local/bin/rso-context" doctor
```

| Component | Default location |
| --- | --- |
| Program | `~/.local/share/RSOContextAlpha` |
| Command | `~/.local/bin/rso-context` |
| Index | `~/.local/share/rso-context-alpha/context.sqlite3` |

The installer makes the POSIX launchers executable. Run `export PATH="$HOME/.local/bin:$PATH"` for this shell. To retain it in later zsh sessions, add that line once to `~/.zprofile`. The installer doesn't edit shell profiles. The launcher uses `python3`, which must remain Python 3.11+.

## First project

After PATH setup, replace placeholders and quote paths containing spaces:

```text
rso-context use --agent <current-agent-name> --path <bounded-project-folder>
rso-context query "<actual user task>" --agent <current-agent-name> --path <bounded-project-folder>
```

Confirm the returned project is correct and `doctor` says `ready: true`. See [MANUAL.md](MANUAL.md) for a new-project example. Don't register a profile, Documents, Downloads, or an entire cloud root. In Git projects, new source files must be tracked before ingestion sees them.

## Custom installation and isolated checks

`--prefix` selects the base containing `bin` and the program, not the index:

```text
python -B install.py --prefix <installation-base>
```

Windows uses `<installation-base>/RSOContextAlpha`; macOS uses `<installation-base>/share/RSOContextAlpha`. Both use `<installation-base>/bin` for commands. Use the returned absolute command path for checks. To keep test state separate, put the global database option before every command:

```text
rso-context --db <scratch-directory>/context.sqlite3 doctor
rso-context --db <scratch-directory>/context.sqlite3 use --agent <current-agent-name> --path <bounded-project-folder>
```

`RSO_CONTEXT_HOME` can instead select a dedicated state directory. Don't edit SQLite directly or distribute someone else's index.

## Optional MCP adapter

The CLI needs no pip packages. The optional stdio MCP adapter pins `mcp==2.2.0` in an isolated venv. After `--install-runtime`, `mcp --status` and `doctor` must report `sdk_requirement` `mcp==2.2.0` and `runtime_mcp_version` `2.2.0`. A 2.x SDK that is not 2.2.0 is not ready.

```text
rso-context mcp --install-runtime
rso-context mcp --status
```

Setup writes only an RSO-owned `rso-context` server entry. Codex uses `config.toml` `[mcp_servers.rso-context]` and keeps any `[mcp_servers.rso-context.tools.*]` approval tables across a rewrite. Claude Code uses `mcpServers.rso-context` in `.claude.json`. Claude Desktop, Cursor, and Windsurf use `mcpServers.rso-context` in `claude_desktop_config.json`, `~/.cursor/mcp.json`, and `~/.codeium/windsurf/mcp_config.json` (override with `RSO_MCP_CLAUDE_DESKTOP_CONFIG`, `RSO_MCP_CURSOR_CONFIG`, or `RSO_MCP_WINDSURF_CONFIG`). A JSON rewrite keeps keys you added to the entry, such as `autoApprove`. Gemini CLI uses `~/.gemini/settings.json` and Antigravity uses `~/.gemini/config/mcp_config.json` (override with `RSO_MCP_GEMINI_CONFIG` or `RSO_MCP_ANTIGRAVITY_CONFIG`); setting up or removing one leaves the other alone. OpenCode uses `mcp.rso-context` in `~/.config/opencode/opencode.json`, or the `.jsonc` beside it when that file already exists (override with `RSO_MCP_OPENCODE_CONFIG`); its entry is a `command` list rather than a command plus args. Repeat setup replaces that entry and leaves other servers in place. Removal deletes only `rso-context`.

```text
rso-context mcp --setup --client all
rso-context mcp --setup --client codex
rso-context mcp --remove --client codex
```

`--client all` configures every supported host whose config folder already
exists and lists the ones it skipped. Name one host (`codex`, `claude-code`,
`claude-desktop`, `cursor`, `windsurf`, `gemini`, `antigravity`, `opencode`) to
configure only that one. Restart the host afterwards.

The entry pins no `--root`, so one entry serves every project on that host.
Each tool call names the project folder in `path`, and the server accepts any
bounded folder. It refuses a filesystem root, a user profile or a folder that
contains one, Desktop, Documents, Downloads, a OneDrive root, a hidden profile
folder such as `.ssh`, operating-system folders, and the ledger home. The server
starts however the host launches it. Desktop apps often start servers from
their install folder or the user profile; that directory is simply not used as
a root.

To restrict a host to fixed folders instead, pass `--root` (repeatable) with
`--strict-roots`. The server then refuses every path outside those roots, and a
launch without `--root` binds only its working directory.

When the server cannot start, it writes a JSON-RPC error frame to stdout as
well as the JSON error on stderr, with code `-32099` and a null id. A client
that watches only stdout would otherwise see nothing, wait out its own timeout,
and report the startup failure as a timed-out request. Read the `message` for
the actual cause.

The generated entry carries `RSO_CONTEXT_HOME` and, on Windows, `SystemRoot`
with a system-only `PATH`. Some hosts replace the child environment with the
config's env block instead of adding to it; without those the server cannot
find a home directory or load winsock, dies before the handshake, and the host
reports a request timeout rather than a crash.

Isolated checks must pass `--config <file>`. Do not point tests at a live user config. Setup and removal rewrite only the `rso-context` tables; other `[mcp_servers.*]` headers, including those with trailing comments, stay in place. MCP query and explain omit sources outside the launch roots and the folders this server process has been asked to use. `rso_query` sends a text-only tool result so the JSON-RPC `tools/call` payload stays within `byte_budget`. Other clients can launch the stdio command printed by `doctor` under `mcp_clients.stdio`; that is not a tested compatibility claim. Tool discovery is not automatic use: still call `rso_use` then `rso_query` with the actual task.

## Register permitted projects

Before using MCP, distinguish access from registration: reaching a folder does not ingest it. Call `rso_use` with the project path and current agent name, then `rso_query`. A second folder needs its own `rso_use`. An unregistered-project error calls for `rso_use`, not broader filesystem permissions. `rso_resume` alone does not ingest.

Repeat `--setup` replaces the RSO entry's command, arguments, and environment; it does not accumulate previously configured roots. Under `--strict-roots`, pass every intended root each time. Restart or reconnect the host's MCP server after changing launch arguments. Registering or ingesting an already-permitted folder needs no configuration change.

Live Codex checks on Windows are recorded in [VERIFICATION.md](VERIFICATION.md). Automated macOS checks passed on the current branch; live Claude Code MCP acceptance remains unverified.

## Agent adapter

Register `SKILL.md` from the installed package when the client supports a skill path, so its relative documentation links remain available. If the client requires a copied skill directory, preserve the accompanying documentation and `references/` layout. Common roots are `~/.codex/skills/rso-context`, `~/.claude/skills/rso-context`, `~/.grok/skills/rso-context`, and `~/.agents/skills/rso-context`. The installer doesn't register skills with agent applications. Use each client's own name for `--agent`; local clients can share one CLI and per-user index.

## Upgrade and removal

Use the package attached to the [0.9.4 release](https://github.com/Kinestrand/RSOContextAlpha/releases/tag/v0.9.4).
The release ZIP, source branch, and installed program are distinct: downloading
new source does not upgrade the installed command. Confirm the installed command
reports `0.9.4` after running the installer. See [CHANGELOG.md](CHANGELOG.md) for
the changes from 0.7.0.

Run the new release's installer with the same prefix. Differing package files are backed up beside their targets with `.rso-backup-<id>` suffixes before replacement. Identical files are skipped; unrelated files are preserved. Keep backups until the upgraded program has been checked. Program upgrades don't copy or delete the index. After a schema 4 to 5 upgrade, run `rso-context compact-pointers` once.

There is no uninstall command. Remove only the confirmed installed program directory and command if uninstalling; remove a PATH entry only if added for this install. Keep the separate index unless its deletion is explicitly intended.
