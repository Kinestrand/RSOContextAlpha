from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from . import __version__
from .audit import (
    VALIDATION_RESULTS,
    agent_inbox,
    audit_answer,
    pending_validations,
    propose_claim,
    record_validation,
    validate_graph,
)
from .config import Limits, automatic_discovery_roots, default_db_path
from .db import Database
from .discovery import discover_and_register
from .identity import project_explanation, register_project, resolve_existing_project
from .admin import serve_admin
from .ingest import ingest_project
from .pointers import compact_pointers
from .compact import compact_packet
from .query import query_context
from .resume import resume_context
from .run_budget import get_or_start, set_budget


def _print(value: object) -> None:
    # ASCII JSON survives native-command decoding in Windows PowerShell 5.1.
    # JSON parsers restore Unicode escapes, including non-BMP characters.
    print(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True))


def _database(args: argparse.Namespace) -> Database:
    database = Database(args.db)
    database.initialize()
    return database


def _limits(args: argparse.Namespace) -> Limits:
    return Limits(
        max_file_bytes=getattr(args, "max_file_bytes", Limits.max_file_bytes),
        max_files_per_project=getattr(args, "max_files", Limits.max_files_per_project),
        max_projects=getattr(args, "max_projects", Limits.max_projects),
        max_discovery_depth=getattr(args, "max_depth", Limits.max_discovery_depth),
        chunk_characters=getattr(args, "chunk_characters", Limits.chunk_characters),
        query_limit=getattr(args, "limit", Limits.query_limit),
        query_token_budget=getattr(args, "token_budget", Limits.query_token_budget),
    )


def command_init(args: argparse.Namespace) -> int:
    database = _database(args)
    _print({"status": "initialized", "version": __version__, "database": str(database.path)})
    return 0


def _roots(args: argparse.Namespace) -> list[Path]:
    if getattr(args, "roots", None):
        return [Path(root).expanduser().resolve() for root in args.roots]
    return automatic_discovery_roots()


def command_discover(args: argparse.Namespace) -> int:
    database = _database(args)
    roots = _roots(args)
    registrations = discover_and_register(
        database,
        roots,
        max_depth=args.max_depth,
        max_projects=args.max_projects,
        agent=args.agent,
    )
    _print(
        {
            "roots": [str(root) for root in roots],
            "projects": registrations,
            "project_count": len(registrations),
        }
    )
    return 0


def command_bootstrap(args: argparse.Namespace) -> int:
    database = _database(args)
    roots = _roots(args)
    registrations = discover_and_register(
        database,
        roots,
        max_depth=args.max_depth,
        max_projects=args.max_projects,
        agent=args.agent,
    )
    results = []
    seen_paths: set[str] = set()
    for registration in registrations:
        path = str(registration["identity"]["resolved_path"])
        if path.casefold() in seen_paths:
            continue
        seen_paths.add(path.casefold())
        results.append(
            ingest_project(
                database,
                path,
                agent=args.agent,
                limits=_limits(args),
            )
        )
    _print(
        {
            "status": "bootstrapped",
            "roots": [str(root) for root in roots],
            "projects_discovered": len(registrations),
            "projects_ingested": len(results),
            "results": results,
            "stats": database.stats(),
        }
    )
    return 0


def command_register(args: argparse.Namespace) -> int:
    database = _database(args)
    _print(
        register_project(
            database,
            args.path,
            agent=args.agent,
            external_id=args.external_id,
            scope=args.scope,
        )
    )
    return 0


def command_ingest(args: argparse.Namespace) -> int:
    database = _database(args)
    results = []
    if args.all:
        with database.connect() as connection:
            paths = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT resolved_path FROM project_aliases WHERE resolved_path IS NOT NULL "
                    "ORDER BY resolved_path"
                )
            ]
    else:
        paths = [str(Path(args.path or Path.cwd()).expanduser().resolve())]
    for path in paths:
        if Path(path).is_dir():
            results.append(ingest_project(database, path, agent=args.agent, limits=_limits(args)))
    _print({"results": results, "count": len(results)})
    return 0



def command_use(args: argparse.Namespace) -> int:
    """Agent one-shot: register, incremental ingest, resume. Humans should not run this by hand."""
    database = _database(args)
    path = str(Path(args.path or Path.cwd()).expanduser().resolve())
    ingest = ingest_project(database, path, agent=args.agent, limits=_limits(args))
    packet = resume_context(
        database,
        path=path,
        agent=args.agent,
        limit=getattr(args, "limit", 8),
    )
    _print({"schema": "rso-context-use/v1", "ingest": ingest, "resume": packet})
    return 0


