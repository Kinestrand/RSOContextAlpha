"""MCP stdio adapter. Imported only when the isolated SDK runtime is present."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from . import __version__
from .db import Database, SchemaVersionError
from .ingest import ingest_project
from .mcp_contract import (
    PROTOCOL_ID,
    TOOL_NAMES,
    alias_is_visible,
    bind_roots,
    require_agent,
    resolve_workspace_path,
    restrict_packet_to_roots,
)
from .compact import compact_packet, expand_reference
from .config import Limits
from .query import query_context
from .resume import resume_context


INSTRUCTIONS = (
    "Local RSO Context evidence ledger over bounded folders. "
    "Launch with --db and one or more --root values. "
    "Tools do not accept a database path. "
    "path arguments must stay under those roots. "
    "agent is attribution, not authority. "
    "rso_use and rso_query write to the ledger. "
    "rso_query returns a compact packet; rso_expand recovers omitted evidence. "
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
    except (ValueError, OSError, KeyError, TypeError, SchemaVersionError, sqlite3.OperationalError) as error:
        _raise_tool(error_type, error)


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


def fit_payload_to_wire_budget(source: dict, *, byte_budget: int, compact: bool = True) -> dict:
    """Shrink a compact packet until the MCP wire result is within byte_budget."""
    budget = max(256, int(byte_budget))
    payload = compact_packet(source, byte_budget=budget) if compact else dict(source)
    if not compact and tool_result_wire_bytes(payload) <= budget:
        return payload
    inner = budget
    for _ in range(16):
        payload = compact_packet(source, byte_budget=inner)
        size = tool_result_wire_bytes(payload)
        if size <= budget:
            return payload
        inner = max(256, inner - (size - budget) - 32)
    return compact_packet(source, byte_budget=256)


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
    result["packet"] = bound_packet(database, json.loads(result.pop("packet_json")), roots)
    return result


def build_server(*, db_path: str, roots: list[str]):
    """Build the stdio MCP server bound to one database and explicit roots."""
    from mcp.server import MCPServer

    error_type = _tool_error()
    allowed = bind_roots(roots)
    database = _database(db_path)
    ledger = Path(db_path).expanduser().resolve()

    mcp = MCPServer(
        "rso-context",
        version=__version__,
        description="Local evidence ledger over bounded project folders",
        instructions=INSTRUCTIONS,
    )

    def workspace(path: str) -> str:
        return str(resolve_workspace_path(path, allowed, database_path=ledger))

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
                allowed,
            )
            fitted = fit_payload_to_wire_budget(packet, byte_budget=byte_budget, compact=compact)
            return call_tool_result(fitted)

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
        return _invoke(error_type, lambda: explain_packet(database, packet_hash, allowed))

    @mcp.tool(structured_output=False)
    def rso_expand(ref: str, max_bytes: int = Limits.compact_byte_budget):
        """Retrieve omitted evidence by expansion reference. Bounded; stale if the file changed."""

        def run():
            if not str(ref).strip():
                raise ValueError("ref is required")
            budget = max(256, int(max_bytes))
            inner = budget
            payload = expand_reference(database, ref, roots=allowed, max_bytes=inner)
            for _ in range(16):
                if tool_result_wire_bytes(payload) <= budget:
                    return call_tool_result(payload)
                inner = max(256, inner - (tool_result_wire_bytes(payload) - budget) - 32)
                payload = expand_reference(database, ref, roots=allowed, max_bytes=inner)
            return call_tool_result(expand_reference(database, ref, roots=allowed, max_bytes=256))

        return _invoke(error_type, run)

    advertised = {rso_use.__name__, rso_query.__name__, rso_resume.__name__, rso_explain.__name__, rso_expand.__name__}
    if advertised != set(TOOL_NAMES):
        raise RuntimeError(f"MCP tool names drifted from contract: {sorted(advertised)}")
    mcp._rso_meta = {  # type: ignore[attr-defined]
        "protocol_id": PROTOCOL_ID,
        "version": __version__,
        "db": str(Path(db_path)),
        "roots": [str(path) for path in allowed],
    }
    return mcp


def serve_stdio(*, db_path: str, roots: list[str]) -> None:
    """Block on stdin/stdout. stdout is the MCP wire."""
    build_server(db_path=db_path, roots=roots).run()
