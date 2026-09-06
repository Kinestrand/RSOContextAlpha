from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import SCHEMA_VERSION, Database
from .identity import sha256_file


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def slice_source_lines(text: str, line_start: int, line_end: int) -> str:
    """Slice 1-indexed inclusive lines using the same newline rules as chunk_text."""
    lines = normalize_newlines(text).split("\n")
    start = max(0, int(line_start) - 1)
    end = min(len(lines), max(start, int(line_end)))
    return "\n".join(lines[start:end]).strip()


def file_matches_hash(path: Path, expected_hash: str) -> bool:
    if not expected_hash:
        return False
    try:
        if not path.is_file():
            return False
        return sha256_file(path) == expected_hash
    except OSError:
        return False


def _extract_text(path: Path) -> str:
    from .ingest import extract_text

    return extract_text(path)


class PlateCache:
    """Read each live file at most once per query while reconstructing plate text."""

    def __init__(self) -> None:
        self._loaded: dict[tuple[str, str], tuple[str | None, bool]] = {}

    def _load(self, path: str, expected_hash: str) -> tuple[str | None, bool]:
        key = (path, expected_hash)
        if key in self._loaded:
            return self._loaded[key]
        plate_path = Path(path)
        if not file_matches_hash(plate_path, expected_hash):
            result: tuple[str | None, bool] = (None, False)
            self._loaded[key] = result
            return result
        try:
            extracted = _extract_text(plate_path)
        except (OSError, ValueError, KeyError):
            result = (None, False)
            self._loaded[key] = result
            return result
        result = (extracted, True)
        self._loaded[key] = result
        return result

    def reconstruct(
        self,
        path: str,
        expected_hash: str,
        line_start: int,
        line_end: int,
    ) -> tuple[str | None, bool]:
        extracted, matches = self._load(path, expected_hash)
        if not matches or extracted is None:
            return None, False
        return slice_source_lines(extracted, line_start, line_end), True


def hydrate_chunk_row(row: dict[str, Any], cache: PlateCache | None = None) -> dict[str, Any]:
    """Fill plate text from the live file; fall back to stored text only when reconstruct fails."""
    cache = cache or PlateCache()
    stored = str(row.get("text") or "")
    path = str(row.get("resolved_path") or "")
    expected = str(row.get("source_hash") or "")
    line_start = int(row["line_start"])
    line_end = int(row["line_end"])
    reconstructed, ok = cache.reconstruct(path, expected, line_start, line_end)
    out = dict(row)
    if ok:
        out["text"] = reconstructed or ""
        out["stale"] = False
        return out
    # Hash mismatch or missing file: never return the live file's current lines as current.
    if stored:
        out["text"] = stored
        out["stale"] = True
        return out
    out["text"] = ""
    out["stale"] = True
    return out


def _page_bytes(connection) -> int:
    page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    return page_count * page_size


def compact_pointers(database: Database) -> dict[str, object]:
    """Clear stored chunk bodies that can be reconstructed from the live current plate.

    Schema migrate 4→5 does not wipe bodies. This is the explicit clean.
    Old-version chunks (content_hash != current_hash) keep stored text because the
    current file is a different take and reconstruct would slice the wrong lines.
    """
    database.initialize()
    clear_ids: list[int] = []
    kept_legacy = 0
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT c.id, c.line_start, c.line_end, v.content_hash,
                   s.resolved_path, s.current_hash, s.active
            FROM chunks c
            JOIN source_versions v ON v.id = c.source_version_id
            JOIN sources s ON s.id = v.source_id
            WHERE c.text IS NOT NULL AND c.text <> ''
            """
        ).fetchall()
        for row in rows:
            current_hash = str(row["current_hash"] or "")
            version_hash = str(row["content_hash"] or "")
            if int(row["active"] or 0) != 1 or version_hash != current_hash:
                kept_legacy += 1
                continue
            if file_matches_hash(Path(str(row["resolved_path"])), current_hash):
                clear_ids.append(int(row["id"]))
            else:
                kept_legacy += 1
        for offset in range(0, len(clear_ids), 400):
            batch = clear_ids[offset : offset + 400]
            placeholders = ",".join("?" for _ in batch)
            connection.execute(
                f"UPDATE chunks SET text='' WHERE id IN ({placeholders})",
                batch,
            )
        connection.commit()

    with database.connect() as connection:
        bytes_before = _page_bytes(connection)
        previous = connection.isolation_level
        try:
            connection.isolation_level = None
            connection.execute("VACUUM")
        finally:
            connection.isolation_level = previous
        bytes_after = _page_bytes(connection)

    return {
        "schema": SCHEMA_VERSION,
        "cleared": len(clear_ids),
        "kept_legacy": kept_legacy,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
    }