def command_resume(args: argparse.Namespace) -> int:
    database = _database(args)
    packet = resume_context(
        database,
        path=args.path,
        agent=args.agent,
        limit=args.limit,
    )
    _print(packet)
    return 0


def command_query(args: argparse.Namespace) -> int:
    database = _database(args)
    packet = query_context(
        database,
        args.query,
        path=args.path,
        project_id=args.project_id,
        agent=args.agent,
        limit=args.limit,
        token_budget=args.token_budget,
        use_cache=not args.no_cache,
        run_budget=args.run_budget,
    )
    if getattr(args, "compact", False):
        packet = compact_packet(packet, byte_budget=getattr(args, "byte_budget", Limits.compact_byte_budget))
    _print(packet)
    return 0


def command_run_budget(args: argparse.Namespace) -> int:
    database = _database(args)
    project = resolve_existing_project(database, path=args.path, project_id=None)
    project_id = str(project["id"])
    with database.transaction() as connection:
        if args.set_value is not None:
            result = set_budget(connection, project_id, args.agent, args.set_value)
        else:
            result = get_or_start(connection, project_id, args.agent)
    _print(
        {
            "remaining": result["remaining"],
            "initial": result["initial"],
            "project_id": project_id,
            "agent": args.agent,
        }
    )
    return 0


def command_propose(args: argparse.Namespace) -> int:
    database = _database(args)
    _print(
        propose_claim(
            database,
            args.text,
            agent=args.agent,
            path=args.path,
            project_id=args.project_id,
        )
    )
    return 0


def command_pending(args: argparse.Namespace) -> int:
    database = _database(args)
    _print(
        pending_validations(
            database,
            agent=args.agent,
            path=args.path,
            project_id=None,
        )
    )
    return 0


def command_inbox(args: argparse.Namespace) -> int:
    database = _database(args)
    _print(
        agent_inbox(
            database,
            path=args.path,
            topic=args.topic,
            limit=args.limit,
        )
    )
    return 0


def command_watch(args: argparse.Namespace) -> int:
    """Watch a registered folder. Do not scan a user profile."""
    database = _database(args)
    iteration = 0
    agent = getattr(args, "agent", None) or "observer"
    bounded = getattr(args, "path", None)
    while True:
        iteration += 1
        results = []
        if bounded:
            path = str(Path(bounded).expanduser().resolve())
            resolve_existing_project(database, path=path)
            if Path(path).is_dir():
                results.append(ingest_project(database, path, agent=agent, limits=_limits(args)))
        else:
            if iteration == 1 or iteration % args.discover_every == 0:
                discover_and_register(
                    database,
                    _roots(args),
                    max_depth=args.max_depth,
                    max_projects=args.max_projects,
                    agent=agent,
                )
            with database.connect() as connection:
                paths = [
                    row[0]
                    for row in connection.execute(
                        "SELECT DISTINCT resolved_path FROM project_aliases WHERE resolved_path IS NOT NULL "
                        "ORDER BY resolved_path"
                    )
                ]
            for path in paths:
                if Path(path).is_dir():
                    results.append(ingest_project(database, path, agent=agent, limits=_limits(args)))
        changed = sum(1 for result in results if result["changed"])
        _print({"iteration": iteration, "projects": len(results), "changed_projects": changed})
        if args.once:
            return 0
        time.sleep(max(1.0, args.interval))


def command_stats(args: argparse.Namespace) -> int:
    _print(_database(args).stats())
    return 0


def command_explain_project(args: argparse.Namespace) -> int:
    _print(project_explanation(_database(args), args.path or Path.cwd()))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    result = validate_graph(_database(args), project_id=args.project_id)
    _print(result)
    return 0 if result["result"] == "pass" else 1


def command_record_validation(args: argparse.Namespace) -> int:
    if args.details and args.details_file:
        raise ValueError("Provide only one of --details or --details-file")
    if args.details_file:
        raw_details = Path(args.details_file).read_text(encoding="utf-8")
    else:
        raw_details = args.details or "{}"
    details = json.loads(raw_details)
    if not isinstance(details, dict):
        raise ValueError("Validation details must be a JSON object")
    _print(
        record_validation(
            _database(args),
            args.claim_id,
            validator=args.validator,
            result=args.result,
            details=details,
        )
    )
    return 0


