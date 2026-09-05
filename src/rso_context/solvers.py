from __future__ import annotations

import hashlib
import re

from .db import json_text

SOLVER_NAMES = ("schema_holds", "span_exists", "hash_matches")

PACKET_SCHEMA = "rso-context-packet/v2"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def packet_body_for_hash(packet: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in packet.items() if key not in ("packet_hash", "checks")}


def _check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"name": name, "result": "pass" if passed else "fail", "detail": detail}


def _schema_holds(packet: dict[str, object]) -> dict[str, str]:
    schema = packet.get("schema")
    passed = schema == PACKET_SCHEMA
    detail = f"schema is {schema!r}" if not passed else PACKET_SCHEMA
    return _check("schema_holds", passed, detail)


def _span_exists(packet: dict[str, object]) -> dict[str, str]:
    evidence = packet.get("evidence") or []
    passed = any(
        isinstance(item, dict) and not item.get("stale", False)
        and bool(item.get("text"))
        for item in evidence
    )
    detail = "current evidence present" if passed else "no current evidence"
    return _check("span_exists", passed, detail)


def _hash_matches(packet: dict[str, object]) -> dict[str, str]:
    packet_hash = str(packet.get("packet_hash") or "")
    expected = hashlib.sha256(json_text(packet_body_for_hash(packet)).encode("utf-8")).hexdigest()
    if not _HASH_RE.fullmatch(packet_hash):
        return _check("hash_matches", False, "packet_hash is not 64 lowercase hex")
    passed = packet_hash == expected
    detail = "packet_hash matches" if passed else "packet_hash mismatch"
    return _check("hash_matches", passed, detail)


def apply_solvers(packet: dict[str, object]) -> list[dict[str, str]]:
    """Run symbolic solvers. A pass is not verification and does not change trust_state."""
    schema = _schema_holds(packet)
    checks = [schema]
    requirements = packet.get("requirements") or []
    disagreement = bool(packet.get("clarification_needed")) or any(
        isinstance(item, dict) and str(item.get("status") or "") == "disagreement"
        for item in requirements
    )
    if schema["result"] == "fail" or disagreement:
        checks.append(_span_exists(packet))
        checks.append(_hash_matches(packet))
    else:
        checks.append(_span_exists(packet))
    packet["checks"] = checks
    return checks
