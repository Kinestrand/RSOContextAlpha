from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

from .config import Limits
from .db import Database, json_text
from .identity import resolve_existing_project, source_path_filter, utc_now
from .pointers import PlateCache, file_matches_hash, hydrate_chunk_row
from .run_budget import consume, set_budget
from .scope import search_scope
from .solvers import apply_solvers, packet_body_for_hash


STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "for",
    "to",
    "and",
    "or",
    "is",
    "it",
    "in",
    "on",
    "at",
    "be",
    "as",
    "by",
    "what",
    "how",
    "does",
    "did",
    "can",
}


def _tokens(value: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for token in re.findall(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", value.casefold()):
        if len(token) < 3 or token in STOPWORDS or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result[:24]


def _escape_fts_token(token: str) -> str:
    return '"' + token.replace('"', '""') + '"'


def _fts_expressions(value: str, project_ids: list[str] | None = None) -> list[str]:
    del project_ids  # project scope is SQL IN, never FTS (UUID hyphens break MATCH)
    tokens = _tokens(value)
    if not tokens:
        return []
    escaped = [_escape_fts_token(token) for token in tokens]
    expressions: list[str] = []
    if 2 <= len(escaped) <= 6:
        expressions.append(" AND ".join(escaped))
    expressions.append(" OR ".join(escaped))
    deduped: list[str] = []
    for expression in expressions:
        if expression not in deduped:
            deduped.append(expression)
    return deduped


PREFERRED_FIRST_SPLIT = 4
MAX_LEAVES = 12
LONG_LEAF_CHARS = 80

_CLAUSE_SPLIT = re.compile(r"\s*(?:;|\n|\.(?=\s+[A-Z]))\s*")
_AND_SPLIT = re.compile(r"\s+\band\b\s+", re.IGNORECASE)
_NEGATION_SPAN = re.compile(
    r"\b(?:must\s+not|do\s+not|don't|never|not\s+approved|unapproved)\b",
    re.IGNORECASE,
)
_AFFIRM_MUST = re.compile(r"\bmust\b(?!\s+not\b)", re.IGNORECASE)
_AFFIRM_SHALL = re.compile(r"\bshall\b(?!\s+not\b)", re.IGNORECASE)
_AFFIRM_REQUIRED = re.compile(r"\brequired\b", re.IGNORECASE)
_AFFIRM_APPROVED = re.compile(r"\bapproved\b", re.IGNORECASE)


def _normalize_clause(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" .")


def _clause_pieces(text: str) -> list[str]:
    pieces = [_normalize_clause(piece) for piece in _CLAUSE_SPLIT.split(text)]
    return [piece for piece in pieces if piece]


def _conjunction_pieces(text: str, *, min_length: int = 12) -> list[str]:
    parts = [_normalize_clause(piece) for piece in _AND_SPLIT.split(text)]
    parts = [piece for piece in parts if len(piece) >= min_length]
    return parts if len(parts) > 1 else []


def _has_compound_separator(text: str) -> bool:
    return ";" in text or " and " in text.casefold()


def _split_leaf_further(text: str, *, min_length: int = 12) -> list[str]:
    pieces = _clause_pieces(text)
    if len(pieces) <= 1:
        conjunctions = _conjunction_pieces(text, min_length=min_length)
        if conjunctions:
            pieces = conjunctions
    if len(pieces) <= 1:
        return [ _normalize_clause(text) or text ]
    return pieces


def split_requirements(query: str, limit: int = MAX_LEAVES) -> list[str]:
    """Split a request into checkable leaves.

    Preferred first split is up to four clauses. Long compound leaves are then
    expanded until ``limit`` (hard max 12) or they no longer split.
    """
    cap = max(1, min(limit, MAX_LEAVES))
    normalized = _normalize_clause(query)
    if not normalized:
        return []

    pieces = _clause_pieces(query)
    if len(pieces) == 1 and len(normalized) > LONG_LEAF_CHARS:
        conjunctions = _conjunction_pieces(normalized)
        if 1 < len(conjunctions) <= PREFERRED_FIRST_SPLIT:
            pieces = conjunctions
    if not pieces:
        pieces = [normalized]

    expanded = True
    while expanded and len(pieces) < cap:
        expanded = False
        next_pieces: list[str] = []
        for index, leaf in enumerate(pieces):
            remaining_after = len(pieces) - index - 1
            room = cap - len(next_pieces) - remaining_after
            if (
                room > 1
                and len(leaf) > LONG_LEAF_CHARS
                and _has_compound_separator(leaf)
            ):
                children = _split_leaf_further(leaf)
                if len(children) > 1:
                    next_pieces.extend(children[:room])
                    expanded = True
                    continue
            next_pieces.append(leaf)
        pieces = next_pieces
    return pieces[:cap] or [normalized]


def _text_has_negation(text: str) -> bool:
    return _NEGATION_SPAN.search(text) is not None


def _text_has_affirmative(text: str) -> bool:
    if _AFFIRM_MUST.search(text) or _AFFIRM_SHALL.search(text) or _AFFIRM_REQUIRED.search(text):
        return True
    for match in _AFFIRM_APPROVED.finditer(text):
        prefix = text[: match.start()].rstrip()
        if prefix.casefold().endswith("not"):
            continue
        return True
    return False


def texts_disagree(texts: list[str]) -> bool:
    """True when retrieved spans for one requirement observably conflict."""
    for index, left in enumerate(texts):
        left_neg = _text_has_negation(left)
        if not left_neg:
            continue
        for other_index, right in enumerate(texts):
            if other_index == index:
                continue
            if _text_has_affirmative(right):
                return True
    return False


def _leaf_stop_reason(
    status: str,
    *,
    blocked_by_max: bool = False,
) -> str:
    if status == "disagreement":
        return "disagreement"
    if status == "evidence_found":
        return "evidence_found"
    if status == "budget_exhausted":
        return "evidence_found"
    if blocked_by_max:
        return "max_leaves"
    return "unknown"


def _resolve_project(
    database: Database,
    *,
    path: str | Path | None,
    project_id: str | None,
    agent: str,
) -> dict[str, object]:
    # Query does not auto-register. Register/ingest first; resume and query error if missing.
    del agent
    return resolve_existing_project(database, path=path, project_id=project_id)


def _query_one(
    connection,
    *,
    expression: str,
    project_id: str,
    shared_ids: list[str],
    limit: int,
    path: str | Path | None = None,
) -> list[dict[str, object]]:
    project_ids = [project_id, *shared_ids]
    placeholders = ",".join("?" for _ in project_ids)
    path_sql, path_params = source_path_filter("s.resolved_path", path)
    if path_sql:
        # Path filter applies only to the active project. Domain/shared files live elsewhere.
        path_and = f" AND (s.project_id <> ? OR {path_sql})"
        path_bind: tuple[object, ...] = (project_id, *path_params)
    else:
        path_and = ""
        path_bind = ()
    # Isolate MATCH+rank so bm25 runs on the FTS table, then join. Outer SQL IN
    # still scopes projects. Inner LIMIT is a cost cap; keep it well above the
    # packet limit so project/domain/shared hits are not squeezed out.
    fts_cap = max(64, min(256, max(limit, 1) * 32))
    sql = f"""
        SELECT c.id AS chunk_id, c.text, c.heading, c.line_start, c.line_end,
               c.chunk_hash, s.relative_path, s.resolved_path, s.project_id,
               s.current_hash AS source_hash, s.media_type,
               p.display_name AS project_name,
               p.scope AS scope,
               CASE
                 WHEN s.project_id=? THEN 0
                 WHEN p.scope='domain' THEN 1
                 WHEN p.scope='shared' THEN 2
                 ELSE 3
               END AS scope_priority,
               fts.text_rank AS text_rank
        FROM (
            SELECT chunk_id AS fts_chunk_id, bm25(chunk_fts) AS text_rank
            FROM chunk_fts
            WHERE chunk_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        ) AS fts
        JOIN chunks c ON c.id = fts.fts_chunk_id
        JOIN source_versions v ON v.id=c.source_version_id
        JOIN sources s ON s.id=v.source_id
        JOIN projects p ON p.id=s.project_id
        WHERE s.project_id IN ({placeholders})
          AND s.active=1
          AND s.current_hash=v.content_hash{path_and}
        ORDER BY scope_priority ASC, text_rank ASC, s.relative_path ASC, c.line_start ASC
        LIMIT ?
    """
    try:
        rows = connection.execute(
            sql,
            (project_id, expression, fts_cap, *project_ids, *path_bind, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


GRAPH_LIKE_NODE_CAP = 500
GRAPH_PREFIX_MIN_LEN = 5


def _like_prefix(token: str) -> str:
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _graph_matches(connection, project_ids: list[str], tokens: list[str]) -> list[dict[str, object]]:
    if not tokens or not project_ids:
        return []
    project_placeholders = ",".join("?" for _ in project_ids)
    sampled = connection.execute(
        f"SELECT 1 FROM nodes n WHERE n.project_id IN ({project_placeholders}) LIMIT ?",
        (*project_ids, GRAPH_LIKE_NODE_CAP + 1),
    ).fetchall()
    large = len(sampled) > GRAPH_LIKE_NODE_CAP
    if large:
        # Substring LIKE cannot use indexes and the EXISTS join makes a full
        # node scan too expensive. Graph is secondary: equality/prefix only.
        graph_tokens = [token for token in tokens[:8] if len(token) >= GRAPH_PREFIX_MIN_LEN]
        if not graph_tokens:
            return []
        token_conditions = " OR ".join(
            "(n.canonical_name = ? OR n.canonical_name LIKE ? ESCAPE '\\')"
            for _ in graph_tokens
        )
        token_params: list[str] = []
        for token in graph_tokens:
            token_params.append(token)
            token_params.append(_like_prefix(token))
    else:
        graph_tokens = tokens[:8]
        token_conditions = " OR ".join("n.canonical_name LIKE ?" for _ in graph_tokens)
        token_params = [f"%{token}%" for token in graph_tokens]
    rows = connection.execute(
        f"SELECT n.id,n.project_id,n.kind,n.display_name,n.trust_state,n.properties_json "
        f"FROM nodes n WHERE n.project_id IN ({project_placeholders}) AND ({token_conditions}) "
        "AND EXISTS (SELECT 1 FROM edges e "
        "JOIN chunks c ON c.id=e.evidence_chunk_id "
        "JOIN source_versions v ON v.id=c.source_version_id "
        "JOIN sources s ON s.id=v.source_id "
        "WHERE e.project_id=n.project_id AND (e.subject_id=n.id OR e.object_id=n.id) "
        "AND s.active=1 AND s.current_hash=v.content_hash) "
        "ORDER BY CASE n.trust_state WHEN 'verified' THEN 0 WHEN 'observed' THEN 1 ELSE 2 END,"
        "n.kind,n.display_name LIMIT 20",
        (*project_ids, *token_params),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "project_id": row["project_id"],
            "kind": row["kind"],
            "name": row["display_name"],
            "trust_state": row["trust_state"],
            "properties": json.loads(row["properties_json"]),
        }
        for row in rows
    ]


def _claims_for_chunks(
    connection,
    project_ids: list[str],
    chunk_ids: list[int],
) -> list[dict[str, object]]:
    if not chunk_ids:
        return []
    project_placeholders = ",".join("?" for _ in project_ids)
    chunk_placeholders = ",".join("?" for _ in chunk_ids)
    rows = connection.execute(
        f"SELECT DISTINCT c.id,c.project_id,c.display_text,c.trust_state,c.created_by "
        f"FROM claims c JOIN claim_evidence ce ON ce.claim_id=c.id "
        f"WHERE c.project_id IN ({project_placeholders}) AND ce.chunk_id IN ({chunk_placeholders}) "
        "AND EXISTS (SELECT 1 FROM claim_evidence ce2 "
        "JOIN chunks ch ON ch.id=ce2.chunk_id "
        "JOIN source_versions v ON v.id=ch.source_version_id "
        "JOIN sources s ON s.id=v.source_id "
        "WHERE ce2.claim_id=c.id AND s.active=1 AND s.current_hash=v.content_hash) "
        "ORDER BY CASE c.trust_state WHEN 'verified' THEN 0 WHEN 'disputed' THEN 1 "
        "WHEN 'observed' THEN 2 ELSE 3 END, c.display_text",
        (*project_ids, *chunk_ids),
    ).fetchall()
    return [dict(row) for row in rows]


def _verified_claim_boost(
    connection,
    project_ids: list[str],
    already: list[dict[str, object]],
    limit: int = 4,
) -> list[dict[str, object]]:
    if limit <= 0:
        return already
    have = {str(item["id"]) for item in already}
    project_placeholders = ",".join("?" for _ in project_ids)
    rows = connection.execute(
        f"SELECT c.id,c.project_id,c.display_text,c.trust_state,c.created_by FROM claims c "
        f"WHERE c.project_id IN ({project_placeholders}) AND c.trust_state='verified' "
        "AND EXISTS (SELECT 1 FROM claim_evidence ce "
        "JOIN chunks ch ON ch.id=ce.chunk_id "
        "JOIN source_versions v ON v.id=ch.source_version_id "
        "JOIN sources s ON s.id=v.source_id "
        "WHERE ce.claim_id=c.id AND s.active=1 AND s.current_hash=v.content_hash) "
        "ORDER BY c.updated_at DESC, c.display_text LIMIT ?",
        (*project_ids, limit + len(have)),
    ).fetchall()
    extra = [dict(row) for row in rows if str(row["id"]) not in have]
    return already + extra[:limit]


def _proposed_claim_boost(
    connection,
    project_ids: list[str],
    already: list[dict[str, object]],
    limit: int = 8,
) -> list[dict[str, object]]:
    if limit <= 0:
        return already
    have = {str(item["id"]) for item in already}
    project_placeholders = ",".join("?" for _ in project_ids)
    rows = connection.execute(
        f"SELECT c.id,c.project_id,c.display_text,c.trust_state,c.created_by FROM claims c "
        f"WHERE c.project_id IN ({project_placeholders}) AND c.trust_state='proposed' "
        "ORDER BY c.updated_at DESC, c.display_text LIMIT ?",
        (*project_ids, limit + len(have)),
    ).fetchall()
    extra = [dict(row) for row in rows if str(row["id"]) not in have]
    return already + extra[:limit]


def _validation_matches(connection, claim_ids: list[str]) -> list[dict[str, object]]:
    if not claim_ids:
        return []
    placeholders = ",".join("?" for _ in claim_ids)
    rows = connection.execute(
        f"SELECT id,claim_id,validator,result,details_json,observed_at FROM validations "
        f"WHERE claim_id IN ({placeholders}) ORDER BY claim_id,observed_at,id",
        claim_ids,
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "claim_id": row["claim_id"],
            "validator": row["validator"],
            "result": row["result"],
            "details": json.loads(row["details_json"]),
            "observed_at": row["observed_at"],
        }
        for row in rows
    ]


def _live_backed_items(connection, items, *, kind, cache, dependencies):
    """Keep ledger items only while at least one backing plate still matches."""
    kept = []
    for item in items:
        if kind == "claim" and item["trust_state"] == "proposed":
            kept.append(item)
            continue
        if kind == "claim":
            join = "claim_evidence e JOIN chunks c ON c.id=e.chunk_id"
            condition = "e.claim_id=?"
            params = (item["id"],)
        else:
            join = "edges e JOIN chunks c ON c.id=e.evidence_chunk_id"
            condition = "e.project_id=? AND (e.subject_id=? OR e.object_id=?)"
            params = (item["project_id"], item["id"], item["id"])
        rows = connection.execute(
            "SELECT DISTINCT s.resolved_path,s.current_hash AS source_hash,"
            "c.line_start,c.line_end,c.text FROM " + join +
            " JOIN source_versions v ON v.id=c.source_version_id"
            " JOIN sources s ON s.id=v.source_id WHERE " + condition +
            " AND s.active=1 AND s.current_hash=v.content_hash", params,
        ).fetchall()
        live = False
        for row in rows:
            matches = not hydrate_chunk_row(dict(row), cache)["stale"]
            dependencies[(row["resolved_path"], row["source_hash"])] = matches
            if matches:
                live = True
        if live:
            kept.append(item)
    return kept


def _claim_chars(claim: dict[str, object]) -> int:
    return len(str(claim.get("display_text") or ""))


def _graph_chars(node: dict[str, object]) -> int:
    return len(str(node.get("name") or "")) + len(
        json.dumps(node.get("properties") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _build_hit(row: dict[str, object], text: str, truncated: bool) -> dict[str, object]:
    return {
        "project_id": row["project_id"],
        "project_name": row["project_name"],
        "scope": row.get("scope"),
        "relative_path": row["relative_path"],
        "resolved_path": row["resolved_path"],
        "line_start": int(row["line_start"]),
        "line_end": int(row["line_end"]),
        "heading": row["heading"],
        "text": text,
        "truncated": truncated,
        "chunk_hash": row["chunk_hash"],
        "source_hash": row["source_hash"],
        "stale": bool(row.get("stale")),
        "media_type": row["media_type"],
        "span_kind": (
            "extracted_text_lines"
            if row["media_type"]
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            else "source_lines"
        ),
        "requirements": sorted(row["requirements"]),
        "rank": float(row["text_rank"]),
    }


def _apply_whole_packet_budget(
    ordered_hits: list[dict[str, object]],
    claims: list[dict[str, object]],
    graph_nodes: list[dict[str, object]],
    token_budget: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    char_budget = max(256, token_budget * 4)
    verified = [claim for claim in claims if claim.get("trust_state") == "verified"]
    others = [claim for claim in claims if claim.get("trust_state") != "verified"]

    used = sum(_claim_chars(claim) for claim in verified)
    kept_others: list[dict[str, object]] = []
    for claim in others:
        size = _claim_chars(claim)
        if used + size <= char_budget:
            kept_others.append(claim)
            used += size

    kept_graph: list[dict[str, object]] = []
    for node in graph_nodes:
        size = _graph_chars(node)
        if used + size <= char_budget:
            kept_graph.append(node)
            used += size

    hits: list[dict[str, object]] = []
    for row in ordered_hits:
        if used >= char_budget:
            break
        text = str(row["text"])
        remaining = char_budget - used
        truncated = len(text) > remaining
        included_text = text[:remaining] if truncated else text
        hits.append(_build_hit(row, included_text, truncated))
        used += len(included_text)

    return hits, verified + kept_others, kept_graph


def _retrieve_requirement(
    connection,
    requirement: str,
    *,
    project_id: str,
    shared_ids: list[str],
    limit: int,
    path: str | Path | None,
    scoped_ids: list[str],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for expression in _fts_expressions(requirement, scoped_ids):
        rows = _query_one(
            connection,
            expression=expression,
            project_id=project_id,
            shared_ids=shared_ids,
            limit=limit,
            path=path,
        )
        if rows:
            break
    return rows


def _build_partition(
    query: str,
    coverage: list[dict[str, object]],
    hits: list[dict[str, object]],
) -> dict[str, object]:
    included = {index: 0 for index in range(len(coverage))}
    for hit in hits:
        if hit.get("stale"):
            continue
        for index in hit.get("requirements") or []:
            if index in included:
                included[index] += 1
    leaves = []
    for item in coverage:
        status = str(item.get("status") or "")
        index = int(item["index"])
        leaves.append(
            {
                "index": index,
                "text": item["text"],
                "stop_reason": item.get("stop_reason") or _leaf_stop_reason(status),
                "checkable": bool(status),
                "evidence_count": included.get(index, 0),
                "status": status,
            }
        )
    return {
        "root": query,
        "method": "deterministic-split",
        "max_leaves": MAX_LEAVES,
        "leaves": leaves,
    }


def query_context(
    database: Database,
    query: str,
    *,
    path: str | Path | None = None,
    project_id: str | None = None,
    agent: str = "unknown",
    limit: int = 8,
    token_budget: int = 4_000,
    use_cache: bool = True,
    run_budget: int | None = None,
) -> dict[str, object]:
    """Return a deterministic context packet for an already-registered project.

    Query does not auto-register a workspace. If ``path`` / cwd has no project,
    this raises ValueError (same as resume). Run ingest first.
    """
    database.initialize()
    started = time.perf_counter()
    project = _resolve_project(database, path=path, project_id=project_id, agent=agent)
    project_id_value = str(project["id"])
    with database.transaction() as budget_connection:
        if run_budget is not None:
            set_budget(budget_connection, project_id_value, agent, int(run_budget))
        budget = consume(budget_connection, project_id_value, agent, n=1)
    run_info = {"remaining": int(budget["remaining"]), "initial": int(budget["initial"])}
    requirements = split_requirements(query, limit=MAX_LEAVES)
    config = {
        "algorithm": "fts5-bounded-requirements-v4-live-sources",
        "limit_per_requirement": limit,
        "max_requirements": MAX_LEAVES,
        "max_leaves": MAX_LEAVES,
        "token_budget": token_budget,
    }

    search_order: list[dict[str, object]] = []
    with database.connect() as connection:
        scoped = search_scope(connection, project_id_value, path=path)
        search_order = [
            {"id": item["id"], "scope": item["scope"], "name": item["name"]}
            for item in scoped["order"]
        ]
        shared_ids = [str(item["id"]) for item in search_order[1:]]
        version_vector = dict(scoped["corpus_versions"])
        cache_key = hashlib.sha256(
            json_text(
                {
                    "query": query,
                    "project_id": project_id_value,
                    "path": str(path) if path else "",
                    "versions": version_vector,
                    "config": config,
                    "run": run_info,
                }
            ).encode("utf-8")
        ).hexdigest()
        if use_cache:
            cached = connection.execute(
                "SELECT packet_json FROM cache WHERE cache_key=?", (cache_key,)
            ).fetchone()
            if cached is not None:
                cached_body = json.loads(cached["packet_json"])
                packet = cached_body.get("packet", {})
                # Corpus versions change on ingest, but files can change between
                # ingests. Include candidates excluded by budget and backing
                # plates for graph/claim boosts, not only displayed evidence.
                dependencies = cached_body.get("source_checks")
                if dependencies is None or not all(
                    item.get("matched") and
                    file_matches_hash(Path(item["resolved_path"]), item["source_hash"])
                    for item in dependencies
                ):
                    cached = None
            if cached is not None:
                with database.transaction() as write_connection:
                    write_connection.execute(
                        "UPDATE cache SET last_used_at=?,hit_count=hit_count+1 WHERE cache_key=?",
                        (utc_now(), cache_key),
                    )
                apply_solvers(packet)
                _record_run(
                    database,
                    project_id_value,
                    query,
                    project["corpus_version"],
                    config,
                    packet,
                    started,
                    remaining_budget=run_info["remaining"],
                )
                return packet

        scoped_ids = [project_id_value, *shared_ids]
        plate_cache = PlateCache()
        dependencies: dict[tuple[str, str], bool] = {}
        leaves: list[dict[str, object]] = [
            {
                "text": requirement,
                "rows": [
                    hydrate_chunk_row(row, plate_cache)
                    for row in _retrieve_requirement(
                        connection,
                        requirement,
                        project_id=project_id_value,
                        shared_ids=shared_ids,
                        limit=limit,
                        path=path,
                        scoped_ids=scoped_ids,
                    )
                ],
                "extra_split": False,
                "blocked_by_max": False,
            }
            for requirement in requirements
        ]
        index = 0
        while index < len(leaves) and len(leaves) < MAX_LEAVES:
            leaf = leaves[index]
            if leaf["rows"] or leaf["extra_split"] or not _has_compound_separator(str(leaf["text"])):
                index += 1
                continue
            children = [
                child
                for child in _split_leaf_further(str(leaf["text"]), min_length=3)
                if child and child != leaf["text"]
            ]
            if len(children) <= 1:
                index += 1
                continue
            room = MAX_LEAVES - len(leaves) + 1
            taken = children[:room]
            replacements = []
            for child in taken:
                replacements.append(
                    {
                        "text": child,
                        "rows": [
                            hydrate_chunk_row(row, plate_cache)
                            for row in _retrieve_requirement(
                                connection,
                                child,
                                project_id=project_id_value,
                                shared_ids=shared_ids,
                                limit=limit,
                                path=path,
                                scoped_ids=scoped_ids,
                            )
                        ],
                        "extra_split": True,
                        "blocked_by_max": False,
                    }
                )
            if len(children) > len(taken):
                replacements[-1]["blocked_by_max"] = True
            leaves[index : index + 1] = replacements
            index += len(replacements)
        if len(leaves) >= MAX_LEAVES:
            for leaf in leaves:
                if (
                    not leaf["rows"]
                    and not leaf["extra_split"]
                    and _has_compound_separator(str(leaf["text"]))
                ):
                    leaf["blocked_by_max"] = True

        selected: dict[tuple[str, str], dict[str, object]] = {}
        coverage: list[dict[str, object]] = []
        disagreement_keys: set[tuple[str, str]] = set()
        for requirement_index, leaf in enumerate(leaves):
            rows = list(leaf["rows"])
            hit_keys: list[tuple[str, str]] = []
            for row in rows:
                dependencies[(row["resolved_path"], row["source_hash"])] = not row["stale"]
                key = (row["relative_path"], row["chunk_hash"])
                if not row["stale"]:
                    hit_keys.append(key)
                if key not in selected:
                    selected[key] = row | {"requirements": [requirement_index]}
                elif requirement_index not in selected[key]["requirements"]:
                    selected[key]["requirements"].append(requirement_index)
            live_rows = [row for row in rows if not row["stale"]]
            row_texts = [str(row["text"]) for row in live_rows]
            # Lexical conflict is only a disagreement when two source paths differ.
            if live_rows and len({str(row["relative_path"]) for row in live_rows}) > 1 and texts_disagree(row_texts):
                status = "disagreement"
                for key in hit_keys:
                    disagreement_keys.add(key)
            elif hit_keys:
                status = "evidence_found"
            else:
                status = "unknown"
            coverage.append(
                {
                    "index": requirement_index,
                    "text": leaf["text"],
                    "status": status,
                    "candidate_count": len(hit_keys),
                    "stop_reason": _leaf_stop_reason(
                        status, blocked_by_max=bool(leaf["blocked_by_max"])
                    ),
                }
            )
        requirements = [str(item["text"]) for item in coverage]

        ordered_hits = sorted(
            selected.values(),
            key=lambda item: (
                bool(item["stale"]),
                0 if (item["relative_path"], item["chunk_hash"]) in disagreement_keys else 1,
                min(item["requirements"]),
                int(item["scope_priority"]),
                float(item["text_rank"]),
                str(item["relative_path"]),
                int(item["line_start"]),
            ),
        )
        chunk_ids = [int(row["chunk_id"]) for row in ordered_hits if not row["stale"]]
        claims = _proposed_claim_boost(
            connection,
            scoped_ids,
            _verified_claim_boost(
                connection,
                scoped_ids,
                _claims_for_chunks(connection, scoped_ids, chunk_ids),
            ),
        )
        graph_nodes = _graph_matches(connection, scoped_ids, _tokens(query))
        claims = _live_backed_items(
            connection, claims, kind="claim", cache=plate_cache, dependencies=dependencies
        )
        graph_nodes = _live_backed_items(
            connection, graph_nodes, kind="node", cache=plate_cache, dependencies=dependencies
        )
        hits, claims, graph_nodes = _apply_whole_packet_budget(
            ordered_hits, claims, graph_nodes, token_budget
        )
        validations = _validation_matches(connection, [str(claim["id"]) for claim in claims])

        included_by_requirement = {index: 0 for index in range(len(coverage))}
        for hit in hits:
            if hit.get("stale"):
                continue
            for index in hit["requirements"]:
                included_by_requirement[index] += 1
        for item in coverage:
            blocked = str(item.get("stop_reason")) == "max_leaves"
            if item["status"] == "evidence_found" and included_by_requirement[item["index"]] == 0:
                item["status"] = "budget_exhausted"
            item["stop_reason"] = _leaf_stop_reason(
                str(item["status"]),
                blocked_by_max=blocked,
            )

    clarification_needed = any(item["status"] == "disagreement" for item in coverage)
    partition = _build_partition(query, coverage, hits)
    packet: dict[str, object] = {
        "schema": "rso-context-packet/v2",
        "project": {
            "id": project_id_value,
            "name": project["display_name"],
            "scope": project["scope"],
        },
        "search_order": search_order,
        "corpus_versions": version_vector,
        "query": query,
        "requirements": coverage,
        "evidence": hits,
        "graph_nodes": graph_nodes,
        "claims": claims,
        "validations": validations,
        "partition": partition,
        "clarification_needed": clarification_needed,
        "config": config,
        "run": run_info,
    }
    packet_hash = hashlib.sha256(json_text(packet_body_for_hash(packet)).encode("utf-8")).hexdigest()
    packet["packet_hash"] = packet_hash
    # checks are attached after packet_hash so they are not hashed
    apply_solvers(packet)
    # Freshness dependencies are cache metadata, not duplicated in output/run packets.
    packet_json = json_text({
        "packet": packet,
        "source_checks": [
            {"resolved_path": source_path, "source_hash": source_hash,
             "matched": dependencies[(source_path, source_hash)]}
            for source_path, source_hash in sorted(dependencies)
        ],
    })
    now = utc_now()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO cache(cache_key,project_id,corpus_version,packet_json,packet_hash,"
            "created_at,last_used_at,hit_count) VALUES(?,?,?,?,?,?,?,0) "
            "ON CONFLICT(cache_key) DO UPDATE SET packet_json=excluded.packet_json,"
            "packet_hash=excluded.packet_hash,last_used_at=excluded.last_used_at",
            (
                cache_key,
                project_id_value,
                int(project["corpus_version"]),
                packet_json,
                packet_hash,
                now,
                now,
            ),
        )
    _record_run(
        database,
        project_id_value,
        query,
        project["corpus_version"],
        config,
        packet,
        started,
        remaining_budget=run_info["remaining"],
    )
    return packet


def _record_run(
    database: Database,
    project_id: str,
    query: str,
    corpus_version: int,
    config: dict[str, object],
    packet: dict[str, object],
    started: float,
    remaining_budget: int | None = None,
) -> None:
    packet_json = json_text(packet)
    packet_hash = str(packet.get("packet_hash") or hashlib.sha256(packet_json.encode()).hexdigest())
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO runs(id,project_id,query_text,corpus_version,config_json,packet_json,"
            "packet_hash,elapsed_ms,created_at,remaining_budget) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                project_id,
                query,
                int(corpus_version),
                json_text(config),
                packet_json,
                packet_hash,
                (time.perf_counter() - started) * 1000,
                utc_now(),
                remaining_budget,
            ),
        )
