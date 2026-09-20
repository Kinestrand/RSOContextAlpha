"""Offline sweep of retrieval limits over recorded runs. Advisory only.

Dream-RSI evaluates candidate policies against recorded history instead of
paying for fresh online rollouts. The same trick applies to RSO's deterministic
knobs: ``limit_per_requirement`` and ``token_budget`` can be swept over the runs
already in the ledger, with no model in the loop and no new evaluation cost.

This reports a quality/cost table. It does not change defaults, and it never
should on its own: a sweep over a small corpus will happily recommend whatever
overfits that corpus. Read it as a curve to argue about, not an answer. The run
is read-only, so it consumes no run budget and writes no ``runs`` row.

    python benchmarks/budget_sweep.py --path <project> [--limits 4,8,12]

Dev tool: not part of the release allowlist.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rso_context.check import CHECK_SCHEMA  # noqa: E402
from rso_context.config import default_db_path  # noqa: E402
from rso_context.db import Database  # noqa: E402
from rso_context.identity import resolve_existing_project  # noqa: E402
from rso_context.query import query_context  # noqa: E402


def _int_list(raw: str) -> list[int]:
    return [int(piece) for piece in raw.split(",") if piece.strip()]


def _packet_bytes(packet: dict) -> int:
    return len(json.dumps(packet, sort_keys=True, ensure_ascii=False).encode("utf-8"))


def _covered(packet: dict) -> int:
    return sum(
        1
        for item in packet.get("requirements") or []
        if item.get("status") == "evidence_found"
    )


def _checks_passed(packet: dict) -> int:
    return sum(
        1 for item in packet.get("checks") or [] if item.get("result") == "pass"
    )


def sweep(
    database: Database,
    *,
    path: Path,
    limits: list[int],
    token_budgets: list[int],
) -> dict:
    database.initialize()
    project = resolve_existing_project(database, path=path)
    project_id = str(project["id"])
    current_version = int(project["corpus_version"])

    with database.connect() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT query_text,packet_json,corpus_version,remaining_budget FROM runs "
                "WHERE project_id=? AND corpus_version=? ORDER BY created_at DESC",
                (project_id, current_version),
            ).fetchall()
        ]
    queries = [
        row
        for row in rows
        if json.loads(row["packet_json"]).get("schema") != CHECK_SCHEMA
    ]
    if not queries:
        return {
            "schema": "rso-budget-sweep/v1",
            "project": {"id": project_id, "corpus_version": current_version},
            "note": "No query runs recorded at the current corpus version.",
            "grid": [],
        }

    grid = []
    for limit in limits:
        for token_budget in token_budgets:
            covered = passed = size = 0
            started = time.perf_counter()
            for row in queries:
                recorded_run = json.loads(row["packet_json"]).get("run") or {}
                packet = query_context(
                    database,
                    row["query_text"],
                    project_id=project_id,
                    agent="sweep",
                    limit=limit,
                    token_budget=token_budget,
                    use_cache=False,
                    replay_run_info={
                        "remaining": int(recorded_run.get("remaining", 0)),
                        "initial": int(recorded_run.get("initial", 0)),
                    },
                )
                covered += _covered(packet)
                passed += _checks_passed(packet)
                size += _packet_bytes(packet)
            elapsed = (time.perf_counter() - started) * 1000
            count = len(queries)
            grid.append(
                {
                    "limit_per_requirement": limit,
                    "token_budget": token_budget,
                    "requirements_covered": round(covered / count, 3),
                    "checks_passed": round(passed / count, 3),
                    "packet_bytes": round(size / count, 1),
                    "ms_per_query": round(elapsed / count, 2),
                }
            )
    return {
        "schema": "rso-budget-sweep/v1",
        "project": {
            "id": project_id,
            "name": project["display_name"],
            "corpus_version": current_version,
        },
        "queries_replayed": len(queries),
        "grid": grid,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--db", default=None)
    parser.add_argument("--limits", default="4,8,12", type=_int_list)
    parser.add_argument("--token-budgets", default="2000,4000,8000", type=_int_list)
    args = parser.parse_args()

    database = Database(Path(args.db) if args.db else default_db_path())
    report = sweep(
        database,
        path=args.path,
        limits=args.limits,
        token_budgets=args.token_budgets,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
