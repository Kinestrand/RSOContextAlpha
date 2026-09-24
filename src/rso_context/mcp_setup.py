"""Write and remove RSO-owned MCP client entries. Stdlib only."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys

from .config import default_home
from .mcp_contract import bind_roots
from .mcp_runtime import program_root, runtime_python


SERVER_NAME = "rso-context"
CLIENTS = ("codex", "claude-code", "gemini", "antigravity", "opencode")
JSON_CLIENTS = ("claude-code", "gemini", "antigravity")
GOOGLE_CLIENTS = ("gemini", "antigravity")


def mcp_launch(root: Path) -> dict[str, object]:
    """Stdio launch spec shared by Codex, Claude Code, and the documented generic host."""
    python = runtime_python() or Path(sys.executable)
    src = program_root() / "src"
    env = {
        "PYTHONPATH": str(src),
        "RSO_MCP_IN_RUNTIME": "1",
        "RSO_CONTEXT_HOME": str(default_home()),
    }
    env.update(_spawn_env_passthrough())
    return {
        "command": str(python),
        "args": ["-X", "utf8", "-m", "rso_context", "mcp", "--root", str(root.resolve())],
        "env": env,
    }


def _spawn_env_passthrough() -> dict[str, str]:
    """Variables a host must forward for the server to start at all.

    Some hosts replace the child environment with the config's env block instead
    of adding to it. On Windows a child without SystemRoot cannot load winsock,
    so the server dies before the handshake and the host reports a request
    timeout rather than a crash. RSO_CONTEXT_HOME above covers the other half by
    removing the need for a home directory.
    """
    if os.name != "nt":
        return {}
    system_root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT")
    if not system_root:
        return {}
    root = Path(system_root)
    return {
        "SystemRoot": str(root),
        "PATH": os.pathsep.join([str(root / "System32"), str(root)]),
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


def default_gemini_config() -> Path:
    """Gemini CLI settings path."""
    override = os.environ.get("RSO_MCP_GEMINI_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".gemini" / "settings.json"


def default_antigravity_config() -> Path:
    """Antigravity global MCP config, separate from Gemini CLI settings."""
    override = os.environ.get("RSO_MCP_ANTIGRAVITY_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".gemini" / "config" / "mcp_config.json"


def default_opencode_config() -> Path:
    """OpenCode global config. XDG on every platform, including Windows."""
    override = os.environ.get("RSO_MCP_OPENCODE_CONFIG")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    jsonc = root / "opencode" / "opencode.jsonc"
    if jsonc.is_file():
        return jsonc
    return root / "opencode" / "opencode.json"


def _write_opencode(path: Path, launch: dict[str, object] | None) -> dict[str, object]:
    """OpenCode keys servers under "mcp" with a command list, not "mcpServers"."""
    if path.is_file():
        data = json.loads(_strip_jsonc_comments(path.read_text(encoding="utf-8")))
        if not isinstance(data, dict):
            raise ValueError("MCP host config must be a JSON object")
    else:
        data = {}
    servers = data.get("mcp")
    if servers is None:
        servers = {}
        data["mcp"] = servers
    if not isinstance(servers, dict):
        raise ValueError("mcp must be a JSON object")
    if launch is None:
        servers.pop(SERVER_NAME, None)
    else:
        servers[SERVER_NAME] = {
            "type": "local",
            "enabled": True,
            "command": [launch["command"], *list(launch["args"])],
            "environment": dict(launch["env"]),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"format": "json", "path": str(path), "wrote": launch is not None}


def _strip_jsonc_comments(text: str) -> str:
    """Drop // and /* */ comments outside strings so a .jsonc file parses."""
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == '"':
            end = index + 1
            while end < length:
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == '"':
                    end += 1
                    break
                end += 1
            out.append(text[index:end])
            index = end
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end == -1 else end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


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


def _write_json_mcp(path: Path, launch: dict[str, object] | None) -> dict[str, object]:
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("MCP host config must be a JSON object")
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



def _write_claude(path: Path, launch: dict[str, object] | None) -> dict[str, object]:
    return _write_json_mcp(path, launch)


def setup_client(client: str, root: str | Path, *, config: str | Path | None = None) -> dict[str, object]:
    if client not in CLIENTS:
        raise ValueError(f"Unsupported client {client!r}; supported: {', '.join(CLIENTS)}")
    bound = bind_roots([str(root)])[0]
    launch = mcp_launch(bound)
    if client == "codex":
        path = Path(config) if config else default_codex_config()
        written = _write_codex(path, launch)
    elif client == "opencode":
        path = Path(config) if config else default_opencode_config()
        written = _write_opencode(path, launch)
    elif client in GOOGLE_CLIENTS:
        default = default_gemini_config if client == "gemini" else default_antigravity_config
        path = Path(config) if config else default()
        written = _write_json_mcp(path, launch)
        written["host"] = client
    else:
        path = Path(config) if config else default_claude_config()
        written = _write_json_mcp(path, launch)
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
    elif client == "opencode":
        path = Path(config) if config else default_opencode_config()
        written = _write_opencode(path, None)
    elif client in GOOGLE_CLIENTS:
        default = default_gemini_config if client == "gemini" else default_antigravity_config
        path = Path(config) if config else default()
        written = _write_json_mcp(path, None)
        written["host"] = client
    else:
        path = Path(config) if config else default_claude_config()
        written = _write_json_mcp(path, None)
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
    gemini = default_gemini_config()
    antigravity = default_antigravity_config()
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
        "gemini": {
            "config": str(gemini),
            "exists": gemini.is_file(),
            "rso_context": _claude_has_entry(gemini),
        },
        "antigravity": {
            "config": str(antigravity),
            "exists": antigravity.is_file(),
            "rso_context": _claude_has_entry(antigravity),
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
