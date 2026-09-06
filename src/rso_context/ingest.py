from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from .config import (
    DOCUMENT_EXTENSIONS,
    IGNORED_DIR_NAMES,
    TEXT_EXTENSIONS,
    Limits,
    is_sensitive_path,
)
from .db import Database, json_text
from .identity import register_project, sha256_file, sha256_text, utc_now
from .pointers import slice_source_lines


NODE_NAMESPACE = uuid.UUID("55f724f6-aaed-459f-8b3f-0aa177ca9cf4")
CLAIM_NAMESPACE = uuid.UUID("bb44190c-303c-4f80-8f8b-145ccbcd8210")

CLAIM_PATTERN = re.compile(
    r"\b(must|should|shall|required|approved|decision|authoritative|canonical|"
    r"do not|don't|never|prohibited|blocked|supersedes?)\b",
    re.IGNORECASE,
)

PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
CREDENTIAL_ASSIGNMENT_PATTERN = re.compile(
    r"(?im)^\s*[\"']?[A-Za-z0-9_.-]*(?:api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|password|passwd|private[_-]?key)"
    r"[A-Za-z0-9_.-]*[\"']?\s*[:=]\s*[\"']?([^\"'\s,;#]{8,})"
)
SAFE_PLACEHOLDER_PREFIXES = (
    "${",
    "<",
    "change-me",
    "changeme",
    "dummy",
    "example",
    "placeholder",
    "redacted",
    "re.compile(",
    "getenv(",
    "os.environ",
    "settings.",
    "your-",
    "your_",
    "xxxxx",
)

SYMBOL_PATTERNS = [
    re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
    re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|interface|type)\s+"
        r"([A-Za-z_$][A-Za-z0-9_$]*)",
        re.MULTILINE,
    ),
    re.compile(r"^\s*(?:public\s+|private\s+|internal\s+)?(?:class|interface|struct)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
]


@dataclass(frozen=True)
class TextChunk:
    ordinal: int
    line_start: int
    line_end: int
    heading: str | None
    text: str


def _media_type(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if path.suffix.lower() in {".md", ".rst"}:
        return "text/markdown"
    if path.suffix.lower() in {".json"}:
        return "application/json"
    if path.suffix.lower() in {".yaml", ".yml"}:
        return "application/yaml"
    return "text/plain"


def is_supported_file(path: Path, limits: Limits) -> bool:
    suffix = path.suffix.lower()
    if is_sensitive_path(path):
        return False
    if suffix not in TEXT_EXTENSIONS and suffix not in DOCUMENT_EXTENSIONS:
        return False
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size <= limits.max_file_bytes
    except OSError:
        return False


def _has_ignored_dir_part(path: Path, root: Path) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return True
    return any(part.casefold() in IGNORED_DIR_NAMES for part in relative.parts)


def _git_ls_files(root: Path) -> list[Path] | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached"],
            check=False,
            capture_output=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    resolved_root = root.resolve()
    files: list[Path] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8", errors="surrogateescape")
        candidate = root / relative
        try:
            resolved = candidate.resolve()
            resolved.relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        files.append(resolved)
    return files


def _walk_project_files(root: Path, limits: Limits) -> list[Path]:
    files: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name.casefold() not in IGNORED_DIR_NAMES and not name.startswith(".")
        )
        for filename in sorted(filenames):
            path = Path(current) / filename
            if _has_ignored_dir_part(path, root):
                continue
            if is_supported_file(path, limits):
                files.append(path)
                if len(files) >= limits.max_files_per_project:
                    return files
    return files


def iter_project_files(root: Path, limits: Limits) -> list[Path]:
    tracked = _git_ls_files(root)
    if tracked is None:
        return _walk_project_files(root, limits)
    files: list[Path] = []
    for path in sorted(tracked, key=lambda item: os.path.normcase(str(item))):
        if _has_ignored_dir_part(path, root):
            continue
        if is_supported_file(path, limits):
            files.append(path)
            if len(files) >= limits.max_files_per_project:
                break
    return files


