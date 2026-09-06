from __future__ import annotations

import json
import re
from pathlib import Path

from .config import is_sensitive_path
from .db import Database, json_text
from .identity import resolve_existing_project, utc_now
from .ingest import CLAIM_NAMESPACE, _canonical_name, contains_sensitive_content
from .query import _tokens, query_context


VALIDATION_RESULTS = {"verified", "disputed", "superseded"}


def record_validation(
    database: Database,
    claim_id: str,
    *,
    validator: str,
    result: str,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    database.initialize()
    validator_name = validator.strip()
    validation_result = result.strip().casefold()
    if not validator_name:
        raise ValueError("Validator name is required")
    if validation_result not in VALIDATION_RESULTS:
        allowed = ", ".join(sorted(VALIDATION_RESULTS))
        raise ValueError(f"Validation result must be one of: {allowed}")
    if details is not None and not isinstance(details, dict):
        raise ValueError("Validation details must be a JSON object")

    now = utc_now()
    with database.transaction() as connection:
        claim = connection.execute(
            "SELECT id,project_id,display_text,trust_state FROM claims WHERE id=?", (claim_id,)
        ).fetchone()
        if claim is None:
            raise ValueError(f"Unknown claim id: {claim_id}")
        cursor = connection.execute(
            "INSERT INTO validations(claim_id,validator,result,details_json,observed_at) "
            "VALUES(?,?,?,?,?)",
            (claim_id, validator_name, validation_result, json_text(details or {}), now),
        )
        connection.execute(
            "UPDATE claims SET trust_state=?,updated_at=? WHERE id=?",
            (validation_result, now, claim_id),
        )
        connection.execute(
            "UPDATE projects SET corpus_version=corpus_version+1,updated_at=? WHERE id=?",
            (now, claim["project_id"]),
        )
        connection.execute("DELETE FROM cache WHERE project_id=?", (claim["project_id"],))
        validation = dict(
            connection.execute(
                "SELECT id,claim_id,validator,result,details_json,observed_at "
                "FROM validations WHERE id=?",
                (cursor.lastrowid,),
            ).fetchone()
        )
        updated_claim = dict(
            connection.execute(
                "SELECT id,project_id,display_text,trust_state,created_by FROM claims WHERE id=?",
                (claim_id,),
            ).fetchone()
        )

    validation["details"] = json.loads(validation.pop("details_json"))
    return {
        "schema": "rso-claim-validation/v1",
        "claim": updated_claim,
        "validation": validation,
    }


def propose_claim(
    database: Database,
    text: str,
    *,
    agent: str,
    path: str | Path | None = None,
    project_id: str | None = None,
) -> dict[str, object]:
    import uuid

    database.initialize()
    agent_name = agent.strip()
    if not agent_name:
        raise ValueError("Agent name is required")
    display = re.sub(r"\s+", " ", text).strip()
    if not display:
        raise ValueError("Claim text is required")
    normalized = _canonical_name(display)
    project = resolve_existing_project(database, path=path, project_id=project_id)
    project_id_value = str(project["id"])
    claim_id = str(uuid.uuid5(CLAIM_NAMESPACE, f"{project_id_value}:{normalized}"))
    now = utc_now()
    with database.transaction() as connection:
        existing = connection.execute(
            "SELECT id,project_id,display_text,trust_state,created_by,created_at,updated_at "
            "FROM claims WHERE project_id=? AND normalized_text=?",
            (project_id_value, normalized),
        ).fetchone()
        if existing is not None:
            claim = dict(existing)
            already_existed = True
        else:
            connection.execute(
                "INSERT INTO claims(id,project_id,normalized_text,display_text,trust_state,created_by,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    claim_id,
                    project_id_value,
                    normalized,
                    display,
                    "proposed",
                    agent_name,
                    now,
                    now,
                ),
            )
            connection.execute("DELETE FROM cache WHERE project_id=?", (project_id_value,))
            claim = dict(
                connection.execute(
                    "SELECT id,project_id,display_text,trust_state,created_by,created_at,updated_at "
                    "FROM claims WHERE id=?",
                    (claim_id,),
                ).fetchone()
            )
            already_existed = False
    return {
        "schema": "rso-claim-proposal/v1",
        "claim": claim,
        "already_existed": already_existed,
    }


