"""Find and (only on request) merge duplicate or prune junk project registrations.

``plan_cleanup`` is read-only. ``apply_cleanup`` changes the ledger and is only
reached through ``rso-context cleanup-projects --apply``; back the ledger up first.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

from .db import Database, batched, delete_fts_for_chunks, sql_batch_size
from .identity import folder_hash_from_key, utc_now

CLEANUP_SCHEMA = "rso-project-cleanup/v1"

# Folder segments that agent hosts use for throwaway checkouts.
_WORKTREE_MARKERS = (
    ("/.claude/worktrees/",),
    ("/antigravity/worktrees/",),
    ("/.codex/worktrees/",),
    ("/.cursor/worktrees/",),
    ("/.worktrees/",),
)
_SCRATCH_NAME = re.compile(r"^agent-[0-9a-f]{8,}$", re.IGNORECASE)
_STRONG_CLAIM_STATES = ("proposed", "verified", "disputed", "superseded")


def _norm(path: str) -> str:
    return os.path.normcase(str(path)).replace("\\", "/").rstrip("/") + "/"


def _temp_roots() -> list[str]:
    roots = {_norm(tempfile.gettempdir()), "/tmp/"}
    if os.name == "nt":
        drive = os.environ.get("SystemDrive", "C:")
        roots.add(_norm(drive + "\\tmp"))
        roots.add(_norm(drive + "\\temp"))
    return sorted(roots)


def _project_rows(connection) -> list[dict[str, object]]:
    rows = connection.execute(
        "SELECT p.id,p.display_name,p.kind,p.scope,p.canonical_key,p.git_root_commit,"
        "p.corpus_version,p.created_at,"
        "(SELECT COUNT(*) FROM sources s WHERE s.project_id=p.id AND s.active=1) AS active_sources,"
        "(SELECT COUNT(*) FROM runs r WHERE r.project_id=p.id) AS runs,"
        "(SELECT COUNT(*) FROM claims c WHERE c.project_id=p.id AND c.trust_state IN "
        "('proposed','verified','disputed','superseded')) AS ledger_claims,"
        "(SELECT COUNT(*) FROM validations v JOIN claims c ON c.id=v.claim_id "
        "WHERE c.project_id=p.id) AS validations "
        "FROM projects p ORDER BY p.display_name, p.created_at"
    ).fetchall()
    projects = [dict(row) for row in rows]
    for project in projects:
        project["paths"] = sorted(
            {
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT resolved_path FROM project_aliases "
                    "WHERE project_id=? AND resolved_path IS NOT NULL",
                    (project["id"],),
                )
            }
        )
    return projects


def _strength(project: dict[str, object]) -> tuple:
    key = str(project["canonical_key"])
    form = 3 if key.startswith("git:") else 2 if key.startswith("git-common:") else 1 if project["kind"] == "git" else 0
    return (
        int(project["validations"]),
        int(project["ledger_claims"]),
        int(project["corpus_version"]),
        int(project["active_sources"]),
        form,
        str(project["created_at"]),
    )


def _summary(project: dict[str, object]) -> dict[str, object]:
    return {
        key: project[key]
        for key in (
            "id", "display_name", "kind", "corpus_version", "active_sources",
            "runs", "ledger_claims", "validations",
        )
    } | {"path": project["paths"][0] if project["paths"] else None}


def _prune_reasons(project: dict[str, object], temp_roots: list[str]) -> list[str]:
    reasons: list[str] = []
    paths = [str(path) for path in project["paths"]]
    normalized = [_norm(path) for path in paths]
    if not paths:
        reasons.append("no_registered_path")
    elif not any(Path(path).is_dir() for path in paths):
        reasons.append("path_missing")
    if any(marker[0] in path for path in normalized for marker in _WORKTREE_MARKERS):
        reasons.append("agent_worktree")
    if any(path.startswith(root) for path in normalized for root in temp_roots):
        reasons.append("temp_folder")
    if _SCRATCH_NAME.match(str(project["display_name"])):
        reasons.append("scratch_name")
    if int(project["active_sources"]) == 0 and int(project["runs"]) == 0:
        reasons.append("empty")
    return reasons


def plan_cleanup(database: Database) -> dict[str, object]:
    """Read-only plan: duplicate registrations of one folder, and junk projects."""
    database.initialize()
    with database.connect() as connection:
        projects = _project_rows(connection)
    temp_roots = _temp_roots()

    by_folder: dict[str, list[dict[str, object]]] = {}
    for project in projects:
        folder = folder_hash_from_key(str(project["canonical_key"]))
        keys = {folder} if folder else set()
        keys.update(_norm(path) for path in project["paths"])
        project["_keys"] = keys
    # Union projects that share a folder hash or an exact registered path.
    parent: dict[str, str] = {str(p["id"]): str(p["id"]) for p in projects}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    owner: dict[str, str] = {}
    for project in projects:
        for key in project["_keys"]:
            if key in owner:
                parent[find(str(project["id"]))] = find(owner[key])
            else:
                owner[key] = str(project["id"])
    for project in projects:
        by_folder.setdefault(find(str(project["id"])), []).append(project)

    merges = []
    merged_ids: set[str] = set()
    for group in by_folder.values():
        if len(group) < 2:
            continue
        roots = {p["git_root_commit"] for p in group if p["git_root_commit"]}
        if len(roots) > 1:
            merges.append(
                {
                    "action": "review",
                    "reason": "same folder, different git root commits",
                    "projects": [_summary(p) for p in group],
                }
            )
            continue
        survivor = max(group, key=_strength)
        others = [p for p in group if p is not survivor]
        # Neither side of a merge is also offered for pruning in the same plan.
        merged_ids.update(str(p["id"]) for p in group)
        merges.append(
            {
                "action": "merge",
                "reason": "same folder registered under several identity keys",
                "survivor": _summary(survivor),
                "merge": [_summary(p) for p in others],
            }
        )

    prunes = []
    for project in projects:
        if str(project["id"]) in merged_ids:
            continue
        reasons = _prune_reasons(project, temp_roots)
        strong = {"path_missing", "agent_worktree", "temp_folder", "scratch_name", "empty", "no_registered_path"}
        if not reasons or not strong.intersection(reasons):
            continue
        protected = int(project["validations"]) > 0 or int(project["ledger_claims"]) > 0
        prunes.append(
            _summary(project)
            | {
                "action": "prune",
                "reasons": reasons,
                "protected": protected,
                "protected_reason": (
                    "has proposed/validated claims; kept unless --include-protected" if protected else None
                ),
            }
        )

    merge_count = sum(len(item.get("merge") or []) for item in merges if item["action"] == "merge")
    return {
        "schema": CLEANUP_SCHEMA,
        "mode": "dry_run",
        "database": str(database.path),
        "pending": pending_cleanup(database),
        "projects": len(projects),
        "merge_groups": merges,
        "prune": prunes,
        "summary": {
            "projects": len(projects),
            "merge_groups": sum(1 for item in merges if item["action"] == "merge"),
            "projects_to_merge": merge_count,
            "review_groups": sum(1 for item in merges if item["action"] == "review"),
            "prune_candidates": len(prunes),
            "prune_protected": sum(1 for item in prunes if item["protected"]),
            "projects_after": len(projects) - merge_count - sum(1 for item in prunes if not item["protected"]),
        },
        "note": "Dry run: nothing was changed. Re-run with --apply after backing up the ledger.",
    }


def _move(connection, table: str, column: str, source: str, target: str) -> None:
    connection.execute(f"UPDATE OR IGNORE {table} SET {column}=? WHERE {column}=?", (target, source))
    connection.execute(f"DELETE FROM {table} WHERE {column}=?", (source,))


PENDING_KEY = "cleanup_pending"
DEFAULT_BATCH_ROWS = 2000
DEFAULT_PAUSE_SECONDS = 0.05
CLEANUP_BUSY_TIMEOUT_MS = 5000


class _Batcher:
    """Run cleanup work as many short write transactions.

    Each step takes the write lock with BEGIN IMMEDIATE, does a bounded amount of
    work, commits, and then pauses so other readers and writers get through. An
    interruption loses at most the step in flight, which SQLite rolls back.
    """

    def __init__(self, connection, *, pause: float, progress) -> None:
        self.connection = connection
        self.pause = pause
        self.progress = progress
        self.transactions = 0
        self.longest_ms = 0.0
        self.total_lock_ms = 0.0

    def step(self, work):
        started = time.monotonic()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            result = work(self.connection)
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        held = (time.monotonic() - started) * 1000
        self.transactions += 1
        self.total_lock_ms += held
        self.longest_ms = max(self.longest_ms, held)
        if self.pause:
            time.sleep(self.pause)
        return result

    def drain(self, work) -> int:
        """Repeat a step until it reports zero rows; return the total."""
        total = 0
        while True:
            done = self.step(work)
            if not done:
                return total
            total += done


def _ids(connection, sql: str, params: tuple) -> list:
    return [row[0] for row in connection.execute(sql, params)]


def _delete_project_batched(batcher: _Batcher, project_id: str, rows: int) -> dict[str, int]:
    """Delete one project's rows in the same foreign-key order, a batch at a time."""
    counts: dict[str, int] = {}

    def edges(connection):
        ids = _ids(connection, "SELECT rowid FROM edges WHERE project_id=? LIMIT ?", (project_id, rows))
        for batch in batched(ids, sql_batch_size(connection)):
            connection.execute(f"DELETE FROM edges WHERE rowid IN ({','.join('?' * len(batch))})", batch)
        return len(ids)

    def claims(connection):
        ids = _ids(connection, "SELECT id FROM claims WHERE project_id=? LIMIT ?", (project_id, rows))
        for batch in batched(ids, sql_batch_size(connection)):
            marks = ",".join("?" * len(batch))
            connection.execute(f"DELETE FROM claim_evidence WHERE claim_id IN ({marks})", batch)
            counts["validations"] = counts.get("validations", 0) + connection.execute(
                f"DELETE FROM validations WHERE claim_id IN ({marks})", batch
            ).rowcount
            connection.execute(f"DELETE FROM claims WHERE id IN ({marks})", batch)
        return len(ids)

    def nodes(connection):
        ids = _ids(connection, "SELECT rowid FROM nodes WHERE project_id=? LIMIT ?", (project_id, rows))
        for batch in batched(ids, sql_batch_size(connection)):
            connection.execute(f"DELETE FROM nodes WHERE rowid IN ({','.join('?' * len(batch))})", batch)
        return len(ids)

    def chunks(connection):
        ids = [
            int(value)
            for value in _ids(
                connection,
                "SELECT c.id FROM chunks c JOIN source_versions v ON v.id=c.source_version_id "
                "JOIN sources s ON s.id=v.source_id WHERE s.project_id=? LIMIT ?",
                (project_id, rows),
            )
        ]
        for batch in batched(ids, sql_batch_size(connection)):
            marks = ",".join("?" * len(batch))
            connection.execute(f"UPDATE edges SET evidence_chunk_id=NULL WHERE evidence_chunk_id IN ({marks})", batch)
            connection.execute(f"DELETE FROM claim_evidence WHERE chunk_id IN ({marks})", batch)
        counts["fts_rows"] = counts.get("fts_rows", 0) + delete_fts_for_chunks(connection, ids)
        for batch in batched(ids, sql_batch_size(connection)):
            connection.execute(f"DELETE FROM chunks WHERE id IN ({','.join('?' * len(batch))})", batch)
        return len(ids)

    def versions(connection):
        ids = _ids(
            connection,
            "SELECT v.id FROM source_versions v JOIN sources s ON s.id=v.source_id WHERE s.project_id=? LIMIT ?",
            (project_id, rows),
        )
        for batch in batched(ids, sql_batch_size(connection)):
            connection.execute(f"DELETE FROM source_versions WHERE id IN ({','.join('?' * len(batch))})", batch)
        return len(ids)

    def sources(connection):
        ids = _ids(connection, "SELECT id FROM sources WHERE project_id=? LIMIT ?", (project_id, rows))
        for batch in batched(ids, sql_batch_size(connection)):
            connection.execute(f"DELETE FROM sources WHERE id IN ({','.join('?' * len(batch))})", batch)
        return len(ids)

    for name, work in (
        ("edges", edges),
        ("claims", claims),
        ("nodes", nodes),
        ("chunks", chunks),
        ("source_versions", versions),
        ("sources", sources),
    ):
        counts[name] = batcher.drain(work)

    def finish(connection):
        for table in ("project_aliases", "runs", "run_budgets", "cache"):
            counts[table] = connection.execute(f"DELETE FROM {table} WHERE project_id=?", (project_id,)).rowcount
        connection.execute(
            "DELETE FROM possible_project_matches WHERE left_project_id=? OR right_project_id=?",
            (project_id, project_id),
        )
        connection.execute("DELETE FROM projects WHERE id=?", (project_id,))

    batcher.step(finish)
    return counts


