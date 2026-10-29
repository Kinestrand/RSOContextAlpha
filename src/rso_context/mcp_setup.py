"""Write and remove RSO-owned MCP client entries. Stdlib only."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys

from .config import default_home, git_executable
from .mcp_contract import bind_roots
from .mcp_runtime import program_root, runtime_python


SERVER_NAME = "rso-context"
CLIENTS = (
    "codex",
    "claude-code",
    "claude-desktop",
    "cursor",
    "windsurf",
    "gemini",
    "antigravity",
    "qwen",
    "opencode",
)
# Hosts whose config keys servers under "mcpServers" with command/args/env.
JSON_CLIENTS = ("claude-code", "claude-desktop", "cursor", "windsurf", "gemini", "antigravity", "qwen")
GOOGLE_CLIENTS = ("gemini", "antigravity")


def _as_roots(roots: str | Path | list | tuple | None) -> list[str]:
    if roots is None:
        return []
    if isinstance(roots, (str, Path)):
        return [str(roots)]
    return [str(item) for item in roots]


def mcp_launch(roots: list[Path] | None = None, *, strict_roots: bool = False) -> dict[str, object]:
    """Stdio launch spec shared by every supported host and the generic stdio host.

    With no roots the entry is portable: the server binds the host's working
    directory when that is a bounded folder and otherwise takes each project
    folder from the tool call, so one entry serves every project.
    """
    python = runtime_python() or Path(sys.executable)
    src = program_root() / "src"
    env = {
        "PYTHONPATH": str(src),
        "RSO_MCP_IN_RUNTIME": "1",
        "RSO_CONTEXT_HOME": str(default_home()),
    }
    env.update(_spawn_env_passthrough())
    args = ["-X", "utf8", "-m", "rso_context", "mcp"]
    for root in roots or []:
        args.extend(["--root", str(Path(root).resolve())])
    if strict_roots:
        args.append("--strict-roots")
    return {
        "command": str(python),
        "args": args,
        "env": env,
    }


def _spawn_env_passthrough() -> dict[str, str]:
    """Variables a host must forward for the server to start at all.

    Some hosts replace the child environment with the config's env block instead
    of adding to it. On Windows a child without SystemRoot cannot load winsock,
    so the server dies before the handshake and the host reports a request
    timeout rather than a crash. RSO_CONTEXT_HOME above covers the other half by
    removing the need for a home directory. RSO_GIT keeps ingest and project
    identity working when PATH loses Git, and the profile variable keeps the
    profile refusal working when the host drops it.
    """
    env: dict[str, str] = {}
    git = git_executable()
    if git != "git":
        env["RSO_GIT"] = git
    if os.name != "nt":
        home = os.environ.get("HOME")
        if home:
            env["HOME"] = home
        return env
    profile = os.environ.get("USERPROFILE")
    if profile:
        env["USERPROFILE"] = profile
    system_root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT")
    if not system_root:
        return env
    root = Path(system_root)
    env["SystemRoot"] = str(root)
    env["PATH"] = os.pathsep.join([str(root / "System32"), str(root)])
    return env


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


def default_claude_desktop_config() -> Path:
    """Claude Desktop app config, separate from Claude Code's ~/.claude.json."""
    override = os.environ.get("RSO_MCP_CLAUDE_DESKTOP_CONFIG")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "Claude" / "claude_desktop_config.json"