def pending_validations(
    database: Database,
    *,
    agent: str | None = None,
    path: str | Path | None = None,
    project_id: str | None = None,
) -> dict[str, object]:
    del agent
    database.initialize()
    project = resolve_existing_project(database, path=path, project_id=project_id)
    project_id_value = str(project["id"])
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT c.id, c.display_text, c.trust_state, c.created_by, c.updated_at
            FROM claims c
            WHERE c.project_id=?
              AND (
                c.trust_state='proposed'
                OR (
                  c.trust_state='observed'
                  AND NOT EXISTS (SELECT 1 FROM validations v WHERE v.claim_id=c.id)
                )
              )
            ORDER BY c.updated_at DESC, c.id DESC
            LIMIT 20
            """,
            (project_id_value,),
        ).fetchall()
    return {
        "schema": "rso-pending-validations/v1",
        "project_id": project_id_value,
        "claims": [dict(row) for row in rows],
    }


def validate_graph(database: Database, project_id: str | None = None) -> dict[str, object]:
    database.initialize()
    project_filter = " AND p.id=?" if project_id else ""
    parameters = (project_id,) if project_id else ()
    with database.connect() as connection:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        missing_current_versions = connection.execute(
            "SELECT COUNT(*) FROM sources s JOIN projects p ON p.id=s.project_id "
            "LEFT JOIN source_versions v ON v.source_id=s.id AND v.content_hash=s.current_hash "
            f"WHERE s.active=1 AND v.id IS NULL{project_filter}",
            parameters,
        ).fetchone()[0]
        claims_without_evidence = connection.execute(
            "SELECT COUNT(*) FROM claims c JOIN projects p ON p.id=c.project_id "
            "LEFT JOIN claim_evidence e ON e.claim_id=c.id "
            f"WHERE c.trust_state IN ('observed','verified') AND e.claim_id IS NULL{project_filter}",
            parameters,
        ).fetchone()[0]
        dangling_edges = connection.execute(
            "SELECT COUNT(*) FROM edges e JOIN projects p ON p.id=e.project_id "
            "LEFT JOIN nodes s ON s.id=e.subject_id LEFT JOIN nodes o ON o.id=e.object_id "
            f"WHERE (s.id IS NULL OR o.id IS NULL){project_filter}",
            parameters,
        ).fetchone()[0]
        current_chunks = connection.execute(
            "SELECT COUNT(*) FROM chunks c JOIN source_versions v ON v.id=c.source_version_id "
            "JOIN sources s ON s.id=v.source_id JOIN projects p ON p.id=s.project_id "
            f"WHERE s.active=1 AND s.current_hash=v.content_hash{project_filter}",
            parameters,
        ).fetchone()[0]
        validation_state_mismatches = connection.execute(
            "SELECT COUNT(*) FROM claims c JOIN projects p ON p.id=c.project_id "
            "JOIN validations v ON v.id=(SELECT MAX(v2.id) FROM validations v2 "
            "WHERE v2.claim_id=c.id) "
            f"WHERE c.trust_state<>v.result{project_filter}",
            parameters,
        ).fetchone()[0]
        sensitive_active_sources = sum(
            1
            for row in connection.execute(
                "SELECT s.resolved_path FROM sources s JOIN projects p ON p.id=s.project_id "
                f"WHERE s.active=1{project_filter}",
                parameters,
            )
            if is_sensitive_path(Path(row["resolved_path"]))
        )
        sensitive_current_chunks = sum(
            1
            for row in connection.execute(
                "SELECT c.text FROM chunks c JOIN source_versions v ON v.id=c.source_version_id "
                "JOIN sources s ON s.id=v.source_id JOIN projects p ON p.id=s.project_id "
                f"WHERE s.active=1 AND s.current_hash=v.content_hash{project_filter}",
                parameters,
            )
            if contains_sensitive_content(str(row["text"]))
        )

    checks = {
        "sqlite_quick_check": quick_check,
        "missing_current_versions": int(missing_current_versions),
        "claims_without_evidence": int(claims_without_evidence),
        "dangling_edges": int(dangling_edges),
        "current_chunks": int(current_chunks),
        "validation_state_mismatches": int(validation_state_mismatches),
        "sensitive_active_sources": int(sensitive_active_sources),
        "sensitive_current_chunks": int(sensitive_current_chunks),
    }
    passed = (
        quick_check == "ok"
        and missing_current_versions == 0
        and claims_without_evidence == 0
        and dangling_edges == 0
        and validation_state_mismatches == 0
        and sensitive_active_sources == 0
        and sensitive_current_chunks == 0
    )
    return {
        "schema": "rso-graph-validation/v1",
        "project_id": project_id,
        "result": "pass" if passed else "fail",
        "checks": checks,
    }


def _answer_statements(answer: str) -> list[str]:
    candidates = re.split(r"(?<=[.!?])\s+|\n+", answer)
    return [re.sub(r"\s+", " ", value).strip(" -*\t") for value in candidates if len(value.strip()) >= 12]


def audit_answer(
    database: Database,
    request: str,
    answer: str,
    *,
    path: str | Path | None = None,
    project_id: str | None = None,
    agent: str = "unknown",
    token_budget: int = 4_000,
) -> dict[str, object]:
    packet = query_context(
        database,
        request,
        path=path,
        project_id=project_id,
        agent=agent,
        token_budget=token_budget,
    )
    evidence = packet["evidence"]
    evidence_tokens = [set(_tokens(str(item["text"]))) for item in evidence]
    normalized_evidence = [re.sub(r"\s+", " ", str(item["text"])).casefold() for item in evidence]
    statement_results: list[dict[str, object]] = []
    for statement in _answer_statements(answer):
        tokens = set(_tokens(statement))
        scored: list[tuple[float, int]] = []
        for index, candidate_tokens in enumerate(evidence_tokens):
            normalized_statement = re.sub(r"\s+", " ", statement).casefold()
            if normalized_statement and normalized_statement in normalized_evidence[index]:
                score = 1.0
            elif not tokens:
                score = 0.0
            else:
                score = len(tokens.intersection(candidate_tokens)) / len(tokens)
            scored.append((score, index))
        scored.sort(key=lambda value: (-value[0], value[1]))
        mapped = [index for score, index in scored if score >= 0.45]
        best_score, best_index = scored[0] if scored else (0.0, -1)
        statement_results.append(
            {
                "text": statement,
                "status": "candidate_evidence" if best_score >= 0.45 else "unknown",
                "lexical_coverage": round(best_score, 4),
                "evidence_index": best_index if best_index >= 0 else None,
                "evidence_indexes": mapped,
            }
        )

    requirements = list(packet["requirements"])
    requirement_to_evidence: list[list[int]] = [[] for _ in requirements]
    for evidence_index, hit in enumerate(evidence):
        for requirement_index in hit.get("requirements") or []:
            if 0 <= int(requirement_index) < len(requirement_to_evidence):
                requirement_to_evidence[int(requirement_index)].append(evidence_index)
    output_to_evidence = [list(item.get("evidence_indexes") or []) for item in statement_results]

    uncovered_requirements = [
        item
        for item in requirements
        if item["status"] not in {"evidence_found", "disagreement"}
    ]
    disagreements = [item for item in requirements if item["status"] == "disagreement"]
    unsupported_outputs = [item for item in statement_results if item["status"] == "unknown"]

    worst_residual: dict[str, object] | None = None
    if uncovered_requirements:
        worst = min(
            uncovered_requirements,
            key=lambda item: (int(item.get("candidate_count") or 0), int(item["index"])),
        )
        worst_residual = {
            "kind": "uncovered_requirement",
            "index": int(worst["index"]),
            "text": worst["text"],
        }
    elif unsupported_outputs:
        first = unsupported_outputs[0]
        worst_residual = {
            "kind": "unsupported_output",
            "index": statement_results.index(first),
            "text": first["text"],
        }

    clarification_needed = bool(disagreements) or bool(packet.get("clarification_needed"))
    if clarification_needed:
        result = "clarification_needed"
    elif uncovered_requirements or unsupported_outputs:
        result = "residuals_open"
    else:
        result = "match_move_complete"

    return {
        "schema": "rso-match-move-audit/v2",
        "project": packet["project"],
        "request": request,
        "requirement_coverage": requirements,
        "output_claim_coverage": statement_results,
        "matrix": {
            "requirement_to_evidence": requirement_to_evidence,
            "output_to_evidence": output_to_evidence,
        },
        "residuals": {
            "uncovered_requirements": uncovered_requirements,
            "unsupported_outputs": unsupported_outputs,
            "disagreements": disagreements,
        },
        "worst_residual": worst_residual,
        "partition": packet.get("partition"),
        "clarification_needed": clarification_needed,
        "result": result,
        "warning": "Lexical coverage locates candidate evidence; it does not prove factual correctness.",
        "context_packet_hash": packet["packet_hash"],
    }