def _prepare_merge(connection, source: str, target: str) -> dict[str, object] | None:
    """Move history from a duplicate to its survivor (idempotent). None means proceed."""
    orphaned = connection.execute(
        "SELECT COUNT(*) FROM validations v JOIN claims c ON c.id=v.claim_id "
        "WHERE c.project_id=? AND NOT EXISTS (SELECT 1 FROM claims t WHERE t.project_id=? "
        "AND t.normalized_text=c.normalized_text)",
        (source, target),
    ).fetchone()[0]
    if orphaned:
        return {"merged": False, "reason": f"{orphaned} validation(s) have no matching survivor claim"}
    for row in connection.execute(
        "SELECT c.id AS old_id, t.id AS new_id FROM claims c JOIN claims t "
        "ON t.project_id=? AND t.normalized_text=c.normalized_text WHERE c.project_id=?",
        (target, source),
    ).fetchall():
        connection.execute("UPDATE validations SET claim_id=? WHERE claim_id=?", (row["new_id"], row["old_id"]))
        connection.execute(
            "UPDATE claims SET trust_state=(SELECT trust_state FROM claims WHERE id=?), updated_at=? "
            "WHERE id=? AND trust_state='observed' AND (SELECT trust_state FROM claims WHERE id=?)<>'observed'",
            (row["old_id"], utc_now(), row["new_id"], row["old_id"]),
        )
    connection.execute(
        "UPDATE OR IGNORE claims SET project_id=? WHERE project_id=? AND trust_state='proposed'",
        (target, source),
    )
    _move(connection, "project_aliases", "project_id", source, target)
    connection.execute("UPDATE runs SET project_id=? WHERE project_id=?", (target, source))
    _move(connection, "run_budgets", "project_id", source, target)
    return None


