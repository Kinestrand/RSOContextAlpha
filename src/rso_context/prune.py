"""Prune chunks of superseded file versions (older takes) from the ledger.

When a file changes, ingest keeps the previous version's chunks, edges and
claim links as history, but they are never searched again (their FTS rows are
removed). On a long-lived ledger they become most of the chunk table.

Chunks that back a protected claim (verified, disputed, proposed, or any claim
with a named validation) are kept, so no human or validator decision loses
its evidence. Everything runs in short batched write transactions.
"""

from __future__ import annotations

import time
from typing import Callable

from .db import Database, batched, delete_fts_for_chunks, sql_batch_size

_CANDIDATES = """
SELECT c.id FROM chunks c
JOIN source_versions v ON v.id = c.source_version_id
JOIN sources s ON s.id = v.source_id
WHERE v.content_hash <> s.current_hash
  AND NOT EXISTS (
    SELECT 1 FROM claim_evidence ce JOIN claims cl ON cl.id = ce.claim_id
    WHERE ce.chunk_id = c.id
      AND (cl.trust_state <> 'observed'
           OR EXISTS (SELECT 1 FROM validations va WHERE va.claim_id = cl.id))
  )
"""


def plan_orphans(database: Database) -> dict[str, object]:
    database.initialize()
    with database.connect() as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM chunks c JOIN source_versions v ON v.id=c.source_version_id "
            "JOIN sources s ON s.id=v.source_id WHERE v.content_hash <> s.current_hash"
        ).fetchone()[0]
        prunable = connection.execute(f"SELECT COUNT(*) FROM ({_CANDIDATES})").fetchone()[0]
        versions = connection.execute(
            "SELECT COUNT(*) FROM source_versions v JOIN sources s ON s.id=v.source_id "
            "WHERE v.content_hash <> s.current_hash"
        ).fetchone()[0]
    return {
        "schema": "rso-prune-orphans/v1",
        "mode": "dry_run",
        "superseded_chunks": int(total),
        "prunable_chunks": int(prunable),
        "protected_chunks": int(total) - int(prunable),
        "superseded_versions": int(versions),
        "note": "Nothing changed. Back up the ledger, then re-run with --apply --yes.",
    }


def prune_orphans(
    database: Database,
    *,
    batch_rows: int = 2000,
    pause_seconds: float = 0.05,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    database.initialize()
    started = time.monotonic()
    counts = {"chunks": 0, "edges": 0, "claim_links": 0, "claims": 0, "versions": 0, "nodes": 0}
    size = max(1, int(batch_rows))
    while True:
        with database.transaction() as connection:
            ids = [int(row[0]) for row in connection.execute(_CANDIDATES + " LIMIT ?", (size,))]
            if not ids:
                break
            step = sql_batch_size(connection)
            delete_fts_for_chunks(connection, ids)
            for part in batched(ids, step):
                marks = ",".join("?" for _ in part)
                counts["edges"] += connection.execute(
                    f"DELETE FROM edges WHERE evidence_chunk_id IN ({marks})", part
                ).rowcount
                counts["claim_links"] += connection.execute(
                    f"DELETE FROM claim_evidence WHERE chunk_id IN ({marks})", part
                ).rowcount
                counts["chunks"] += connection.execute(
                    f"DELETE FROM chunks WHERE id IN ({marks})", part
                ).rowcount
        if progress:
            progress(f"pruned {counts['chunks']} chunks")
        if pause_seconds > 0:
            time.sleep(pause_seconds)
    with database.transaction() as connection:
        counts["claims"] = connection.execute(
            "DELETE FROM claims WHERE trust_state='observed' "
            "AND NOT EXISTS (SELECT 1 FROM claim_evidence ce WHERE ce.claim_id=claims.id) "
            "AND NOT EXISTS (SELECT 1 FROM validations va WHERE va.claim_id=claims.id)"
        ).rowcount
        counts["versions"] = connection.execute(
            "DELETE FROM source_versions WHERE NOT EXISTS "
            "(SELECT 1 FROM chunks c WHERE c.source_version_id=source_versions.id) "
            "AND content_hash <> (SELECT s.current_hash FROM sources s WHERE s.id=source_versions.source_id)"
        ).rowcount
        counts["nodes"] = connection.execute(
            "DELETE FROM nodes WHERE kind IN ('claim','symbol','topic') "
            "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.subject_id=nodes.id) "
            "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.object_id=nodes.id)"
        ).rowcount
    return {
        "schema": "rso-prune-orphans/v1",
        "mode": "applied",
        "deleted": counts,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "note": "Free pages are reused; run `compact --yes` to shrink the file.",
    }
