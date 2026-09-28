"""MCP stdio adapter. Imported only when the isolated SDK runtime is present."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from . import __version__
from .db import Database, SchemaVersionError
from .ingest import ingest_project
from .mcp_contract import (
    ACCESS_OPEN,
    ACCESS_STRICT,
    PROTOCOL_ID,
    TOOL_NAMES,
    alias_is_visible,
    bind_roots,
    require_agent,
    resolve_workspace_path,
    restrict_packet_to_roots,
)
from .check import CHECK_SCHEMA, check_questions, explain_check, fit_check
from .compact import compact_packet, expand_reference
from .config import Limits
from .query import query_context
from .resume import resume_context


INSTRUCTIONS = (
    "Local RSO Context evidence ledger over bounded folders. "
    "Pass the absolute path of the project folder you are working in as path. "
    "Call rso_use on a folder once before rso_query, rso_check, or rso_resume. "
    "path must be a bounded project folder, never a user profile, Documents, "
    "Downloads, a drive root, or a system folder; under --strict-roots it must "
    "also sit under a launch --root. "
    "Tools do not accept a database path. "
    "agent is attribution, not authority; use your own agent name. "
    "rso_use and rso_query write to the ledger. "
    "rso_query returns a compact packet; rso_expand recovers omitted evidence. "
    "rso_check answers typed questions with evidence statuses, not probabilities; it also writes. "
    "Do not treat symbolic checks as named verification."
)


def _tool_error():
    try:
        from mcp.server.mcpserver.exceptions import ToolError
        return ToolError
    except ImportError:
        return ValueError


def _database(db_path: str) -> Database:
    database = Database(db_path)
    database.initialize()
    return database


def _raise_tool(error_type, error) -> None:
    raise error_type(str(error).strip() or type(error).__name__) from error


def _invoke(error_type, fn):
    try:
        return fn()
    except error_type:
        raise
    except (ValueError, OSError, KeyError, TypeError, SchemaVersionError, sqlite3.Error, RuntimeError) as error:
        _raise_tool(error_type, error)
    except Exception as error:  # noqa: BLE001 - an agent must see why, not a generic failure
        raise error_type(f"{type(error).__name__}: {error}") from error


def _payload_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def call_tool_result(payload: dict):
    """Text-only tool result. Avoids duplicating the packet in structuredContent."""
    from mcp_types import CallToolResult, TextContent

    return CallToolResult(content=[TextContent(type="text", text=_payload_json(payload))])


def tool_result_wire_bytes(payload: dict) -> int:
    """JSON-RPC tools/call result size for a text-only CallToolResult."""
    result = call_tool_result(payload)
    dumped = json.loads(result.model_dump_json(by_alias=True, exclude_none=True))
    message = {"jsonrpc": "2.0", "id": 1, "result": dumped}
    return len(json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def fit_to_wire_budget(build, byte_budget: int) -> dict:
    """Largest build(inner) whose MCP wire result fits byte_budget.

    The wire result is larger than the packet's own byte count because JSON
    escaping inflates the text, and packet size moves in whole evidence spans,
    so the inner budget is found by binary search. Stepping down by the
    overshoot stalled on a packet a few bytes over and fell back to an empty
    256-byte packet, which agents read as RSO finding nothing.
    """
    budget = max(256, int(byte_budget))
    payload = build(budget)
    if tool_result_wire_bytes(payload) <= budget:
        return payload
    low, high = 256, budget - 1
    best = None
    while low <= high:
        inner = (low + high) // 2
        candidate = build(inner)
        if tool_result_wire_bytes(candidate) <= budget:
            best = candidate
            low = inner + 1
        else:
            high = inner - 1
    return best if best is not None else build(256)


def fit_payload_to_wire_budget(source: dict, *, byte_budget: int, compact: bool = True) -> dict:
    """Shrink a compact packet until the MCP wire result is within byte_budget."""
    if not compact:
        payload = dict(source)
        if tool_result_wire_bytes(payload) <= max(256, int(byte_budget)):
            return payload
    return fit_to_wire_budget(lambda inner: compact_packet(source, byte_budget=inner), byte_budget)


def project_ids_visible_to_roots(database: Database, roots: list[Path]) -> set[str]:
    """Projects with at least one alias path under a launch root."""
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT project_id, resolved_path FROM project_aliases "
            "WHERE resolved_path IS NOT NULL"
        ).fetchall()
    visible: set[str] = set()
    for row in rows:
        if alias_is_visible(row["resolved_path"], roots):
            visible.add(str(row["project_id"]))
    return visible


def bound_packet(database: Database, packet: dict, roots: list[Path]) -> dict:
    """MCP responses may only cite sources under launch roots."""
    return restrict_packet_to_roots(packet, roots, project_ids_visible_to_roots(database, roots))


def explain_packet(database: Database, packet_hash: str, roots: list[Path]) -> dict:
    """Return a saved run packet only when its project alias sits under a launch root."""
    digest = str(packet_hash or "").strip()
    if not digest:
        raise ValueError("packet_hash is required")
    with database.connect() as connection:
        row = connection.execute(
            "SELECT id,project_id,query_text,corpus_version,config_json,packet_json,packet_hash,"
            "elapsed_ms,created_at FROM runs WHERE packet_hash=? ORDER BY created_at DESC LIMIT 1",
            (digest,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown packet hash: {digest}")
        aliases = connection.execute(
            "SELECT resolved_path FROM project_aliases "
            "WHERE project_id=? AND resolved_path IS NOT NULL",
            (row["project_id"],),
        ).fetchall()
    if not any(alias_is_visible(item["resolved_path"], roots) for item in aliases):
        raise ValueError("packet is outside the server's allowed roots")
    result = dict(row)
    result["config"] = json.loads(result.pop("config_json"))
    packet = json.loads(result.pop("packet_json"))
    if packet.get("schema") == CHECK_SCHEMA:
        with database.connect() as connection:
            result["packet"] = explain_check(connection, packet, project_ids_visible_to_roots(database, roots))
    else:
        result["packet"] = bound_packet(database, packet, roots)
    return result


def build_server(*, db_path: str, roots: list[str], strict_roots: bool = False):
    """Build the stdio MCP server bound to one database.

    Launch roots are always served. Unless strict_roots is set, a tool call may
    also name any other bounded project folder; each folder used that way is
    remembered for this server process so explain and expand can reach its
    packets and evidence.
    """
    from mcp.server import MCPServer

    error_type = _tool_error()
    access = ACCESS_STRICT if strict_roots else ACCESS_OPEN
    allowed = bind_roots(roots) if roots else []
    granted: list[Path] = []
    database = _database(db_path)
    ledger = Path(db_path).expanduser().resolve()

    mcp = MCPServer(
        "rso-context",
        version=__version__,
        description="Local evidence ledger over bounded project folders",
        instructions=INSTRUCTIONS,
    )

    def workspace(path: str) -> str:
        if access == ACCESS_STRICT and not allowed:
            raise ValueError(
                "This server runs with --strict-roots and has no launch root. "
                "Its host must start it with --root <project> or from the project folder."
            )
        resolved = resolve_workspace_path(path, allowed, database_path=ledger, access=access)
        if not any(alias_is_visible(resolved, [root]) for root in allowed + granted):
            granted.append(resolved)
        return str(resolved)

    def visible() -> list[Path]:
        return allowed + granted

    @mcp.tool()
    def rso_use(path: str, agent: str) -> dict:
        """Ingest a bounded workspace and return a resume packet. This writes."""

        def run():
            resolved = workspace(path)
            attributed = require_agent(agent)
            ingest = ingest_project(database, resolved, agent=attributed)
            packet = resume_context(database, path=resolved, agent=attributed)
            return {"schema": "rso-context-use/v1", "ingest": ingest, "resume": packet}

        return _invoke(error_type, run)

    @mcp.tool(structured_output=False)
    def rso_query(
        query: str,
        path: str,
        agent: str,
        limit: int = 8,
        token_budget: int = 4000,
        compact: bool = True,
        byte_budget: int = Limits.compact_byte_budget,
    ):
        """Return a compact evidence packet by default. This writes a run record and consumes budget."""

        def run():
            resolved = workspace(path)
            attributed = require_agent(agent)
            if not str(query).strip():
                raise ValueError("query is required")
            packet = bound_packet(
                database,
                query_context(
                    database,
                    query,
                    path=resolved,
                    agent=attributed,
                    limit=limit,
                    token_budget=token_budget,
                ),
                visible(),
            )
            fitted = fit_payload_to_wire_budget(packet, byte_budget=byte_budget, compact=compact)
            return call_tool_result(fitted)

        return _invoke(error_type, run)

    @mcp.tool(structured_output=False)
    def rso_check(
        questions: list[dict],
        path: str,
        agent: str,
        byte_budget: int = Limits.compact_byte_budget,
    ):
        """Answer typed claim/choice/value questions from ledger evidence. Writes a run record."""

        def run():
            resolved = workspace(path)
            attributed = require_agent(agent)
            full = check_questions(database, questions, path=resolved, agent=attributed, roots=visible())
            return call_tool_result(fit_to_wire_budget(lambda inner: fit_check(full, inner), byte_budget))

        return _invoke(error_type, run)

    @mcp.tool()
    def rso_resume(path: str, agent: str, limit: int = 8) -> dict:
        """Return a compact resume packet. Does not ingest."""

        def run():
            resolved = workspace(path)
            attributed = require_agent(agent)
            return resume_context(database, path=resolved, agent=attributed, limit=limit)

        return _invoke(error_type, run)

    @mcp.tool()
    def rso_explain(packet_hash: str) -> dict:
        """Return a saved query packet from the launch-bound ledger if its project is in-root."""
        return _invoke(error_type, lambda: explain_packet(database, packet_hash, visible()))

    @mcp.tool(structured_output=False)
    def rso_expand(ref: str, max_bytes: int = Limits.compact_byte_budget):
        """Retrieve omitted evidence by expansion reference. Bounded; stale if the file changed."""

        def run():
            if not str(ref).strip():
                raise ValueError("ref is required")
            roots = visible()
            return call_tool_result(
                fit_to_wire_budget(
                    lambda inner: expand_reference(database, ref, roots=roots, max_bytes=inner),
                    max_bytes,
                )
            )

        return _invoke(error_type, run)

    advertised = {
        rso_use.__name__,
        rso_query.__name__,
        rso_check.__name__,
        rso_resume.__name__,
        rso_explain.__name__,
        rso_expand.__name__,
    }
    if advertised != set(TOOL_NAMES):
        raise RuntimeError(f"MCP tool names drifted from contract: {sorted(advertised)}")
    mcp._rso_meta = {  # type: ignore[attr-defined]
        "protocol_id": PROTOCOL_ID,
        "version": __version__,
        "db": str(Path(db_path)),
        "roots": [str(path) for path in allowed],
        "access": access,
    }
    return mcp


def serve_stdio(*, db_path: str, roots: list[str], strict_roots: bool = False) -> None:
    """Block on stdin/stdout. stdout is the MCP wire."""
    build_server(db_path=db_path, roots=roots, strict_roots=strict_roots).run()
