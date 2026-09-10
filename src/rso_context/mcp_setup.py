"""Write and remove RSO-owned MCP client entries. Stdlib only."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys

from .mcp_contract import bind_roots
from .mcp_runtime import program_root, runtime_python


SERVER_NAME = "rso-context"
CLIENTS = ("codex", "claude-code")


def mcp_launch(root: Path) -> dict[str, object]:
    """Stdio launch spec shared by Codex, Claude Code, and the documented generic host."""
    python = runtime_python() or Path(sys.executable)
    src = program_root() / "src"
    return {
        "command": str(python),
        "args": ["-X", "utf8", "-m", "rso_context", "mcp", "--root", str(root.resolve())],
        "env": {
            "PYTHONPATH": str(src),
            "RSO_MCP_IN_RUNTIME": "1",
        },
    }


def default_codex_config() -> Path:
    override = os.environ.get("RSO_MCP_CODEX_CONFIG")
    if override:
        return Path(override).expanduser()
    home = os.environ.get("CODEX_HOME")
    if home:
        return Path(home).expanduser() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def default_claude_config() -> Path:
    override = os.environ.get("RSO_MCP_CLAUDE_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude.json"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _toml_block(launch: dict[str, object]) -> str:
    args = ", ".join(_toml_string(str(item)) for item in launch["args"])
    env_lines = "\n".join(
        f"{key} = {_toml_string(str(value))}" for key, value in launch["env"].items()
    )
    return (
        f"[mcp_servers.{SERVER_NAME}]\n"
        f"command = {_toml_string(str(launch['command']))}\n"
        f"args = [{args}]\n"
        f"\n[mcp_servers.{SERVER_NAME}.env]\n"
        f"{env_lines}\n"
    )


def _toml_table_name(line: str) -> str | None:
    """Return a table name, including headers with a trailing comment."""
    stripped = line.strip()
    if not stripped.startswith("["):
        return None
    close = stripped.find("]")
    if close < 1:
        return None
    rest = stripped[close + 1 :].strip()
    if rest and not rest.startswith("#"):
        return None
    return stripped[1:close].strip()


def _strip_toml_tables(text: str, header: str) -> str:
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    skipping = False
    for line in lines:
        name = _toml_table_name(line)
        if name is not None:
            skipping = name == header or name.startswith(header + ".")
        if not skipping:
            kept.append(line)
    return "".join(kept).rstrip() + ("\n" if kept else "")


def _write_codex(path: Path, launch: dict[str, object] | None) -> dict[str, object]:
    original = path.read_text(encoding="utf-8") if path.is_file() else ""
    body = _strip_toml_tables(original, f"mcp_servers.{SERVER_NAME}")
    if launch is not None:
        body = body.rstrip() + ("\n\n" if body.strip() else "") + _toml_block(launch)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if body.endswith("\n") or not body else body + "\n", encoding="utf-8")
    return {"format": "toml", "path": str(path), "wrote": launch is not None}


def _write_claude(path: Path, launch: dict[str, object] | None) -> dict[str, object]:
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Claude config must be a JSON object")
    else:
        data = {}
    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
        data["mcpServers"] = servers
    if not isinstance(servers, dict):
        raise ValueError("mcpServers must be a JSON object")
    if launch is None:
        servers.pop(SERVER_NAME, None)
    else:
        servers[SERVER_NAME] = {
            "command": launch["command"],
            "args": list(launch["args"]),
            "env": dict(launch["env"]),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"format": "json", "path": str(path), "wrote": launch is not None}


def setup_client(client: str, root: str | Path, *, config: str | Path | None = None) -> dict[str, object]:
    if client not in CLIENTS:
        raise ValueError(f"Unsupported client {client!r}; supported: {', '.join(CLIENTS)}")
    bound = bind_roots([str(root)])[0]
    launch = mcp_launch(bound)
    if client == "codex":
        path = Path(config) if config else default_codex_config()
        written = _write_codex(path, launch)
    else:
        path = Path(config) if config else default_claude_config()
        written = _write_claude(path, launch)
    return {
        "schema": "rso-mcp-setup/v1",
        "action": "setup",
        "client": client,
        "server": SERVER_NAME,
        "root": str(bound),
        "launch": launch,
        **written,
        "note": "Unrelated client settings were left in place. Tool discovery is not automatic use.",
    }


def remove_client(client: str, *, config: str | Path | None = None) -> dict[str, object]:
    if client not in CLIENTS:
        raise ValueError(f"Unsupported client {client!r}; supported: {', '.join(CLIENTS)}")
    if client == "codex":
        path = Path(config) if config else default_codex_config()
        written = _write_codex(path, None)
    else:
        path = Path(config) if config else default_claude_config()
        written = _write_claude(path, None)
    return {
        "schema": "rso-mcp-setup/v1",
        "action": "remove",
        "client": client,
        "server": SERVER_NAME,
        **written,
        "note": "Only the RSO-owned rso-context entry was removed.",
    }


def _codex_has_entry(path: Path) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    return bool(
        re.search(
            rf"^\[mcp_servers\.{re.escape(SERVER_NAME)}\](?:\s*#.*)?\s*$",
            text,
            re.MULTILINE,
        )
    )


def _claude_has_entry(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return isinstance(servers, dict) and SERVER_NAME in servers


def inspect_clients() -> dict[str, object]:
    """Read-only. Does not create or edit host configs."""
    codex = default_codex_config()
    claude = default_claude_config()
    return {
        "codex": {
            "config": str(codex),
            "exists": codex.is_file(),
            "rso_context": _codex_has_entry(codex),
        },
        "claude-code": {
            "config": str(claude),
            "exists": claude.is_file(),
            "rso_context": _claude_has_entry(claude),
        },
        "other_clients": "Documented stdio launch only; not a compatibility claim.",
        "stdio": {
            "command": str(runtime_python() or Path(sys.executable)),
            "args": ["-X", "utf8", "-m", "rso_context", "mcp", "--root", "<bounded-folder>"],
            "env": {
                "PYTHONPATH": str(program_root() / "src"),
                "RSO_MCP_IN_RUNTIME": "1",
            },
        },
    }
