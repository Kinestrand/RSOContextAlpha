"""Stdlib MCP tool contracts. Importable without the MCP SDK."""

from __future__ import annotations

from pathlib import Path

from .scope import path_is_parent


PROTOCOL_ID = "rso-mcp-tools/v1"
SERVER_NAME = "rso-context"
SDK_REQUIREMENT = "mcp==2.2.0"
SDK_SPEC = "https://modelcontextprotocol.io/docs/learn/architecture"

# Launch-time only. Tools must not accept a database path or arbitrary file read.
LAUNCH_BINDINGS = ("db", "root")

UNBOUNDED_HOME_CHILDREN = {
    "desktop",
    "documents",
    "downloads",
    "onedrive",
}

TOOLS = (
    {
        "name": "rso_use",
        "writes": True,
        "required": ("path", "agent"),
        "description": (
            "Register and incrementally ingest a bounded workspace, then return a resume packet. "
            "This writes ingest state. path must stay under a launch --root. "
            "agent is attribution, not authentication."
        ),
    },
    {
        "name": "rso_query",
        "writes": True,
        "required": ("query", "path", "agent"),
        "description": (
            "Return a compact evidence packet (rso-mcp-packet/v1) for an already-registered project. "
            "This writes a run record and consumes run budget. "
            "path must stay under a launch --root. Database path is not a tool argument. "
            "Omitted spans are recovered with rso_expand, not by guessing."
        ),
    },
    {
        "name": "rso_resume",
        "writes": False,
        "required": ("path", "agent"),
        "description": (
            "Return a compact resume packet for an already-registered project. "
            "Does not ingest. path must stay under a launch --root."
        ),
    },
    {
        "name": "rso_explain",
        "writes": False,
        "required": ("packet_hash",),
        "description": (
            "Return a saved query packet from the launch-bound ledger by packet_hash. "
            "Read-only against that database."
        ),
    },
    {
        "name": "rso_expand",
        "writes": False,
        "required": ("ref",),
        "description": (
            "Retrieve omitted evidence by a stable expansion reference "
            "(project, source, expected hash, line range). "
            "Bounded and stale when the live file no longer matches. "
            "Not a generic path read."
        ),
    },
)

TOOL_NAMES = tuple(item["name"] for item in TOOLS)


def tool_names() -> tuple[str, ...]:
    return TOOL_NAMES


def _home_paths() -> list[Path]:
    homes: list[Path] = []
    for candidate in (Path.home(),):
        try:
            homes.append(candidate.expanduser().resolve())
        except OSError:
            continue
    return homes


def assert_bounded_root(path: str | Path) -> Path:
    """Refuse a user profile or a well-known unbounded home child as a launch root."""
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError as error:
        raise ValueError(f"Cannot resolve MCP root: {path}") from error
    if not resolved.is_dir():
        raise ValueError(f"MCP root is not a directory: {resolved}")
    name = resolved.name.casefold()
    for home in _home_paths():
        if resolved == home:
            raise ValueError("Refusing to bind a user profile as an MCP root")
        if resolved.parent == home and name in UNBOUNDED_HOME_CHILDREN:
            raise ValueError(f"Refusing to bind unbounded folder as an MCP root: {resolved}")
    return resolved


def bind_roots(roots: list[str] | tuple[str, ...]) -> list[Path]:
    if not roots:
        raise ValueError("rso-context mcp requires one or more --root folders")
    bound: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        resolved = assert_bounded_root(root)
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        bound.append(resolved)
    return bound


def path_is_within_root(root: str | Path, path: str | Path) -> bool:
    try:
        root_path = Path(root).expanduser().resolve()
        child = Path(path).expanduser().resolve()
    except OSError:
        return False
    if root_path == child:
        return True
    return path_is_parent(root_path, child)


def resolve_tool_path(path: str | Path, roots: list[Path]) -> Path:
    """Resolve a tool path and require it to stay inside a launch root."""
    if not str(path).strip():
        raise ValueError("path is required")
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError as error:
        raise ValueError(f"Cannot resolve path: {path}") from error
    for root in roots:
        if path_is_within_root(root, resolved):
            return resolved
    raise ValueError("path is outside the server's allowed roots")


def resolve_workspace_path(
    path: str | Path,
    roots: list[Path],
    *,
    database_path: str | Path | None = None,
) -> Path:
    """Workspace path for use/query/resume: a directory under a launch root, never the ledger file."""
    resolved = resolve_tool_path(path, roots)
    if database_path is not None:
        try:
            db = Path(database_path).expanduser().resolve()
        except OSError as error:
            raise ValueError(f"Cannot resolve launch database: {database_path}") from error
        if resolved == db:
            raise ValueError("path must not be the launch database")
    if not resolved.is_dir():
        raise ValueError(f"path is not a directory: {resolved}")
    return resolved


def alias_is_visible(resolved_path: str | Path | None, roots: list[Path]) -> bool:
    if resolved_path is None or not str(resolved_path).strip():
        return False
    return any(path_is_within_root(root, resolved_path) for root in roots)


def require_agent(agent: str) -> str:
    value = str(agent or "").strip()
    if not value:
        raise ValueError("agent is required and is attribution, not authentication")
    return value
