from __future__ import annotations

import os
from pathlib import Path


def _resolved(path: str | Path) -> Path:
    try:
        return Path(path).expanduser().resolve()
    except OSError:
        return Path(os.path.normpath(str(path)))


def path_is_parent(parent: str | Path, child: str | Path) -> bool:
    """True when parent is a real ancestor directory of child."""
    parent_path = _resolved(parent)
    child_path = _resolved(child)
    if parent_path == child_path:
        return False
    is_relative_to = getattr(child_path, "is_relative_to", None)
    if callable(is_relative_to):
        try:
            return bool(child_path.is_relative_to(parent_path))
        except (ValueError, OSError, TypeError):
            return False
    return str(child_path).startswith(str(parent_path) + os.sep)


def _child_path(connection, project_id: str, path: str | Path | None) -> str | None:
    if path is not None:
        return str(_resolved(path))
    row = connection.execute(
        "SELECT resolved_path FROM project_aliases "
        "WHERE project_id=? AND resolved_path IS NOT NULL "
        "ORDER BY last_seen DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    return str(_resolved(row["resolved_path"]))


def search_scope(connection, project_id: str, path: str | Path | None = None) -> dict:
    """Ordered search: active project, path-parent domains, then shared.

    Returns ``order`` (``{id, scope, name}``), ``ids``, and ``corpus_versions``.
    Unrelated ``project`` siblings are omitted. Only domains whose alias path
    is a parent of the active (or query) path are included.
    """
    active = connection.execute(
        "SELECT id, scope, display_name, corpus_version FROM projects WHERE id=?",
        (project_id,),
    ).fetchone()
    if active is None:
        return {"order": [], "ids": [], "corpus_versions": {}}

    order: list[dict[str, object]] = [
        {"id": active["id"], "scope": active["scope"], "name": active["display_name"]}
    ]
    seen: set[str] = {str(active["id"])}
    versions: dict[str, int] = {str(active["id"]): int(active["corpus_version"])}
    child = _child_path(connection, project_id, path)

    domain_hits: list[tuple[int, str, dict[str, object]]] = []
    domain_rows = connection.execute(
        "SELECT p.id, p.scope, p.display_name, p.corpus_version, a.resolved_path "
        "FROM projects p JOIN project_aliases a ON a.project_id=p.id "
        "WHERE p.scope='domain' AND p.id<>? AND a.resolved_path IS NOT NULL",
        (project_id,),
    ).fetchall()
    for row in domain_rows:
        domain_id = str(row["id"])
        if domain_id in seen:
            continue
        if child is None or not path_is_parent(row["resolved_path"], child):
            continue
        depth = len(Path(str(row["resolved_path"])).parts)
        domain_hits.append(
            (
                depth,
                domain_id,
                {
                    "id": domain_id,
                    "scope": row["scope"],
                    "name": row["display_name"],
                    "corpus_version": int(row["corpus_version"]),
                },
            )
        )
        seen.add(domain_id)
    domain_hits.sort(key=lambda item: (-item[0], item[1]))
    for _, _, item in domain_hits:
        order.append({"id": item["id"], "scope": item["scope"], "name": item["name"]})
        versions[str(item["id"])] = int(item["corpus_version"])

    shared_rows = connection.execute(
        "SELECT id, scope, display_name, corpus_version FROM projects "
        "WHERE scope='shared' ORDER BY id",
    ).fetchall()
    for row in shared_rows:
        shared_id = str(row["id"])
        if shared_id in seen:
            continue
        seen.add(shared_id)
        order.append({"id": shared_id, "scope": row["scope"], "name": row["display_name"]})
        versions[shared_id] = int(row["corpus_version"])

    return {
        "order": order,
        "ids": [str(item["id"]) for item in order],
        "corpus_versions": versions,
    }
