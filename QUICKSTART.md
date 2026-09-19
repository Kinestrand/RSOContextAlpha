# Start using RSO on Windows

This guide takes you from downloading RSO to asking a question about a sample
project. You don't need to write code or know Git commands. Copy each command
block into PowerShell and press Enter; don't copy the surrounding backticks.

RSO runs on your computer. It reads project files and returns matching evidence.
For example, it can find the file that says your preview must be 24 fps.
You can use it directly or ask a local coding agent to use it for you.

RSO stands for **Recursive Semantic Octree**, the research proposal that inspired this application. [Research origin](RESEARCH-ORIGIN.md) explains it in plain language.

These steps are for Windows. macOS instructions are in
[INSTALL.md](INSTALL.md#macos); native macOS execution is still unverified.

## 1. Download and extract

1. Open the [RSO 0.9.2 release](https://github.com/Kinestrand/RSOContextAlpha/releases/tag/v0.9.2).
2. Under **Assets**, download **RSOContextAlpha-0.9.2.zip**.
3. In File Explorer, open Downloads. Right-click the downloaded ZIP and choose
   **Extract All**, then **Extract**.
4. Open the extracted folder, then any folder inside it, until you can see
   `install.py`, `README.md`, and `RELEASE-FILES.txt` together. File Explorer may
   hide the `.py`, `.md`, and `.txt` endings.

You don't need to fork the repository, clone it, or use GitHub Actions.
The packaged ZIP is tied to version 0.9.2. For development, **Code > Download ZIP**
on the repository downloads the current branch instead; it also contains the
installer, but can include changes newer than a tagged release.

If GitHub shows 404, sign in with an account that has access. While the repository
is private, its owner must grant access before you can download it.

GitHub also documents the [Code > Download ZIP steps](https://docs.github.com/en/repositories/working-with-files/using-files/downloading-source-code-archives).

## 2. Choose agent-assisted or manual setup

If you have a coding agent that can read files and run commands on your computer,
open the extracted RSO folder in that application. Paste this request into its
chat, then follow its progress:

```text
Install RSO from this extracted folder for my Windows user account. Read
QUICKSTART.md, INSTALL.md, and SKILL.md first. Check Python 3.11+ and Git,
install missing prerequisites if your permissions allow, and run the installer
yourself. Verify the installed command's version and doctor result. Run the
sample project from QUICKSTART.md and show the evidence for 24 fps. Keep all
test data in a separate demo folder and database. Tell me exactly what succeeded
and anything you could not complete. Do not index my entire home or Downloads.
```

A chat application without local file and command access can't perform this
installation. Use the manual steps below instead. If your agent completes setup
and the demo, skip to **Use it with your own project**.

## 3. Install Python and Git

Open the extracted folder containing `install.py`. Click File Explorer's address
bar at the top, type `powershell`, and press Enter. This opens PowerShell in that
folder. Run:

```powershell
python --version
git --version
```

Python should report 3.11 or newer. Git should print a version number. If both
work, go to step 4.

- If Python is missing or older than 3.11, visit
  [Python for Windows](https://www.python.org/downloads/windows/). Choose a
  stable Python 3 release, then its **Windows installer (64-bit)** for an
  ordinary Intel/AMD Windows PC, or **ARM64** for an ARM PC. Use the regular
  installer, not the embeddable package. Open the downloaded installer, select
  **Add python.exe to PATH** if offered, and choose **Install Now**.
- If Git is missing, visit [Git for Windows](https://git-scm.com/install/windows),
  download the installer for your computer, and run it. Retain the option to
  use Git from the command line and third-party software when offered.

Close PowerShell after installing either prerequisite. Open it again from the
RSO folder and repeat the version commands. Both must work in the new window.
Even when installing from a ZIP, RSO needs Git installed.

## 4. Install and check RSO

In that PowerShell window, run:

```powershell
python -B install.py
```

The result is structured text called JSON. Look for `program` and `command`:
these show where RSO was installed. Next, copy this block:

```powershell
& "$env:USERPROFILE\.local\bin\rso-context.cmd" --version
& "$env:USERPROFILE\.local\bin\rso-context.cmd" doctor
```

The version should include `0.9.2`. The doctor result should include
`"ready": true`. If it says false, look at the checks in that result and the
troubleshooting table below. MCP is optional. If you install it later with
`rso-context mcp --install-runtime`, `doctor` must show `runtime_mcp_version`
`2.2.0` before that adapter is ready. The CLI itself does not need that step.

These commands use RSO's full installed path. You don't need to change your PATH
settings to follow this guide. The `&` tells PowerShell to run the quoted path.

## 5. Ask a question about a sample project

Copy this whole block into the same PowerShell window. It creates a new demo
folder with a unique name under your Windows temporary folder, writes a sample
project file, and uses a separate demo database. It won't overwrite an existing
project or use your normal RSO index.

```powershell
$rsoDemoRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("RSO-Demo-" + [guid]::NewGuid().ToString('N'))
$rsoDemoProject = Join-Path $rsoDemoRoot 'Sample Project'
New-Item -ItemType Directory -Path $rsoDemoProject | Out-Null
Set-Content -LiteralPath (Join-Path $rsoDemoProject 'AGENTS.md') -Encoding UTF8 -Value "# Sample project`nDecision: export the preview at 24 fps."
$rsoDemoDb = Join-Path $rsoDemoRoot 'demo.sqlite3'
& "$env:USERPROFILE\.local\bin\rso-context.cmd" --db $rsoDemoDb use --agent quickstart-demo --path $rsoDemoProject
& "$env:USERPROFILE\.local\bin\rso-context.cmd" --db $rsoDemoDb query "What frame rate should the preview use?" --agent quickstart-demo --path $rsoDemoProject
```

The first result describes the project RSO read. In the second result, look for:

- `"status": "evidence_found"` under `requirements`.
- An item under `evidence` pointing to your demo's `AGENTS.md`.
- Text containing `Decision: export the preview at 24 fps.`

That is your first successful retrieval. RSO returns source evidence as JSON;
it doesn't write a conversational answer by itself. A coding agent can read
that evidence and explain it in normal language. The demo files are temporary;
use a durable project folder for real work.

## 6. Use it with your own project

Pick one specific project folder containing the files you want RSO to read.
For example, select the folder for one film, application, or course. Don't select
your entire user folder, Documents, or Downloads.

To use it through a local coding agent, open your actual project in the agent
application. Paste the following message, replacing `PASTE PROJECT FOLDER HERE`
with the full folder path copied from File Explorer's address bar:

```text
Use the installed RSO Context tool for this project:
PASTE PROJECT FOLDER HERE

Read the installed adapter at %USERPROFILE%\.local\RSOContextAlpha\SKILL.md
and installation guidance at %USERPROFILE%\.local\RSOContextAlpha\INSTALL.md.
Use your actual agent name with --agent. Register and ingest only the project
folder above, then query: "What requirements and decisions are recorded here?"
Explain the answer with its source files. If no usable project file exists,
help me create PROJECT-TRUTH.md or AGENTS.md without replacing existing files.
If this application supports persistent local skills, register the RSO adapter
using its supported mechanism, preserve existing configuration, and verify it
is available. Report whether setup applies only to this conversation or also
to future conversations. Don't mark claims verified without my approval.
```

RSO's installer doesn't connect it to an agent application automatically. Skill
setup depends on that application; [INSTALL.md](INSTALL.md#agent-adapter)
describes the supplied adapter files. Until persistent setup is confirmed,
repeat the request when starting a new conversation.

If your agent uses MCP, giving it permission to access a project folder is only the first step. Ask it to call `rso_use` for that folder, then query it. Repeat this for each project you want searchable. A permitted folder can still be unregistered. RSO does not copy files between projects.

To use RSO manually, run this block. The first line asks you to paste your real
project path; paste it without surrounding quote marks and press Enter:

```powershell
$rsoProject = Read-Host 'Paste the full path to one project folder'
& "$env:USERPROFILE\.local\bin\rso-context.cmd" use --agent manual-user --path $rsoProject
& "$env:USERPROFILE\.local\bin\rso-context.cmd" query "What requirements and decisions are recorded here?" --agent manual-user --path $rsoProject
```

Here, `manual-user` identifies you as the caller. An agent should use its own
name. Unlike the demo, these commands use your normal per-user index.

RSO reads decisions written in files; it doesn't capture chat history. For a
new project, see the small example in [MANUAL.md](MANUAL.md#1-give-the-project-something-to-remember).
For projects managed with Git, files must be tracked by Git before RSO reads
them. Ask your coding agent to check this if an expected file is missing.

After editing project files, run `use` again before asking another question.
In a newly opened PowerShell window, repeat the manual block so `$rsoProject`
is set again. You can change the question inside the quotation marks.

## If something goes wrong

| What you see | What to do |
| --- | --- |
| GitHub shows 404 | Check the repository address and sign in with an account granted access if it is private. |
| Python is not recognized, opens the Store, or reports an older version | Complete Python setup in step 3, reopen PowerShell, and check `python --version`. The installed RSO launcher needs the `python` command to work. |
| Git is not recognized | Install Git with command-line access enabled, then reopen PowerShell. |
| Python cannot open `install.py` | Open the extracted folder that actually contains `install.py`, then launch PowerShell from its address bar. |
| The installed RSO command cannot be found | Check that installation finished and printed a `command` path. Use that path if you chose a custom installation. |
| Doctor reports `"ready": false` | Read the failed check. Python, Git, and SQLite FTS5 must be available; include the doctor result when asking your agent for help. |
| A query says `unknown` or returns no useful evidence | Check that the selected folder contains the relevant text, check Git tracking if applicable, then rerun `use`. |
| A query says `budget_exhausted` | Ask the agent to inspect the run budget. Manual users can run the budget command below, then repeat their query. |

In the same PowerShell window where `$rsoProject` was set:

```powershell
& "$env:USERPROFILE\.local\bin\rso-context.cmd" run-budget --agent manual-user --path $rsoProject --set 8
```

For command details, read [MANUAL.md](MANUAL.md). For upgrades, custom install
locations, and removal, read [INSTALL.md](INSTALL.md).
