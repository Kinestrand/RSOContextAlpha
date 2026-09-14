#!/usr/bin/env python3
"""Install the explicit release manifest without copying a user's index."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import subprocess
import sys
import uuid


REQUIRED = {"LICENSE", "NOTICE", "install.py", "rso-context", "rso-context.ps1", "RELEASE-FILES.txt",
            "src/rso_context/__main__.py"}
EXCLUDED = {".git", ".rso-context", ".agent-coordination", "__pycache__",
            "private", "graft", "output", "outputs", "backup", "backups"}


def release_files(source: Path) -> list[Path]:
    """Validate the whole manifest before writing any installed file."""
    entries = []
    seen = set()
    for line in (source / "RELEASE-FILES.txt").read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        path = PurePosixPath(name)
        if (path.is_absolute() or "\\" in name or ":" in name
                or any(part in {".", ".."} for part in name.split("/"))
                or any(part.casefold() in EXCLUDED for part in path.parts)
                or any(ch in name for ch in "*?[]\r\n")
                or path.name.casefold().startswith(".env")
                or path.suffix.casefold() in {".db", ".sqlite", ".sqlite3", ".pyc"}
                or ".rso-backup-" in name.casefold()):
            raise ValueError(f"Unsafe release manifest entry: {name!r}")
        if name.casefold() in seen:
            raise ValueError(f"Duplicate release manifest entry: {name}")
        seen.add(name.casefold())
        target = source.joinpath(*path.parts)
        if not target.resolve().is_relative_to(source.resolve()) or target.is_symlink():
            raise ValueError(f"Release entry escapes source: {name}")
        if not target.is_file():
            raise ValueError(f"Release entry is missing or is not a file: {name}")
        entries.append(Path(*path.parts))
    missing = REQUIRED - {p.as_posix() for p in entries}
    if missing:
        raise ValueError(f"Required release files missing: {', '.join(sorted(missing))}")
    return entries


def check_prerequisites() -> None:
    if sys.version_info < (3, 11):
        raise ValueError("Python 3.11 or newer is required.")
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("Git must be installed and available on PATH.") from error


def install(source: Path, prefix: Path, *, platform: str | None = None) -> dict:
    check_prerequisites()
    source, prefix = source.resolve(), prefix.expanduser().absolute()
    files = release_files(source)
    windows = (platform or sys.platform) == "win32"
    program = prefix / ("RSOContextAlpha" if windows else "share/RSOContextAlpha")
    command = prefix / "bin" / ("rso-context.cmd" if windows else "rso-context")
    destinations = [program / relative for relative in files] + [command]
    bash_command = prefix / "bin" / "rso-context" if windows else None
    if bash_command is not None:
        destinations.append(bash_command)
    for target in destinations:
        if not target.resolve().is_relative_to(prefix.resolve()) or target.is_symlink():
            raise ValueError(f"Install destination escapes prefix or is a symlink: {target}")
        if target.exists() and not target.is_file():
            raise ValueError(f"Install destination is not a file: {target}")
    backups = []

    def write(target: Path, content: bytes):
        if target.exists() and target.read_bytes() == content:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = target.with_name(target.name + ".rso-backup-" + uuid.uuid4().hex)
            shutil.copy2(target, backup)
            backups.append(str(backup))
        target.write_bytes(content)

    for relative in files:
        original, target = source / relative, program / relative
        if original.resolve() != target.resolve():
            write(target, original.read_bytes())
    if windows:
        source_path = str(program / "src").replace("%", "%%")
        program_path = str(program).replace('%', '%%')
        pointer = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\n'
            'set "RSO_ROOT=' + program_path + '"\r\n'
            'set "PYTHONPATH=' + source_path + ';%PYTHONPATH%"\r\n'
            'set "RSO_PY=python"\r\n'
            'if /I "%~1"=="mcp" (\r\n'
            '  if exist "%RSO_ROOT%\\mcp-runtime\\Scripts\\python.exe" (\r\n'
            '    set "RSO_PY=%RSO_ROOT%\\mcp-runtime\\Scripts\\python.exe"\r\n'
            '    set "RSO_MCP_IN_RUNTIME=1"\r\n'
            '  )\r\n'
            ')\r\n'
            '"%RSO_PY%" -X utf8 -m rso_context %*\r\n'
            'exit /b %ERRORLEVEL%\r\n'
        )
    else:
        pointer = '#!/bin/sh\nexec /bin/sh ' + shlex.quote(str(program / "rso-context")) + ' "$@"\n'
    write(command, pointer.encode("utf-8"))
    if bash_command is not None:
        bash_pointer = (
            '#!/bin/sh\n'
            'PYTHONPATH=' + shlex.quote(str(program / "src")) + '"${PYTHONPATH:+;$PYTHONPATH}"\n'
            'export PYTHONPATH\n'
            'exec python -X utf8 -m rso_context "$@"\n'
        )
        write(bash_command, bash_pointer.encode("utf-8"))
        bash_command.chmod(bash_command.stat().st_mode | 0o111)
    if not windows:
        command.chmod(command.stat().st_mode | 0o111)
        launcher = program / "rso-context"
        launcher.chmod(launcher.stat().st_mode | 0o111)
    return {"program": str(program), "command": str(command), "backups": backups,
            "path_guidance": f"Add {command.parent} to PATH if it is not already present. Shell profiles were not changed."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local",
                        help="Base containing bin and the program directory (default: ~/.local)")
    args = parser.parse_args()
    try:
        result = install(Path(__file__).resolve().parent, args.prefix)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Install failed: {error}\n")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
