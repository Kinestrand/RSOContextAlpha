from __future__ import annotations

import hashlib
import json
import os
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


def attach_empty_result(packet: dict[str, object]) -> None:
    """Label unknown/stale-only packets. Does not change packet_hash."""
    hits = packet.get("evidence") or []
    current = [item for item in hits if isinstance(item, dict) and not item.get("stale") and item.get("text")]
    if current:
        return
    kind = "only_stale_spans" if hits else "no_matching_spans"
    packet["empty_result"] = {
        "kind": kind,
        "message": (
            "No current spans matched this query. "
            "Untracked and ignored files remain outside the corpus. "
            "This is not a proof of absence."
        ),
    }


STOPWORDS = frozenset(
    """
    the a an of for to and or is it in on at be as by what how does did can
    are was were been being has have had do done this that these those there their
    them they then than with without from into onto over under about after before
    when where which while who whom whose why will would should could may might must
    shall not no yes all any each some such only also just very more most other our
    ours your yours his her its use used using uses get got make made like need needs
    want wants find show tell give know see look please let lets out off per via
    thing things way ways
    """.split()
)


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


AND_MAX_TOKENS = 8
PREFIX_MIN_CHARS = 4


def _prefix_token(token: str) -> str:
    # Prefix match on whole identifiers (porter stems are matched as prefixes too).
    if len(token) >= PREFIX_MIN_CHARS and re.fullmatch(r"[a-z0-9_]+", token):
        return _escape_fts_token(token) + "*"
    return _escape_fts_token(token)


def _fts_expressions(value: str, project_ids: list[str] | None = None) -> list[str]:
    """AND of all terms, then OR of all terms, then OR with prefix matching.

    Porter stemming already folds plurals and verb forms, so the prefix pass
    is a last resort that runs only when nothing else matched.
    """
    del project_ids  # project scope is SQL IN, never FTS (UUID hyphens break MATCH)
    tokens = _tokens(value)
    if not tokens:
        return []
    escaped = [_escape_fts_token(token) for token in tokens]
    expressions: list[str] = []
    if 2 <= len(escaped) <= AND_MAX_TOKENS:
        expressions.append(" AND ".join(escaped))
    if 3 <= len(escaped) <= 6:
        # "Most terms": any n-1 of the n terms together, before a plain OR.
        subsets = [
            "(" + " AND ".join(term for index, term in enumerate(escaped) if index != skip) + ")"
            for skip in range(len(escaped))
        ]
        expressions.append(" OR ".join(subsets))
    expressions.append(" OR ".join(escaped))
    prefixed = " OR ".join(_prefix_token(token) for token in tokens)
    if prefixed != expressions[-1]:
        expressions.append(prefixed)
    deduped: list[str] = []
    for expression in expressions:
        if expression not in deduped:
            deduped.append(expression)
    return deduped


def _scoped_match(expression: str, project_ids: list[str]) -> str:
    """Restrict an FTS expression to the scoped projects inside MATCH.

    Project ids are quoted phrases, so UUID hyphens tokenize as a phrase rather
    than breaking the query. Ranking ignores the project column (weight 0), so
    scores are the same as an unscoped search; the inner LIMIT now only sees
    in-scope rows instead of the top hits of every project in the ledger.
    """
    ids = [pid for pid in project_ids if pid]
    if not ids:
        return expression
    scope = " OR ".join(_escape_fts_token(pid) for pid in ids)
    return f"({expression}) AND project_id : ({scope})"


PREFERRED_FIRST_SPLIT = 4
MAX_LEAVES = 12
LONG_LEAF_CHARS = 80

