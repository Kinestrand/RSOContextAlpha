"""Replay recorded runs against the current code and report packet drift.

The ``runs`` table already stores every query and check packet with the corpus
version it was produced from. Re-executing those runs against the current build
turns "did this parsing change move anything else?" into a diff. Replay is
read-only: it consumes no run budget, writes no ``runs`` row, and never touches
the cache.

A run is only reproducible while the sources behind it are unchanged. Two things
can move underneath it. ``corpus_version`` advances on ingest, so runs recorded
at an older version are skipped and counted as ``stale_corpus_skipped``;
``--include-historical`` compares them anyway. Separately, retrieval reads live
files, so a file edited since the run was recorded changes the packet even
though ``corpus_version`` has not moved. Each recorded evidence span carries the
hash of its source file, so drift is only attributed to the code once those
hashes still match on disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .check import CHECK_SCHEMA, check_questions
from .db import Database
from .pointers import file_matches_hash
from .query import query_context
from .identity import resolve_existing_project

REPLAY_SCHEMA = "rso-replay-report/v1"

# Present in a recorded packet but not reproducible from the code under test.
# ``run`` is budget bookkeeping rather than retrieval behaviour: a check reads the
# live budget, and a query replay restores the recorded one so its packet_hash
# stays comparable. Neither says anything about whether retrieval changed.
_VOLATILE_FIELDS = ("packet_hash", "check_hash", "status", "run")


def _stored_questions(query_text: str) -> object | None:
    try:
        payload = json.loads(query_text)
    except (TypeError, ValueError):
        return None
    if isinstance(payload, dict) and "rso_check" in payload:
        return payload["rso_check"]
    return None


def _changed_fields(recorded: dict[str, Any], current: dict[str, Any]) -> list[str]:
    keys = set(recorded) | set(current)
    changed = [
        key
        for key in sorted(keys - set(_VOLATILE_FIELDS))
        if recorded.get(key) != current.get(key)
    ]
    return changed


def _sources_changed(recorded: dict[str, Any]) -> list[str]:
    """Return the recorded evidence sources whose file no longer matches its hash."""
    changed: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in recorded.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        resolved = item.get("resolved_path")
        source_hash = item.get("source_hash")
        if not isinstance(resolved, str) or not isinstance(source_hash, str):
            continue
        key = (resolved, source_hash)
        if key in seen:
            continue
        seen.add(key)
        if not file_matches_hash(Path(resolved), source_hash):
            changed.append(item.get("relative_path") or resolved)
    return sorted(set(changed))


def _recorded_hash(packet: dict[str, Any], fallback: str) -> str:
    for key in ("packet_hash", "check_hash"):
        value = packet.get(key)
        if isinstance(value, str) and value:
            return value
    return fallback


def _replay_one(
    database: Database,
    row: dict[str, Any],
    *,
    agent: str,
    historical: bool = False,
) -> dict[str, Any]:
    recorded = json.loads(row["packet_json"])
    config = json.loads(row["config_json"])
    entry: dict[str, Any] = {
        "packet_hash": row["packet_hash"],
        "recorded_at": row["created_at"],
        "corpus_version": int(row["corpus_version"]),
        "kind": "check" if recorded.get("schema") == CHECK_SCHEMA else "query",
    }
    if historical:
        entry["corpus"] = "historical"

    if entry["kind"] == "check":
        questions = _stored_questions(row["query_text"])
        if questions is None:
            entry["result"] = "unreplayable"
            entry["detail"] = "recorded check run has no stored questions"
            return entry
        entry["questions"] = questions
        current = check_questions(
            database,
            questions,
            project_id=row["project_id"],
            agent=agent,
            limit=int(config.get("limit_per_question", 8)),
            replay=True,
        )
    else:
        entry["query"] = row["query_text"]
        run_info = recorded.get("run") or {}
        remaining = row["remaining_budget"]
        current = query_context(
            database,
            row["query_text"],
            project_id=row["project_id"],
            agent=agent,
            limit=int(config.get("limit_per_requirement", 8)),
            token_budget=int(config.get("token_budget", 4_000)),
            use_cache=False,
            replay_run_info={
                "remaining": int(remaining if remaining is not None else run_info.get("remaining", 0)),
                "initial": int(run_info.get("initial", 0)),
            },
        )

    recorded_hash = _recorded_hash(recorded, str(row["packet_hash"]))
    current_hash = _recorded_hash(current, "")
    changed = _changed_fields(recorded, current)
    entry["current_hash"] = current_hash
    if not changed and recorded_hash == current_hash:
        entry["result"] = "match"
    else:
        entry["result"] = "drift"
        entry["changed_fields"] = changed
        # Drift only isolates the code under test once the inputs are ruled out.
        # A run recorded at an older corpus version reads a different corpus, and
        # an edited file changes retrieval without any ingest, so neither is
        # evidence of an algorithm change.
        edited = _sources_changed(recorded)
        if entry.get("corpus") == "historical":
            entry["cause"] = "corpus_moved"
        elif edited:
            entry["cause"] = "sources_changed"
            entry["changed_sources"] = edited
        else:
            entry["cause"] = "algorithmic"
    return entry


def replay_runs(
    database: Database,
    *,
    path: str | Path | None = None,
    project_id: str | None = None,
    agent: str = "replay",
    limit: int | None = None,
    include_historical: bool = False,
) -> dict[str, Any]:
    """Re-execute recorded runs for one project and report which packets drifted."""
    database.initialize()
    project = resolve_existing_project(database, path=path, project_id=project_id)
    project_id_value = str(project["id"])
    current_version = int(project["corpus_version"])

    sql = (
        "SELECT id,project_id,query_text,corpus_version,config_json,packet_json,"
        "packet_hash,created_at,remaining_budget FROM runs WHERE project_id=? "
        "ORDER BY created_at DESC"
    )
    params: list[Any] = [project_id_value]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with database.connect() as connection:
        rows = [dict(row) for row in connection.execute(sql, params).fetchall()]

    entries: list[dict[str, Any]] = []
    stale = 0
    for row in rows:
        historical = int(row["corpus_version"]) != current_version
        if historical and not include_historical:
            stale += 1
            continue
        entry = _replay_one(database, row, agent=agent, historical=historical)
        entries.append(entry)

    drift = [entry for entry in entries if entry["result"] == "drift"]
    algorithmic = [entry for entry in drift if entry.get("cause") == "algorithmic"]
    unreplayable = [entry for entry in entries if entry["result"] == "unreplayable"]
    return {
        "schema": REPLAY_SCHEMA,
        "project": {
            "id": project_id_value,
            "name": project["display_name"],
            "corpus_version": current_version,
        },
        "counts": {
            "recorded": len(rows),
            "replayed": len(entries),
            "matched": len(entries) - len(drift) - len(unreplayable),
            "drifted": len(drift),
            "drifted_algorithmic": len(algorithmic),
            "unreplayable": len(unreplayable),
            "stale_corpus_skipped": stale,
        },
        "drift": drift,
        "unreplayable": unreplayable,
    }