def _decode_text(path: Path) -> str:
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        raise ValueError("binary content")
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        data = archive.read("word/document.xml")
    root = ElementTree.fromstring(data)
    paragraphs: list[str] = []
    for paragraph in root.iter():
        if not paragraph.tag.endswith("}p"):
            continue
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag.endswith("}t") and node.text:
                parts.append(node.text)
            elif node.tag.endswith("}tab"):
                parts.append("\t")
            elif node.tag.endswith("}br") or node.tag.endswith("}cr"):
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return _docx_text(path)
    return _decode_text(path)


def contains_sensitive_content(text: str) -> bool:
    if PRIVATE_KEY_PATTERN.search(text):
        return True
    for match in CREDENTIAL_ASSIGNMENT_PATTERN.finditer(text):
        value = match.group(1).strip().casefold()
        if value and not value.startswith(SAFE_PLACEHOLDER_PREFIXES):
            return True
    return False


def chunk_text(text: str, max_characters: int = 1_600) -> list[TextChunk]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    chunks: list[TextChunk] = []
    buffer: list[str] = []
    start_line = 1
    current_heading: str | None = None

    def flush(end_line: int) -> None:
        nonlocal buffer, start_line
        value = "\n".join(buffer).strip()
        if value:
            chunks.append(
                TextChunk(
                    ordinal=len(chunks),
                    line_start=start_line,
                    line_end=max(start_line, end_line),
                    heading=current_heading,
                    text=value,
                )
            )
        buffer = []

    for index, line in enumerate(lines, start=1):
        markdown_heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if markdown_heading:
            if buffer:
                flush(index - 1)
            current_heading = markdown_heading.group(1).strip()
            start_line = index

        projected = len("\n".join(buffer)) + len(line) + 1
        if buffer and projected > max_characters:
            flush(index - 1)
            start_line = index
        if not buffer:
            start_line = index
        buffer.append(line)

        if not line.strip() and len("\n".join(buffer)) >= max_characters // 3:
            flush(index)
            start_line = index + 1

    if buffer:
        flush(len(lines))
    return chunks


def _canonical_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _node_id(project_id: str, kind: str, canonical_name: str) -> str:
    return str(uuid.uuid5(NODE_NAMESPACE, f"{project_id}:{kind}:{canonical_name}"))


def _upsert_node(
    connection,
    *,
    project_id: str,
    kind: str,
    name: str,
    trust_state: str = "observed",
    properties: dict[str, object] | None = None,
) -> str:
    canonical = _canonical_name(name)
    node_id = _node_id(project_id, kind, canonical)
    now = utc_now()
    connection.execute(
        "INSERT INTO nodes(id,project_id,kind,canonical_name,display_name,trust_state,"
        "properties_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name,"
        "properties_json=excluded.properties_json,updated_at=excluded.updated_at",
        (
            node_id,
            project_id,
            kind,
            canonical,
            name,
            trust_state,
            json_text(properties or {}),
            now,
            now,
        ),
    )
    return node_id


def _upsert_edge(
    connection,
    *,
    project_id: str,
    subject_id: str,
    predicate: str,
    object_id: str,
    evidence_chunk_id: int | None,
    rule_id: str,
    trust_state: str = "observed",
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO edges(project_id,subject_id,predicate,object_id,trust_state,"
        "evidence_chunk_id,rule_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (
            project_id,
            subject_id,
            predicate,
            object_id,
            trust_state,
            evidence_chunk_id,
            rule_id,
            utc_now(),
        ),
    )


