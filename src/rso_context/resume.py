from __future__ import annotations

import hashlib
from pathlib import Path

from .db import Database, json_text, read_schema_version
from .identity import resolve_existing_project, source_path_filter


def resume_context(
    database: Database,
    *,
    path: str | Path | None = None,
    agent: str = "unknown",
    limit: int = 8,
) -> dict[str, object]:
    """Return a compact resume packet for an already-registered project.

    Does not register a new project, dump chat, or invent a task query.
    When path is set, counts and claims are limited to sources under that path
    so a nested git worktree cannot pollute the parent workspace packet.
    """
    database.initialize()
    project = resolve_existing_project(database, path=path, project_id=None)
    project_id = str(project["id"])
    claim_limit = max(1, int(limit))
    path_sql, path_params = source_path_filter("s.resolved_path", path)
    path_and = f" AND {path_sql}" if path_sql else ""

    with database.connect() as connection:
        db_schema_version = read_schema_version(connection)
        requested = str(Path(path).expanduser().resolve()) if path else None
        alias_row = connection.execute(
            "SELECT resolved_path FROM project_aliases "
            "WHERE project_id=? AND resolved_path IS NOT NULL "
            "ORDER BY CASE WHEN agent=? THEN 0 ELSE 1 END, last_seen DESC LIMIT 1",
            (project_id, agent),
        ).fetchone()
        resolved_path = requested or (alias_row["resolved_path"] if alias_row else project.get("resolved_path"))

        last_seen = connection.execute(
            f"SELECT MAX(s.last_seen) FROM sources s WHERE s.project_id=?{path_and}",
            (project_id, *path_params),
        ).fetchone()[0]
        active_sources = connection.execute(
            f"SELECT COUNT(*) FROM sources s WHERE s.project_id=? AND s.active=1{path_and}",
            (project_id, *path_params),
        ).fetchone()[0]
        current_chunks = connection.execute(
            "SELECT COUNT(*) FROM chunks c "
            "JOIN source_versions v ON v.id=c.source_version_id "
            "JOIN sources s ON s.id=v.source_id "
            f"WHERE s.project_id=? AND s.active=1 AND s.current_hash=v.content_hash{path_and}",
            (project_id, *path_params),
        ).fetchone()[0]
        claim_from = (
            "SELECT c.id, c.display_text, c.trust_state, c.created_by, c.created_at FROM ("
            "SELECT DISTINCT c.id, c.display_text, c.trust_state, c.created_by, c.created_at "
            "FROM claims c "
            "JOIN claim_evidence ce ON ce.claim_id=c.id "
            "JOIN chunks ch ON ch.id=ce.chunk_id "
            "JOIN source_versions v ON v.id=ch.source_version_id "
            "JOIN sources s ON s.id=v.source_id "
            f"WHERE c.project_id=? AND s.active=1 AND s.current_hash=v.content_hash{path_and} "
            "UNION "
            "SELECT c.id, c.display_text, c.trust_state, c.created_by, c.created_at "
            "FROM claims c "
            "WHERE c.project_id=? AND c.trust_state='proposed'"
            ") c"
        )
        claim_params = (project_id, *path_params, project_id)
        claim_count = connection.execute(
            f"SELECT COUNT(*) FROM ({claim_from})",
            claim_params,
        ).fetchone()[0]
        validation_count = connection.execute(
            "SELECT COUNT(*) FROM validations v JOIN claims c ON c.id=v.claim_id "
            "WHERE c.project_id=?",
            (project_id,),
        ).fetchone()[0]
        run_count = connection.execute(
            "SELECT COUNT(*) FROM runs WHERE project_id=?",
            (project_id,),
        ).fetchone()[0]
        possible_matches = connection.execute(
            "SELECT COUNT(*) FROM possible_project_matches "
            "WHERE left_project_id=? OR right_project_id=?",
            (project_id, project_id),
        ).fetchone()[0]
        older_versions = connection.execute(
            "SELECT COUNT(*) FROM source_versions v "
            "JOIN sources s ON s.id=v.source_id "
            f"WHERE s.project_id=? AND v.content_hash != s.current_hash{path_and}",
            (project_id, *path_params),
        ).fetchone()[0]

        claims = [
            {
                "id": row["id"],
                "display_text": row["display_text"],
                "trust_state": row["trust_state"],
                "created_by": row["created_by"],
            }
            for row in connection.execute(
                claim_from
                + " ORDER BY CASE c.trust_state "
                "WHEN 'verified' THEN 0 WHEN 'disputed' THEN 1 "
                "WHEN 'observed' THEN 2 ELSE 3 END, c.created_at DESC, c.id "
                "LIMIT ?",
                (*claim_params, claim_limit),
            )
        ]
        last_runs = [
            {
                "created_at": row["created_at"],
                "query_text": row["query_text"],
                "packet_hash": row["packet_hash"],
                "corpus_version": int(row["corpus_version"]),
            }
            for row in connection.execute(
                "SELECT created_at, query_text, packet_hash, corpus_version "
                "FROM runs WHERE project_id=? "
                "ORDER BY created_at DESC, id DESC LIMIT 3",
                (project_id,),
            )
        ]

    project_payload: dict[str, object] = {
        "id": project_id,
        "name": project["display_name"],
        "scope": project["scope"],
        "kind": project["kind"],
        "canonical_key": project["canonical_key"],
        "corpus_version": int(project["corpus_version"]),
    }
    if resolved_path:
        project_payload["resolved_path"] = resolved_path
        folder = Path(str(resolved_path)).name
        if folder and folder != project["display_name"]:
            project_payload["workspace_name"] = folder

    packet: dict[str, object] = {
        "schema": "rso-context-resume/v1",
        "db_schema_version": db_schema_version,
        "project": project_payload,
        "freshness": {
            "last_seen": last_seen,
            "updated_at": project["updated_at"],
            "active_sources": int(active_sources),
            "chunks": int(current_chunks),
            "claims": int(claim_count),
            "validations": int(validation_count),
            "runs": int(run_count),
        },
        "claims": claims,
        "last_runs": last_runs,
        "takes": {
            "current": int(active_sources),
            "older_versions": int(older_versions),
            "note": "older takes stored; not included in this packet",
        },
    }
    if possible_matches:
        packet["possible_matches"] = int(possible_matches)
    packet_hash = hashlib.sha256(json_text(packet).encode("utf-8")).hexdigest()
    packet["packet_hash"] = packet_hash
    return packet
