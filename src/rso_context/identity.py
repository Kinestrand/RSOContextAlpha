from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .config import PROJECT_MARKERS
from .db import Database, json_text


PROJECT_NAMESPACE = uuid.UUID("a675f736-7358-4d37-a45a-9b192538ab42")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def sha256_file(path: Path, limit: int | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        remaining = limit
        while True:
            size = 1024 * 1024 if remaining is None else min(1024 * 1024, remaining)
            if size <= 0:
                break
            data = stream.read(size)
            if not data:
                break
            digest.update(data)
            if remaining is not None:
                remaining -= len(data)
    return digest.hexdigest()


def _git(path: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def normalize_git_remote(value: str | None) -> str | None:
    if not value:
        return None
    remote = value.strip().replace("\\", "/")
    scp_match = re.match(r"^(?:[^@/]+@)?([^:]+):(.+)$", remote)
    if scp_match and "://" not in remote:
        host = scp_match.group(1).lower()
        path = scp_match.group(2).strip("/")
        remote = f"ssh://{host}/{path}"
    try:
        parsed = urlsplit(remote)
        if parsed.scheme:
            host = (parsed.hostname or "").lower()
            port = f":{parsed.port}" if parsed.port else ""
            path = parsed.path.rstrip("/")
            if path.endswith(".git"):
                path = path[:-4]
            return urlunsplit((parsed.scheme.lower(), host + port, path.lower(), "", ""))
    except ValueError:
        pass
    remote = remote.rstrip("/")
    if remote.endswith(".git"):
        remote = remote[:-4]
    return remote.lower()


def _structure_signature(root: Path) -> tuple[str, int, int, list[str]]:
    records: list[str] = []
    marker_count = 0
    marker_file_count = 0
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        children = []

    for child in children:
        name = child.name
        if name in PROJECT_MARKERS or child.suffix.lower() in {".sln", ".code-workspace"}:
            marker_count += 1
            if child.is_file():
                marker_file_count += 1
                try:
                    content_hash = sha256_file(child, limit=256 * 1024)
                except OSError:
                    content_hash = "unreadable"
                records.append(f"file:{name.lower()}:{content_hash}")
            else:
                records.append(f"dir:{name.lower()}")

    for child in children[:200]:
        if child.name.startswith("."):
            continue
        kind = "d" if child.is_dir() else "f"
        records.append(f"shape:{kind}:{child.name.lower()}")

    signature = sha256_text("\n".join(sorted(records)))
    return signature, marker_count, marker_file_count, records


@dataclass(frozen=True)
class ProjectIdentity:
    resolved_path: str
    display_name: str
    kind: str
    canonical_key: str
    git_remote: str | None
    git_root_commit: str | None
    git_common_dir: str | None
    git_worktree_root: str | None
    structure_hash: str
    marker_count: int
    marker_file_count: int
    structure_records: list[str]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def inspect_project(path: str | Path) -> ProjectIdentity:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"Project path is not a directory: {resolved}")

    worktree = _git(resolved, "rev-parse", "--show-toplevel")
    project_root = Path(worktree).resolve() if worktree else resolved
    common_dir = _git(project_root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common_dir:
        common_dir = str(Path(common_dir).resolve())
    remote = normalize_git_remote(_git(project_root, "config", "--get", "remote.origin.url"))
    roots = _git(project_root, "rev-list", "--max-parents=0", "HEAD")
    root_commit = sorted(roots.splitlines())[0] if roots else None
    structure_hash, marker_count, marker_file_count, records = _structure_signature(project_root)
    path_hash = sha256_text(os.path.normcase(str(project_root)))

    tree_tag = f"#tree:{path_hash}"
    if remote and root_commit:
        canonical_key = f"git:{remote}#{root_commit}{tree_tag}"
        kind = "git"
    elif common_dir:
        canonical_key = f"git-common:{sha256_text(os.path.normcase(common_dir))}{tree_tag}"
        kind = "git"
    elif root_commit or worktree:
        canonical_key = f"git-path:{path_hash}"
        kind = "git"
    else:
        # File contents and folder shape are evidence, not project identity.
        # Independent directories can have identical templates.
        canonical_key = f"directory-path:{path_hash}"
        kind = "directory"

    return ProjectIdentity(
        resolved_path=str(project_root),
        display_name=project_root.name,
        kind=kind,
        canonical_key=canonical_key,
        git_remote=remote,
        git_root_commit=root_commit,
        git_common_dir=common_dir,
        git_worktree_root=str(project_root) if worktree else None,
        structure_hash=structure_hash,
        marker_count=marker_count,
        marker_file_count=marker_file_count,
        structure_records=records,
    )


def _project_id(canonical_key: str) -> str:
    return str(uuid.uuid5(PROJECT_NAMESPACE, canonical_key))


def register_project(
    database: Database,
    path: str | Path,
    *,
    agent: str = "observer",
    external_id: str | None = None,
    scope: str = "project",
) -> dict[str, object]:
    database.initialize()
    identity = inspect_project(path)
    now = utc_now()
    match_method = "canonical_key"
    confidence = 1.0
    possible_candidates: list[dict[str, object]] = []
    created_new = False

    with database.transaction() as connection:
        project = connection.execute(
            "SELECT * FROM projects WHERE canonical_key = ?", (identity.canonical_key,)
        ).fetchone()

        if project is None and identity.kind == "directory":
            # Retain legacy structure-keyed projects by their registered folder.
            # Keep the original project row and key so all history and named
            # validations retain their existing foreign-key identity. Never use
            # a directory alias to join a Git worktree to another project.
            path_collation = " COLLATE NOCASE" if os.name == "nt" else ""
            alias = connection.execute(
                "SELECT p.* FROM project_aliases a JOIN projects p ON p.id=a.project_id "
                f"WHERE a.resolved_path = ?{path_collation} AND p.kind = 'directory' "
                "ORDER BY a.last_seen DESC, a.id DESC LIMIT 1",
                (identity.resolved_path,),
            ).fetchone()
            if alias is not None:
                project = alias
                match_method = "resolved_path"

        if project is None and identity.kind != "git":
            # Similar structure is a possible match only, never authorization
            # to combine independent folders or their evidence histories.
            candidates = connection.execute(
                "SELECT * FROM projects WHERE structure_hash=?", (identity.structure_hash,)
            ).fetchall()
            if candidates:
                possible_candidates = [dict(candidate) for candidate in candidates]

        if project is None:
            project_id = _project_id(identity.canonical_key)
            connection.execute(
                "INSERT INTO projects(id,canonical_key,display_name,scope,kind,git_remote,"
                "git_root_commit,structure_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    project_id,
                    identity.canonical_key,
                    identity.display_name,
                    scope,
                    identity.kind,
                    identity.git_remote,
                    identity.git_root_commit,
                    identity.structure_hash,
                    now,
                    now,
                ),
            )
            project = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            match_method = "created"
            created_new = True
        else:
            project_id = project["id"]
            connection.execute(
                "UPDATE projects SET updated_at=?, display_name=CASE WHEN display_name='' THEN ? ELSE display_name END "
                "WHERE id=?",
                (now, identity.display_name, project_id),
            )

        external_key = external_id or f"path:{os.path.normcase(identity.resolved_path)}"
        connection.execute(
            "INSERT INTO project_aliases(project_id,agent,external_id,resolved_path,match_method,"
            "match_confidence,identity_json,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(agent, external_id) DO UPDATE SET project_id=excluded.project_id,"
            "resolved_path=excluded.resolved_path,match_method=excluded.match_method,"
            "match_confidence=excluded.match_confidence,identity_json=excluded.identity_json,"
            "last_seen=excluded.last_seen",
            (
                project_id,
                agent,
                external_key,
                identity.resolved_path,
                match_method,
                confidence,
                json_text(identity.as_dict()),
                now,
                now,
            ),
        )

        if created_new:
            for candidate in possible_candidates:
                candidate_id = str(candidate["id"])
                left_id, right_id = sorted((project_id, candidate_id))
                connection.execute(
                    "INSERT INTO possible_project_matches(left_project_id,right_project_id,score,"
                    "evidence_json,observed_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(left_project_id,right_project_id) DO UPDATE SET "
                    "score=excluded.score,evidence_json=excluded.evidence_json,"
                    "observed_at=excluded.observed_at",
                    (
                        left_id,
                        right_id,
                        0.55,
                        json_text(
                            {
                                "method": "weak_structure",
                                "structure_hash": identity.structure_hash,
                                "marker_count": identity.marker_count,
                                "marker_file_count": identity.marker_file_count,
                            }
                        ),
                        now,
                    ),
                )

        project_dict = dict(connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())

    return {
        "project": project_dict,
        "identity": identity.as_dict(),
        "match": {"method": match_method, "confidence": confidence},
    }


def path_variants(path: str | Path) -> list[str]:
    """Comparable spellings of a workspace path (drive letter vs UNC, slashes)."""
    raw = Path(path).expanduser()
    values: list[str] = [str(path), str(raw)]
    try:
        values.append(str(raw.resolve()))
    except OSError:
        pass
    variants: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        for candidate in (value, value.replace("/", "\\"), value.replace("\\", "/")):
            key = os.path.normcase(candidate.rstrip("/\\"))
            if key in seen:
                continue
            seen.add(key)
            variants.append(candidate.rstrip("/\\") or candidate)
    return variants


def source_path_filter(column: str, path: str | Path | None) -> tuple[str, list[str]]:
    """SQL clause limiting sources to a workspace path. Empty if path is None."""
    if path is None:
        return "", []
    if os.name != "nt":
        prefix = str(Path(path).expanduser().resolve()).rstrip("/") or "/"
        return (f"({column} = ? COLLATE BINARY OR instr({column}, ?) = 1)",
                [prefix, prefix.rstrip("/") + "/"])
    norms: list[str] = []
    seen: set[str] = set()
    for prefix in path_variants(path):
        normalized = os.path.normcase(prefix).replace("\\", "/").lower().rstrip("/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            norms.append(normalized)
    if not norms:
        return "", []
    col = f"replace(lower({column}), char(92), '/')"
    parts: list[str] = []
    params: list[str] = []
    for normalized in norms:
        parts.append(f"{col} = ?")
        params.append(normalized)
        parts.append(f"instr({col} || '/', ?) = 1")
        params.append(normalized + "/")
    return "(" + " OR ".join(parts) + ")", params


def find_project_for_path(database: Database, path: str | Path) -> dict[str, object] | None:
    database.initialize()
    resolved = Path(path).expanduser().resolve()
    variants = {os.path.normcase(v) for v in path_variants(resolved)}
    variants.add(os.path.normcase(str(path)))
    with database.connect() as connection:
        aliases = connection.execute(
            "SELECT p.*, a.resolved_path, a.match_method, a.match_confidence "
            "FROM project_aliases a JOIN projects p ON p.id=a.project_id "
            "WHERE a.resolved_path IS NOT NULL ORDER BY a.last_seen DESC, a.id DESC"
        ).fetchall()

    exact: list[dict[str, object]] = []
    prefixed: list[tuple[int, dict[str, object]]] = []
    resolved_case = os.path.normcase(str(resolved))
    for row in aliases:
        root = os.path.normcase(str(Path(row["resolved_path"])))
        record = dict(row)
        if root in variants:
            exact.append(record)
            continue
        try:
            common = os.path.commonpath([resolved_case, root])
        except ValueError:
            continue
        if common != root:
            continue
        # Nested worktree aliases must not win for the parent workspace path.
        if len(Path(root).parts) > len(Path(resolved_case).parts):
            continue
        prefixed.append((len(Path(root).parts), record))
    if exact:
        # Registration refreshes the selected alias. Follow that same latest
        # alias when older agents still point at a historical split identity.
        return exact[0]
    if prefixed:
        prefixed.sort(key=lambda item: item[0], reverse=True)
        return prefixed[0][1]
    return None


def resolve_existing_project(
    database: Database,
    *,
    path: str | Path | None = None,
    project_id: str | None = None,
) -> dict[str, object]:
    """Resolve an already-registered project. Never creates a new project."""
    database.initialize()
    if project_id:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown project id: {project_id}")
        return dict(row)
    project_path = Path(path or Path.cwd()).expanduser().resolve()
    found = find_project_for_path(database, project_path)
    if found:
        return found
    raise ValueError(
        f"No registered project for path: {project_path}. "
        "Register and ingest the workspace before query or resume."
    )


def project_explanation(database: Database, path: str | Path) -> dict[str, object]:
    registration = register_project(database, path, agent="explain")
    project_id = registration["project"]["id"]
    with database.connect() as connection:
        aliases = [
            dict(row)
            for row in connection.execute(
                "SELECT agent,external_id,resolved_path,match_method,match_confidence,first_seen,last_seen "
                "FROM project_aliases WHERE project_id=? ORDER BY agent,resolved_path",
                (project_id,),
            )
        ]
        possible_matches = [
            {
                "project_id": row["other_project_id"],
                "project_name": row["display_name"],
                "score": float(row["score"]),
                "evidence": json.loads(row["evidence_json"]),
                "observed_at": row["observed_at"],
            }
            for row in connection.execute(
                "SELECT CASE WHEN m.left_project_id=? THEN m.right_project_id "
                "ELSE m.left_project_id END AS other_project_id,p.display_name,m.score,"
                "m.evidence_json,m.observed_at FROM possible_project_matches m "
                "JOIN projects p ON p.id=CASE WHEN m.left_project_id=? THEN m.right_project_id "
                "ELSE m.left_project_id END "
                "WHERE m.left_project_id=? OR m.right_project_id=? ORDER BY m.score DESC,p.display_name",
                (project_id, project_id, project_id, project_id),
            )
        ]
    registration["aliases"] = aliases
    registration["possible_matches"] = possible_matches
    return registration