def _extract_graph(
    connection,
    *,
    project_id: str,
    source_id: int,
    relative_path: str,
    chunk_id: int,
    chunk: TextChunk,
) -> tuple[int, int]:
    project_node = _upsert_node(
        connection,
        project_id=project_id,
        kind="project",
        name=project_id,
        properties={"project_id": project_id},
    )
    source_node = _upsert_node(
        connection,
        project_id=project_id,
        kind="source",
        name=relative_path,
        properties={"source_id": source_id, "relative_path": relative_path},
    )
    _upsert_edge(
        connection,
        project_id=project_id,
        subject_id=project_node,
        predicate="contains",
        object_id=source_node,
        evidence_chunk_id=chunk_id,
        rule_id="structure.project-source.v1",
    )

    entity_count = 0
    claim_count = 0
    if chunk.heading:
        heading_node = _upsert_node(
            connection,
            project_id=project_id,
            kind="topic",
            name=chunk.heading,
            properties={"heading": chunk.heading},
        )
        _upsert_edge(
            connection,
            project_id=project_id,
            subject_id=source_node,
            predicate="has_topic",
            object_id=heading_node,
            evidence_chunk_id=chunk_id,
            rule_id="structure.heading.v1",
        )
        entity_count += 1

    symbols: set[str] = set()
    for pattern in SYMBOL_PATTERNS:
        symbols.update(pattern.findall(chunk.text))
    for symbol in sorted(symbols, key=str.casefold):
        symbol_node = _upsert_node(
            connection,
            project_id=project_id,
            kind="symbol",
            name=symbol,
            properties={"declared_in": relative_path},
        )
        _upsert_edge(
            connection,
            project_id=project_id,
            subject_id=source_node,
            predicate="declares",
            object_id=symbol_node,
            evidence_chunk_id=chunk_id,
            rule_id="structure.symbol.v1",
        )
        entity_count += 1

    statements = re.split(r"(?<=[.!?])\s+|\n+", chunk.text)
    for statement in statements:
        display = re.sub(r"\s+", " ", statement).strip()
        if len(display) < 12 or len(display) > 600 or not CLAIM_PATTERN.search(display):
            continue
        normalized = _canonical_name(display)
        claim_id = str(uuid.uuid5(CLAIM_NAMESPACE, f"{project_id}:{normalized}"))
        now = utc_now()
        connection.execute(
            "INSERT INTO claims(id,project_id,normalized_text,display_text,trust_state,created_by,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET display_text=excluded.display_text,updated_at=excluded.updated_at",
            (claim_id, project_id, normalized, display, "observed", "deterministic.rule.v1", now, now),
        )
        connection.execute(
            "INSERT OR IGNORE INTO claim_evidence(claim_id,chunk_id,relation) VALUES(?,?,?)",
            (claim_id, chunk_id, "states"),
        )
        claim_node = _upsert_node(
            connection,
            project_id=project_id,
            kind="claim",
            name=normalized,
            properties={"claim_id": claim_id, "display_text": display},
        )
        _upsert_edge(
            connection,
            project_id=project_id,
            subject_id=source_node,
            predicate="states",
            object_id=claim_node,
            evidence_chunk_id=chunk_id,
            rule_id="claim.modal.v1",
        )
        claim_count += 1

    return entity_count, claim_count


