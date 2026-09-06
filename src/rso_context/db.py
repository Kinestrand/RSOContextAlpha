from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 5


class SchemaVersionError(ValueError):
    """Raised when the on-disk schema is newer than this code."""


class ClosingConnection(sqlite3.Connection):
    """A sqlite connection whose context manager also releases the file handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    text,
    heading,
    path,
    project_id,
    chunk_id UNINDEXED,
    tokenize='unicode61 remove_diacritics 2'
);
"""

SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    canonical_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'project',
    kind TEXT NOT NULL,
    git_remote TEXT,
    git_root_commit TEXT,
    structure_hash TEXT,
    corpus_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    agent TEXT NOT NULL,
    external_id TEXT,
    resolved_path TEXT,
    match_method TEXT NOT NULL,
    match_confidence REAL NOT NULL,
    identity_json TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(agent, external_id),
    UNIQUE(agent, resolved_path)
);

CREATE TABLE IF NOT EXISTS possible_project_matches (
    left_project_id TEXT NOT NULL REFERENCES projects(id),
    right_project_id TEXT NOT NULL REFERENCES projects(id),
    score REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(left_project_id, right_project_id)
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    resolved_path TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    current_hash TEXT,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(project_id, resolved_path)
);

CREATE TABLE IF NOT EXISTS source_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    content_hash TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    text_length INTEGER NOT NULL,
    UNIQUE(source_id, content_hash)
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_version_id INTEGER NOT NULL REFERENCES source_versions(id),
    ordinal INTEGER NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    heading TEXT,
    text TEXT NOT NULL,
    chunk_hash TEXT NOT NULL,
    UNIQUE(source_version_id, ordinal)
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    trust_state TEXT NOT NULL,
    properties_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, kind, canonical_name)
);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    subject_id TEXT NOT NULL REFERENCES nodes(id),
    predicate TEXT NOT NULL,
    object_id TEXT NOT NULL REFERENCES nodes(id),
    trust_state TEXT NOT NULL,
    evidence_chunk_id INTEGER REFERENCES chunks(id),
    rule_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(subject_id, predicate, object_id, evidence_chunk_id, rule_id)
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    normalized_text TEXT NOT NULL,
    display_text TEXT NOT NULL,
    trust_state TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, normalized_text)
);

CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_id TEXT NOT NULL REFERENCES claims(id),
    chunk_id INTEGER NOT NULL REFERENCES chunks(id),
    relation TEXT NOT NULL DEFAULT 'states',
    PRIMARY KEY(claim_id, chunk_id, relation)
);

CREATE TABLE IF NOT EXISTS validations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL REFERENCES claims(id),
    validator TEXT NOT NULL,
    result TEXT NOT NULL,
    details_json TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    query_text TEXT NOT NULL,
    corpus_version INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    packet_json TEXT NOT NULL,
    packet_hash TEXT NOT NULL,
    elapsed_ms REAL NOT NULL,
    created_at TEXT NOT NULL,
    remaining_budget INTEGER
);

CREATE TABLE IF NOT EXISTS run_budgets (
    project_id TEXT NOT NULL REFERENCES projects(id),
    agent TEXT NOT NULL,
    remaining INTEGER NOT NULL,
    initial INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, agent)
);