def _read_pending(connection) -> list[dict[str, str]] | None:
    row = connection.execute("SELECT value FROM meta WHERE key=?", (PENDING_KEY,)).fetchone()
    if row is None:
        return None
    try:
        return list(json.loads(row[0]).get("items", []))
    except (ValueError, AttributeError):
        return None


def _write_pending(connection, items: list[dict[str, str]]) -> None:
    if items:
        connection.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (PENDING_KEY, json.dumps({"schema": "rso-cleanup-pending/v1", "items": items})),
        )
    else:
        connection.execute("DELETE FROM meta WHERE key=?", (PENDING_KEY,))


def pending_cleanup(database: Database) -> list[dict[str, str]]:
    database.initialize()
    connection = database.connect()
    try:
        return _read_pending(connection) or []
    finally:
        connection.close()


def apply_cleanup(
    database: Database,
    *,
    merge: bool = True,
    prune: bool = True,
    include_protected: bool = False,
    project_ids: list[str] | None = None,
    batch_rows: int = DEFAULT_BATCH_ROWS,
    pause_seconds: float = DEFAULT_PAUSE_SECONDS,
    progress=None,
) -> dict[str, object]:
    """Apply a cleanup plan in short, resumable write transactions.

    The work list is frozen in the ledger's meta table before anything is
    deleted. A later --apply after an interruption finishes that list first,
    so a partly deleted project is always completed rather than re-planned.
    """
    say = progress or (lambda message: None)
    plan = plan_cleanup(database)
    wanted = set(project_ids or [])
    results: list[dict[str, object]] = []
    started = time.monotonic()
    connection = database.connect()
    try:
        connection.execute(f"PRAGMA busy_timeout = {int(CLEANUP_BUSY_TIMEOUT_MS)}")
        batcher = _Batcher(connection, pause=pause_seconds, progress=say)
        items = _read_pending(connection)
        resumed = items is not None
        if not resumed:
            items = []
            if merge:
                for group in plan["merge_groups"]:
                    if group["action"] != "merge":
                        continue
                    target = str(group["survivor"]["id"])
                    for item in group["merge"]:
                        if wanted and str(item["id"]) not in wanted:
                            continue
                        items.append({"action": "merge", "project": str(item["id"]), "into": target})
            if prune:
                for item in plan["prune"]:
                    if wanted and str(item["id"]) not in wanted:
                        continue
                    if item["protected"] and not include_protected:
                        results.append({"action": "prune", "project": item["id"], "skipped": "protected"})
                        continue
                    items.append({"action": "prune", "project": str(item["id"])})
            frozen = list(items)
            batcher.step(lambda c: _write_pending(c, frozen))
        else:
            say(f"resuming an interrupted cleanup: {len(items)} project(s) left")
        total = len(items)
        for index, item in enumerate(list(items), start=1):
            project_id = item["project"]
            exists = connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone()
            outcome: dict[str, object] = {"action": item["action"], "project": project_id}
            if item.get("into"):
                outcome["into"] = item["into"]
            if exists is None:
                outcome["skipped"] = "already removed"
            else:
                blocked = None
                if item["action"] == "merge":
                    blocked = batcher.step(lambda c: _prepare_merge(c, project_id, item["into"]))
                if blocked:
                    outcome |= blocked
                else:
                    deleted = _delete_project_batched(batcher, project_id, batch_rows)
                    if item["action"] == "merge":
                        target = item["into"]
                        batcher.step(
                            lambda c: (
                                c.execute(
                                    "UPDATE projects SET corpus_version=corpus_version+1, updated_at=? WHERE id=?",
                                    (utc_now(), target),
                                ),
                                c.execute("DELETE FROM cache WHERE project_id=?", (target,)),
                            )
                            and None
                        )
                        outcome["merged"] = True
                    outcome["deleted"] = deleted
            results.append(outcome)
            items.remove(item)
            remaining = list(items)
            batcher.step(lambda c: _write_pending(c, remaining))
            say(
                f"[{index}/{total}] {item['action']} {project_id[:8]}: "
                f"{(outcome.get('deleted') or {}).get('chunks', 0)} chunks, "
                f"{outcome.get('skipped') or outcome.get('reason') or 'done'} "
                f"({time.monotonic() - started:.0f}s, longest lock {batcher.longest_ms:.0f} ms)"
            )
    finally:
        connection.close()
    plan["mode"] = "applied"
    plan["resumed"] = resumed
    plan["results"] = results
    plan["timing"] = {
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "transactions": batcher.transactions,
        "longest_lock_ms": round(batcher.longest_ms, 1),
        "total_lock_ms": round(batcher.total_lock_ms, 1),
        "batch_rows": batch_rows,
        "pause_seconds": pause_seconds,
    }
    plan["note"] = "Applied. Re-run ingest on surviving projects to refresh their corpus."
    return plan