def ingest_project(
    database: Database,
    path: str | Path,
    *,
    agent: str = "observer",
    external_id: str | None = None,
    limits: Limits | None = None,
    scope: str = "project",
) -> dict[str, object]:
    limits = limits or Limits()
    registration = register_project(
        database, path, agent=agent, external_id=external_id, scope=scope
    )
    project = registration["project"]
    project_id = str(project["id"])
    root = Path(str(registration["identity"]["resolved_path"]))
    files = iter_project_files(root, limits)
    now = utc_now()
    seen_paths: set[str] = set()
    counters = {
        "files_seen": len(files),
        "files_added": 0,
        "files_updated": 0,
        "files_unchanged": 0,
        "files_skipped": 0,
        "files_removed": 0,
        "files_renamed": 0,
        "chunks_added": 0,
        "entities_observed": 0,
        "claims_observed": 0,
    }
    ingest_paths: set[str] = set()
    for candidate in files:
        try:
            ingest_paths.add(os.path.normcase(str(candidate.resolve())))
        except OSError:
            continue
    changed = False

    with database.transaction() as connection:
        for path_obj in files:
            resolved_path = str(path_obj.resolve())
            try:
                stat = path_obj.stat()
                relative_path = path_obj.relative_to(root).as_posix()
            except (OSError, ValueError):
                counters["files_skipped"] += 1
                continue

            source = connection.execute(
                "SELECT * FROM sources WHERE project_id=? AND resolved_path=?",
                (project_id, resolved_path),
            ).fetchone()
            if (
                source is not None
                and source["active"]
                and source["size_bytes"] == stat.st_size
                and source["mtime_ns"] == stat.st_mtime_ns
            ):
                seen_paths.add(os.path.normcase(resolved_path))
                connection.execute("UPDATE sources SET last_seen=? WHERE id=?", (now, source["id"]))
                counters["files_unchanged"] += 1
                continue

            try:
                text = extract_text(path_obj)
                if contains_sensitive_content(text):
                    counters["files_skipped"] += 1
                    continue
                content_hash = sha256_file(path_obj)
            except (OSError, ValueError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
                counters["files_skipped"] += 1
                continue

            seen_paths.add(os.path.normcase(resolved_path))

            if source is not None and source["current_hash"] == content_hash:
                connection.execute(
                    "UPDATE sources SET size_bytes=?,mtime_ns=?,active=1,last_seen=? WHERE id=?",
                    (stat.st_size, stat.st_mtime_ns, now, source["id"]),
                )
                counters["files_unchanged"] += 1
                continue

            if source is None:
                hash_matches = connection.execute(
                    "SELECT * FROM sources WHERE project_id=? AND current_hash=?",
                    (project_id, content_hash),
                ).fetchall()
                rename_source = None
                if len(hash_matches) == 1:
                    candidate = hash_matches[0]
                    if os.path.normcase(str(candidate["resolved_path"])) not in ingest_paths:
                        rename_source = candidate
                if rename_source is not None:
                    source_id = int(rename_source["id"])
                    connection.execute(
                        "UPDATE sources SET resolved_path=?,relative_path=?,media_type=?,"
                        "size_bytes=?,mtime_ns=?,active=1,last_seen=? WHERE id=?",
                        (
                            resolved_path,
                            relative_path,
                            _media_type(path_obj),
                            stat.st_size,
                            stat.st_mtime_ns,
                            now,
                            source_id,
                        ),
                    )
                    chunk_ids = [
                        int(row[0])
                        for row in connection.execute(
                            "SELECT c.id FROM chunks c "
                            "JOIN source_versions v ON v.id=c.source_version_id "
                            "WHERE v.source_id=?",
                            (source_id,),
                        )
                    ]
                    for chunk_id in chunk_ids:
                        connection.execute(
                            "UPDATE chunk_fts SET path=? WHERE chunk_id=?",
                            (relative_path, chunk_id),
                        )
                    counters["files_renamed"] += 1
                    changed = True
                    continue
                cursor = connection.execute(
                    "INSERT INTO sources(project_id,resolved_path,relative_path,media_type,current_hash,"
                    "size_bytes,mtime_ns,active,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        project_id,
                        resolved_path,
                        relative_path,
                        _media_type(path_obj),
                        content_hash,
                        stat.st_size,
                        stat.st_mtime_ns,
                        1,
                        now,
                        now,
                    ),
                )
                source_id = int(cursor.lastrowid)
                counters["files_added"] += 1
            else:
                source_id = int(source["id"])
                old_chunk_ids = [
                    row[0]
                    for row in connection.execute(
                        "SELECT c.id FROM chunks c JOIN source_versions v ON v.id=c.source_version_id "
                        "WHERE v.source_id=?",
                        (source_id,),
                    )
                ]
                if old_chunk_ids:
                    placeholders = ",".join("?" for _ in old_chunk_ids)
                    connection.execute(
                        f"DELETE FROM chunk_fts WHERE chunk_id IN ({placeholders})", old_chunk_ids
                    )
                connection.execute(
                    "UPDATE sources SET relative_path=?,media_type=?,current_hash=?,size_bytes=?,"
                    "mtime_ns=?,active=1,last_seen=? WHERE id=?",
                    (
                        relative_path,
                        _media_type(path_obj),
                        content_hash,
                        stat.st_size,
                        stat.st_mtime_ns,
                        now,
                        source_id,
                    ),
                )
                counters["files_updated"] += 1

            version = connection.execute(
                "SELECT id FROM source_versions WHERE source_id=? AND content_hash=?",
                (source_id, content_hash),
            ).fetchone()
            if version is None:
                version_cursor = connection.execute(
                    "INSERT INTO source_versions(source_id,content_hash,observed_at,text_length) "
                    "VALUES(?,?,?,?)",
                    (source_id, content_hash, now, len(text)),
                )
                version_id = int(version_cursor.lastrowid)
                text_chunks = chunk_text(text, limits.chunk_characters)
                for chunk in text_chunks:
                    chunk_hash = sha256_text(chunk.text)
                    chunk_cursor = connection.execute(
                        "INSERT INTO chunks(source_version_id,ordinal,line_start,line_end,heading,text,chunk_hash) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (
                            version_id,
                            chunk.ordinal,
                            chunk.line_start,
                            chunk.line_end,
                            chunk.heading,
                            "",
                            chunk_hash,
                        ),
                    )
                    chunk_id = int(chunk_cursor.lastrowid)
                    connection.execute(
                        "INSERT INTO chunk_fts(text,heading,path,project_id,chunk_id) VALUES(?,?,?,?,?)",
                        (chunk.text, chunk.heading or "", relative_path, project_id, chunk_id),
                    )
                    entity_count, claim_count = _extract_graph(
                        connection,
                        project_id=project_id,
                        source_id=source_id,
                        relative_path=relative_path,
                        chunk_id=chunk_id,
                        chunk=chunk,
                    )
                    counters["chunks_added"] += 1
                    counters["entities_observed"] += entity_count
                    counters["claims_observed"] += claim_count
            else:
                version_id = int(version["id"])
                for row in connection.execute(
                    "SELECT id,text,heading,line_start,line_end FROM chunks "
                    "WHERE source_version_id=? ORDER BY ordinal",
                    (version_id,),
                ):
                    plate = slice_source_lines(text, int(row["line_start"]), int(row["line_end"]))
                    fts_body = plate or (row["text"] or "")
                    connection.execute(
                        "INSERT INTO chunk_fts(text,heading,path,project_id,chunk_id) VALUES(?,?,?,?,?)",
                        (fts_body, row["heading"] or "", relative_path, project_id, row["id"]),
                    )
            changed = True

        active_sources = connection.execute(
            "SELECT id,resolved_path FROM sources WHERE project_id=? AND active=1", (project_id,)
        ).fetchall()
        for source in active_sources:
            try:
                Path(source["resolved_path"]).resolve().relative_to(root)
            except (OSError, ValueError):
                # Another agent may use a different checkout of this canonical project.
                # Ingesting one checkout must not deactivate the other checkout's evidence.
                continue
            if os.path.normcase(source["resolved_path"]) not in seen_paths:
                connection.execute(
                    "UPDATE sources SET active=0,last_seen=? WHERE id=?", (now, source["id"])
                )
                counters["files_removed"] += 1
                changed = True

        if changed:
            connection.execute(
                "UPDATE projects SET corpus_version=corpus_version+1,updated_at=? WHERE id=?",
                (now, project_id),
            )
            connection.execute("DELETE FROM cache WHERE project_id=?", (project_id,))

        project_row = dict(
            connection.execute(
                "SELECT id,display_name,scope,corpus_version FROM projects WHERE id=?", (project_id,)
            ).fetchone()
        )

    return {
        "project": project_row,
        "root": str(root),
        "changed": changed,
        "counters": counters,
        "limits": json.loads(json.dumps(limits.__dict__)),
    }