CREATE TABLE IF NOT EXISTS cache (
    cache_key TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    corpus_version INTEGER NOT NULL,
    packet_json TEXT NOT NULL,
    packet_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_aliases_project ON project_aliases(project_id);
CREATE INDEX IF NOT EXISTS idx_aliases_path ON project_aliases(resolved_path);
CREATE INDEX IF NOT EXISTS idx_sources_project_active ON sources(project_id, active);
CREATE INDEX IF NOT EXISTS idx_versions_source ON source_versions(source_id);
CREATE INDEX IF NOT EXISTS idx_chunks_version ON chunks(source_version_id);
CREATE INDEX IF NOT EXISTS idx_nodes_project_kind ON nodes(project_id, kind);
CREATE INDEX IF NOT EXISTS idx_edges_project_subject ON edges(project_id, subject_id);
CREATE INDEX IF NOT EXISTS idx_edges_project_object ON edges(project_id, object_id);
CREATE INDEX IF NOT EXISTS idx_claims_project_state ON claims(project_id, trust_state);
CREATE INDEX IF NOT EXISTS idx_claim_evidence_claim ON claim_evidence(claim_id);
CREATE INDEX IF NOT EXISTS idx_validations_claim ON validations(claim_id, id);
CREATE INDEX IF NOT EXISTS idx_runs_project_created ON runs(project_id, created_at);
""" + FTS_SQL


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_schema_version(connection: sqlite3.Connection) -> int | None:
    try:
        row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    value = row[0] if not isinstance(row, sqlite3.Row) else row["value"]
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _chunk_fts_sql(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type IN ('table','view') AND name='chunk_fts'"
    ).fetchone()
    return None if row is None else str(row[0] or "")


def _chunk_fts_is_v3(connection: sqlite3.Connection) -> bool:
    sql = _chunk_fts_sql(connection)
    if not sql:
        return False
    # v3 indexes project_id (must not be marked UNINDEXED) and keeps chunk_id UNINDEXED.
    compact = " ".join(sql.split())
    if "project_id UNINDEXED" in compact:
        return False
    return "chunk_id UNINDEXED" in compact and "project_id" in compact


def rebuild_chunk_fts_v3(connection: sqlite3.Connection) -> None:
    """Rebuild chunk_fts with project_id as an INDEXED FTS column."""
    connection.execute("DROP TABLE IF EXISTS chunk_fts")
    connection.executescript(FTS_SQL)
    connection.execute(
        """
        INSERT INTO chunk_fts(text, heading, path, project_id, chunk_id)
        SELECT c.text, COALESCE(c.heading, ''), s.relative_path, s.project_id, c.id
        FROM chunks c
        JOIN source_versions v ON v.id = c.source_version_id
        JOIN sources s ON s.id = v.source_id
        """
    )


def _write_schema_version(connection: sqlite3.Connection, version: int) -> None:
    # Never lower an existing schema_version, even if a caller passes a smaller value.
    connection.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value "
        "WHERE CAST(meta.value AS INTEGER) < CAST(excluded.value AS INTEGER)",
        (str(version),),
    )


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _migrate_v4(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS run_budgets (
            project_id TEXT NOT NULL REFERENCES projects(id),
            agent TEXT NOT NULL,
            remaining INTEGER NOT NULL,
            initial INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(project_id, agent)
        )
        """
    )
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "runs" in tables and "remaining_budget" not in _column_names(connection, "runs"):
        connection.execute("ALTER TABLE runs ADD COLUMN remaining_budget INTEGER")


def _migrate_v5(connection: sqlite3.Connection) -> None:
    # Stamp only. Do not wipe stored chunk bodies; compact-pointers is the explicit clean.
    del connection


def _migrate(connection: sqlite3.Connection, from_version: int) -> None:
    if from_version < 2:
        connection.executescript(SCHEMA_SQL)
    if from_version < 3 or not _chunk_fts_is_v3(connection):
        rebuild_chunk_fts_v3(connection)
    if from_version < 4:
        _migrate_v4(connection)
    if from_version < 5:
        _migrate_v5(connection)
    _write_schema_version(connection, SCHEMA_VERSION)


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            has_meta = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
            ).fetchone()
            if has_meta is None:
                connection.executescript(SCHEMA_SQL)
                _write_schema_version(connection, SCHEMA_VERSION)
                connection.execute(
                    "INSERT OR IGNORE INTO meta(key, value) VALUES('created_by', 'rso-context-alpha')"
                )
                return

            db_version = read_schema_version(connection)
            if db_version is None:
                connection.executescript(SCHEMA_SQL)
                if not _chunk_fts_is_v3(connection):
                    rebuild_chunk_fts_v3(connection)
                _write_schema_version(connection, SCHEMA_VERSION)
                connection.execute(
                    "INSERT OR IGNORE INTO meta(key, value) VALUES('created_by', 'rso-context-alpha')"
                )
                return

            if db_version > SCHEMA_VERSION:
                raise SchemaVersionError(
                    f"Database schema_version {db_version} is newer than code version {SCHEMA_VERSION}"
                )
            if db_version < SCHEMA_VERSION:
                _migrate(connection, db_version)
            connection.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('created_by', 'rso-context-alpha')"
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def stats(self) -> dict[str, object]:
        self.initialize()
        tables = [
            "projects",
            "project_aliases",
            "possible_project_matches",
            "sources",
            "source_versions",
            "chunks",
            "nodes",
            "edges",
            "claims",
            "claim_evidence",
            "validations",
            "runs",
            "run_budgets",
            "cache",
        ]
        with self.connect() as connection:
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }
            project_rows = connection.execute(
                "SELECT id, display_name, scope, corpus_version FROM projects ORDER BY display_name"
            ).fetchall()
            db_schema_version = read_schema_version(connection)
        return {
            "database": str(self.path),
            "schema_version": db_schema_version,
            "db_schema_version": db_schema_version,
            "code_schema_version": SCHEMA_VERSION,
            "counts": counts,
            "projects": [dict(row) for row in project_rows],
        }