_CLAUSE_SPLIT = re.compile(r"\s*(?:;|\n|\.(?=\s+[A-Z]))\s*")
_AND_SPLIT = re.compile(r"\s+\band\b\s+", re.IGNORECASE)
# One negation rule for the whole codebase: the conflict detector and rso_check
# used to disagree about plain "not", so the same sentence read two ways.
_NEGATION_SPAN = re.compile(
    r"\b(?:not|never|cannot|no\s+longer|unapproved)\b|n't\b",
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


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_TOPIC_TOKEN = re.compile(r"[a-z0-9][a-z0-9_\-]*")
_TOPIC_STOPWORDS = frozenset(
    "a an and are as at be been by can could do does don't for from has have if in into is it its "
    "may must never no not of on or shall should so than that the their then there these this those "
    "to use used uses using was were when which while will with without would you your "
    "approved unapproved required decision".split()
)
# A negated sentence and an affirmative sentence conflict only when they are about the same thing.
_MIN_SHARED_TOPIC_TOKENS = 2
_MIN_TOPIC_OVERLAP = 0.5


def _topic_tokens(sentence: str) -> frozenset[str]:
    tokens = _TOPIC_TOKEN.findall(sentence.casefold())
    return frozenset(token for token in tokens if len(token) > 2 and token not in _TOPIC_STOPWORDS)


def _polar_sentences(text: str) -> tuple[list[frozenset[str]], list[frozenset[str]]]:
    negated: list[frozenset[str]] = []
    affirmed: list[frozenset[str]] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        if not sentence.strip():
            continue
        if _text_has_negation(sentence):
            negated.append(_topic_tokens(sentence))
        elif _text_has_affirmative(sentence):
            affirmed.append(_topic_tokens(sentence))
    return negated, affirmed


def _same_topic(left: frozenset[str], right: frozenset[str]) -> bool:
    shared = len(left & right)
    if shared < _MIN_SHARED_TOPIC_TOKENS:
        return False
    return shared / min(len(left), len(right)) >= _MIN_TOPIC_OVERLAP


def texts_disagree(texts: list[str], paths: list[str] | None = None) -> bool:
    """True when retrieved spans for one requirement observably conflict.

    A conflict needs a negated sentence in one span and an affirmative sentence about
    the same topic in another span. When paths are given, the two spans must come from
    different source paths.
    """
    polar = [_polar_sentences(text) for text in texts]
    for index, (negated, _) in enumerate(polar):
        if not negated:
            continue
        for other_index, (_, affirmed) in enumerate(polar):
            if other_index == index or not affirmed:
                continue
            if paths is not None and paths[index] == paths[other_index]:
                continue
            if any(_same_topic(left, right) for left in negated for right in affirmed):
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
            SELECT chunk_id AS fts_chunk_id, bm25(chunk_fts, 1.0, 1.5, 1.0, 0.0, 0.0) AS text_rank
            FROM chunk_fts
            WHERE chunk_fts MATCH ?
            ORDER BY text_rank
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
            (project_id, _scoped_match(expression, project_ids), fts_cap, *project_ids, *path_bind, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


GRAPH_LIKE_NODE_CAP = 500
GRAPH_PREFIX_MIN_LEN = 5


def _like_prefix(token: str) -> str:
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


GRAPH_RESULT_CAP = 20
GRAPH_PREFIX_CANDIDATES = 48
# Nodes that only restate the project id are structural, never evidence of topic.
GRAPH_SKIP_KINDS = ("project",)
_PREFIX_CEILING = chr(0x10FFFF)

_NODE_LIVE_SQL = (
    "SELECT 1 FROM edges e "
    "JOIN chunks c ON c.id=e.evidence_chunk_id "
    "JOIN source_versions v ON v.id=c.source_version_id "
    "JOIN sources s ON s.id=v.source_id "
    "WHERE e.project_id=? AND e.{column}=? AND s.active=1 AND s.current_hash=v.content_hash LIMIT 1"
)


def _node_has_live_edge(connection, project_id: str, node_id: str) -> bool:
    for column in ("subject_id", "object_id"):
        if connection.execute(_NODE_LIVE_SQL.format(column=column), (project_id, node_id)).fetchone():
            return True
    return False


def _graph_score(canonical_name: str, tokens: list[str]) -> tuple[int, bool]:
    name = canonical_name.casefold()
    exact = name in tokens
    return sum(1 for token in tokens if token in name), exact


def _graph_matches(connection, project_ids: list[str], tokens: list[str]) -> list[dict[str, object]]:
    """Graph nodes that name the query's terms, most relevant first.

    Large projects use the (project_id, canonical_name) index for equality and
    prefix ranges instead of scanning every node, then check liveness only for
    the few candidates that survive scoring. A node must share at least two
    query terms (or equal one exactly) when the query has three or more terms,
    so a single common word no longer pads the packet with unrelated cards.
    """
    if not tokens or not project_ids:
        return []
    columns = "n.id,n.project_id,n.kind,n.canonical_name,n.display_name,n.trust_state,n.properties_json"
    skip = ",".join("?" for _ in GRAPH_SKIP_KINDS)
    candidates: dict[str, sqlite3.Row] = {}
    for scoped_id in project_ids:
        sampled = connection.execute(
            "SELECT 1 FROM nodes n WHERE n.project_id=? LIMIT ?",
            (scoped_id, GRAPH_LIKE_NODE_CAP + 1),
        ).fetchall()
        if not sampled:
            continue
        if len(sampled) <= GRAPH_LIKE_NODE_CAP:
            graph_tokens = tokens[:8]
            conditions = " OR ".join("n.canonical_name LIKE ?" for _ in graph_tokens)
            for row in connection.execute(
                f"SELECT {columns} FROM nodes n WHERE n.project_id=? AND n.kind NOT IN ({skip}) "
                f"AND ({conditions})",
                (scoped_id, *GRAPH_SKIP_KINDS, *[f"%{token}%" for token in graph_tokens]),
            ):
                candidates.setdefault(str(row["id"]), row)
            continue
        for token in tokens[:8]:
            for row in connection.execute(
                f"SELECT {columns} FROM nodes n WHERE n.project_id=? AND n.canonical_name=? "
                f"AND n.kind NOT IN ({skip})",
                (scoped_id, token, *GRAPH_SKIP_KINDS),
            ):
                candidates.setdefault(str(row["id"]), row)
            if len(token) < GRAPH_PREFIX_MIN_LEN:
                continue
            for row in connection.execute(
                f"SELECT {columns} FROM nodes n WHERE n.project_id=? "
                "AND n.canonical_name>=? AND n.canonical_name<? "
                f"AND n.kind NOT IN ({skip}) ORDER BY n.canonical_name LIMIT ?",
                (scoped_id, token, token + _PREFIX_CEILING, *GRAPH_SKIP_KINDS, GRAPH_PREFIX_CANDIDATES),
            ):
                candidates.setdefault(str(row["id"]), row)
    if not candidates:
        return []
    score_tokens = tokens[:8]
    need = 2 if len(score_tokens) >= 3 else 1
    trust_order = {"verified": 0, "observed": 1}
    scored = []
    for row in candidates.values():
        score, exact = _graph_score(str(row["canonical_name"]), score_tokens)
        if not exact and score < need:
            continue
        scored.append((row, score + (2 if exact else 0)))
    scored.sort(
        key=lambda item: (
            -item[1],
            trust_order.get(str(item[0]["trust_state"]), 2),
            str(item[0]["kind"]),
            str(item[0]["display_name"]),
        )
    )
    kept: list[tuple[sqlite3.Row, int]] = []
    for row, score in scored:
        if len(kept) >= GRAPH_RESULT_CAP:
            break
        if _node_has_live_edge(connection, str(row["project_id"]), str(row["id"])):
            kept.append((row, score))
    kept.sort(
        key=lambda item: (
            trust_order.get(str(item[0]["trust_state"]), 2),
            -item[1],
            str(item[0]["kind"]),
            str(item[0]["display_name"]),
        )
    )
    return [
        {
            "id": row["id"],
            "project_id": row["project_id"],
            "kind": row["kind"],
            "name": row["display_name"],
            "trust_state": row["trust_state"],
            "properties": json.loads(row["properties_json"]),
            "match_score": score,
        }
        for row, score in kept
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
    """Keep ledger items only while at least one backing plate still matches.

    Stops at the first live plate per item instead of hydrating every edge of a
    hub node. Every plate it did read is recorded as a cache dependency, so a
    cached packet is reused only while those same plates still match.
    """
    kept = []
    for item in items:
        if kind == "claim" and item["trust_state"] == "proposed":
            kept.append(item)
            continue
        if kind == "claim":
            queries = [("claim_evidence e JOIN chunks c ON c.id=e.chunk_id", "e.claim_id=?", (item["id"],))]
        else:
            join = "edges e JOIN chunks c ON c.id=e.evidence_chunk_id"
            queries = [
                (join, "e.project_id=? AND e.subject_id=?", (item["project_id"], item["id"])),
                (join, "e.project_id=? AND e.object_id=?", (item["project_id"], item["id"])),
            ]
        live = False
        checked: set[tuple[str, str, int, int]] = set()
        for join, condition, params in queries:
            cursor = connection.execute(
                "SELECT s.resolved_path,s.current_hash AS source_hash,"
                "c.line_start,c.line_end,c.text FROM " + join +
                " JOIN source_versions v ON v.id=c.source_version_id"
                " JOIN sources s ON s.id=v.source_id WHERE " + condition +
                " AND s.active=1 AND s.current_hash=v.content_hash", params,
            )
            for row in cursor:
                key = (row["resolved_path"], row["source_hash"], row["line_start"], row["line_end"])
                if key in checked:
                    continue
                checked.add(key)
                matches = not hydrate_chunk_row(dict(row), cache)["stale"]
                dependency = (row["resolved_path"], row["source_hash"])
                dependencies[dependency] = dependencies.get(dependency, True) and matches
                if matches:
                    live = True
                    break
            cursor.close()
            if live:
                break
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


OR_MIN_COVERAGE_TOKENS = 4
OR_MIN_COVERAGE = 2


def _token_coverage(row: dict[str, object], tokens: list[str]) -> int:
    haystack = " ".join(
        str(row.get(key) or "") for key in ("text", "heading", "relative_path")
    ).casefold()
    return sum(1 for token in tokens if token in haystack)


def _retrieve_requirement(
    connection,
    requirement: str,
    *,
    project_id: str,
    shared_ids: list[str],
    limit: int,
    path: str | Path | None,
    scoped_ids: list[str],
    strict_or: bool = False,
) -> list[dict[str, object]]:
    """FTS rows for one requirement: all-terms (AND) first, OR fallback to fill.

    AND hits always rank first. When they do not fill ``limit``, the any-term
    fallback adds the rest; for requests with 4+ terms it drops single-term
    spans whenever spans with two or more terms exist, and ranks by how many
    terms they hold, so one common word no longer pads the packet.
    """
    if limit <= 0:
        return []
    tokens = _tokens(requirement)
    expressions = _fts_expressions(requirement, scoped_ids)
    rows: list[dict[str, object]] = []
    seen: set[int] = set()
    # Fetch a little more than needed so per-file dedup still fills the limit.
    want = max(1, limit) * 2
    for position, expression in enumerate(expressions):
        if len(rows) >= want:
            break
        if "*" in expression and rows:
            break  # prefix pass only when nothing else matched
        is_or_fallback = " OR " in expression and "(" not in expression and (position > 0 or len(expressions) == 1)
        filtering = strict_or and is_or_fallback and len(tokens) >= OR_MIN_COVERAGE_TOKENS
        found = _query_one(
            connection,
            expression=expression,
            project_id=project_id,
            shared_ids=shared_ids,
            limit=want * 4 if filtering else want,
            path=path,
        )
        if filtering and found:
            scored = [(row, _token_coverage(row, tokens)) for row in found]
            if any(item[1] >= OR_MIN_COVERAGE for item in scored):
                scored = [item for item in scored if item[1] >= OR_MIN_COVERAGE]
            scored.sort(key=lambda item: (int(item[0]["scope_priority"]), -item[1]))
            found = [row for row, _ in scored]
        for row in found:
            chunk_id = int(row["chunk_id"])
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            row["match_pass"] = position
            rows.append(row)
            if len(rows) >= want:
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


_INLINE_SPACE = re.compile(r"[ \t\f\v]+")


def normalize_query_for_cache(query: str) -> str:
    """Spacing-only normalization: runs of spaces/tabs collapse, line ends trim.

    Newlines and case are kept because they change how a request splits.
    """
    lines = [_INLINE_SPACE.sub(" ", line).strip() for line in str(query).strip().splitlines()]
    return "\n".join(lines)


def normalize_scope_path(path: str | Path | None) -> str:
    if not path:
        return ""
    try:
        resolved = str(Path(path).expanduser().resolve())
    except OSError:
        resolved = str(path)
    return os.path.normcase(resolved.rstrip("/\\") or resolved)


def normalize_agent(agent: str | None) -> str | None:
    value = str(agent or "").strip()
    return value or None


def cache_key_for(
    *,
    query: str,
    project_id: str,
    path: str | Path | None,
    versions: dict[str, int],
    config: dict[str, object],
) -> str:
    return hashlib.sha256(
        json_text(
            {
                "query": normalize_query_for_cache(query),
                "project_id": project_id,
                "path": normalize_scope_path(path),
                "versions": versions,
                "config": config,
                "cache_schema": 2,
            }
        ).encode("utf-8")
    ).hexdigest()


BOARD_DIR_NAMES = frozenset({".agent-coordination", "agent-coordination", ".board", "board", "inbox"})
CLAIM_MIN_WORDS = 4
MERGE_MAX_CHARS = 2_400
CLAIM_MAX_CHARS = 400


def _is_board_path(relative_path: str) -> bool:
    parts = [part.casefold() for part in str(relative_path).replace("\\", "/").split("/")]
    return any(part in BOARD_DIR_NAMES for part in parts[:-1])


def _fill_stale_text(connection, rows: list[dict[str, object]]) -> None:
    """A stale plate (file edited since ingest) used to come back with no text.

    The FTS row still holds the text that was indexed; return it, labelled
    stale, so the agent sees what matched and knows to re-read the file.
    """
    missing = [row for row in rows if row.get("stale") and not row.get("text")]
    for row in missing:
        found = connection.execute(
            "SELECT f.text FROM chunk_fts_map m JOIN chunk_fts f ON f.rowid=m.fts_rowid "
            "WHERE m.chunk_id=? LIMIT 1",
            (int(row["chunk_id"]),),
        ).fetchone()
        if found is not None and found[0]:
            row["text"] = str(found[0])
            row["stale_text"] = "indexed_copy"


def _clip_long_text(text: str, tokens: list[str], max_chars: int) -> tuple[str, bool]:
    """Window a very long span (minified or one-line files) around the first match."""
    if len(text) <= max_chars:
        return text, False
    folded = text.casefold()
    first = min((folded.find(token) for token in tokens if token in folded), default=0)
    start = max(0, first - max_chars // 4)
    return text[start : start + max_chars], True


def _merge_adjacent(hits: list[dict[str, object]]) -> list[dict[str, object]]:
    """Merge spans of one file whose line ranges touch or overlap."""
    by_file: dict[tuple[str, str], list[dict[str, object]]] = {}
    order: list[tuple[str, str]] = []
    for hit in hits:
        key = (str(hit["project_id"]), str(hit["relative_path"]))
        if key not in by_file:
            by_file[key] = []
            order.append(key)
        by_file[key].append(hit)
    merged_all: dict[int, dict[str, object]] = {}
    for key in order:
        group = sorted(by_file[key], key=lambda item: int(item["line_start"]))
        current: dict[str, object] | None = None
        for hit in group:
            if (
                current is not None
                and bool(current["stale"]) == bool(hit["stale"])
                and int(hit["line_start"]) <= int(current["line_end"]) + 1
                and len(str(current["text"])) + len(str(hit["text"])) <= MERGE_MAX_CHARS
            ):
                if int(hit["line_end"]) > int(current["line_end"]):
                    current["text"] = str(current["text"]).rstrip("\n") + "\n" + str(hit["text"])
                    current["line_end"] = int(hit["line_end"])
                current["requirements"] = sorted(set(current["requirements"]) | set(hit["requirements"]))
                current["text_rank"] = min(float(current["text_rank"]), float(hit["text_rank"]))
                current["match_pass"] = min(int(current.get("match_pass", 0)), int(hit.get("match_pass", 0)))
                current.setdefault("merged_chunks", 1)
                current["merged_chunks"] += 1
                continue
            current = dict(hit)
            current["requirements"] = list(hit["requirements"])
            merged_all[id(hit)] = current
    # keep original priority order: position of the first member
    result = []
    for hit in hits:
        if id(hit) in merged_all:
            result.append(merged_all[id(hit)])
    return result


def _limit_per_file(hits: list[dict[str, object]], max_per_file: int) -> list[dict[str, object]]:
    kept: list[dict[str, object]] = []
    counts: dict[tuple[str, str], int] = {}
    hashes: set[str] = set()
    for hit in hits:
        key = (str(hit["project_id"]), str(hit["relative_path"]))
        # identical content in another file (copies) adds nothing
        digest = str(hit.get("chunk_hash") or "")
        if digest and digest in hashes and not hit.get("merged_chunks"):
            continue
        if counts.get(key, 0) >= max_per_file:
            continue
        counts[key] = counts.get(key, 0) + 1
        if digest:
            hashes.add(digest)
        kept.append(hit)
    return kept


_CLAIM_WORD = re.compile(r"[A-Za-z]{2,}")


def _claim_is_noise(text: str) -> bool:
    words = _CLAIM_WORD.findall(text)
    if len(words) < CLAIM_MIN_WORDS:
        return True
    letters = sum(len(word) for word in words)
    # mostly code, markup or punctuation
    return letters < 0.55 * len(text.replace(" ", ""))


def _select_claims(
    claims: list[dict[str, object]], tokens: list[str], cap: int
) -> list[dict[str, object]]:
    """Verified first, then claims sharing the most query terms; capped, noise dropped."""
    order = {"verified": 0, "disputed": 1, "observed": 2, "proposed": 3}
    scored = []
    for claim in claims:
        text = str(claim.get("display_text") or "")
        if claim.get("trust_state") != "verified" and _claim_is_noise(text):
            continue
        folded = text.casefold()
        overlap = sum(1 for token in tokens if token in folded)
        scored.append((order.get(str(claim.get("trust_state")), 4), -overlap, len(text), text, claim))
    scored.sort(key=lambda item: item[:4])
    selected = []
    for _state, _overlap, _length, text, claim in scored[: max(0, cap)]:
        item = dict(claim)
        if len(text) > CLAIM_MAX_CHARS:
            item["display_text"] = text[:CLAIM_MAX_CHARS].rstrip() + "..."
        selected.append(item)
    return selected


def _matching_ledger_claims(connection, project_ids, tokens, already, limit=4):
    """Verified or proposed claims that mention at least two query terms.

    Replaces the old unconditional boosts, which added the newest proposed
    (board) and verified claims to every packet whatever the query.
    """
    if not tokens or limit <= 0:
        return already
    need = 2 if len(tokens) >= 2 else 1
    have = {str(item["id"]) for item in already}
    placeholders = ",".join("?" for _ in project_ids)
    like = " OR ".join("c.normalized_text LIKE ?" for _ in tokens[:6])
    rows = connection.execute(
        f"SELECT c.id,c.project_id,c.display_text,c.trust_state,c.created_by FROM claims c "
        f"WHERE c.project_id IN ({placeholders}) AND c.trust_state IN ('verified','proposed') "
        f"AND ({like}) ORDER BY c.updated_at DESC LIMIT 200",
        (*project_ids, *[f"%{token}%" for token in tokens[:6]]),
    ).fetchall()
    extra = []
    for row in rows:
        if str(row["id"]) in have:
            continue
        folded = str(row["display_text"]).casefold()
        if sum(1 for token in tokens if token in folded) >= need:
            extra.append(dict(row))
        if len(extra) >= limit:
            break
    return already + extra


def query_context(
    database: Database,
    query: str,
    *,
    path: str | Path | None = None,
    project_id: str | None = None,
    agent: str = "unknown",
    limit: int = Limits.query_limit,
    token_budget: int = Limits.query_token_budget,
    use_cache: bool = True,
    run_budget: int | None = None,
    replay_run_info: dict[str, int | None] | None = None,
    include_graph: bool = False,
    refresh_stale: bool = True,
    max_items_per_file: int = Limits.max_items_per_file,
) -> dict[str, object]:
    """Return a deterministic context packet for an already-registered project.

    Query does not auto-register a workspace. If ``path`` / cwd has no project,
    this raises ValueError (same as resume). Run ingest first.

    Reads happen without a write lock. All ledger writes of one query (budget,
    cache, run record) share a single short transaction at the end. When a
    matched file was edited after ingest, that file alone is re-ingested and
    the query runs once more, so edited files return current evidence.

    ``replay_run_info`` re-executes a recorded run without touching the ledger:
    the budget is neither read nor decremented, the cache is bypassed, and no
    ``runs`` row is written.
    """
    database.initialize()
    started = time.perf_counter()
    project = _resolve_project(database, path=path, project_id=project_id, agent=agent)
    project_id_value = str(project["id"])
    requirements = split_requirements(query, limit=MAX_LEAVES)
    config = {
        "algorithm": "fts5-bounded-requirements-v6-lean",
        "limit_per_requirement": limit,
        "max_requirements": MAX_LEAVES,
        "max_leaves": MAX_LEAVES,
        "token_budget": token_budget,
        "graph": bool(include_graph),
        "max_items_per_file": int(max_items_per_file),
    }
    if replay_run_info is not None:
        run_info_fixed: dict[str, int | None] | None = {
            "remaining": replay_run_info.get("remaining"),
            "initial": replay_run_info.get("initial"),
        }
    else:
        run_info_fixed = None

    search_order: list[dict[str, object]] = []
    with database.connect() as connection:
        scoped = search_scope(connection, project_id_value, path=path)
        search_order = [
            {"id": item["id"], "scope": item["scope"], "name": item["name"]}
            for item in scoped["order"]
        ]
        shared_ids = [str(item["id"]) for item in search_order[1:]]
        version_vector = dict(scoped["corpus_versions"])
        cache_key = cache_key_for(
            query=query,
            project_id=project_id_value,
            path=path,
            versions=version_vector,
            config=config,
        )
        if use_cache and replay_run_info is None:
            cached = connection.execute(
                "SELECT packet_json FROM cache WHERE cache_key=?", (cache_key,)
            ).fetchone()
            if cached is not None:
                cached_body = json.loads(cached["packet_json"])
                packet = cached_body.get("packet", {})
                dependencies_list = cached_body.get("source_checks")
                if dependencies_list is None or not all(
                    bool(item.get("matched")) ==
                    file_matches_hash(Path(item["resolved_path"]), item["source_hash"])
                    for item in dependencies_list
                ):
                    cached = None
            if cached is not None:
                return _finish_query(
                    database, packet, project_id_value=project_id_value, project=project,
                    query=query, config=config, started=started, agent=agent,
                    run_budget=run_budget, cache_key=cache_key, cache_hit=True,
                )

        scoped_ids = [project_id_value, *shared_ids]
        plate_cache = PlateCache()
        dependencies: dict[tuple[str, str], bool] = {}

        def retrieve(text: str) -> list[dict[str, object]]:
            found = [
                hydrate_chunk_row(row, plate_cache)
                for row in _retrieve_requirement(
                    connection,
                    text,
                    project_id=project_id_value,
                    shared_ids=shared_ids,
                    limit=limit,
                    path=path,
                    scoped_ids=scoped_ids,
                    strict_or=True,
                )
            ]
            return found

        leaves: list[dict[str, object]] = [
            {"text": requirement, "rows": retrieve(requirement), "extra_split": False, "blocked_by_max": False}
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
            replacements = [
                {"text": child, "rows": retrieve(child), "extra_split": True, "blocked_by_max": False}
                for child in taken
            ]
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

        # Edited-file freshness: re-ingest just the changed files, then retry once.
        stale_paths = sorted(
            {
                str(row["resolved_path"])
                for leaf in leaves
                for row in leaf["rows"]
                if row.get("stale") and Path(str(row["resolved_path"])).is_file()
                and str(row["project_id"]) == project_id_value
            }
        )
    if stale_paths and refresh_stale and replay_run_info is None and len(stale_paths) <= 25:
        root = None
        try:
            from .ingest import ingest_project

            alias_root = _project_root_for(database, project_id_value, stale_paths[0])
            if alias_root is not None:
                root = alias_root
                ingest_project(database, root, agent=agent, only_paths=stale_paths)
        except (sqlite3.OperationalError, OSError, ValueError, RuntimeError):
            root = None
        if root is not None:
            return query_context(
                database, query, path=path, project_id=project_id, agent=agent, limit=limit,
                token_budget=token_budget, use_cache=use_cache, run_budget=run_budget,
                replay_run_info=replay_run_info, include_graph=include_graph,
                refresh_stale=False, max_items_per_file=max_items_per_file,
            )

    with database.connect() as connection:
        selected: dict[tuple[str, str], dict[str, object]] = {}
        coverage: list[dict[str, object]] = []
        disagreement_keys: set[tuple[str, str]] = set()
        for requirement_index, leaf in enumerate(leaves):
            rows = list(leaf["rows"])
            _fill_stale_text(connection, rows)
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
            row_paths = [str(row["relative_path"]) for row in live_rows]
            if live_rows and texts_disagree(row_texts, row_paths):
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

        ordered_hits = sorted(
            selected.values(),
            key=lambda item: (
                bool(item["stale"]),
                0 if (item["relative_path"], item["chunk_hash"]) in disagreement_keys else 1,
                min(item["requirements"]),
                int(item["scope_priority"]),
                int(item.get("match_pass", 0)),
                1 if _is_board_path(str(item["relative_path"])) else 0,
                float(item["text_rank"]),
                str(item["relative_path"]),
                int(item["line_start"]),
            ),
        )
        disagreeing = [item for item in ordered_hits if (item["relative_path"], item["chunk_hash"]) in disagreement_keys]
        others = [item for item in ordered_hits if (item["relative_path"], item["chunk_hash"]) not in disagreement_keys]
        # Conflicting spans are never dropped by per-file dedup.
        ordered_hits = disagreeing + _limit_per_file(_merge_adjacent(others), max(1, int(max_items_per_file)))
        query_tokens = _tokens(query)
        clip = Limits.long_chunk_characters
        for item in ordered_hits:
            text, clipped = _clip_long_text(str(item["text"]), query_tokens, clip)
            if clipped:
                item["text"] = text
                item["clipped"] = True
        chunk_ids = [int(row["chunk_id"]) for row in ordered_hits if not row["stale"]]
        claims = _matching_ledger_claims(
            connection, scoped_ids, query_tokens,
            _claims_for_chunks(connection, scoped_ids, chunk_ids),
        )
        claims = _live_backed_items(
            connection, claims, kind="claim", cache=plate_cache, dependencies=dependencies
        )
        claims = _select_claims(claims, query_tokens, Limits.max_claims)
        graph_nodes: list[dict[str, object]] = []
        if include_graph:
            graph_nodes = _graph_matches(connection, scoped_ids, query_tokens)
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
        "run": {"remaining": None, "initial": None},
    }
    # Budget counters ("run") are excluded from packet_hash.
    packet["packet_hash"] = hashlib.sha256(json_text(packet_body_for_hash(packet)).encode("utf-8")).hexdigest()
    if run_info_fixed is not None:
        packet["run"] = run_info_fixed
        apply_solvers(packet)
        attach_empty_result(packet)
        return packet
    source_checks = [
        {"resolved_path": source_path, "source_hash": source_hash,
         "matched": dependencies[(source_path, source_hash)]}
        for source_path, source_hash in sorted(dependencies)
    ]
    return _finish_query(
        database, packet, project_id_value=project_id_value, project=project,
        query=query, config=config, started=started, agent=agent,
        run_budget=run_budget, cache_key=cache_key, cache_hit=False,
        source_checks=source_checks,
    )


def _project_root_for(database: Database, project_id: str, file_path: str) -> str | None:
    """The registered alias folder of this project that contains file_path."""
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT resolved_path FROM project_aliases WHERE project_id=? AND resolved_path IS NOT NULL",
            (project_id,),
        ).fetchall()
    target = Path(file_path)
    best: str | None = None
    for row in rows:
        candidate = Path(str(row["resolved_path"]))
        try:
            target.relative_to(candidate)
        except ValueError:
            continue
        if best is None or len(str(candidate)) > len(best):
            best = str(candidate)
    return best


def _finish_query(
    database: Database,
    packet: dict[str, object],
    *,
    project_id_value: str,
    project: dict[str, object],
    query: str,
    config: dict[str, object],
    started: float,
    agent: str,
    run_budget: int | None,
    cache_key: str,
    cache_hit: bool,
    source_checks: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """The one write transaction of a query: budget, cache and run record."""
    now = utc_now()
    with database.transaction() as connection:
        if run_budget is not None:
            set_budget(connection, project_id_value, agent, int(run_budget))
        budget = consume(connection, project_id_value, agent, n=1)
        packet["run"] = {"remaining": budget["remaining"], "initial": budget["initial"]}
        packet.pop("cache", None)
        packet.pop("checks", None)
        packet.pop("empty_result", None)
        if cache_hit:
            connection.execute(
                "UPDATE cache SET last_used_at=?,hit_count=hit_count+1 WHERE cache_key=?",
                (now, cache_key),
            )
        else:
            connection.execute(
                "INSERT INTO cache(cache_key,project_id,corpus_version,packet_json,packet_hash,"
                "created_at,last_used_at,hit_count) VALUES(?,?,?,?,?,?,?,0) "
                "ON CONFLICT(cache_key) DO UPDATE SET packet_json=excluded.packet_json,"
                "packet_hash=excluded.packet_hash,last_used_at=excluded.last_used_at",
                (
                    cache_key,
                    project_id_value,
                    int(project["corpus_version"]),
                    json_text({"packet": packet, "source_checks": source_checks or []}),
                    packet["packet_hash"],
                    now,
                    now,
                ),
            )
        apply_solvers(packet)
        attach_empty_result(packet)
        packet["cache"] = {"hit": cache_hit, "key": cache_key[:16]}
        _insert_run(
            connection,
            project_id_value,
            query,
            project["corpus_version"],
            config,
            packet,
            started,
            remaining_budget=budget["remaining"],
            agent=agent,
        )
    return packet


def _insert_run(
    connection,
    project_id: str,
    query: str,
    corpus_version: int,
    config: dict[str, object],
    packet: dict[str, object],
    started: float,
    remaining_budget: int | None = None,
    agent: str | None = None,
) -> None:
    packet_json = json_text(packet)
    packet_hash = str(packet.get("packet_hash") or hashlib.sha256(packet_json.encode()).hexdigest())
    connection.execute(
        "INSERT INTO runs(id,project_id,query_text,corpus_version,config_json,packet_json,"
        "packet_hash,elapsed_ms,created_at,remaining_budget,agent) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
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
            normalize_agent(agent),
        ),
    )


def _record_run(
    database: Database,
    project_id: str,
    query: str,
    corpus_version: int,
    config: dict[str, object],
    packet: dict[str, object],
    started: float,
    remaining_budget: int | None = None,
    agent: str | None = None,
) -> None:
    with database.transaction() as connection:
        _insert_run(connection, project_id, query, corpus_version, config, packet, started,
                    remaining_budget=remaining_budget, agent=agent)