def command_audit(args: argparse.Namespace) -> int:
    if args.answer_file:
        answer = Path(args.answer_file).read_text(encoding="utf-8")
    elif args.answer is not None:
        answer = args.answer
    else:
        raise ValueError("Provide --answer or --answer-file")
    _print(
        audit_answer(
            _database(args),
            args.request,
            answer,
            path=args.path,
            project_id=args.project_id,
            agent=args.agent,
            token_budget=args.token_budget,
        )
    )
    return 0


def command_explain(args: argparse.Namespace) -> int:
    database = _database(args)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT id,project_id,query_text,corpus_version,config_json,packet_json,packet_hash,"
            "elapsed_ms,created_at FROM runs WHERE packet_hash=? ORDER BY created_at DESC LIMIT 1",
            (args.packet_hash,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Unknown packet hash: {args.packet_hash}")
    result = dict(row)
    result["config"] = json.loads(result.pop("config_json"))
    result["packet"] = json.loads(result.pop("packet_json"))
    _print(result)
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    from .mcp_runtime import runtime_status
    from .mcp_setup import inspect_clients

    database = _database(args)
    checks: dict[str, object] = {
        "python": sys.version.split()[0],
        "git": shutil.which("git"),
        "database": str(database.path),
        "automatic_roots": [str(path) for path in automatic_discovery_roots()],
    }
    with database.connect() as connection:
        checks["sqlite"] = sqlite3.sqlite_version
        checks["quick_check"] = connection.execute("PRAGMA quick_check").fetchone()[0]
        try:
            connection.execute("SELECT count(*) FROM chunk_fts WHERE chunk_fts MATCH 'test'").fetchone()
            checks["fts5"] = True
        except sqlite3.OperationalError as error:
            checks["fts5"] = False
            checks["fts5_error"] = str(error)
    checks["mcp"] = runtime_status()
    checks["mcp_clients"] = inspect_clients()
    checks["ready"] = checks["quick_check"] == "ok" and checks["fts5"] is True and checks["git"] is not None
    _print(checks)
    return 0 if checks["ready"] else 1



def command_compact_pointers(args: argparse.Namespace) -> int:
    database = _database(args)
    _print(compact_pointers(database))
    return 0


def command_admin(args: argparse.Namespace) -> int:
    database = _database(args)
    serve_admin(database, port=args.port)
    return 0


def _mcp_argv(args: argparse.Namespace) -> list[str]:
    argv = ["--db", str(args.db), "mcp"]
    for root in args.roots or []:
        argv.extend(["--root", str(root)])
    return argv


def command_mcp(args: argparse.Namespace) -> int:
    from .mcp_runtime import current_sdk_status, install_runtime, runtime_status, serve_via_runtime

    if args.install_runtime:
        _print(install_runtime())
        return 0
    if args.status:
        _print(runtime_status())
        return 0
    if args.setup or args.remove:
        from .mcp_setup import remove_client, setup_client

        if not args.client:
            raise ValueError("mcp --setup/--remove requires --client codex, claude-code, gemini, or antigravity")
        if args.remove:
            _print(remove_client(args.client, config=args.config))
            return 0
        roots = [str(path) for path in (args.roots or [])]
        if not roots:
            raise ValueError("mcp --setup requires --root")
        _print(setup_client(args.client, roots[0], config=args.config))
        return 0
    roots = [str(path) for path in (args.roots or [])]
    from .mcp_contract import bind_roots

    bind_roots(roots)
    status = current_sdk_status()
    if not status["usable"] and os.environ.get("RSO_MCP_IN_RUNTIME") != "1":
        return serve_via_runtime(_mcp_argv(args))
    from .mcp_server import serve_stdio

    serve_stdio(db_path=str(args.db), roots=roots)
    return 0


def _add_common_limits(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-file-bytes", type=int, default=Limits.max_file_bytes)
    parser.add_argument("--max-files", type=int, default=Limits.max_files_per_project)
    parser.add_argument("--chunk-characters", type=int, default=Limits.chunk_characters)


def _add_discovery(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("roots", nargs="*", help="Optional roots; automatic roots are used when omitted")
    parser.add_argument("--max-depth", type=int, default=Limits.max_discovery_depth)
    parser.add_argument("--max-projects", type=int, default=Limits.max_projects)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rso-context", description="Local agent-independent context graph")
    parser.add_argument("--db", default=str(default_db_path()), help="SQLite database path")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize the database")
    init_parser.set_defaults(function=command_init)

    discover_parser = subparsers.add_parser("discover", help="Discover and register projects")
    _add_discovery(discover_parser)
    discover_parser.add_argument("--agent", default="observer")
    discover_parser.set_defaults(function=command_discover)

    bootstrap_parser = subparsers.add_parser("bootstrap", help="Discover projects and build the initial graph")
    _add_discovery(bootstrap_parser)
    _add_common_limits(bootstrap_parser)
    bootstrap_parser.add_argument("--agent", default="observer")
    bootstrap_parser.set_defaults(function=command_bootstrap)

    register_parser = subparsers.add_parser("register", help="Register an agent workspace alias")
    register_parser.add_argument("--path", default=str(Path.cwd()))
    register_parser.add_argument("--agent", required=True)
    register_parser.add_argument("--external-id")
    register_parser.add_argument("--scope", choices=["project", "domain", "shared"], default="project")
    register_parser.set_defaults(function=command_register)

    ingest_parser = subparsers.add_parser("ingest", help="Incrementally ingest one or all registered projects")
    ingest_parser.add_argument("--path")
    ingest_parser.add_argument("--all", action="store_true")
    ingest_parser.add_argument("--agent", default="observer")
    _add_common_limits(ingest_parser)
    ingest_parser.set_defaults(function=command_ingest)


    use_parser = subparsers.add_parser(
        "use", help="Agent one-shot: ingest a bounded workspace and return a resume packet"
    )
    use_parser.add_argument("--path", default=str(Path.cwd()))
    use_parser.add_argument("--agent", required=True)
    use_parser.add_argument("--limit", type=int, default=8)
    _add_common_limits(use_parser)
    use_parser.set_defaults(function=command_use)

    resume_parser = subparsers.add_parser(
        "resume", help="Return a compact resume packet for an existing project"
    )
    resume_parser.add_argument("--path")
    resume_parser.add_argument("--agent", default="unknown")
    resume_parser.add_argument("--limit", type=int, default=8)
    resume_parser.set_defaults(function=command_resume)

    query_parser = subparsers.add_parser("query", help="Return a deterministic context packet")
    query_parser.add_argument("query")
    query_parser.add_argument("--path")
    query_parser.add_argument("--project-id")
    query_parser.add_argument("--agent", default="unknown")
    query_parser.add_argument("--limit", type=int, default=Limits.query_limit)
    query_parser.add_argument("--token-budget", type=int, default=Limits.query_token_budget)
    query_parser.add_argument("--no-cache", action="store_true")
    query_parser.add_argument("--run-budget", type=int, dest="run_budget")
    query_parser.add_argument(
        "--compact",
        action="store_true",
        help="Return rso-mcp-packet/v1 instead of the default rso-context-packet/v2",
    )
    query_parser.add_argument(
        "--byte-budget",
        type=int,
        default=Limits.compact_byte_budget,
        help="Max ASCII JSON bytes for --compact (MCP default too)",
    )
    query_parser.set_defaults(function=command_query)

    run_budget_parser = subparsers.add_parser(
        "run-budget", help="Show or reset the remaining run budget for an agent"
    )
    run_budget_parser.add_argument("--agent", required=True)
    run_budget_parser.add_argument("--path", default=str(Path.cwd()))
    run_budget_parser.add_argument("--set", type=int, dest="set_value", metavar="N")
    run_budget_parser.set_defaults(function=command_run_budget)

    propose_parser = subparsers.add_parser(
        "propose", help="File a proposed claim without attaching evidence"
    )
    propose_parser.add_argument("text")
    propose_parser.add_argument("--agent", required=True)
    propose_parser.add_argument("--path")
    propose_parser.add_argument("--project-id")
    propose_parser.set_defaults(function=command_propose)

    pending_parser = subparsers.add_parser(
        "pending", help="List claims waiting for a named validation"
    )
    pending_parser.add_argument("--agent")
    pending_parser.add_argument("--path")
    pending_parser.set_defaults(function=command_pending)

    inbox_parser = subparsers.add_parser(
        "inbox", help="List open RSO-CARD/v1 coordination proposals"
    )
    inbox_parser.add_argument("--path", default=str(Path.cwd()))
    inbox_parser.add_argument("--topic")
    inbox_parser.add_argument("--limit", type=int, default=50)
    inbox_parser.set_defaults(function=command_inbox)

    watch_parser = subparsers.add_parser(
        "watch",
        help="Watch a registered folder; do not scan a user profile",
    )
    _add_discovery(watch_parser)
    _add_common_limits(watch_parser)
    watch_parser.add_argument(
        "--path",
        help="Already-registered folder to watch; skip discovery when set",
    )
    watch_parser.add_argument("--agent", default="observer")
    watch_parser.add_argument("--interval", type=float, default=10.0)
    watch_parser.add_argument("--discover-every", type=int, default=30)
    watch_parser.add_argument("--once", action="store_true", help="Run a single poll and exit")
    watch_parser.set_defaults(function=command_watch)

    stats_parser = subparsers.add_parser("stats", help="Show graph and corpus counts")
    stats_parser.set_defaults(function=command_stats)

    explain_parser = subparsers.add_parser("explain-project", help="Explain canonical project matching")
    explain_parser.add_argument("--path")
    explain_parser.set_defaults(function=command_explain_project)

    validate_parser = subparsers.add_parser("validate", help="Validate graph and evidence invariants")
    validate_parser.add_argument("--project-id")
    validate_parser.set_defaults(function=command_validate)

    record_validation_parser = subparsers.add_parser(
        "record-validation", help="Record a named validation for a claim"
    )
    record_validation_parser.add_argument("claim_id")
    record_validation_parser.add_argument("--validator", required=True)
    record_validation_parser.add_argument("--result", choices=sorted(VALIDATION_RESULTS), required=True)
    record_validation_parser.add_argument("--details", help="Validation details as a JSON object")
    record_validation_parser.add_argument("--details-file", help="Path to validation details JSON")
    record_validation_parser.set_defaults(function=command_record_validation)

    audit_parser = subparsers.add_parser("audit", help="Run a lightweight Match Move coverage audit")
    audit_parser.add_argument("request")
    audit_parser.add_argument("--answer")
    audit_parser.add_argument("--answer-file")
    audit_parser.add_argument("--path")
    audit_parser.add_argument("--project-id")
    audit_parser.add_argument("--agent", default="unknown")
    audit_parser.add_argument("--token-budget", type=int, default=Limits.query_token_budget)
    audit_parser.set_defaults(function=command_audit)

    packet_parser = subparsers.add_parser("explain", help="Explain a saved context packet")
    packet_parser.add_argument("packet_hash")
    packet_parser.set_defaults(function=command_explain)

    compact_parser = subparsers.add_parser(
        "compact-pointers",
        help="Clear stored chunk bodies that can be reconstructed from live files",
    )
    compact_parser.set_defaults(function=command_compact_pointers)

    admin_parser = subparsers.add_parser(
        "admin",
        help="Read-only HTML viewer bound to 127.0.0.1",
    )
    admin_parser.add_argument("--port", type=int, default=7432)
    admin_parser.set_defaults(function=command_admin)

    doctor_parser = subparsers.add_parser("doctor", help="Check local runtime support")
    doctor_parser.set_defaults(function=command_doctor)

    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Serve a local stdio MCP adapter bound to --root folders",
    )
    mcp_parser.add_argument(
        "--db",
        default=argparse.SUPPRESS,
        help="SQLite database path (also accepted after mcp for host configs)",
    )
    mcp_parser.add_argument(
        "--root",
        action="append",
        dest="roots",
        help="Allowed project folder (repeatable). Required to serve. Not a user profile.",
    )
    mcp_parser.add_argument(
        "--install-runtime",
        action="store_true",
        help="Create or refresh the isolated MCP SDK venv and exit",
    )
    mcp_parser.add_argument(
        "--status",
        action="store_true",
        help="Print isolated runtime and SDK status as JSON and exit",
    )
    mcp_parser.add_argument(
        "--setup",
        action="store_true",
        help="Write an RSO-owned MCP entry for --client (Codex, Claude Code, or Gemini/Antigravity)",
    )
    mcp_parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove only the RSO-owned rso-context MCP entry for --client",
    )
    mcp_parser.add_argument(
        "--client",
        choices=("codex", "claude-code", "gemini", "antigravity"),
        help="Host to configure: Codex, Claude Code, or Gemini/Antigravity (~/.gemini/settings.json)",
    )
    mcp_parser.add_argument(
        "--config",
        help="Client config path. Tests and isolated checks must pass this; omit to use the host default.",
    )
    mcp_parser.set_defaults(function=command_mcp)
    return parser


def main(argv: list[str] | None = None) -> int:
    # CLI JSON is UTF-8 even when Windows redirects output through a legacy
    # code page. Leave in-memory streams used by embedding callers alone.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.function(args))
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(
            json.dumps(
                {"error": type(error).__name__, "message": str(error)},
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
