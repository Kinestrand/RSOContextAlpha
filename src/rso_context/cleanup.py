"""Find and (only on request) merge duplicate or prune junk project registrations.

``plan_cleanup`` is read-only. ``apply_cleanup`` changes the ledger and is only
reached through ``rso-context cleanup-projects --apply``; back the ledger up first.
"""
from __future__ import annotations

import os
import re
import tempfile
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


def _delete_project_data(connection, project_id: str) -> dict[str, int]:
    """Delete one project's derived rows in foreign-key order."""
    counts: dict[str, int] = {}
    chunk_ids = [
        int(row[0])
        for row in connection.execute(
            "SELECT c.id FROM chunks c JOIN source_versions v ON v.id=c.source_version_id "
            "JOIN sources s ON s.id=v.source_id WHERE s.project_id=?",
            (project_id,),
        )
    ]
    counts["edges"] = connection.execute("DELETE FROM edges WHERE project_id=?", (project_id,)).rowcount
    # Edges of other projects never cite this project's chunks, but guard anyway.
    for batch in batched(chunk_ids, sql_batch_size(connection)):
        marks = ",".join("?" for _ in batch)
        connection.execute(f"UPDATE edges SET evidence_chunk_id=NULL WHERE evidence_chunk_id IN ({marks})", batch)
        connection.execute(f"DELETE FROM claim_evidence WHERE chunk_id IN ({marks})", batch)
    claim_ids = [str(row[0]) for row in connection.execute("SELECT id FROM claims WHERE project_id=?", (project_id,))]
    for batch in batched(claim_ids, sql_batch_size(connection)):
        marks = ",".join("?" for _ in batch)
        connection.execute(f"DELETE FROM claim_evidence WHERE claim_id IN ({marks})", batch)
        counts["validations"] = counts.get("validations", 0) + connection.execute(
            f"DELETE FROM validations WHERE claim_id IN ({marks})", batch
        ).rowcount
    counts["claims"] = connection.execute("DELETE FROM claims WHERE project_id=?", (project_id,)).rowcount
    counts["nodes"] = connection.execute("DELETE FROM nodes WHERE project_id=?", (project_id,)).rowcount
    counts["fts_rows"] = delete_fts_for_chunks(connection, chunk_ids)
    for batch in batched(chunk_ids, sql_batch_size(connection)):
        marks = ",".join("?" for _ in batch)
        connection.execute(f"DELETE FROM chunks WHERE id IN ({marks})", batch)
    counts["chunks"] = len(chunk_ids)
    connection.execute(
        "DELETE FROM source_versions WHERE source_id IN (SELECT id FROM sources WHERE project_id=?)",
        (project_id,),
    )
    counts["sources"] = connection.execute("DELETE FROM sources WHERE project_id=?", (project_id,)).rowcount
    for table in ("project_aliases", "runs", "run_budgets", "cache"):
        counts[table] = connection.execute(f"DELETE FROM {table} WHERE project_id=?", (project_id,)).rowcount
    connection.execute(
        "DELETE FROM possible_project_matches WHERE left_project_id=? OR right_project_id=?",
        (project_id, project_id),
    )
    connection.execute("DELETE FROM projects WHERE id=?", (project_id,))
    return counts


def _merge_into(connection, source: str, target: str) -> dict[str, object]:
    """Keep the duplicate's history on the survivor, then drop its derived index.

    Aliases, runs and budgets move. Proposed claims move. A validation moves to
    the survivor's claim with the same normalized text; one without such a claim
    stays put and blocks the merge so no named validation is ever lost.
    """
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
    counts = _delete_project_data(connection, source)
    connection.execute(
        "UPDATE projects SET corpus_version=corpus_version+1, updated_at=? WHERE id=?", (utc_now(), target)
    )
    connection.execute("DELETE FROM cache WHERE project_id=?", (target,))
    return {"merged": True, "deleted": counts}


def apply_cleanup(
    database: Database,
    *,
    merge: bool = True,
    prune: bool = True,
    include_protected: bool = False,
    project_ids: list[str] | None = None,
) -> dict[str, object]:
    plan = plan_cleanup(database)
    wanted = set(project_ids or [])
    results: list[dict[str, object]] = []
    with database.transaction() as connection:
        if merge:
            for group in plan["merge_groups"]:
                if group["action"] != "merge":
                    continue
                target = str(group["survivor"]["id"])
                for item in group["merge"]:
                    if wanted and str(item["id"]) not in wanted:
                        continue
                    outcome = _merge_into(connection, str(item["id"]), target)
                    results.append({"action": "merge", "project": item["id"], "into": target} | outcome)
        if prune:
            for item in plan["prune"]:
                if wanted and str(item["id"]) not in wanted:
                    continue
                if item["protected"] and not include_protected:
                    results.append({"action": "prune", "project": item["id"], "skipped": "protected"})
                    continue
                counts = _delete_project_data(connection, str(item["id"]))
                results.append({"action": "prune", "project": item["id"], "deleted": counts})
    plan["mode"] = "applied"
    plan["results"] = results
    plan["note"] = "Applied. Re-run ingest on surviving projects to refresh their corpus."
    return plan
