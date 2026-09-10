"""Isolated MCP SDK runtime. The CLI itself stays on the standard library."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import venv

from .mcp_contract import SDK_REQUIREMENT


RUNTIME_DIRNAME = "mcp-runtime"
REQUIREMENTS_NAME = "requirements-mcp.txt"


def program_root() -> Path:
    return Path(__file__).resolve().parents[2]


def requirements_path() -> Path:
    return program_root() / REQUIREMENTS_NAME


def runtime_dir() -> Path:
    configured = os.environ.get("RSO_MCP_RUNTIME")
    if configured:
        return Path(configured).expanduser().resolve()
    return program_root() / RUNTIME_DIRNAME


def runtime_python(directory: Path | None = None) -> Path | None:
    root = directory or runtime_dir()
    if os.name == "nt":
        candidate = root / "Scripts" / "python.exe"
    else:
        candidate = root / "bin" / "python"
    if candidate.is_file():
        return candidate
    return None


def _metadata_version(dist: str = "mcp") -> str | None:
    try:
        from importlib.metadata import version
    except ImportError:
        return None
    try:
        return version(dist)
    except Exception:
        return None


def current_sdk_status() -> dict[str, object]:
    version = _metadata_version("mcp")
    server_import = False
    try:
        from mcp.server import MCPServer  # noqa: F401
        server_import = True
    except ImportError:
        server_import = False
    usable = bool(version and version.startswith("2.") and server_import)
    return {
        "interpreter": sys.executable,
        "mcp_version": version,
        "mcpserver_importable": server_import,
        "usable": usable,
    }


def _runtime_sdk_version(python: Path) -> str | None:
    probe = (
        "from importlib.metadata import version\n"
        "print(version('mcp'))\n"
    )
    try:
        completed = subprocess.run(
            [str(python), "-c", probe],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip().splitlines()
    return value[-1] if value else None


def runtime_status() -> dict[str, object]:
    directory = runtime_dir()
    python = runtime_python(directory)
    version = _runtime_sdk_version(python) if python else None
    current = current_sdk_status()
    return {
        "schema": "rso-mcp-runtime/v1",
        "sdk_requirement": SDK_REQUIREMENT,
        "program_root": str(program_root()),
        "runtime_dir": str(directory),
        "runtime_python": str(python) if python else None,
        "runtime_mcp_version": version,
        "runtime_ready": bool(version and version.startswith("2.")),
        "current_interpreter": current,
        "requirements_file": str(requirements_path()),
        "requirements_present": requirements_path().is_file(),
    }


def install_runtime(directory: Path | None = None) -> dict[str, object]:
    """Create or refresh the isolated venv and pin the official MCP SDK there."""
    req = requirements_path()
    if not req.is_file():
        raise FileNotFoundError(f"Missing {REQUIREMENTS_NAME} next to the program")
    root = directory or runtime_dir()
    root.parent.mkdir(parents=True, exist_ok=True)
    venv.EnvBuilder(with_pip=True, clear=False, upgrade=False).create(root)
    python = runtime_python(root)
    if python is None:
        raise RuntimeError(f"venv created without a python executable: {root}")
    install = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--upgrade",
            "-r",
            str(req),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if install.returncode != 0:
        detail = (install.stderr or install.stdout or "").strip()
        raise RuntimeError(f"Failed to install {SDK_REQUIREMENT} into {root}: {detail}")
    version = _runtime_sdk_version(python)
    if not version or not version.startswith("2."):
        raise RuntimeError(f"Isolated runtime did not report MCP SDK 2.x; got {version!r}")
    return {
        "schema": "rso-mcp-runtime-install/v1",
        "runtime_dir": str(root),
        "runtime_python": str(python),
        "mcp_version": version,
        "sdk_requirement": SDK_REQUIREMENT,
    }


def serve_via_runtime(argv: list[str], *, directory: Path | None = None) -> int:
    """Run the same CLI argv under the isolated interpreter, inheriting stdio."""
    python = runtime_python(directory)
    if python is None:
        raise RuntimeError(
            "MCP SDK 2.x is not available. Run `rso-context mcp --install-runtime` first."
        )
    env = os.environ.copy()
    src = str(program_root() / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not existing else src + os.pathsep + existing
    env["RSO_MCP_IN_RUNTIME"] = "1"
    completed = subprocess.run(
        [str(python), "-X", "utf8", "-m", "rso_context", *argv],
        env=env,
        check=False,
    )
    return int(completed.returncode)