def default_cursor_config() -> Path:
    override = os.environ.get("RSO_MCP_CURSOR_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cursor" / "mcp.json"


def default_windsurf_config() -> Path:
    override = os.environ.get("RSO_MCP_WINDSURF_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codeium" / "windsurf" / "mcp_config.json"


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


def default_qwen_config() -> Path:
    """Qwen Code user settings, a Gemini CLI fork with its own folder."""
    override = os.environ.get("RSO_MCP_QWEN_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".qwen" / "settings.json"


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
        previous = servers.get(SERVER_NAME)
        entry = dict(previous) if isinstance(previous, dict) else {}
        entry.update(
            {
                "type": "local",
                "enabled": True,
                "command": [launch["command"], *list(launch["args"])],
                "environment": dict(launch["env"]),
            }
        )
        servers[SERVER_NAME] = entry
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


def _strip_toml_tables(
    text: str,
    header: str,
    *,
    keep_prefixes: tuple[str, ...] = (),
) -> tuple[str, str]:
    """Remove header and its subtables; return (rest, lifted subtables).

    Subtables under a keep prefix (the user's tool approvals under .tools) are
    lifted out and returned so a rewrite can put them back after the
    regenerated entry instead of discarding them.
    """
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    lifted: list[str] = []
    mode = "keep"
    for line in lines:
        name = _toml_table_name(line)
        if name is not None:
            if name == header or name.startswith(header + "."):
                keep = any(name == prefix or name.startswith(prefix + ".") for prefix in keep_prefixes)
                mode = "lift" if keep else "drop"
            else:
                mode = "keep"
        if mode == "keep":
            kept.append(line)
        elif mode == "lift":
            lifted.append(line)
    rest = "".join(kept).rstrip() + ("\n" if kept else "")
    extra = "".join(lifted).strip()
    return rest, (extra + "\n" if extra else "")


def _write_codex(path: Path, launch: dict[str, object] | None) -> dict[str, object]:
    original = path.read_text(encoding="utf-8") if path.is_file() else ""
    header = f"mcp_servers.{SERVER_NAME}"
    keep = (f"{header}.tools",) if launch is not None else ()
    body, lifted = _strip_toml_tables(original, header, keep_prefixes=keep)
    if launch is not None:
        body = body.rstrip() + ("\n\n" if body.strip() else "") + _toml_block(launch)
        if lifted:
            body = body.rstrip() + "\n\n" + lifted
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
        previous = servers.get(SERVER_NAME)
        entry = dict(previous) if isinstance(previous, dict) else {}
        entry.update(
            {
                "command": launch["command"],
                "args": list(launch["args"]),
                "env": dict(launch["env"]),
            }
        )
        servers[SERVER_NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"format": "json", "path": str(path), "wrote": launch is not None}



def default_config(client: str) -> Path:
    if client not in CLIENTS:
        raise ValueError(f"Unsupported client {client!r}; supported: {', '.join(CLIENTS)}")
    return {
        "codex": default_codex_config,
        "claude-code": default_claude_config,
        "claude-desktop": default_claude_desktop_config,
        "cursor": default_cursor_config,
        "windsurf": default_windsurf_config,
        "gemini": default_gemini_config,
        "antigravity": default_antigravity_config,
        "qwen": default_qwen_config,
        "opencode": default_opencode_config,
    }[client]()


def host_is_present(client: str) -> bool:
    """True when the host's config file or its folder already exists."""
    path = default_config(client)
    return path.is_file() or path.parent.is_dir()


def _write_client(client: str, path: Path, launch: dict[str, object] | None) -> dict[str, object]:
    if client == "codex":
        return _write_codex(path, launch)
    if client == "opencode":
        return _write_opencode(path, launch)
    written = _write_json_mcp(path, launch)
    written["host"] = client
    return written


def setup_client(
    client: str,
    roots: str | Path | list | tuple | None = None,
    *,
    config: str | Path | None = None,
    strict_roots: bool = False,
) -> dict[str, object]:
    """Write the RSO entry for one host. No --root is pinned unless roots are given."""
    if client not in CLIENTS:
        raise ValueError(f"Unsupported client {client!r}; supported: {', '.join(CLIENTS)}")
    requested = _as_roots(roots)
    bound = bind_roots(requested) if requested else []
    if strict_roots and not bound:
        raise ValueError("--strict-roots setup needs at least one --root")
    launch = mcp_launch(bound, strict_roots=strict_roots)
    path = Path(config) if config else default_config(client)
    written = _write_client(client, path, launch)
    return {
        "schema": "rso-mcp-setup/v1",
        "action": "setup",
        "client": client,
        "server": SERVER_NAME,
        "roots": [str(item) for item in bound],
        "access": "strict" if strict_roots else "open",
        "launch": launch,
        **written,
        "note": (
            "Unrelated client settings were left in place. Restart the host to load the entry. "
            "Tool discovery is not automatic use: call rso_use with the project folder first. "
            "rso_use is an MCP tool the host lists, not a shell command; "
            "from a shell the equivalent is rso-context use."
        ),
    }


def setup_all(
    roots: str | Path | list | tuple | None = None,
    *,
    strict_roots: bool = False,
) -> dict[str, object]:
    """Configure every supported host that is installed; skip the rest."""
    results = []
    skipped = []
    for client in CLIENTS:
        if host_is_present(client):
            results.append(setup_client(client, roots, strict_roots=strict_roots))
        else:
            skipped.append({"client": client, "config": str(default_config(client))})
    return {"schema": "rso-mcp-setup-all/v1", "results": results, "skipped": skipped}


def remove_client(client: str, *, config: str | Path | None = None) -> dict[str, object]:
    if client not in CLIENTS:
        raise ValueError(f"Unsupported client {client!r}; supported: {', '.join(CLIENTS)}")
    path = Path(config) if config else default_config(client)
    if path.is_file():
        written = _write_client(client, path, None)
    else:
        written = {"path": str(path), "wrote": False}
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


def _opencode_has_entry(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        data = json.loads(_strip_jsonc_comments(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return False
    servers = data.get("mcp") if isinstance(data, dict) else None
    return isinstance(servers, dict) and SERVER_NAME in servers


def inspect_clients() -> dict[str, object]:
    """Read-only. Does not create or edit host configs."""
    report: dict[str, object] = {}
    for client in CLIENTS:
        path = default_config(client)
        if client == "codex":
            present = _codex_has_entry(path)
        elif client == "opencode":
            present = _opencode_has_entry(path)
        else:
            present = _claude_has_entry(path)
        report[client] = {"config": str(path), "exists": path.is_file(), "rso_context": present}
    report["other_clients"] = (
        "Any stdio MCP host can launch the command below; that is not a tested compatibility claim."
    )
    report["stdio"] = {
        "command": str(runtime_python() or Path(sys.executable)),
        "args": ["-X", "utf8", "-m", "rso_context", "mcp"],
        "env": {
            "PYTHONPATH": str(program_root() / "src"),
            "RSO_MCP_IN_RUNTIME": "1",
        },
    }
    return report
