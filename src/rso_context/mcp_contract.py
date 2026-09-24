"""Stdlib MCP tool contracts. Importable without the MCP SDK."""

from __future__ import annotations

from pathlib import Path
import os

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
        "name": "rso_check",
        "writes": True,
        "required": ("questions", "path", "agent"),
        "description": (
            "Answer 1-12 typed questions (claim, choice, value) from live ledger evidence "
            "and return rso-check/v1 with the spans behind each answer. No probabilities; "
            "a supported answer is not verification. Writes a run record and consumes one "
            "run-budget unit per call. path must stay under a launch --root."
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
    """Every profile directory this process can name, for the root refusal.

    Path.home() raises RuntimeError when the environment carries no home, which
    a host that replaces the child environment can produce. The environment
    variables are consulted as well so a partial environment still gets the
    refusal instead of silently losing it. A process that can name no home at
    all cannot recognise a profile, and the refusal has nothing to compare.
    """
    candidates: list[Path] = []
    try:
        candidates.append(Path.home())
    except RuntimeError:
        pass
    for name in ("USERPROFILE", "HOME"):
        value = os.environ.get(name)
        if value:
            candidates.append(Path(value))
    drive = os.environ.get("HOMEDRIVE")
    tail = os.environ.get("HOMEPATH")
    if drive and tail:
        candidates.append(Path(drive + tail))
    homes: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        homes.append(resolved)
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
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        bound.append(resolved)
    return bound


def resolve_launch_roots(
    roots: list[str] | tuple[str, ...] | None,
    *,
    cwd: str | Path | None = None,
) -> list[Path]:
    """Launch roots, falling back to the folder the host started the server in.

    A stdio host spawns the server with its working directory set to the project
    the user opened, so taking that directory when no --root is given lets one
    config entry serve every project instead of needing an edit per folder. This
    is still a launch-time bound: assert_bounded_root refuses a user profile or
    an unbounded home child either way, and nothing widens access at run time.
    """
    if roots:
        return bind_roots(roots)
    if cwd is not None:
        candidate = Path(cwd)
    else:
        try:
            candidate = Path.cwd()
        except OSError as error:
            raise ValueError(
                f"No --root was given and the working directory cannot be read: {error}"
            ) from error
    try:
        return bind_roots([str(candidate)])
    except ValueError as error:
        raise ValueError(
            f"No --root was given and the working directory cannot be a launch root: {error}"
        ) from error


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
    allowed = ", ".join(str(root) for root in roots) or "none"
    raise ValueError(f"path is outside the server's allowed roots: {resolved} (allowed: {allowed})")


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


def restrict_packet_to_roots(
    packet: dict,
    roots: list[Path],
    visible_project_ids: set[str],
) -> dict:
    """Drop evidence, claims, and search_order entries outside launch roots."""
    restricted = dict(packet)

    def keep_source(item: dict) -> bool:
        resolved = item.get("resolved_path")
        if resolved is not None and str(resolved).strip():
            return alias_is_visible(resolved, roots)
        project_id = str(item.get("project_id") or item.get("id") or "")
        return bool(project_id) and project_id in visible_project_ids

    restricted["evidence"] = [item for item in packet.get("evidence") or [] if keep_source(item)]
    restricted["claims"] = [
        item
        for item in packet.get("claims") or []
        if str(item.get("project_id") or "") in visible_project_ids
    ]
    restricted["graph_nodes"] = [
        item
        for item in packet.get("graph_nodes") or []
        if str(item.get("project_id") or "") in visible_project_ids
    ]
    restricted["search_order"] = [
        item
        for item in packet.get("search_order") or []
        if str(item.get("id") or "") in visible_project_ids
    ]
    restricted["validations"] = [
        item
        for item in packet.get("validations") or []
        if not item.get("project_id") or str(item.get("project_id")) in visible_project_ids
    ]
    return restricted


def require_agent(agent: str) -> str:
    value = str(agent or "").strip()
    if not value:
        raise ValueError("agent is required and is attribution, not authentication")
    return value
