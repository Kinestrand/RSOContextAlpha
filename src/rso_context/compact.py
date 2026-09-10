"""Compact evidence packets for MCP. Does not alter CLI rso-context-packet/v2 hashes."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Limits
from .db import Database
from .mcp_contract import alias_is_visible
from .pointers import PlateCache, file_matches_hash
from .query import _text_has_affirmative, _text_has_negation


COMPACT_SCHEMA = "rso-mcp-packet/v1"
EXPAND_REF_SCHEMA = "rso-expand-ref/v1"
EXPAND_RESULT_SCHEMA = "rso-expand-result/v1"
SOURCE_SCHEMA = "rso-context-packet/v2"

_TOKEN = re.compile(r"[A-Za-z0-9_]{3,}")
_POLICY = re.compile(
    r"\b(must|shall|required|do not|don't|never|except|unless|decision|authoritative)\b",
    re.IGNORECASE,
)


def serialized_bytes(value: object) -> int:
    """Canonical size of a compact result: ASCII JSON, no indent."""
    return len(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def token_estimate(byte_count: int) -> dict[str, object]:
    return {
        "value": (max(0, int(byte_count)) + 3) // 4,
        "labeled": "estimate",
        "method": "utf8-bytes/4",
    }


def _query_tokens(query: str) -> list[str]:
    return [token.casefold() for token in _TOKEN.findall(query or "")]


def is_protected_policy(text: str) -> bool:
    """True when shrinking could drop a condition or exception."""
    if not text:
        return False
    if _text_has_negation(text) and _text_has_affirmative(text):
        return True
    return _POLICY.search(text) is not None


def _make_ref(hit: dict[str, object], line_start: int, line_end: int) -> dict[str, object]:
    return {
        "schema": EXPAND_REF_SCHEMA,
        "project_id": hit.get("project_id"),
        "relative_path": hit.get("relative_path"),
        "expected_hash": hit.get("source_hash"),
        "line_start": int(line_start),
        "line_end": int(line_end),
        "chunk_hash": hit.get("chunk_hash"),
    }


def _excerpt(hit: dict[str, object], text: str, line_start: int, line_end: int) -> dict[str, object]:
    return {
        "project_id": hit.get("project_id"),
        "relative_path": hit.get("relative_path"),
        "line_start": int(line_start),
        "line_end": int(line_end),
        "text": text,
        "chunk_hash": hit.get("chunk_hash"),
        "source_hash": hit.get("source_hash"),
        "stale": bool(hit.get("stale")),
        "span_kind": hit.get("span_kind"),
        "media_type": hit.get("media_type"),
        "requirements": list(hit.get("requirements") or []),
        "expand": _make_ref(hit, line_start, line_end),
    }


def matching_line_groups(text: str, line_start: int, query: str) -> list[tuple[int, int, str]]:
    """Consecutive matching lines as separate ranges. Never splice gaps."""
    tokens = _query_tokens(query)
    lines = str(text).split("\n")
    start = int(line_start)
    matched: list[int] = []
    for offset, line in enumerate(lines):
        folded = line.casefold()
        if tokens and any(token in folded for token in tokens):
            matched.append(offset)
    if not matched:
        return []
    groups: list[tuple[int, int, str]] = []
    run_start = matched[0]
    prev = matched[0]
    for index in matched[1:]:
        if index == prev + 1:
            prev = index
            continue
        chunk = "\n".join(lines[run_start : prev + 1])
        groups.append((start + run_start, start + prev, chunk))
        run_start = index
        prev = index
    chunk = "\n".join(lines[run_start : prev + 1])
    groups.append((start + run_start, start + prev, chunk))
    return groups


def excerpts_for_hit(hit: dict[str, object], query: str) -> list[dict[str, object]]:
    line_start = int(hit.get("line_start") or 1)
    line_end = int(hit.get("line_end") or line_start)
    if hit.get("stale"):
        stub = _excerpt(hit, "", line_start, line_end)
        stub["text"] = ""
        stub["stale"] = True
        return [stub]
    text = str(hit.get("text") or "")
    if is_protected_policy(text):
        return [_excerpt(hit, text, line_start, line_end)]
    groups = matching_line_groups(text, line_start, query)
    if not groups:
        return [_excerpt(hit, text, line_start, line_end)]
    return [_excerpt(hit, chunk, start, end) for start, end, chunk in groups]


def _skeleton(packet: dict[str, object], byte_budget: int) -> dict[str, object]:
    requirements = []
    for item in packet.get("requirements") or []:
        requirements.append(
            {
                "index": item.get("index"),
                "status": item.get("status"),
                "stop_reason": item.get("stop_reason"),
                "text": item.get("text"),
            }
        )
    claims = []
    for claim in packet.get("claims") or []:
        claims.append(
            {
                "id": claim.get("id"),
                "trust_state": claim.get("trust_state"),
                "display_text": claim.get("display_text"),
            }
        )
    return {
        "schema": COMPACT_SCHEMA,
        "status": "ok",
        "source_schema": SOURCE_SCHEMA,
        "source_packet_hash": packet.get("packet_hash"),
        "project": packet.get("project"),
        "search_order": packet.get("search_order"),
        "corpus_versions": packet.get("corpus_versions"),
        "query": packet.get("query"),
        "clarification_needed": bool(packet.get("clarification_needed")),
        "requirements": requirements,
        "evidence": [],
        "omitted": [],
        "omitted_count": 0,
        "claims": claims,
        "validations": packet.get("validations") or [],
        "checks": packet.get("checks") or [],
        "run": packet.get("run"),
        "empty_result": packet.get("empty_result"),
        "byte_budget": int(byte_budget),
        "byte_count": 0,
        "token_estimate": token_estimate(0),
    }


def _stamp_size(compact: dict[str, object]) -> None:
    for _ in range(6):
        size = serialized_bytes(compact)
        compact["byte_count"] = size
        compact["token_estimate"] = token_estimate(size)
        if serialized_bytes(compact) == size:
            return


def _fits(compact: dict[str, object], budget: int) -> bool:
    return serialized_bytes(compact) <= budget


def _drop_evidence(compact: dict[str, object], reason: str) -> None:
    if not compact["evidence"]:
        return
    item = compact["evidence"].pop()
    compact["omitted"].append({"reason": reason, "expand": item.get("expand")})
    compact["omitted_count"] = len(compact["omitted"])


def _minimal_provenance(compact: dict[str, object]) -> None:
    compact["evidence"] = []
    compact["claims"] = [
        {"id": claim.get("id"), "trust_state": claim.get("trust_state")}
        for claim in compact.get("claims") or []
    ]
    compact["requirements"] = [
        {
            "index": item.get("index"),
            "status": item.get("status"),
            "stop_reason": item.get("stop_reason"),
        }
        for item in compact.get("requirements") or []
    ]
    compact["validations"] = []
    compact["checks"] = []
    compact["omitted"] = []
    compact["status"] = "insufficient_budget"
    compact["message"] = "Required conflict/provenance information cannot fit."


def _force_under_budget(compact: dict[str, object], budget: int) -> None:
    """Last resort: drop metadata until the packet itself fits."""
    compact["status"] = "insufficient_budget"
    drop_keys = (
        "empty_result",
        "corpus_versions",
        "search_order",
        "validations",
        "checks",
        "run",
        "query",
        "requirements",
        "claims",
        "omitted",
        "evidence",
        "source_packet_hash",
        "source_schema",
        "clarification_needed",
        "token_estimate",
        "message",
        "project",
        "omitted_count",
    )
    for key in drop_keys:
        if serialized_bytes(compact) <= budget:
            return
        if key == "project" and isinstance(compact.get("project"), dict):
            compact["project"] = {"id": compact["project"].get("id")}
            _stamp_size(compact)
            if serialized_bytes(compact) <= budget:
                return
        compact.pop(key, None)
        _stamp_size(compact)
    if serialized_bytes(compact) > budget:
        compact.clear()
        compact.update(
            {
                "schema": COMPACT_SCHEMA,
                "status": "insufficient_budget",
                "byte_budget": budget,
                "byte_count": 0,
            }
        )
        _stamp_size(compact)


def _finalize(compact: dict[str, object], budget: int, *, protect_conflict: bool) -> dict[str, object]:
    _stamp_size(compact)
    while serialized_bytes(compact) > budget:
        if compact["evidence"] and not protect_conflict:
            _drop_evidence(compact, "byte_budget")
            _stamp_size(compact)
            continue
        _minimal_provenance(compact)
        _stamp_size(compact)
        break
    if serialized_bytes(compact) > budget:
        compact["claims"] = []
        compact["search_order"] = []
        compact["omitted"] = compact.get("omitted") or []
        _stamp_size(compact)
    if serialized_bytes(compact) > budget:
        _force_under_budget(compact, budget)
    return compact


def compact_packet(
    packet: dict[str, object],
    *,
    byte_budget: int | None = None,
    query: str | None = None,
) -> dict[str, object]:
    """Build rso-mcp-packet/v1 from a full v2 packet. The input packet is not modified."""
    budget = max(256, int(byte_budget if byte_budget is not None else Limits.compact_byte_budget))
    query_text = query if query is not None else str(packet.get("query") or "")
    compact = _skeleton(packet, budget)
    disagreement = {
        int(item["index"])
        for item in packet.get("requirements") or []
        if item.get("status") == "disagreement" and item.get("index") is not None
    }
    candidates: list[dict[str, object]] = []
    for hit in packet.get("evidence") or []:
        candidates.extend(excerpts_for_hit(hit, query_text))

    conflict = [item for item in candidates if disagreement.intersection(item.get("requirements") or [])]
    conflict_ids = {id(item) for item in conflict}
    ordinary = [item for item in candidates if id(item) not in conflict_ids]

    if conflict:
        compact["evidence"] = list(conflict)
        if not _fits(compact, budget):
            compact["omitted"] = [{"reason": "conflict_set", "expand": item.get("expand")} for item in conflict]
            compact["omitted_count"] = len(conflict)
            return _finalize(compact, budget, protect_conflict=True)

    kept = list(compact["evidence"])
    omitted: list[dict[str, object]] = list(compact.get("omitted") or [])
    for item in ordinary:
        compact["evidence"] = kept + [item]
        if _fits(compact, budget):
            kept.append(item)
        else:
            omitted.append({"reason": "byte_budget", "expand": item.get("expand")})
    compact["evidence"] = kept
    compact["omitted"] = omitted
    compact["omitted_count"] = len(omitted)
    return _finalize(compact, budget, protect_conflict=bool(conflict and kept[: len(conflict)] == conflict))


def parse_expand_ref(ref: str | dict) -> dict[str, object]:
    if isinstance(ref, dict):
        data = dict(ref)
    else:
        text = str(ref).strip()
        if not text:
            raise ValueError("ref is required")
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("expansion ref must be a JSON object")
    required = ("project_id", "relative_path", "expected_hash", "line_start", "line_end")
    missing = [key for key in required if not str(data.get(key) or "").strip() and data.get(key) != 0]
    if missing:
        raise ValueError("expansion ref missing " + ", ".join(missing))
    return data


def expand_reference(
    database: Database,
    ref: str | dict,
    *,
    roots: list[Path],
    max_bytes: int | None = None,
) -> dict[str, object]:
    """Resolve an expansion ref against the ledger and launch roots. Never uses client paths."""
    data = parse_expand_ref(ref)
    budget = max(256, int(max_bytes if max_bytes is not None else Limits.compact_byte_budget))
    project_id = str(data["project_id"])
    relative_path = str(data["relative_path"]).replace("\\", "/")
    expected_hash = str(data["expected_hash"])
    line_start = int(data["line_start"])
    line_end = int(data["line_end"])
    if line_end < line_start:
        raise ValueError("line_end must be >= line_start")

    database.initialize()
    with database.connect() as connection:
        row = connection.execute(
            "SELECT resolved_path, relative_path, current_hash, active FROM sources "
            "WHERE project_id=? AND relative_path=? AND active=1 "
            "ORDER BY last_seen DESC LIMIT 1",
            (project_id, relative_path),
        ).fetchone()
    if row is None:
        return {
            "schema": EXPAND_RESULT_SCHEMA,
            "status": "unavailable",
            "message": "Source is not an active plate in the launch-bound ledger.",
            "ref": {
                "schema": EXPAND_REF_SCHEMA,
                "project_id": project_id,
                "relative_path": relative_path,
                "expected_hash": expected_hash,
                "line_start": line_start,
                "line_end": line_end,
            },
        }
    resolved = Path(str(row["resolved_path"]))
    if not alias_is_visible(resolved, roots):
        raise ValueError("expansion ref is outside the server's allowed roots")
    if not file_matches_hash(resolved, expected_hash):
        return {
            "schema": EXPAND_RESULT_SCHEMA,
            "status": "stale",
            "message": "Live file hash does not match the expansion reference.",
            "relative_path": relative_path,
            "expected_hash": expected_hash,
            "current_hash": row["current_hash"],
        }
    reconstructed, ok = PlateCache().reconstruct(str(resolved), expected_hash, line_start, line_end)
    if not ok:
        return {
            "schema": EXPAND_RESULT_SCHEMA,
            "status": "unavailable",
            "message": "Pointers do not guarantee recovery of a deleted historical file.",
            "relative_path": relative_path,
        }
    text = reconstructed or ""
    result = {
        "schema": EXPAND_RESULT_SCHEMA,
        "status": "ok",
        "relative_path": relative_path,
        "line_start": line_start,
        "line_end": line_end,
        "text": text,
        "source_hash": expected_hash,
        "stale": False,
    }
    if is_protected_policy(text) and serialized_bytes(result) > budget:
        result["status"] = "insufficient_budget"
        result["text"] = ""
        result["message"] = "Required condition and exception cannot fit this expansion budget."
        result["expand"] = {
            "schema": EXPAND_REF_SCHEMA,
            "project_id": project_id,
            "relative_path": relative_path,
            "expected_hash": expected_hash,
            "line_start": line_start,
            "line_end": line_end,
        }
        return result
    if serialized_bytes(result) <= budget:
        return result
    lines = text.split("\n")
    kept: list[str] = []
    last_line = line_start - 1
    for offset, line in enumerate(lines):
        candidate = dict(result)
        candidate["text"] = "\n".join(kept + [line])
        candidate["line_end"] = line_start + offset
        if serialized_bytes(candidate) > budget:
            break
        kept.append(line)
        last_line = line_start + offset
    if not kept:
        result["status"] = "insufficient_budget"
        result["text"] = ""
        result["message"] = "A single line exceeds the expansion budget."
        return result
    result["text"] = "\n".join(kept)
    result["line_end"] = last_line
    if last_line < line_end:
        result["expand"] = {
            "schema": EXPAND_REF_SCHEMA,
            "project_id": project_id,
            "relative_path": relative_path,
            "expected_hash": expected_hash,
            "line_start": last_line + 1,
            "line_end": line_end,
        }
    return result
